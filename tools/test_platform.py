"""Run the selected platform's offline checks and retain machine-readable receipts."""

# ruff: noqa: T201 — user-facing CLI output

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from platform_support import PLATFORMS, require_platform


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=PLATFORMS)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    selected = args.platform
    if selected is None:
        try:
            selected = json.loads((root / ".local/integration-platform.json").read_text())["platform"]
        except (OSError, ValueError, KeyError):
            parser.error("run tools/integrate.py first or specify --platform")
    try:
        require_platform(selected)
    except ValueError as exc:
        parser.error(str(exc))
    os.umask(0o077)
    temp_base = Path(tempfile.gettempdir()).resolve() if os.name == "nt" else Path("/tmp").resolve()  # noqa: S108
    if os.name == "nt":
        sys.path.insert(0, str(root / "src"))
        from codex_app_server_adapter.platform_files import make_private_directory

        receipts = temp_base / f"cb-{uuid.uuid4().hex[:12]}"
        make_private_directory(receipts)
    else:
        receipts = Path(tempfile.mkdtemp(prefix="cb-", dir=temp_base)).resolve()
    environment = {**os.environ, "PYTHONPATH": str(root / "src"), "PYTHONDONTWRITEBYTECODE": "1",
                   "CODEX_HOME": str(receipts / "codex-home")}
    files = [str(root / "tests")]
    if selected == "windows":
        files = [str(root / "tests" / name) for name in (
            "test_windows_platform.py", "test_platform_transport.py", "test_platform_setup.py", "test_platform_store.py", "test_platform_deadline.py",
            "test_mcp_polling.py")]
    commands = {
        "pytest": [sys.executable, "-m", "pytest", *files, "-q", "-m", "not installed_runtime",
                   "--basetemp", str(receipts / "tests"), "-p", "no:cacheprovider",
                   "--junitxml", str(receipts / "tests.xml")],
        "lint": [sys.executable, "-m", "ruff", "check", "--no-cache", str(root / "src"), str(root / "tools"),
                 *[str(root / "tests" / name) for name in (
                     "test_standalone_extraction.py", "installed_wheel_smoke.py", "test_windows_platform.py",
                     "windows_fixture.py", "wsl_fixture.py", "wsl_forwarding.py", "test_wsl_forwarding.py", "test_platform_transport.py", "test_platform_setup.py", "test_platform_store.py", "test_platform_deadline.py",
                     "mcp_polling.py", "test_mcp_polling.py", "test_security_tools.py", "test_advisory_receipts.py")]],
        "transfer": [sys.executable, str(root / "tools/verify_transfer.py")],
    }
    if selected != "windows":
        commands["mcp-smoke"] = [sys.executable, str(root / "tests/installed_wheel_smoke.py"),
                                 "--source-root", str(root / "src"), "--fixture",
                                 str(root / "tests/fixtures/codex_app_server_adapter/native_server.py")]
    report = {"platform": selected, "os": platform.platform(), "python": sys.version,
              "scope": "offline fixtures only; no installed runtime or provider", "checks": {}}
    print(f"Receipts: {receipts}", flush=True)
    for name, command in commands.items():
        with (receipts / f"{name}.log").open("w", encoding="utf-8") as log:
            try:
                result = subprocess.run(command,  # noqa: S603 - fixed local test commands
                                         cwd=temp_base, env=environment, stdout=log,
                                        stderr=subprocess.STDOUT, timeout=600, check=False)
                code = result.returncode
            except subprocess.TimeoutExpired:
                code = 124
        report["checks"][name] = {"exit_code": code, "log": f"{name}.log"}
        print(f"{name}: {'PASS' if code == 0 else 'FAIL'} (exit {code})", flush=True)
        (receipts / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if (receipts / "tests.xml").exists():
        suites = ET.parse(receipts / "tests.xml").getroot().findall(".//testsuite")  # noqa: S314 - owned pytest receipt
        counts = {key: sum(int(s.get(key, 0)) for s in suites) for key in ("tests", "failures", "errors", "skipped")}
        passed = counts["tests"] - counts["failures"] - counts["errors"] - counts["skipped"]
        minimum, expected_skips = (19, 1) if selected == "windows" else (535, 4)
        counts_ok = passed >= minimum and counts["skipped"] == expected_skips
        report["counts"] = {**counts, "passed": passed}
        report["checks"]["test-counts"] = {"exit_code": 0 if counts_ok else 1}
    else:
        report["checks"]["test-counts"] = {"exit_code": 1}
    (receipts / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    success = all(item["exit_code"] == 0 for item in report["checks"].values())
    print("OFFLINE CHECKS PASSED" if success else "OFFLINE CHECKS FAILED — retain and inspect receipts")
    raise SystemExit(0 if success else 1)


if __name__ == "__main__":
    main()
