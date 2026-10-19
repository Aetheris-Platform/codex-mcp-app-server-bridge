"""Offline, private-fixture proofs of adapter lifetime ownership."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import select
import signal
import socket
import subprocess
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from test_codex_app_server_adapter import (
    FakeClient,
    FakeTransport,
    _claimed_service,
    _request,
    _service,
)

from codex_app_server_adapter.errors import ValidationError
from codex_app_server_adapter.ownership import DataRootOwnership
from codex_app_server_adapter.profile import OperatorProfile
from codex_app_server_adapter.protocol import JsonRpcClient
from codex_app_server_adapter.runtime import create_owned_service


def _profile(tmp_path: Path) -> OperatorProfile:
    tmp_path.mkdir(mode=0o700, parents=True, exist_ok=True)
    runtime = tmp_path / "codex"
    marker = tmp_path / "invocations"
    runtime.write_text(
        f"#!{sys.executable}\nfrom pathlib import Path\n"
        f"p=Path({str(marker)!r}); p.write_bytes(p.read_bytes()+b'x' if p.exists() else b'x')\n"
        "print('0.156.0')\n",
    )
    runtime.chmod(0o700)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    worktree = tmp_path / "worktree"
    worktree.mkdir(mode=0o700)
    return OperatorProfile(
        runtime, "0.156.0", hashlib.sha256(runtime.read_bytes()).hexdigest(),
        private / "app.sock", (worktree,), ("gpt-6-sol",), ("read-only",),
        tmp_path / "data",
    )


def _write_profile(profile: OperatorProfile, path: Path) -> None:
    path.write_text(json.dumps(asdict(profile), default=str))


def _environment() -> dict[str, str]:
    return {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": os.pathsep.join(
            (str(Path(__file__).parents[1] / "src"), str(Path(__file__).parent))
        ),
    }


def _launch(path: Path) -> subprocess.Popen[str]:
    return subprocess.Popen(  # noqa: S603 - owned fixture executable and profile
        [sys.executable, "-B", "-m", "codex_app_server_adapter", "--profile", str(path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=_environment(),
    )


def _line(process: subprocess.Popen[str]) -> str:
    assert process.stdout is not None
    assert select.select([process.stdout], [], [], 5)[0], "fixture did not become ready"
    line = process.stdout.readline()
    assert line, process.poll()
    return line


def _stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.kill()
    process.communicate(timeout=10)


def _snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_loser_different_profile_cannot_touch_owner_or_probe_runtime(tmp_path: Path) -> None:
    async def exercise() -> None:
        owner = _service(tmp_path, FakeClient())
        # Keep the worker unscheduled while the competing process starts.
        task = await owner.start(_request(owner.profile.allowed_worktree_roots[0]))
        artifacts = owner.profile.data_root / "artifacts" / "retained"
        artifacts.mkdir(mode=0o700)
        for name in ("events", "result", "native-session.jsonl"):
            (artifacts / name).write_bytes(b"original private fixture bytes\n")
        with socket.socket(socket.AF_UNIX) as endpoint:
            endpoint.bind(str(owner.profile.socket_path))
            owner.profile.socket_path.chmod(0o600)
            identity = owner.profile.socket_path.lstat()
            before = _snapshot(owner.profile.data_root)
            profile_path = tmp_path / "another-profile.json"
            _write_profile(replace(owner.profile, allowed_models=("different-model",)), profile_path)
            loser = _launch(profile_path)
            try:
                _, error = loser.communicate(timeout=5)
                assert loser.returncode != 0
                assert "already owned" in error
                assert _snapshot(owner.profile.data_root) == before
                assert owner.store.load(task["task_id"])["state"] == "starting"
                after = owner.profile.socket_path.lstat()
                assert (after.st_dev, after.st_ino, after.st_mode) == (
                    identity.st_dev, identity.st_ino, identity.st_mode,
                )
            finally:
                _stop(loser)
        await owner.shutdown()

    asyncio.run(exercise())


def test_loser_creates_no_store_children_or_cli_probe(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    claim = DataRootOwnership(profile.data_root)
    path = tmp_path / "other.json"
    _write_profile(profile, path)
    lock = profile.data_root / ".adapter-owner.lock"
    before = lock.stat()
    process = _launch(path)
    try:
        _, error = process.communicate(timeout=5)
        assert process.returncode != 0
        assert "already owned" in error
        assert sorted(p.name for p in profile.data_root.iterdir()) == [".adapter-owner.lock"]
        assert lock.read_bytes() == b""
        assert lock.stat().st_ino == before.st_ino
        assert not (tmp_path / "invocations").exists()
    finally:
        _stop(process)
        claim.close()


def test_independent_roots_and_stale_service_handles(tmp_path: Path) -> None:
    async def exercise() -> None:
        first = create_owned_service(_profile(tmp_path / "one"))
        other = create_owned_service(_profile(tmp_path / "two"))
        await first.shutdown()
        successor = create_owned_service(first.profile)
        try:
            before = _snapshot(successor.profile.data_root)
            await first.shutdown()
            for method in (first.start, first.status, first.result, first.cancel):
                with pytest.raises(ValidationError, match="admission is closed"):
                    await method({})
            assert _snapshot(successor.profile.data_root) == before
            with pytest.raises(ValidationError, match="already owned"):
                DataRootOwnership(other.profile.data_root)
        finally:
            await successor.shutdown()
            await other.shutdown()

    asyncio.run(exercise())


def test_restart_preserves_terminal_bytes_and_request_idempotency(tmp_path: Path) -> None:
    async def exercise() -> None:
        client = FakeClient()
        first = _service(tmp_path, client)
        request = _request(first.profile.allowed_worktree_roots[0])
        task = await first.start(request)
        status = await first.status({"task_id": task["task_id"], "wait_seconds": 2})
        assert status["state"] == "completed"
        await first.shutdown()
        before = _snapshot(first.profile.data_root)
        second = _claimed_service(first.profile, client)
        try:
            assert _snapshot(second.profile.data_root) == before
            replay = await second.start(request)
            assert replay["task_id"] == task["task_id"]
            assert replay["state"] == "completed"
            assert [method for method, _ in client.calls].count("turn/start") == 1
            assert _snapshot(second.profile.data_root) == before
        finally:
            await second.shutdown()

    asyncio.run(exercise())


def test_constructor_requires_live_matching_store_claim(tmp_path: Path) -> None:
    from codex_app_server_adapter.service import AdapterService
    from codex_app_server_adapter.store import TaskStore

    profile = _profile(tmp_path / "one")
    other = _profile(tmp_path / "two")
    claim = DataRootOwnership(other.data_root)
    try:
        with pytest.raises(ValidationError, match="live ownership"):
            AdapterService(profile, TaskStore(other.data_root), FakeClient, ownership=claim)
    finally:
        claim.close()
    with pytest.raises(ValidationError, match="live ownership"):
        AdapterService(other, TaskStore(other.data_root), FakeClient, ownership=claim)


def test_death_releases_lock_with_exec_child_alive_and_restart_reconciles(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    path = tmp_path / "profile.json"
    _write_profile(profile, path)
    code = """
