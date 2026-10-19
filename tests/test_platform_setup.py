"""The platform selector must reject mismatches and never install dependencies."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


def test_selector_records_choice_without_running_setup(tmp_path: Path):
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("integrate.py", "platform_support.py"):
        shutil.copyfile(Path(__file__).parents[1] / "tools" / name, tools / name)
    native = "windows" if sys.platform == "win32" else "macos" if sys.platform == "darwin" else "linux"
    if sys.platform == "linux" and "microsoft" in Path("/proc/sys/kernel/osrelease").read_text().lower():
        native = "wsl"
    process = subprocess.run(  # noqa: S603 - copied local selector
        [sys.executable, str(tools / "integrate.py"), "--platform", native],
        capture_output=True, text=True, check=False, timeout=10,
    )
    assert process.returncode == 0, process.stderr
    assert json.loads((tmp_path / ".local/integration-platform.json").read_text()) == {"platform": native}
    assert not (tmp_path / ".venv").exists()
    assert "No dependencies" in process.stdout
    other = "windows" if native != "windows" else "macos"
    mismatch = subprocess.run(  # noqa: S603 - copied local selector
        [sys.executable, str(tools / "integrate.py"), "--platform", other],
        capture_output=True, text=True, check=False, timeout=10,
    )
    assert mismatch.returncode == 2
    assert json.loads((tmp_path / ".local/integration-platform.json").read_text()) == {"platform": native}
