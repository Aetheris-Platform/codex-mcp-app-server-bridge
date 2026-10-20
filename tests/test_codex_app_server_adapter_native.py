"""Deterministic Unix-WebSocket and owned-process boundary tests."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import signal
import sys
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from codex_app_server_adapter.errors import ProtocolError
from codex_app_server_adapter.mcp_server import (
    _MAX_STDIO_LINE_BYTES,
    _read_bounded_line,
    _success_result,
)
from codex_app_server_adapter.profile import OperatorProfile
from codex_app_server_adapter.protocol import JsonRpcClient, UnixWebSocketTransport, _decode
from codex_app_server_adapter.runtime import OwnedAppServerClient, OwnedRuntime
from codex_app_server_adapter.service import AdapterService
from codex_app_server_adapter.store import TaskStore


def test_two_clients_share_one_owned_runtime_and_cleanup_only_it(tmp_path: Path) -> None:
    asyncio.run(_test_two_clients_share_one_owned_runtime_and_cleanup_only_it(tmp_path))


async def _test_two_clients_share_one_owned_runtime_and_cleanup_only_it(tmp_path: Path) -> None:
    source = Path(__file__).parent / "fixtures" / "codex_app_server_adapter" / "native_server.py"
    runtime_path = tmp_path / "native-fixture"
    runtime_path.write_text(f"#!{sys.executable}\n" + source.read_text(encoding="utf-8"), encoding="utf-8")
    os.chmod(runtime_path, 0o700)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    worktree = tmp_path / "worktree"
    worktree.mkdir(mode=0o700)
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    profile = OperatorProfile(
        runtime_path=runtime_path,
        runtime_version="bridge-native-fixture/1",
        runtime_sha256=hashlib.sha256(runtime_path.read_bytes()).hexdigest(),
        socket_path=private / "app.sock",
        allowed_worktree_roots=(worktree,),
        allowed_models=("gpt-5.6-luna",),
        allowed_sandboxes=("read-only",),
        data_root=data,
    )
    runtime = OwnedRuntime(profile)
    sentinel = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import time; time.sleep(30)",
        start_new_session=True,
    )
    first = OwnedAppServerClient(runtime)
    second = OwnedAppServerClient(runtime)
    try:
        await asyncio.gather(first.connect(), second.connect())
        assert runtime.process is not None
        owned_pid = runtime.process.pid
        await first.request("thread/start", {"cwd": str(worktree), "model": "gpt-5.6-luna", "sandbox": "read-only"})
        await first.close()
        await second.request("thread/read", {"threadId": "fixture-thread", "includeTurns": True})
        assert runtime.process is not None
        assert runtime.process.pid == owned_pid
    finally:
        await first.close()
        await second.close()
        assert sentinel.returncode is None
        await runtime.close()
        assert sentinel.returncode is None
        sentinel.terminate()
        await sentinel.wait()
    assert not profile.socket_path.exists()
    assert sentinel.returncode is not None


class _SingleFrameTransport:
    def __init__(self, frame: bytes) -> None:
        self.frame = frame
        self.sent: list[bytes] = []

    async def connect(self) -> None:
        return None

    async def send(self, raw: bytes) -> None:
        self.sent.append(raw)

    async def recv(self) -> bytes:
        frame, self.frame = self.frame, b""
        if frame:
            return frame
        await asyncio.Future()
        return b""

    async def close(self) -> None:
        return None


class _FrameSequenceTransport(_SingleFrameTransport):
    def __init__(self, frames: list[bytes]) -> None:
        super().__init__(b"")
        self.frames = iter(frames)

    async def recv(self) -> bytes:
        try:
            return next(self.frames)
        except StopIteration:
            raise EOFError from None


class _ChunkReader:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = iter(chunks)

    async def read(self, _size: int = -1) -> bytes:
        try:
            return next(self.chunks)
        except StopIteration:
            return b""


def test_malformed_pending_frame_fails_immediately_and_retains_raw() -> None:
    asyncio.run(_test_malformed_pending_frame_fails_immediately_and_retains_raw())


async def _test_malformed_pending_frame_fails_immediately_and_retains_raw() -> None:
    raw = b'{"id":1,"result":{"ok":true},"result":{"duplicate":true}}'
    transport = _SingleFrameTransport(raw)
    client = JsonRpcClient(transport)
    await transport.connect()
    client._reader_task = asyncio.create_task(client._reader())
    with pytest.raises(ProtocolError):
        await asyncio.wait_for(client.request("thread/read", {}), timeout=1)
    assert client.last_failure_raw == raw
    await client.close()


def test_native_decoder_rejects_duplicate_keys_and_invalid_utf8() -> None:
    with pytest.raises(ProtocolError):
        _decode(b'{"id":1,"id":2}')
    with pytest.raises(ProtocolError):
        _decode(b"\xff")


def test_mcp_tool_reply_budget_covers_duplicate_text_and_structured_payloads() -> None:
    normal = _success_result(
        {
            "task_id": "opaque-provider-task",
            "format": "codex-final-messages-v1",
            "chunk": base64.b64encode(b"x" * 4096).decode("ascii"),
            "next_offset": None,
        }
    )
    normal_bytes = len(normal.model_dump_json(by_alias=True, exclude_unset=True).encode("utf-8"))
    assert normal_bytes <= 16 * 1024

    pathological = _success_result({"task_id": "opaque-" + "x" * 12000, "reason": "y" * 12000})
    pathological_bytes = len(
        pathological.model_dump_json(by_alias=True, exclude_unset=True).encode("utf-8")
    )
    assert pathological_bytes <= 16 * 1024
    assert pathological.is_error is True
    assert pathological.structured_content is None


@pytest.mark.parametrize(
    ("raw", "expected_failure"),
    [
        (b'{"result":{}}', "missing-id"),
        (b'{"id":true,"result":{}}', "boolean-id"),
        (b'{"id":999,"result":{}}', "unmatched-id"),
        (b'{"id":1,"result":{},"error":{}}', "result-and-error"),
        (b'{"id":1,"result":1}', "wrong-result-shape"),
    ],
)
def test_protocol_response_correlation_matrix(raw: bytes, expected_failure: str) -> None:
    asyncio.run(_test_protocol_response_correlation_matrix(raw, expected_failure))


async def _test_protocol_response_correlation_matrix(raw: bytes, expected_failure: str) -> None:
    transport = _SingleFrameTransport(raw)
    client = JsonRpcClient(transport)
    await transport.connect()
    client._reader_task = asyncio.create_task(client._reader())
    with pytest.raises(ProtocolError):
        await asyncio.wait_for(client.request("thread/read", {}), timeout=1)
    assert client.last_failure_raw == raw or client.last_response_raw == raw
    assert expected_failure
    await client.close()


def test_protocol_error_response_retains_raw_and_fails_immediately() -> None:
    asyncio.run(_test_protocol_error_response_retains_raw_and_fails_immediately())


async def _test_protocol_error_response_retains_raw_and_fails_immediately() -> None:
    raw = b'{"id":1,"error":{"code":-32000,"message":"no"}}'
    transport = _SingleFrameTransport(raw)
    client = JsonRpcClient(transport)
    await transport.connect()
    client._reader_task = asyncio.create_task(client._reader())
    with pytest.raises(ProtocolError):
        await asyncio.wait_for(client.request("thread/read", {}), timeout=1)
    assert client.last_response_raw == raw
    await client.close()


def test_eof_after_valid_response_is_reported_without_dead_pending_waiters() -> None:
    asyncio.run(_test_eof_after_valid_response_is_reported_without_dead_pending_waiters())


async def _test_eof_after_valid_response_is_reported_without_dead_pending_waiters() -> None:
    valid = b'{"id":1,"result":{"ok":true}}'
    transport = _FrameSequenceTransport([valid])
    client = JsonRpcClient(transport)
    await transport.connect()
    client._reader_task = asyncio.create_task(client._reader())
    assert await asyncio.wait_for(client.request("thread/read", {}), timeout=1) == {"ok": True}
    await asyncio.sleep(0)
    assert client.last_failure_raw == valid
    await client.close()


def test_duplicate_response_after_valid_response_is_reported() -> None:
    asyncio.run(_test_duplicate_response_after_valid_response_is_reported())


async def _test_duplicate_response_after_valid_response_is_reported() -> None:
    valid = b'{"id":1,"result":{"ok":true}}'
    duplicate = b'{"id":1,"result":{"duplicate":true}}'
    transport = _FrameSequenceTransport([valid, duplicate])
    client = JsonRpcClient(transport)
    await transport.connect()
    client._reader_task = asyncio.create_task(client._reader())
    assert await asyncio.wait_for(client.request("thread/read", {}), timeout=1) == {"ok": True}
    await asyncio.sleep(0)
    assert client.last_failure_raw == duplicate
    await client.close()


def test_approval_server_request_gets_bounded_denial() -> None:
    asyncio.run(_test_approval_server_request_gets_bounded_denial())


async def _test_approval_server_request_gets_bounded_denial() -> None:
    raw = b'{"id":7,"method":"item/approvalRequest","params":{}}'
    transport = _SingleFrameTransport(raw)
    client = JsonRpcClient(transport)
    await transport.connect()
    client._reader_task = asyncio.create_task(client._reader())
    event = await asyncio.wait_for(client.next_event(1), timeout=1)
    assert event is not None
    assert any(b"-32001" in sent for sent in transport.sent)
    await client.close()


def test_stdio_duplicate_request_returns_bounded_error_and_recovers(tmp_path: Path) -> None:
    asyncio.run(_test_stdio_duplicate_request_returns_bounded_error_and_recovers(tmp_path))


async def _test_stdio_duplicate_request_returns_bounded_error_and_recovers(tmp_path: Path) -> None:
    source = Path(__file__).parent / "fixtures" / "codex_app_server_adapter" / "native_server.py"
    runtime_path = tmp_path / "native-fixture"
    runtime_path.write_text(f"#!{sys.executable}\n" + source.read_text(encoding="utf-8"), encoding="utf-8")
    os.chmod(runtime_path, 0o700)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    worktree = tmp_path / "worktree"
    worktree.mkdir(mode=0o700)
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({
        "runtime_path": str(runtime_path),
        "runtime_version": "bridge-native-fixture/1",
        "runtime_sha256": hashlib.sha256(runtime_path.read_bytes()).hexdigest(),
        "socket_path": str(private / "app.sock"),
        "allowed_worktree_roots": [str(worktree)],
        "allowed_models": ["gpt-5.6-luna"],
        "allowed_sandboxes": ["read-only"],
        "data_root": str(tmp_path / "data"),
    }), encoding="utf-8")
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "codex_app_server_adapter",
        "--profile",
        str(profile),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=environment,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    try:
        process.stdin.write(b'{"jsonrpc":"2.0","id":1,"method":"tools/list","id":2}\n')
        await process.stdin.drain()
        malformed = json.loads(await asyncio.wait_for(process.stdout.readline(), 2))
        assert malformed["id"] == 2
        assert malformed["error"]["code"] == -32700
        oversized = b'{"jsonrpc":"2.0","id":4,"method":"' + b"x" * (9 * 1024 * 1024) + b'"}\n'
        followup = (
            b'{"jsonrpc":"2.0","id":3,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"test","version":"1"}}}\n'
            b'{"jsonrpc":"2.0","id":5,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"test-2","version":"1"}}}\n'
        )
        process.stdin.write(oversized + followup)
        await process.stdin.drain()
        oversized_reply = json.loads(await asyncio.wait_for(process.stdout.readline(), 2))
        assert oversized_reply["error"]["code"] == -32700
        initialized = json.loads(await asyncio.wait_for(process.stdout.readline(), 2))
        assert initialized["id"] == 3
        assert "result" in initialized
        followup_initialized = json.loads(await asyncio.wait_for(process.stdout.readline(), 2))
        assert followup_initialized["id"] == 5
        assert "result" in followup_initialized
    finally:
        process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), 2)
        except TimeoutError:
            process.terminate()
            await asyncio.wait_for(process.wait(), 2)
            raise
    assert process.returncode == 0


def test_bounded_stdio_framing_preserves_split_coalesced_followups_and_eof() -> None:
    asyncio.run(_test_bounded_stdio_framing_preserves_split_coalesced_followups_and_eof())


async def _test_bounded_stdio_framing_preserves_split_coalesced_followups_and_eof() -> None:
    oversized = b"x" * (_MAX_STDIO_LINE_BYTES + 1) + b"\n"
    first = b'{"jsonrpc":"2.0","id":1}\n'
    second = b'{"jsonrpc":"2.0","id":2}\n'
    reader = _ChunkReader(
        [oversized[:100], oversized[100:] + first[:4], first[4:] + second]
    )
    buffered = bytearray()

    line, too_large = await _read_bounded_line(reader, buffered)
    assert line is None
    assert too_large is True
    line, too_large = await _read_bounded_line(reader, buffered)
    assert line == first
    assert too_large is False
    line, too_large = await _read_bounded_line(reader, buffered)
    assert line == second
    assert too_large is False
    line, too_large = await _read_bounded_line(reader, buffered)
    assert line is None
    assert too_large is False


def test_stdio_sigterm_runs_bounded_service_shutdown(tmp_path: Path) -> None:
    asyncio.run(_test_stdio_signal_runs_bounded_service_shutdown(tmp_path, signal.SIGTERM))


def test_stdio_sigint_runs_bounded_service_shutdown(tmp_path: Path) -> None:
    asyncio.run(_test_stdio_signal_runs_bounded_service_shutdown(tmp_path, signal.SIGINT))


def test_stdio_eof_runs_bounded_service_shutdown_with_active_owned_child(tmp_path: Path) -> None:
    asyncio.run(_test_stdio_signal_runs_bounded_service_shutdown(tmp_path, None))


async def _test_stdio_signal_runs_bounded_service_shutdown(
    tmp_path: Path, signum: signal.Signals | None
) -> None:
    source = Path(__file__).parent / "fixtures" / "codex_app_server_adapter" / "native_server.py"
    runtime_path = tmp_path / "native-fixture"
    runtime_path.write_text(
        f"#!{sys.executable}\n" + source.read_text(encoding="utf-8"), encoding="utf-8"
    )
    os.chmod(runtime_path, 0o700)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    worktree = tmp_path / "worktree"
    worktree.mkdir(mode=0o700)
    profile = tmp_path / "profile.json"
    profile.write_text(
        json.dumps(
            {
                "runtime_path": str(runtime_path),
                "runtime_version": "bridge-native-fixture/1",
                "runtime_sha256": hashlib.sha256(runtime_path.read_bytes()).hexdigest(),
                "socket_path": str(private / "app.sock"),
                "allowed_worktree_roots": [str(worktree)],
                "allowed_models": ["gpt-5.6-luna"],
                "allowed_sandboxes": ["read-only"],
                "data_root": str(tmp_path / "data"),
            }
        ),
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    pid_path = tmp_path / "fixture.pid"
    environment["BRIDGE_FIXTURE_PID_PATH"] = str(pid_path)
    environment["BRIDGE_FIXTURE_HOLD_TURN"] = "1"
    sentinel = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import time; time.sleep(30)",
        start_new_session=True,
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "codex_app_server_adapter",
        "--profile",
        str(profile),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=environment,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    try:
        process.stdin.write(
            b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"test","version":"1"}}}\n'
        )
        await process.stdin.drain()
        initialized = json.loads(await asyncio.wait_for(process.stdout.readline(), 2))
        assert initialized["id"] == 1
        request = {
            "request_id": "6d3514ae-d8bd-4b0e-bd8f-bb5d64921e7a",
            "prompt": "x" * (70 * 1024),
            "cwd": str(worktree),
            "model": "gpt-5.6-luna",
            "sandbox": "read-only",
            "deadline_utc": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        }
        process.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "codex_start", "arguments": request},
                }
            ).encode()
            + b"\n"
        )
        await process.stdin.drain()
        started = json.loads(await asyncio.wait_for(process.stdout.readline(), 2))
        assert started["id"] == 2
        assert "result" in started
        assert started["result"].get("isError") is not True, started
        start_status = json.loads(started["result"]["content"][0]["text"])
        task_id = start_status["task_id"]
        for _ in range(200):
            if pid_path.exists() and (private / "app.sock").exists():
                break
            await asyncio.sleep(0.01)
        assert pid_path.exists()
        assert (private / "app.sock").exists()
        child_pid = int(pid_path.read_text(encoding="utf-8"))
        os.kill(child_pid, 0)
        active_status: dict[str, object] = {}
        for request_id in range(3, 203):
            process.stdin.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": "tools/call",
                        "params": {
                            "name": "codex_status",
                            "arguments": {"task_id": task_id, "wait_seconds": 0},
                        },
                    }
                ).encode()
                + b"\n"
            )
            await process.stdin.drain()
            active = json.loads(await asyncio.wait_for(process.stdout.readline(), 2))
            active_status = json.loads(active["result"]["content"][0]["text"])
            if active_status["state"] == "running" and active_status["turn_submitted"] is True:
                break
            await asyncio.sleep(0.01)
        assert active_status["state"] == "running"
        assert active_status["turn_submitted"] is True
        if signum is None:
            process.stdin.close()
        else:
            process.send_signal(signum)
        await asyncio.wait_for(process.wait(), 2)
        for _ in range(200):
            if not (private / "app.sock").exists():
                break
            await asyncio.sleep(0.01)
        assert not (private / "app.sock").exists()
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
        assert sentinel.returncode is None
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
        if sentinel.returncode is None:
            sentinel.terminate()
            await sentinel.wait()
    assert process.returncode == 0


def test_large_native_history_is_losslessly_paginated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRIDGE_FIXTURE_LARGE", "1")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    journal = tmp_path / "codex" / "sessions" / "fixture.jsonl"
    journal.parent.mkdir(parents=True, mode=0o700)
    journal.write_bytes(
        b'{"type":"session_meta","payload":{"id":"fixture-thread","session_id":"fixture-thread"}}\n'
        b'{"type":"turn_context","payload":{"turn_id":"fixture-turn"}}\n'
        b'{"type":"response_item","payload":{"role":"assistant","phase":"final_answer","content":[{"type":"output_text","text":"done"}]}}\n'
        b'{"type":"event_msg","payload":{"type":"task_complete","turn_id":"fixture-turn"}}\n'
    )
    monkeypatch.setenv("BRIDGE_FIXTURE_JOURNAL_PATH", str(journal))
    asyncio.run(_test_large_native_history_is_losslessly_paginated(tmp_path))


async def _test_large_native_history_is_losslessly_paginated(tmp_path: Path) -> None:
    source = Path(__file__).parent / "fixtures" / "codex_app_server_adapter" / "native_server.py"
    runtime_path = tmp_path / "native-fixture"
    runtime_path.write_text(f"#!{sys.executable}\n" + source.read_text(encoding="utf-8"), encoding="utf-8")
    os.chmod(runtime_path, 0o700)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    worktree = tmp_path / "worktree"
    worktree.mkdir(mode=0o700)
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    profile = OperatorProfile(
        runtime_path=runtime_path,
        runtime_version="bridge-native-fixture/1",
        runtime_sha256=hashlib.sha256(runtime_path.read_bytes()).hexdigest(),
        socket_path=private / "app.sock",
        allowed_worktree_roots=(worktree,),
        allowed_models=("gpt-5.6-luna",),
        allowed_sandboxes=("read-only",),
        data_root=data,
    )
    from codex_app_server_adapter.ownership import DataRootOwnership

    runtime = OwnedRuntime(profile)
    ownership = DataRootOwnership(data)
    service = AdapterService(
        profile,
        TaskStore(data),
        lambda: OwnedAppServerClient(runtime),
        ownership=ownership,
        shutdown_callback=runtime.close,
    )
    request = {
        "request_id": "4b8b0e7f-13c6-4b24-a7d7-6d2cbdad5ec1",
        "prompt": "bounded prompt",
        "cwd": str(worktree),
        "model": "gpt-5.6-luna",
        "sandbox": "read-only",
        "deadline_utc": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    }
    try:
        started = await service.start(request)
        status: dict[str, object] = {}
        cursor = 0
        for _ in range(20):
            status = await service.status(
                {"task_id": started["task_id"], "wait_seconds": 5, "cursor": cursor}
            )
            if status["state"] in {"completed", "failed", "unknown", "cancelled"}:
                break
            cursor = int(status["cursor"])
        assert status["state"] == "completed"
        assert status["native_evidence_status"] == "complete"
        chunks: list[bytes] = []
        offset = 0
        while True:
            page = await service.result({"task_id": started["task_id"], "offset_bytes": offset})
            chunks.append(base64.b64decode(page["chunk"]))
            if page["next_offset"] is None:
                break
            offset = int(page["next_offset"])
        content = b"".join(chunks)
        assert len(content) > 2 * 1024 * 1024
        result_metadata = service.store.load(str(started["task_id"]))["result"]
        assert isinstance(result_metadata, dict)
        assert hashlib.sha256(content).hexdigest() == result_metadata["sha256"]
        journal_metadata = service.store.load(str(started["task_id"]))["native_journal"]
        assert isinstance(journal_metadata, dict)
        assert journal_metadata["session_id"] == "fixture-thread"
        assert journal_metadata["turn_id"] == "fixture-turn"
    finally:
        await service.shutdown()


def _derive_physical(alias: Path) -> Path:
    """Second, independent derivation of the Codex CLI 0.156.0 physical socket path."""

    shared = Path(os.path.realpath("/tmp")) / f"codex-daemon-{os.geteuid()}"
    digest = hashlib.sha256(
        os.fsencode(os.path.join(os.path.realpath(alias.parent), alias.name))
    ).hexdigest()
    return shared / digest


def _alias_mode_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> OperatorProfile:
    source = Path(__file__).parent / "fixtures" / "codex_app_server_adapter" / "native_server.py"
    runtime_path = tmp_path / "native-fixture"
    runtime_path.write_text(
        f"#!{sys.executable}\n" + source.read_text(encoding="utf-8"), encoding="utf-8"
    )
    os.chmod(runtime_path, 0o700)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    worktree = tmp_path / "worktree"
    worktree.mkdir(mode=0o700)
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    monkeypatch.setenv("BRIDGE_FIXTURE_ALIAS", "1")
    return OperatorProfile(
        runtime_path=runtime_path,
        runtime_version="bridge-native-fixture/1",
        runtime_sha256=hashlib.sha256(runtime_path.read_bytes()).hexdigest(),
        socket_path=private / "app.sock",
        allowed_worktree_roots=(worktree,),
        allowed_models=("gpt-5.6-luna",),
        allowed_sandboxes=("read-only",),
        data_root=data,
    )


def test_owned_client_connects_through_the_verified_physical_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asyncio.run(_test_owned_client_connects_through_the_verified_physical_socket(tmp_path, monkeypatch))


async def _test_owned_client_connects_through_the_verified_physical_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = _alias_mode_profile(tmp_path, monkeypatch)
    physical = _derive_physical(profile.socket_path)
    runtime = OwnedRuntime(profile)
    client = OwnedAppServerClient(runtime)
    try:
        await client.connect()
        assert profile.socket_path.is_symlink()
        assert os.readlink(profile.socket_path) == str(physical)
        assert runtime.connect_path == physical
        assert client.unix_transport.socket_path == physical
        started = await client.request(
            "thread/start",
            {"cwd": str(profile.allowed_worktree_roots[0]), "model": "gpt-5.6-luna", "sandbox": "read-only"},
        )
        assert started["thread"] == {"id": "fixture-thread"}
        assert started["cwd"] == str(profile.allowed_worktree_roots[0])
    finally:
        await client.close()
        await runtime.close()
        with suppress(FileNotFoundError):
            profile.socket_path.unlink()
        with suppress(FileNotFoundError):
            physical.unlink()
    assert not os.path.lexists(profile.socket_path)
    assert not physical.exists()


def test_alias_identity_change_refuses_reuse_of_the_live_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asyncio.run(_test_alias_identity_change_refuses_reuse_of_the_live_runtime(tmp_path, monkeypatch))


async def _test_alias_identity_change_refuses_reuse_of_the_live_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket as socket_module

    profile = _alias_mode_profile(tmp_path, monkeypatch)
    physical = _derive_physical(profile.socket_path)
    runtime = OwnedRuntime(profile)
    decoy = tmp_path / "decoy.sock"
    bound = socket_module.socket(socket_module.AF_UNIX)
    try:
        await runtime.ensure_started()
        assert runtime.connect_path == physical
        bound.bind(str(decoy))
        os.chmod(decoy, 0o600)
        profile.socket_path.unlink()
        profile.socket_path.symlink_to(decoy)
        with pytest.raises(ProtocolError, match="owned app-server socket identity changed"):
            await runtime.ensure_started()
        # The captured target still governs the connection; the re-pointed alias never does.
        assert runtime.connect_path == physical
    finally:
        bound.close()
        with suppress(ProtocolError):
            await runtime.close()
        with suppress(FileNotFoundError):
            decoy.unlink()
        with suppress(FileNotFoundError):
            profile.socket_path.unlink()
        with suppress(FileNotFoundError):
            physical.unlink()


def test_endpoint_substituted_in_the_connect_window_receives_no_protocol_traffic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asyncio.run(_test_endpoint_substituted_in_the_connect_window_receives_no_protocol_traffic(tmp_path, monkeypatch))


async def _test_endpoint_substituted_in_the_connect_window_receives_no_protocol_traffic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for P2-AG-395-002: substitution between admission and the transport connect.

    Connection admission re-checks the captured endpoint after the transport connects and before
    the first request leaves the process, so a substituted endpoint receives no JSON-RPC traffic.
    The WebSocket handshake still reaches it; that residual is documented rather than claimed
    away, and this test records exactly which of the two happens.
    """

    from websockets.asyncio.server import unix_serve

    profile = _alias_mode_profile(tmp_path, monkeypatch)
    physical = _derive_physical(profile.socket_path)
    staging = tmp_path / "recorder.sock"
    runtime = OwnedRuntime(profile)
    received: list[object] = []
    reached = asyncio.Event()

    async def recorder(connection: object) -> None:
        reached.set()
        async for message in connection:  # type: ignore[attr-defined]
            received.append(message)

    server = None
    try:
        await runtime.ensure_started()
        assert runtime.connect_path == physical
        captured = runtime._socket_identity
        assert captured is not None

        server = await unix_serve(recorder, path=str(staging), compression=None)
        os.chmod(staging, 0o600)
        substituted: list[int] = []
        original_connect = UnixWebSocketTransport.connect

        async def substituting_connect(transport: UnixWebSocketTransport) -> None:
            if not substituted:
                # Replace the captured socket inside the admission-to-connect window.
                physical.unlink()
                os.rename(staging, physical)
                substituted.append(physical.lstat().st_ino)
            await original_connect(transport)

        monkeypatch.setattr(UnixWebSocketTransport, "connect", substituting_connect)

        client = OwnedAppServerClient(runtime)
        with pytest.raises(ProtocolError, match="owned app-server socket identity changed"):
            await client.connect()

        assert substituted and substituted[0] != captured.ino
        # The substitution really did take the connection: the replacement served the
        # WebSocket handshake. It was nonetheless never sent a JSON-RPC request, because
        # admission refused the endpoint before `initialize`.
        await asyncio.wait_for(reached.wait(), timeout=5)
        assert received == []
    finally:
        monkeypatch.undo()
        if server is not None:
            server.close()
            await server.wait_closed()
        with suppress(ProtocolError):
            await runtime.close()
        for leftover in (staging, physical, profile.socket_path):
            with suppress(FileNotFoundError):
                leftover.unlink()


