"""Private Windows child pipes with atomic Job Object membership.

Imported only on Windows. No PID-based cleanup, shell, listener or attach route.
PROC_THREAD_ATTRIBUTE_JOB_LIST assigns the child before its first instruction;
closing the adapter's sole job handle kills the child and its descendants.
"""

from __future__ import annotations

import ctypes
import msvcrt
import os
import subprocess
import time
from ctypes import wintypes as wt

import win32api
import win32con
import win32event
import win32file
import win32job
import win32pipe
import win32process
import win32security

from .errors import ProtocolError


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD), ("lpReserved", wt.LPWSTR), ("lpDesktop", wt.LPWSTR),
        ("lpTitle", wt.LPWSTR), ("dwX", wt.DWORD), ("dwY", wt.DWORD),
        ("dwXSize", wt.DWORD), ("dwYSize", wt.DWORD),
        ("dwXCountChars", wt.DWORD), ("dwYCountChars", wt.DWORD),
        ("dwFillAttribute", wt.DWORD), ("dwFlags", wt.DWORD),
        ("wShowWindow", wt.WORD), ("cbReserved2", wt.WORD),
        ("lpReserved2", ctypes.c_void_p), ("hStdInput", wt.HANDLE),
        ("hStdOutput", wt.HANDLE), ("hStdError", wt.HANDLE),
    ]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [("StartupInfo", _StartupInfo), ("lpAttributeList", ctypes.c_void_p)]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [("hProcess", wt.HANDLE), ("hThread", wt.HANDLE),
                ("dwProcessId", wt.DWORD), ("dwThreadId", wt.DWORD)]


def _spawn_in_job(command: list[str], job: object, stdin: object, stdout: object,
                  stderr: object) -> tuple[int, int]:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    initialize = kernel.InitializeProcThreadAttributeList
    initialize.argtypes = [ctypes.c_void_p, wt.DWORD, wt.DWORD, ctypes.POINTER(ctypes.c_size_t)]
    initialize.restype = wt.BOOL
    update = kernel.UpdateProcThreadAttribute
    update.argtypes = [ctypes.c_void_p, wt.DWORD, ctypes.c_size_t, ctypes.c_void_p,
                       ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
    update.restype = wt.BOOL
    delete = kernel.DeleteProcThreadAttributeList
    delete.argtypes = [ctypes.c_void_p]
    delete.restype = None
    create = kernel.CreateProcessW
    create.argtypes = [wt.LPCWSTR, wt.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
                       wt.BOOL, wt.DWORD, ctypes.c_void_p, wt.LPCWSTR,
                       ctypes.POINTER(_StartupInfoEx), ctypes.POINTER(_ProcessInfo)]
    create.restype = wt.BOOL
    size = ctypes.c_size_t()
    initialize(None, 2, 0, ctypes.byref(size))
    attributes = ctypes.create_string_buffer(size.value)
    if not initialize(attributes, 2, 0, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        jobs = (wt.HANDLE * 1)(int(job))
        handles = (wt.HANDLE * 3)(int(stdin), int(stdout), int(stderr))
        # Documented PROC_THREAD_ATTRIBUTE_JOB_LIST and HANDLE_LIST constants.
        for kind, value in ((0x0002000D, jobs), (0x00020002, handles)):
            if not update(attributes, 0, kind, value, ctypes.sizeof(value), None, None):
                raise ctypes.WinError(ctypes.get_last_error())
        startup = _StartupInfoEx()
        startup.StartupInfo.cb = ctypes.sizeof(startup)
        startup.StartupInfo.dwFlags = win32con.STARTF_USESTDHANDLES
        startup.StartupInfo.hStdInput = int(stdin)
        startup.StartupInfo.hStdOutput = int(stdout)
        startup.StartupInfo.hStdError = int(stderr)
        startup.lpAttributeList = ctypes.cast(attributes, ctypes.c_void_p)
        process = _ProcessInfo()
        line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
        if not create(command[0], line, None, None, True,
                      0x00080000 | win32con.CREATE_NO_WINDOW, None, None,
                      ctypes.byref(startup), ctypes.byref(process)):
            raise ctypes.WinError(ctypes.get_last_error())
        win32api.CloseHandle(process.hThread)
        return process.hProcess, process.dwProcessId
    finally:
        delete(attributes)


class JobProcess:
    """One native child; the job is private and its handle is never inherited."""

    def __init__(self, command: list[str]) -> None:
        self.job = win32job.CreateJobObject(None, None)
        self.process: int | None = None
        self.stdin = None
        self.stdout = None
        inherited: list[object] = []
        parents: list[object] = []
        try:
            limits = win32job.QueryInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation)
            limits["BasicLimitInformation"]["LimitFlags"] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation, limits)
            security = win32security.SECURITY_ATTRIBUTES()
            security.bInheritHandle = True
            child_in, parent_in = win32pipe.CreatePipe(security, 0)
            inherited.append(child_in)
            parents.append(parent_in)
            parent_out, child_out = win32pipe.CreatePipe(security, 0)
            parents.append(parent_out)
            inherited.append(child_out)
            for handle in parents:
                win32api.SetHandleInformation(handle, win32con.HANDLE_FLAG_INHERIT, 0)
            null = win32file.CreateFile("NUL", win32con.GENERIC_WRITE,
                                      win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE,
                                      security, win32con.OPEN_EXISTING, 0, None)
            inherited.append(null)
            self.process, self.pid = _spawn_in_job(command, self.job, child_in, child_out, null)
            # Transfer ownership to the file objects exactly once.
            self.stdin = os.fdopen(msvcrt.open_osfhandle(parent_in.Detach(), os.O_WRONLY | os.O_BINARY), "wb", buffering=0)
            self.stdout = os.fdopen(msvcrt.open_osfhandle(parent_out.Detach(), os.O_RDONLY | os.O_BINARY), "rb")
        except BaseException:
            self.close()
            raise
        finally:
            for handle in (*inherited, *parents):
                if int(handle):
                    handle.Close()

    def poll(self) -> int | None:
        if self.process is None:
            return None
        if win32event.WaitForSingleObject(self.process, 0) == win32con.WAIT_TIMEOUT:
            return None
        return win32process.GetExitCodeProcess(self.process)

    def close(self) -> None:
        """Terminate only this job and verify all members have exited before release."""
        try:
            if self.job is not None:
                win32job.TerminateJobObject(self.job, 1)
                deadline = time.monotonic() + 10
                while win32job.QueryInformationJobObject(self.job, win32job.JobObjectBasicAccountingInformation)["ActiveProcesses"]:
                    if time.monotonic() >= deadline:
                        raise ProtocolError("Windows job cleanup timed out")
                    time.sleep(0.02)
        finally:
            if self.job is not None:
                self.job.Close()
                self.job = None
            if self.process is not None:
                win32api.CloseHandle(self.process)
                self.process = None
            for stream in (self.stdin, self.stdout):
                if stream is not None:
                    stream.close()
