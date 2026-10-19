"""Nonblocking adapter-lifetime ownership of one private local task store."""

from __future__ import annotations

import fcntl
import os
import stat
from typing import TYPE_CHECKING

from .errors import ValidationError
from .profile import _has_symlink_component, _require_private

if TYPE_CHECKING:
    from pathlib import Path


class DataRootOwnership:
    """Keep the persistent lock inode locked until owned work has stopped."""

    def __init__(self, root: Path) -> None:
        if not root.is_absolute() or _has_symlink_component(root):
            raise ValidationError("data_root must be an absolute non-symlink path")
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not root.is_dir():
            raise ValidationError("data_root must be a real directory")
        _require_private(root, "data root")
        self.root = root.resolve(strict=True)
        self._fd: int | None = None
        path = self.root / ".adapter-owner.lock"
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        except OSError as exc:
            raise ValidationError("cannot open private data-root ownership lock") from exc
        try:
            os.set_inheritable(fd, False)
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
                or info.st_nlink != 1
            ):
                raise ValidationError("data-root ownership lock must be a private regular file")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValidationError("profile data root is already owned by another adapter") from exc
            except OSError as exc:
                raise ValidationError("data-root ownership locking is unavailable") from exc
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd

    def require(self, root: Path) -> None:
        try:
            matches = self._fd is not None and root.resolve(strict=True) == self.root
        except (OSError, RuntimeError) as exc:
            raise ValidationError("adapter requires live ownership of its data root") from exc
        if not matches:
            raise ValidationError("adapter requires live ownership of its data root")

    def close(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            # Never unlink: contenders must continue to lock the same inode.
            os.close(fd)