def test_endpoint_substitution_reverted_before_admission_is_not_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asyncio.run(_test_endpoint_substitution_reverted_before_admission_is_not_detected(tmp_path, monkeypatch))


async def _test_endpoint_substitution_reverted_before_admission_is_not_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Execute the accepted residual behind connection admission (P2C-AG-395-001).

    Admission re-derives the identity of the captured path. It does not authenticate the peer,
    so a substitution reverted before the check passes it and the substitute receives the
    JSON-RPC handshake. This test demonstrates and labels that behaviour; it deliberately does
    not assert prevention, because nothing here prevents it.
    """

    from websockets.asyncio.server import unix_serve

    profile = _alias_mode_profile(tmp_path, monkeypatch)
    physical = _derive_physical(profile.socket_path)
    substitute = tmp_path / "substitute.sock"
    held = tmp_path / "held.sock"
    runtime = OwnedRuntime(profile)
    received: list[str] = []
    handshake_seen = asyncio.Event()

    async def recorder(connection: object) -> None:
        async for message in connection:  # type: ignore[attr-defined]
            payload = json.loads(message)
            if "method" in payload:
                received.append(str(payload["method"]))
            if "id" in payload:
                await connection.send(  # type: ignore[attr-defined]
                    json.dumps({"id": payload["id"], "result": {}})
                )
            if received == ["initialize", "initialized"]:
                handshake_seen.set()

    server = None
    client = None
    try:
        await runtime.ensure_started()
        captured = runtime._socket_identity
        assert captured is not None

        server = await unix_serve(recorder, path=str(substitute), compression=None)
        os.chmod(substitute, 0o600)
        original_connect = UnixWebSocketTransport.connect
        swapped: list[str] = []

        async def swap_and_restore_connect(transport: UnixWebSocketTransport) -> None:
            # Renames preserve inodes, so the captured socket can be moved aside and put back.
            os.rename(physical, held)
            os.rename(substitute, physical)
            swapped.append("substituted")
            try:
                await original_connect(transport)
            finally:
                os.rename(physical, substitute)
                os.rename(held, physical)

        monkeypatch.setattr(UnixWebSocketTransport, "connect", swap_and_restore_connect)

        client = OwnedAppServerClient(runtime)
        await client.connect()

        assert swapped == ["substituted"]
        # The captured identity is back in place, so admission sees exactly what it captured.
        assert runtime._same_socket() is True
        # The substitute nonetheless served the whole handshake. This is the accepted residual.
        # `initialized` is a notification, so wait for the server to observe it rather than
        # racing it.
        await asyncio.wait_for(handshake_seen.wait(), timeout=5)
        assert received == ["initialize", "initialized"]
    finally:
        monkeypatch.undo()
        if client is not None:
            with suppress(Exception):
                await client.close()
        if server is not None:
            server.close()
            await server.wait_closed()
        with suppress(ProtocolError):
            await runtime.close()
        for leftover in (substitute, held, physical, profile.socket_path):
            with suppress(FileNotFoundError):
                leftover.unlink()
