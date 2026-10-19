"""Small session-scoped owner for one native app-server child.

This module is intentionally standalone.  The adapter passes it one inherited
read end of an anonymous pipe; EOF is the only normal liveness signal.
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import os
import select
import signal
import stat
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Callable

_STARTUP_SECONDS = 5.0
_TERM_SECONDS = 5.0
_KILL_SECONDS = 5.0
_POLL_SECONDS = 0.05

SOCKET_TYPE_FAILURE = "type"
SOCKET_PRIVACY_FAILURE = "private"


class SocketIdentity(NamedTuple):
    """One admitted owned socket, plus the alias link when the runtime publishes one.

    The first four fields always describe the socket that is actually bound.  For a direct
    socket that is the requested path itself; for a deterministic alias it is the physical
    socket the alias names, and the link fields carry the alias link's own identity.
    """

    dev: int
    ino: int
    uid: int
    mode: int
    link_dev: int | None = None
    link_ino: int | None = None
    link_target: str | None = None


def _shared_daemon_socket_directory() -> Path:
    """Mirror the runtime's fixed per-user rendezvous directory for owned sockets."""

    # The runtime fixes this root and must not depend on TMPDIR, HOME, or CODEX_HOME; the
    # directory's owner and exact 0700 mode are verified before any socket there is admitted.
    return Path(os.path.realpath("/tmp")) / f"codex-daemon-{os.geteuid()}"  # noqa: S108


def expected_physical_socket_path(alias: Path) -> Path:
    """Derive the only physical socket an alias at this path may name."""

    digest = hashlib.sha256(
        os.fsencode(os.path.join(os.path.realpath(alias.parent), alias.name))
    ).hexdigest()
    return _shared_daemon_socket_directory() / digest


def inspect_socket_path(path: Path) -> tuple[SocketIdentity | None, str | None]:
    """Admit exactly a private direct socket or the runtime's deterministic alias."""

    try:
        info = path.lstat()
    except FileNotFoundError:
        return (None, None)
    except OSError:
        return (None, SOCKET_TYPE_FAILURE)
    if stat.S_ISLNK(info.st_mode):
        return _inspect_alias(path, info)
    if not stat.S_ISSOCK(info.st_mode):
        return (None, SOCKET_TYPE_FAILURE)
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        return (None, SOCKET_PRIVACY_FAILURE)
    return (SocketIdentity(info.st_dev, info.st_ino, info.st_uid, info.st_mode & 0o777), None)


def _inspect_alias(path: Path, link: os.stat_result) -> tuple[SocketIdentity | None, str | None]:
    """Admit one alias only when it names the derived physical socket in a private directory."""

    if link.st_uid != os.getuid():
        return (None, SOCKET_PRIVACY_FAILURE)
    try:
        target = os.readlink(path)
    except OSError:
        return (None, SOCKET_TYPE_FAILURE)
    physical = expected_physical_socket_path(path)
    if target != str(physical):
        return (None, SOCKET_TYPE_FAILURE)
    try:
        directory = physical.parent.lstat()
    except OSError:
        return (None, SOCKET_TYPE_FAILURE)
    if stat.S_ISLNK(directory.st_mode) or not stat.S_ISDIR(directory.st_mode):
        return (None, SOCKET_TYPE_FAILURE)
    if directory.st_uid != os.geteuid() or directory.st_mode & 0o777 != 0o700:
        return (None, SOCKET_PRIVACY_FAILURE)
    try:
        bound = physical.lstat()
    except OSError:
        return (None, SOCKET_TYPE_FAILURE)
    if stat.S_ISLNK(bound.st_mode) or not stat.S_ISSOCK(bound.st_mode):
        return (None, SOCKET_TYPE_FAILURE)
    if bound.st_uid != os.getuid() or bound.st_mode & 0o077:
        return (None, SOCKET_PRIVACY_FAILURE)
    return (
        SocketIdentity(
            bound.st_dev,
            bound.st_ino,
            bound.st_uid,
            bound.st_mode & 0o777,
            link.st_dev,
            link.st_ino,
            target,
        ),
        None,
    )


def _validate_read_pipe(fd: int) -> None:
    if fd <= 2:
        raise ValueError("guardian read descriptor must not be stdio")
    try:
        info = os.fstat(fd)
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    except (OSError, ValueError) as exc:
        raise ValueError("guardian read descriptor is invalid") from exc
    anonymous = info.st_nlink == 0
    if sys.platform == "linux":
        # Linux anonymous pipes have nlink=1, just like a named FIFO. The
        # kernel's procfs descriptor target distinguishes them without opening
        # or trusting a filesystem FIFO supplied by the caller.
        anonymous = info.st_nlink == 1 and os.readlink(f"/proc/self/fd/{fd}") == f"pipe:[{info.st_ino}]"
    if not stat.S_ISFIFO(info.st_mode) or not anonymous:
        raise ValueError("guardian read descriptor is not an anonymous pipe")
    if flags & os.O_ACCMODE != os.O_RDONLY:
        raise ValueError("guardian read descriptor is not readable")


