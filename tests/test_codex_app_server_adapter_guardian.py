"""Real-process coverage for the bounded app-server guardian."""

from __future__ import annotations

import asyncio
import errno
import hashlib
import json
import os
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from codex_app_server_adapter import guardian
from codex_app_server_adapter.errors import ProtocolError, ValidationError
from codex_app_server_adapter.guardian import (
    SOCKET_PRIVACY_FAILURE,
    SOCKET_TYPE_FAILURE,
    SocketIdentity,
    _remove_owned_socket,
    _socket_identity,
    _socket_is_candidate,
    _terminate_child,
    _validate_read_pipe,
    expected_physical_socket_path,
    inspect_socket_path,
)
from codex_app_server_adapter.profile import OperatorProfile
from codex_app_server_adapter.runtime import (
    OwnedRuntime,
    _remove_owned_endpoint,
    _same_socket,
)
from codex_app_server_adapter.runtime import _socket_identity as _runtime_socket_identity


def test_guardian_rejects_invalid_internal_read_descriptors(tmp_path: Path) -> None:
    read_fd, write_fd = os.pipe()
    try:
        for fd in (-1, 0, 1, 2, read_fd + 10000):
            with pytest.raises(ValueError):
                _validate_read_pipe(fd)
        with pytest.raises(ValueError):
            _validate_read_pipe(write_fd)
        fifo = tmp_path / "named.fifo"
        os.mkfifo(fifo, 0o600)
        named_fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
        try:
            with pytest.raises(ValueError):
                _validate_read_pipe(named_fd)
        finally:
            os.close(named_fd)
        os.close(read_fd)
        with pytest.raises(ValueError):
            _validate_read_pipe(read_fd)
    finally:
        with pytest.raises(OSError):
            os.close(read_fd)
        os.close(write_fd)


def test_guardian_cli_rejects_bad_fd_and_relative_paths_without_pid_argument(tmp_path: Path) -> None:
    guardian = Path(__file__).parents[1] / "src/codex_app_server_adapter/guardian.py"
    runtime = tmp_path / "runtime.py"
    runtime.write_text(f"#!{sys.executable}\n", encoding="utf-8")
    os.chmod(runtime, 0o700)
    socket_path = tmp_path / "server.sock"
    base = [sys.executable, str(guardian), "--runtime", str(runtime), "--socket", str(socket_path)]
    assert subprocess.run(base, capture_output=True).returncode == 2
    assert subprocess.run(
        [sys.executable, str(guardian), "--socket", str(socket_path), "--read-fd", "3"],
        capture_output=True,
    ).returncode == 2
    assert subprocess.run(
        [sys.executable, str(guardian), "--runtime", str(runtime), "--read-fd", "3"],
        capture_output=True,
    ).returncode == 2
    assert subprocess.run(base + ["--read-fd", "not-an-int"], capture_output=True).returncode == 2

    read_fd, write_fd = os.pipe()
    try:
        for args in (
            ["--runtime", "relative-runtime.py", "--socket", str(socket_path)],
            ["--runtime", str(runtime), "--socket", "relative.sock"],
        ):
            result = subprocess.run(
                [sys.executable, str(guardian), *args, "--read-fd", str(read_fd)],
                pass_fds=(read_fd,),
                capture_output=True,
            )
            assert result.returncode == 1
    finally:
        os.close(read_fd)
        os.close(write_fd)

    read_fd, write_fd = os.pipe()
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(guardian),
                "--runtime",
                str(tmp_path / "missing-runtime"),
                "--socket",
                str(socket_path),
                "--read-fd",
                str(read_fd),
            ],
            pass_fds=(read_fd,),
            capture_output=True,
        )
        assert result.returncode == 1
    finally:
        os.close(read_fd)
        os.close(write_fd)

    regular = tmp_path / "regular"
    regular.write_text("not a pipe", encoding="utf-8")
    regular_fd = os.open(regular, os.O_RDONLY)
    try:
        result = subprocess.run(
            [*base, "--read-fd", str(regular_fd)], pass_fds=(regular_fd,), capture_output=True
        )
        assert result.returncode == 1
    finally:
        os.close(regular_fd)


def test_guardian_eof_before_spawn_never_invokes_runtime(tmp_path: Path) -> None:
    marker = tmp_path / "invoked"
    runtime = tmp_path / "runtime.py"
    runtime.write_text(
        f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(marker)!r}).write_text('started')\n",
        encoding="utf-8",
    )
    os.chmod(runtime, 0o700)
    read_fd, write_fd = os.pipe()
    os.close(write_fd)
    guardian = Path(__file__).parents[1] / "src/codex_app_server_adapter/guardian.py"
    process = subprocess.Popen(
        [sys.executable, str(guardian), "--runtime", str(runtime), "--socket", str(tmp_path / "server.sock"),
         "--read-fd", str(read_fd)],
        pass_fds=(read_fd,),
        close_fds=True,
    )
    os.close(read_fd)
    assert process.wait(timeout=5) == 0
    assert not marker.exists()


def test_guardian_eof_during_startup_kills_child_before_bind(tmp_path: Path) -> None:
    pid_path = tmp_path / "child.pid"
    runtime = tmp_path / "delayed-runtime.py"
    runtime.write_text(
        f"#!{sys.executable}\nimport os, time\nopen({str(pid_path)!r}, 'w').write(str(os.getpid()))\ntime.sleep(60)\n",
        encoding="utf-8",
    )
    os.chmod(runtime, 0o700)
    socket_path = tmp_path / "server.sock"
    read_fd, write_fd = os.pipe()
    guardian = Path(__file__).parents[1] / "src/codex_app_server_adapter/guardian.py"
    process = subprocess.Popen(
        [sys.executable, str(guardian), "--runtime", str(runtime), "--socket", str(socket_path),
         "--read-fd", str(read_fd)],
        pass_fds=(read_fd,),
        close_fds=True,
    )
    os.close(read_fd)
    child_pid: int | None = None
    try:
        for _ in range(100):
            if pid_path.exists():
                child_pid = int(pid_path.read_text(encoding="utf-8"))
                break
            time.sleep(0.02)
        assert child_pid is not None
        os.close(write_fd)
        assert process.wait(timeout=10) == 0
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
        assert not socket_path.exists()
    finally:
        with suppress(OSError):
            os.close(write_fd)
        if process.poll() is None:
            process.kill()
            process.wait()


