"""Validate operator-transferred advisory evidence for a disconnected host."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def load_advisory_receipt(source: Path, label: str, *, commit: str,
                          lock_sha256: dict[str, str], tool_version: str,
                          now: datetime | None = None) -> tuple[object, dict]:
    """Require a clean, successful, fresh direct audit of the exact candidate.

    Receipt hashes detect copy changes; they do not authenticate the producer.
    Only transfer receipts from a maintainer-controlled, trusted machine.
    """
    raw_summary = (source / "summary.json").read_bytes()
    summary = json.loads(raw_summary)
    if not isinstance(summary, dict):
        raise ValueError("advisory summary must be an object")
    if (summary.get("commit") != commit or summary.get("dirty") is not False
            or summary.get("pass") is not True
            or summary.get("lock_sha256") != lock_sha256
            or summary.get("tools", {}).get("pip-audit") != tool_version):
        raise ValueError("advisory source must be a successful clean audit of this commit, locks and tool")
    started = datetime.fromisoformat(summary["started_at_utc"])
    if started.tzinfo is None:
        raise ValueError("advisory source timestamp must include timezone")
    age = ((now or datetime.now(UTC)) - started).total_seconds()
    if not 0 <= age <= 24 * 60 * 60:
        raise ValueError("advisory source must be from the last 24 hours, not the future")
    for kind in ("bridge", "tools"):
        check = summary.get("checks", {}).get(f"dependencies-{kind}", {})
        if check.get("pass") is not True or check.get("exit_code") != 0 or check.get("mode") != "direct":
            raise ValueError("both source dependency checks must be successful direct queries")
    raw = (source / f"dependencies-{label}.json").read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != summary["checks"][f"dependencies-{label}"].get("report_sha256"):
        raise ValueError("advisory report digest mismatch")
    return json.loads(raw), {
        "mode": "transferred", "source_commit": commit,
        "source_started_at_utc": summary["started_at_utc"],
        "source_summary_sha256": hashlib.sha256(raw_summary).hexdigest(),
        "source_report_sha256": digest,
    }
