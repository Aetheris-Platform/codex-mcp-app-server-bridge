"""Verify extracted bytes, optionally against a read-only committed source repository."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-repository", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "docs/provenance/transfer-manifest.json").read_text())
    checked = 0
    for entry in manifest["files"]:
        destination = entry["destination_path"]
        if destination is not None:
            data = (root / destination).read_bytes()
            if hashlib.sha256(data).hexdigest() != entry["destination_sha256"]:
                raise SystemExit(f"destination digest mismatch: {destination}")
            checked += 1
        if args.source_repository is not None:
            data = subprocess.check_output(  # noqa: S603 - explicit read-only Git blob lookup
                ["git", "-C", str(args.source_repository), "show",  # noqa: S607 - operator Git
                 f"{manifest['source_commit']}:{entry['source_path']}"],
            )
            if hashlib.sha256(data).hexdigest() != entry["source_sha256"]:
                raise SystemExit(f"source digest mismatch: {entry['source_path']}")
    print(f"Verified {checked} destination files; source comparison: {args.source_repository is not None}")  # noqa: T201


if __name__ == "__main__":
    main()
