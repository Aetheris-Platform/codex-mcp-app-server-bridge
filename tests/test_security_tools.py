"""Security acceptance must fail on incomplete audits and changed exceptions."""
from __future__ import annotations

import io
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from security_check import audit_ok, bandit_ok, finding_key, locked_packages
from setup_security import unpack


@pytest.mark.parametrize("report", [None, {}, {"dependencies": []},
    {"dependencies": [{"name": "example", "version": "1", "skip_reason": "not found"}]},
    {"dependencies": [{"name": "example", "version": "1", "vulns": [{"id": "example-advisory"}]}]},
    {"dependencies": [{"name": "example", "version": "2", "vulns": []}]},
])
def test_incomplete_or_vulnerable_audit_fails(report: object) -> None:
    assert not audit_ok(report, {("example", "1")})


def test_complete_audit_passes_but_duplicates_fail() -> None:
    dependency = {"name": "example", "version": "1", "vulns": []}
    assert audit_ok({"dependencies": [dependency]}, {("example", "1")})
    assert not audit_ok({"dependencies": [dependency, dependency]}, {("example", "1")})


def test_all_lock_platforms_are_included_without_installing(tmp_path: Path) -> None:
    lock = tmp_path / "uv.lock"
    lock.write_text('''[[package]]
name = "virtual-root"
version = "1"
source = { virtual = "." }
[[package]]
name = "windows-only"
version = "2"
source = { registry = "https://pypi.org/simple" }
resolution-markers = ["sys_platform == 'win32'"]
''')
    assert locked_packages(lock) == {("windows-only", "2")}


def test_reviewed_finding_is_bound_to_exact_source(tmp_path: Path) -> None:
    source = tmp_path / "example.py"
    source.write_text("import subprocess\n")
    finding = {"filename": "example.py", "test_id": "B404", "line_number": 1, "code": "import subprocess"}
    report = {"results": [finding], "errors": [], "metrics": {"_totals": {"loc": 1}}}
    exception = {"fingerprint": finding_key(finding, tmp_path), "reason": "Reviewed explicit subprocess use"}
    assert bandit_ok(report, [exception], tmp_path)
    assert not bandit_ok(report, [], tmp_path)
    assert not bandit_ok({**report, "errors": ["unreadable"]}, [exception], tmp_path)
    source.write_text("import subprocess\n# changed\n")
    assert not bandit_ok(report, [exception], tmp_path)
    assert not bandit_ok({**report, "results": []}, [exception], tmp_path)


def test_missing_bandit_coverage_fails() -> None:
    assert not bandit_ok({"results": [], "errors": []}, [])


def test_archive_reader_does_not_extract_paths(tmp_path: Path) -> None:
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as archive:
        for name in ("gitleaks", "LICENSE", "../../escape"):
            item = tarfile.TarInfo(name)
            item.size = 2
            archive.addfile(item, io.BytesIO(b"ok"))
    assert unpack(data.getvalue(), "test.tar.gz", "gitleaks") == {"gitleaks": b"ok", "LICENSE": b"ok"}
    assert not list(tmp_path.iterdir())


def test_archive_reader_refuses_executable_symlink() -> None:
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as archive:
        item = tarfile.TarInfo("gitleaks")
        item.type = tarfile.SYMTYPE
        item.linkname = "/outside"
        archive.addfile(item)
    with pytest.raises(ValueError, match="not a regular file"):
        unpack(data.getvalue(), "test.tar.gz", "gitleaks")
