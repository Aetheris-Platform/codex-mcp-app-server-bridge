"""Explicit setup: download hash-pinned Gitleaks into this checkout only."""

# ruff: noqa: T201 -- operator-facing setup command
from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path


def unpack(data: bytes, name: str, executable: str) -> dict[str, bytes]:
    """Read only named regular members; never extract arbitrary archive paths."""
    wanted = {executable, "LICENSE", "README.md"}
    result = {}
    if name.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for item in archive.infolist():
                if item.filename in wanted and not item.is_dir():
                    if (item.external_attr >> 16) & 0o170000 == 0o120000:
                        raise ValueError("archive member is a symlink")
                    result[item.filename] = archive.read(item)
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            for item in archive:
                if item.name in wanted:
                    if not item.isfile():
                        raise ValueError("archive member is not a regular file")
                    stream = archive.extractfile(item)
                    if stream is not None:
                        result[item.name] = stream.read()
    if executable not in result or "LICENSE" not in result:
        raise ValueError("archive lacks executable or license")
    return result


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    pins = json.loads((root / "tools/security/gitleaks.json").read_text())
    key = f"{sys.platform}-{platform.machine().lower()}"
    if key not in pins["assets"]:
        raise SystemExit(f"No pinned security-tool asset for {key}")
    asset = pins["assets"][key]
    url = f"https://github.com/gitleaks/gitleaks/releases/download/v{pins['version']}/{asset['name']}"
    os.umask(0o077)
    destination = root / ".local/security/gitleaks"
    destination.mkdir(parents=True, exist_ok=True)
    # Only the committed official release URL is used; hash checked before unpacking.
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310
        data = response.read()
    if hashlib.sha256(data).hexdigest() != asset["sha256"]:
        raise SystemExit("Gitleaks archive digest mismatch")
    executable = "gitleaks.exe" if sys.platform == "win32" else "gitleaks"
    contents = unpack(data, asset["name"], executable)
    for name, content in contents.items():
        (destination / name).write_bytes(content)
    (destination / executable).chmod(0o700)
    (destination / asset["name"]).write_bytes(data)
    print(f"Installed Gitleaks {pins['version']} in {destination}; archive SHA-256 verified")


if __name__ == "__main__":
    main()