def test_guardian_eof_before_delayed_bind_removes_socket_during_cleanup(tmp_path: Path) -> None:
    pid_path = tmp_path / "child.pid"
    bound_path = tmp_path / "bound"
    runtime = tmp_path / "delayed-bind-runtime.py"
    runtime.write_text(
        f"#!{sys.executable}\n"
        "import os, signal, socket, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"open({str(pid_path)!r}, 'w').write(str(os.getpid()))\n"
        "time.sleep(0.6)\n"
        "path = sys.argv[3].removeprefix('unix://')\n"
        "sock = socket.socket(socket.AF_UNIX)\n"
        "sock.bind(path)\n"
        "os.chmod(path, 0o600)\n"
        "sock.listen()\n"
        f"open({str(bound_path)!r}, 'w').write('bound')\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    os.chmod(runtime, 0o700)
    socket_path = tmp_path / "delayed-bind.sock"
    read_fd, write_fd = os.pipe()
    guardian = Path(__file__).parents[1] / "src/codex_app_server_adapter/guardian.py"
    process = subprocess.Popen(
        [
            sys.executable,
            str(guardian),
            "--runtime",
            str(runtime),
            "--socket",
            str(socket_path),
            "--read-fd",
            str(read_fd),
        ],
        pass_fds=(read_fd,),
        close_fds=True,
    )
    os.close(read_fd)
    child_pid: int | None = None
    try:
        for _ in range(100):
            if pid_path.exists():
                child_pid = int(pid_path.read_text(encoding="utf-8"))
                break
            time.sleep(0.02)
        assert child_pid is not None
        os.close(write_fd)
        write_fd = -1
        assert process.wait(timeout=12) == 0
        assert bound_path.read_text(encoding="utf-8") == "bound"
        assert not socket_path.exists()
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
    finally:
        if write_fd != -1:
            with suppress(OSError):
                os.close(write_fd)
        if process.poll() is None:
            process.kill()
            process.wait()
        if child_pid is not None:
            with suppress(ProcessLookupError):
                os.kill(child_pid, signal.SIGKILL)
        with suppress(FileNotFoundError):
            socket_path.unlink()


def test_guardian_unexpected_data_proves_invocation_and_child_cleanup(tmp_path: Path) -> None:
    pid_path = tmp_path / "child.pid"
    runtime = tmp_path / "delayed-runtime.py"
    runtime.write_text(
        f"#!{sys.executable}\nimport os, time\nopen({str(pid_path)!r}, 'w').write(str(os.getpid()))\ntime.sleep(60)\n",
        encoding="utf-8",
    )
    os.chmod(runtime, 0o700)
    socket_path = tmp_path / "server.sock"
    read_fd, write_fd = os.pipe()
    guardian = Path(__file__).parents[1] / "src/codex_app_server_adapter/guardian.py"
    process = subprocess.Popen(
        [sys.executable, str(guardian), "--runtime", str(runtime), "--socket", str(socket_path),
         "--read-fd", str(read_fd)],
        pass_fds=(read_fd,),
        close_fds=True,
    )
    os.close(read_fd)
    child_pid: int | None = None
    try:
        for _ in range(100):
            if pid_path.exists():
                child_pid = int(pid_path.read_text(encoding="utf-8"))
                break
            time.sleep(0.02)
        assert child_pid is not None
        os.write(write_fd, b"unexpected")
        assert process.wait(timeout=10) == 0
        assert pid_path.exists()
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
        assert not socket_path.exists()
    finally:
        with suppress(OSError):
            os.close(write_fd)
        if process.poll() is None:
            process.kill()
            process.wait()


def _make_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[OperatorProfile, Path]:
    source = Path(__file__).parent / "fixtures" / "codex_app_server_adapter" / "native_server.py"
    runtime_path = tmp_path / "native-fixture"
    runtime_path.write_text(f"#!{sys.executable}\n" + source.read_text(encoding="utf-8"), encoding="utf-8")
    os.chmod(runtime_path, 0o700)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    worktree = tmp_path / "worktree"
    worktree.mkdir(mode=0o700)
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    pid_path = tmp_path / "fixture.pid"
    monkeypatch.setenv("BRIDGE_FIXTURE_PID_PATH", str(pid_path))
    monkeypatch.setenv("BRIDGE_FIXTURE_HOLD_TURN", "1")
    return OperatorProfile(
        runtime_path=runtime_path,
        runtime_version="bridge-native-fixture/1",
        runtime_sha256=hashlib.sha256(runtime_path.read_bytes()).hexdigest(),
        socket_path=private / "app.sock",
        allowed_worktree_roots=(worktree,),
        allowed_models=("gpt-5.6-luna",),
        allowed_sandboxes=("read-only",),
        data_root=data,
    ), pid_path


def test_guardian_sigkill_leaves_uncertain_socket_and_stale_admission_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asyncio.run(_test_guardian_sigkill_leaves_uncertain_socket_and_stale_admission_fails(tmp_path, monkeypatch))


async def _test_guardian_sigkill_leaves_uncertain_socket_and_stale_admission_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile, pid_path = _make_runtime(tmp_path, monkeypatch)
    runtime = OwnedRuntime(profile)
    child_pid: int | None = None
    try:
        await runtime.ensure_started()
        for _ in range(100):
            if pid_path.exists():
                break
            await asyncio.sleep(0.02)
        child_pid = int(pid_path.read_text(encoding="utf-8"))
        assert runtime.process is not None
        runtime.process.kill()
        await runtime.process.wait()
        assert profile.socket_path.exists()
        with pytest.raises(ProtocolError):
            await runtime.close()
        assert profile.socket_path.exists()
        with pytest.raises(ProtocolError):
            await runtime.ensure_started()
    finally:
        if child_pid is not None:
            with suppress(ProcessLookupError):
                os.kill(child_pid, signal.SIGTERM)
        if profile.socket_path.exists() and not stat.S_ISLNK(profile.socket_path.lstat().st_mode):
            profile.socket_path.unlink()


def test_adapter_sigkill_closes_liveness_pipe_and_guardian_cleans_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile, pid_path = _make_runtime(tmp_path, monkeypatch)
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "runtime_path": str(profile.runtime_path),
                "runtime_version": profile.runtime_version,
                "runtime_sha256": profile.runtime_sha256,
                "socket_path": str(profile.socket_path),
                "allowed_worktree_roots": [str(path) for path in profile.allowed_worktree_roots],
                "allowed_models": list(profile.allowed_models),
                "allowed_sandboxes": list(profile.allowed_sandboxes),
                "data_root": str(profile.data_root),
            }
        ),
        encoding="utf-8",
    )
    runner = tmp_path / "adapter-runner.py"
    runner.write_text(
        "import asyncio, sys, time\n"
        "from pathlib import Path\n"
        "from codex_app_server_adapter.profile import OperatorProfile\n"
        "from codex_app_server_adapter.runtime import OwnedRuntime\n"
        "async def main():\n"
        " runtime = OwnedRuntime(OperatorProfile.from_json(Path(sys.argv[1]).read_text()))\n"
        " await runtime.ensure_started()\n"
        " while True: await asyncio.sleep(1)\n"
        "asyncio.run(main())\n",
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    process = subprocess.Popen([sys.executable, str(runner), str(profile_path)], env=environment)
    child_pid: int | None = None
    try:
        for _ in range(200):
            if pid_path.exists() and profile.socket_path.exists():
                break
            time.sleep(0.02)
        assert pid_path.exists()
        child_pid = int(pid_path.read_text(encoding="utf-8"))
        process.kill()
        assert process.wait(timeout=5) is not None
        for _ in range(300):
            if not profile.socket_path.exists():
                try:
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    break
            time.sleep(0.02)
        assert not profile.socket_path.exists()
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        if child_pid is not None:
            with suppress(ProcessLookupError):
                os.kill(child_pid, signal.SIGKILL)
        with suppress(FileNotFoundError):
            profile.socket_path.unlink()


def test_guardian_normal_close_removes_exact_socket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asyncio.run(_test_guardian_normal_close_removes_exact_socket(tmp_path, monkeypatch))


async def _test_guardian_normal_close_removes_exact_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile, _ = _make_runtime(tmp_path, monkeypatch)
    runtime = OwnedRuntime(profile)
    await runtime.ensure_started()
    await runtime.close()
    assert not profile.socket_path.exists()


def test_guardian_leaves_substituted_socket_untouched(tmp_path: Path) -> None:
    original = tmp_path / "original.sock"
    replacement = tmp_path / "replacement.sock"
    first = socket.socket(socket.AF_UNIX)
    second = socket.socket(socket.AF_UNIX)
    try:
        first.bind(str(original))
        second.bind(str(replacement))
        os.chmod(original, 0o600)
        os.chmod(replacement, 0o600)
        identity = _socket_identity(original)
        assert identity is not None
        original.unlink()
        original.symlink_to(replacement)
        _remove_owned_socket(original, identity)
        assert original.is_symlink()
        assert _same_socket(original, (identity[0], identity[1])) is False
    finally:
        first.close()
        second.close()
        for path in (original, replacement):
            with suppress(FileNotFoundError):
                path.unlink()


def test_guardian_leaves_same_path_inode_replacement_untouched(tmp_path: Path) -> None:
    socket_path = tmp_path / "replaced.sock"
    first = socket.socket(socket.AF_UNIX)
    replacement = socket.socket(socket.AF_UNIX)
    try:
        first.bind(str(socket_path))
        os.chmod(socket_path, 0o600)
        identity = _socket_identity(socket_path)
        assert identity is not None
        socket_path.unlink()
        replacement.bind(str(socket_path))
        os.chmod(socket_path, 0o600)
        _remove_owned_socket(socket_path, identity)
        assert socket_path.exists()
        assert _socket_identity(socket_path) != identity
    finally:
        first.close()
        replacement.close()
        with suppress(FileNotFoundError):
            socket_path.unlink()


@pytest.mark.parametrize(
    ("mode", "uid", "kind"),
    [
        (stat.S_IFREG | 0o600, os.getuid(), "non-socket"),
        (stat.S_IFSOCK | 0o666, os.getuid(), "non-private-mode"),
        (stat.S_IFSOCK | 0o600, os.getuid() + 1, "wrong-owner"),
    ],
)
def test_guardian_rejects_unsafe_socket_identity_platform_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: int,
    uid: int,
    kind: str,
) -> None:
    path = tmp_path / f"{kind}.sock"
    fake_lstat = SimpleNamespace(
        st_mode=mode,
        st_uid=uid,
        st_dev=1,
        st_ino=2,
        st_nlink=1,
    )
    monkeypatch.setattr(Path, "lstat", lambda _path: fake_lstat)
    with pytest.raises(ValueError):
        _socket_identity(path)


