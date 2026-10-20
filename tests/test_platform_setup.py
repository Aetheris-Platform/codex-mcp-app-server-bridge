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


def test_transfer_bytes_survive_autocrlf_checkout(tmp_path: Path):
    """Exercise Git's checkout conversion, then retain strict tamper detection."""
    source = Path(__file__).resolve().parents[1]
    staged = tmp_path / "staged"
    staged.mkdir()
    manifest_path = Path("docs/provenance/transfer-manifest.json")
    manifest = json.loads((source / manifest_path).read_text(encoding="utf-8"))
    names = [".gitattributes", str(manifest_path), "tools/verify_transfer.py"]
    names += [entry["destination_path"] for entry in manifest["files"] if entry["destination_path"]]
    for name in names:
        destination = staged / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, destination)
    checkout = tmp_path / "checkout"
    # Only this disposable repository's index/config is written; no commit,
    # global configuration change, network or source-checkout mutation.
    for arguments in (["init", "--quiet"], ["add", "."],
                      ["checkout-index", "--all", f"--prefix={checkout.as_posix()}/"]):
        subprocess.run(  # noqa: S603 - fixed local Git operations on owned temp repo
            ["git", "-c", "core.autocrlf=true", "-C", str(staged), *arguments],  # noqa: S607
            check=True, capture_output=True, timeout=30,
        )
    command = [sys.executable, str(checkout / "tools/verify_transfer.py")]
    verified = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)  # noqa: S603
    assert verified.returncode == 0, verified.stderr
    assert "Verified 25 destination files" in verified.stdout
    target = checkout / "LICENSE-DISPOSITION.md"
    original = target.read_bytes()
    assert b"\r\n" not in original
    target.write_bytes(original.replace(b"\n", b"\r\n"))
    rejected = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)  # noqa: S603
    assert rejected.returncode != 0
    assert "destination digest mismatch" in rejected.stderr
