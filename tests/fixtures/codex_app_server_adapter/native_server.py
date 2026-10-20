"""Deterministic local native app-server fixture; never calls a provider."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

from websockets.asyncio.server import ServerConnection, unix_serve

VERSION = "bridge-native-fixture/1"


def expected_physical_socket_path(alias: Path) -> Path:
    """Derive the alias target exactly as Codex CLI 0.156.0 does, independently of the adapter.

    This mirrors `protected_socket_path` over `shared_daemon_socket_directory` in
    `codex-rs/app-server-transport/src/transport/unix_socket.rs` and `codex-rs/uds/src/
    daemon_directory.rs`.  It is deliberately a second implementation: the adapter must agree
    with the runtime, not with itself.
    """

    shared = Path(os.path.realpath("/tmp")) / f"codex-daemon-{os.geteuid()}"
    digest = hashlib.sha256(
        os.fsencode(os.path.join(os.path.realpath(alias.parent), alias.name))
    ).hexdigest()
    return shared / digest


def _record_request(path: str, message: dict) -> None:
    with Path(path).open("a", encoding="utf-8") as log:
        log.write(json.dumps(message) + "\n")


async def serve(socket_path: Path) -> None:
    output_text = (
        "x" * (2 * 1024 * 1024)
        if os.environ.get("BRIDGE_FIXTURE_LARGE") == "1"
        else "fixture ✓"
    )
    history = {
        "thread": {
            "id": "fixture-thread",
            **({"path": os.environ["BRIDGE_FIXTURE_JOURNAL_PATH"]}
               if os.environ.get("BRIDGE_FIXTURE_JOURNAL_PATH")
               else {}),
            "turns": [{
                "id": "fixture-turn",
                "status": "completed",
                "itemsView": "full",
                "items": [{
                    "id": "fixture-item",
                    "type": "agentMessage",
                    "phase": "final_answer",
                    "text": output_text,
                }],
            }],
        }
    }

    async def handler(connection: ServerConnection) -> None:
        async for raw in connection:
            message = json.loads(raw)
            log_path = os.environ.get("BRIDGE_FIXTURE_REQUEST_LOG")
            if log_path:
                await asyncio.to_thread(_record_request, log_path, message)
            request_id = message.get("id")
            if request_id is None:
                continue
            method = message.get("method")
            if method == "initialize":
                result = {"serverInfo": {"name": "fixture", "version": VERSION}}
            elif method == "thread/start":
                params = message["params"]
                sandbox = {"type": "readOnly", "networkAccess": False}
                if params.get("sandbox") == "workspace-write":
                    sandbox = {
                        "type": "workspaceWrite",
                        "writableRoots": [],
                        "networkAccess": False,
                        "excludeTmpdirEnvVar": True,
                        "excludeSlashTmp": True,
                    }
                result = {
                    "thread": {"id": "fixture-thread"},
                    "cwd": params["cwd"],
                    "model": params["model"],
                    "modelProvider": "openai",
                    "approvalPolicy": "never",
                    "approvalsReviewer": "user",
                    "sandbox": sandbox,
                }
            elif method == "turn/start":
                await connection.send(json.dumps({"id": request_id, "result": {"turn": {"id": "fixture-turn"}}}))
                if os.environ.get("BRIDGE_FIXTURE_HOLD_TURN") == "1":
                    continue
                await connection.send(json.dumps({
                    "method": "item/completed",
                    "params": {
                        "threadId": "fixture-thread",
                        "turnId": "fixture-turn",
                        "completedAtMs": 1,
                        "item": history["thread"]["turns"][0]["items"][0],
                    },
                }))
                await connection.send(json.dumps({
                    "method": "turn/completed",
                    "params": {"threadId": "fixture-thread", "turn": {"id": "fixture-turn", "status": "completed"}},
                }))
                continue
            elif method == "thread/read":
                result = history
            elif method == "turn/interrupt":
                await connection.send(json.dumps({"id": request_id, "result": {}}))
                if os.environ.get("BRIDGE_FIXTURE_HOLD_TURN") == "1":
                    await connection.send(json.dumps({
                        "method": "turn/completed",
                        "params": {
                            "threadId": "fixture-thread",
                            "turn": {"id": "fixture-turn", "status": "interrupted"},
                        },
                    }))
                continue
            else:
                result = {}
            await connection.send(json.dumps({"id": request_id, "result": result}))

    if os.environ.get("BRIDGE_FIXTURE_ALIAS") == "1":
        # Codex CLI 0.156.0 layout: bind the derived physical socket, chmod it, then publish
        # the requested path as a symlink.  Unlike the real runtime this fixture is killed by
        # signal and never removes the pair, which leaves the adapter's own cleanup observable.
        physical = expected_physical_socket_path(socket_path)
        physical.parent.mkdir(mode=0o700, exist_ok=True)
        server = await unix_serve(handler, path=str(physical), compression=None)
        os.chmod(physical, 0o600)
        os.symlink(physical, socket_path)
    else:
        server = await unix_serve(handler, path=str(socket_path), compression=None)
        os.chmod(socket_path, 0o600)
    pid_path = os.environ.get("BRIDGE_FIXTURE_PID_PATH")
    if pid_path:
        Path(pid_path).write_text(str(os.getpid()), encoding="utf-8")
    await asyncio.Future()
    server.close()
    await server.wait_closed()


def main() -> None:
    if sys.argv[1:] == ["--version"]:
        print(VERSION)  # noqa: T201 - --version is the fixture's required stdout contract.
        return
    if len(sys.argv) == 4 and sys.argv[1:3] == ["app-server", "--listen"]:
        asyncio.run(serve(Path(sys.argv[3].removeprefix("unix://"))))
        return
    raise SystemExit("unsupported fixture invocation")


if __name__ == "__main__":
    main()