def test_guardian_absent_socket_is_not_deleted(tmp_path: Path) -> None:
    missing = tmp_path / "missing.sock"
    _remove_owned_socket(missing, (1, 2, os.getuid(), 0o600))
    assert not missing.exists()


def test_guardian_child_and_unrelated_sentinel_inherit_no_liveness_descriptors(
    tmp_path: Path,
) -> None:
    inventory = tmp_path / "inventory"
    sentinel_inventory = tmp_path / "sentinel"
    runtime = tmp_path / "inventory-runtime.py"
    runtime.write_text(
        f"#!{sys.executable}\n"
        "import os, socket, time\n"
        "from pathlib import Path\n"
        "def open_fd(name):\n"
        " try: os.fstat(int(os.environ[name])); return '1'\n"
        " except OSError: return '0'\n"
        f"path = {str(inventory)!r}\n"
        "sock = socket.socket(socket.AF_UNIX)\n"
        "sock.bind(os.sys.argv[3].removeprefix('unix://'))\n"
        "os.chmod(os.sys.argv[3].removeprefix('unix://'), 0o600)\n"
        "sock.listen()\n"
        "Path(path).write_text(open_fd('BRIDGE_READ_FD') + open_fd('BRIDGE_WRITE_FD'))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    os.chmod(runtime, 0o700)
    socket_path = tmp_path / "inventory.sock"
    read_fd, write_fd = os.pipe()
    environment = dict(os.environ)
    environment.update(
        BRIDGE_READ_FD=str(read_fd),
        BRIDGE_WRITE_FD=str(write_fd),
        BRIDGE_SENTINEL=str(sentinel_inventory),
    )
    sentinel = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import os; from pathlib import Path;\n"
            "try: os.fstat(int(os.environ['BRIDGE_WRITE_FD'])); value='1'\n"
            "except OSError: value='0'\n"
            "Path(os.environ['BRIDGE_SENTINEL']).write_text(value)",
        ],
        env=environment,
        close_fds=True,
    )
    guardian = Path(__file__).parents[1] / "src/codex_app_server_adapter/guardian.py"
    process = subprocess.Popen(
        [sys.executable, str(guardian), "--runtime", str(runtime), "--socket", str(socket_path),
         "--read-fd", str(read_fd)],
        env=environment,
        pass_fds=(read_fd,),
        close_fds=True,
    )
    os.close(read_fd)
    try:
        assert sentinel.wait(timeout=5) == 0
        for _ in range(100):
            if inventory.exists() and socket_path.exists():
                break
            time.sleep(0.02)
        assert inventory.read_text(encoding="utf-8") == "00"
        assert sentinel_inventory.read_text(encoding="utf-8") == "0"
        os.close(write_fd)
        assert process.wait(timeout=10) == 0
        assert not socket_path.exists()
    finally:
        with suppress(OSError):
            os.close(write_fd)
        if process.poll() is None:
            process.kill()
            process.wait()
        if sentinel.poll() is None:
            sentinel.kill()
            sentinel.wait()


def test_guardian_cleanup_does_not_unlink_after_lost_group_ownership(tmp_path: Path) -> None:
    class ExitedChild:
        pid = 12345

        def wait(self) -> int:
            raise AssertionError("lost ownership must not reap or unlink")

    socket_path = tmp_path / "owned.sock"
    socket_path.write_text("sentinel", encoding="utf-8")
    with patch("codex_app_server_adapter.guardian._leader_state", return_value=True), patch(
        "codex_app_server_adapter.guardian.os.killpg",
        side_effect=PermissionError(errno.EACCES, "final group SIGKILL denied after ownership loss"),
    ):
        assert not _terminate_child(cast(subprocess.Popen[bytes], ExitedChild()), socket_path, (1, 2, os.getuid(), 0o600))
    assert socket_path.exists()


def test_guardian_darwin_empty_zombie_group_eprem_is_accepted(tmp_path: Path) -> None:
    class ExitedChild:
        pid = 12345

        def wait(self) -> int:
            return 0

    final_signal = PermissionError(errno.EPERM, "final group SIGKILL denied after leader exit")
    empty_group_probe = ProcessLookupError(errno.ESRCH, "empty zombie group after reap")
    with patch("codex_app_server_adapter.guardian.sys.platform", "darwin"), patch(
        "codex_app_server_adapter.guardian._leader_state", return_value=True
    ), patch(
        "codex_app_server_adapter.guardian.os.killpg",
        side_effect=[final_signal, empty_group_probe],
    ) as killpg:
        assert _terminate_child(cast(subprocess.Popen[bytes], ExitedChild()), tmp_path / "missing.sock", None)
    assert [call.args[1] for call in killpg.call_args_list] == [signal.SIGKILL, 0]


def test_guardian_darwin_denied_final_signal_with_live_group_fails(tmp_path: Path) -> None:
    class ExitedChild:
        pid = 12345

        def wait(self) -> int:
            return 0

    final_signal = PermissionError(errno.EPERM, "final group SIGKILL denied after leader exit")
    with patch("codex_app_server_adapter.guardian.sys.platform", "darwin"), patch(
        "codex_app_server_adapter.guardian._leader_state", return_value=True
    ), patch(
        "codex_app_server_adapter.guardian.os.killpg",
        side_effect=[final_signal, None],
    ):
        assert not _terminate_child(cast(subprocess.Popen[bytes], ExitedChild()), tmp_path / "missing.sock", None)


