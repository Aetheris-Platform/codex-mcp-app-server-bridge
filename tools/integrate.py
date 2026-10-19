"""Select the target platform before following its setup and acceptance route."""

# ruff: noqa: T201 — user-facing CLI output

from __future__ import annotations

import argparse
import json
from pathlib import Path

from platform_support import PLATFORMS, require_platform


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=PLATFORMS)
    args = parser.parse_args()
    choice = args.platform
    if choice is None:
        print("Select your integration platform:")
        for index, (key, label) in enumerate(PLATFORMS.items(), 1):
            print(f"  {index}. {label} [{key}]")
        answer = input("Platform (name or number): ").strip().lower()
        choice = dict(zip(map(str, range(1, 5)), PLATFORMS, strict=True)).get(answer, answer)
    if choice not in PLATFORMS:
        parser.error("choose macos, linux, windows or wsl")
    try:
        require_platform(choice)
    except ValueError as exc:
        parser.error(str(exc))
    root = Path(__file__).resolve().parents[1]
    destination = root / ".local/integration-platform.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps({"platform": choice}) + "\n", encoding="utf-8")
    python = ".venv\\Scripts\\python.exe" if choice == "windows" else ".venv/bin/python"
    print(f"Selected {PLATFORMS[choice]}. Follow docs/platforms.md#{choice}.")
    print("Setup: uv sync --locked --extra dev --no-install-project --no-python-downloads")
    print(f"After setup: {python} tools/test_platform.py --platform {choice}")
    print("Selection saved locally. No dependencies, credentials, runtime or MCP registration were changed.")


if __name__ == "__main__":
    main()