def _pipe_event(fd: int) -> str | None:
    try:
        ready, _, _ = select.select([fd], [], [], 0)
    except (OSError, ValueError):
        return "invalid"
    if not ready:
        return None
    try:
        return "eof" if os.read(fd, 4096) == b"" else "data"
    except OSError:
        return "invalid"


def _socket_identity(path: Path) -> SocketIdentity | None:
    identity, failure = inspect_socket_path(path)
    if failure == SOCKET_TYPE_FAILURE:
        raise ValueError("guardian socket path is not an admitted app-server socket")
    if failure == SOCKET_PRIVACY_FAILURE:
        raise ValueError("guardian socket is not private to the current user")
    return identity


def _alias_matches(path: Path, identity: SocketIdentity) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    if not stat.S_ISLNK(info.st_mode):
        return False
    try:
        target = os.readlink(path)
    except OSError:
        return False
    return (info.st_dev, info.st_ino, target) == (
        identity.link_dev,
        identity.link_ino,
        identity.link_target,
    )


def _bound_socket_matches(physical: Path, identity: SocketIdentity) -> bool:
    try:
        info = physical.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISSOCK(info.st_mode):
        return False
    return (info.st_dev, info.st_ino, info.st_uid, info.st_mode & 0o777) == (
        identity.dev,
        identity.ino,
        identity.uid,
        identity.mode,
    )


def _remove_owned_socket(
    path: Path, identity: SocketIdentity | tuple[int, int, int, int] | None
) -> None:
    """Remove the captured pair only while both entries are still exactly what was captured.

    Ownership is established by comparing identities and each removal is a separate unlink on a
    path, so this is a best-effort check and is not atomic.  That race is narrowed here, not
    fixed: in the one remaining removal branch either the alias link or the socket can be
    replaced between the comparison and the unlink, and the replacement is then removed, and a
    socket that reuses the captured inode number is indistinguishable from the captured one.
    Accidental same-user interference has the same effect as deliberate substitution.

    An alias whose socket is already gone is left in place for explicit operator handling: the
    adapter cannot establish that the link is still the captured one at the moment it would
    unlink it, and a stale alias already refuses reuse at the next launch.
    """

    if identity is None:
        return
    if not isinstance(identity, SocketIdentity) or identity.link_target is None:
        current, _failure = inspect_socket_path(path)
        if current is not None and current == identity:
            with suppress(FileNotFoundError):
                path.unlink()
        return
    physical = Path(identity.link_target)
    if not _alias_matches(path, identity):
        # The alias is not the link that was captured, so this instance cannot establish
        # ownership of either entry; another instance may already own them.
        return
    if _bound_socket_matches(physical, identity):
        # Both entries matched a moment ago. Neither unlink is bound to that observation.
        with suppress(FileNotFoundError):
            path.unlink()
        with suppress(FileNotFoundError):
            physical.unlink()
        return
    # The socket is absent, or is not the captured one: leave both entries for an operator.


def _leader_state(pid: int) -> bool | None:
    """Observe direct-child exit without reaping its process-group leader."""

    try:
        result = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    except (ChildProcessError, OSError):
        return None
    return result is not None and result.si_pid == pid


def _wait_for_leader_exit(
    pid: int, timeout: float, observe: Callable[[], None] | None = None
) -> bool | None:
    deadline = time.monotonic() + timeout
    while True:
        if observe is not None:
            observe()
        state = _leader_state(pid)
        if state is not False:
            return state
        if time.monotonic() >= deadline:
            return False
        time.sleep(_POLL_SECONDS)


def _group_probe(pgid: int) -> bool | None:
    """Probe group membership without signalling a process in the group."""

    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # A post-reap EPERM is ambiguous: it may be a denied live group.
        # The caller must fail closed rather than infer that the group is empty.
        return None
    except OSError:
        return None
    return True


