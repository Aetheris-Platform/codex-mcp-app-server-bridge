"""Portable tests of Windows JSONL framing, disconnect and reader ownership.

These exercise the transport on any OS; Windows kernel guarantees are tested
separately in test_windows_platform.py, never inferred from these mocks.
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from codex_app_server_adapter.errors import ProtocolError
from codex_app_server_adapter.protocol import JsonRpcClient
from codex_app_server_adapter.windows_runtime import OwnedRuntime, StdioTransport


class PipeJob:
    def __init__(self):
        self.lines = queue.Queue()
        self.stdout = self
        self.stdin = self
        self.closed = False
        self.read_count = 0
        self.read_started = threading.Event()
        self.sent = []

    def readline(self):
        self.read_count += 1
        self.read_started.set()
        return self.lines.get(timeout=10)

    def write(self, raw):
        message = json.loads(bytes(raw))
        self.sent.append(message)
        if "id" in message and "method" in message:
            self.lines.put(json.dumps({"id": message["id"], "result": {"accepted": True}}).encode() + b"\n")
        return len(raw)

    def poll(self):
        return 0 if self.closed else None

    def close(self):
        self.closed = True
        self.lines.put(b"")


def setup_transport():
    runtime = OwnedRuntime(SimpleNamespace())
    job = PipeJob()
    runtime._jobs.append(job)

    async def open_connection():
        return job

    runtime.open_connection = open_connection
    return runtime, job, StdioTransport(runtime)


def test_stdio_identity_and_disconnect_do_not_imply_termination():
    async def exercise():
        runtime, job, transport = setup_transport()
        client = JsonRpcClient(transport)
        try:
            await client.connect()
            assert job.sent[0]["params"]["clientInfo"]["name"] == "codex-mcp-app-server-bridge"
            assert await client.request("thread/read", {}) == {"accepted": True}
            await client.close()
            assert not job.closed
            job.lines.put(b'{"method":"late-event","params":{}}\n')
            await asyncio.sleep(0.02)
        finally:
            await runtime.close()
        assert job.closed
    asyncio.run(exercise())


def test_cancelled_reader_transfers_one_outstanding_read_to_drainer():
    async def exercise():
        runtime, job, transport = setup_transport()
        await transport.connect()
        reading = asyncio.create_task(transport.recv())
        try:
            assert await asyncio.to_thread(job.read_started.wait, 5)
            reading.cancel()
            await asyncio.gather(reading, return_exceptions=True)
            assert job.read_count == 1
            job.lines.put(b'{"method":"event"}\n')
            assert await transport.recv() == b'{"method":"event"}'
            assert job.read_count == 1
        finally:
            await runtime.close()
    asyncio.run(exercise())


@pytest.mark.parametrize("line", [b"", b'{"truncated":true}'])
def test_stdio_eof_and_truncation_fail(line):
    async def exercise():
        runtime, job, transport = setup_transport()
        await transport.connect()
        job.lines.put(line)
        try:
            with pytest.raises((EOFError, ProtocolError)):
                await transport.recv()
        finally:
            await runtime.close()
    asyncio.run(exercise())


def test_stdio_large_utf8_frame_is_not_split():
    async def exercise():
        runtime, job, transport = setup_transport()
        await transport.connect()
        content = json.dumps({"text": "✓" * 800000}, ensure_ascii=False).encode()
        job.lines.put(content + b"\r\n")
        try:
            assert await transport.recv() == content
            with pytest.raises(ProtocolError, match="one JSON line"):
                await transport.send(b"{}\n{}")
        finally:
            await runtime.close()
    asyncio.run(exercise())


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has the actual Job Object MCP test")
def test_real_stdio_fixture_completes_and_interrupts(tmp_path, monkeypatch):
    """Exercise native JSONL with real pipes on Unix, without claiming Windows jobs."""
    from test_codex_app_server_adapter import FakeClient, _request, _service

    from codex_app_server_adapter.windows_runtime import OwnedAppServerClient

    history = tmp_path / "history"
    history.mkdir()
    monkeypatch.setenv("BRIDGE_FIXTURE_HISTORY_DIR", str(history))
    monkeypatch.setenv("BRIDGE_FIXTURE_REQUEST_LOG", str(tmp_path / "native.log"))
    fixture = Path(__file__).parent / "fixtures/codex_app_server_adapter/stdio_server.py"

    class LocalJob:
        def __init__(self):
            self.process = subprocess.Popen(  # noqa: S603 - owned offline fixture
                [sys.executable, str(fixture)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, bufsize=0,
            )
            self.stdin, self.stdout = self.process.stdin, self.process.stdout

        def poll(self):
            return self.process.poll()

        def close(self):
            self.process.kill()
            self.process.wait(timeout=5)
            self.stdin.close()
            self.stdout.close()

    async def exercise():
        service = _service(tmp_path, FakeClient())
        runtime = OwnedRuntime(service.profile)

        async def connect():
            job = LocalJob()
            runtime._jobs.append(job)
            return job

        runtime.open_connection = connect
        service.client_factory = lambda: OwnedAppServerClient(runtime)
        try:
            request = _request(tmp_path / "worktree")
            request["prompt"] = "small"
            started = await service.start(request)
            result = {}
            for _ in range(30):
                result = await service.status({"task_id": started["task_id"], "wait_seconds": 1, "cursor": result.get("cursor", 0)})
                if result["state"] in {"completed", "failed", "unknown"}:
                    break
            assert result["state"] == "completed", result
            held_request = _request(tmp_path / "worktree")
            held_request["prompt"] = "hold"
            held = await service.start(held_request)
            result = {}
            for _ in range(30):
                result = await service.status({"task_id": held["task_id"], "wait_seconds": 1, "cursor": result.get("cursor", 0)})
                if result["turn_submitted"]:
                    break
            assert result["turn_submitted"]
            await service.cancel({"task_id": held["task_id"]})
            result = {}
            for _ in range(30):
                result = await service.status({"task_id": held["task_id"], "wait_seconds": 1, "cursor": result.get("cursor", 0)})
                if result["state"] == "cancelled":
                    break
            assert result["reason"] == "provider_interrupted", result
        finally:
            await service.shutdown()
            await runtime.close()
    asyncio.run(exercise())