def test_guardian_cleanup_sends_no_signal_after_lost_leader_observation(tmp_path: Path) -> None:
    class UnknownChild:
        pid = 12345

    socket_path = tmp_path / "owned.sock"
    socket_path.write_text("sentinel", encoding="utf-8")
    with patch("codex_app_server_adapter.guardian._leader_state", return_value=None), patch(
        "codex_app_server_adapter.guardian.os.killpg"
    ) as killpg:
        assert not _terminate_child(cast(subprocess.Popen[bytes], UnknownChild()), socket_path, (1, 2, os.getuid(), 0o600))
    killpg.assert_not_called()
    assert socket_path.exists()


def test_guardian_cleanup_timeout_preserves_socket_and_reports_failure(tmp_path: Path) -> None:
    class AliveChild:
        pid = 12345

        def wait(self) -> int:
            raise AssertionError("a bounded cleanup timeout must not reap")

    socket_path = tmp_path / "owned.sock"
    socket_path.write_text("sentinel", encoding="utf-8")
    with patch("codex_app_server_adapter.guardian._leader_state", return_value=False), patch(
        "codex_app_server_adapter.guardian._wait_for_leader_exit", return_value=False
    ), patch("codex_app_server_adapter.guardian.os.killpg") as killpg:
        assert not _terminate_child(cast(subprocess.Popen[bytes], AliveChild()), socket_path, (1, 2, os.getuid(), 0o600))
    assert [call.args[1] for call in killpg.call_args_list] == [signal.SIGTERM, signal.SIGKILL]
    assert socket_path.exists()


def test_runtime_latches_guardian_failure_before_termination_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile, _ = _make_runtime(tmp_path, monkeypatch)
    runtime = OwnedRuntime(profile)

    class StuckProcess:
        returncode = None

        def wait(self) -> None:
            return None

    runtime.process = StuckProcess()  # type: ignore[assignment]

    async def timeout(_awaitable: object, timeout: float) -> None:
        raise TimeoutError

    monkeypatch.setattr("codex_app_server_adapter.runtime.asyncio.wait_for", timeout)
    runtime._terminate = AsyncMock(side_effect=OSError("termination denied"))  # type: ignore[method-assign]

    with pytest.raises(OSError, match="termination denied"):
        asyncio.run(runtime.close())
    assert runtime._guardian_failed


