"""Per-field negative matrix for the adapter-owned boundaries."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from codex_app_server_adapter.errors import ConflictError, ValidationError
from codex_app_server_adapter.models import parse_result, parse_status
from codex_app_server_adapter.profile import OperatorProfile
from codex_app_server_adapter.service import _public_status
from codex_app_server_adapter.store import TaskStore, _native_journal_status, _validate_record
from test_codex_app_server_adapter import FakeClient, _request, _service


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        pytest.param("request_id", 1, id="request_id-wrong-type"),
        pytest.param("request_id", "not-a-uuid", id="request_id-wrong-value"),
        pytest.param("prompt", 1, id="prompt-wrong-type"),
        pytest.param("prompt", "", id="prompt-wrong-value"),
        pytest.param("cwd", 1, id="cwd-wrong-type"),
        pytest.param("cwd", "relative", id="cwd-wrong-value"),
        pytest.param("model", 1, id="model-wrong-type"),
        pytest.param("model", "not-allowed", id="model-wrong-value"),
        pytest.param("sandbox", 1, id="sandbox-wrong-type"),
        pytest.param("sandbox", "dangerFullAccess", id="sandbox-wrong-value"),
        pytest.param("deadline_utc", 1, id="deadline-wrong-type"),
        pytest.param(
            "deadline_utc",
            (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
            id="deadline-wrong-value",
        ),
    ],
)
def test_start_field_matrix(tmp_path: Path, field: str, bad: object) -> None:
    client = FakeClient()
    service = _service(tmp_path, client)
    request = _request(tmp_path / "worktree")
    request[field] = bad
    with pytest.raises(ValidationError):
        asyncio.run(service.start(request))


@pytest.mark.parametrize(
    "field",
    [
        pytest.param("request_id", id="request_id-missing"),
        pytest.param("prompt", id="prompt-missing"),
        pytest.param("cwd", id="cwd-missing"),
        pytest.param("model", id="model-missing"),
        pytest.param("sandbox", id="sandbox-missing"),
        pytest.param("deadline_utc", id="deadline-missing"),
    ],
)
def test_start_required_fields_are_not_optional(tmp_path: Path, field: str) -> None:
    service = _service(tmp_path, FakeClient())
    request = _request(tmp_path / "worktree")
    del request[field]
    with pytest.raises(ValidationError):
        asyncio.run(service.start(request))


def test_adapter_owned_extra_and_duplicate_keys_are_rejected(tmp_path: Path) -> None:
    service = _service(tmp_path, FakeClient())
    request = _request(tmp_path / "worktree")
    request["unexpected"] = True
    with pytest.raises(ValidationError):
        asyncio.run(service.start(request))
    from codex_app_server_adapter.validation import loads_object

    with pytest.raises(ValidationError):
        loads_object('{"request_id":"a","request_id":"b"}')


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        pytest.param("wait_seconds", True, id="wait_seconds-bool"),
        pytest.param("wait_seconds", -1, id="wait_seconds-negative"),
        pytest.param("wait_seconds", 31, id="wait_seconds-too-large"),
        pytest.param("cursor", True, id="cursor-bool"),
        pytest.param("cursor", -1, id="cursor-negative"),
    ],
)
def test_status_optional_field_matrix(tmp_path: Path, field: str, bad: object) -> None:
    service = _service(tmp_path, FakeClient())
    task_id = str(uuid4())
    with pytest.raises(ValidationError):
        asyncio.run(service.status({"task_id": task_id, field: bad}))


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param("status", id="status-task_id-missing"),
        pytest.param("result", id="result-task_id-missing"),
        pytest.param("cancel", id="cancel-task_id-missing"),
    ],
)
def test_task_id_required_at_every_request_boundary(
    tmp_path: Path, operation: str
) -> None:
    service = _service(tmp_path, FakeClient())
    with pytest.raises(ValidationError):
        asyncio.run(getattr(service, operation)({}))


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        pytest.param("offset_bytes", True, id="offset-bool"),
        pytest.param("offset_bytes", -1, id="offset-negative"),
        pytest.param("limit_bytes", True, id="limit-bool"),
        pytest.param("limit_bytes", 0, id="limit-zero"),
        pytest.param("limit_bytes", 4097, id="limit-too-large"),
    ],
)
def test_result_optional_field_matrix(tmp_path: Path, field: str, bad: object) -> None:
    service = _service(tmp_path, FakeClient())
    task_id = str(uuid4())
    with pytest.raises(ValidationError):
        asyncio.run(service.result({"task_id": task_id, field: bad}))


def test_optional_request_fields_have_documented_defaults() -> None:
    status = parse_status({"task_id": str(uuid4())})
    result = parse_result({"task_id": str(uuid4())})
    assert status.wait_seconds == 0
    assert status.cursor == 0
    assert result.offset_bytes == 0
    assert result.limit_bytes == 4096


def test_status_cursor_beyond_retained_sequence_is_rejected(tmp_path: Path) -> None:
    service = _service(tmp_path, FakeClient())
    started = asyncio.run(service.start(_request(tmp_path / "worktree")))
    with pytest.raises(ValidationError):
        asyncio.run(service.status({"task_id": started["task_id"], "cursor": 1}))
    asyncio.run(service.shutdown())


def _profile_object(profile: OperatorProfile) -> dict[str, object]:
    return {
        "runtime_path": str(profile.runtime_path),
        "runtime_version": profile.runtime_version,
        "runtime_sha256": profile.runtime_sha256,
        "socket_path": str(profile.socket_path),
        "allowed_worktree_roots": [str(root) for root in profile.allowed_worktree_roots],
        "allowed_models": list(profile.allowed_models),
        "allowed_sandboxes": list(profile.allowed_sandboxes),
        "data_root": str(profile.data_root),
    }


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        pytest.param("runtime_path", 1, id="runtime_path-wrong-type"),
        pytest.param("runtime_path", "missing", id="runtime_path-wrong-value"),
        pytest.param("runtime_version", 1, id="runtime_version-wrong-type"),
        pytest.param("runtime_version", "other", id="runtime_version-wrong-value"),
        pytest.param("runtime_sha256", 1, id="runtime_sha256-wrong-type"),
        pytest.param("runtime_sha256", "0" * 64, id="runtime_sha256-wrong-value"),
        pytest.param("socket_path", 1, id="socket_path-wrong-type"),
        pytest.param("socket_path", "relative.sock", id="socket_path-wrong-value"),
        pytest.param("allowed_worktree_roots", 1, id="roots-wrong-type"),
        pytest.param("allowed_worktree_roots", [], id="roots-wrong-value"),
        pytest.param("allowed_models", 1, id="models-wrong-type"),
        pytest.param("allowed_models", [""], id="models-wrong-value"),
        pytest.param("allowed_sandboxes", 1, id="sandboxes-wrong-type"),
        pytest.param("allowed_sandboxes", ["dangerFullAccess"], id="sandboxes-wrong-value"),
        pytest.param("data_root", 1, id="data_root-wrong-type"),
        pytest.param("data_root", "", id="data_root-wrong-value"),
    ],
)
def test_profile_field_matrix(tmp_path: Path, field: str, bad: object) -> None:
    service = _service(tmp_path, FakeClient())
    profile = _profile_object(service.profile)
    profile[field] = bad
    with pytest.raises(ValidationError):
        OperatorProfile.from_json(json.dumps(profile))


@pytest.mark.parametrize(
    "field",
    [
        pytest.param("runtime_path", id="runtime_path-missing"),
        pytest.param("runtime_version", id="runtime_version-missing"),
        pytest.param("runtime_sha256", id="runtime_sha256-missing"),
        pytest.param("socket_path", id="socket_path-missing"),
        pytest.param("allowed_worktree_roots", id="roots-missing"),
        pytest.param("allowed_models", id="models-missing"),
        pytest.param("allowed_sandboxes", id="sandboxes-missing"),
        pytest.param("data_root", id="data_root-missing"),
    ],
)
def test_profile_required_fields_are_not_optional(tmp_path: Path, field: str) -> None:
    service = _service(tmp_path, FakeClient())
    profile = _profile_object(service.profile)
    del profile[field]
    with pytest.raises(ValidationError):
        OperatorProfile.from_json(json.dumps(profile))


@pytest.mark.parametrize("field", ["runtime_path", "socket_path", "data_root"])
def test_profile_rejects_symlink_aliases(tmp_path: Path, field: str) -> None:
    service = _service(tmp_path, FakeClient())
    profile = _profile_object(service.profile)
    alias = tmp_path / f"{field}-alias"
    target = {
        "runtime_path": service.profile.runtime_path,
        "socket_path": service.profile.socket_path.parent,
        "data_root": service.profile.data_root,
    }[field]
    alias.symlink_to(target, target_is_directory=field != "runtime_path")
    if field == "runtime_path":
        profile[field] = str(alias)
    elif field == "socket_path":
        profile[field] = str(alias / "socket.sock")
    else:
        profile[field] = str(alias)
    with pytest.raises(ValidationError):
        OperatorProfile.from_json(json.dumps(profile))


def test_existing_unsafe_store_directory_is_not_chmodded(tmp_path: Path) -> None:
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir(mode=0o755)
    unsafe.chmod(0o755)  # Preserve the unsafe-directory negative under a private test umask.
    with pytest.raises(ValidationError):
        TaskStore(unsafe)
    assert unsafe.stat().st_mode & 0o777 == 0o755


def _completed_record(tmp_path: Path) -> tuple[TaskStore, Path, dict[str, object]]:
    async def build() -> tuple[TaskStore, Path, dict[str, object]]:
        service = _service(tmp_path, FakeClient())
        started = await service.start(_request(tmp_path / "worktree"))
        cursor = 0
        status: dict[str, object] = {}
        for _ in range(20):
            status = await service.status(
                {"task_id": started["task_id"], "wait_seconds": 1, "cursor": cursor}
            )
            if status["state"] == "completed":
                break
            cursor = int(status["cursor"])
        assert status["state"] == "completed"
        await service.shutdown()
        path = service.store.tasks / f"{started['task_id']}.json"
        return service.store, path, json.loads(path.read_text(encoding="utf-8"))

    return asyncio.run(build())


_RECORD_WRONG_TYPES: dict[str, object] = {
    "schema_version": 1,
    "task_id": 1,
    "request_id": 1,
    "request": [],
    "request_sha256": 1,
    "cwd_identity": [],
    "runtime_path": 1,
    "runtime_version": 1,
    "runtime_sha256": 1,
    "profile_sha256": 1,
    "created_at_utc": 1,
    "deadline_utc": 1,
    "state": 1,
    "reason": 1,
    "thread_id": 1,
    "turn_id": 1,
    "thread_submission_started": 1,
    "turn_submission_started": 1,
    "turn_submitted": 1,
    "stop_intent": 1,
    "stop_requested_at_utc": 1,
    "interrupt_sent": 1,
    "last_event_sequence": True,
    "completed_items": [],
    "provider_outcome": 1,
    "result": [],
    "pending_event": [],
    "native_journal": [],
    "artifacts": [],
}


@pytest.mark.parametrize("field", sorted(_RECORD_WRONG_TYPES))
def test_persisted_record_wrong_type_matrix(tmp_path: Path, field: str) -> None:
    store, path, original = _completed_record(tmp_path)
    mutated = dict(original)
    mutated[field] = _RECORD_WRONG_TYPES[field]
    path.write_text(json.dumps(mutated), encoding="utf-8")
    with pytest.raises(ValidationError):
        store.load(str(original["task_id"]))


@pytest.mark.parametrize("field", sorted(_RECORD_WRONG_TYPES))
def test_persisted_record_missing_field_matrix(tmp_path: Path, field: str) -> None:
    store, path, original = _completed_record(tmp_path)
    mutated = dict(original)
    del mutated[field]
    path.write_text(json.dumps(mutated), encoding="utf-8")
    with pytest.raises(ValidationError):
        store.load(str(original["task_id"]))


_RECORD_WRONG_VALUES: dict[str, object] = {
    "schema_version": "2",
    "task_id": str(uuid4()),
    "request_id": str(uuid4()),
    "request": {},
    "request_sha256": "0" * 64,
    "cwd_identity": {},
    "runtime_path": "relative-runtime",
    "runtime_version": "",
    "runtime_sha256": "z" * 64,
    "profile_sha256": "z" * 64,
    "created_at_utc": "9999-01-01T00:00:00+00:00",
    "deadline_utc": "2020-01-01T00:00:00+00:00",
    "state": "not-a-state",
    "reason": [],
    "thread_id": "",
    "turn_id": "",
    "thread_submission_started": False,
    "turn_submission_started": False,
    "turn_submitted": False,
    "stop_intent": "expiry",
    "stop_requested_at_utc": "2020-01-01T00:00:00+00:00",
    "interrupt_sent": True,
    "last_event_sequence": 0,
    "completed_items": {"bad": []},
    "provider_outcome": "failed",
    "result": None,
    "pending_event": {"sequence": 0},
    "native_journal": {},
    "artifacts": {},
}


@pytest.mark.parametrize("field", sorted(_RECORD_WRONG_VALUES))
def test_persisted_record_wrong_value_matrix(
    tmp_path: Path, field: str
) -> None:
    store, path, original = _completed_record(tmp_path)
    mutated = dict(original)
    mutated[field] = _RECORD_WRONG_VALUES[field]
    path.write_text(json.dumps(mutated), encoding="utf-8")
    with pytest.raises(ValidationError):
        store.load(str(original["task_id"]))


@pytest.mark.parametrize(
    ("leaf", "bad"),
    [
        pytest.param("path", 1, id="cwd-path-wrong-type"),
        pytest.param("components", {}, id="cwd-components-wrong-type"),
        pytest.param("path", "", id="cwd-path-wrong-value"),
        pytest.param("components", [], id="cwd-components-wrong-value"),
    ],
)
def test_cwd_identity_nested_leaf_matrix(tmp_path: Path, leaf: str, bad: object) -> None:
    _store, _path, original = _completed_record(tmp_path)
    identity = dict(original["cwd_identity"])
    identity[leaf] = bad
    mutated = dict(original)
    mutated["cwd_identity"] = identity
    with pytest.raises(ValidationError):
        _validate_record(mutated)


@pytest.mark.parametrize(
    "leaf",
    [
        pytest.param("path", id="cwd-path-missing"),
        pytest.param("components", id="cwd-components-missing"),
    ],
)
def test_cwd_identity_nested_missing_leaf_matrix(tmp_path: Path, leaf: str) -> None:
    _store, _path, original = _completed_record(tmp_path)
    identity = dict(original["cwd_identity"])
    del identity[leaf]
    mutated = dict(original)
    mutated["cwd_identity"] = identity
    with pytest.raises(ValidationError):
        _validate_record(mutated)


@pytest.mark.parametrize(
    ("leaf", "bad"),
    [
        pytest.param("path", 1, id="pending-path-wrong-type"),
        pytest.param("sequence", "1", id="pending-sequence-wrong-type"),
        pytest.param("sha256", 1, id="pending-sha256-wrong-type"),
        pytest.param("size_bytes", "0", id="pending-size-wrong-type"),
        pytest.param("path", "", id="pending-path-wrong-value"),
        pytest.param("sequence", 0, id="pending-sequence-wrong-value"),
        pytest.param("sha256", "z" * 64, id="pending-sha256-wrong-value"),
        pytest.param("size_bytes", -1, id="pending-size-wrong-value"),
    ],
)
def test_pending_event_nested_leaf_matrix(tmp_path: Path, leaf: str, bad: object) -> None:
    _store, _path, original = _completed_record(tmp_path)
    task_id = str(original["task_id"])
    sequence = int(original["last_event_sequence"]) + 1
    pending = {
        "sequence": sequence,
        "path": f"artifacts/{task_id}/events/{sequence:08d}.json",
        "sha256": "0" * 64,
        "size_bytes": 0,
    }
    pending[leaf] = bad
    mutated = dict(original)
    mutated["pending_event"] = pending
    with pytest.raises(ValidationError):
        _validate_record(mutated)


@pytest.mark.parametrize(
    "leaf",
    [
        pytest.param("sequence", id="pending-sequence-missing"),
        pytest.param("path", id="pending-path-missing"),
        pytest.param("sha256", id="pending-sha256-missing"),
        pytest.param("size_bytes", id="pending-size-missing"),
    ],
)
def test_pending_event_nested_missing_leaf_matrix(tmp_path: Path, leaf: str) -> None:
    _store, _path, original = _completed_record(tmp_path)
    task_id = str(original["task_id"])
    sequence = int(original["last_event_sequence"]) + 1
    pending = {
        "sequence": sequence,
        "path": f"artifacts/{task_id}/events/{sequence:08d}.json",
        "sha256": "0" * 64,
        "size_bytes": 0,
    }
    del pending[leaf]
    mutated = dict(original)
    mutated["pending_event"] = pending
    with pytest.raises(ValidationError):
        _validate_record(mutated)


def test_persisted_result_artifact_identity_digest_and_size_are_rechecked(tmp_path: Path) -> None:
    store, path, original = _completed_record(tmp_path)
    result = dict(original["result"])
    assert isinstance(result, dict)
    for field, bad in (
        ("path", "artifacts/foreign/result.json"),
        ("sha256", "0" * 64),
        ("size_bytes", 1),
    ):
        mutated = dict(original)
        changed = dict(result)
        changed[field] = bad
        mutated["result"] = changed
        path.write_text(json.dumps(mutated), encoding="utf-8")
        if field == "path":
            with pytest.raises(ValidationError):
                store.load(str(original["task_id"]))
        else:
            with pytest.raises(ValidationError):
                store.read_result(store.load(str(original["task_id"])))
    path.write_text(json.dumps(original), encoding="utf-8")


def test_native_journal_is_copied_only_from_trusted_matching_session_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _path, original = _completed_record(tmp_path)
    codex_home = tmp_path / "native-codex-home"
    source = codex_home / "sessions" / "fixture.jsonl"
    source.parent.mkdir(parents=True, mode=0o700)
    source.write_bytes(
        b'{"type":"session_meta","payload":{"id":"thread-opaque-1","session_id":"thread-opaque-1"}}\n'
        b'{"type":"turn_context","payload":{"turn_id":"turn-opaque-1"}}\n'
        b'{"type":"response_item","payload":{"role":"assistant","phase":"final_answer","content":[{"type":"output_text","text":"done"}]}}\n'
        b'{"type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-1"}}\n'
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    updated = store.copy_native_journal(
        str(original["task_id"]), source, session_id="thread-opaque-1", turn_id="turn-opaque-1"
    )
    journal = updated["native_journal"]
    assert isinstance(journal, dict)
    assert journal["evidence_status"] == "incomplete"
    assert store.load(str(original["task_id"]))["native_journal"] == journal
    assert (store.root / str(journal["path"])).read_bytes() == source.read_bytes()

    foreign = tmp_path / "foreign.jsonl"
    foreign.write_bytes(source.read_bytes())
    with pytest.raises(ValidationError):
        store.copy_native_journal(
            str(original["task_id"]), foreign, session_id="thread-opaque-1", turn_id="turn-opaque-1"
        )
    spoof = codex_home / "sessions" / "spoof.jsonl"
    spoof.write_bytes(
        b'{"type":"session_meta","payload":{"id":"thread-opaque-1","session_id":"thread-opaque-1"}}\n'
        b'{"type":"turn_context","payload":{"turn_id":"turn-opaque-1-suffix"}}\n'
    )
    with pytest.raises(ValidationError):
        store.copy_native_journal(
            str(original["task_id"]), spoof, session_id="thread-opaque-1", turn_id="turn-opaque-1"
        )


def test_raw_event_artifact_tampering_is_detected_on_reload(tmp_path: Path) -> None:
    store, _path, original = _completed_record(tmp_path)
    event = store.artifacts / str(original["task_id"]) / "events" / "00000001.json"
    event.write_bytes(event.read_bytes() + b"tampered")
    with pytest.raises(ValidationError):
        store.load(str(original["task_id"]))


def _native_journal_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[TaskStore, Path, dict[str, object]]:
    store, path, original = _completed_record(tmp_path)
    codex_home = tmp_path / "journal-codex-home"
    source = codex_home / "sessions" / "fixture.jsonl"
    source.parent.mkdir(parents=True, mode=0o700)
    source.write_bytes(
        b'{"type":"session_meta","payload":{"id":"thread-opaque-1","session_id":"thread-opaque-1"}}\n'
        b'{"type":"turn_context","payload":{"turn_id":"turn-opaque-1"}}\n'
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    updated = store.copy_native_journal(
        str(original["task_id"]), source, session_id="thread-opaque-1", turn_id="turn-opaque-1"
    )
    return store, path, updated


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        pytest.param("path", "artifacts/foreign/native-session.jsonl", id="path-value"),
        pytest.param("sha256", 1, id="sha256-type"),
        pytest.param("sha256", "0" * 64, id="sha256-value"),
        pytest.param("size_bytes", True, id="size-type"),
        pytest.param("size_bytes", 0, id="size-value"),
        pytest.param("session_id", "foreign-session", id="session-value"),
        pytest.param("turn_id", "foreign-turn", id="turn-value"),
        pytest.param("evidence_status", "unknown", id="evidence-status-value"),
    ],
)
def test_native_journal_metadata_leaf_matrix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, bad: object
) -> None:
    store, path, original = _native_journal_record(tmp_path, monkeypatch)
    journal = dict(original["native_journal"])
    journal[field] = bad
    mutated = dict(original)
    mutated["native_journal"] = journal
    mutated["artifacts"] = dict(original["artifacts"])
    mutated["artifacts"]["native_journal"] = journal
    path.write_text(json.dumps(mutated), encoding="utf-8")
    with pytest.raises(ValidationError):
        store.load(str(original["task_id"]))


@pytest.mark.parametrize("field", ["path", "sha256", "size_bytes", "session_id", "turn_id", "evidence_status"])
def test_native_journal_metadata_missing_leaf_matrix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    store, path, original = _native_journal_record(tmp_path, monkeypatch)
    journal = dict(original["native_journal"])
    del journal[field]
    mutated = dict(original)
    mutated["native_journal"] = journal
    mutated["artifacts"] = dict(original["artifacts"])
    mutated["artifacts"]["native_journal"] = journal
    path.write_text(json.dumps(mutated), encoding="utf-8")
    with pytest.raises(ValidationError):
        store.load(str(original["task_id"]))


@pytest.mark.parametrize(
    ("suffix", "expected"),
    [
        (
            b'{"type":"turn_context","payload":{"turn_id":"turn-old"}}\n'
            b'{"type":"response_item","payload":{"role":"assistant","phase":"final_answer","content":[{"type":"output_text","text":"old"}]}}\n'
            b'{"type":"turn_context","payload":{"turn_id":"turn-new"}}\n'
            b'{"type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-new"}}\n',
            "incomplete",
        ),
        (
            b'{"type":"turn_context","payload":{"turn_id":"turn-new"}}\n'
            b'{"type":"response_item","payload":{"role":"assistant","phase":"final_answer","content":[{"type":"output_text","text":"new"}]}}\n'
            b'{"type":"event_msg","payload":{"type":"task_aborted","turn_id":"turn-new"}}\n'
            b'{"type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-new"}}\n',
            "incomplete",
        ),
        (
            b'{"type":"turn_context","payload":{"turn_id":"turn-new"}}\n'
            b'{"type":"response_item","payload":{"role":"assistant","phase":"final_answer","content":[{"type":"output_text","text":"new"}]}}\n'
            b'{"type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-new"}}\n'
            b'{"type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-new"}}\n',
            "incomplete",
        ),
        (
            b'{"type":"turn_context","payload":{"turn_id":"turn-new"}}\n'
            b'{"type":"response_item","payload":{"role":"assistant","phase":"final_answer","content":[{"type":"output_text","text":"first"}]}}\n'
            b'{"type":"response_item","payload":{"role":"assistant","phase":"final_answer","content":[{"type":"output_text","text":"second"}]}}\n'
            b'{"type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-new"}}\n',
            "complete",
        ),
    ],
)
def test_selected_turn_journal_evidence_is_not_inflated_by_other_turns(
    suffix: bytes, expected: str
) -> None:
    content = (
        b'{"type":"session_meta","payload":{"id":"session-1","session_id":"session-1"}}\n'
        + suffix
    )
    assert _native_journal_status(content, "session-1", "turn-new") == expected


def test_native_journal_conflicting_session_metadata_is_rejected() -> None:
    content = (
        b'{"type":"session_meta","payload":{"id":"session-1","session_id":"session-1"}}\n'
        b'{"type":"session_meta","payload":{"id":"foreign","session_id":"foreign"}}\n'
        b'{"type":"turn_context","payload":{"turn_id":"turn-new"}}\n'
    )
    with pytest.raises(ValidationError):
        _native_journal_status(content, "session-1", "turn-new")


def test_truncated_native_journal_is_retained_as_incomplete_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _path, original = _completed_record(tmp_path)
    codex_home = tmp_path / "truncated-codex-home"
    source = codex_home / "sessions" / "fixture.jsonl"
    source.parent.mkdir(parents=True, mode=0o700)
    content = (
        b'{"type":"session_meta","payload":{"id":"thread-opaque-1","session_id":"thread-opaque-1"}}\n'
        b'{"type":"turn_context","payload":{"turn_id":"turn-opaque-1"}}\n'
        b'{"type":"response_item","payload":{"role":"assistant","phase":"final_answer","content":[{"type":"output_text","text":"done"}]}}\n'
        b'{"type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-opaque-1"}}\n'
        b'{"type":"response_item","payload":{"role":"assistant"}'
    )
    source.write_bytes(content)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    updated = store.copy_native_journal(
        str(original["task_id"]), source, session_id="thread-opaque-1", turn_id="turn-opaque-1"
    )
    journal = updated["native_journal"]
    assert isinstance(journal, dict)
    assert journal["evidence_status"] == "incomplete"
    assert _public_status(updated)["native_evidence_status"] == "incomplete"
    assert (store.root / str(journal["path"])).read_bytes() == content


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        pytest.param("path", 1, id="path-wrong-type"),
        pytest.param("path", "artifacts/foreign/result.json", id="path-wrong-value"),
        pytest.param("sha256", 1, id="sha256-wrong-type"),
        pytest.param("sha256", "0" * 64, id="sha256-wrong-value"),
        pytest.param("size_bytes", True, id="size-wrong-type"),
        pytest.param("size_bytes", 1, id="size-wrong-value"),
    ],
)
def test_artifact_leaf_type_value_matrix(tmp_path: Path, field: str, bad: object) -> None:
    store, path, original = _completed_record(tmp_path)
    result = dict(original["result"])
    result[field] = bad
    mutated = dict(original)
    mutated["result"] = result
    path.write_text(json.dumps(mutated), encoding="utf-8")

    def read_mutated() -> object:
        if field == "path" or field == "size_bytes" and bad is True:
            return store.load(str(original["task_id"]))
        return store.read_result(store.load(str(original["task_id"])))

    with pytest.raises(ValidationError):
        read_mutated()


@pytest.mark.parametrize("field", ["path", "sha256", "size_bytes"])
def test_artifact_leaf_missing_matrix(tmp_path: Path, field: str) -> None:
    store, path, original = _completed_record(tmp_path)
    result = dict(original["result"])
    del result[field]
    mutated = dict(original)
    mutated["result"] = result
    path.write_text(json.dumps(mutated), encoding="utf-8")
    with pytest.raises(ValidationError):
        store.load(str(original["task_id"]))


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        pytest.param("id", "", id="completed-item-id-wrong-value"),
        pytest.param("id", 1, id="completed-item-id-wrong-type"),
        pytest.param("type", "", id="completed-item-type-wrong-value"),
        pytest.param("type", 1, id="completed-item-type-wrong-type"),
        pytest.param("text", 1, id="completed-item-text-wrong-type"),
        pytest.param("phase", "unknown", id="completed-item-phase-wrong-value"),
        pytest.param("phase", 1, id="completed-item-phase-wrong-type"),
    ],
)
def test_completed_item_nested_matrix(tmp_path: Path, field: str, bad: object) -> None:
    store, path, original = _completed_record(tmp_path)
    completed_items = dict(original["completed_items"])
    item_id = next(iter(completed_items))
    item = dict(completed_items[item_id])
    item[field] = bad
    completed_items[item_id] = item
    mutated = dict(original)
    mutated["completed_items"] = completed_items
    path.write_text(json.dumps(mutated), encoding="utf-8")
    with pytest.raises(ValidationError):
        store.load(str(original["task_id"]))


@pytest.mark.parametrize("field", ["id", "type", "text"])
def test_completed_item_required_leaf_missing_matrix(tmp_path: Path, field: str) -> None:
    store, path, original = _completed_record(tmp_path)
    completed_items = dict(original["completed_items"])
    item_id = next(iter(completed_items))
    item = dict(completed_items[item_id])
    del item[field]
    completed_items[item_id] = item
    mutated = dict(original)
    mutated["completed_items"] = completed_items
    path.write_text(json.dumps(mutated), encoding="utf-8")
    with pytest.raises(ValidationError):
        store.load(str(original["task_id"]))


def test_replay_requires_exact_cwd_and_profile_but_allows_expired_identical_replay(
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    service = _service(tmp_path, FakeClient(), now=now)
    request_id = str(uuid4())
    request = _request(
        tmp_path / "worktree", deadline=now + timedelta(seconds=1), request_id=request_id
    )
    started = asyncio.run(service.start(request))
    changed_cwd = dict(request)
    changed_cwd["cwd"] = str(tmp_path)
    with pytest.raises(ConflictError):
        asyncio.run(service.start(changed_cwd))
    service._monotonic_deadlines[started["task_id"]] = service._monotonic() - 1
    replayed = asyncio.run(service.start(request))
    assert replayed["task_id"] == started["task_id"]
    asyncio.run(service.shutdown())