import asyncio,json,subprocess,sys
from pathlib import Path
from codex_app_server_adapter.profile import OperatorProfile
from test_codex_app_server_adapter import _claimed_service,FakeClient,_request
async def run():
    profile=OperatorProfile.parse_json(Path(sys.argv[1]).read_text())
    owner=_claimed_service(profile,FakeClient())
    task=await owner.start({**_request(profile.allowed_worktree_roots[0]),"model":"gpt-6-sol"})
    child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"],
        close_fds=False,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    print(json.dumps({"task":task["task_id"],"child":child.pid}),flush=True)
    sys.stdin.readline()
asyncio.run(run())
"""
    process = subprocess.Popen(  # noqa: S603 - isolated fixture code
        [sys.executable, "-B", "-c", code, str(path)], env=_environment(),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    child = None
    try:
        ready = json.loads(_line(process))
        child = ready["child"]
        process.kill()
        process.communicate(timeout=5)
        os.kill(child, 0)

        async def restart() -> None:
            successor = _claimed_service(profile, FakeClient())
            try:
                record = successor.store.load(ready["task"])
                assert record["state"] == "unknown"
                assert record["reason"] == "adapter_restarted"
                assert not record["turn_submission_started"]
            finally:
                await successor.shutdown()

        asyncio.run(restart())
    finally:
        _stop(process)
        if child is not None:
            os.kill(child, signal.SIGKILL)


@pytest.mark.parametrize("failure", ["probe", "store", "service"])
def test_factory_failure_releases_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    import codex_app_server_adapter.runtime as runtime

    profile = _profile(tmp_path)

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("setup failure")

    if failure == "probe":
        monkeypatch.setattr(OperatorProfile, "verify_runtime", fail)
    else:
        monkeypatch.setattr(runtime, "TaskStore" if failure == "store" else "AdapterService", fail)
    with pytest.raises(RuntimeError, match="setup failure"):
        create_owned_service(profile)
    claim = DataRootOwnership(profile.data_root)
    claim.close()


def test_cancelled_shutdown_retains_claim_until_cleanup_and_drains_requests(tmp_path: Path) -> None:
    async def exercise() -> None:
        owner = _service(tmp_path, FakeClient())
        task = await owner.start(_request(owner.profile.allowed_worktree_roots[0]))
        status = await owner.status({"task_id": task["task_id"], "wait_seconds": 2})
        waiting = asyncio.create_task(owner.status({
            "task_id": task["task_id"], "wait_seconds": 30, "cursor": status["cursor"],
        }))
        await asyncio.sleep(0)
        entered, finish = asyncio.Event(), asyncio.Event()

        async def cleanup() -> None:
            entered.set()
            await finish.wait()

        owner.shutdown_callback = cleanup
        shutdown = asyncio.create_task(owner.shutdown())
        await entered.wait()
        shutdown.cancel()
        await asyncio.sleep(0)
        assert not shutdown.done()
        assert waiting.cancelled()
        with pytest.raises(ValidationError, match="already owned"):
            DataRootOwnership(owner.profile.data_root)
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await shutdown
        claim = DataRootOwnership(owner.profile.data_root)
        claim.close()

    asyncio.run(exercise())


def test_shutdown_callback_failure_releases_claim(tmp_path: Path) -> None:
    async def exercise() -> None:
        owner = _service(tmp_path, FakeClient())

        async def fail() -> None:
            raise RuntimeError("cleanup failure")

        owner.shutdown_callback = fail
        with pytest.raises(RuntimeError, match="cleanup failure"):
            await owner.shutdown()
        claim = DataRootOwnership(owner.profile.data_root)
        claim.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("ending", ["late-ack", "timeout"])
def test_shutdown_drains_interrupt_before_successor_ownership(tmp_path: Path, ending: str) -> None:
    class DelayedInterruptTransport(FakeTransport):
        def __init__(self) -> None:
            super().__init__()
            self.interrupt_sent = asyncio.Event()
            self.interrupt_id: object = None

        async def send(self, raw: bytes) -> None:
            message = json.loads(raw)
            if message.get("method") == "turn/interrupt":
                self.sent.append(raw)
                self.interrupt_id = message["id"]
                self.interrupt_sent.set()
            else:
                await super().send(raw)

    async def exercise() -> None:
        event_gate = asyncio.Event()
        worker_client = FakeClient(event_gate=event_gate)
        owner = _service(tmp_path, worker_client)
        task = await owner.start(_request(owner.profile.allowed_worktree_roots[0]))
        task_id = task["task_id"]
        await worker_client.turn_started.wait()
        await asyncio.sleep(0)
        assert owner.store.load(task_id)["turn_id"] is not None
        transport = DelayedInterruptTransport()
        client = JsonRpcClient(transport)
        await client.connect()
        owner._clients[task_id] = client
        await owner.cancel({"task_id": task_id})
        await asyncio.wait_for(transport.interrupt_sent.wait(), timeout=2)
        if ending == "timeout":
            client.last_failure_raw = b'{"fixture":"retained interrupt failure"}'
        event_gate.set()
        await asyncio.gather(*tuple(owner._workers.values()))
        await owner.shutdown()
        successor = _claimed_service(owner.profile, FakeClient())
        before = _snapshot(owner.profile.data_root)
        try:
            if ending == "late-ack":
                await transport.incoming.put(json.dumps({
                    "id": transport.interrupt_id, "result": {},
                }).encode())
                await asyncio.sleep(0.05)
            else:
                await asyncio.sleep(5.1)
            assert _snapshot(owner.profile.data_root) == before
            assert not client._pending
            assert not owner._interrupts
        finally:
            await client.close()
            await successor.shutdown()

    asyncio.run(exercise())


def test_shutdown_drains_invocation_not_callers_reentrant_cleanup(tmp_path: Path) -> None:
    async def exercise() -> None:
        owner = _service(tmp_path, FakeClient())
        task = await owner.start(_request(owner.profile.allowed_worktree_roots[0]))
        await asyncio.gather(*tuple(owner._workers.values()))
        status = await owner.status({"task_id": task["task_id"]})
        entered = asyncio.Event()

        async def caller() -> None:
            try:
                entered.set()
                await owner.status({
                    "task_id": task["task_id"], "wait_seconds": 30, "cursor": status["cursor"],
                })
            finally:
                await owner.shutdown()

        request = asyncio.create_task(caller())
        await entered.wait()
        shutdowns = [asyncio.create_task(owner.shutdown()) for _ in range(2)]
        all_callers = [request, *shutdowns]
        _, pending = await asyncio.wait(all_callers, timeout=2)
        if pending:
            # Make a regression failure terminate even when caller cleanup is deadlocked.
            blocked_shutdown = owner._shutdown_task
            owner._shutdown_task = asyncio.create_task(asyncio.sleep(0))
            blocked_shutdown.cancel()
        await asyncio.gather(*all_callers, return_exceptions=True)
        assert not pending
        assert request.cancelled()
        assert not owner._requests
        claim = DataRootOwnership(owner.profile.data_root)
        claim.close()

    asyncio.run(exercise())


def test_entry_setup_failure_releases_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from codex_app_server_adapter.__main__ import _run

    profile = _profile(tmp_path)
    path = tmp_path / "profile.json"
    _write_profile(profile, path)

    async def exercise() -> None:
        def fail(*args: object) -> None:
            raise OSError("signal setup failed")
        monkeypatch.setattr(asyncio.get_running_loop(), "add_signal_handler", fail)
        with pytest.raises(OSError, match="signal setup failed"):
            await _run(path)
        claim = DataRootOwnership(profile.data_root)
        claim.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory", "public", "fifo"])
def test_unsafe_lock_refused_without_mutation(tmp_path: Path, kind: str) -> None:
    root = tmp_path / "data"
    root.mkdir(mode=0o700)
    lock = root / ".adapter-owner.lock"
    target = tmp_path / "sentinel"
    target.write_bytes(b"unchanged")
    target.chmod(0o600)
    if kind == "symlink":
        lock.symlink_to(target)
    elif kind == "hardlink":
        os.link(target, lock)
    elif kind == "directory":
        lock.mkdir()
    elif kind == "fifo":
        os.mkfifo(lock, 0o600)
    else:
        lock.write_bytes(b"")
        lock.chmod(0o644)
    with pytest.raises(ValidationError):
        DataRootOwnership(root)
    assert target.read_bytes() == b"unchanged"
    assert not (root / "tasks").exists()


@pytest.mark.parametrize("kind", ["symlink", "public", "unavailable", "wrong-owner"])
def test_unsafe_root_or_unavailable_lock_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    import codex_app_server_adapter.ownership as ownership

    root = tmp_path / "data"
    root.mkdir(mode=0o700)
    if kind == "symlink":
        alias = tmp_path / "alias"
        alias.symlink_to(root, target_is_directory=True)
        root = alias
    elif kind == "public":
        root.chmod(0o755)
    elif kind == "wrong-owner":
        monkeypatch.setattr(ownership.os, "getuid", lambda: root.stat().st_uid + 1)
    else:
        def unavailable(*args: object) -> None:
            raise OSError("locking unavailable")
        monkeypatch.setattr(ownership.fcntl, "flock", unavailable)
    with pytest.raises(ValidationError):
        DataRootOwnership(root)
    assert not (root / "tasks").exists()


@pytest.mark.parametrize("ending", ["eof", "term", "int"])
@pytest.mark.parametrize("repeat", range(2))
def test_idle_stdio_shutdown_releases_ownership(tmp_path: Path, ending: str, repeat: int) -> None:
    profile = _profile(tmp_path)
    path = tmp_path / f"profile-{repeat}.json"
    _write_profile(profile, path)
    process = _launch(path)
    try:
        assert process.stdin is not None
        process.stdin.write(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "ownership-fixture", "version": "1"}},
        }) + "\n")
        process.stdin.flush()
        assert json.loads(_line(process))["id"] == 1
        with pytest.raises(ValidationError, match="already owned"):
            DataRootOwnership(profile.data_root)
        begin = time.monotonic()
        if ending == "eof":
            process.stdin.close()
            process.stdin = None
        else:
            process.send_signal(signal.SIGTERM if ending == "term" else signal.SIGINT)
        _, error = process.communicate(timeout=15)
        elapsed = time.monotonic() - begin
        assert process.returncode == 0, error
        assert elapsed < 15
        claim = DataRootOwnership(profile.data_root)
        claim.close()
    finally:
        _stop(process)