def test_runtime_stop_guardian_latches_before_termination_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile, _ = _make_runtime(tmp_path, monkeypatch)
    runtime = OwnedRuntime(profile)

    class StuckProcess:
        returncode = None

        def wait(self) -> None:
            return None

    process = StuckProcess()

    async def timeout(_awaitable: object, timeout: float) -> None:
        raise TimeoutError

    monkeypatch.setattr("codex_app_server_adapter.runtime.asyncio.wait_for", timeout)
    runtime._terminate = AsyncMock(side_effect=OSError("termination denied"))  # type: ignore[method-assign]

    with pytest.raises(OSError, match="termination denied"):
        asyncio.run(runtime._stop_guardian(process))  # type: ignore[arg-type]
    assert runtime._guardian_failed


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_guardian_signal_bounds_cleanup_of_term_ignoring_child(
    tmp_path: Path, signum: signal.Signals
) -> None:
    runtime = tmp_path / "term-ignoring-runtime.py"
    runtime.write_text(
        f"#!{sys.executable}\n"
        "import os, signal, socket, sys, time\n"
        "if sys.argv[1:] != ['app-server', '--listen', sys.argv[3]]: raise SystemExit(2)\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "sock = socket.socket(socket.AF_UNIX)\n"
        "sock.bind(sys.argv[3].removeprefix('unix://'))\n"
        "os.chmod(sys.argv[3].removeprefix('unix://'), 0o600)\n"
        "sock.listen()\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    os.chmod(runtime, 0o700)
    socket_path = tmp_path / "term.sock"
    read_fd, write_fd = os.pipe()
    guardian = Path(__file__).parents[1] / "src/codex_app_server_adapter/guardian.py"
    process = subprocess.Popen(
        [
            sys.executable,
            str(guardian),
            "--runtime",
            str(runtime),
            "--socket",
            str(socket_path),
            "--read-fd",
            str(read_fd),
        ],
        pass_fds=(read_fd,),
        close_fds=True,
    )
    os.close(read_fd)
    try:
        for _ in range(100):
            if socket_path.exists():
                break
            time.sleep(0.02)
        assert socket_path.exists()
        started = time.monotonic()
        process.send_signal(signum)
        assert process.wait(timeout=12) == 0
        assert time.monotonic() - started < 12
        assert not socket_path.exists()
    finally:
        os.close(write_fd)
        if process.poll() is None:
            process.kill()
            process.wait()


def test_guardian_cleans_term_ignoring_descendant_after_leader_exit(tmp_path: Path) -> None:
    runtime = tmp_path / "forking-runtime.py"
    descendant_pid_path = tmp_path / "descendant.pid"
    runtime.write_text(
        f"#!{sys.executable}\n"
        "import os, signal, socket, sys, time\n"
        "path = sys.argv[3].removeprefix('unix://')\n"
        "if os.fork() == 0:\n"
        " signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f" open({str(descendant_pid_path)!r}, 'w').write(str(os.getpid()))\n"
        " sock = socket.socket(socket.AF_UNIX)\n"
        " sock.bind(path)\n"
        " os.chmod(path, 0o600)\n"
        " sock.listen()\n"
        " time.sleep(60)\n"
        "os._exit(0)\n",
        encoding="utf-8",
    )
    os.chmod(runtime, 0o700)
    socket_path = tmp_path / "descendant.sock"
    read_fd, write_fd = os.pipe()
    guardian = Path(__file__).parents[1] / "src/codex_app_server_adapter/guardian.py"
    process = subprocess.Popen(
        [sys.executable, str(guardian), "--runtime", str(runtime), "--socket", str(socket_path), "--read-fd", str(read_fd)],
        pass_fds=(read_fd,),
        close_fds=True,
    )
    os.close(read_fd)
    try:
        assert process.wait(timeout=15) == 1
        assert not socket_path.exists()
        descendant_pid = int(descendant_pid_path.read_text(encoding="utf-8"))
        with pytest.raises(ProcessLookupError):
            os.kill(descendant_pid, 0)
    finally:
        os.close(write_fd)
        if process.poll() is None:
            process.kill()
            process.wait()
        with suppress(FileNotFoundError):
            socket_path.unlink()


def test_guardian_final_group_cleanup_kills_term_ignoring_descendant_after_term(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "running-parent-runtime.py"
    parent_pid_path = tmp_path / "parent.pid"
    descendant_pid_path = tmp_path / "descendant.pid"
    descendant_ready = tmp_path / "descendant.ready"
    runtime.write_text(
        f"#!{sys.executable}\n"
        "import os, signal, socket, sys, time\n"
        "path = sys.argv[3].removeprefix('unix://')\n"
        "sock = socket.socket(socket.AF_UNIX)\n"
        "sock.bind(path)\n"
        "os.chmod(path, 0o600)\n"
        "sock.listen()\n"
        f"open({str(parent_pid_path)!r}, 'w').write(str(os.getpid()))\n"
        "if os.fork() == 0:\n"
        " signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f" open({str(descendant_pid_path)!r}, 'w').write(str(os.getpid()))\n"
        f" open({str(descendant_ready)!r}, 'w').write('ready')\n"
        " time.sleep(60)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    os.chmod(runtime, 0o700)
    socket_path = tmp_path / "running-parent.sock"
    read_fd, write_fd = os.pipe()
    guardian = Path(__file__).parents[1] / "src/codex_app_server_adapter/guardian.py"
    process = subprocess.Popen(
        [
            sys.executable,
            str(guardian),
            "--runtime",
            str(runtime),
            "--socket",
            str(socket_path),
            "--read-fd",
            str(read_fd),
        ],
        pass_fds=(read_fd,),
        close_fds=True,
    )
    os.close(read_fd)
    parent_pid: int | None = None
    descendant_pid: int | None = None
    try:
        for _ in range(200):
            if parent_pid_path.exists() and descendant_ready.exists() and socket_path.exists():
                break
            time.sleep(0.02)
        parent_pid = int(parent_pid_path.read_text(encoding="utf-8"))
        descendant_pid = int(descendant_pid_path.read_text(encoding="utf-8"))
        assert socket_path.exists()
        os.close(write_fd)
        write_fd = -1
        assert process.wait(timeout=12) == 0
        assert not socket_path.exists()
        with pytest.raises(ProcessLookupError):
            os.kill(parent_pid, 0)
        with pytest.raises(ProcessLookupError):
            os.kill(descendant_pid, 0)
    finally:
        if write_fd != -1:
            with suppress(OSError):
                os.close(write_fd)
        if process.poll() is None:
            process.kill()
            process.wait()
        for pid in (parent_pid, descendant_pid):
            if pid is not None:
                with suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGKILL)
        with suppress(FileNotFoundError):
            socket_path.unlink()


def test_guardian_unexpected_liveness_data_cleans_before_socket_start(tmp_path: Path) -> None:
    runtime = tmp_path / "delayed-runtime.py"
    runtime.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(60)\n", encoding="utf-8")
    os.chmod(runtime, 0o700)
    socket_path = tmp_path / "delayed.sock"
    read_fd, write_fd = os.pipe()
    guardian = Path(__file__).parents[1] / "src/codex_app_server_adapter/guardian.py"
    process = subprocess.Popen(
        [sys.executable, str(guardian), "--runtime", str(runtime), "--socket", str(socket_path), "--read-fd", str(read_fd)],
        pass_fds=(read_fd,),
        close_fds=True,
    )
    os.close(read_fd)
    try:
        os.write(write_fd, b"unexpected")
        assert process.wait(timeout=10) == 0
        assert not socket_path.exists()
    finally:
        os.close(write_fd)
        if process.poll() is None:
            process.kill()
            process.wait()


def test_runtime_refuses_restart_after_child_exits_before_socket(tmp_path: Path) -> None:
    runtime_path = tmp_path / "early-exit-runtime.py"
    runtime_path.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "if sys.argv[1:] == ['--version']:\n print('unused')\n"
        "else:\n raise SystemExit(7)\n",
        encoding="utf-8",
    )
    os.chmod(runtime_path, 0o700)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    worktree = tmp_path / "worktree"
    worktree.mkdir(mode=0o700)
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    profile = OperatorProfile(
        runtime_path=runtime_path,
        runtime_version="unused",
        runtime_sha256=hashlib.sha256(runtime_path.read_bytes()).hexdigest(),
        socket_path=private / "early.sock",
        allowed_worktree_roots=(worktree,),
        allowed_models=("gpt-5.6-luna",),
        allowed_sandboxes=("read-only",),
        data_root=data,
    )
    runtime = OwnedRuntime(profile)

    async def exercise() -> None:
        with pytest.raises(ProtocolError):
            await runtime.ensure_started()
        with pytest.raises(ProtocolError):
            await runtime.ensure_started()

    asyncio.run(exercise())


# --- Codex CLI 0.156.0 deterministic socket alias (deterministic alias coverage) -------------------------

_CODEX_0156 = Path(os.environ.get("BRIDGE_TEST_CODEX_0156", "/nonexistent/codex"))


def _derive_physical(alias: Path) -> Path:
    """Second, independent derivation of the 0.156.0 physical socket path.

    Mirrors `protected_socket_path` over `shared_daemon_socket_directory` in the release
    source; the adapter must agree with the runtime, not merely with itself.
    """

    shared = Path(os.path.realpath("/tmp")) / f"codex-daemon-{os.geteuid()}"
    digest = hashlib.sha256(
        os.fsencode(os.path.join(os.path.realpath(alias.parent), alias.name))
    ).hexdigest()
    return shared / digest


@contextmanager
def _alias_layout(alias: Path, *, mode: int = 0o600) -> Iterator[Path]:
    """Publish the runtime's alias layout for one alias and remove only what it created."""

    physical = _derive_physical(alias)
    physical.parent.mkdir(mode=0o700, exist_ok=True)
    bound = socket.socket(socket.AF_UNIX)
    try:
        bound.bind(str(physical))
        os.chmod(physical, mode)
        alias.symlink_to(physical)
        yield physical
    finally:
        bound.close()
        with suppress(FileNotFoundError):
            alias.unlink()
        with suppress(FileNotFoundError):
            physical.unlink()


def test_derived_physical_socket_path_matches_the_runtime_layout(tmp_path: Path) -> None:
    shared = Path(os.path.realpath("/tmp")) / f"codex-daemon-{os.geteuid()}"
    deep = tmp_path / "a" / "b c" / "d"
    deep.mkdir(parents=True)
    for alias in (tmp_path / "app.sock", deep / "name with spaces.sock", deep / "app.sock"):
        derived = expected_physical_socket_path(alias)
        assert derived == _derive_physical(alias)
        assert derived.parent == shared
        assert len(derived.name) == 64
        assert derived.name == derived.name.lower()
        int(derived.name, 16)
    assert expected_physical_socket_path(tmp_path / "app.sock") != expected_physical_socket_path(
        tmp_path / "app2.sock"
    )
    aliased_parent = tmp_path / "link"
    aliased_parent.symlink_to(deep)
    assert expected_physical_socket_path(
        aliased_parent / "app.sock"
    ) == expected_physical_socket_path(deep / "app.sock")


def test_alias_layout_is_admitted_by_guardian_and_runtime(tmp_path: Path) -> None:
    alias = tmp_path / "app.sock"
    with _alias_layout(alias) as physical:
        bound = physical.lstat()
        link = alias.lstat()
        expected = SocketIdentity(
            bound.st_dev,
            bound.st_ino,
            bound.st_uid,
            0o600,
            link.st_dev,
            link.st_ino,
            str(physical),
        )
        assert _socket_identity(alias) == expected
        assert _runtime_socket_identity(alias) == expected
        assert inspect_socket_path(alias) == (expected, None)
        assert _same_socket(alias, expected) is True
        assert _socket_is_candidate(alias) is True


def test_direct_socket_shape_is_unchanged_by_alias_support(tmp_path: Path) -> None:
    path = tmp_path / "direct.sock"
    bound = socket.socket(socket.AF_UNIX)
    try:
        bound.bind(str(path))
        os.chmod(path, 0o600)
        info = path.lstat()
        identity = _socket_identity(path)
        assert identity == SocketIdentity(info.st_dev, info.st_ino, info.st_uid, 0o600)
        assert identity is not None
        assert identity.link_target is None
        assert _runtime_socket_identity(path) == identity
        assert _socket_is_candidate(path) is True
        assert _same_socket(path, identity) is True
    finally:
        bound.close()
        with suppress(FileNotFoundError):
            path.unlink()


@pytest.mark.parametrize(
    "variant",
    ["other-private-socket", "relative", "one-hex-digit", "other-uid-directory", "second-level"],
)
def test_alias_rejects_every_target_other_than_the_derived_socket(
    tmp_path: Path, variant: str
) -> None:
    alias = tmp_path / "app.sock"
    other = tmp_path / "other.sock"
    indirect = tmp_path / "indirect"
    bound = socket.socket(socket.AF_UNIX)
    with _alias_layout(alias) as physical:
        try:
            bound.bind(str(other))
            os.chmod(other, 0o600)
            alias.unlink()
            if variant == "other-private-socket":
                alias.symlink_to(other)
            elif variant == "relative":
                alias.symlink_to(Path(os.path.relpath(physical, tmp_path)))
            elif variant == "one-hex-digit":
                head = "0" if physical.name[0] != "0" else "1"
                alias.symlink_to(physical.with_name(head + physical.name[1:]))
            elif variant == "other-uid-directory":
                foreign = Path(os.path.realpath("/tmp")) / f"codex-daemon-{os.geteuid() + 1}"
                alias.symlink_to(foreign / physical.name)
            else:
                indirect.symlink_to(physical)
                alias.symlink_to(indirect)
            assert inspect_socket_path(alias) == (None, SOCKET_TYPE_FAILURE)
            with pytest.raises(ValueError):
                _socket_identity(alias)
            with pytest.raises(ValidationError, match="app-server path is not a Unix socket"):
                _runtime_socket_identity(alias)
            assert _socket_is_candidate(alias) is False
        finally:
            bound.close()
            with suppress(FileNotFoundError):
                other.unlink()


@pytest.mark.parametrize("kind", ["dangling", "regular-file", "directory", "fifo"])
def test_alias_rejects_a_target_that_is_not_a_private_socket(tmp_path: Path, kind: str) -> None:
    alias = tmp_path / "app.sock"
    physical = _derive_physical(alias)
    physical.parent.mkdir(mode=0o700, exist_ok=True)
    try:
        if kind == "regular-file":
            physical.write_bytes(b"")
        elif kind == "directory":
            physical.mkdir(mode=0o700)
        elif kind == "fifo":
            os.mkfifo(physical, 0o600)
        alias.symlink_to(physical)
        assert inspect_socket_path(alias) == (None, SOCKET_TYPE_FAILURE)
        with pytest.raises(ValueError):
            _socket_identity(alias)
        assert _socket_is_candidate(alias) is False
    finally:
        with suppress(FileNotFoundError):
            alias.unlink()
        if kind == "directory":
            with suppress(FileNotFoundError):
                physical.rmdir()
        else:
            with suppress(FileNotFoundError):
                physical.unlink()


@pytest.mark.parametrize("kind", ["regular-file", "directory", "fifo"])
def test_non_socket_at_the_alias_path_is_rejected(tmp_path: Path, kind: str) -> None:
    path = tmp_path / "app.sock"
    if kind == "regular-file":
        path.write_bytes(b"")
    elif kind == "directory":
        path.mkdir(mode=0o700)
    else:
        os.mkfifo(path, 0o600)
    assert inspect_socket_path(path) == (None, SOCKET_TYPE_FAILURE)
    with pytest.raises(ValueError):
        _socket_identity(path)
    with pytest.raises(ValidationError, match="app-server path is not a Unix socket"):
        _runtime_socket_identity(path)
    assert _socket_is_candidate(path) is False


@pytest.mark.parametrize(
    ("kind", "failure"),
    [
        ("absent", SOCKET_TYPE_FAILURE),
        ("regular-file", SOCKET_TYPE_FAILURE),
        ("symlinked", SOCKET_TYPE_FAILURE),
        ("group-readable", SOCKET_PRIVACY_FAILURE),
        ("group-writable", SOCKET_PRIVACY_FAILURE),
    ],
)
def test_alias_rejects_an_unsafe_rendezvous_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, failure: str
) -> None:
    root = Path(tempfile.mkdtemp(prefix="cb-alias-", dir=Path("/tmp").resolve()))
    shared = root / "s"
    bound = socket.socket(socket.AF_UNIX)
    try:
        if kind == "regular-file":
            shared.write_bytes(b"")
        elif kind == "symlinked":
            (root / "r").mkdir(mode=0o700)
            shared.symlink_to(root / "r")
        elif kind != "absent":
            shared.mkdir()
            os.chmod(shared, 0o750 if kind == "group-readable" else 0o770)
        monkeypatch.setattr(guardian, "_shared_daemon_socket_directory", lambda: shared)
        alias = tmp_path / "app.sock"
        physical = expected_physical_socket_path(alias)
        assert physical.parent == shared
        if kind in {"symlinked", "group-readable", "group-writable"}:
            bound.bind(str(physical))
            os.chmod(physical, 0o600)
        alias.symlink_to(physical)
        assert inspect_socket_path(alias) == (None, failure)
        with pytest.raises(ValueError):
            _socket_identity(alias)
        with suppress(FileNotFoundError):
            alias.unlink()
    finally:
        bound.close()
        shutil.rmtree(root, ignore_errors=True)


