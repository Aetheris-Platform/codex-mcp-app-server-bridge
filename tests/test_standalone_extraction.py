"""Focused checks for the standalone identity and package boundary."""

from __future__ import annotations

import ast
import asyncio
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from test_codex_app_server_adapter import FakeClient, FakeTransport, _request, _service

from codex_app_server_adapter.protocol import JsonRpcClient


@pytest.mark.parametrize(
    ("kind", "message"),
    [("missing", "profile not found"), ("directory", "cannot read profile"),
     ("invalid-utf8", "profile must be UTF-8 text")],
)
def test_cli_reports_unreadable_profile_without_traceback_or_stdout(
    tmp_path: Path, kind: str, message: str,
) -> None:
    profile = tmp_path / "profile.json"
    if kind == "directory":
        profile.mkdir()
    elif kind == "invalid-utf8":
        profile.write_bytes(b"\xff")
    root = Path(__file__).parents[1]
    result = subprocess.run(  # noqa: S603 - local module, no runtime/profile creation
        [sys.executable, "-m", "codex_app_server_adapter", "--profile", str(profile)],
        cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(root / "src")},
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert message in result.stderr
    assert str(profile) in result.stderr
    assert "Traceback" not in result.stderr
    assert set(tmp_path.iterdir()) == ({profile} if kind != "missing" else set())


def test_native_identity_is_truthful_and_matches_thread_source(tmp_path: Path) -> None:
    async def exercise() -> None:
        transport = FakeTransport()
        protocol = JsonRpcClient(transport)
        client = FakeClient()
        service = _service(tmp_path, client)
        try:
            await protocol.connect()
            initialize = json.loads(transport.sent[0])["params"]["clientInfo"]
            assert initialize == {
                "name": "codex-mcp-app-server-bridge",
                "title": "Codex MCP app-server bridge",
                "version": "1",
            }
            started = await service.start(_request(tmp_path / "worktree"))
            await service.status({"task_id": started["task_id"], "wait_seconds": 2})
            thread = next(params for method, params in client.calls if method == "thread/start")
            assert thread["threadSource"] == initialize["name"]
            assert "aetheris" not in thread["threadSource"]
            assert thread["modelProvider"] == "openai"
            assert thread["approvalPolicy"] == "never"
        finally:
            await service.shutdown()
            await protocol.close()

    asyncio.run(exercise())


def test_package_is_self_contained_and_has_neutral_entrypoint() -> None:
    root = Path(__file__).parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    assert project["name"] == "codex-mcp-app-server-bridge"
    assert project["scripts"] == {
        "codex-mcp-app-server-bridge": "codex_app_server_adapter.__main__:main",
    }
    assert project["dependencies"] == [
        "mcp==2.2.0", "websockets==17.1", "pywin32==312; sys_platform == 'win32'",
    ]
    forbidden = {"workflow_core", "devtools", "aetheris", "aetherion"}
    for path in (root / "src").rglob("*.py"):
        source = path.read_text()
        assert "/Users/" not in source
        assert "aetheris-codex-app-server-adapter" not in source
        for node in ast.walk(ast.parse(source)):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module]
            assert not any(module.split(".")[0] in forbidden for module in modules)
