"""A disconnected host cannot silently substitute incomplete or stale audits."""

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from advisory_receipts import load_advisory_receipt
from security_check import audit_ok

NOW = datetime(2026, 10, 20, 17, tzinfo=UTC)
LOCKS = {"bridge": "a" * 64, "tools": "b" * 64}


def make_receipts(path):
    raw = json.dumps({"dependencies": [{"name": "example", "version": "1", "vulns": []}]}).encode()
    for label in ("bridge", "tools"):
        (path / f"dependencies-{label}.json").write_bytes(raw)
    summary = {
        "commit": "candidate", "dirty": False, "pass": True,
        "lock_sha256": LOCKS, "tools": {"pip-audit": "2.10.1"},
        "started_at_utc": "2026-10-20T16:00:00+00:00",
        "checks": {f"dependencies-{label}": {
            "pass": True, "exit_code": 0, "mode": "direct",
            "report_sha256": hashlib.sha256(raw).hexdigest(),
        } for label in ("bridge", "tools")},
    }
    (path / "summary.json").write_text(json.dumps(summary))
    return summary


def read(path):
    return load_advisory_receipt(path, "bridge", commit="candidate",
                                 lock_sha256=LOCKS, tool_version="2.10.1", now=NOW)


def test_verified_transfer_preserves_complete_audit_and_provenance(tmp_path):
    make_receipts(tmp_path)
    report, provenance = read(tmp_path)
    assert audit_ok(report, {("example", "1")})
    assert not audit_ok(report, {("another-platform-package", "2")})
    assert provenance["mode"] == "transferred"
    assert provenance["source_commit"] == "candidate"


@pytest.mark.parametrize(("field", "value"), [
    ("commit", "other"), ("dirty", True), ("pass", False),
    ("lock_sha256", {"bridge": "changed", "tools": "b" * 64}),
    ("tools", {"pip-audit": "other"}),
    ("started_at_utc", "2026-10-19T16:59:59+00:00"),
    ("started_at_utc", "2026-10-20T17:00:01+00:00"),
    ("started_at_utc", "2026-10-20T16:00:00"),
])
def test_wrong_identity_or_unfresh_receipt_is_rejected(tmp_path, field, value):
    summary = make_receipts(tmp_path)
    summary[field] = value
    (tmp_path / "summary.json").write_text(json.dumps(summary))
    with pytest.raises(ValueError, match="advisory"):
        read(tmp_path)


@pytest.mark.parametrize("change", ["failed", "reused", "tampered", "missing"])
def test_failed_reused_tampered_or_missing_evidence_is_rejected(tmp_path, change):
    summary = make_receipts(tmp_path)
    if change == "failed":
        summary["checks"]["dependencies-tools"]["exit_code"] = 1
    elif change == "reused":
        summary["checks"]["dependencies-tools"]["mode"] = "transferred"
    elif change == "tampered":
        (tmp_path / "dependencies-bridge.json").write_text('{"dependencies": []}')
    else:
        (tmp_path / "dependencies-bridge.json").unlink()
    (tmp_path / "summary.json").write_text(json.dumps(summary))
    with pytest.raises((ValueError, OSError)):
        read(tmp_path)
