"""Native Windows owned stdio connections; no TCP listener or Unix emulation.

Codex stdio admits one client per process. Each connection therefore gets an
owned child in a private kill-on-close Job Object. Logical disconnect leaves
the native child alive (and drains output); only session shutdown kills jobs.
This preserves uncertainty/disconnect semantics instead of implying cancel.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from .errors import ProtocolError
from .protocol import JsonRpcClient

if TYPE_CHECKING:
    from .profile import OperatorProfile
    from .windows_process import JobProcess


class OwnedRuntime:
    def __init__(self, profile: OperatorProfile) -> None:
        self.profile = profile
        self._jobs: list[JobProcess] = []
        self._drainers: list[asyncio.Task[None]] = []
        self._lock = asyncio.Lock()
        self._closed = False

    async def open_connection(self) -> JobProcess:
        from .windows_process import JobProcess

        async with self._lock:
            if self._closed:
                raise ProtocolError("owned Windows runtime is closed")
            if any(job.poll() not in (None, 0) for job in self._jobs):
                raise ProtocolError("owned Windows child exited abnormally; recreation refused")
            await asyncio.to_thread(self.profile.verify_runtime)
            # Creation is synchronous: cancellation cannot abandon a job created
            # in a worker thread after its awaiting coroutine has disappeared.
            job = JobProcess([str(self.profile.runtime_path), "app-server", "--listen", "stdio://"])
            self._jobs.append(job)
            return job

    def drain(self, transport: StdioTransport) -> None:
        async def consume() -> None:
            try:
                while True:
                    await transport.read_frame()
            except (EOFError, OSError, ValueError, ProtocolError):
                return

        self._drainers.append(asyncio.create_task(consume()))

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            failures: list[Exception] = []
            for job in self._jobs:
                try:
                    await asyncio.to_thread(job.close)
                except (OSError, ProtocolError) as exc:
                    failures.append(exc)
            self._jobs.clear()
            for task in self._drainers:
                task.cancel()
            await asyncio.gather(*self._drainers, return_exceptions=True)
            self._drainers.clear()
            if failures:
                raise ProtocolError("owned Windows job cleanup failed") from failures[0]


class StdioTransport:
    def __init__(self, runtime: OwnedRuntime) -> None:
        self.runtime = runtime
        self.job: JobProcess | None = None
        self._reading: asyncio.Task[bytes] | None = None
        self._closed = False
        self._write_lock = asyncio.Lock()

    async def connect(self) -> None:
        if self._closed or self.job is not None:
            raise ProtocolError("native stdio connection cannot be reopened")
        self.job = await self.runtime.open_connection()

    async def send(self, raw: bytes) -> None:
        job = self.job
        if self._closed or job is None or job.stdin is None or job.poll() is not None:
            raise ProtocolError("native stdio connection is not open")
        if b"\n" in raw:
            raise ProtocolError("native stdio frame must be one JSON line")
        async with self._write_lock:
            await asyncio.to_thread(self._write, raw + b"\n")

    def _write(self, raw: bytes) -> None:
        if self.job is None or self.job.stdin is None:
            raise ProtocolError("native stdio connection is not open")
        # Raw Windows pipe writes can be partial.
        remaining = memoryview(raw)
        while remaining:
            count = self.job.stdin.write(remaining)
            if not count:
                raise EOFError("native input pipe closed")
            remaining = remaining[count:]

    async def read_frame(self) -> bytes:
        if self.job is None or self.job.stdout is None:
            raise EOFError("native stdio connection is not open")
        if self._reading is None:
            self._reading = asyncio.create_task(asyncio.to_thread(self.job.stdout.readline))
        # Cancellation of a client must not start two competing pipe readers.
        line = await asyncio.shield(self._reading)
        self._reading = None
        if not line:
            raise EOFError("native output pipe closed")
        if not line.endswith(b"\n"):
            raise ProtocolError("native stdio frame is truncated")
        return line.rstrip(b"\r\n")

    async def recv(self) -> bytes:
        if self._closed:
            raise EOFError("native stdio connection closed")
        return await self.read_frame()

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            if self.job is not None:
                self.runtime.drain(self)


class OwnedAppServerClient(JsonRpcClient):
    def __init__(self, runtime: OwnedRuntime) -> None:
        super().__init__(StdioTransport(runtime))
        self.runtime = runtime
