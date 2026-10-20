"""Native Windows acceptance: real ACLs, locks, jobs and stdio MCP.

These tests are explicitly excluded on Unix, whose full existing suite remains
required. They do not execute the installed Codex executable or a paid turn.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import subprocess
import sys
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="native Windows kernel required")


def test_private_directory_rejects_world_acl_and_junction(tmp_path: Path) -> None:
    import _winapi

    import win32security

    from codex_app_server_adapter.errors import ValidationError
    from codex_app_server_adapter.platform_files import (
        is_link,
        make_private_directory,
        require_private_windows,
    )

    private = tmp_path / "private"
    make_private_directory(private)
    require_private_windows(private, "fixture")
    alias = tmp_path / "junction"
    _winapi.CreateJunction(str(private), str(alias))
    try:
        assert is_link(alias)
    finally:
        alias.rmdir()
    descriptor = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        "D:P(A;OICI;FA;;;WD)", win32security.SDDL_REVISION_1,
    )
    win32security.SetNamedSecurityInfo(str(private), win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None, None, descriptor.GetSecurityDescriptorDacl(), None)
    with pytest.raises(ValidationError, match="outside"):
        require_private_windows(private, "fixture")


def test_root_lock_is_exclusive_and_reusable(tmp_path: Path) -> None:
    from codex_app_server_adapter.errors import ValidationError
    from codex_app_server_adapter.ownership import DataRootOwnership

    root = tmp_path / "data"
    first = DataRootOwnership(root)
    other = DataRootOwnership(tmp_path / "other")
    try:
        with pytest.raises(ValidationError, match="already owned"):
            DataRootOwnership(root)
    finally:
        first.close()
        other.close()
    assert (root / ".adapter-owner.lock").exists()
    DataRootOwnership(root).close()


def test_job_cleans_descendants_on_owner_death(tmp_path: Path) -> None:
    import win32api
    import win32con
    import win32event

    owner_code = (
        "from codex_app_server_adapter.windows_process import JobProcess; "
        "import sys,time; "
        "job=JobProcess([sys.executable,'-c',sys.argv[1]]); "
        "print(job.pid,flush=True); time.sleep(120)"
    )
    pid_file = tmp_path / "descendant.pid"
    child_code = (
        "import subprocess,sys,time; from pathlib import Path; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)']); "
        f"Path({str(pid_file)!r}).write_text(str(p.pid)); time.sleep(120)"
    )
    owner = subprocess.Popen(  # noqa: S603 - owned fixture code
        [sys.executable, "-c", owner_code, child_code], stdout=subprocess.PIPE, text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")})
    handles = []
    try:
        deadline = time.monotonic() + 10
        while not pid_file.exists() and time.monotonic() < deadline:
            assert owner.poll() is None
            time.sleep(0.05)
        assert pid_file.exists()
        child_pid = int(owner.stdout.readline())
        for pid in (child_pid, int(pid_file.read_text())):
            handles.append(win32api.OpenProcess(win32con.SYNCHRONIZE, False, pid))
        owner.kill()
        owner.wait(timeout=5)
        for handle in handles:
            assert win32event.WaitForSingleObject(handle, 10000) == win32con.WAIT_OBJECT_0
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=5)
        for handle in handles:
            handle.Close()


def test_actual_windows_mcp_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from windows_fixture import build_fixture

    from codex_app_server_adapter.platform_files import make_private_directory

    executable = build_fixture(tmp_path)
    for name in ("worktree", "history", "home"):
        make_private_directory(tmp_path / name)
    log = tmp_path / "native.jsonl"
    monkeypatch.setenv("BRIDGE_FIXTURE_HISTORY_DIR", str(tmp_path / "history"))
    monkeypatch.setenv("BRIDGE_FIXTURE_REQUEST_LOG", str(log))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).parents[1] / "src"))
    profile = {"runtime_path": str(executable), "runtime_version": "bridge-native-fixture/1",
               "runtime_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
               "socket_path": None, "allowed_worktree_roots": [str(tmp_path / "worktree")],
               "allowed_models": ["fixture"], "allowed_sandboxes": ["read-only"],
               "data_root": str(tmp_path / "data")}
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    asyncio.run(_mcp_case(tmp_path, profile_path, log))


async def _mcp_case(root: Path, profile: Path, log: Path) -> None:
    import pywintypes
    import win32api
    import win32con
    import win32event

    processes = []
    request_number = 0

    async def rpc(process, method, params):
        nonlocal request_number
        request_number += 1
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": request_number,
            "method": method, "params": params}).encode() + b"\n")
        await process.stdin.drain()
        reply = json.loads(await asyncio.wait_for(process.stdout.readline(), 15))
        assert reply["id"] == request_number, reply
        assert "error" not in reply, reply
        return reply["result"]

    async def tool(process, name, params, *, error=False):
        reply = await rpc(process, "tools/call", {"name": name, "arguments": params})
        assert bool(reply.get("isError")) is error, reply
        return reply if error else json.loads(reply["content"][0]["text"])

    async def launch(path=profile):
        process = await asyncio.create_subprocess_exec(sys.executable, "-m", "codex_app_server_adapter",
            "--profile", str(path), cwd=root, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        processes.append(process)
        await rpc(process, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "platform-acceptance", "version": "1"}})
        tools = await rpc(process, "tools/list", {})
        assert {t["name"] for t in tools["tools"]} == {"codex_start", "codex_status", "codex_result", "codex_cancel"}
        return process

    async def stop(process):
        process.stdin.close()
        assert await asyncio.wait_for(process.wait(), 20) == 0, await process.stderr.read()

    async def native_handles():
        messages = [json.loads(line) for line in (await asyncio.to_thread(log.read_text, encoding="utf-8")).splitlines()]
        handles = []
        for pid in {m["fixture_pid"] for m in messages}:
            with suppress(pywintypes.error):
                handles.append(win32api.OpenProcess(win32con.SYNCHRONIZE, False, pid))
        return handles

    def assert_stopped(handles):
        try:
            for handle in handles:
                assert win32event.WaitForSingleObject(handle, 10000) == win32con.WAIT_OBJECT_0
        finally:
            for handle in handles:
                handle.Close()

    async def status(process, task, *, submitted=False):
        from mcp_polling import poll_status

        async def fetch(cursor):
            return await tool(process, "codex_status", {"task_id": task, "wait_seconds": 1, "cursor": cursor})

        return await poll_status(fetch, submitted=submitted)

    def request(prompt, seconds=120):
        return {"request_id": str(uuid4()), "prompt": prompt, "cwd": str(root / "worktree"),
            "model": "fixture", "sandbox": "read-only", "deadline_utc": (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()}

    try:
        process = await launch()
        await tool(process, "codex_status", {"task_id": str(uuid4())}, error=True)
        original = request("large")
        started = await tool(process, "codex_start", original)
        replay = await tool(process, "codex_start", original)
        assert replay["task_id"] == started["task_id"]
        await tool(process, "codex_start", {**original, "prompt": "conflict"}, error=True)
        completed = await status(process, started["task_id"])
        assert completed["state"] == "completed", completed
        chunks = []
        offset = 0
        while True:
            page = await tool(process, "codex_result", {"task_id": started["task_id"], "offset_bytes": offset})
            chunks.append(base64.b64decode(page["chunk"], validate=True))
            if page["next_offset"] is None:
                break
            offset = page["next_offset"]
        content = b"".join(chunks)
        assert len(content) == page["total_bytes"] > 2 * 1024 * 1024
        assert hashlib.sha256(content).hexdigest() == page["output_sha256"]
        assert "fixture ✓" in json.dumps(json.loads(content), ensure_ascii=False)
        held = await tool(process, "codex_start", request("hold"))
        assert (await status(process, held["task_id"], submitted=True))["turn_submitted"]
        cancelled = await tool(process, "codex_cancel", {"task_id": held["task_id"]})
        assert cancelled["state"] in {"cancel-requested", "cancelled"}
        cancelled = await status(process, held["task_id"])
        assert (cancelled["state"], cancelled["reason"]) == ("cancelled", "provider_interrupted")
        deadline_request = request("hold", 2)
        expired = await tool(process, "codex_start", deadline_request)
        expired_status = await status(process, expired["task_id"])
        assert expired_status["deadline_utc"] == deadline_request["deadline_utc"]
        assert expired_status["stop_intent"] == "expiry", expired_status
        assert (expired_status["state"], expired_status["reason"]) == ("failed", "deadline_exceeded"), expired_status
        # A competing profile on this root must lose before invoking its runtime.
        before_log = (await asyncio.to_thread(log.read_bytes))
        loser = await asyncio.create_subprocess_exec(sys.executable, "-m", "codex_app_server_adapter",
            "--profile", str(profile), cwd=root, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(loser.communicate(), 10)
        assert loser.returncode == 2
        assert out == b""
        assert b"already owned" in err
        assert (await asyncio.to_thread(log.read_bytes)) == before_log
        handles = await native_handles()
        await stop(process)
        assert_stopped(handles)
        before_log = (await asyncio.to_thread(log.read_bytes))
        process = await launch()
        assert (await tool(process, "codex_status", {"task_id": held["task_id"]}))["state"] == "cancelled"
        assert (await tool(process, "codex_start", original))["task_id"] == started["task_id"]
        assert (await tool(process, "codex_result", {"task_id": started["task_id"]}))["output_sha256"] == page["output_sha256"]
        assert (await asyncio.to_thread(log.read_bytes)) == before_log
        # A process-death/restart path must not resubmit a saved request. An
        # independent data root remains usable throughout the first death.
        crashing_request = request("hold")
        crashing = await tool(process, "codex_start", crashing_request)
        assert (await status(process, crashing["task_id"], submitted=True))["turn_submitted"]
        handles = await native_handles()
        other_profile = root / "other-profile.json"
        definition = json.loads(await asyncio.to_thread(profile.read_text, encoding="utf-8"))
        definition["data_root"] = str(root / "other-data")
        await asyncio.to_thread(other_profile.write_text, json.dumps(definition), encoding="utf-8")
        bystander = await launch(other_profile)
        process.kill()
        await process.wait()
        assert_stopped(handles)
        other_task = await tool(bystander, "codex_start", request("small"))
        assert (await status(bystander, other_task["task_id"]))["state"] == "completed"
        process = await launch()
        recovered = await tool(process, "codex_status", {"task_id": crashing["task_id"]})
        assert recovered["state"] == "unknown", recovered
        assert recovered["reason"] == "adapter_restarted", recovered
        assert (await tool(process, "codex_start", crashing_request))["task_id"] == crashing["task_id"]
        handles = await native_handles()
        await stop(process)
        await stop(bystander)
        assert_stopped(handles)
        messages = [json.loads(line) for line in (await asyncio.to_thread(log.read_text, encoding="utf-8")).splitlines()]
        assert sum(m.get("method") == "turn/start" for m in messages) == 5
    finally:
        for process in processes:
            if process.returncode is None:
                process.kill()
                await process.wait()