def _terminate_child(child: subprocess.Popen[bytes], socket_path: Path,
                     socket_identity: SocketIdentity | None) -> bool:
    def observe_socket() -> None:
        nonlocal socket_identity
        if socket_identity is None:
            socket_identity = _capture_socket(socket_path)

    state = _leader_state(child.pid)
    if state is None:
        return False
    if state is False:
        with suppress(OSError):
            os.killpg(child.pid, signal.SIGTERM)
        state = _wait_for_leader_exit(child.pid, _TERM_SECONDS, observe_socket)
        if state is None:
            return False
        if state is False:
            with suppress(OSError):
                os.killpg(child.pid, signal.SIGKILL)
            state = _wait_for_leader_exit(child.pid, _KILL_SECONDS, observe_socket)
            if state is None or state is False:
                return False
    # Keep the unreaped leader as the process-group identity while requesting
    # final cleanup. This catches descendants that outlive their leader too.
    # On macOS, an empty group whose leader is an unreaped zombie can report
    # EPERM for killpg; only accept that platform quirk after reaping proves
    # that no group member remains. A live denied group remains a failure.
    observe_socket()
    final_signal_denied = False
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError as exc:
        if sys.platform != "darwin" or exc.errno != errno.EPERM:
            return False
        final_signal_denied = True
    except OSError:
        return False

    # Reap only after the final group signal while the leader identity is
    # reserved. Descendants receive the same signal without a PID lookup.
    try:
        child.wait()
    except (ChildProcessError, OSError):
        return False
    if final_signal_denied and (sys.platform != "darwin" or _group_probe(child.pid) is not False):
        return False
    if socket_identity is None and (
        _path_is_present(socket_path)
        or _path_is_present(expected_physical_socket_path(socket_path))
    ):
        return False
    _remove_owned_socket(socket_path, socket_identity)
    return True


def _capture_socket(path: Path) -> SocketIdentity | None:
    try:
        return _socket_identity(path)
    except (OSError, ValueError):
        return None


def _socket_is_candidate(path: Path) -> bool:
    """Report whether the path may still become an admitted socket during startup."""

    try:
        info = path.lstat()
    except OSError:
        return False
    if not stat.S_ISLNK(info.st_mode):
        return stat.S_ISSOCK(info.st_mode)
    try:
        target = os.readlink(path)
    except OSError:
        return False
    if target != str(expected_physical_socket_path(path)):
        return False
    try:
        bound = Path(target).lstat()
    except OSError:
        return False
    return stat.S_ISSOCK(bound.st_mode) and not stat.S_ISLNK(bound.st_mode)


def _path_is_present(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def _run(runtime_path: Path, socket_path: Path, read_fd: int) -> int:
    _validate_read_pipe(read_fd)
    if not runtime_path.is_absolute() or not socket_path.is_absolute():
        raise ValueError("guardian paths must be absolute")
    if _pipe_event(read_fd) is not None:
        return 0

    stop_requested = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True

    old_term = signal.signal(signal.SIGTERM, request_stop)
    old_int = signal.signal(signal.SIGINT, request_stop)
    child: subprocess.Popen[bytes] | None = None
    socket_identity: SocketIdentity | None = None
    cleanup_attempted = False

    def cleanup() -> bool:
        nonlocal cleanup_attempted
        cleanup_attempted = True
        if child is None:
            raise RuntimeError("guardian child was not started")
        return _terminate_child(child, socket_path, socket_identity)

    try:
        child = subprocess.Popen(  # noqa: S603 - runtime identity is verified by the adapter
            [str(runtime_path), "app-server", "--listen", f"unix://{socket_path}"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
        # Catch adapter death during the small launch window before entering
        # the regular monitoring loop.
        if stop_requested or _pipe_event(read_fd) is not None:
            socket_identity = _capture_socket(socket_path)
            return 0 if cleanup() else 1
        deadline = time.monotonic() + _STARTUP_SECONDS
        while True:
            if _leader_state(child.pid) is True:
                # A child that exits before readiness is a failed helper
                # startup; the runtime must not silently retry it.
                socket_identity = _capture_socket(socket_path)
                cleanup()
                return 1
            event = _pipe_event(read_fd)
            if stop_requested or event is not None:
                socket_identity = _capture_socket(socket_path)
                return 0 if cleanup() else 1
            try:
                current_socket = _socket_identity(socket_path)
            except (OSError, ValueError):
                if not _socket_is_candidate(socket_path):
                    return 0 if cleanup() else 1
                current_socket = None
            if current_socket is not None:
                socket_identity = current_socket
                break
            if time.monotonic() >= deadline:
                return 0 if cleanup() else 1
            time.sleep(_POLL_SECONDS)

        while _leader_state(child.pid) is False:
            event = _pipe_event(read_fd)
            if stop_requested or event is not None:
                return 0 if cleanup() else 1
            time.sleep(_POLL_SECONDS)
        return 0 if cleanup() else 1
    finally:
        if child is not None and _leader_state(child.pid) is False and not cleanup_attempted:
            cleanup()
        signal.signal(signal.SIGTERM, old_term)
        signal.signal(signal.SIGINT, old_int)
        os.close(read_fd)


def main() -> int:
    parser = argparse.ArgumentParser(description="Codex bridge private app-server child guardian")
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--read-fd", type=int, required=True)
    args = parser.parse_args()
    try:
        return _run(args.runtime, args.socket, args.read_fd)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 1


if __name__ == "__main__":
    sys.exit(main())
