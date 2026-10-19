"""Portable negative checks for forwarding harness boundaries; not WSL evidence."""

import base64
import json
import subprocess

import pytest
from wsl_fixture import case_path, kill_adapter, provenance
from wsl_forwarding import command, reconstruct


def test_forwarding_preserves_argument_boundaries():
    config = {"linux_checkout": "/home/test/a space ✓", "linux_python": "/home/test/a space ✓/.venv/bin/python",
              "local": False, "wsl_exe": r"C:\Windows\System32\wsl.exe",
              "distribution": "Test Distro", "linux_user": "ordinary"}
    payload = {"action": "launch", "root": "/fixture/cbf-a", "case": "cancel"}
    argv = command(config, payload)
    assert argv[:7] == [config["wsl_exe"], "--distribution", "Test Distro", "--user", "ordinary", "--exec", "/usr/bin/env"]
    assert argv[-3:-1] == [config["linux_python"], "/home/test/a space ✓/tests/wsl_fixture.py"]
    assert json.loads(base64.b64decode(argv[-1])) == payload
    assert not {"sh", "bash", "-c"}.intersection(argv)


def test_unknown_case_refused(tmp_path):
    with pytest.raises(AssertionError):
        case_path(tmp_path, "../other-session")


def test_provenance_rejects_changed_tracked_bytes(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args])  # noqa: S603,S607

    git("init", "-q")
    (tmp_path / "file").write_bytes(b"original\n")
    git("add", "file")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
    head = git("rev-parse", "HEAD").decode().strip()
    assert provenance(tmp_path, head)["commit"] == head
    (tmp_path / "file").write_bytes(b"changed\n")
    with pytest.raises(AssertionError):
        provenance(tmp_path, head)


@pytest.mark.parametrize("mismatch", ["pid-start", "profile"])
def test_cleanup_refuses_unowned_process(tmp_path, monkeypatch, mismatch):
    import wsl_fixture

    directory = case_path(tmp_path, "crash")
    directory.mkdir()
    (directory / "launch-test.json").write_text(json.dumps({"pid": 123, "start": "100"}))
    current = {"pid": 123, "start": "999" if mismatch == "pid-start" else "100",
               "argv": ["python", "codex_app_server_adapter", "--profile",
                        str(directory / "profile.json") if mismatch == "pid-start" else "/other/profile.json"]}
    monkeypatch.setattr(wsl_fixture.os, "pidfd_open", lambda pid: 987, raising=False)
    monkeypatch.setattr(wsl_fixture.os, "close", lambda fd: None)
    monkeypatch.setattr(wsl_fixture, "proc", lambda pid: current)
    signals = []
    monkeypatch.setattr(wsl_fixture.signal, "pidfd_send_signal", lambda *args: signals.append(args), raising=False)
    assert kill_adapter(tmp_path, "crash") == []
    assert signals == []


@pytest.mark.parametrize("fault", ["offset", "digest"])
def test_pagination_rejects_corruption(fault):
    class Corrupt:
        def tool(self, name, args):
            total = 2 * 1024 * 1024 + 1
            end = min(args["offset_bytes"] + 4096, total)
            content = b"x" * (end - args["offset_bytes"])
            next_offset = 0 if fault == "offset" else (end if end < total else None)
            return {"chunk": base64.b64encode(content).decode(), "next_offset": next_offset,
                    "total_bytes": total, "complete": True, "output_sha256": "0" * 64}

    with pytest.raises(AssertionError):
        reconstruct(Corrupt(), "task")
