"""Run pinned local security checks; never install tools or submit provider work."""

# ruff: noqa: T201 -- maintainer-facing CLI
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import tomllib
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def locked_packages(path: Path) -> set[tuple[str, str]]:
    lock = tomllib.loads(path.read_text())
    packages = {(p["name"], p["version"]) for p in lock["package"] if "registry" in p["source"]}
    if not packages:
        raise ValueError(f"Empty package inventory: {path}")
    return packages


def audit_ok(report: object, expected: set[tuple[str, str]]) -> bool:
    if not isinstance(report, dict) or not isinstance(report.get("dependencies"), list):
        return False
    dependencies = report["dependencies"]
    if any(not isinstance(p, dict) or "vulns" not in p or p.get("skip_reason") for p in dependencies):
        return False
    actual = {(p.get("name"), p.get("version")) for p in dependencies}
    return actual == expected and len(dependencies) == len(expected) and all(p["vulns"] == [] for p in dependencies)


def finding_key(finding: dict, root: Path = ROOT) -> str:
    path = Path(finding["filename"])
    if not path.is_absolute():
        path = root / path
    relative = path.resolve().relative_to(root.resolve()).as_posix()
    value = [relative, finding["test_id"], finding["line_number"], finding["code"],
             hashlib.sha256(path.read_bytes()).hexdigest()]
    return hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()


