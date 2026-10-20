"""Dependency-free platform selection shared by setup and test entry points."""

from __future__ import annotations

import sys
from pathlib import Path

PLATFORMS = {"macos": "macOS", "linux": "Linux", "windows": "Windows 11 (native)",
             "wsl": "Windows 11 with WSL2 (run inside the Linux distribution)"}


def current_platform() -> str:
    if sys.platform == "darwin":
        return "macos"
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "linux":
        release = Path("/proc/sys/kernel/osrelease").read_text().lower()
        return "wsl" if "microsoft" in release else "linux"
    return "unsupported"


def require_platform(choice: str) -> None:
    actual = current_platform()
    if choice != actual:
        raise ValueError(f"Selected {choice}, but this interpreter runs on {actual}. Run setup/tests in the selected environment.")
    if choice == "windows" and (sys.getwindowsversion().build < 22000 or sys.getwindowsversion().product_type != 1):
        raise ValueError("Native acceptance requires Windows 11, not an older Windows release")
    if choice == "wsl":
        release = Path("/proc/sys/kernel/osrelease").read_text().lower()
        if "wsl2" not in release:
            raise ValueError("WSL2 is required; verify the distribution with wsl.exe --list --verbose")
