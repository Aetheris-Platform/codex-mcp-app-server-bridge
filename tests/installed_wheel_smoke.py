"""Offline bridge check from outside source checkouts, for wheel or source launch."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

SHORT_TEMP_ROOT = Path("/tmp").resolve()  # noqa: S108 - private mkdtemp children


async def _rpc(
    process: asyncio.subprocess.Process, request_id: int, method: str, params: dict
) -> dict:
    assert process.stdin is not None
    assert process.stdout is not None
    request = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    process.stdin.write(json.dumps(request, separators=(",", ":")).encode() + b"\n")
    await process.stdin.drain()
    reply = json.loads(await asyncio.wait_for(process.stdout.readline(), timeout=5))
    assert reply["id"] == request_id, reply
    assert "error" not in reply, reply
    return reply["result"]


async def _tool(
    process: asyncio.subprocess.Process, request_id: int, name: str, args: dict
) -> dict:
    reply = await _rpc(process, request_id, "tools/call", {"name": name, "arguments": args})
    assert reply.get("isError") is not True, reply
    return json.loads(reply["content"][0]["text"])


def _assert_source_guardian(source_root: Path, socket: Path) -> None:
    guardian = source_root / "codex_app_server_adapter" / "guardian.py"
    if sys.platform == "linux":
        commands = []
        for path in Path("/proc").glob("[0-9]*/cmdline"):
            try:
                commands.append(path.read_bytes().replace(b"\0", b" ").decode("utf-8", errors="replace"))
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue
    else:
        commands = subprocess.check_output(  # noqa: S603 - read-only process inventory
            ["/bin/ps", "-axww", "-o", "command="], text=True
        ).splitlines()
    assert any(str(guardian) in line and f"--socket {socket}" in line for line in commands)


async def _case(
    launch: tuple[str, ...], fixture: Path, *, hold: bool, source_root: Path | None
) -> None:
    with tempfile.TemporaryDirectory(prefix="cb-installed-", dir=SHORT_TEMP_ROOT) as temp:
        root = Path(temp)
        runtime = root / "native-fixture"
        source = fixture.read_text(encoding="utf-8")  # noqa: ASYNC240 - disposable fixture setup
        runtime.write_text(
            f"#!{sys.executable}\n" + source, encoding="utf-8"
        )  # noqa: ASYNC240 - disposable fixture setup
        runtime.chmod(0o700)
        private = root / "private"
        worktree = root / "worktree"
        data = root / "data"
        for directory in (private, worktree, data):
            directory.mkdir(mode=0o700)
        socket = private / "app.sock"
        profile = root / "profile.json"
        profile.write_text(
            json.dumps(
                {
                    "runtime_path": str(runtime),
                    "runtime_version": "bridge-native-fixture/1",
                    "runtime_sha256": hashlib.sha256(runtime.read_bytes()).hexdigest(),
                    "socket_path": str(socket),
                    "allowed_worktree_roots": [str(worktree)],
                    "allowed_models": ["gpt-5.6-luna"],
                    "allowed_sandboxes": ["read-only"],
                    "data_root": str(data),
                }
            ),
            encoding="utf-8",
        )
        environment = dict(os.environ)
        environment["CODEX_HOME"] = str(root / "codex-home")
        request_log = root / "requests.jsonl"
        environment["BRIDGE_FIXTURE_REQUEST_LOG"] = str(request_log)
        if source_root is None:
            environment.pop("PYTHONPATH", None)
        else:
            environment["PYTHONPATH"] = str(source_root)
            environment["PIP_NO_INDEX"] = "1"
            environment["UV_OFFLINE"] = "1"
        environment["BRIDGE_FIXTURE_HOLD_TURN"] = "1" if hold else "0"
        environment["BRIDGE_FIXTURE_LARGE"] = "0" if hold else "1"
        process = await asyncio.create_subprocess_exec(
            *launch,
            "--profile",
            str(profile),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=root,
            env=environment,
        )
        try:
            initialized = await _rpc(
                process,
                1,
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "installed-wheel-smoke", "version": "1"},
                },
            )
            assert initialized["serverInfo"]["name"] == "codex-app-server"
            assert initialized["serverInfo"]["version"] == "1.0.0"
            tools = await _rpc(process, 2, "tools/list", {})
            assert {item["name"] for item in tools["tools"]} == {
                "codex_start",
                "codex_status",
                "codex_result",
                "codex_cancel",
            }
            request = {
                "request_id": str(uuid4()),
                "prompt": "offline fixture only",
                "cwd": str(worktree),
                "model": "gpt-5.6-luna",
                "sandbox": "read-only",
                "deadline_utc": (datetime.now(UTC) + timedelta(minutes=2)).isoformat(),
            }
            started = await _tool(process, 3, "codex_start", request)
            replay = await _tool(process, 4, "codex_start", request)
            assert replay["task_id"] == started["task_id"]
            task_id = started["task_id"]
            status: dict = {}
            for request_id in range(5, 205):
                status = await _tool(
                    process,
                    request_id,
                    "codex_status",
                    {
                        "task_id": task_id,
                        "wait_seconds": 1,
                        "cursor": status.get("cursor", 0),
                    },
                )
                if status["state"] in {"completed", "cancelled", "failed", "unknown"}:
                    break
                if hold and status.get("turn_submitted") is True:
                    break
                await asyncio.sleep(0.02)
            if hold:
                assert status.get("turn_submitted") is True, status
                if source_root is not None:
                    _assert_source_guardian(source_root, socket)
                cancelled = await _tool(process, 205, "codex_cancel", {"task_id": task_id})
                assert cancelled["state"] in {"cancel-requested", "cancelled"}, cancelled
                for request_id in range(206, 216):
                    cancelled = await _tool(process, request_id, "codex_status", {
                        "task_id": task_id, "wait_seconds": 1,
                        "cursor": cancelled.get("cursor", 0),
                    })
                    if cancelled["state"] == "cancelled":
                        break
                assert cancelled["state"] == "cancelled", cancelled
                assert cancelled["reason"] == "provider_interrupted", cancelled
            else:
                assert status["state"] == "completed", status
                chunks: list[bytes] = []
                offset = 0
                for request_id in range(205, 905):
                    page = await _tool(
                        process,
                        request_id,
                        "codex_result",
                        {
                            "task_id": task_id,
                            "offset_bytes": offset,
                            "limit_bytes": 4096,
                        },
                    )
                    chunks.append(base64.b64decode(page["chunk"]))
                    if page["next_offset"] is None:
                        break
                    offset = page["next_offset"]
                content = b"".join(chunks)
                assert len(content) > 2 * 1024 * 1024
                assert hashlib.sha256(content).hexdigest() == page["output_sha256"]
                assert page["complete"] is True
                await _tool(process, 906, "codex_cancel", {"task_id": task_id})
        finally:
            if process.stdin is not None:
                process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), timeout=12)
            except TimeoutError:
                process.kill()
                await process.wait()
            assert process.returncode == 0, await process.stderr.read() if process.stderr else None
            assert not socket.exists()
        messages = [json.loads(line) for line in request_log.read_text().splitlines()]
        assert sum(m.get("method") == "turn/start" for m in messages) == 1
        assert sum(m.get("method") == "thread/start" for m in messages) == 1
        initialize = next(m["params"]["clientInfo"] for m in messages if m.get("method") == "initialize")
        thread = next(m["params"] for m in messages if m.get("method") == "thread/start")
        assert initialize["name"] == thread["threadSource"] == "codex-mcp-app-server-bridge"


def main() -> None:
    parser = argparse.ArgumentParser()
    route = parser.add_mutually_exclusive_group(required=True)
    route.add_argument("--entrypoint", type=Path)
    route.add_argument("--source-root", type=Path)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--sandbox-profile", type=Path, help="Optional macOS sandbox for adapter and owned children only")
    args = parser.parse_args()
    import importlib.metadata as metadata

    import codex_app_server_adapter
    from codex_app_server_adapter.errors import ValidationError
    from codex_app_server_adapter.models import parse_start
    from codex_app_server_adapter.profile import OperatorProfile
    from codex_app_server_adapter.store import _validate_record

    source_root: Path | None = None
    if args.source_root is None:
        assert Path(codex_app_server_adapter.__file__).is_relative_to(Path(sys.prefix))
        launch = (str(args.entrypoint),)
    else:
        source_root = args.source_root.resolve(strict=True)
        package_root = source_root / "codex_app_server_adapter"
        assert Path(codex_app_server_adapter.__file__).resolve() == package_root / "__init__.py"
        from codex_app_server_adapter import guardian, runtime

        assert Path(runtime.__file__).resolve() == package_root / "runtime.py"
        assert Path(guardian.__file__).resolve() == package_root / "guardian.py"
        import sysconfig

        installed = metadata.distributions(path=[sysconfig.get_path("purelib")])
        assert all(
            distribution.metadata["Name"].replace("_", "-") != "codex-mcp-app-server-bridge"
            for distribution in installed
        ), "source smoke must not have the bridge wheel installed"
        launch = (sys.executable, "-m", "codex_app_server_adapter")

    if args.sandbox_profile is not None:
        launch = ("/usr/bin/sandbox-exec", "-f", str(args.sandbox_profile.resolve(strict=True)), *launch)

    assert all(callable(item) for item in (ValidationError, parse_start, OperatorProfile, _validate_record))
    assert Path.cwd().is_relative_to(SHORT_TEMP_ROOT)
    asyncio.run(_case(launch, args.fixture, hold=False, source_root=source_root))
    asyncio.run(_case(launch, args.fixture, hold=True, source_root=source_root))
    route_name = "source checkout" if source_root is not None else "installed wheel"
    print(f"{route_name} smoke passed: stdio, replay, status, pages, cancel, cleanup")  # noqa: T201 - CLI receipt


if __name__ == "__main__":
    main()
