"""Inactive-by-default stdio MCP facade for the adapter service."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, cast

import anyio
import mcp_types as types
from mcp.shared._context_streams import create_context_streams
from mcp.shared.message import SessionMessage

from .errors import AdapterError, ValidationError
from .validation import loads_object

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from mcp.server.lowlevel.server import Server
    from mcp.shared._stream_protocols import ReadStream, WriteStream
    from mcp.types import CallToolRequestParams, CallToolResult, ListToolsResult

    from .service import AdapterService

JsonObject = dict[str, object]
_MAX_REPLY_BYTES = 16 * 1024
_MAX_STDIO_LINE_BYTES = 8 * 1024 * 1024


def create_server(service: AdapterService) -> Server:
    """Create the distinct ``codex-app-server`` MCP server identity."""

    from mcp.server.lowlevel.server import Server

    async def on_list_tools(
        _context: object, _params: object
    ) -> ListToolsResult:
        from mcp.types import ListToolsResult, Tool

        return ListToolsResult(tools=[
            Tool(
                name="codex_start",
                description="Start one bounded Codex task.",
                input_schema=_schema(
                    {"request_id": {"type": "string"}, "prompt": {"type": "string"},
                     "cwd": {"type": "string"}, "model": {"type": "string"},
                     "sandbox": {"type": "string", "enum": ["read-only", "workspace-write"]},
                     "deadline_utc": {"type": "string"}},
                    ["request_id", "prompt", "cwd", "model", "sandbox", "deadline_utc"],
                ),
            ),
            Tool(
                name="codex_status",
                description="Read one bounded Codex task status.",
                input_schema=_schema(
                    {"task_id": {"type": "string"}, "wait_seconds": {"type": "integer", "minimum": 0, "maximum": 30},
                     "cursor": {"type": "integer", "minimum": 0}}, ["task_id"],
                ),
            ),
            Tool(
                name="codex_result",
                description="Read one bounded result chunk.",
                input_schema=_schema(
                    {"task_id": {"type": "string"}, "offset_bytes": {"type": "integer", "minimum": 0},
                     "limit_bytes": {"type": "integer", "minimum": 1, "maximum": 4096}}, ["task_id"],
                ),
            ),
            Tool(
                name="codex_cancel",
                description="Request cancellation of one bounded Codex task.",
                input_schema=_schema({"task_id": {"type": "string"}}, ["task_id"]),
            ),
        ])

    async def on_call_tool(
        _context: object, params: CallToolRequestParams
    ) -> CallToolResult:
        arguments = params.arguments
        if not isinstance(arguments, dict):
            return _error_result("tool arguments must be an object")
        try:
            if params.name == "codex_start":
                result = await service.start(cast("JsonObject", arguments))
            elif params.name == "codex_status":
                result = await service.status(cast("JsonObject", arguments))
            elif params.name == "codex_result":
                result = await service.result(cast("JsonObject", arguments))
            elif params.name == "codex_cancel":
                result = await service.cancel(cast("JsonObject", arguments))
            else:
                return _error_result("unknown adapter tool")
        except AdapterError as exc:
            return _error_result(str(exc))
        return _success_result(result)

    return Server(
        "codex-app-server",
        version="1.0.0",
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


async def run_stdio(service: AdapterService) -> None:
    """Run the facade only when an operator explicitly starts this process."""

    server = create_server(service)
    async with _strict_stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


@asynccontextmanager
async def _strict_stdio_server() -> AsyncIterator[
    tuple[ReadStream[SessionMessage | Exception], WriteStream[SessionMessage]]
]:
    """Provide SDK low-level streams while rejecting duplicate JSON object keys."""

    stdin_transport = None
    if os.name == "nt":
        # MCP hosts may supply synchronous anonymous Windows handles, which
        # Proactor connect_read_pipe cannot turn into overlapped handles.
        stdin_reader = _WindowsInput()
    else:
        stdin_reader = asyncio.StreamReader(limit=_MAX_STDIO_LINE_BYTES)
        stdin_protocol = asyncio.StreamReaderProtocol(stdin_reader)
        stdin_transport, _ = await asyncio.get_running_loop().connect_read_pipe(
            lambda: stdin_protocol, sys.stdin.buffer
        )
    stdout = anyio.wrap_file(sys.stdout.buffer)
    read_send, read_stream = create_context_streams[SessionMessage | Exception](0)
    write_stream, write_receive = create_context_streams[SessionMessage](0)

    buffered_input = bytearray()

    async def read_input() -> None:
        async with read_send:
            while True:
                raw_line, oversized = await _read_bounded_line(stdin_reader, buffered_input)
                if oversized:
                    await write_stream.send(
                        SessionMessage(
                            types.JSONRPCError(
                                jsonrpc="2.0",
                                id=None,
                                error=types.ErrorData(
                                    code=-32700, message="invalid JSON-RPC request"
                                ),
                            )
                        )
                    )
                    continue
                if not raw_line:
                    return
                line = ""
                try:
                    line = raw_line.decode("utf-8")
                    value = loads_object(line)
                    message = types.jsonrpc_message_adapter.validate_python(value, by_name=False)
                    await read_send.send(SessionMessage(message))
                except (UnicodeDecodeError, ValueError, TypeError, ValidationError):
                    error = types.JSONRPCError(
                        jsonrpc="2.0",
                        id=_request_id_hint(line),
                        error=types.ErrorData(code=-32700, message="invalid JSON-RPC request"),
                    )
                    await write_stream.send(SessionMessage(error))

    async def write_output() -> None:
        async with write_receive:
            async for session_message in write_receive:
                await stdout.write(
                    (session_message.message.model_dump_json(by_alias=True, exclude_unset=True)
                    + "\n").encode("utf-8")
                )
                await stdout.flush()

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(read_input)
        task_group.start_soon(write_output)
        try:
            yield read_stream, write_stream
        finally:
            task_group.cancel_scope.cancel()
            if stdin_transport is not None:
                stdin_transport.close()


class _WindowsInput:
    async def read(self, size: int) -> bytes:
        return await anyio.to_thread.run_sync(sys.stdin.buffer.read1, size, abandon_on_cancel=True)


async def _read_bounded_line(
    reader: asyncio.StreamReader | _WindowsInput, buffered: bytearray
) -> tuple[bytes | None, bool]:
    """Read one newline-delimited frame while retaining coalesced follow-up bytes."""

    oversized = False
    while True:
        newline = buffered.find(b"\n")
        if newline >= 0:
            length = newline + 1
            line = bytes(buffered[:length])
            del buffered[:length]
            if oversized or length > _MAX_STDIO_LINE_BYTES:
                return None, True
            return line, False
        if len(buffered) > _MAX_STDIO_LINE_BYTES:
            oversized = True
            buffered.clear()
        chunk = await reader.read(64 * 1024)
        if not chunk:
            if not buffered:
                return None, oversized
            line = bytes(buffered)
            buffered.clear()
            if oversized or len(line) > _MAX_STDIO_LINE_BYTES:
                return None, True
            return line, False
        buffered.extend(chunk)


def _request_id_hint(line: str) -> int | str | None:
    try:
        value = json.loads(line)
    except (TypeError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    request_id = value.get("id")
    if isinstance(request_id, bool) or not isinstance(request_id, (int, str)):
        return None
    return request_id


def _schema(properties: dict[str, JsonObject], required: list[str]) -> JsonObject:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _success_result(result: JsonObject) -> CallToolResult:
    from mcp.types import CallToolResult, TextContent

    text = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    reply = CallToolResult(
        content=[TextContent(type="text", text=text)], structured_content=result
    )
    if len(reply.model_dump_json(by_alias=True, exclude_unset=True).encode("utf-8")) > _MAX_REPLY_BYTES:
        return _error_result("adapter reply exceeds the 16 KiB limit")
    return reply


def _error_result(message: str) -> CallToolResult:
    from mcp.types import CallToolResult, TextContent

    return CallToolResult(content=[TextContent(type="text", text=message)], is_error=True)
