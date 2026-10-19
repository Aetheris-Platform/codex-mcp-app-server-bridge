"""Focused deterministic tests for the bounded Codex app-server adapter."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from codex_app_server_adapter.errors import (
    ConflictError,
    ProtocolError,
    ValidationError,
)
from codex_app_server_adapter.profile import OperatorProfile
from codex_app_server_adapter.protocol import JsonRpcClient
from codex_app_server_adapter.service import AdapterService
from codex_app_server_adapter.store import TaskStore


class FakeClient:
    def __init__(
        self,
        *,
        permission_mismatch: bool = False,
        fail_turn: bool = False,
        fail_interrupt: bool = False,
        process_lost: bool = False,
        turn_gate: asyncio.Event | None = None,
        thread_gate: asyncio.Event | None = None,
        event_gate: asyncio.Event | None = None,
        connect_gate: asyncio.Event | None = None,
        read_gate: asyncio.Event | None = None,
        terminal_status: str = "completed",
    ) -> None:
        self.permission_mismatch = permission_mismatch
        self.fail_turn = fail_turn
        self.fail_interrupt = fail_interrupt
        self.process_lost = process_lost
        self.turn_gate = turn_gate
        self.thread_gate = thread_gate
        self.event_gate = event_gate
        self.connect_gate = connect_gate
        self.read_gate = read_gate
        self.terminal_status = terminal_status
        self.turn_started = asyncio.Event()
        self.thread_started = asyncio.Event()
        self.connect_started = asyncio.Event()
        self.read_started = asyncio.Event()
        self.interrupt_started = asyncio.Event()
        self.last_response_raw: bytes | None = None
        self.thread_response_hook: object = None
        self.events: asyncio.Queue[tuple[bytes, dict[str, object]]] = asyncio.Queue()
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.connected = False
        self.closed = False

    async def connect(self) -> None:
        self.connected = True
        self.connect_started.set()
        if self.connect_gate is not None:
            await self.connect_gate.wait()

    async def request(
        self, method: str, params: dict[str, object]
    ) -> dict[str, object]:
        self.calls.append((method, params))
        if method == "thread/start":
            self.thread_started.set()
            if self.thread_gate is not None:
                await self.thread_gate.wait()
            if callable(self.thread_response_hook):
                self.thread_response_hook()
            sandbox: dict[str, object]
            if self.permission_mismatch:
                sandbox = {"type": "workspaceWrite", "writableRoots": [params["cwd"]]}
            elif params.get("sandbox") == "workspace-write":
                sandbox = {
                    "type": "workspaceWrite",
                    "writableRoots": [params["cwd"]],
                    "networkAccess": False,
                    "excludeTmpdirEnvVar": True,
                    "excludeSlashTmp": True,
                }
            else:
                sandbox = {"type": "readOnly", "networkAccess": False}
            response = {
                "thread": {"id": "thread-opaque-1"},
                "cwd": params["cwd"],
                "model": params["model"],
                "modelProvider": "openai",
                "approvalPolicy": "never",
                "approvalsReviewer": "user",
                "sandbox": sandbox,
            }
            self.last_response_raw = json.dumps(
                {"direction": "response", "method": method, "value": response},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            return response
        if method == "turn/start":
            self.turn_started.set()
            if self.turn_gate is not None:
                await self.turn_gate.wait()
            if self.fail_turn:
                raise ProtocolError("simulated lost turn acknowledgement")
            item = {
                "id": "item-final",
                "type": "agentMessage",
                "phase": "final_answer",
                "text": "done ✓",
            }
            commentary = {
                "id": "item-commentary",
                "type": "agentMessage",
                "phase": "commentary",
                "text": "working",
            }
            await self.put_event(
                "item/completed",
                {
                    "threadId": "thread-opaque-1",
                    "turnId": "turn-opaque-1",
                    "completedAtMs": 1,
                    "item": item,
                },
            )
            await self.put_event(
                "turn/completed",
                {
                    "threadId": "thread-opaque-1",
                    "turn": {"id": "turn-opaque-1", "status": self.terminal_status},
                },
            )
            self._history = {
                "thread": {
                    "id": "thread-opaque-1",
                    "turns": [
                        {
                            "id": "turn-opaque-1",
                            "status": self.terminal_status,
                            "itemsView": "full",
                            "items": [commentary, item],
                        }
                    ],
                }
            }
            return {"turn": {"id": "turn-opaque-1"}}
        if method == "thread/read":
            self.read_started.set()
            if self.read_gate is not None:
                await self.read_gate.wait()
            return cast("dict[str, object]", self._history)
        if method == "turn/interrupt":
            self.interrupt_started.set()
            if self.fail_interrupt:
                raise ProtocolError("simulated lost interrupt acknowledgement")
            return {}
        raise AssertionError(method)

    async def put_event(self, method: str, params: dict[str, object]) -> None:
        message = {"jsonrpc": "2.0", "method": method, "params": params}
        self.events.put_nowait(
            (json.dumps(message, separators=(",", ":")).encode(), message)
        )

    async def next_event(
        self, timeout_seconds: float
    ) -> tuple[bytes, dict[str, object]] | None:
        if self.process_lost:
            raise OSError("simulated native process loss")
        if self.event_gate is not None:
            await self.event_gate.wait()
        try:
            return await asyncio.wait_for(self.events.get(), timeout_seconds)
        except TimeoutError:
            return None

    async def close(self) -> None:
        self.closed = True


class FakeTransport:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[bytes] = asyncio.Queue()
        self.sent: list[bytes] = []

    async def connect(self) -> None:
        return None

    async def send(self, raw: bytes) -> None:
        self.sent.append(raw)
        message = json.loads(raw)
        if "id" in message:
            response = {"id": message["id"], "result": {"accepted": True}}
            await self.incoming.put(
                json.dumps(response, separators=(",", ":")).encode()
            )

    async def recv(self) -> bytes:
        return await self.incoming.get()

    async def close(self) -> None:
        return None


def test_native_json_rpc_uses_app_server_wire_shape_and_retains_raw_response() -> None:
    asyncio.run(
        _test_native_json_rpc_uses_app_server_wire_shape_and_retains_raw_response()
    )


async def _test_native_json_rpc_uses_app_server_wire_shape_and_retains_raw_response() -> (
    None
):
    from codex_app_server_adapter.protocol import JsonObject

    transport = FakeTransport()
    client = JsonRpcClient(transport)
    await client.connect()
    sent_initialize = json.loads(transport.sent[0])
    assert "jsonrpc" not in sent_initialize
    assert sent_initialize["params"]["capabilities"] == {}
    result: JsonObject = await client.request("thread/start", {"cwd": "/private/bridge-rpc"})
    assert result == {"accepted": True}
    assert client.last_response_raw == b'{"id":2,"result":{"accepted":true}}'
    await client.close()


def _service(
    tmp_path: Path, client: FakeClient, now: datetime | None = None
) -> AdapterService:
    runtime = tmp_path / "codex"
    runtime.write_bytes(b"#!/bin/sh\nprintf '%s\\n' '0.154.0'\n")
    os.chmod(runtime, 0o700)
    root = tmp_path / "worktree"
    root.mkdir(mode=0o700)
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    profile = OperatorProfile(
        runtime_path=runtime,
        runtime_version="0.154.0",
        runtime_sha256=hashlib.sha256(runtime.read_bytes()).hexdigest(),
        socket_path=tmp_path / "private" / "app.sock",
        allowed_worktree_roots=(root,),
        allowed_models=("gpt-5.6-luna",),
        allowed_sandboxes=("read-only", "workspace-write"),
        data_root=data,
    )
    (tmp_path / "private").mkdir(mode=0o700)
    return _claimed_service(profile, client, now=now)


def _claimed_service(
    profile: OperatorProfile, client: FakeClient, *, now: datetime | None = None
) -> AdapterService:
    from codex_app_server_adapter.ownership import DataRootOwnership

    ownership = DataRootOwnership(profile.data_root)
    try:
        return AdapterService(
            profile, TaskStore(profile.data_root), lambda: client,
            ownership=ownership, clock=(lambda: now) if now else None,
        )
    except BaseException:
        ownership.close()
        raise


def _request(
    root: Path, *, deadline: datetime | None = None, request_id: str | None = None
) -> dict[str, object]:
    return {
        "request_id": request_id or str(uuid4()),
        "prompt": "bounded prompt",
        "cwd": str(root),
        "model": "gpt-5.6-luna",
        "sandbox": "read-only",
        "deadline_utc": (
            deadline or datetime.now(UTC) + timedelta(minutes=5)
        ).isoformat(),
    }


def test_real_boundaries_retain_complete_history_and_paginate(tmp_path: Path) -> None:
    asyncio.run(_test_real_boundaries_retain_complete_history_and_paginate(tmp_path))


async def _test_real_boundaries_retain_complete_history_and_paginate(
    tmp_path: Path,
) -> None:
    client = FakeClient()
    service = _service(tmp_path, client)
    request = _request(tmp_path / "worktree")

    started = await service.start(request)
    status = await service.status(
        {"task_id": started["task_id"], "wait_seconds": 2, "cursor": 0}
    )
    assert status["state"] == "completed"
    assert [method for method, _ in client.calls] == [
        "thread/start",
        "turn/start",
        "thread/read",
    ]

    first = await service.result(
        {"task_id": started["task_id"], "offset_bytes": 0, "limit_bytes": 8}
    )
    second = await service.result(
        {
            "task_id": started["task_id"],
            "offset_bytes": first["next_offset"],
            "limit_bytes": 4096,
        }
    )
    reconstructed = base64.b64decode(cast("str", first["chunk"])) + base64.b64decode(
        cast("str", second["chunk"])
    )
    assert (
        reconstructed
        == json.dumps(
            {
                "format": "codex-final-messages-v1",
                "messages": [{"id": "item-final", "text": "done ✓"}],
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    )
    assert first["complete"] is True
    await service.shutdown()


def test_permission_is_verified_before_turn_dispatch(tmp_path: Path) -> None:
    asyncio.run(_test_permission_is_verified_before_turn_dispatch(tmp_path))


async def _test_permission_is_verified_before_turn_dispatch(tmp_path: Path) -> None:
    client = FakeClient(permission_mismatch=True)
    service = _service(tmp_path, client)

    started = await service.start(_request(tmp_path / "worktree"))
    status = await service.status({"task_id": started["task_id"], "wait_seconds": 2})
    assert status["state"] == "failed"
    assert status["reason"] == "effective sandbox is broader than the bound profile"
    assert [method for method, _ in client.calls] == ["thread/start"]


def test_lost_turn_ack_is_unknown_and_never_retried(tmp_path: Path) -> None:
    asyncio.run(_test_lost_turn_ack_is_unknown_and_never_retried(tmp_path))


async def _test_lost_turn_ack_is_unknown_and_never_retried(tmp_path: Path) -> None:
    client = FakeClient(fail_turn=True)
    service = _service(tmp_path, client)
    request = _request(tmp_path / "worktree")

    started = await service.start(request)
    status = await service.status({"task_id": started["task_id"], "wait_seconds": 2})
    assert status["state"] == "unknown"
    assert [method for method, _ in client.calls] == ["thread/start", "turn/start"]
    replay = await service.start(request)
    assert replay["task_id"] == started["task_id"]


def test_request_id_conflict_and_strict_negative_inputs(tmp_path: Path) -> None:
    asyncio.run(_test_request_id_conflict_and_strict_negative_inputs(tmp_path))


async def _test_request_id_conflict_and_strict_negative_inputs(tmp_path: Path) -> None:
    client = FakeClient()
    service = _service(tmp_path, client)
    request_id = str(uuid4())
    request = _request(tmp_path / "worktree", request_id=request_id)
    await service.start(request)
    changed = dict(request, prompt="different")
    with pytest.raises(ConflictError):
        await service.start(changed)
    with pytest.raises(ValidationError):
        await service.start(dict(request, request_id="not-a-uuid"))
    with pytest.raises(ValidationError):
        await service.status({"task_id": "not-a-uuid", "wait_seconds": True})


def test_cancel_before_turn_dispatch_is_local_and_does_not_claim_provider_stop(
    tmp_path: Path,
) -> None:
    asyncio.run(
        _test_cancel_before_turn_dispatch_is_local_and_does_not_claim_provider_stop(
            tmp_path
        )
    )


async def _test_cancel_before_turn_dispatch_is_local_and_does_not_claim_provider_stop(
    tmp_path: Path,
) -> None:
    client = FakeClient()
    service = _service(tmp_path, client)
    request = _request(tmp_path / "worktree")
    started = await service.start(request)
    cancelled = await service.cancel({"task_id": started["task_id"]})
    assert cancelled["state"] == "cancelled"
    assert cancelled["reason"] == "cancelled_before_dispatch"
    await asyncio.sleep(0)
    assert [method for method, _ in client.calls] == []


def test_cancel_during_turn_ack_records_stop_without_false_local_cancel(
    tmp_path: Path,
) -> None:
    asyncio.run(
        _test_cancel_during_turn_ack_records_stop_without_false_local_cancel(tmp_path)
    )


async def _test_cancel_during_turn_ack_records_stop_without_false_local_cancel(
    tmp_path: Path,
) -> None:
    gate = asyncio.Event()
    client = FakeClient(turn_gate=gate)
    service = _service(tmp_path, client)
    started = await service.start(_request(tmp_path / "worktree"))
    await asyncio.wait_for(client.turn_started.wait(), timeout=2)

    requested = await service.cancel({"task_id": started["task_id"]})
    assert requested["state"] == "cancel-requested"
    assert requested["reason"] == "cancellation_requested"
    gate.set()
    final = await service.status(
        {
            "task_id": started["task_id"],
            "wait_seconds": 2,
            "cursor": requested["cursor"],
        }
    )
    assert final["state"] == "failed"
    assert final["reason"] == "provider_completed_after_stop"
    assert [method for method, _ in client.calls] == [
        "thread/start",
        "turn/start",
        "turn/interrupt",
    ]


def test_cancel_while_thread_start_is_pending_never_dispatches_turn(
    tmp_path: Path,
) -> None:
    asyncio.run(_test_cancel_while_thread_start_is_pending_never_dispatches_turn(tmp_path))


async def _test_cancel_while_thread_start_is_pending_never_dispatches_turn(
    tmp_path: Path,
) -> None:
    gate = asyncio.Event()
    client = FakeClient(thread_gate=gate)
    service = _service(tmp_path, client)
    started = await service.start(_request(tmp_path / "worktree"))
    await asyncio.wait_for(client.thread_started.wait(), timeout=2)

    requested = await service.cancel({"task_id": started["task_id"]})
    assert requested["state"] == "cancelled"
    assert requested["reason"] == "cancelled_before_dispatch"
    assert requested["stop_intent"] == "cancel"
    gate.set()
    final = await service.status(
        {"task_id": started["task_id"], "wait_seconds": 2, "cursor": requested["cursor"]}
    )
    assert final["state"] == "cancelled"
    assert final["reason"] == "cancelled_before_dispatch"
    assert [method for method, _ in client.calls] == ["thread/start"]


def test_native_process_loss_after_turn_submission_is_unknown_and_not_retried(
    tmp_path: Path,
) -> None:
    asyncio.run(
        _test_native_process_loss_after_turn_submission_is_unknown_and_not_retried(tmp_path)
    )


async def _test_native_process_loss_after_turn_submission_is_unknown_and_not_retried(
    tmp_path: Path,
) -> None:
    client = FakeClient(process_lost=True)
    service = _service(tmp_path, client)
    request = _request(tmp_path / "worktree")
    started = await service.start(request)
    status = await service.status({"task_id": started["task_id"], "wait_seconds": 2})
    assert status["state"] == "unknown"
    assert status["reason"] == "adapter_internal_failure"
    replay = await service.start(request)
    assert replay["task_id"] == started["task_id"]
    assert [method for method, _ in client.calls] == ["thread/start", "turn/start"]
    await service.shutdown()


def test_connection_loss_before_deadline_supervises_expiry_interrupt_without_resubmit(
    tmp_path: Path,
) -> None:
    asyncio.run(
        _test_connection_loss_before_deadline_supervises_expiry_interrupt_without_resubmit(
            tmp_path
        )
    )


async def _test_connection_loss_before_deadline_supervises_expiry_interrupt_without_resubmit(
    tmp_path: Path,
) -> None:
    client = FakeClient(process_lost=True)
    service = _service(tmp_path, client)
    started = await service.start(
        _request(tmp_path / "worktree", deadline=datetime.now(UTC) + timedelta(seconds=1))
    )
    initial = await service.status({"task_id": started["task_id"], "wait_seconds": 2})
    assert initial["state"] == "unknown"
    await asyncio.wait_for(client.interrupt_started.wait(), timeout=3)
    final = await service.status({"task_id": started["task_id"]})
    assert final["state"] == "unknown"
    assert final["stop_intent"] == "expiry"
    assert [method for method, _ in client.calls] == [
        "thread/start",
        "turn/start",
        "turn/interrupt",
    ]
    await service.shutdown()


def test_cwd_substitution_before_thread_start_fails_without_provider_request(
    tmp_path: Path,
) -> None:
    asyncio.run(_test_cwd_substitution_before_thread_start_fails_without_provider_request(tmp_path))


async def _test_cwd_substitution_before_thread_start_fails_without_provider_request(
    tmp_path: Path,
) -> None:
    connect_gate = asyncio.Event()
    client = FakeClient(connect_gate=connect_gate)
    service = _service(tmp_path, client)
    started = await service.start(_request(tmp_path / "worktree"))
    await asyncio.wait_for(client.connect_started.wait(), timeout=2)
    original = tmp_path / "worktree"
    original.rename(tmp_path / "original-worktree")
    original.mkdir(mode=0o700)
    connect_gate.set()
    status = await service.status({"task_id": started["task_id"], "wait_seconds": 2})
    assert status["state"] == "failed"
    assert status["reason"] == "bound cwd identity changed"
    assert client.calls == []
    await service.shutdown()


def test_same_inode_cwd_symlink_before_thread_start_fails_without_provider_request(
    tmp_path: Path,
) -> None:
    asyncio.run(
        _test_same_inode_cwd_symlink_before_thread_start_fails_without_provider_request(
            tmp_path
        )
    )


async def _test_same_inode_cwd_symlink_before_thread_start_fails_without_provider_request(
    tmp_path: Path,
) -> None:
    connect_gate = asyncio.Event()
    client = FakeClient(connect_gate=connect_gate)
    service = _service(tmp_path, client)
    started = await service.start(_request(tmp_path / "worktree"))
    await asyncio.wait_for(client.connect_started.wait(), timeout=2)
    original = tmp_path / "worktree"
    relocated = tmp_path / "relocated-worktree"
    original.rename(relocated)
    original.symlink_to(relocated, target_is_directory=True)
    assert original.stat().st_dev == relocated.stat().st_dev
    assert original.stat().st_ino == relocated.stat().st_ino
    connect_gate.set()
    status = await service.status({"task_id": started["task_id"], "wait_seconds": 2})
    assert status["state"] == "failed"
    assert status["reason"] == "bound cwd identity changed"
    assert client.calls == []
    await service.shutdown()


def test_cwd_substitution_after_permission_verification_blocks_turn_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asyncio.run(
        _test_cwd_substitution_after_permission_verification_blocks_turn_start(
            tmp_path, monkeypatch
        )
    )


async def _test_cwd_substitution_after_permission_verification_blocks_turn_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeClient()
    service = _service(tmp_path, client)
    original_verify = service._verify_permissions

    def verify_then_replace(task_id: str, response: dict[str, object]) -> None:
        original_verify(task_id, response)
        original = tmp_path / "worktree"
        original.rename(tmp_path / "original-worktree")
        original.mkdir(mode=0o700)

    monkeypatch.setattr(service, "_verify_permissions", verify_then_replace)
    started = await service.start(_request(tmp_path / "worktree"))
    status = await service.status({"task_id": started["task_id"], "wait_seconds": 2})
    assert status["state"] == "unknown"
    assert status["reason"] == "bound cwd identity changed"
    assert [method for method, _ in client.calls] == ["thread/start"]
    await service.shutdown()


def test_same_inode_cwd_symlink_after_permission_verification_blocks_turn_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asyncio.run(
        _test_same_inode_cwd_symlink_after_permission_verification_blocks_turn_start(
            tmp_path, monkeypatch
        )
    )


async def _test_same_inode_cwd_symlink_after_permission_verification_blocks_turn_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeClient()
    service = _service(tmp_path, client)
    original_verify = service._verify_permissions

    def verify_then_replace(task_id: str, response: dict[str, object]) -> None:
        original_verify(task_id, response)
        original = tmp_path / "worktree"
        relocated = tmp_path / "relocated-worktree"
        original.rename(relocated)
        original.symlink_to(relocated, target_is_directory=True)
        assert original.stat().st_dev == relocated.stat().st_dev
        assert original.stat().st_ino == relocated.stat().st_ino

    monkeypatch.setattr(service, "_verify_permissions", verify_then_replace)
    started = await service.start(_request(tmp_path / "worktree"))
    status = await service.status({"task_id": started["task_id"], "wait_seconds": 2})
    assert status["state"] == "unknown"
    assert status["reason"] == "bound cwd identity changed"
    assert [method for method, _ in client.calls] == ["thread/start"]
    await service.shutdown()


def test_same_inode_ancestor_symlink_before_thread_start_fails_without_provider_request(
    tmp_path: Path,
) -> None:
    asyncio.run(
        _test_same_inode_ancestor_symlink_before_thread_start_fails_without_provider_request(
            tmp_path
        )
    )


async def _test_same_inode_ancestor_symlink_before_thread_start_fails_without_provider_request(
    tmp_path: Path,
) -> None:
    connect_gate = asyncio.Event()
    client = FakeClient(connect_gate=connect_gate)
    service = _service(tmp_path, client)
    cwd_parent = tmp_path / "worktree" / "parent"
    cwd_parent.mkdir(mode=0o700)
    cwd = cwd_parent / "child"
    cwd.mkdir(mode=0o700)
    started = await service.start(_request(cwd))
    await asyncio.wait_for(client.connect_started.wait(), timeout=2)
    relocated = tmp_path / "relocated-parent"
    cwd_parent.rename(relocated)
    cwd_parent.symlink_to(relocated, target_is_directory=True)
    assert cwd_parent.stat().st_dev == relocated.stat().st_dev
    assert cwd_parent.stat().st_ino == relocated.stat().st_ino
    connect_gate.set()
    status = await service.status({"task_id": started["task_id"], "wait_seconds": 2})
    assert status["state"] == "failed"
    assert status["reason"] == "bound cwd identity changed"
    assert client.calls == []
    await service.shutdown()


def test_same_inode_ancestor_symlink_after_permission_verification_blocks_turn_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asyncio.run(
        _test_same_inode_ancestor_symlink_after_permission_verification_blocks_turn_start(
            tmp_path, monkeypatch
        )
    )


async def _test_same_inode_ancestor_symlink_after_permission_verification_blocks_turn_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeClient()
    service = _service(tmp_path, client)
    cwd_parent = tmp_path / "worktree" / "parent"
    cwd_parent.mkdir(mode=0o700)
    cwd = cwd_parent / "child"
    cwd.mkdir(mode=0o700)
    original_verify = service._verify_permissions

    def verify_then_replace(task_id: str, response: dict[str, object]) -> None:
        original_verify(task_id, response)
        relocated = tmp_path / "relocated-parent"
        cwd_parent.rename(relocated)
        cwd_parent.symlink_to(relocated, target_is_directory=True)
        assert cwd_parent.stat().st_dev == relocated.stat().st_dev
        assert cwd_parent.stat().st_ino == relocated.stat().st_ino

    monkeypatch.setattr(service, "_verify_permissions", verify_then_replace)
    started = await service.start(_request(cwd))
    status = await service.status({"task_id": started["task_id"], "wait_seconds": 2})
    assert status["state"] == "unknown"
    assert status["reason"] == "bound cwd identity changed"
    assert [method for method, _ in client.calls] == ["thread/start"]
    await service.shutdown()


def test_persisted_cwd_component_substitution_is_rejected_before_provider_use(
    tmp_path: Path,
) -> None:
    asyncio.run(
        _test_persisted_cwd_component_substitution_is_rejected_before_provider_use(tmp_path)
    )


async def _test_persisted_cwd_component_substitution_is_rejected_before_provider_use(
    tmp_path: Path,
) -> None:
    connect_gate = asyncio.Event()
    client = FakeClient(connect_gate=connect_gate)
    service = _service(tmp_path, client)
    started = await service.start(_request(tmp_path / "worktree"))
    await asyncio.wait_for(client.connect_started.wait(), timeout=2)
    record = service.store.load(str(started["task_id"]))
    identity = cast("dict[str, object]", record["cwd_identity"])
    components = cast("list[dict[str, object]]", identity["components"])
    substituted = [dict(component) for component in components]
    substituted[-1]["inode"] = cast("int", substituted[-1]["inode"]) + 1
    service.store.mutate(
        str(started["task_id"]),
        lambda current: cast("dict[str, object]", current["cwd_identity"]).update(
            {"components": substituted}
        ),
    )
    connect_gate.set()
    status = await service.status({"task_id": started["task_id"], "wait_seconds": 2})
    assert status["state"] == "failed"
    assert status["reason"] == "bound cwd identity changed"
    assert client.calls == []
    await service.shutdown()


def test_failed_interrupt_acknowledgement_preserves_unknown_state(tmp_path: Path) -> None:
    asyncio.run(_test_failed_interrupt_acknowledgement_preserves_unknown_state(tmp_path))


async def _test_failed_interrupt_acknowledgement_preserves_unknown_state(
    tmp_path: Path,
) -> None:
    event_gate = asyncio.Event()
    client = FakeClient(fail_interrupt=True, event_gate=event_gate)
    service = _service(tmp_path, client)
    started = await service.start(_request(tmp_path / "worktree"))
    await asyncio.wait_for(client.turn_started.wait(), timeout=2)

    requested = await service.cancel({"task_id": started["task_id"]})
    assert requested["state"] == "cancel-requested"
    event_gate.set()
    final = await service.status(
        {"task_id": started["task_id"], "wait_seconds": 2, "cursor": requested["cursor"]}
    )
    assert final["state"] == "unknown"
    assert final["reason"] == "interrupt_ack_unknown"
    assert [method for method, _ in client.calls].count("turn/interrupt") == 1


def test_event_write_failure_is_truthful_and_does_not_retry(tmp_path: Path) -> None:
    asyncio.run(_test_event_write_failure_is_truthful_and_does_not_retry(tmp_path))


async def _test_event_write_failure_is_truthful_and_does_not_retry(tmp_path: Path) -> None:
    client = FakeClient()
    service = _service(tmp_path, client)

    def fail_append(_task_id: str, _raw: bytes) -> int:
        raise OSError("simulated event write failure")

    service.store.append_event = fail_append  # type: ignore[method-assign]
    started = await service.start(_request(tmp_path / "worktree"))
    status = await service.status({"task_id": started["task_id"], "wait_seconds": 2})
    assert status["state"] == "unknown"
    assert status["reason"] == "adapter_internal_failure"
    assert [method for method, _ in client.calls] == ["thread/start"]


def test_failure_before_intent_persistence_makes_no_provider_request_or_phantom_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeClient()
    service = _service(tmp_path, client)

    def fail_reserve(_record: dict[str, object]) -> object:
        raise OSError("simulated intent persistence failure")

    monkeypatch.setattr(service.store, "reserve_request", fail_reserve)
    with pytest.raises(OSError, match="intent persistence"):
        asyncio.run(service.start(_request(tmp_path / "worktree")))
    assert client.calls == []
    assert tuple(service.store.tasks.glob("*.json")) == ()


def test_event_bytes_survive_one_shot_metadata_failure_and_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asyncio.run(_test_event_bytes_survive_one_shot_metadata_failure_and_repair(tmp_path, monkeypatch))


async def _test_event_bytes_survive_one_shot_metadata_failure_and_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeClient()
    service = _service(tmp_path, client)
    original_write = service.store._write
    armed = False
    failed = False

    def fail_after_pending(path: Path, record: dict[str, object]) -> object:
        nonlocal armed, failed
        if record.get("pending_event") is not None:
            armed = True
        elif armed and not failed and record.get("last_event_sequence") == 1:
            failed = True
            raise OSError("simulated event metadata failure")
        return original_write(path, record)

    monkeypatch.setattr(service.store, "_write", fail_after_pending)
    started = await service.start(_request(tmp_path / "worktree"))
    status = await service.status({"task_id": started["task_id"], "wait_seconds": 2})
    assert status["state"] == "unknown"
    assert status["reason"] == "adapter_internal_failure"
    loaded = service.store.load(str(started["task_id"]))
    assert loaded["last_event_sequence"] == 1
    assert loaded["pending_event"] is None
    event = service.store.artifacts / str(started["task_id"]) / "events" / "00000001.json"
    assert event.read_bytes() == client.last_response_raw


def test_pending_event_repair_rejects_mutated_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asyncio.run(_test_pending_event_repair_rejects_mutated_bytes(tmp_path, monkeypatch))


async def _test_pending_event_repair_rejects_mutated_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeClient()
    service = _service(tmp_path, client)
    started = await service.start(_request(tmp_path / "worktree"))
    status = await service.status({"task_id": started["task_id"], "wait_seconds": 2})
    assert status["state"] == "completed"
    task_id = str(started["task_id"])
    next_sequence = int(service.store.load(task_id)["last_event_sequence"]) + 1
    original_write = service.store._write
    armed = False
    failed = False

    def fail_after_pending(path: Path, record: dict[str, object]) -> object:
        nonlocal armed, failed
        if record.get("pending_event") is not None:
            armed = True
        elif armed and not failed and record.get("last_event_sequence") == next_sequence:
            failed = True
            raise OSError("simulated event metadata failure")
        return original_write(path, record)

    monkeypatch.setattr(service.store, "_write", fail_after_pending)
    with pytest.raises(OSError, match="metadata failure"):
        service.store.append_event(task_id, b'{"adversarial":true}')
    assert failed is True
    event = service.store.artifacts / task_id / "events" / f"{next_sequence:08d}.json"
    event.write_bytes(b'{"tampered":true}')
    with pytest.raises(ValidationError, match="pending event bytes"):
        service.store.load(task_id)
    await service.shutdown()


@pytest.mark.parametrize(
    "contents",
    [pytest.param(b"[]", id="invalid-json-shape"), pytest.param(b"\xff", id="invalid-utf8")],
)
def test_shutdown_reaches_callback_when_a_record_is_unreadable(
    tmp_path: Path, contents: bytes
) -> None:
    async def exercise() -> bool:
        service = _service(tmp_path, FakeClient())
        called = False

        async def callback() -> None:
            nonlocal called
            called = True

        service.shutdown_callback = callback
        corrupt = service.store.tasks / f"{uuid4()}.json"
        corrupt.write_bytes(contents)
        await service.shutdown()
        return called

    assert asyncio.run(exercise()) is True


def test_shutdown_reaches_callback_when_record_processing_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> bool:
        service = _service(tmp_path, FakeClient())
        called = False

        async def callback() -> None:
            nonlocal called
            called = True

        def fail_records() -> list[tuple[str, dict[str, object]]]:
            raise RuntimeError("simulated record processing failure")

        service.shutdown_callback = callback
        monkeypatch.setattr(service, "_records", fail_records)
        with pytest.raises(RuntimeError, match="record processing failure"):
            await service.shutdown()
        return called

    assert asyncio.run(exercise()) is True


@pytest.mark.parametrize("failure", ["result", "metadata"])
def test_terminal_retention_failure_matrix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    asyncio.run(_test_terminal_retention_failure_cannot_report_success(tmp_path, monkeypatch, failure))


async def _test_terminal_retention_failure_cannot_report_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    client = FakeClient()
    service = _service(tmp_path, client)
    if failure == "result":
        def fail_result(_task_id: str, _content: bytes) -> object:
            raise OSError("simulated result write failure")

        monkeypatch.setattr(service.store, "write_result", fail_result)
    else:
        original_write = service.store._write

        def fail_terminal_metadata(path: Path, record: dict[str, object]) -> object:
            if record.get("state") == "completed":
                raise OSError("simulated terminal metadata write failure")
            return original_write(path, record)

        monkeypatch.setattr(service.store, "_write", fail_terminal_metadata)
    started = await service.start(_request(tmp_path / "worktree"))
    status = await service.status({"task_id": started["task_id"], "wait_seconds": 2})
    assert status["state"] == "unknown"
    assert status["reason"] == "terminal_retention_failed"
    assert status["thread_id"] == "thread-opaque-1"
    assert status["turn_id"] == "turn-opaque-1"
    result = await service.result({"task_id": started["task_id"]})
    assert result["complete"] is False
    assert [method for method, _ in client.calls].count("turn/start") == 1


def test_cancel_while_thread_read_is_blocked_fences_result_persistence(tmp_path: Path) -> None:
    asyncio.run(_test_cancel_while_thread_read_is_blocked_fences_result_persistence(tmp_path))


async def _test_cancel_while_thread_read_is_blocked_fences_result_persistence(
    tmp_path: Path,
) -> None:
    read_gate = asyncio.Event()
    client = FakeClient(read_gate=read_gate)
    service = _service(tmp_path, client)
    started = await service.start(_request(tmp_path / "worktree"))
    await asyncio.wait_for(client.read_started.wait(), timeout=2)
    requested = await service.cancel({"task_id": started["task_id"]})
    assert requested["stop_intent"] == "cancel"
    read_gate.set()
    final = await service.status(
        {"task_id": started["task_id"], "wait_seconds": 2, "cursor": requested["cursor"]}
    )
    assert final["state"] == "failed"
    assert final["reason"] == "provider_completed_after_stop"
    result = await service.result({"task_id": started["task_id"]})
    assert result["complete"] is False
    assert [method for method, _ in client.calls].count("turn/start") == 1


def test_deadline_is_not_renewed_by_idempotent_replay(tmp_path: Path) -> None:
    asyncio.run(_test_deadline_is_not_renewed_by_idempotent_replay(tmp_path))


async def _test_deadline_is_not_renewed_by_idempotent_replay(tmp_path: Path) -> None:
    client = FakeClient()
    service = _service(tmp_path, client)
    request_id = str(uuid4())
    deadline = datetime.now(UTC) + timedelta(seconds=2)
    request = _request(tmp_path / "worktree", deadline=deadline, request_id=request_id)
    started = await service.start(request)
    replay = await service.start(request)
    assert replay["task_id"] == started["task_id"]
    assert replay["deadline_utc"] == started["deadline_utc"]


def test_concurrent_duplicate_starts_reserve_one_intent(tmp_path: Path) -> None:
    asyncio.run(_test_concurrent_duplicate_starts_reserve_one_intent(tmp_path))


async def _test_concurrent_duplicate_starts_reserve_one_intent(tmp_path: Path) -> None:
    client = FakeClient()
    service = _service(tmp_path, client)
    request = _request(tmp_path / "worktree")
    first, second = await asyncio.gather(service.start(request), service.start(request))
    assert first["task_id"] == second["task_id"]
    await service.status({"task_id": first["task_id"], "wait_seconds": 2})
    assert [method for method, _ in client.calls] == [
        "thread/start",
        "turn/start",
        "thread/read",
    ]


def test_malformed_provider_terminal_status_is_unknown_and_reloadable(tmp_path: Path) -> None:
    asyncio.run(_test_malformed_provider_terminal_status_is_unknown_and_reloadable(tmp_path))


async def _test_malformed_provider_terminal_status_is_unknown_and_reloadable(
    tmp_path: Path,
) -> None:
    client = FakeClient(terminal_status="not-a-terminal-status")
    service = _service(tmp_path, client)
    started = await service.start(_request(tmp_path / "worktree"))
    status = await service.status({"task_id": started["task_id"], "wait_seconds": 2})
    assert status["state"] == "unknown"
    assert service.store.load(str(started["task_id"]))["provider_outcome"] is None
    await service.shutdown()


def test_known_ids_reconcile_read_only_after_restart(tmp_path: Path) -> None:
    asyncio.run(_test_known_ids_reconcile_read_only_after_restart(tmp_path))


async def _test_known_ids_reconcile_read_only_after_restart(tmp_path: Path) -> None:
    client = FakeClient()
    first = _service(tmp_path, client)
    started = await first.start(_request(tmp_path / "worktree"))
    cursor = 0
    while True:
        original = await first.status(
            {"task_id": started["task_id"], "wait_seconds": 2, "cursor": cursor}
        )
        if original["state"] == "completed":
            break
        cursor = int(original["cursor"])
    first.store.mutate(
        str(started["task_id"]),
        lambda record: record.update({
            "state": "unknown",
            "reason": "adapter_restarted",
            "result": None,
            "provider_outcome": None,
            "artifacts": {
                "events": record["artifacts"]["events"],
                "result": None,
                "native_journal": record["artifacts"]["native_journal"],
            },
        }),
    )
    await first.shutdown()
    second = _claimed_service(first.profile, client)
    persisted = second.store.load(str(started["task_id"]))
    assert persisted["state"] == "unknown"
    assert persisted["reason"] == "adapter_restarted"
    assert persisted["thread_id"] == "thread-opaque-1"
    assert persisted["turn_id"] == "turn-opaque-1"
    reconciled = await second.status({"task_id": started["task_id"], "wait_seconds": 2})
    assert reconciled["state"] == "completed"
    assert [method for method, _ in client.calls].count("turn/start") == 1
    await first.shutdown()
    await second.shutdown()


def test_incomplete_history_stays_unknown_until_later_reconciliation(tmp_path: Path) -> None:
    asyncio.run(_test_incomplete_history_stays_unknown_until_later_reconciliation(tmp_path))


async def _test_incomplete_history_stays_unknown_until_later_reconciliation(
    tmp_path: Path,
) -> None:
    client = FakeClient()
    first = _service(tmp_path, client)
    started = await first.start(_request(tmp_path / "worktree"))
    cursor = 0
    while True:
        status = await first.status(
            {"task_id": started["task_id"], "wait_seconds": 2, "cursor": cursor}
        )
        if status["state"] == "completed":
            break
        cursor = int(status["cursor"])
    first.store.mutate(
        str(started["task_id"]),
        lambda record: record.update({
            "state": "unknown",
            "reason": "adapter_restarted",
            "result": None,
            "provider_outcome": None,
            "artifacts": {
                "events": record["artifacts"]["events"],
                "result": None,
                "native_journal": record["artifacts"]["native_journal"],
            },
        }),
    )
    assert isinstance(client._history, dict)
    thread = client._history["thread"]
    assert isinstance(thread, dict)
    turns = thread["turns"]
    assert isinstance(turns, list)
    assert isinstance(turns[0], dict)
    turns[0]["itemsView"] = "summary"
    await first.shutdown()
    second = _claimed_service(first.profile, client)
    incomplete = await second.status({"task_id": started["task_id"]})
    assert incomplete["state"] == "unknown"
    turns[0]["itemsView"] = "full"
    recovered = await second.status({"task_id": started["task_id"], "wait_seconds": 2})
    assert recovered["state"] == "completed"
    assert [method for method, _ in client.calls].count("turn/start") == 1
    await first.shutdown()
    await second.shutdown()


def test_restarted_status_zero_wait_is_prompt_and_wait30_is_bounded(
    tmp_path: Path,
) -> None:
    asyncio.run(_test_restarted_status_zero_wait_is_prompt_and_wait30_is_bounded(tmp_path))


async def _test_restarted_status_zero_wait_is_prompt_and_wait30_is_bounded(
    tmp_path: Path,
) -> None:
    client = FakeClient()
    first = _service(tmp_path, client)
    started = await first.start(_request(tmp_path / "worktree"))
    cursor = 0
    while True:
        status = await first.status(
            {"task_id": started["task_id"], "wait_seconds": 2, "cursor": cursor}
        )
        if status["state"] == "completed":
            break
        cursor = int(status["cursor"])
    first.store.mutate(
        str(started["task_id"]),
        lambda record: record.update({
            "state": "unknown",
            "reason": "adapter_restarted",
            "result": None,
            "provider_outcome": None,
            "artifacts": {
                "events": record["artifacts"]["events"],
                "result": None,
                "native_journal": record["artifacts"]["native_journal"],
            },
        }),
    )
    read_gate = asyncio.Event()
    client.read_gate = read_gate
    client.read_started.clear()
    await first.shutdown()
    second = _claimed_service(first.profile, client)
    begin = time.monotonic()
    immediate = await second.status({"task_id": started["task_id"], "wait_seconds": 0})
    assert time.monotonic() - begin < 0.5
    assert immediate["state"] == "unknown"
    await asyncio.wait_for(client.read_started.wait(), timeout=2)
    waiting = asyncio.create_task(
        second.status(
            {
                "task_id": started["task_id"],
                "wait_seconds": 30,
                "cursor": int(immediate["cursor"]),
            }
        )
    )
    await asyncio.sleep(0.05)
    assert not waiting.done()
    read_gate.set()
    begin = time.monotonic()
    bounded = await asyncio.wait_for(waiting, timeout=2)
    assert time.monotonic() - begin < 2
    assert bounded["state"] == "completed"
    await first.shutdown()
    await second.shutdown()


def test_cancel_preempts_blocked_restart_recovery_and_fences_late_success(
    tmp_path: Path,
) -> None:
    asyncio.run(
        _test_cancel_preempts_blocked_restart_recovery_and_fences_late_success(tmp_path)
    )


async def _test_cancel_preempts_blocked_restart_recovery_and_fences_late_success(
    tmp_path: Path,
) -> None:
    initial_client = FakeClient()
    first = _service(tmp_path, initial_client)
    started = await first.start(_request(tmp_path / "worktree"))
    status = await first.status({"task_id": started["task_id"], "wait_seconds": 2})
    assert status["state"] == "completed"
    first.store.mutate(
        str(started["task_id"]),
        lambda record: record.update(
            {
                "state": "unknown",
                "reason": "adapter_restarted",
                "result": None,
                "provider_outcome": None,
                "artifacts": {
                    "events": record["artifacts"]["events"],
                    "result": None,
                    "native_journal": record["artifacts"]["native_journal"],
                },
            }
        ),
    )
    read_gate = asyncio.Event()
    recovery_client = FakeClient(read_gate=read_gate)
    await first.shutdown()
    second = _claimed_service(first.profile, recovery_client)
    status_task = asyncio.create_task(
        second.status({"task_id": started["task_id"], "wait_seconds": 30})
    )
    await asyncio.wait_for(recovery_client.read_started.wait(), timeout=2)
    begin = time.monotonic()
    cancelled = await asyncio.wait_for(
        second.cancel({"task_id": started["task_id"]}), timeout=2
    )
    assert time.monotonic() - begin < 1
    assert cancelled["state"] == "unknown"
    assert cancelled["stop_intent"] == "cancel"
    assert [method for method, _ in recovery_client.calls] == [
        "thread/read",
        "turn/interrupt",
    ]
    recovered_wait = await asyncio.wait_for(status_task, timeout=2)
    assert recovered_wait["state"] == "unknown"
    assert recovered_wait["stop_intent"] == "cancel"
    await first.shutdown()
    await second.shutdown()


def test_status_caller_cancellation_propagates_during_recovery_wait(tmp_path: Path) -> None:
    asyncio.run(_test_status_caller_cancellation_propagates_during_recovery_wait(tmp_path))


async def _test_status_caller_cancellation_propagates_during_recovery_wait(
    tmp_path: Path,
) -> None:
    initial_client = FakeClient()
    first = _service(tmp_path, initial_client)
    started = await first.start(_request(tmp_path / "worktree"))
    status = await first.status({"task_id": started["task_id"], "wait_seconds": 2})
    assert status["state"] == "completed"
    first.store.mutate(
        str(started["task_id"]),
        lambda record: record.update(
            {
                "state": "unknown",
                "reason": "adapter_restarted",
                "result": None,
                "provider_outcome": None,
                "artifacts": {
                    "events": record["artifacts"]["events"],
                    "result": None,
                    "native_journal": record["artifacts"]["native_journal"],
                },
            }
        ),
    )
    read_gate = asyncio.Event()
    recovery_client = FakeClient(read_gate=read_gate)
    await first.shutdown()
    second = _claimed_service(first.profile, recovery_client)
    status_task = asyncio.create_task(
        second.status({"task_id": started["task_id"], "wait_seconds": 30})
    )
    await asyncio.wait_for(recovery_client.read_started.wait(), timeout=2)
    status_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await status_task
    read_gate.set()
    await first.shutdown()
    await second.shutdown()


def test_simultaneous_status_and_recovery_cancellation_preserves_caller_cancel(
    tmp_path: Path,
) -> None:
    asyncio.run(
        _test_simultaneous_status_and_recovery_cancellation_preserves_caller_cancel(
            tmp_path
        )
    )


async def _test_simultaneous_status_and_recovery_cancellation_preserves_caller_cancel(
    tmp_path: Path,
) -> None:
    initial_client = FakeClient()
    first = _service(tmp_path, initial_client)
    started = await first.start(_request(tmp_path / "worktree"))
    status = await first.status({"task_id": started["task_id"], "wait_seconds": 2})
    assert status["state"] == "completed"
    first.store.mutate(
        str(started["task_id"]),
        lambda record: record.update(
            {
                "state": "unknown",
                "reason": "adapter_restarted",
                "result": None,
                "provider_outcome": None,
                "artifacts": {
                    "events": record["artifacts"]["events"],
                    "result": None,
                    "native_journal": record["artifacts"]["native_journal"],
                },
            }
        ),
    )
    read_gate = asyncio.Event()
    recovery_client = FakeClient(read_gate=read_gate)
    await first.shutdown()
    second = _claimed_service(first.profile, recovery_client)
    status_task = asyncio.create_task(
        second.status({"task_id": started["task_id"], "wait_seconds": 30})
    )
    await asyncio.wait_for(recovery_client.read_started.wait(), timeout=2)
    await asyncio.sleep(0)
    reconciliation = second._reconciliations.get(str(started["task_id"]))
    assert reconciliation is not None
    assert not status_task.done()
    status_task.cancel()
    reconciliation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await status_task
    read_gate.set()
    await first.shutdown()
    await second.shutdown()


def test_expiry_while_thread_start_is_pending_fails_before_turn_dispatch(tmp_path: Path) -> None:
    asyncio.run(_test_expiry_while_thread_start_is_pending_fails_before_turn_dispatch(tmp_path))


async def _test_expiry_while_thread_start_is_pending_fails_before_turn_dispatch(
    tmp_path: Path,
) -> None:
    gate = asyncio.Event()
    client = FakeClient(thread_gate=gate)
    service = _service(tmp_path, client)
    deadline = datetime.now(UTC) + timedelta(seconds=1)
    started = await service.start(_request(tmp_path / "worktree", deadline=deadline))
    await asyncio.wait_for(client.thread_started.wait(), timeout=2)
    await asyncio.sleep(1.1)
    gate.set()
    status = await service.status({"task_id": started["task_id"], "wait_seconds": 2})
    assert status["state"] == "failed"
    assert status["reason"] == "deadline_before_dispatch"
    assert status["stop_intent"] == "expiry"
    assert [method for method, _ in client.calls] == ["thread/start"]
    await service.shutdown()


def test_expiry_while_turn_start_is_pending_is_unknown_without_resubmit(tmp_path: Path) -> None:
    asyncio.run(_test_expiry_while_turn_start_is_pending_is_unknown_without_resubmit(tmp_path))


async def _test_expiry_while_turn_start_is_pending_is_unknown_without_resubmit(
    tmp_path: Path,
) -> None:
    gate = asyncio.Event()
    client = FakeClient(turn_gate=gate)
    service = _service(tmp_path, client)
    deadline = datetime.now(UTC) + timedelta(seconds=1)
    started = await service.start(_request(tmp_path / "worktree", deadline=deadline))
    await asyncio.wait_for(client.turn_started.wait(), timeout=2)
    await asyncio.sleep(1.1)
    gate.set()
    status = await service.status({"task_id": started["task_id"], "wait_seconds": 2})
    assert status["state"] == "unknown"
    assert status["stop_intent"] == "expiry"
    assert [method for method, _ in client.calls] == ["thread/start", "turn/start"]
    await service.shutdown()


def test_saved_cancel_intent_cannot_reconcile_to_success_after_restart(tmp_path: Path) -> None:
    asyncio.run(_test_saved_cancel_intent_cannot_reconcile_to_success_after_restart(tmp_path))


async def _test_saved_cancel_intent_cannot_reconcile_to_success_after_restart(
    tmp_path: Path,
) -> None:
    client = FakeClient()
    first = _service(tmp_path, client)
    started = await first.start(_request(tmp_path / "worktree"))
    cursor = 0
    while True:
        status = await first.status(
            {"task_id": started["task_id"], "wait_seconds": 2, "cursor": cursor}
        )
        if status["state"] == "completed":
            break
        cursor = int(status["cursor"])
    first.store.mutate(
        str(started["task_id"]),
        lambda record: record.update({
            "state": "unknown",
            "reason": "adapter_restarted",
            "stop_intent": "cancel",
            "stop_requested_at_utc": datetime.now(UTC).isoformat(),
            "result": None,
            "provider_outcome": None,
            "artifacts": {
                "events": record["artifacts"]["events"],
                "result": None,
                "native_journal": record["artifacts"]["native_journal"],
            },
        }),
    )
    await first.shutdown()
    second = _claimed_service(first.profile, client)
    recovered = await second.status({"task_id": started["task_id"], "wait_seconds": 2})
    assert recovered["state"] == "unknown"
    assert recovered["stop_intent"] == "cancel"
    assert [method for method, _ in client.calls].count("turn/start") == 1
    assert [method for method, _ in client.calls].count("turn/interrupt") == 1
    await first.shutdown()
    await second.shutdown()


def test_restart_recovery_refuses_a_different_profile_before_provider_use(tmp_path: Path) -> None:
    asyncio.run(_test_restart_recovery_refuses_a_different_profile_before_provider_use(tmp_path))


async def _test_restart_recovery_refuses_a_different_profile_before_provider_use(
    tmp_path: Path,
) -> None:
    client = FakeClient()
    first = _service(tmp_path, client)
    started = await first.start(_request(tmp_path / "worktree"))
    cursor = 0
    while True:
        status = await first.status(
            {"task_id": started["task_id"], "wait_seconds": 2, "cursor": cursor}
        )
        if status["state"] == "completed":
            break
        cursor = int(status["cursor"])
    first.store.mutate(
        str(started["task_id"]),
        lambda record: record.update({
            "state": "unknown",
            "reason": "adapter_restarted",
            "result": None,
            "provider_outcome": None,
            "artifacts": {
                "events": record["artifacts"]["events"],
                "result": None,
                "native_journal": record["artifacts"]["native_journal"],
            },
        }),
    )
    calls_before = list(client.calls)
    incompatible = replace(first.profile, allowed_models=("gpt-5.6-sol-other",))
    await first.shutdown()
    second = _claimed_service(incompatible, client)
    recovered = await second.status({"task_id": started["task_id"], "wait_seconds": 2})
    assert recovered["state"] == "unknown"
    assert client.calls == calls_before
    await first.shutdown()
    await second.shutdown()


def test_cancel_unknown_with_known_ids_runs_bounded_interrupt_reconciliation(
    tmp_path: Path,
) -> None:
    asyncio.run(
        _test_cancel_unknown_with_known_ids_runs_bounded_interrupt_reconciliation(tmp_path)
    )


async def _test_cancel_unknown_with_known_ids_runs_bounded_interrupt_reconciliation(
    tmp_path: Path,
) -> None:
    initial_client = FakeClient()
    first = _service(tmp_path, initial_client)
    started = await first.start(_request(tmp_path / "worktree"))
    cursor = 0
    while True:
        status = await first.status(
            {"task_id": started["task_id"], "wait_seconds": 2, "cursor": cursor}
        )
        if status["state"] == "completed":
            break
        cursor = int(status["cursor"])
    first.store.mutate(
        str(started["task_id"]),
        lambda record: record.update({
            "state": "unknown",
            "reason": "adapter_restarted",
            "result": None,
            "provider_outcome": None,
            "artifacts": {
                "events": record["artifacts"]["events"],
                "result": None,
                "native_journal": record["artifacts"]["native_journal"],
            },
        }),
    )
    recovery_client = FakeClient()
    await first.shutdown()
    second = _claimed_service(first.profile, recovery_client)
    cancelled = await second.cancel({"task_id": started["task_id"]})
    assert cancelled["state"] == "unknown"
    assert cancelled["stop_intent"] == "cancel"
    assert [method for method, _ in recovery_client.calls] == ["turn/interrupt"]
    assert not second._clients
    assert not second._workers
    await first.shutdown()
    await second.shutdown()


def test_expired_recovery_uses_cleanup_grace_and_interrupts_without_turn_start(
    tmp_path: Path,
) -> None:
    asyncio.run(
        _test_expired_recovery_uses_cleanup_grace_and_interrupts_without_turn_start(tmp_path)
    )


async def _test_expired_recovery_uses_cleanup_grace_and_interrupts_without_turn_start(
    tmp_path: Path,
) -> None:
    initial_client = FakeClient()
    first = _service(tmp_path, initial_client)
    started = await first.start(_request(tmp_path / "worktree"))
    cursor = 0
    while True:
        status = await first.status(
            {"task_id": started["task_id"], "wait_seconds": 2, "cursor": cursor}
        )
        if status["state"] == "completed":
            break
        cursor = int(status["cursor"])
    first.store.mutate(
        str(started["task_id"]),
        lambda record: record.update({
            "state": "unknown",
            "reason": "adapter_restarted",
            "result": None,
            "provider_outcome": None,
            "artifacts": {
                "events": record["artifacts"]["events"],
                "result": None,
                "native_journal": record["artifacts"]["native_journal"],
            },
        }),
    )
    connect_gate = asyncio.Event()
    recovery_client = FakeClient(connect_gate=connect_gate)
    await first.shutdown()
    second = _claimed_service(first.profile, recovery_client)
    second._monotonic_deadlines[str(started["task_id"])] = second._monotonic() - 1
    pending = asyncio.create_task(
        second.status({"task_id": started["task_id"], "wait_seconds": 2})
    )
    await asyncio.wait_for(recovery_client.connect_started.wait(), timeout=2)
    await asyncio.sleep(0.05)
    connect_gate.set()
    recovered = await pending
    assert recovered["state"] == "unknown"
    assert recovered["stop_intent"] == "expiry"
    assert [method for method, _ in recovery_client.calls] == ["turn/interrupt"]
    assert not any(method == "turn/start" for method, _ in recovery_client.calls)
    await first.shutdown()
    await second.shutdown()