def test_alias_rejects_a_physical_socket_that_is_not_private(tmp_path: Path) -> None:
    alias = tmp_path / "app.sock"
    with _alias_layout(alias, mode=0o666):
        assert inspect_socket_path(alias) == (None, SOCKET_PRIVACY_FAILURE)
        with pytest.raises(ValueError):
            _socket_identity(alias)
        with pytest.raises(ValidationError, match="not private to the current user"):
            _runtime_socket_identity(alias)
        # A socket whose mode the runtime has not tightened yet stays a startup candidate.
        assert _socket_is_candidate(alias) is True


@pytest.mark.parametrize("foreign", ["link", "directory", "socket"])
def test_alias_rejects_foreign_ownership_platform_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, foreign: str
) -> None:
    alias = tmp_path / "app.sock"
    physical = _derive_physical(alias)
    mine = os.getuid()
    other = mine + 1
    fakes = {
        alias: SimpleNamespace(
            st_mode=stat.S_IFLNK | 0o755,
            st_uid=other if foreign == "link" else mine,
            st_dev=1,
            st_ino=2,
            st_nlink=1,
        ),
        physical.parent: SimpleNamespace(
            st_mode=stat.S_IFDIR | 0o700,
            st_uid=other if foreign == "directory" else mine,
            st_dev=1,
            st_ino=3,
            st_nlink=2,
        ),
        physical: SimpleNamespace(
            st_mode=stat.S_IFSOCK | 0o600,
            st_uid=other if foreign == "socket" else mine,
            st_dev=1,
            st_ino=4,
            st_nlink=1,
        ),
    }
    real_readlink = os.readlink

    def fake_readlink(path: object, **kwargs: object) -> object:
        if os.fspath(path) == str(alias):  # type: ignore[arg-type]
            return str(physical)
        return real_readlink(path, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "lstat", lambda self: fakes[self])
    monkeypatch.setattr(os, "readlink", fake_readlink)
    assert inspect_socket_path(alias) == (None, SOCKET_PRIVACY_FAILURE)
    with pytest.raises(ValueError):
        _socket_identity(alias)


