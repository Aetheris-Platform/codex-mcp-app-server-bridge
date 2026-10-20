"""Exercise fresh artifact directories and portable persisted path encoding."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path, PureWindowsPath
from uuid import uuid4

import pytest

from codex_app_server_adapter.platform_files import make_private_directory, require_private_windows
from codex_app_server_adapter.store import TaskStore
from codex_app_server_adapter.validation import canonical_json


class WindowsRelativePath(type(Path())):
    """Real host filesystem I/O with Windows relative-path string semantics."""

    def relative_to(self, *args, **kwargs):
        return PureWindowsPath(super().relative_to(*args, **kwargs).as_posix())


def _record(root):
    now = datetime.now(UTC)
    request = {"request_id": str(uuid4()), "prompt": "offline store fixture",
               "cwd": str(root), "model": "fixture", "sandbox": "read-only",
               "deadline_utc": (now + timedelta(minutes=2)).isoformat()}
    info = root.stat()
    return {
        "schema_version": "1", "task_id": str(uuid4()), "request_id": request["request_id"],
        "request": request, "request_sha256": hashlib.sha256(canonical_json(request)).hexdigest(),
        "cwd_identity": {"path": str(root), "components": [
            {"path": str(root), "device": info.st_dev, "inode": info.st_ino}]},
        "runtime_path": sys.executable, "runtime_version": "offline-fixture",
        "runtime_sha256": "0" * 64, "profile_sha256": "0" * 64,
        "created_at_utc": now.isoformat(), "deadline_utc": request["deadline_utc"],
        "state": "running", "reason": None, "thread_id": "fixture-thread", "turn_id": "fixture-turn",
        "thread_submission_started": True, "turn_submission_started": True, "turn_submitted": True,
        "stop_intent": None, "stop_requested_at_utc": None, "interrupt_sent": False,
        "last_event_sequence": 0, "completed_items": {}, "provider_outcome": "completed",
        "result": None, "pending_event": None, "native_journal": None,
        "artifacts": {"events": {}, "result": None, "native_journal": None},
    }


@pytest.mark.parametrize("artifact", ["event", "result", "journal"])
def test_fresh_private_artifact_roundtrip_with_windows_path_encoding(tmp_path, monkeypatch, artifact):
    root = tmp_path.resolve()
    store = TaskStore(WindowsRelativePath(root / "data"))
    record = _record(root)
    task = record["task_id"]
    store.create(record)
    task_dir = store.artifacts / task
    assert not task_dir.exists()  # Each route must create its own private directory.
    if artifact == "event":
        raw = b'{"fixture":true}\n'
        assert store.append_event(task, raw) == 1
        relative = f"artifacts/{task}/events/00000001.json"
        metadata = store.load(task)["artifacts"]["events"]["00000001.json"]
        directories = (task_dir, task_dir / "events")
    elif artifact == "result":
        raw = '{"text":"fixture ✓"}'.encode()
        store.write_result(task, raw)
        loaded = store.load(task)
        assert store.read_result(loaded) == raw
        metadata = loaded["result"]
        relative = f"artifacts/{task}/result.json"
        directories = (task_dir,)
    else:
        home = root / "home"
        make_private_directory(home)
        make_private_directory(home / "sessions")
        monkeypatch.setenv("CODEX_HOME", str(home))
        source = home / "sessions/fixture.jsonl"
        raw = (json.dumps({"type": "session_meta", "payload": {
            "id": "fixture-thread", "session_id": "fixture-thread"}}) + "\n" +
            json.dumps({"type": "turn_context", "payload": {"turn_id": "fixture-turn"}}) + "\n").encode()
        source.write_bytes(raw)
        store.copy_native_journal(task, source, session_id="fixture-thread", turn_id="fixture-turn")
        metadata = store.load(task)["native_journal"]
        relative = f"artifacts/{task}/native-session.jsonl"
        directories = (task_dir,)
    assert metadata["path"] == relative
    assert metadata["sha256"] == hashlib.sha256(raw).hexdigest()
    assert metadata["size_bytes"] == len(raw)
    assert (store.root / relative).read_bytes() == raw
    for directory in directories:
        if os.name == "nt":
            require_private_windows(directory, "test artifact directory")
        else:
            assert directory.stat().st_mode & 0o077 == 0
