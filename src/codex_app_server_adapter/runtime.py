"""Session-scoped owned app-server process and runnable service construction."""

from __future__ import annotations

import asyncio
import errno
import os
import signal
import sys
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

from .errors import ProtocolError, ValidationError
from .guardian import (
    SOCKET_PRIVACY_FAILURE,
    SOCKET_TYPE_FAILURE,
    SocketIdentity,
    _path_is_present,
    _remove_owned_socket,
    inspect_socket_path,
)
from .ownership import DataRootOwnership
from .protocol import JsonRpcClient, UnixWebSocketTransport
from .service import AdapterService
from .store import TaskStore

_TERM_SECONDS = 5.0
_KILL_SECONDS = 5.0

if TYPE_CHECKING:
    from .profile import OperatorProfile


class OwnedRuntime:
    """Own one verified app-server process for the adapter session."""

    def __init__(self, profile: OperatorProfile) -> None:
        self.profile = profile
        self._lock = asyncio.Lock()
        self.process: asyncio.subprocess.Process | None = None
        self._socket_identity: SocketIdentity | None = None
        self._liveness_write_fd: int | None = None
        self._guardian_failed = False

    async def ensure_started(self) -> None:
        async with self._lock:
            if self._guardian_failed:
                raise ProtocolError("guardian state is uncertain; runtime recreation is refused")
            if self.process is not None and self.process.returncode is not None:
                clean_exit = self.process.returncode == 0
                if not clean_exit:
                    self._guardian_failed = True
                self._close_liveness_pipe()
                identity = self._socket_identity
                self.process = None
                self._socket_identity = None
                if clean_exit and identity is not None:
                    _remove_owned_endpoint(self.profile.socket_path, identity)
                if not clean_exit:
                    raise ProtocolError("guardian exited abnormally; runtime recreation is refused")
            if self.process is not None and self.process.returncode is None:
                if self._same_socket():
                    return
                raise ProtocolError("owned app-server socket identity changed")
            if _path_is_present(self.profile.socket_path):
                raise ValidationError("refusing to reuse an existing app-server socket")
            await asyncio.to_thread(self.profile.verify_runtime)
            read_fd, write_fd = os.pipe()
            os.set_inheritable(read_fd, True)
            os.set_inheritable(write_fd, False)
            guardian_path = Path(__file__).with_name("guardian.py")
            try:
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    str(guardian_path),
                    "--runtime",
                    str(self.profile.runtime_path),
                    "--socket",
                    str(self.profile.socket_path),
                    "--read-fd",
                    str(read_fd),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    close_fds=True,
                    pass_fds=(read_fd,),
                    start_new_session=True,
                )
            except (OSError, ValueError, asyncio.CancelledError):
                os.close(read_fd)
                os.close(write_fd)
                raise
            else:
                os.close(read_fd)
            self._liveness_write_fd = write_fd
            self.process = process
            try:
                await self._wait_for_socket(process)
                self._socket_identity = _socket_identity(self.profile.socket_path)
            except asyncio.CancelledError:
                await self._stop_guardian(process)
                if process.returncode not in (None, 0):
                    self._guardian_failed = True
                self.process = None
                raise
            except (OSError, ProtocolError, ValidationError, TimeoutError):
                await self._stop_guardian(process)
                if process.returncode not in (None, 0):
                    self._guardian_failed = True
                self.process = None
                raise

    async def close(self) -> None:
        async with self._lock:
            process = self.process
            identity = self._socket_identity
            self.process = None
            self._socket_identity = None
            self._close_liveness_pipe()
            if process is not None:
                try:
                    await asyncio.wait_for(process.wait(), timeout=(_TERM_SECONDS + _KILL_SECONDS + 1.0))
                except TimeoutError as exc:
                    self._guardian_failed = True
                    await self._terminate(process)
                    raise ProtocolError("guardian cleanup timed out") from exc
                if process.returncode != 0:
                    self._guardian_failed = True
                    raise ProtocolError("guardian cleanup failed")
            if identity is not None:
                _remove_owned_endpoint(self.profile.socket_path, identity)

    async def _wait_for_socket(self, process: asyncio.subprocess.Process) -> None:
        async with asyncio.timeout(5.0):
            while not _path_is_present(self.profile.socket_path):
                if process.returncode is not None:
                    raise ProtocolError("app-server exited before opening its socket")
                await asyncio.sleep(0.02)
        if process.returncode is not None:
            raise ProtocolError("app-server exited before handshake")
        _socket_identity(self.profile.socket_path)

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except (OSError, TimeoutError):
            with suppress(OSError):
                os.killpg(process.pid, signal.SIGKILL)
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except TimeoutError as kill_exc:
                raise ProtocolError("guardian termination timed out") from kill_exc
            except OSError as kill_exc:
                raise ProtocolError("guardian termination failed") from kill_exc

    async def _stop_guardian(self, process: asyncio.subprocess.Process) -> None:
        self._close_liveness_pipe()
        try:
            await asyncio.wait_for(process.wait(), timeout=(_TERM_SECONDS + _KILL_SECONDS + 1.0))
        except TimeoutError:
            self._guardian_failed = True
            await self._terminate(process)

    def _close_liveness_pipe(self) -> None:
        fd = self._liveness_write_fd
        self._liveness_write_fd = None
        if fd is not None:
            with suppress(OSError):
                os.close(fd)

    def _same_socket(self) -> bool:
        identity = self._socket_identity
        return identity is not None and _same_socket(self.profile.socket_path, identity)

    def verify_owned_endpoint(self) -> None:
        """Re-derive the identity of the captured path, for callers about to send traffic.

        This inspects the path, not the connection, and it does not authenticate the peer.  A
        substitution that is still in place when this runs is refused.  One that is reverted
        before this runs is not detected, because the connection was already established to
        whatever the path named at connect time.
        """

        if not self._same_socket():
            raise ProtocolError("owned app-server socket identity changed")

    @property
    def connect_path(self) -> Path:
        """Return the verified socket to connect to, never a re-resolved alias."""

        identity = self._socket_identity
        if identity is not None and identity.link_target is not None:
            return Path(identity.link_target)
        return self.profile.socket_path