def test_repointed_alias_leaves_the_link_and_the_other_instance_untouched(tmp_path: Path) -> None:
    alias = tmp_path / "app.sock"
    second = tmp_path / "second" / "app.sock"
    second.parent.mkdir()
    with _alias_layout(alias) as physical, _alias_layout(second) as other_physical:
        identity = _socket_identity(alias)
        assert identity is not None
        alias.unlink()
        alias.symlink_to(other_physical)
        assert _same_socket(alias, identity) is False
        _remove_owned_socket(alias, identity)
        # The link no longer names what was captured, so ownership cannot be established and
        # both entries are left for an operator. The other instance keeps everything.
        assert alias.is_symlink()
        assert os.readlink(alias) == str(other_physical)
        assert other_physical.exists()
        assert physical.exists()


def test_recreated_alias_link_on_disk_is_rejected_and_preserved(tmp_path: Path) -> None:
    alias = tmp_path / "app.sock"
    staged = tmp_path / "staged.link"
    with _alias_layout(alias) as physical:
        identity = _socket_identity(alias)
        assert identity is not None
        assert identity.link_ino is not None
        # Stage a distinct symlink to the same target and rename it over the alias, so the link
        # is genuinely replaced on disk with a different inode rather than simulated in memory.
        staged.symlink_to(physical)
        staged_ino = staged.lstat().st_ino
        assert staged_ino != identity.link_ino
        os.rename(staged, alias)
        replaced = _socket_identity(alias)
        assert replaced is not None
        assert replaced.link_ino == staged_ino
        assert (replaced.dev, replaced.ino) == (identity.dev, identity.ino)
        assert replaced != identity
        assert _same_socket(alias, identity) is False
        _remove_owned_socket(alias, identity)
        # The replacement link and the socket it names are both preserved.
        assert alias.is_symlink()
        assert alias.lstat().st_ino == staged_ino
        assert os.readlink(alias) == str(physical)
        assert physical.exists()


def test_rebound_physical_socket_is_not_the_captured_socket(tmp_path: Path) -> None:
    alias = tmp_path / "app.sock"
    staging = tmp_path / "staging.sock"
    replacement = socket.socket(socket.AF_UNIX)
    with _alias_layout(alias) as physical:
        try:
            identity = _socket_identity(alias)
            assert identity is not None
            replacement.bind(str(staging))
            os.chmod(staging, 0o600)
            physical.unlink()
            os.rename(staging, physical)
            assert physical.lstat().st_ino != identity.ino
            assert _same_socket(alias, identity) is False
            _remove_owned_socket(alias, identity)
            assert alias.is_symlink()
            assert physical.exists()
        finally:
            replacement.close()
            with suppress(FileNotFoundError):
                staging.unlink()


def test_alias_cleanup_removes_exactly_the_captured_pair(tmp_path: Path) -> None:
    alias = tmp_path / "app.sock"
    physical = _derive_physical(alias)
    lock = physical.with_name(physical.name + ".lock")
    with _alias_layout(alias):
        identity = _socket_identity(alias)
        lock.write_bytes(b"")
        try:
            _remove_owned_socket(alias, identity)
            assert not os.path.lexists(alias)
            assert not physical.exists()
            assert lock.exists()
            assert physical.parent.is_dir()
        finally:
            with suppress(FileNotFoundError):
                lock.unlink()


def test_alias_cleanup_leaves_an_alias_whose_socket_is_already_gone(tmp_path: Path) -> None:
    """The alias-only removal path was withdrawn: such an alias is an operator's to resolve.

    The adapter cannot establish that the link is still the captured one at the moment it would
    unlink it, so it does not unlink it at all. A stale alias already refuses reuse at the next
    launch, which is where an operator sees it.
    """

    alias = tmp_path / "app.sock"
    with _alias_layout(alias) as physical:
        identity = _socket_identity(alias)
        physical.unlink()
        _remove_owned_socket(alias, identity)
        assert alias.is_symlink()
        assert os.readlink(alias) == str(physical)


