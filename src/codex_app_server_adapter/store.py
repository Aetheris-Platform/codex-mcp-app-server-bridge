"""Private, atomic task and artifact persistence."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import cast

from .errors import NotFoundError, ValidationError
from .models import AdapterTaskState, parse_start
from .validation import canonical_json, loads_object

Record = dict[str, object]
RecordMutator = Callable[[Record], None]


class TaskStore:
    """A small versioned store rooted below one operator-selected private directory."""

    def __init__(self, data_root: Path) -> None:
        self.root = data_root
        self.tasks = data_root / "tasks"
        self.artifacts = data_root / "artifacts"
        for path in (self.root, self.tasks, self.artifacts):
            if path.is_symlink():
                raise ValidationError("store directories may not be symlinks")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.tasks.mkdir(mode=0o700, exist_ok=True)
        self.artifacts.mkdir(mode=0o700, exist_ok=True)
        for path in (self.root, self.tasks, self.artifacts):
            _require_private_directory(path)

    def create(self, record: Record) -> None:
        task_id = _string(record.get("task_id"))
        path = self._record_path(task_id)
        with self._lock(task_id):
            if path.exists():
                raise ValidationError("task record already exists")
            self._write(path, record)

    def reserve_request(self, record: Record) -> Record:
        """Atomically return an existing request or reserve a new intent."""

        request_id = _string(record.get("request_id"))
        task_id = _string(record.get("task_id"))
        request_lock = self.tasks / ".request-reservation.lock"
        with request_lock.open("a+") as handle:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                existing = self.find_request(request_id)
                if existing is not None:
                    return existing
                path = self._record_path(task_id)
                if path.exists() or path.is_symlink():
                    raise ValidationError("task record already exists")
                self._write(path, record)
                return record
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def find_request(self, request_id: str) -> Record | None:
        for path in sorted(self.tasks.glob("*.json")):
            if path.is_symlink():
                raise ValidationError("task record may not be a symlink")
            repair = self._pending_event_commit(path)
            record = self._read(
                path, expected_task_id=path.stem, repair_pending_event=repair
            )
            if repair:
                self._write(path, record)
            if record.get("request_id") == request_id:
                return record
        return None

    def load(self, task_id: str) -> Record:
        path = self._record_path(task_id)
        if path.is_symlink():
            raise ValidationError("task record may not be a symlink")
        if not path.exists():
            raise NotFoundError("task not found")
        with self._lock(task_id):
            repair = self._pending_event_commit(path)
            record = self._read(
                path, expected_task_id=task_id, repair_pending_event=repair
            )
            if repair:
                self._write(path, record)
            return record

    def mutate(self, task_id: str, mutator: RecordMutator) -> Record:
        path = self._record_path(task_id)
        with self._lock(task_id):
            if path.is_symlink() or not path.exists():
                raise NotFoundError("task not found")
            record = self._read(path, expected_task_id=task_id)
            mutator(record)
            _validate_record(record)
            self._write(path, record)
            return record

    def append_event(self, task_id: str, raw: bytes) -> int:
        sequence = _integer(self.load(task_id).get("last_event_sequence")) + 1
        event_name = f"{sequence:08d}.json"
        metadata = {
            "path": f"artifacts/{task_id}/events/{event_name}",
            "sha256": sha256(raw).hexdigest(),
            "size_bytes": len(raw),
        }

        def mark_pending(record: Record) -> None:
            if record.get("pending_event") is not None:
                raise ValidationError("another event commit is pending")
            if _integer(record.get("last_event_sequence")) != sequence - 1:
                raise ValidationError("event sequence changed during append")
            record["pending_event"] = {
                "sequence": sequence,
                **metadata,
            }

        self.mutate(task_id, mark_pending)
        task_artifacts = self.artifacts / task_id
        if task_artifacts.is_symlink():
            raise ValidationError("task artifact directory may not be a symlink")
        task_artifacts.mkdir(mode=0o700, exist_ok=True)
        _require_private_directory(task_artifacts)
        event_dir = task_artifacts / "events"
        if event_dir.is_symlink():
            raise ValidationError("event directory may not be a symlink")
        event_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        _require_private_directory(event_dir)
        event_path = event_dir / event_name
        if event_path.exists() or event_path.is_symlink():
            raise ValidationError("event sequence already exists")
        event_path.write_bytes(raw)

        def finalize(record: Record) -> None:
            if record.get("pending_event") != {"sequence": sequence, **metadata}:
                raise ValidationError("pending event binding changed")
            if _artifact_metadata(event_path, self.root) != metadata:
                raise ValidationError("pending event bytes do not match their binding")
            record["last_event_sequence"] = sequence
            artifacts = _mapping(record.get("artifacts"))
            events = _mapping(artifacts.get("events"))
            events[event_name] = metadata
            record["pending_event"] = None

        self.mutate(task_id, finalize)
        return sequence

    def write_result(self, task_id: str, content: bytes) -> Record:
        result_path = self.artifacts / task_id / "result.json"
        digest = sha256(content).hexdigest()
        existing = self.load(task_id).get("result")
        if existing is not None:
            result = _mapping(existing)
            if result.get("sha256") == digest and result.get("size_bytes") == len(content):
                return self.load(task_id)
            raise ValidationError("terminal result artifact is immutable")
        if result_path.parent.is_symlink() or result_path.is_symlink():
            raise ValidationError("result artifact path may not be a symlink")
        result_path.parent.mkdir(mode=0o700, exist_ok=True)
        _require_private_directory(result_path.parent)
        fd, temporary = tempfile.mkstemp(prefix=".result-", dir=result_path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, result_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

        def update(record: Record) -> None:
            metadata = {
                "path": str(result_path.relative_to(self.root)),
                "sha256": digest,
                "size_bytes": len(content),
            }
            record["result"] = metadata
            artifacts = _mapping(record.get("artifacts"))
            artifacts["result"] = metadata

        return self.mutate(task_id, update)

    def read_result(self, record: Record) -> bytes:
        result = _mapping(record.get("result"))
        relative = _string(result.get("path"))
        relative_path = Path(relative)
        if relative_path.is_absolute() or any(
            part in {"", ".", ".."} for part in relative_path.parts
        ):
            raise ValidationError("result artifact reference is unsafe")
        path = self.root / relative_path
        if path.is_symlink() or any(
            (self.root / parent).is_symlink() for parent in relative_path.parents
        ):
            raise ValidationError("result artifact may not traverse symlinks")
        path = path.resolve()
        try:
            path.relative_to(self.root.resolve())
        except ValueError as exc:
            raise ValidationError("result artifact escapes data root") from exc
        content = path.read_bytes()
        if sha256(content).hexdigest() != _string(result.get("sha256")):
            raise ValidationError("result artifact digest mismatch")
        size = result.get("size_bytes")
        if isinstance(size, bool) or not isinstance(size, int) or size != len(content):
            raise ValidationError("result artifact size mismatch")
        return content

    def copy_native_journal(
        self, task_id: str, source: Path, *, session_id: str, turn_id: str
    ) -> Record:
        """Copy one matching Codex session journal without admitting arbitrary paths."""

        root_value = os.environ.get("CODEX_HOME")
        session_root = Path(root_value).expanduser() if root_value else Path.home() / ".codex"
        session_root = session_root / "sessions"
        if (
            not session_root.is_absolute()
            or _has_symlink_component(session_root)
            or not source.is_absolute()
            or _has_symlink_component(source)
            or not source.is_file()
            or source.is_symlink()
        ):
            raise ValidationError("native journal source is outside the trusted session root")
        try:
            source.relative_to(session_root.resolve(strict=True))
        except (ValueError, FileNotFoundError) as exc:
            raise ValidationError("native journal source is outside the trusted session root") from exc
        content = source.read_bytes()
        evidence_status = _native_journal_status(content, session_id, turn_id)
        task_dir = self.artifacts / task_id
        if task_dir.is_symlink():
            raise ValidationError("task artifact directory may not be a symlink")
        task_dir.mkdir(mode=0o700, exist_ok=True)
        _require_private_directory(task_dir)
        destination = task_dir / "native-session.jsonl"
        if destination.exists() or destination.is_symlink():
            raise ValidationError("native journal artifact is immutable")
        fd, temporary = tempfile.mkstemp(prefix=".journal-", dir=task_dir)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        metadata = {
            "path": str(destination.relative_to(self.root)),
            "sha256": sha256(content).hexdigest(),
            "size_bytes": len(content),
            "session_id": session_id,
            "turn_id": turn_id,
            "evidence_status": evidence_status,
        }
        def update(record: Record) -> None:
            record["native_journal"] = metadata
            artifacts = _mapping(record.get("artifacts"))
            artifacts["native_journal"] = metadata

        return self.mutate(task_id, update)

    def _record_path(self, task_id: str) -> Path:
        try:
            uuid.UUID(task_id)
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValidationError("invalid task identity") from exc
        if not task_id or Path(task_id).name != task_id or task_id.endswith(".json"):
            raise ValidationError("invalid task identity")
        return self.tasks / f"{task_id}.json"

    @contextmanager
    def _lock(self, task_id: str) -> Iterator[None]:
        import fcntl

        lock_path = self.tasks / f".{task_id}.lock"
        handle = lock_path.open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    @staticmethod
    def _write(path: Path, record: Record) -> None:
        fd, temporary = tempfile.mkstemp(prefix=".task-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(record, handle, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _read(
        self,
        path: Path,
        *,
        expected_task_id: str | None = None,
        repair_pending_event: bool = False,
    ) -> Record:
        try:
            value = loads_object(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValidationError, json.JSONDecodeError) as exc:
            raise ValidationError("task record is unreadable") from exc
        if not isinstance(value, dict):
            raise ValidationError("task record is not an object")
        record = value
        _validate_record(record)
        if expected_task_id is not None and record.get("task_id") != expected_task_id:
            raise ValidationError("task record identity differs from its filename")
        if repair_pending_event:
            _repair_pending_event(record, self.artifacts)
        if expected_task_id is not None:
            self._validate_event_journal(record)
        self._validate_native_journal_artifact(record)
        return record

    def _pending_event_commit(self, path: Path) -> bool:
        try:
            record = loads_object(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValidationError, json.JSONDecodeError):
            return False
        if not isinstance(record, dict):
            return False
        return record.get("pending_event") is not None

    def _validate_event_journal(self, record: Record) -> None:
        task_id = _string(record.get("task_id"))
        sequence = _integer(record.get("last_event_sequence"))
        event_dir = self.artifacts / task_id / "events"
        pending = _mapping(record["pending_event"]) if record.get("pending_event") is not None else None
        pending_name = (
            f"{_integer(pending['sequence']):08d}.json" if pending is not None else None
        )
        if sequence == 0:
            if event_dir.exists():
                if event_dir.is_symlink() or not event_dir.is_dir():
                    raise ValidationError("event journal directory is missing or unsafe")
                actual = {path.name for path in event_dir.iterdir()}
                if actual and actual != {pending_name}:
                    raise ValidationError("event journal exists for an empty sequence")
            return
        if event_dir.is_symlink() or not event_dir.is_dir():
            raise ValidationError("event journal directory is missing or unsafe")
        expected = {f"{index:08d}.json" for index in range(1, sequence + 1)}
        paths = tuple(event_dir.iterdir())
        actual = {path.name for path in paths}
        allowed = expected | ({pending_name} if pending_name is not None else set())
        if actual != allowed or any(path.is_symlink() for path in paths):
            raise ValidationError("event journal sequence is incomplete or reordered")
        artifacts = _mapping(record.get("artifacts"))
        events = _mapping(artifacts.get("events"))
        for path in paths:
            metadata: object
            if path.name == pending_name and pending is not None:
                metadata = {
                    "path": pending["path"],
                    "sha256": pending["sha256"],
                    "size_bytes": pending["size_bytes"],
                }
            else:
                metadata = events.get(path.name)
            _validate_artifact_metadata(
                metadata, f"artifacts/{task_id}/events/{path.name}"
            )
            if _artifact_metadata(path, self.root) != metadata:
                raise ValidationError("event artifact digest or size mismatch")

    def _validate_native_journal_artifact(self, record: Record) -> None:
        journal = record.get("native_journal")
        if journal is None:
            return
        metadata = _mapping(journal)
        relative = _string(metadata.get("path"))
        relative_path = Path(relative)
        if relative_path.is_absolute() or any(
            part in {"", ".", ".."} for part in relative_path.parts
        ):
            raise ValidationError("native journal artifact reference is unsafe")
        path = self.root / relative_path
        if path.is_symlink() or any(
            (self.root / parent).is_symlink() for parent in relative_path.parents
        ):
            raise ValidationError("native journal artifact may not traverse symlinks")
        try:
            path = path.resolve(strict=True)
            path.relative_to(self.root.resolve(strict=True))
            content = path.read_bytes()
        except (OSError, ValueError) as exc:
            raise ValidationError("native journal artifact is missing") from exc
        if sha256(content).hexdigest() != metadata.get("sha256"):
            raise ValidationError("native journal artifact digest mismatch")
        size = metadata.get("size_bytes")
        if isinstance(size, bool) or not isinstance(size, int) or size != len(content):
            raise ValidationError("native journal artifact size mismatch")
        evidence_status = _native_journal_status(
            content, _string(metadata.get("session_id")), _string(metadata.get("turn_id"))
        )
        if evidence_status != metadata.get("evidence_status"):
            raise ValidationError("native journal evidence status mismatch")


def _string(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError("record string field is invalid")
    return value


def _artifact_metadata(path: Path, root: Path) -> dict[str, object]:
    content = path.read_bytes()
    return {
        "path": str(path.relative_to(root)),
        "sha256": sha256(content).hexdigest(),
        "size_bytes": len(content),
    }


def _validate_artifact_metadata(value: object, expected_path: str) -> None:
    metadata = _mapping(value)
    if set(metadata) != {"path", "sha256", "size_bytes"}:
        raise ValidationError("artifact metadata is invalid")
    if metadata.get("path") != expected_path:
        raise ValidationError("artifact metadata path is invalid")
    digest = metadata.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValidationError("artifact metadata digest is invalid")
    try:
        int(digest, 16)
    except ValueError as exc:
        raise ValidationError("artifact metadata digest is invalid") from exc
    size = metadata.get("size_bytes")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ValidationError("artifact metadata size is invalid")


def _native_journal_status(content: bytes, session_id: str, turn_id: str) -> str:
    """Bind journal identity structurally and classify selected-turn evidence."""

    truncated = False
    try:
        rows: list[dict[str, object]] = []
        lines = content.splitlines(keepends=True)
        for index, raw_line in enumerate(lines):
            line = raw_line.decode("utf-8")
            if not line.strip():
                continue
            try:
                rows.append(loads_object(line))
            except ValidationError:
                if index == len(lines) - 1 and not raw_line.endswith((b"\n", b"\r")):
                    truncated = True
                    break
                raise
    except (UnicodeDecodeError, ValidationError) as exc:
        raise ValidationError("native journal is not valid JSONL") from exc
    session_matches = 0
    conflicting_session = False
    selected_contexts = 0
    current_turn: str | None = None
    final_messages = 0
    completed_events = 0
    aborted_events = 0
    for row in rows:
        row_type = row.get("type")
        payload = row.get("payload")
        if not isinstance(row_type, str) or not isinstance(payload, dict):
            continue
        if row_type == "session_meta":
            if payload.get("id") == session_id and payload.get("session_id") == session_id:
                session_matches += 1
            else:
                conflicting_session = True
        elif row_type == "turn_context":
            current_turn = (
                payload.get("turn_id") if isinstance(payload.get("turn_id"), str) else None
            )
            if current_turn == turn_id:
                selected_contexts += 1
        elif row_type == "response_item":
            if payload.get("role") != "assistant" or payload.get("phase") != "final_answer":
                continue
            content_items = payload.get("content")
            if current_turn == turn_id and isinstance(content_items, list) and any(
                isinstance(item, dict)
                and item.get("type") == "output_text"
                and isinstance(item.get("text"), str)
                for item in content_items
            ):
                final_messages += 1
        elif row_type == "event_msg" and payload.get("turn_id") == turn_id:
            if payload.get("type") == "task_complete":
                completed_events += 1
            elif payload.get("type") in {"task_aborted", "turn_aborted", "turn_failed"}:
                aborted_events += 1
    if session_matches != 1 or conflicting_session or selected_contexts < 1:
        raise ValidationError("native journal identity does not match the task")
    return (
        "complete"
        if not truncated and final_messages >= 1 and completed_events == 1 and aborted_events == 0
        else "incomplete"
    )


def _repair_pending_event(record: Record, artifacts_root: Path) -> None:
    pending_value = record.get("pending_event")
    if pending_value is None:
        return
    task_id = _string(record.get("task_id"))
    pending = _mapping(pending_value)
    sequence = _integer(pending.get("sequence"))
    if sequence != _integer(record.get("last_event_sequence")) + 1:
        raise ValidationError("pending event sequence is not next in journal")
    expected_path = f"artifacts/{task_id}/events/{sequence:08d}.json"
    if pending.get("path") != expected_path:
        raise ValidationError("pending event path is invalid")
    event_path = artifacts_root.parent / expected_path
    if not event_path.exists():
        record["pending_event"] = None
        return
    if event_path.is_symlink() or not event_path.is_file():
        raise ValidationError("pending event artifact is unsafe")
    content = event_path.read_bytes()
    if sha256(content).hexdigest() != pending.get("sha256"):
        raise ValidationError("pending event bytes do not match their binding")
    size = pending.get("size_bytes")
    if isinstance(size, bool) or not isinstance(size, int) or size != len(content):
        raise ValidationError("pending event size does not match its binding")
    artifacts = _mapping(record.get("artifacts"))
    events = _mapping(artifacts.get("events"))
    event_name = f"{sequence:08d}.json"
    if event_name in events:
        raise ValidationError("pending event contradicts retained metadata")
    events[event_name] = {
        "path": pending["path"],
        "sha256": pending["sha256"],
        "size_bytes": pending["size_bytes"],
    }
    record["last_event_sequence"] = sequence
    record["pending_event"] = None


def _require_private_directory(path: Path) -> None:
    if not path.is_dir() or path.is_symlink():
        raise ValidationError("store path must be a real directory")
    info = path.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValidationError("store directory must be owned by the current user with mode 0700")


def _has_symlink_component(path: Path) -> bool:
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        if current.is_symlink():
            return True
    return False


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError("record integer field is invalid")
    return value


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValidationError("record object field is invalid")
    return cast("dict[str, object]", value)


_RECORD_KEYS = {
    "schema_version",
    "task_id",
    "request_id",
    "request",
    "request_sha256",
    "cwd_identity",
    "runtime_path",
    "runtime_version",
    "runtime_sha256",
    "profile_sha256",
    "created_at_utc",
    "deadline_utc",
    "state",
    "reason",
    "thread_id",
    "turn_id",
    "thread_submission_started",
    "turn_submission_started",
    "turn_submitted",
    "stop_intent",
    "stop_requested_at_utc",
    "interrupt_sent",
    "last_event_sequence",
    "completed_items",
    "provider_outcome",
    "result",
    "pending_event",
    "native_journal",
    "artifacts",
}


def _validate_record(record: Record) -> None:
    if set(record) != _RECORD_KEYS or record.get("schema_version") != "1":
        raise ValidationError("unsupported or incomplete task record schema")
    for field in (
        "task_id",
        "request_id",
        "runtime_path",
        "runtime_version",
        "runtime_sha256",
        "profile_sha256",
        "created_at_utc",
        "deadline_utc",
    ):
        if not isinstance(record.get(field), str) or not record[field]:
            raise ValidationError(f"record field {field} is invalid")
    if not Path(cast("str", record["runtime_path"])).is_absolute():
        raise ValidationError("record runtime path must be absolute")
    for field in ("task_id", "request_id"):
        try:
            uuid.UUID(cast("str", record[field]))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValidationError(f"record field {field} is not a UUID") from exc
    created_at = _timestamp(record["created_at_utc"])
    deadline = _timestamp(record["deadline_utc"])
    if created_at > deadline:
        raise ValidationError("record creation is after its deadline")
    try:
        AdapterTaskState(cast("str", record["state"]))
    except ValueError as exc:
        raise ValidationError("record state is invalid") from exc
    if not isinstance(record.get("request"), dict) or not isinstance(
        record.get("completed_items"), dict
    ):
        raise ValidationError("record object field is invalid")
    request = cast("dict[str, object]", record["request"])
    try:
        parsed_request = parse_start(request)
        parsed_request.request_uuid()
        parsed_request.deadline()
    except ValidationError as exc:
        raise ValidationError("record request is invalid") from exc
    if record.get("request_id") != request.get("request_id"):
        raise ValidationError("record request identity is inconsistent")
    if parsed_request.deadline() != deadline.astimezone(UTC):
        raise ValidationError("record deadline differs from retained request")
    request_digest = record.get("request_sha256")
    if not isinstance(request_digest, str) or len(request_digest) != 64:
        raise ValidationError("record request digest is invalid")
    try:
        int(request_digest, 16)
    except ValueError as exc:
        raise ValidationError("record request digest is invalid") from exc
    if sha256(canonical_json(request)).hexdigest() != request_digest.lower():
        raise ValidationError("record request digest does not match request")
    cwd_identity = _mapping(record.get("cwd_identity"))
    if set(cwd_identity) != {"path", "components"}:
        raise ValidationError("record cwd identity is invalid")
    if cwd_identity.get("path") != request.get("cwd"):
        raise ValidationError("record cwd identity path differs from request")
    components = cwd_identity.get("components")
    if not isinstance(components, list) or not components:
        raise ValidationError("record cwd identity components are invalid")
    for component in components:
        component_map = _mapping(component)
        if set(component_map) != {"path", "device", "inode"}:
            raise ValidationError("record cwd identity component is invalid")
        if not isinstance(component_map.get("path"), str) or not component_map["path"]:
            raise ValidationError("record cwd identity component path is invalid")
        for field in ("device", "inode"):
            value = component_map.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValidationError("record cwd identity component number is invalid")
    runtime_digest = record.get("runtime_sha256")
    if not isinstance(runtime_digest, str) or len(runtime_digest) != 64:
        raise ValidationError("record runtime digest is invalid")
    try:
        int(runtime_digest, 16)
    except ValueError as exc:
        raise ValidationError("record runtime digest is invalid") from exc
    profile_digest = record.get("profile_sha256")
    if not isinstance(profile_digest, str) or len(profile_digest) != 64:
        raise ValidationError("record profile digest is invalid")
    try:
        int(profile_digest, 16)
    except ValueError as exc:
        raise ValidationError("record profile digest is invalid") from exc
    for field in (
        "thread_submission_started",
        "turn_submission_started",
        "turn_submitted",
        "interrupt_sent",
    ):
        if not isinstance(record.get(field), bool):
            raise ValidationError(f"record boolean field {field} is invalid")
    reason = record.get("reason")
    if reason is not None and (not isinstance(reason, str) or not reason):
        raise ValidationError("record reason is invalid")
    provider_outcome = record.get("provider_outcome")
    if provider_outcome is not None and (
        not isinstance(provider_outcome, str)
        or provider_outcome not in {"completed", "failed", "interrupted"}
    ):
        raise ValidationError("record provider outcome is invalid")
    completed_items = cast("dict[str, object]", record["completed_items"])
    for item_id, item_value in completed_items.items():
        if not isinstance(item_id, str) or not item_id:
            raise ValidationError("completed item key is invalid")
        item = _mapping(item_value)
        if (
            item.get("id") != item_id
            or not isinstance(item.get("type"), str)
            or not item.get("type")
        ):
            raise ValidationError("completed item identity or type is invalid")
        if item.get("type") == "agentMessage":
            if not isinstance(item.get("text"), str):
                raise ValidationError("completed agent message text is invalid")
            phase = item.get("phase")
            if phase is not None and (
                not isinstance(phase, str)
                or phase not in {"commentary", "final_answer"}
            ):
                raise ValidationError("completed agent message phase is invalid")
    sequence = record.get("last_event_sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        raise ValidationError("record event sequence is invalid")
    thread_id = record.get("thread_id")
    turn_id = record.get("turn_id")
    if thread_id is not None and (not isinstance(thread_id, str) or not thread_id):
        raise ValidationError("record thread_id is invalid")
    if turn_id is not None and (not isinstance(turn_id, str) or not turn_id or thread_id is None):
        raise ValidationError("record turn_id is invalid")
    stop_intent = record.get("stop_intent")
    stop_time = record.get("stop_requested_at_utc")
    if stop_intent is None and stop_time is not None:
        raise ValidationError("stop timestamp exists without stop intent")
    if stop_intent is not None:
        if (
            not isinstance(stop_intent, str)
            or stop_intent not in {"cancel", "expiry"}
            or not isinstance(stop_time, str)
        ):
            raise ValidationError("stop intent is invalid")
        if _timestamp(stop_time) < created_at:
            raise ValidationError("stop timestamp precedes task creation")
    result = record.get("result")
    if result is not None:
        result_map = _mapping(result)
        if set(result_map) != {"path", "sha256", "size_bytes"}:
            raise ValidationError("result metadata is invalid")
        if not isinstance(result_map.get("path"), str) or not isinstance(
            result_map.get("sha256"), str
        ):
            raise ValidationError("result metadata is invalid")
        size = result_map.get("size_bytes")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValidationError("result size is invalid")
        result_digest = result_map.get("sha256")
        if not isinstance(result_digest, str) or len(result_digest) != 64:
            raise ValidationError("result digest is invalid")
        try:
            int(result_digest, 16)
        except ValueError as exc:
            raise ValidationError("result digest is invalid") from exc
        expected_path = f"artifacts/{record['task_id']}/result.json"
        result_path = result_map.get("path")
        if result_path != expected_path:
            raise ValidationError("result artifact path is invalid")
        if cast("str", record["state"]) not in {
            AdapterTaskState.RUNNING.value,
            AdapterTaskState.COMPLETED.value,
            AdapterTaskState.UNKNOWN.value,
        } or provider_outcome != "completed":
            raise ValidationError("result artifact exists before completed state")
    pending_event = record.get("pending_event")
    if pending_event is not None:
        pending = _mapping(pending_event)
        if set(pending) != {"sequence", "path", "sha256", "size_bytes"}:
            raise ValidationError("pending event metadata is invalid")
        sequence_value = pending.get("sequence")
        if isinstance(sequence_value, bool) or not isinstance(sequence_value, int) or sequence_value < 1:
            raise ValidationError("pending event sequence is invalid")
        expected_path = f"artifacts/{record['task_id']}/events/{sequence_value:08d}.json"
        if pending.get("path") != expected_path:
            raise ValidationError("pending event path is invalid")
        digest = pending.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValidationError("pending event digest is invalid")
        try:
            int(digest, 16)
        except ValueError as exc:
            raise ValidationError("pending event digest is invalid") from exc
        size = pending.get("size_bytes")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValidationError("pending event size is invalid")
    journal = record.get("native_journal")
    if journal is not None:
        journal_map = _mapping(journal)
        if set(journal_map) != {
            "path", "sha256", "size_bytes", "session_id", "turn_id", "evidence_status"
        }:
            raise ValidationError("native journal metadata is invalid")
        if journal_map.get("path") != f"artifacts/{record['task_id']}/native-session.jsonl":
            raise ValidationError("native journal path is invalid")
        if not isinstance(journal_map.get("session_id"), str) or not journal_map.get("session_id"):
            raise ValidationError("native journal session identity is invalid")
        if not isinstance(journal_map.get("turn_id"), str) or not journal_map.get("turn_id"):
            raise ValidationError("native journal turn identity is invalid")
        if journal_map.get("session_id") != record.get("thread_id") or journal_map.get(
            "turn_id"
        ) != record.get("turn_id"):
            raise ValidationError("native journal identity is not bound to the task")
        if journal_map.get("evidence_status") not in {"complete", "incomplete"}:
            raise ValidationError("native journal evidence status is invalid")
        journal_digest = journal_map.get("sha256")
        if not isinstance(journal_digest, str) or len(journal_digest) != 64:
            raise ValidationError("native journal digest is invalid")
        try:
            int(journal_digest, 16)
        except ValueError as exc:
            raise ValidationError("native journal digest is invalid") from exc
        journal_size = journal_map.get("size_bytes")
        if isinstance(journal_size, bool) or not isinstance(journal_size, int) or journal_size < 0:
            raise ValidationError("native journal size is invalid")
    artifacts = _mapping(record.get("artifacts"))
    if set(artifacts) != {"events", "result", "native_journal"}:
        raise ValidationError("artifact index is invalid")
    events = _mapping(artifacts.get("events"))
    if result != artifacts.get("result") or journal != artifacts.get("native_journal"):
        raise ValidationError("artifact index does not match record metadata")
    for name, metadata in events.items():
        if not isinstance(name, str) or not name.endswith(".json"):
            raise ValidationError("event artifact index name is invalid")
        _validate_artifact_metadata(metadata, f"artifacts/{record['task_id']}/events/{name}")
    if len(events) != sequence:
        raise ValidationError("event artifact index is incomplete")
    state = AdapterTaskState(cast("str", record["state"]))
    if state == AdapterTaskState.COMPLETED and stop_intent is not None:
        raise ValidationError("completed record retains a stop intent")
    if state == AdapterTaskState.COMPLETED and (
        result is None
        or provider_outcome != "completed"
        or turn_id is None
        or not record["turn_submitted"]
    ):
        raise ValidationError("completed record lacks terminal proof")
    if state == AdapterTaskState.RUNNING and not record["turn_submitted"]:
        raise ValidationError("running record has no submitted turn")
    if record["turn_submitted"] and (
        not record["thread_submission_started"]
        or not record["turn_submission_started"]
        or thread_id is None
        or turn_id is None
    ):
        raise ValidationError("submitted turn identity is incomplete")
    if record["turn_submission_started"] and not record["thread_submission_started"]:
        raise ValidationError("turn submission precedes thread submission")
    if record["interrupt_sent"] and stop_intent is None:
        raise ValidationError("interrupt was sent without a stop intent")


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValidationError("timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError("timestamp is malformed") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValidationError("timestamp must include a timezone")
    if parsed.utcoffset() != UTC.utcoffset(None):
        raise ValidationError("timestamp must be UTC")
    return parsed.astimezone(UTC)
