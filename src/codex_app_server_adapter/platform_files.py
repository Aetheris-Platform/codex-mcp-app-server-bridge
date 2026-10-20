"""Platform-specific private filesystem and locking primitives.

Windows uses ACLs and kernel byte-range locks, never chmod as an ACL substitute.
Unix callers retain their existing flock and mode/uid checks.
"""

from __future__ import annotations

import os
import stat
from typing import TYPE_CHECKING

from .errors import ValidationError

if TYPE_CHECKING:
    from pathlib import Path


def is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    if os.name == "nt":
        try:
            return bool(path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
        except FileNotFoundError:
            return False
    return False


def require_private_windows(path: Path, label: str) -> None:
    import pywintypes

    try:
        _check_windows_acl(path, label)
    except pywintypes.error as exc:
        raise ValidationError(f"cannot verify private Windows ACL for {label}") from exc


def _check_windows_acl(path: Path, label: str) -> None:
    import win32api
    import win32con
    import win32security

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        user = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        token.Close()
    descriptor = win32security.GetNamedSecurityInfo(
        str(path), win32security.SE_FILE_OBJECT,
        win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION,
    )
    if descriptor.GetSecurityDescriptorOwner() != user:
        raise ValidationError(f"{label} must be owned by the current Windows user")
    acl = descriptor.GetSecurityDescriptorDacl()
    if acl is None:
        raise ValidationError(f"{label} must have a private Windows DACL")
    allowed = {win32security.ConvertSidToStringSid(user), "S-1-5-18", "S-1-5-32-544"}
    for index in range(acl.GetAceCount()):
        ace = acl.GetAce(index)
        if ace[0][0] == win32security.ACCESS_DENIED_ACE_TYPE:
            continue
        if (ace[0][0] != win32security.ACCESS_ALLOWED_ACE_TYPE
                or win32security.ConvertSidToStringSid(ace[2]) not in allowed):
            raise ValidationError(f"{label} grants access outside the user, SYSTEM and administrators")


def make_private_directory(path: Path, *, parents: bool = False) -> None:
    """Protect a newly created directory before any private content is written."""
    try:
        path.mkdir(mode=0o700, parents=parents, exist_ok=False)
    except FileExistsError:
        if not path.is_dir():
            raise
        return  # Never rewrite an existing root's permissions, including a startup race.
    if os.name != "nt":
        return
    import win32api
    import win32con
    import win32security

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        user = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        token.Close()
    sid = win32security.ConvertSidToStringSid(user)
    descriptor = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        f"D:P(A;OICI;FA;;;{sid})(A;OICI;FA;;;SY)", win32security.SDDL_REVISION_1,
    )
    win32security.SetNamedSecurityInfo(
        str(path), win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None, None, descriptor.GetSecurityDescriptorDacl(), None,
    )


def lock_file(fd: int, *, nonblocking: bool = False) -> None:
    if os.name != "nt":
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0))
        return
    import msvcrt

    import pywintypes
    import win32con
    import win32file

    flags = win32con.LOCKFILE_EXCLUSIVE_LOCK
    if nonblocking:
        flags |= win32con.LOCKFILE_FAIL_IMMEDIATELY
    try:
        win32file.LockFileEx(msvcrt.get_osfhandle(fd), flags, 1, 0, pywintypes.OVERLAPPED())
    except pywintypes.error as exc:
        if exc.winerror == 33:
            raise BlockingIOError("file is locked") from exc
        raise OSError("Windows file locking failed") from exc


def unlock_file(fd: int) -> None:
    if os.name != "nt":
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)
        return
    import msvcrt

    import pywintypes
    import win32file

    win32file.UnlockFileEx(msvcrt.get_osfhandle(fd), 1, 0, pywintypes.OVERLAPPED())
