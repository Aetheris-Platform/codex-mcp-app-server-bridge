"""Bounded task lifecycle service for the native app-server client."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import stat
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, cast

from .errors import (
    ConflictError,
    NotFoundError,
    PermissionError,
    ProtocolError,
    UncertainError,
    ValidationError,
)
from .models import (
    AdapterTaskState,
    parse_cancel,
    parse_result,
    parse_start,
    parse_status,
)

if TYPE_CHECKING:
    from .ownership import DataRootOwnership
    from .profile import OperatorProfile
    from .protocol import AppServerClient, JsonObject
    from .store import Record, TaskStore
from .validation import canonical_json, validate_start_request

type ClientFactory = Callable[[], AppServerClient]
Clock = Callable[[], datetime]


def _owned_request(method: Callable[..., Awaitable[JsonObject]]) -> Callable[..., Awaitable[JsonObject]]:
    """Drain service invocations without waiting for caller-owned cleanup."""

    @wraps(method)
    async def call(self: AdapterService, raw: object) -> JsonObject:
        if self._admission_closed:
            raise ValidationError("adapter admission is closed")
        self._ownership.require(self.profile.data_root)
        task = asyncio.current_task()
        if task is None:
            raise ValidationError("adapter request requires an asyncio task")
        finished = asyncio.get_running_loop().create_future()
        self._requests[finished] = task
        try:
            return await method(self, raw)
        finally:
            self._requests.pop(finished, None)
            finished.set_result(None)

    return call


class AdapterService:
    """Expose start/status/result/cancel without making transport a workflow authority."""

    def __init__(
        self,
        profile: OperatorProfile,
        store: TaskStore,
        client_factory: ClientFactory,
        *,
        ownership: DataRootOwnership,
        clock: Clock | None = None,
        shutdown_callback: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        ownership.require(profile.data_root)
        ownership.require(store.root)
        self._ownership = ownership
        self._requests: dict[asyncio.Future[None], asyncio.Task[object]] = {}
        self._interrupts: set[asyncio.Task[None]] = set()
        self._shutdown_task: asyncio.Task[None] | None = None
        self.profile = profile
        self.store = store
        self.client_factory = client_factory
        self.shutdown_callback = shutdown_callback
        self.clock = clock or (lambda: datetime.now(UTC))
        self._monotonic = time.monotonic
        self._condition = asyncio.Condition()
        self._task_locks: dict[str, asyncio.Lock] = {}
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._supervisors: dict[str, asyncio.Task[None]] = {}
        self._reconciliations: dict[str, asyncio.Task[None]] = {}
        self._clients: dict[str, AppServerClient] = {}
        self._monotonic_deadlines: dict[str, float] = {}
        self._admission_closed = False
        self._reconcile_restarted_tasks()

    @_owned_request
    async def start(self, raw: object) -> JsonObject:
        if self._admission_closed:
            raise ValidationError("adapter admission is closed")
        request = parse_start(raw)
        request.request_uuid()
        existing = self.store.find_request(request.request_id)
        if existing is not None:
            if not self._same_profile(existing):
                raise ConflictError("request_id belongs to a different runtime profile")
            replay = request.model_dump(mode="json")
            try:
                normalized_replay_cwd = await asyncio.to_thread(
                    Path(request.cwd).resolve, strict=True
                )
                replay["cwd"] = str(normalized_replay_cwd)
            except (OSError, RuntimeError) as exc:
                raise ValidationError("replayed cwd is not a resolvable directory") from exc
            replay_digest = hashlib.sha256(canonical_json(replay)).hexdigest()
            if existing.get("request_sha256") != replay_digest:
                raise ConflictError("request_id was already used with different input")
            return _public_status(existing)
        normalized_cwd = validate_start_request(
            request,
            now=self.clock(),
            allowed_models=self.profile.allowed_models,
            allowed_sandboxes=self.profile.allowed_sandboxes,
            allowed_worktree_roots=tuple(
                str(root.resolve()) for root in self.profile.allowed_worktree_roots
            ),
        )
        normalized = request.model_dump(mode="json")
        normalized["cwd"] = normalized_cwd
        digest = hashlib.sha256(canonical_json(normalized)).hexdigest()
        cwd_identity = _path_identity(Path(normalized_cwd))
        task_id = str(uuid.uuid4())
        self.profile.validate()
        candidate: Record = {
            "schema_version": "1",
            "task_id": task_id,
            "request_id": request.request_id,
            "request": normalized,
            "request_sha256": digest,
            "cwd_identity": cwd_identity,
            "runtime_path": str(self.profile.runtime_path),
            "runtime_version": self.profile.runtime_version,
            "runtime_sha256": self.profile.runtime_sha256.lower(),
            "profile_sha256": self.profile.identity_digest(),
            "created_at_utc": self.clock().astimezone(UTC).isoformat(),
            "deadline_utc": request.deadline().isoformat(),
            "state": AdapterTaskState.STARTING.value,
            "reason": None,
            "thread_id": None,
            "turn_id": None,
            "thread_submission_started": False,
            "turn_submission_started": False,
            "turn_submitted": False,
            "stop_intent": None,
            "stop_requested_at_utc": None,
            "interrupt_sent": False,
            "last_event_sequence": 0,
            "completed_items": {},
            "provider_outcome": None,
            "result": None,
            "pending_event": None,
            "native_journal": None,
            "artifacts": {"events": {}, "result": None, "native_journal": None},
        }
        reserved = self.store.reserve_request(candidate)
        if reserved is not candidate:
            if reserved.get("request_sha256") != digest:
                raise ConflictError("request_id was already used with different input")
            if not self._same_profile(reserved):
                raise ConflictError("request_id belongs to a different runtime profile")
            return _public_status(reserved)
        self._monotonic_deadlines[task_id] = self._monotonic() + max(
            0.0, (request.deadline() - self.clock().astimezone(UTC)).total_seconds()
        )
        worker = asyncio.create_task(self._run(task_id))
        self._workers[task_id] = worker
        worker.add_done_callback(lambda _: self._workers.pop(task_id, None))
        return _public_status(self.store.load(task_id))

    @_owned_request
    async def status(self, raw: object) -> JsonObject:
        request = parse_status(raw)
        request.task_uuid()
        task_id = request.task_id
        record = self.store.load(task_id)
        if request.cursor > _integer(record.get("last_event_sequence")):
            raise ValidationError("status cursor is beyond retained sequence")
        should_reconcile = (
            _state(record) == AdapterTaskState.UNKNOWN
            and (
                record.get("reason") == "adapter_restarted"
                or (
                    isinstance(record.get("reason"), str)
                    and (
                        cast("str", record["reason"]).startswith("reconciliation_")
                        or record["reason"] in {"output_phase_unknown", "full history is unavailable"}
                    )
                )
            )
            and isinstance(record.get("thread_id"), str)
            and isinstance(record.get("turn_id"), str)
        )
        if should_reconcile:
            self._schedule_reconciliation(task_id)
        if request.wait_seconds and should_reconcile:
            reconciliation = self._reconciliations.get(task_id)
            remaining = max(0.0, self._deadline(record).timestamp() - self.clock().timestamp())
            if reconciliation is not None:
                try:
                    async with asyncio.timeout(min(request.wait_seconds, remaining)):
                        await asyncio.shield(reconciliation)
                except asyncio.CancelledError:
                    current = asyncio.current_task()
                    if current is None or current.cancelling() or not reconciliation.cancelled():
                        raise
                except (OSError, ProtocolError, UncertainError, ValidationError, TimeoutError):
                    pass
        elif request.wait_seconds and _integer(record.get("last_event_sequence")) <= request.cursor:
            remaining = max(0.0, self._deadline(record).timestamp() - self.clock().timestamp())
            try:
                async with asyncio.timeout(min(request.wait_seconds, remaining)):
                    async with self._condition:
                        await self._condition.wait_for(
                            lambda: self._changed(task_id, request.cursor, record.get("state"), record.get("reason"))
                        )
            except TimeoutError:
                pass
        if request.wait_seconds:
            record = self.store.load(task_id)
        return _public_status(record)

    @_owned_request
    async def result(self, raw: object) -> JsonObject:
        request = parse_result(raw)
        request.task_uuid()
        record = self.store.load(request.task_id)
        result = record.get("result")
        if not isinstance(result, dict):
            return {
                "task_id": request.task_id,
                "complete": False,
                "format": None,
                "output_sha256": None,
                "total_bytes": None,
                "offset_bytes": request.offset_bytes,
                "chunk": "",
                "next_offset": request.offset_bytes,
            }
        content = self.store.read_result(record)
        if request.offset_bytes > len(content):
            raise ValidationError("offset_bytes is beyond the retained result")
        chunk = content[request.offset_bytes : request.offset_bytes + request.limit_bytes]
        next_offset = request.offset_bytes + len(chunk)
        return {
            "task_id": request.task_id,
            "complete": record.get("state") == AdapterTaskState.COMPLETED.value,
            "format": "codex-final-messages-v1",
            "output_sha256": result.get("sha256"),
            "total_bytes": result.get("size_bytes"),
            "offset_bytes": request.offset_bytes,
            "chunk": base64.b64encode(chunk).decode("ascii"),
            "next_offset": None if next_offset >= len(content) else next_offset,
        }

    @_owned_request
    async def cancel(self, raw: object) -> JsonObject:
        request = parse_cancel(raw)
        request.task_uuid()
        task_id = request.task_id
        lock = self._task_locks.setdefault(task_id, asyncio.Lock())
        reconcile_unknown = False
        async with lock:
            record = self.store.load(task_id)
            state = _state(record)
            if state in _TERMINAL and state != AdapterTaskState.UNKNOWN:
                return _public_status(record)
            reconcile_unknown = (
                state == AdapterTaskState.UNKNOWN
                and isinstance(record.get("thread_id"), str)
                and isinstance(record.get("turn_id"), str)
            )
            if record.get("stop_intent") is None:
                self._set_stop_intent(task_id, "cancel")
            record = self.store.load(task_id)
            if reconcile_unknown:
                self._set_state(task_id, AdapterTaskState.UNKNOWN, "cancellation_requested")
            elif not _boolean(record.get("turn_submitted")) and not _boolean(
                record.get("turn_submission_started")
            ):
                if record.get("stop_intent") == "expiry":
                    self._set_state(task_id, AdapterTaskState.FAILED, "deadline_before_dispatch")
                else:
                    self._set_state(task_id, AdapterTaskState.CANCELLED, "cancelled_before_dispatch")
            else:
                self._set_state(
                    task_id, AdapterTaskState.CANCEL_REQUESTED, "cancellation_requested"
                )
                self._schedule_interrupt(task_id)
        if reconcile_unknown:
            reconciliation = self._reconciliations.get(task_id)
            if reconciliation is not None and not reconciliation.done():
                reconciliation.cancel()
                await asyncio.gather(reconciliation, return_exceptions=True)
            await self._reconcile_known_task(task_id)
        return _public_status(self.store.load(task_id))

    async def shutdown(self) -> None:
        """Stop admission and request interruption for owned active turns."""

        if self._shutdown_task is None:
            self._admission_closed = True
            self._shutdown_task = asyncio.create_task(self._shutdown())
        # Cancellation of a host caller cannot release ownership ahead of cleanup.
        cancelled = False
        while not self._shutdown_task.done():
            try:
                await asyncio.shield(self._shutdown_task)
            except asyncio.CancelledError:
                cancelled = True
        self._shutdown_task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _shutdown(self) -> None:
        try:
            requests = tuple(self._requests)
            for caller in set(self._requests.values()):
                caller.cancel()
            if requests:
                await asyncio.gather(*requests, return_exceptions=True)
            try:
                for task_id, record in self._records():
                    try:
                        if _state(record) not in _TERMINAL:
                            self._set_stop_intent(task_id, "cancel")
                            if _boolean(record.get("turn_submitted")):
                                self._set_state(
                                    task_id, AdapterTaskState.CANCEL_REQUESTED, "shutdown_requested"
                                )
                                self._schedule_interrupt(task_id)
                    except (OSError, ValidationError, NotFoundError):
                        continue
            finally:
                workers = tuple(self._workers.values())
                if workers:
                    done, pending = await asyncio.wait(workers, timeout=5.0)
                    del done
                    if pending:
                        for task_id, worker in tuple(self._workers.items()):
                            if worker in pending:
                                with suppress(OSError, ValidationError, NotFoundError):
                                    self._set_state(
                                        task_id, AdapterTaskState.UNKNOWN, "shutdown_unconfirmed"
                                    )
                                worker.cancel()
                        await asyncio.gather(*pending, return_exceptions=True)
                supervisors = tuple(self._supervisors.values())
                for supervisor in supervisors:
                    supervisor.cancel()
                if supervisors:
                    await asyncio.gather(*supervisors, return_exceptions=True)
                reconciliations = tuple(self._reconciliations.values())
                for reconciliation in reconciliations:
                    reconciliation.cancel()
                if reconciliations:
                    await asyncio.gather(*reconciliations, return_exceptions=True)
        finally:
            try:
                # No interrupt may retain write access beyond the ownership lifetime.
                interrupts = tuple(self._interrupts)
                for interrupt in interrupts:
                    interrupt.cancel()
                if interrupts:
                    await asyncio.gather(*interrupts, return_exceptions=True)
            finally:
                try:
                    if self.shutdown_callback is not None:
                        await self.shutdown_callback()
                finally:
                    self._ownership.close()

    async def _run(self, task_id: str) -> None:
        client: AppServerClient | None = None
        try:
            await asyncio.to_thread(self.profile.verify_runtime)
            client = self.client_factory()
            self._clients[task_id] = client
            async with asyncio.timeout(self._remaining_seconds(task_id)):
                await client.connect()
            async with self._task_locks.setdefault(task_id, asyncio.Lock()):
                if self._stop_or_expired(task_id):
                    return
                self._verify_cwd_identity(task_id)
                self._set_fields(task_id, {"thread_submission_started": True})
            thread_response = await self._request(
                task_id, client, "thread/start", self._thread_params(task_id)
            )
            async with self._task_locks.setdefault(task_id, asyncio.Lock()):
                thread_id = _thread_id(thread_response)
                self._set_fields(task_id, {"thread_id": thread_id})
                if self._stop_or_expired(task_id):
                    return
                self._verify_permissions(task_id, thread_response)
                self._verify_cwd_identity(task_id)
                self._set_fields(task_id, {"turn_submission_started": True})
            turn_response = await self._request(
                task_id, client, "turn/start", self._turn_params(task_id, thread_id)
            )
            async with self._task_locks.setdefault(task_id, asyncio.Lock()):
                turn_id = _turn_id(turn_response)
                self._set_fields(
                    task_id,
                    {
                        "turn_id": turn_id,
                        "turn_submitted": True,
                        "state": AdapterTaskState.RUNNING.value,
                    },
                )
            if self.store.load(task_id).get("stop_intent") is not None:
                self._set_state(
                    task_id, AdapterTaskState.CANCEL_REQUESTED, "cancellation_requested"
                )
                await self._interrupt(task_id, client)
            await self._consume_events(task_id, client, thread_id, turn_id)
        except PermissionError as exc:
            self._set_state(task_id, AdapterTaskState.FAILED, str(exc))
        except (ValidationError, ProtocolError, UncertainError) as exc:
            self._uncertain_or_failed(task_id, str(exc))
        except asyncio.CancelledError:
            raise
        except (OSError, RuntimeError, TypeError, ValueError, KeyError):
            self._uncertain_or_failed(task_id, "adapter_internal_failure")
        finally:
            self._clients.pop(task_id, None)
            if client is not None:
                await client.close()
            async with self._condition:
                self._condition.notify_all()

    async def _consume_events(
        self, task_id: str, client: AppServerClient, thread_id: str, turn_id: str
    ) -> None:
        while True:
            record = self.store.load(task_id)
            state = _state(record)
            if state in _TERMINAL:
                return
            if self._deadline_reached(task_id):
                if not _boolean(record.get("turn_submitted")):
                    self._set_state(task_id, AdapterTaskState.FAILED, "deadline_before_dispatch")
                    return
                self._set_stop_intent(task_id, "expiry")
                self._set_state(
                    task_id, AdapterTaskState.CANCEL_REQUESTED, "deadline_interrupt_requested"
                )
                await self._interrupt(task_id, client)
            event = await client.next_event(0.2)
            if event is None:
                if (
                    self._deadline_reached(task_id)
                    # A deadline crossed during next_event must first pass through
                    # expiry handling above; RPC response events do not prove a stop.
                    and _boolean(self.store.load(task_id).get("interrupt_sent"))
                    and _integer(self.store.load(task_id).get("last_event_sequence")) > 0
                ):
                    self._set_state(task_id, AdapterTaskState.UNKNOWN, "unconfirmed_termination")
                    return
                continue
            raw, message = event
            self.store.append_event(task_id, raw)
            method = message.get("method")
            if not isinstance(method, str):
                continue
            params = _mapping(message.get("params")) if message.get("params") is not None else {}
            if method == "item/completed":
                self._consume_completed_item(task_id, params, thread_id, turn_id)
                continue
            if method == "item/agentMessage/delta":
                self._consume_delta(task_id, params, thread_id, turn_id)
                continue
            if method == "turn/completed":
                await self._consume_turn_completed(task_id, client, params, thread_id, turn_id)
                return
            if "approval" in method.lower() or "permission" in method.lower():
                self._set_state(task_id, AdapterTaskState.FAILED, "permission_required")
                return
            if method == "adapterError":
                self._set_state(task_id, AdapterTaskState.UNKNOWN, "native_connection_lost")
                self._schedule_supervision(task_id)
                return
            self._set_state_if_active(task_id, AdapterTaskState.RUNNING, None)

    async def _consume_turn_completed(
        self,
        task_id: str,
        client: AppServerClient,
        params: dict[str, object],
        thread_id: str,
        turn_id: str,
    ) -> None:
        if params.get("threadId") != thread_id:
            raise ProtocolError("turn completion thread identity mismatch")
        turn = _mapping(params.get("turn"))
        if turn.get("id") != turn_id:
            raise ProtocolError("turn completion identity mismatch")
        status = turn.get("status")
        if status not in {"completed", "failed", "interrupted"}:
            raise ProtocolError("unknown turn terminal status")
        if self._deadline_reached(task_id):
            self._set_stop_intent(task_id, "expiry")
            self._set_state(task_id, AdapterTaskState.FAILED, "deadline_exceeded")
            return
        self._set_fields(task_id, {"provider_outcome": status})
        record = self.store.load(task_id)
        if record.get("stop_intent") is not None:
            if status == "interrupted":
                self._set_state(task_id, AdapterTaskState.CANCELLED, "provider_interrupted")
            else:
                self._set_state(task_id, AdapterTaskState.FAILED, "provider_completed_after_stop")
            return
        if status == "interrupted":
            self._set_state(task_id, AdapterTaskState.FAILED, "provider_interrupted")
            return
        if status == "failed":
            self._set_state(task_id, AdapterTaskState.FAILED, "provider_failed")
            return
        history = await self._request(
            task_id, client, "thread/read", {"threadId": thread_id, "includeTurns": True}
        )
        if self.store.load(task_id).get("stop_intent") is not None:
            self._set_state(task_id, AdapterTaskState.FAILED, "provider_completed_after_stop")
            return
        history_thread = _mapping(history.get("thread"))
        journal_path = history_thread.get("path")
        if journal_path is not None:
            if not isinstance(journal_path, str) or not journal_path:
                raise ProtocolError("native journal path is invalid")
            with suppress(ValidationError):
                self.store.copy_native_journal(
                    task_id,
                    Path(journal_path),
                    session_id=thread_id,
                    turn_id=turn_id,
                )
        result_bytes = self._build_result(task_id, history, thread_id, turn_id)
        async with self._task_locks.setdefault(task_id, asyncio.Lock()):
            if self._deadline_reached(task_id):
                self._set_stop_intent(task_id, "expiry")
                self._set_state(task_id, AdapterTaskState.FAILED, "deadline_exceeded")
                return
            if self.store.load(task_id).get("stop_intent") is not None:
                self._set_state(task_id, AdapterTaskState.FAILED, "provider_completed_after_stop")
                return
            try:
                self.store.write_result(task_id, result_bytes)
            except OSError:
                self._set_state(task_id, AdapterTaskState.UNKNOWN, "terminal_retention_failed")
                return
            try:
                self._set_state(task_id, AdapterTaskState.COMPLETED, None)
            except OSError:
                self._set_state(task_id, AdapterTaskState.UNKNOWN, "terminal_retention_failed")

    async def _reconcile_known_task(self, task_id: str) -> None:
        """Reconcile one restarted task using read-only native history only."""

        existing = self._reconciliations.get(task_id)
        current = asyncio.current_task()
        if existing is not None and existing is not current and not existing.done():
            await existing
            return
        registered = existing is None and current is not None
        if registered:
            self._reconciliations[task_id] = cast("asyncio.Task[None]", current)
        try:
            await self._reconcile_known_task_locked(task_id)
        finally:
            if registered and self._reconciliations.get(task_id) is current:
                self._reconciliations.pop(task_id, None)

    async def _reconcile_known_task_locked(self, task_id: str) -> None:
        """Reconcile with one client while callers fence competing recovery work."""

        record = self.store.load(task_id)
        thread_id = record.get("thread_id")
        turn_id = record.get("turn_id")
        if not isinstance(thread_id, str) or not isinstance(turn_id, str):
            return
        if not self._same_profile(record):
            return
        if record.get("stop_intent") is not None or self._deadline_reached(task_id):
            if record.get("stop_intent") is None:
                self._set_stop_intent(task_id, "expiry")
            self._set_state(task_id, AdapterTaskState.UNKNOWN, "termination_unconfirmed")
            client = self.client_factory()
            self._clients[task_id] = client
            try:
                await asyncio.to_thread(self.profile.verify_runtime)
                async with asyncio.timeout(5.0):
                    await client.connect()
                await self._interrupt(task_id, client)
            except (OSError, ProtocolError, ValidationError, TimeoutError):
                self._set_state(task_id, AdapterTaskState.UNKNOWN, "termination_unconfirmed")
            finally:
                self._clients.pop(task_id, None)
                await client.close()
            return
        client = self.client_factory()
        self._clients[task_id] = client
        try:
            await asyncio.to_thread(self.profile.verify_runtime)
            async with asyncio.timeout(self._remaining_seconds(task_id)):
                await client.connect()
            history = await self._request(
                task_id, client, "thread/read", {"threadId": thread_id, "includeTurns": True}
            )
            if self.store.load(task_id).get("stop_intent") is not None:
                self._set_state(task_id, AdapterTaskState.UNKNOWN, "termination_unconfirmed")
                await self._interrupt(task_id, client)
                return
            thread = _mapping(history.get("thread"))
            turns = thread.get("turns")
            if not isinstance(turns, list):
                raise UncertainError("reconciliation_history_unavailable")
            matches = [turn for turn in turns if isinstance(turn, dict) and turn.get("id") == turn_id]
            if len(matches) != 1:
                raise UncertainError("reconciliation_turn_unavailable")
            native_turn = cast("dict[str, object]", matches[0])
            status = native_turn.get("status")
            if status == "interrupted":
                self._set_fields(task_id, {"provider_outcome": status})
                self._set_state(task_id, AdapterTaskState.FAILED, "provider_interrupted")
                return
            if status == "failed":
                self._set_fields(task_id, {"provider_outcome": status})
                self._set_state(task_id, AdapterTaskState.FAILED, "provider_failed")
                return
            if status != "completed":
                raise UncertainError("reconciliation_terminal_status_unavailable")
            result_bytes = self._build_result(task_id, history, thread_id, turn_id)
            if self._deadline_reached(task_id):
                self._set_stop_intent(task_id, "expiry")
                return
            if self.store.load(task_id).get("stop_intent") is not None:
                self._set_state(task_id, AdapterTaskState.UNKNOWN, "termination_unconfirmed")
                await self._interrupt(task_id, client)
                return
            self._set_state(task_id, AdapterTaskState.RUNNING, "reconciling_after_restart")
            self._set_fields(task_id, {"provider_outcome": "completed"})
            self.store.write_result(task_id, result_bytes)
            self._set_state(task_id, AdapterTaskState.COMPLETED, "reconciled_after_restart")
        except (OSError, ProtocolError, ValidationError, UncertainError, TimeoutError) as exc:
            self._set_state(task_id, AdapterTaskState.UNKNOWN, str(exc))
        finally:
            self._clients.pop(task_id, None)
            await client.close()

    def _consume_completed_item(
        self, task_id: str, params: dict[str, object], thread_id: str, turn_id: str
    ) -> None:
        if params.get("threadId") != thread_id or params.get("turnId") != turn_id:
            raise ProtocolError("completed item identity mismatch")
        item = _mapping(params.get("item"))
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id:
            raise ProtocolError("completed item id is invalid")
        completed_at = params.get("completedAtMs")
        if isinstance(completed_at, bool) or not isinstance(completed_at, int) or completed_at < 0:
            raise ProtocolError("completed item timestamp is invalid")
        item_type = item.get("type")
        if not isinstance(item_type, str) or not item_type:
            raise ProtocolError("completed item type is invalid")
        if item_type == "agentMessage":
            if not isinstance(item.get("text"), str):
                raise ProtocolError("agent message text is invalid")
            phase = item.get("phase")
            if phase is not None and phase not in {"commentary", "final_answer"}:
                raise ProtocolError("agent message phase is unsupported")
        items = _mapping(self.store.load(task_id).get("completed_items"))
        previous = items.get(item_id)
        if previous is not None and previous != item:
            raise ProtocolError("conflicting completed item")
        items[item_id] = item
        self._set_fields(task_id, {"completed_items": items})

    def _consume_delta(
        self, task_id: str, params: dict[str, object], thread_id: str, turn_id: str
    ) -> None:
        if params.get("threadId") != thread_id or params.get("turnId") != turn_id:
            raise ProtocolError("delta identity mismatch")
        if not isinstance(params.get("itemId"), str) or not params["itemId"]:
            raise ProtocolError("delta item identity is invalid")
        if not isinstance(params.get("delta"), str):
            raise ProtocolError("delta text is invalid")

    def _build_result(
        self, task_id: str, response: dict[str, object], thread_id: str, turn_id: str
    ) -> bytes:
        thread = _mapping(response.get("thread"))
        if thread.get("id") != thread_id:
            raise ProtocolError("history thread identity mismatch")
        turns = thread.get("turns")
        if not isinstance(turns, list):
            raise ProtocolError("history turns are missing")
        bound = [item for item in turns if isinstance(item, dict) and item.get("id") == turn_id]
        if len(bound) != 1:
            raise ProtocolError("bound turn is missing or duplicated")
        turn = cast("dict[str, object]", bound[0])
        if turn.get("status") != "completed" or turn.get("itemsView", "full") != "full":
            raise UncertainError("full history is unavailable")
        items = turn.get("items")
        if not isinstance(items, list):
            raise ProtocolError("full turn items are missing")
        ids: set[str] = set()
        history_items: dict[str, dict[str, object]] = {}
        messages: list[dict[str, str]] = []
        completed = _mapping(self.store.load(task_id).get("completed_items"))
        for candidate in items:
            item = _mapping(candidate)
            item_id = item.get("id")
            if not isinstance(item_id, str) or not item_id or item_id in ids:
                raise ProtocolError("history item identity is invalid")
            ids.add(item_id)
            history_items[item_id] = item
            if item_id in completed and completed[item_id] != item:
                raise ProtocolError("history conflicts with completed item")
            item_type = item.get("type")
            if not isinstance(item_type, str) or not item_type:
                raise ProtocolError("history item type is invalid")
            if item_type == "agentMessage":
                text = item.get("text")
                if not isinstance(text, str):
                    raise ProtocolError("agent message text is invalid")
                phase = item.get("phase")
                if phase is not None and phase not in {"commentary", "final_answer"}:
                    raise ProtocolError("history item phase is unsupported")
                if phase == "final_answer":
                    messages.append({"id": item_id, "text": text})
        if not set(completed) <= ids:
            raise ProtocolError("history omits or conflicts with a completed item")
        for item_id, completed_item in completed.items():
            if history_items[item_id] != completed_item:
                raise ProtocolError("history omits or conflicts with a completed item")
        if not messages:
            raise UncertainError("output_phase_unknown")
        return json.dumps(
            {"format": "codex-final-messages-v1", "messages": messages},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

    async def _request(
        self, task_id: str, client: AppServerClient, method: str, params: JsonObject
    ) -> JsonObject:
        if self._deadline_reached(task_id):
            raise UncertainError("deadline_exceeded")
        try:
            async with asyncio.timeout(self._remaining_seconds(task_id)):
                response = await client.request(method, params)
        except TimeoutError as exc:
            raise UncertainError("deadline_exceeded") from exc
        except ProtocolError:
            raw = getattr(client, "last_response_raw", None)
            if not isinstance(raw, bytes):
                raw = getattr(client, "last_failure_raw", None)
            if isinstance(raw, bytes):
                self.store.append_event(task_id, raw)
            raise
        raw = getattr(client, "last_response_raw", None)
        if not isinstance(raw, bytes):
            raw = canonical_json({"direction": "response", "method": method, "value": response})
        self.store.append_event(task_id, raw)
        return response

    async def _interrupt(self, task_id: str, client: AppServerClient) -> None:
        record = self.store.load(task_id)
        if _boolean(record.get("interrupt_sent")):
            return
        thread_id = record.get("thread_id")
        turn_id = record.get("turn_id")
        if not isinstance(thread_id, str) or not isinstance(turn_id, str):
            return
        self._set_fields(task_id, {"interrupt_sent": True})
        try:
            async with asyncio.timeout(5.0):
                response = await client.request(
                    "turn/interrupt", {"threadId": thread_id, "turnId": turn_id}
                )
            raw = getattr(client, "last_response_raw", None)
            if not isinstance(raw, bytes):
                raw = canonical_json(
                    {"direction": "response", "method": "turn/interrupt", "value": response}
                )
            self.store.append_event(task_id, raw)
        except (TimeoutError, ProtocolError):
            raw = getattr(client, "last_response_raw", None)
            if not isinstance(raw, bytes):
                raw = getattr(client, "last_failure_raw", None)
            if isinstance(raw, bytes):
                self.store.append_event(task_id, raw)
            self._set_state(task_id, AdapterTaskState.UNKNOWN, "interrupt_ack_unknown")

    def _schedule_interrupt(self, task_id: str) -> None:
        client = self._clients.get(task_id)
        if client is not None:
            interrupt = asyncio.create_task(self._interrupt(task_id, client))
            self._interrupts.add(interrupt)
            interrupt.add_done_callback(self._interrupts.discard)

    def _thread_params(self, task_id: str) -> JsonObject:
        record = self.store.load(task_id)
        request = _mapping(record.get("request"))
        cwd = cast("str", request["cwd"])
        sandbox = cast("str", request["sandbox"])
        params: JsonObject = {
            "cwd": cwd,
            "model": request["model"],
            "modelProvider": "openai",
            "approvalPolicy": "never",
            "approvalsReviewer": "user",
            "sandbox": sandbox,
            "threadSource": "codex-mcp-app-server-bridge",
        }
        if sandbox == "workspace-write":
            params["config"] = {
                "sandbox_workspace_write": {
                    "writable_roots": [cwd],
                    "network_access": False,
                    "exclude_tmpdir_env_var": True,
                    "exclude_slash_tmp": True,
                }
            }
        return params

    def _turn_params(self, task_id: str, thread_id: str) -> JsonObject:
        record = self.store.load(task_id)
        request = _mapping(record.get("request"))
        sandbox = cast("str", request["sandbox"])
        policy: JsonObject
        if sandbox == "read-only":
            policy = {"type": "readOnly", "networkAccess": False}
        else:
            policy = {
                "type": "workspaceWrite",
                "writableRoots": [request["cwd"]],
                "networkAccess": False,
                "excludeTmpdirEnvVar": True,
                "excludeSlashTmp": True,
            }
        return {
            "threadId": thread_id,
            "input": [{"type": "text", "text": request["prompt"]}],
            "approvalPolicy": "never",
            "approvalsReviewer": "user",
            "sandboxPolicy": policy,
        }

    def _verify_permissions(self, task_id: str, response: JsonObject) -> None:
        record = self.store.load(task_id)
        request = _mapping(record.get("request"))
        if response.get("cwd") != request.get("cwd") or response.get("model") != request.get(
            "model"
        ):
            raise PermissionError("effective runtime identity does not match request")
        if response.get("modelProvider") != "openai":
            raise PermissionError("effective model provider is not openai")
        if response.get("approvalPolicy") != "never" or response.get("approvalsReviewer") != "user":
            raise PermissionError("effective approval settings are not fixed")
        sandbox = _mapping(response.get("sandbox"))
        requested = request.get("sandbox")
        expected_type = "readOnly" if requested == "read-only" else "workspaceWrite"
        if sandbox.get("type") != expected_type or sandbox.get("networkAccess", False) is not False:
            raise PermissionError("effective sandbox is broader than the bound profile")
        if requested == "workspace-write":
            roots = sandbox.get("writableRoots", [])
            if not isinstance(roots, list):
                raise PermissionError("workspace-write returned malformed roots")
            normalized_cwd = Path(cast("str", request["cwd"])).resolve()
            for root in roots:
                if not isinstance(root, str) or Path(root).resolve() != normalized_cwd:
                    raise PermissionError("effective writable root escapes the bound cwd")
            if (
                sandbox.get("excludeTmpdirEnvVar") is not True
                or sandbox.get("excludeSlashTmp") is not True
            ):
                raise PermissionError("temporary roots are not excluded")

    def _stop_or_expired(self, task_id: str) -> bool:
        record = self.store.load(task_id)
        if record.get("stop_intent") is not None:
            if not _boolean(record.get("turn_submission_started")):
                if record.get("stop_intent") == "expiry":
                    self._set_state(task_id, AdapterTaskState.FAILED, "deadline_before_dispatch")
                else:
                    self._set_state(
                        task_id, AdapterTaskState.CANCELLED, "cancelled_before_dispatch"
                    )
            elif not _boolean(record.get("turn_submitted")):
                self._set_state(task_id, AdapterTaskState.UNKNOWN, "termination_unconfirmed")
            return True
        if self._deadline_reached(task_id):
            self._set_stop_intent(task_id, "expiry")
            self._set_state(task_id, AdapterTaskState.FAILED, "deadline_before_dispatch")
            return True
        return False

    def _deadline_reached(self, task_id: str) -> bool:
        record = self.store.load(task_id)
        deadline_mono = self._monotonic_deadlines.get(task_id)
        if deadline_mono is not None:
            return self._monotonic() >= deadline_mono
        value = record.get("deadline_utc")
        if not isinstance(value, str):
            return True
        return self.clock().astimezone(UTC) >= datetime.fromisoformat(value)

    def _deadline(self, record: Record) -> datetime:
        value = record.get("deadline_utc")
        if not isinstance(value, str):
            raise ValidationError("task deadline is invalid")
        return datetime.fromisoformat(value)

    def _remaining_seconds(self, task_id: str) -> float:
        record = self.store.load(task_id)
        deadline_mono = self._monotonic_deadlines.get(task_id)
        if deadline_mono is not None:
            return max(0.001, deadline_mono - self._monotonic())
        return max(0.001, (self._deadline(record) - self.clock().astimezone(UTC)).total_seconds())

    def _set_stop_intent(self, task_id: str, intent: str) -> None:
        def update(record: Record) -> None:
            if record.get("stop_intent") is None:
                record.update(
                    {
                        "stop_intent": intent,
                        "stop_requested_at_utc": self.clock().astimezone(UTC).isoformat(),
                    }
                )

        self.store.mutate(task_id, update)
        self._notify()

    def _same_profile(self, record: Record) -> bool:
        return (
            record.get("runtime_path") == str(self.profile.runtime_path)
            and record.get("runtime_version") == self.profile.runtime_version
            and record.get("runtime_sha256") == self.profile.runtime_sha256.lower()
            and record.get("profile_sha256") == self.profile.identity_digest()
        )

    def _verify_cwd_identity(self, task_id: str) -> None:
        record = self.store.load(task_id)
        expected = _mapping(record.get("cwd_identity"))
        path = expected.get("path")
        components = expected.get("components")
        if not isinstance(path, str) or not isinstance(components, list):
            raise ValidationError("cwd identity is invalid")
        try:
            bound_path = Path(path)
            actual = _path_identity(bound_path)
            resolved_path = bound_path.resolve(strict=True)
            resolved_roots = tuple(root.resolve(strict=True) for root in self.profile.allowed_worktree_roots)
        except (OSError, RuntimeError) as exc:
            raise ValidationError("bound cwd identity changed") from exc
        if actual != expected:
            raise ValidationError("bound cwd identity changed")
        request = _mapping(record.get("request"))
        if (
            request.get("cwd") != path
            or resolved_path != bound_path
            or not _within_path(resolved_path, resolved_roots)
        ):
            raise ValidationError("bound cwd is outside the allowed worktree")

    def _set_state(self, task_id: str, state: AdapterTaskState, reason: str | None) -> None:
        def update(record: Record) -> None:
            current = _state(record)
            if current in _TERMINAL and current != AdapterTaskState.UNKNOWN:
                return
            record["state"] = state.value
            record["reason"] = reason

        self.store.mutate(task_id, update)
        self._notify()

    def _set_state_if_active(
        self, task_id: str, state: AdapterTaskState, reason: str | None
    ) -> None:
        if _state(self.store.load(task_id)) not in _TERMINAL:
            self._set_state(task_id, state, reason)

    def _set_fields(self, task_id: str, fields: Mapping[str, object]) -> None:
        self.store.mutate(task_id, lambda record: record.update(fields))
        self._notify()

    def _uncertain_or_failed(self, task_id: str, reason: str) -> None:
        record = self.store.load(task_id)
        if _state(record) in _TERMINAL:
            return
        if reason == "deadline_exceeded":
            self._set_stop_intent(task_id, "expiry")
            record = self.store.load(task_id)
        before_turn_submission = not _boolean(record.get("turn_submission_started"))
        effective_reason = (
            "deadline_before_dispatch"
            if reason == "deadline_exceeded" and before_turn_submission
            else reason
        )
        uncertain = (
            _boolean(record.get("turn_submitted"))
            or _boolean(record.get("turn_submission_started"))
            or _boolean(record.get("thread_submission_started"))
        )
        if reason == "deadline_exceeded" and before_turn_submission:
            uncertain = False
        self._set_state(
            task_id,
            AdapterTaskState.UNKNOWN if uncertain else AdapterTaskState.FAILED,
            effective_reason,
        )
        current = self.store.load(task_id)
        if (
            _state(current) == AdapterTaskState.UNKNOWN
            and isinstance(current.get("thread_id"), str)
            and isinstance(current.get("turn_id"), str)
        ):
            self._schedule_supervision(task_id)

    def _schedule_supervision(self, task_id: str) -> None:
        existing = self._supervisors.get(task_id)
        if existing is not None and not existing.done():
            return
        supervisor = asyncio.create_task(self._supervise_unknown(task_id))
        self._supervisors[task_id] = supervisor
        supervisor.add_done_callback(lambda _: self._supervisors.pop(task_id, None))

    def _schedule_reconciliation(self, task_id: str) -> None:
        existing = self._reconciliations.get(task_id)
        if existing is not None and not existing.done():
            return
        reconciliation = asyncio.create_task(self._reconcile_known_task(task_id))
        self._reconciliations[task_id] = reconciliation
        reconciliation.add_done_callback(lambda _: self._reconciliations.pop(task_id, None))

    async def _supervise_unknown(self, task_id: str) -> None:
        try:
            await asyncio.sleep(self._remaining_seconds(task_id))
            record = self.store.load(task_id)
            if (
                _state(record) == AdapterTaskState.UNKNOWN
                and isinstance(record.get("thread_id"), str)
                and isinstance(record.get("turn_id"), str)
            ):
                if record.get("stop_intent") is None:
                    self._set_stop_intent(task_id, "expiry")
                await self._reconcile_known_task(task_id)
        except asyncio.CancelledError:
            raise
        except (OSError, ProtocolError, ValidationError, UncertainError, TimeoutError) as exc:
            self._set_state(task_id, AdapterTaskState.UNKNOWN, str(exc))

    def _changed(
        self,
        task_id: str,
        cursor: int,
        initial_state: object = None,
        initial_reason: object = None,
        *,
        include_sequence: bool = True,
    ) -> bool:
        try:
            record = self.store.load(task_id)
            return (
                (include_sequence and _integer(record.get("last_event_sequence")) > cursor)
                or record.get("state") != initial_state
                or record.get("reason") != initial_reason
            )
        except NotFoundError:
            return True

    def _notify(self) -> None:
        if self._condition.locked():
            self._condition.notify_all()
        else:
            asyncio.create_task(self._notify_async())

    async def _notify_async(self) -> None:
        async with self._condition:
            self._condition.notify_all()

    def _records(self) -> list[tuple[str, Record]]:
        result: list[tuple[str, Record]] = []
        for path in self.store.tasks.glob("*.json"):
            try:
                record = self.store.load(path.stem)
            except (NotFoundError, OSError, ValidationError):
                continue
            result.append((path.stem, record))
        return result

    def _reconcile_restarted_tasks(self) -> None:
        for task_id, record in self._records():
            if _state(record) not in _TERMINAL:
                self.store.mutate(
                    task_id,
                    lambda current: current.update(
                        {"state": AdapterTaskState.UNKNOWN.value, "reason": "adapter_restarted"}
                    ),
                )


_TERMINAL = {
    AdapterTaskState.COMPLETED,
    AdapterTaskState.FAILED,
    AdapterTaskState.CANCELLED,
    AdapterTaskState.UNKNOWN,
}


def _public_status(record: Record) -> JsonObject:
    journal = record.get("native_journal")
    evidence_status = (
        journal.get("evidence_status", "unavailable")
        if isinstance(journal, dict)
        else "unavailable"
    )
    return {
        "task_id": record.get("task_id"),
        "request_id": record.get("request_id"),
        "state": record.get("state"),
        "reason": record.get("reason"),
        "thread_id": record.get("thread_id"),
        "turn_id": record.get("turn_id"),
        "turn_submitted": record.get("turn_submitted"),
        "stop_intent": record.get("stop_intent"),
        "stop_requested_at_utc": record.get("stop_requested_at_utc"),
        "deadline_utc": record.get("deadline_utc"),
        "cursor": record.get("last_event_sequence"),
        "provider_outcome": record.get("provider_outcome"),
        "native_evidence_status": evidence_status,
    }


def _state(record: Record) -> AdapterTaskState:
    value = record.get("state")
    if not isinstance(value, str):
        raise ValidationError("task state is invalid")
    try:
        return AdapterTaskState(value)
    except ValueError as exc:
        raise ValidationError("task state is invalid") from exc


def _path_identity(path: Path) -> dict[str, object]:
    components: list[dict[str, object]] = []
    current = path
    while True:
        info = os.lstat(current)
        if stat.S_ISLNK(info.st_mode) or (os.name == "nt" and info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise OSError("bound path component is a symlink")
        info = os.stat(current)
        components.append(
            {"path": str(current), "device": info.st_dev, "inode": info.st_ino}
        )
        parent = current.parent
        if parent == current:
            break
        current = parent
    components.reverse()
    return {"path": str(path), "components": components}


def _within_path(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(path == root or root in path.parents for root in roots)


def _thread_id(response: JsonObject) -> str:
    thread = _mapping(response.get("thread"))
    value = thread.get("id")
    if not isinstance(value, str) or not value:
        raise ProtocolError("thread/start returned no thread id")
    return value


def _turn_id(response: JsonObject) -> str:
    turn = _mapping(response.get("turn"))
    value = turn.get("id")
    if not isinstance(value, str) or not value:
        raise ProtocolError("turn/start returned no turn id")
    return value


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ProtocolError("native object is missing")
    return cast("dict[str, object]", value)


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError("integer field is invalid")
    return value


def _boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValidationError("boolean field is invalid")
    return value
