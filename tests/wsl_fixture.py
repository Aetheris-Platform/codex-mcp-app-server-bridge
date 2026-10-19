"""Linux-only fixture control for wsl_forwarding.py; never launches installed Codex."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

CASES = ("complete", "cancel", "deadline", "crash", "bystander", "hostdeath")


def provenance(checkout: Path, expected: str) -> dict:
    def git(*args):
        return subprocess.check_output(["git", "-C", str(checkout), *args])  # noqa: S603,S607 - read-only Git

    head = git("rev-parse", "HEAD").decode().strip()
    assert re.fullmatch(r"[0-9a-f]{40}", expected)
    assert head == expected
    assert not git("status", "--porcelain", "--untracked-files=normal"), "checkout must be clean"
    hashes = {}
    for entry in git("ls-files", "--stage", "-z").split(b"\0"):
        if not entry:
            continue
        metadata, filename = entry.split(b"\t", 1)
        mode, blob, stage = metadata.split()
        assert mode in (b"100644", b"100755")
        assert stage == b"0"
        path = checkout / os.fsdecode(filename)
        assert not path.is_symlink()
        content = path.read_bytes()
        actual = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()  # noqa: S324 - Git blob identity
        assert actual == blob.decode(), f"tracked bytes differ: {filename!r}"
        hashes[os.fsdecode(filename)] = hashlib.sha256(content).hexdigest()
    return {"commit": head, "files_sha256": hashes}


def proc(pid: int) -> dict | None:
    try:
        directory = Path(f"/proc/{pid}")
        stat = (directory / "stat").read_text().rsplit(")", 1)[1].split()
        if directory.stat().st_uid != os.getuid() or stat[0] == "Z":
            return None
        return {"pid": pid, "start": stat[19],
                "argv": [os.fsdecode(v) for v in (directory / "cmdline").read_bytes().split(b"\0") if v]}
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None


def case_path(root: Path, case: str) -> Path:
    assert case in CASES
    return root / f"{case} space ✓"


def inventory(root: Path) -> list[dict]:
    targets = {str(case_path(root, case) / name)
               for case in CASES for name in ("profile.json", "app.sock", "native-fixture")}
    found = []
    for path in Path("/proc").iterdir():
        if path.name.isdigit():
            item = proc(int(path.name))
            if item and targets.intersection(item["argv"]):
                found.append(item)
    return found


def owned_root(value: str, checkout: Path) -> Path:
    root = Path(value)
    assert root.parent == Path("/tmp").resolve()  # noqa: S108
    assert root.name.startswith("cbf-")
    assert root.resolve(strict=True) == root
    assert root.stat().st_uid == os.getuid()
    assert root.stat().st_mode & 0o777 == 0o700
    assert json.loads((root / "owner.json").read_text())["checkout"] == str(checkout)
    return root


def kill_adapter(root: Path, case: str) -> list[int]:
    """Signal only recorded owned adapters, using pidfds to avoid PID reuse."""
    killed = []
    directory = case_path(root, case)
    for record in directory.glob("launch-*.json"):
        saved = json.loads(record.read_text())
        try:
            descriptor = os.pidfd_open(saved["pid"])
        except ProcessLookupError:
            continue
        try:
            current = proc(saved["pid"])
            if (current and current["start"] == saved["start"]
                    and "codex_app_server_adapter" in current["argv"]
                    and str(directory / "profile.json") in current["argv"]):
                signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                killed.append(saved["pid"])
        finally:
            os.close(descriptor)
    return killed


def main() -> None:
    assert sys.platform == "linux", "fixture control requires Linux /proc and pidfds"
    assert not sys.flags.optimize, "assertions must be enabled"
    os.umask(0o077)
    args = json.loads(base64.b64decode(sys.argv[1], validate=True))
    checkout = Path(__file__).resolve().parents[1]
    action = args["action"]
    if action == "prepare":
        identity = provenance(checkout, args["commit"])
        sys.path.insert(0, str(checkout / "src"))
        from codex_app_server_adapter import __main__, guardian, runtime

        imports = {m.__name__: str(Path(m.__file__).resolve()) for m in (__main__, guardian, runtime)}
        assert all(Path(path).is_relative_to(checkout / "src") for path in imports.values())
        root = Path(tempfile.mkdtemp(prefix="cbf-", dir=Path("/tmp").resolve()))  # noqa: S108
        (root / "owner.json").write_text(json.dumps({"checkout": str(checkout)}))
        for case in CASES:
            directory = case_path(root, case)
            directory.mkdir(mode=0o700)
            for name in ("worktree", "data", "codex-home"):
                (directory / name).mkdir(mode=0o700)
            fixture = directory / "native-fixture"
            source = (checkout / "tests/fixtures/codex_app_server_adapter/native_server.py").read_text()
            # Exercise multibyte UTF-8 in the same large paginated result; fixture-only change.
            assert source.count('"x" * (2 * 1024 * 1024)') == 1
            source = source.replace('"x" * (2 * 1024 * 1024)', '"fixture ✓" + "x" * (2 * 1024 * 1024)')
            fixture.write_text(f"#!{sys.executable}\n" + source, encoding="utf-8")
            fixture.chmod(0o700)
            profile = {"runtime_path": str(fixture), "runtime_version": "bridge-native-fixture/1",
                       "runtime_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
                       "socket_path": str(directory / "app.sock"),
                       "allowed_worktree_roots": [str(directory / "worktree")],
                       "allowed_models": ["fixture"], "allowed_sandboxes": ["read-only"],
                       "data_root": str(directory / "data")}
            (directory / "profile.json").write_text(json.dumps(profile), encoding="utf-8")
        result = {"root": str(root), "identity": identity, "imports": imports,
                  "python": sys.version, "uid": os.getuid(), "kernel": os.uname().release}
    else:
        root = owned_root(args["root"], checkout)
        if action == "launch":
            directory = case_path(root, args["case"])
            launch_id = args["launch_id"]
            assert re.fullmatch(r"[0-9a-f]{32}", launch_id)
            (directory / f"launch-{launch_id}.json").write_text(json.dumps(proc(os.getpid())))
            environment = {key: value for key, value in os.environ.items()
                           if not key.startswith("BRIDGE_FIXTURE_") and key != "PYTHONHOME"}
            environment.update(PYTHONPATH=str(checkout / "src"), PYTHONDONTWRITEBYTECODE="1",
                               CODEX_HOME=str(directory / "codex-home"), UV_OFFLINE="1", PIP_NO_INDEX="1",
                               BRIDGE_FIXTURE_REQUEST_LOG=str(directory / "requests.jsonl"),
                               BRIDGE_FIXTURE_HOLD_TURN="0" if args["case"] in ("complete", "bystander") else "1",
                               BRIDGE_FIXTURE_LARGE="1" if args["case"] == "complete" else "0")
            os.chdir(root)
            os.execve(sys.executable,  # noqa: S606 - replace owned helper with destination module
                      [sys.executable, "-m", "codex_app_server_adapter",
                                      "--profile", str(directory / "profile.json")], environment)
        elif action == "kill":
            result = {"killed": kill_adapter(root, args["case"])}
        elif action == "inspect":
            result = {"processes": inventory(root), "cases": {}}
            for case in CASES:
                directory = case_path(root, case)
                log = directory / "requests.jsonl"
                result["cases"][case] = {
                    "socket_exists": (directory / "app.sock").exists(),
                    "requests": [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else [],
                    "tasks": [json.loads(p.read_text()) for p in (directory / "data/tasks").glob("*.json")]}
        elif action == "provenance":
            result = provenance(checkout, args["commit"])
        else:
            raise ValueError("unknown fixture action")
    print(json.dumps(result, ensure_ascii=True))  # noqa: T201 - control protocol, never MCP stream


if __name__ == "__main__":
    main()
