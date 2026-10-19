"""Native local WebSocket JSON-RPC protocol client."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Protocol, cast

from websockets.exceptions import WebSocketException

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

from .errors import ProtocolError, ValidationError
from .validation import loads_object

JsonObject = dict[str, object]


class ProtocolTransport(Protocol):
    async def connect(self) -> None: ...

    async def send(self, raw: bytes) -> None: ...

    async def recv(self) -> bytes: ...

    async def close(self) -> None: ...


class WebSocketConnection(Protocol):
    async def send(self, data: str) -> None: ...

    async def recv(self) -> str | bytes: ...

    async def close(self) -> None: ...


class AppServerClient(Protocol):
    async def connect(self) -> None: ...

    async def request(self, method: str, params: JsonObject) -> JsonObject: ...

    async def next_event(self, timeout_seconds: float) -> tuple[bytes, JsonObject] | None: ...

    async def close(self) -> None: ...

    @property
    def last_response_raw(self) -> bytes | None: ...

    @property
    def last_failure_raw(self) -> bytes | None: ...


class UnixWebSocketTransport:
    """WebSocket framing over one Unix socket; no TCP fallback is permitted."""

    def __init__(self, socket_path: Path) -> None:
        self.socket_path = socket_path
        self._connection: WebSocketConnection | None = None

    async def connect(self) -> None:
        from websockets.asyncio.client import unix_connect

        self._connection = cast(
            "WebSocketConnection",
            await unix_connect(
                path=str(self.socket_path),
                uri="ws://localhost/",
                compression=None,
                max_size=None,
            ),
        )

    async def send(self, raw: bytes) -> None:
        connection = self._connection
        if connection is None:
            raise ProtocolError("native connection is not open")
        await connection.send(raw.decode("utf-8"))

    async def recv(self) -> bytes:
        connection = self._connection
        if connection is None:
            raise ProtocolError("native connection is not open")
        value = await connection.recv()
        if isinstance(value, str):
            return value.encode("utf-8")
        if isinstance(value, bytes):
            return value
        raise ProtocolError("native server returned a non-text WebSocket frame")

    async def close(self) -> None:
        connection = self._connection
        if connection is not None:
            await connection.close()
            self._connection = None


class JsonRpcClient:
    """JSON-RPC client with a continuous reader and explicit server-request denial."""

    def __init__(self, transport: ProtocolTransport, *, client_version: str = "1") -> None:
        self.transport = transport
        self.client_version = client_version
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[JsonObject]] = {}
        self._response_raw: dict[int, bytes] = {}
        self._events: asyncio.Queue[tuple[bytes, JsonObject]] = asyncio.Queue()
        self._reader_task: asyncio.Task[None] | None = None
        self._request_lock = asyncio.Lock()
        self.last_response_raw: bytes | None = None
        self.last_failure_raw: bytes | None = None

    async def connect(self) -> None:
        await self.transport.connect()
        try:
            await self._admit_connection()
        except BaseException:
            await self.transport.close()
            raise
        self._reader_task = asyncio.create_task(self._reader())
        await self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "codex-mcp-app-server-bridge",
                    "title": "Codex MCP app-server bridge",
                    "version": self.client_version,
                },
                "capabilities": {},
            },
        )
        await self._send_notification("initialized", {})

    async def _admit_connection(self) -> None:
        """Admit the connected endpoint before any protocol traffic is sent.

        The default transport has no endpoint identity to check.  Subclasses that own their
        endpoint override this to re-admit it after the transport connects and before the
        first request leaves the process.
        """

    async def request(self, method: str, params: JsonObject) -> JsonObject:
        async with self._request_lock:
            return await self._request_serialized(method, params)

    async def _request_serialized(self, method: str, params: JsonObject) -> JsonObject:
        request_id = self._next_id
        self._next_id += 1
        self.last_response_raw = None
        self.last_failure_raw = None
        future: asyncio.Future[JsonObject] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        payload = {"id": request_id, "method": method, "params": params}
        try:
            await self.transport.send(_json_bytes(payload))
            response = await future
            self.last_response_raw = self._response_raw.pop(request_id, None)
            return response
        except asyncio.CancelledError:
            raise
        except (OSError, ProtocolError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self.last_response_raw = self._response_raw.pop(request_id, None)
            raise ProtocolError(f"JSON-RPC request failed: {method}") from exc
        finally:
            self._pending.pop(request_id, None)

    async def next_event(self, timeout_seconds: float) -> tuple[bytes, JsonObject] | None:
        try:
            return await asyncio.wait_for(self._events.get(), timeout=max(timeout_seconds, 0.0))
        except TimeoutError:
            return None

    async def close(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            await asyncio.gather(self._reader_task, return_exceptions=True)
            self._reader_task = None
        await self.transport.close()

    async def _reader(self) -> None:
        raw: bytes | None = None
        try:
            while True:
                raw = await self.transport.recv()
                try:
                    message = _decode(raw)
                except ProtocolError:
                    malformed: JsonObject = {
                        "method": "adapterError",
                        "params": {"reason": "native_malformed_frame"},
                    }
                    await self._events.put((raw, malformed))
                    self.last_failure_raw = raw
                    error = ProtocolError("native malformed frame")
                    for future in self._pending.values():
                        if not future.done():
                            future.set_exception(error)
                    return
                if ("result" in message or "error" in message) and "id" not in message:
                    raise ProtocolError("JSON-RPC response id is missing")
                if "id" in message and ("result" in message or "error" in message):
                    request_id = message["id"]
                    if isinstance(request_id, bool) or not isinstance(request_id, int):
                        raise ProtocolError("JSON-RPC response id is invalid")
                    if "result" in message and "error" in message:
                        self._response_raw[request_id] = raw
                        raise ProtocolError("JSON-RPC response contains result and error")
                    future_candidate = self._pending.get(request_id)
                    if future_candidate is None or future_candidate.done():
                        raise ProtocolError("unmatched JSON-RPC response")
                    future = future_candidate
                    if "error" in message:
                        self._response_raw[request_id] = raw
                        future.set_exception(ProtocolError("native JSON-RPC error"))
                    else:
                        self._response_raw[request_id] = raw
                        result = message.get("result")
                        if not isinstance(result, dict):
                            future.set_exception(ProtocolError("JSON-RPC result is not an object"))
                        else:
                            future.set_result(cast("JsonObject", result))
                    continue
                if "method" in message and "id" in message:
                    await self._deny_server_request(message)
                await self._events.put((raw, message))
        except asyncio.CancelledError:
            raise
        except (EOFError, OSError, ProtocolError, TypeError, ValueError, WebSocketException):
            error = ProtocolError("native reader stopped")
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(error)
            failure_message: JsonObject = {
                "method": "adapterError",
                "params": {"reason": "native_connection_lost"},
            }
            if raw is None:
                await self._events.put((_json_bytes(failure_message), failure_message))
            else:
                self.last_failure_raw = raw
                await self._events.put((raw, failure_message))

    async def _deny_server_request(self, message: JsonObject) -> None:
        method = message.get("method")
        request_id = message.get("id")
        if (
            not isinstance(method, str)
            or isinstance(request_id, bool)
            or not isinstance(request_id, int)
        ):
            raise ProtocolError("native server request identity is invalid")
        lowered = method.lower()
        if "approval" in lowered or "permission" in lowered or "elicitation" in lowered:
            response = {
                "id": request_id,
                "error": {"code": -32001, "message": "adapter permission requests are denied"},
            }
            await self.transport.send(_json_bytes(response))

    async def _send_notification(self, method: str, params: JsonObject) -> None:
        await self.transport.send(_json_bytes({"method": method, "params": params}))


def _json_bytes(value: Mapping[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _decode(raw: bytes) -> JsonObject:
    try:
        value = loads_object(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValidationError, json.JSONDecodeError) as exc:
        raise ProtocolError("native message is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ProtocolError("native message is not an object")
    return value