def bandit_ok(report: dict, exceptions: list[dict], root: Path = ROOT) -> bool:
    if report.get("errors") or not isinstance(report.get("results"), list):
        return False
    if report.get("metrics", {}).get("_totals", {}).get("loc", 0) <= 0:
        return False
    keys = [entry["fingerprint"] for entry in exceptions if entry.get("reason", "").strip()]
    if len(keys) != len(exceptions) or len(set(keys)) != len(keys):
        return False
    observed = {finding_key(finding, root) for finding in report["results"]}
    return observed == set(keys)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main", help="Local PR base ref; never fetched automatically")
    parser.add_argument("--allow-dirty", action="store_true", help="Development checks only, not clean-candidate acceptance")
    args = parser.parse_args()
    os.umask(0o077)
    git = shutil.which("git")
    if git is None:
        raise SystemExit("Git is required")

    def git_read(*arguments: str) -> str:
        return subprocess.check_output([git, *arguments], cwd=ROOT, text=True).strip()  # noqa: S603

    head = git_read("rev-parse", "HEAD")
    base = git_read("rev-parse", "--verify", "--end-of-options", f"{args.base}^{{commit}}")
    dirty = bool(git_read("status", "--porcelain"))
    if dirty and not args.allow_dirty:
        raise SystemExit("Commit the candidate first, or use --allow-dirty for development checks")
    if git_read("rev-parse", "--is-shallow-repository") != "false":
        raise SystemExit("Full history is required; use a complete clone")
    tools = ROOT / ".local/security"
    python = tools / ("venv/Scripts/python.exe" if os.name == "nt" else "venv/bin/python")
    gitleaks = tools / "gitleaks" / ("gitleaks.exe" if os.name == "nt" else "gitleaks")
    pins = json.loads((ROOT / "tools/security/gitleaks.json").read_text())
    if not python.is_file() or not gitleaks.is_file():
        raise SystemExit("Run the explicit setup in docs/security-testing.md first")
    versions = json.loads(subprocess.check_output(  # noqa: S603 -- fixed local tool interpreter
        [str(python), "-c", "import importlib.metadata as m,json; print(json.dumps({n:m.version(n) for n in ('bandit','pip-audit','ruff')}))"], text=True))
    required = tomllib.loads((ROOT / "tools/security/pyproject.toml").read_text())["project"]["dependencies"]
    if any(versions[name] != version for name, version in (p.split("==") for p in required)):
        raise SystemExit("Security tool version mismatch; repeat locked setup")
    if subprocess.check_output([str(gitleaks), "version"], text=True).strip().removeprefix("v") != pins["version"]:  # noqa: S603
        raise SystemExit("Gitleaks version mismatch")
    reports = tools / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    receipt = Path(tempfile.mkdtemp(prefix="run-", dir=reports))
    summary = {"commit": head, "base_commit": base, "dirty": dirty,
               "started_at_utc": datetime.now(UTC).isoformat(),
               "tools": {**versions, "gitleaks": pins["version"]}, "checks": {}}
    print(f"Security receipts: {receipt}")

    def run(name: str, command: list[str]) -> int:
        try:
            with (receipt / f"{name}.log").open("w", encoding="utf-8") as log:
                result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,  # noqa: S603 -- argv only, pinned local tools
                                        timeout=600, check=False)
            code = result.returncode
        except (OSError, subprocess.TimeoutExpired):
            code = 124
        summary["checks"][name] = {"exit_code": code, "pass": code == 0}
        return code

    def read_report(name: str) -> object:
        try:
            return json.loads((receipt / f"{name}.json").read_text())
        except (OSError, ValueError):
            return None

    for name, options in (("secrets-history", "--all"), ("secrets-pr", f"{base}..{head}")):
        code = run(name, [str(gitleaks), "git", "--config", str(ROOT / "tools/security/gitleaks.toml"), "--no-banner", "--redact=100", f"--log-opts={options}",
                          "--report-format=json", f"--report-path={receipt / (name + '.json')}"])
        summary["checks"][name]["pass"] = code == 0 and read_report(name) == []
    # Scan candidate files too, including uncommitted development files. Ignored
    # credentials, caches and reports are never copied or traversed.
    with tempfile.TemporaryDirectory(prefix="candidate-", dir=tools) as temporary:
        snapshot = Path(temporary)
        paths = subprocess.check_output([git, "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT).split(b"\0")  # noqa: S603
        for raw in paths:
            if not raw:
                continue
            path = Path(os.fsdecode(raw))
            source = ROOT / path
            if not source.exists():
                continue
            if source.is_symlink() or not source.resolve().is_relative_to(ROOT):
                raise SystemExit("Candidate file escapes the checkout")
            target = snapshot / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        name = "secrets-files"
        code = run(name, [str(gitleaks), "dir", str(snapshot), "--config", str(ROOT / "tools/security/gitleaks.toml"), "--no-banner", "--redact=100",
                          "--report-format=json", f"--report-path={receipt / (name + '.json')}"])
        summary["checks"][name]["pass"] = code == 0 and read_report(name) == []
    name = "bandit"
    code = run(name, [str(python), "-m", "bandit", "-r", "src", "tools", "--ignore-nosec", "-f", "json", "-o", str(receipt / f"{name}.json")])
    report = read_report(name)
    exceptions = json.loads((ROOT / "tools/security/bandit-reviewed.json").read_text())
    summary["checks"][name]["pass"] = code in (0, 1) and isinstance(report, dict) and bandit_ok(report, exceptions)
    summary["checks"][name]["reviewed_findings"] = len(exceptions)
    run("ruff", [str(python), "-m", "ruff", "check", "--no-cache", "src", "tools",
                 "tests/test_standalone_extraction.py", "tests/installed_wheel_smoke.py", "tests/test_security_tools.py"])
    for label, lock in (("bridge", ROOT / "uv.lock"), ("tools", ROOT / "tools/security/uv.lock")):
        expected = locked_packages(lock)
        requirements = receipt / f"{label}-requirements.txt"
        requirements.write_text("".join(f"{name}=={version}\n" for name, version in sorted(expected)))
        name = f"dependencies-{label}"
        code = run(name, [str(python), "-m", "pip_audit", "--no-deps", "--disable-pip", "--progress-spinner", "off",
                          "-r", str(requirements), "-f", "json", "-o", str(receipt / f"{name}.json")])
        summary["checks"][name]["pass"] = code == 0 and audit_ok(read_report(name), expected)
        summary["checks"][name]["expected_packages"] = len(expected)
    unchanged = head == git_read("rev-parse", "HEAD") and (args.allow_dirty or not git_read("status", "--porcelain"))
    summary["pass"] = unchanged and all(check["pass"] for check in summary["checks"].values())
    summary["scope"] = "development working tree" if dirty else "clean committed candidate"
    (receipt / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    for name, check in summary["checks"].items():
        print(f"{name}: {'PASS' if check['pass'] else 'FAIL'}")
    raise SystemExit(0 if summary["pass"] else 1)


if __name__ == "__main__":
    main()