def test_alias_cleanup_never_touches_another_live_instance(tmp_path: Path) -> None:
    alias = tmp_path / "app.sock"
    other_alias = tmp_path / "other" / "app.sock"
    other_alias.parent.mkdir()
    other_physical = _derive_physical(other_alias)
    other_physical.parent.mkdir(mode=0o700, exist_ok=True)
    holder = subprocess.Popen(  # noqa: S603 - fixture interpreter with literal source
        [
            sys.executable,
            "-c",
            "import os, socket, sys, time\n"
            "s = socket.socket(socket.AF_UNIX)\n"
            "s.bind(sys.argv[1])\n"
            "os.chmod(sys.argv[1], 0o600)\n"
            "os.symlink(sys.argv[1], sys.argv[2])\n"
            "sys.stdout.write('ready\\n')\n"
            "sys.stdout.flush()\n"
            "time.sleep(30)\n",
            str(other_physical),
            str(other_alias),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "ready"
        before = other_physical.lstat().st_ino
        with _alias_layout(alias) as physical:
            identity = _socket_identity(alias)
            assert identity is not None
            alias.unlink()
            alias.symlink_to(other_physical)
            _remove_owned_socket(alias, identity)
            assert alias.is_symlink()
            assert other_alias.is_symlink()
            assert other_physical.lstat().st_ino == before
            assert holder.poll() is None
            # Nothing of the other instance is touched, and the uncertain state leaves our own
            # captured socket in place too.
            assert physical.exists()
    finally:
        holder.terminate()
        holder.wait(timeout=10)
        with suppress(FileNotFoundError):
            other_alias.unlink()
        with suppress(FileNotFoundError):
            other_physical.unlink()


def test_dangling_alias_before_launch_refuses_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile, _ = _make_runtime(tmp_path, monkeypatch)
    alias = profile.socket_path
    alias.symlink_to(_derive_physical(alias))
    runtime = OwnedRuntime(profile)
    with pytest.raises(ValidationError, match="refusing to reuse an existing app-server socket"):
        asyncio.run(runtime.ensure_started())
    assert alias.is_symlink()
    assert runtime.process is None


def test_socket_candidate_tracks_only_admissible_shapes(tmp_path: Path) -> None:
    alias = tmp_path / "app.sock"
    with _alias_layout(alias) as physical:
        assert _socket_is_candidate(alias) is True
        alias.unlink()
        alias.symlink_to(physical.with_name("0" * 64))
        assert _socket_is_candidate(alias) is False
    assert _socket_is_candidate(tmp_path / "absent.sock") is False


def test_owned_runtime_admits_the_fixture_alias_and_cleans_both_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asyncio.run(_test_owned_runtime_admits_the_fixture_alias_and_cleans_both_entries(tmp_path, monkeypatch))


async def _test_owned_runtime_admits_the_fixture_alias_and_cleans_both_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile, _ = _make_runtime(tmp_path, monkeypatch)
    monkeypatch.setenv("BRIDGE_FIXTURE_ALIAS", "1")
    physical = _derive_physical(profile.socket_path)
    runtime = OwnedRuntime(profile)
    try:
        await runtime.ensure_started()
        assert profile.socket_path.is_symlink()
        assert os.readlink(profile.socket_path) == str(physical)
        assert runtime.connect_path == physical
        identity = runtime._socket_identity
        assert identity is not None
        assert identity.link_target == str(physical)
        assert (identity.dev, identity.ino) == (physical.lstat().st_dev, physical.lstat().st_ino)
        await runtime.close()
        assert not os.path.lexists(profile.socket_path)
        assert not physical.exists()
    finally:
        with suppress(FileNotFoundError):
            profile.socket_path.unlink()
        with suppress(FileNotFoundError):
            physical.unlink()


@pytest.mark.installed_runtime
@pytest.mark.skipif(not _CODEX_0156.is_file(), reason="Codex CLI 0.156.0 is not installed")
def test_installed_codex_0156_publishes_an_admitted_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asyncio.run(_test_installed_codex_0156_publishes_an_admitted_alias(tmp_path, monkeypatch))


async def _test_installed_codex_0156_publishes_an_admitted_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir(mode=0o700)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    worktree = tmp_path / "worktree"
    worktree.mkdir(mode=0o700)
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    reported = subprocess.run(  # noqa: S603 - operator-installed release binary
        [str(_CODEX_0156), "--version"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout.strip()
    profile = OperatorProfile(
        runtime_path=_CODEX_0156,
        runtime_version=reported,
        runtime_sha256=hashlib.sha256(_CODEX_0156.read_bytes()).hexdigest(),
        socket_path=private / "app.sock",
        allowed_worktree_roots=(worktree,),
        allowed_models=("gpt-6-luna",),
        allowed_sandboxes=("read-only",),
        data_root=data,
    )
    physical = _derive_physical(profile.socket_path)
    runtime = OwnedRuntime(profile)
    try:
        await runtime.ensure_started()
        assert os.readlink(profile.socket_path) == str(physical)
        assert runtime.connect_path == physical
        assert physical.lstat().st_mode & 0o777 == 0o600
        assert physical.parent.lstat().st_mode & 0o777 == 0o700
        await runtime.close()
        assert not os.path.lexists(profile.socket_path)
        assert not physical.exists()
    finally:
        with suppress(FileNotFoundError):
            profile.socket_path.unlink()
        with suppress(FileNotFoundError):
            physical.unlink()


def test_direct_socket_permission_transition_is_still_the_same_socket(tmp_path: Path) -> None:
    """Regression for P2-AG-395-003: 0.154.0 binds, then tightens the mode, on one socket."""

    path = tmp_path / "direct.sock"
    bound = socket.socket(socket.AF_UNIX)
    try:
        bound.bind(str(path))
        os.chmod(path, 0o700)
        captured = _socket_identity(path)
        assert captured is not None
        assert captured.mode == 0o700
        os.chmod(path, 0o600)
        current = _socket_identity(path)
        assert current is not None
        assert current.mode == 0o600
        assert (current.dev, current.ino) == (captured.dev, captured.ino)
        # The runtime keeps the historical device-and-inode rule for a direct socket.
        assert _same_socket(path, captured) is True
        assert _same_socket(path, (captured.dev, captured.ino)) is True
        # Cleanup through the runtime path still removes the socket it owns.
        _remove_owned_endpoint(path, captured)
        assert not path.exists()
    finally:
        bound.close()
        with suppress(FileNotFoundError):
            path.unlink()


def test_direct_socket_rebind_is_not_the_same_socket(tmp_path: Path) -> None:
    """The restored rule still rejects a different socket at the same direct path."""

    path = tmp_path / "direct.sock"
    staging = tmp_path / "staging.sock"
    first = socket.socket(socket.AF_UNIX)
    replacement = socket.socket(socket.AF_UNIX)
    try:
        first.bind(str(path))
        os.chmod(path, 0o600)
        captured = _socket_identity(path)
        assert captured is not None
        replacement.bind(str(staging))
        os.chmod(staging, 0o600)
        path.unlink()
        os.rename(staging, path)
        assert path.lstat().st_ino != captured.ino
        assert _same_socket(path, captured) is False
        _remove_owned_endpoint(path, captured)
        assert path.exists()
    finally:
        first.close()
        replacement.close()
        for leftover in (path, staging):
            with suppress(FileNotFoundError):
                leftover.unlink()


def test_alias_cleanup_leaves_a_substituted_socket_under_the_captured_alias(
    tmp_path: Path,
) -> None:
    """A socket that is not the captured one under an intact alias is uncertain, so it stays."""

    alias = tmp_path / "app.sock"
    staging = tmp_path / "staging.sock"
    replacement = socket.socket(socket.AF_UNIX)
    with _alias_layout(alias) as physical:
        try:
            identity = _socket_identity(alias)
            assert identity is not None
            replacement.bind(str(staging))
            os.chmod(staging, 0o600)
            physical.unlink()
            os.rename(staging, physical)
            assert physical.lstat().st_ino != identity.ino
            _remove_owned_socket(alias, identity)
            assert alias.is_symlink()
            assert physical.exists()
        finally:
            replacement.close()
            with suppress(FileNotFoundError):
                staging.unlink()


def test_alias_cleanup_is_not_atomic_and_the_limitation_is_executed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Execute the documented residual: substitution inside the check-to-unlink window.

    The correction narrows cleanup to the coherent captured pair, but comparing an identity and
    then unlinking a path is two operations. This test drives a substitution between them and
    records what actually happens, so the documented limitation cannot silently change.
    """

    alias = tmp_path / "app.sock"
    staging = tmp_path / "staging.sock"
    replacement = socket.socket(socket.AF_UNIX)
    real_bound_matches = guardian._bound_socket_matches
    with _alias_layout(alias) as physical:
        try:
            identity = _socket_identity(alias)
            assert identity is not None
            replacement.bind(str(staging))
            os.chmod(staging, 0o600)

            def substitute_after_check(target: Path, expected: SocketIdentity) -> bool:
                verdict = real_bound_matches(target, expected)
                if verdict:
                    physical.unlink()
                    os.rename(staging, physical)
                return verdict

            monkeypatch.setattr(guardian, "_bound_socket_matches", substitute_after_check)
            substituted_ino = staging.lstat().st_ino
            _remove_owned_socket(alias, identity)
            # The substituted socket is removed: the identity check cannot bind the later
            # unlink to the object it inspected. This is the limitation the docs state.
            assert not physical.exists()
            assert substituted_ino != identity.ino
        finally:
            replacement.close()
            for leftover in (staging, physical):
                with suppress(FileNotFoundError):
                    leftover.unlink()


def test_alias_cleanup_link_replacement_in_the_pair_branch_is_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Execute the remaining coherent-pair residual on the link rather than the socket.

    The alias is compared once and unlinked later. This drives a link replacement between those
    two operations and records that the replacement is removed. The race is narrowed to this one
    branch and disclosed; it is not fixed, and this test labels the behaviour rather than
    asserting that it is prevented.
    """

    alias = tmp_path / "app.sock"
    staged = tmp_path / "staged.link"
    decoy = tmp_path / "decoy.sock"
    real_bound_matches = guardian._bound_socket_matches
    with _alias_layout(alias) as physical:
        try:
            identity = _socket_identity(alias)
            assert identity is not None
            staged.symlink_to(decoy)
            replacement_ino = staged.lstat().st_ino
            assert replacement_ino != identity.link_ino

            def replace_link_after_check(target: Path, expected: SocketIdentity) -> bool:
                verdict = real_bound_matches(target, expected)
                if verdict:
                    os.rename(staged, alias)
                return verdict

            monkeypatch.setattr(guardian, "_bound_socket_matches", replace_link_after_check)
            _remove_owned_socket(alias, identity)
            # The replacement link is gone: comparing the alias does not bind the later unlink
            # to the link that was compared. The captured socket is removed in the same branch.
            assert not os.path.lexists(alias)
            assert not physical.exists()
        finally:
            for leftover in (staged, decoy, alias, physical):
                with suppress(FileNotFoundError):
                    leftover.unlink()