class OwnedAppServerClient(JsonRpcClient):
    """Connect one task to the session-owned native app-server process."""

    def __init__(self, runtime: OwnedRuntime) -> None:
        transport = UnixWebSocketTransport(runtime.profile.socket_path)
        super().__init__(transport)
        self.runtime = runtime
        self.unix_transport = transport

    async def connect(self) -> None:
        await self.runtime.ensure_started()
        self.unix_transport.socket_path = self.runtime.connect_path
        await super().connect()

    async def _admit_connection(self) -> None:
        """Refuse an endpoint whose captured path identity no longer holds.

        This is a path check rather than peer authentication, so it refuses a substitution that
        is still in place and does not detect one reverted before the check runs.
        """

        self.runtime.verify_owned_endpoint()

    async def close(self) -> None:
        await super().close()


def create_owned_service(profile: OperatorProfile) -> AdapterService:
    """Build the inactive-by-default service from one explicit operator profile."""

    profile.validate()
    ownership = DataRootOwnership(profile.data_root)
    try:
        profile.verify_runtime()
        store = TaskStore(profile.data_root)
        runtime = OwnedRuntime(profile)
        return AdapterService(
            profile,
            store,
            lambda: OwnedAppServerClient(runtime),
            ownership=ownership,
            shutdown_callback=runtime.close,
        )
    except BaseException:
        ownership.close()
        raise


def _socket_identity(path: Path) -> SocketIdentity:
    identity, failure = inspect_socket_path(path)
    if failure == SOCKET_TYPE_FAILURE:
        raise ValidationError("app-server path is not a Unix socket")
    if failure == SOCKET_PRIVACY_FAILURE:
        raise ValidationError("app-server socket is not private to the current user")
    if identity is None:
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), str(path))
    return identity


def _identity_matches(
    current: SocketIdentity, expected: SocketIdentity | tuple[int, ...]
) -> bool:
    """Compare a live endpoint with a captured one.

    An alias endpoint compares its complete identity, the link included, because the alias is
    what makes the socket reachable.  A direct socket keeps the historical device-and-inode
    comparison, so 0.154.0 behaviour is not changed by alias support: a permission transition
    on one socket is still the same socket.
    """

    if isinstance(expected, SocketIdentity) and expected.link_target is not None:
        return current == expected
    if len(expected) < 2:
        return False
    return (current.dev, current.ino) == (expected[0], expected[1])


def _same_socket(path: Path, identity: SocketIdentity | tuple[int, ...]) -> bool:
    try:
        return _identity_matches(_socket_identity(path), identity)
    except (OSError, ValidationError):
        return False


def _remove_owned_endpoint(path: Path, identity: SocketIdentity) -> None:
    """Remove the owned endpoint, keeping the historical rule for a direct socket."""

    if identity.link_target is None:
        if _same_socket(path, identity):
            with suppress(OSError):
                path.unlink()
        return
    _remove_owned_socket(path, identity)
