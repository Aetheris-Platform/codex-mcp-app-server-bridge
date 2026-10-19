"""Offline MCP acceptance initiated by Windows through wsl.exe (stdlib only).

--local is a Linux development check of this harness, never WSL qualification.
All assertions are acceptance targets. No real Codex executable is selected.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import platform
import queue
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from uuid import uuid4

TERMINAL = {"completed", "cancelled", "failed", "unknown"}


def encoded(value: dict) -> str:
    return base64.b64encode(json.dumps(value, ensure_ascii=True).encode()).decode()


def command(config: dict, payload: dict) -> list[str]:
    helper = str(PurePosixPath(config["linux_checkout"]) / "tests/wsl_fixture.py")
    inner = ["/usr/bin/env", "PYTHONDONTWRITEBYTECODE=1", config["linux_python"], helper, encoded(payload)]
    if config["local"]:
        return inner
    return [config["wsl_exe"], "--distribution", config["distribution"], "--user", config["linux_user"],
            "--exec", *inner]


class Session:
    def __init__(self, config: dict, root: str, case: str, receipts: Path):
        self.case = case
        self.serial = 0
        self.lines: queue.Queue = queue.Queue()
        self.stderr = (receipts / f"{case}-{uuid4().hex}.stderr.log").open("wb")
        self.process = subprocess.Popen(  # noqa: S603 - explicit argv, owned offline fixture
            command(config, {"action": "launch", "root": root, "case": case, "launch_id": uuid4().hex}),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr, cwd=receipts)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            while line := self.process.stdout.readline():
                self.lines.put(line)
        finally:
            self.lines.put(None)

    def rpc(self, method: str, params: dict, timeout: float = 20) -> dict:
        self.serial += 1
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.serial,
                                            "method": method, "params": params}, ensure_ascii=False).encode() + b"\n")
        self.process.stdin.flush()
        line = self.lines.get(timeout=timeout)
        assert line is not None, "MCP stdout closed before reply; inspect stderr receipt"
        reply = json.loads(line)
        assert reply.get("jsonrpc") == "2.0", reply
        assert reply.get("id") == self.serial, reply
        assert "error" not in reply, reply
        return reply["result"]

    def initialize(self):
        self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                               "clientInfo": {"name": "wsl-forwarding-acceptance", "version": "1"}})
        self.process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        self.process.stdin.flush()
        tools = self.rpc("tools/list", {})
        assert {item["name"] for item in tools["tools"]} == {
            "codex_start", "codex_status", "codex_result", "codex_cancel"}

    def tool(self, name: str, arguments: dict, *, error: bool = False) -> dict:
        reply = self.rpc("tools/call", {"name": name, "arguments": arguments})
        assert bool(reply.get("isError")) is error, reply
        return reply if error else json.loads(reply["content"][0]["text"])

    def status(self, task: str, *, submitted: bool = False) -> dict:
        value = {}
        end = time.monotonic() + 30
        while time.monotonic() < end:
            before = time.monotonic()
            value = self.tool("codex_status", {"task_id": task, "wait_seconds": 1,
                                              "cursor": value.get("cursor", 0)})
            assert time.monotonic() - before < 5, "bounded status exceeded five seconds"
            if value["state"] in TERMINAL or (submitted and value["turn_submitted"]):
                return value
            # After the deadline status waits return immediately; allow the existing
            # native event reader to observe the interruption without a busy-poll race.
            time.sleep(0.02)
        raise AssertionError("task did not reach expected state")

    def stop(self):
        self.process.stdin.close()
        assert self.process.wait(timeout=20) == 0, "normal EOF shutdown failed"
        self.stderr.close()


def request(root: str, case: str, seconds: int = 120) -> dict:
    return {"request_id": str(uuid4()), "prompt": "offline fixture ✓ only; no provider",
            "cwd": str(PurePosixPath(root) / f"{case} space ✓" / "worktree"),
            "model": "fixture", "sandbox": "read-only",
            "deadline_utc": (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()}


def reconstruct(session: Session, task: str) -> dict:
    chunks, offset = [], 0
    for _ in range(700):
        page = session.tool("codex_result", {"task_id": task, "offset_bytes": offset, "limit_bytes": 4096})
        chunk = base64.b64decode(page["chunk"], validate=True)
        assert len(chunk) <= 4096
        chunks.append(chunk)
        if page["next_offset"] is None:
            break
        assert page["next_offset"] == offset + len(chunk) > offset
        offset = page["next_offset"]
    else:
        raise AssertionError("pagination failed to terminate")
    content = b"".join(chunks)
    assert page["complete"] is True
    assert len(content) == page["total_bytes"] > 2 * 1024 * 1024
    assert hashlib.sha256(content).hexdigest() == page["output_sha256"]
    assert "fixture ✓" in json.dumps(json.loads(content), ensure_ascii=False)
    try:
        json.loads(chunks[0])
    except (ValueError, UnicodeError):
        pass
    else:
        raise AssertionError("first page unexpectedly contained the entire JSON document")
    return {"bytes": len(content), "pages": len(chunks), "sha256": page["output_sha256"]}


class Run:
    def __init__(self, config: dict, receipts: Path):
        self.config, self.receipts = config, receipts
        self.root = ""
        self.sessions = []
        self.worker = None
        self.report = {"verdict": "RUNNING", "route": "local Linux harness development" if config["local"] else "Windows-to-WSL2",
                       "host": platform.platform(), "python": sys.version, "checks": {}}
        self.watchdog = threading.Timer(360, self.expire)
        self.watchdog.daemon = True

    def expire(self):
        # Hard bound on the initiating client; Linux-owned cleanup is handled by EOF/guardian.
        # Outer execution instructions retain and inspect receipts even after this failure.
        self.report["verdict"] = "FAIL"
        self.report["error"] = "360-second harness deadline exceeded; inspect owned cleanup"
        self.save()
        os._exit(124)

    def save(self):
        (self.receipts / "summary.json").write_text(json.dumps(self.report, indent=2) + "\n", encoding="utf-8")

    def control(self, action: str, **kwargs) -> dict:
        result = subprocess.run(command(self.config, {"action": action, "root": self.root, **kwargs}),  # noqa: S603
                                capture_output=True, timeout=30, cwd=self.receipts, check=False)
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        return json.loads(result.stdout)

    def launch(self, case: str) -> Session:
        session = Session(self.config, self.root, case, self.receipts)
        self.sessions.append(session)
        session.initialize()
        return session

    def inspect(self) -> dict:
        value = self.control("inspect")
        (self.receipts / "linux-state.json").write_text(json.dumps(value, indent=2), encoding="utf-8")
        return value

    def clean(self, case: str | None = None):
        end = time.monotonic() + 20
        while True:
            value = self.inspect()
            processes = value["processes"]
            if case:
                needle = str(PurePosixPath(self.root) / f"{case} space ✓") + "/"
                processes = [p for p in processes if any(arg.startswith(needle) for arg in p["argv"])]
            sockets = [v["socket_exists"] for k, v in value["cases"].items() if case is None or k == case]
            if not processes and not any(sockets):
                return value
            assert time.monotonic() < end, {"remaining_processes": processes, "sockets": sockets}
            time.sleep(0.2)

    def check(self, name: str, details=True):
        self.report["checks"][name] = details
        self.save()
        print(f"PASS: {name}", flush=True)  # noqa: T201

    def execute(self):
        self.watchdog.start()
        try:
            from wsl_fixture import provenance

            self.report["host_identity"] = provenance(Path(__file__).resolve().parents[1], self.config["expected_commit"])
            prepared = self.control("prepare", commit=self.config["expected_commit"])
            self.root = prepared["root"]
            self.report["linux"] = prepared
            self.save()
            assert prepared["identity"] == self.report["host_identity"], "Windows/Linux checkout bytes differ"
            if not self.config["local"]:
                assert "microsoft" in prepared["kernel"].lower()
                assert "wsl2" in prepared["kernel"].lower()
                assert prepared["uid"] != 0, "ordinary Linux user required for WSL acceptance"
            full = self.launch("complete")
            full.tool("codex_status", {"task_id": str(uuid4())}, error=True)
            original = request(self.root, "complete")
            task = full.tool("codex_start", original)["task_id"]
            assert full.tool("codex_start", original)["task_id"] == task
            full.tool("codex_start", {**original, "prompt": "conflict"}, error=True)
            assert full.status(task)["state"] == "completed"
            result = reconstruct(full, task)
            self.check("MCP discovery, errors, replay/conflict, bounded status and UTF-8 pagination", result)
            full.stop()
            snapshot = self.clean("complete")
            persisted = self.launch("complete")
            assert persisted.tool("codex_start", original)["task_id"] == task
            assert persisted.tool("codex_status", {"task_id": task})["state"] == "completed"
            assert persisted.tool("codex_result", {"task_id": task})["output_sha256"] == result["sha256"]
            assert self.inspect()["cases"]["complete"]["requests"] == snapshot["cases"]["complete"]["requests"]
            persisted.stop()
            self.clean("complete")
            self.check("EOF cleanup and persisted terminal reads without native traffic")

            held = self.launch("cancel")
            task = held.tool("codex_start", request(self.root, "cancel"))["task_id"]
            assert held.status(task, submitted=True)["turn_submitted"]
            owned = self.inspect()
            guardian = str(PurePosixPath(self.config["linux_checkout"]) / "src/codex_app_server_adapter/guardian.py")
            assert any(guardian in p["argv"] for p in owned["processes"])
            loser = Session(self.config, self.root, "cancel", self.receipts)
            self.sessions.append(loser)
            loser.process.stdin.close()
            assert loser.process.wait(timeout=15) == 2
            assert loser.lines.get(timeout=5) is None
            assert self.inspect()["cases"]["cancel"] == owned["cases"]["cancel"]
            cancelled = held.tool("codex_cancel", {"task_id": task})
            assert cancelled["state"] in {"cancel-requested", "cancelled"}
            cancelled = held.status(task)
            assert (cancelled["state"], cancelled["reason"]) == ("cancelled", "provider_interrupted")
            held.tool("codex_cancel", {"task_id": task})
            held.stop()
            self.clean("cancel")
            self.check("destination guardian, losing startup noninterference and confirmed cancellation")

            deadline = self.launch("deadline")
            expiring = request(self.root, "deadline", 4)
            task = deadline.tool("codex_start", expiring)["task_id"]
            expired = deadline.status(task)
            assert expired["deadline_utc"] == expiring["deadline_utc"]
            assert expired["stop_intent"] == "expiry", expired
            assert expired["state"] in {"failed", "unknown"}, expired
            deadline.stop()
            self.clean("deadline")
            self.check("deadline interruption without deadline extension", expired)

            crash = self.launch("crash")
            crashing = request(self.root, "crash")
            task = crash.tool("codex_start", crashing)["task_id"]
            assert crash.status(task, submitted=True)["turn_submitted"]
            other = self.launch("bystander")
            assert len(self.control("kill", case="crash")["killed"]) == 1
            crash.process.wait(timeout=20)
            self.clean("crash")
            other_task = other.tool("codex_start", request(self.root, "bystander"))["task_id"]
            assert other.status(other_task)["state"] == "completed"
            successor = self.launch("crash")
            recovered = successor.tool("codex_status", {"task_id": task})
            assert (recovered["state"], recovered["reason"]) == ("unknown", "adapter_restarted"), recovered
            assert successor.tool("codex_start", crashing)["task_id"] == task
            successor.stop()
            other.stop()
            self.clean("crash")
            self.clean("bystander")
            self.check("adapter death, independent root and restart replay without resubmission")

            worker_args = {"config": self.config, "root": self.root, "receipts": str(self.receipts)}
            with (self.receipts / "host-worker.stderr.log").open("wb") as err:
                self.worker = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--worker", encoded(worker_args)],  # noqa: S603
                                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err, cwd=self.receipts)
                ready: queue.Queue = queue.Queue()
                threading.Thread(target=lambda: ready.put(self.worker.stdout.readline()), daemon=True).start()
                message = json.loads(ready.get(timeout=30))
                assert message["submitted"] is True
                self.worker.kill()  # exact child handle; deliberately kills the Windows initiating client
                self.worker.wait(timeout=10)
                self.clean("hostdeath")
            self.check("abrupt initiating-client death closes forwarded stdin and cleans owned children")

            final = self.clean()
            for case, info in final["cases"].items():
                messages = info["requests"]
                assert sum(m.get("method") == "turn/start" for m in messages) == 1, case
                assert sum(m.get("method") == "turn/interrupt" for m in messages) == (1 if case in ("cancel", "deadline", "hostdeath") else 0), case
                initialize = next(m["params"]["clientInfo"] for m in messages if m.get("method") == "initialize")
                assert initialize["name"] == "codex-mcp-app-server-bridge"
                assert all(m["params"]["threadSource"] == "codex-mcp-app-server-bridge" for m in messages if m.get("method") == "thread/start")
            after = self.control("provenance", commit=self.config["expected_commit"])
            assert after == prepared["identity"]
            self.check("six fixture turns only, truthful identity, unchanged checkout and no surviving owned processes")
            self.report["verdict"] = "PASS"
        except BaseException as exc:
            self.report["verdict"] = "FAIL"
            self.report["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self.watchdog.cancel()
            # Failure cleanup is separate from acceptance. It cannot turn a failed check into a pass.
            if self.worker and self.worker.poll() is None:
                self.worker.kill()
                self.worker.wait(timeout=10)
            for session in self.sessions:
                if session.process.stdin and not session.process.stdin.closed:
                    session.process.stdin.close()
            if self.root:
                try:
                    for case in ("complete", "cancel", "deadline", "crash", "bystander", "hostdeath"):
                        self.control("kill", case=case)
                    self.report["cleanup"] = self.clean()
                except Exception as exc:  # noqa: BLE001 - retain cleanup failure alongside original failure
                    self.report["cleanup_error"] = str(exc)
                    self.report["verdict"] = "FAIL"
            for session in self.sessions:
                try:
                    session.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    session.process.kill()  # exact owned launcher handle only
                    session.process.wait(timeout=5)
                session.stderr.close()
            self.save()


def main():
    assert not sys.flags.optimize, "acceptance assertions must be enabled"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    parser.add_argument("--local", action="store_true", help="Linux-only harness development, NOT WSL acceptance")
    parser.add_argument("--distribution")
    parser.add_argument("--linux-user")
    parser.add_argument("--linux-checkout")
    parser.add_argument("--linux-python")
    parser.add_argument("--expected-commit")
    parser.add_argument("--receipts", type=Path)
    args = parser.parse_args()
    if args.worker:
        info = json.loads(base64.b64decode(args.worker))
        session = Session(info["config"], info["root"], "hostdeath", Path(info["receipts"]))
        session.initialize()
        task = session.tool("codex_start", request(info["root"], "hostdeath"))["task_id"]
        assert session.status(task, submitted=True)["turn_submitted"]
        print(json.dumps({"submitted": True, "task_id": task}), flush=True)  # noqa: T201
        sys.stdin.buffer.read(1)  # parent kills this client while its owned stdin pipe remains open
        session.stop()
        return
    if not all((args.linux_checkout, args.linux_python, args.expected_commit, args.receipts)):
        parser.error("--linux-checkout, --linux-python, --expected-commit and --receipts are required")
    if args.local:
        if sys.platform != "linux":
            parser.error("--local is only a Linux harness development run")
    elif sys.platform != "win32" or not args.distribution or not args.linux_user:
        parser.error("forwarding must be initiated by Windows Python; name the distribution and ordinary Linux user")
    for value in (args.linux_checkout, args.linux_python):
        if not PurePosixPath(value).is_absolute() or value.startswith("/mnt/"):
            parser.error("use absolute Linux-filesystem paths, not Windows drive mounts")
    receipts = args.receipts.resolve()
    receipts.mkdir(parents=True, exist_ok=False)
    config = {key: value for key, value in vars(args).items() if key not in ("receipts", "worker")}
    config["wsl_exe"] = str(Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32/wsl.exe")
    run = Run(config, receipts)
    run.execute()
    print(f"{run.report['verdict']}: {run.report['route']}; receipts: {receipts}")  # noqa: T201
    raise SystemExit(0 if run.report["verdict"] == "PASS" else 1)


if __name__ == "__main__":
    main()
