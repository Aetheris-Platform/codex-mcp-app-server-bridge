# Local security checks and release preparation

Maintainers run these checks on their own machines. There is no GitHub Actions,
paid GitHub security product, provider authentication or model-call dependency.
The scripts and tool pins are committed; installations, caches and raw reports
remain under ignored `.local/`. Local hooks are optional and bypassable; the
owner reviews receipts before manually merging or publishing.

## Explicit tool setup

Use Python 3.12+ and the existing checkout-local uv 0.12.18 from README setup.
The security environment has its own `tools/security/uv.lock`; it is separate
from the bridge environment. From the checkout root on macOS/Linux/WSL2:

```sh
BRIDGE="$PWD"
UV_CACHE_DIR="$BRIDGE/.local/uv-cache" \
  UV_PROJECT_ENVIRONMENT="$BRIDGE/.local/security/venv" \
  "$BRIDGE/.local/setup/bin/uv" sync --project tools/security --locked --no-python-downloads
"$BRIDGE/.venv/bin/python" tools/setup_security.py
```

On native Windows, after the existing platform setup:

```powershell
$Bridge = (Get-Location).Path
$env:UV_CACHE_DIR = Join-Path $Bridge '.local\uv-cache'
$env:UV_PROJECT_ENVIRONMENT = Join-Path $Bridge '.local\security\venv'
& .\.local\setup\Scripts\uv.exe sync --project tools/security --locked --no-python-downloads
& .\.venv\Scripts\python.exe tools/setup_security.py
Remove-Item Env:UV_PROJECT_ENVIRONMENT
```

If uv is already available elsewhere, substitute that existing executable.
`setup_security.py` downloads only the selected official Gitleaks 8.30.1 archive,
verifies its committed SHA-256 and reads named regular members into
`.local/security/gitleaks/`, retaining the archive and license. It never extracts
arbitrary archive paths or installs global tools. Unsupported architecture pins
fail explicitly. Setup uses the network; running the bridge never invokes it.
Security Python tools are Bandit 1.9.4, pip-audit 2.10.1 and Ruff 0.16.9, with
transitive versions and artifact hashes in the separate lock.

## Evaluate a PR or release candidate

Review candidate code before executing it; local tests and tool scripts can run
with your user privileges. Use a clean complete clone with the intended base
commit available locally. The checker never fetches or switches branches.
From the checkout root:

```sh
.venv/bin/python tools/security_check.py --base origin/main
```

On native Windows:

```powershell
& .\.venv\Scripts\python.exe tools/security_check.py --base origin/main
```

The checker resolves and records the exact base and HEAD. It refuses shallow
clones and dirty candidates unless `--allow-dirty` is explicitly supplied for
**development checks**, which do not establish clean-candidate acceptance.
It runs:

| Check | Scope and pass target |
| --- | --- |
| Gitleaks history | All locally reachable Git refs, zero detected secrets |
| Gitleaks PR | Explicit resolved base-to-HEAD commit range, zero detected secrets |
| Gitleaks files | Candidate files, excluding ignored local data, zero detected secrets |
| Bandit | All `src` and `tools` Python; no parse errors or unreviewed findings |
| Ruff | Production/tools and focused security/extraction tests, no diagnostics |
| pip-audit | Every registry package/version in both locks, including Windows-only and development packages; zero known advisories, skipped or missing packages |

Dependency inventories are generated from the locks without platform-marker
filtering, dependency installation or automatic fixes. `pip-audit` queries
public advisory services with package names and versions; network access is
needed for that step. It does not upload repository source, credentials or
journals. This is a known-advisory check, not detection of malicious packages or
proof of native platform behavior. The other checks run locally without model
usage. There is no paid GitHub service or Actions usage.

The runner prints its unique receipt directory under `.local/security/reports/`.
It retains redacted secret reports, command logs, Bandit findings, dependency
inventories/reports and `summary.json`. Every check must pass and the runner must
exit 0; absent/malformed reports, missing tools, incomplete inventories and
command failures are failures. Review the exact commit, base and dirty flag.
Keep the checkout private while testing and restrict access to raw reports.
Commit only a sanitized summary; retain failures as well as successful reruns.

## Reviewed code-analysis findings

The Gitleaks configuration retains all default rules. Six exact receipt-digest
lines in the dated evidence JSON are reviewed false positives for
`generic-api-key`: each SHA-256 was recomputed from its retained scanner receipt.
The exception requires both the exact evidence file path and exact line/value;
it does not exclude that file, arbitrary hashes or the rule as a whole.

`tools/security/bandit-reviewed.json` contains individual review reasons. Each
exception is bound to file path, test ID, line, code excerpt and the whole source
file SHA-256. A changed file, new finding, stale exception or parse error fails
acceptance. The runner ignores inline `nosec` suppressions. Do not regenerate
this file merely to get a pass: inspect each finding and change source or record
a specific reason with a review date.

Current reviewed uses include owned subprocess execution with explicit argv,
checked runtime version probes, private fixture directories, the established
socket rendezvous path, the fixed hash-checked setup download, and XML produced
by the same local pytest invocation. These are scoped explanations, not blanket
rule exclusions. Maintainer tools rely on the operator's trusted Git/Python
installation and a reviewed candidate; they are not a sandbox for hostile PRs.

## Release checklist

1. Preserve Git history. Scan the complete clone and candidate; resolve confirmed
   secrets or vulnerabilities and retain any failed receipt.
2. Run local security acceptance and the [platform matrix](integration-testing.md#local-maintainer-acceptance)
   against the candidate. A dependency change requires fresh platform evidence;
   earlier Windows/WSL2 results are not silently applied to an updated lock.
3. Review exact distributed files and applicable notices. Optional maintainer
   tools are not bridge runtime dependencies and are not bundled in the package.
4. Record results, exclusions, source/lock identities and the reporting contact
   from [SECURITY.md](../SECURITY.md) in the existing private PR. Historical product
   references are retained by owner decision; current documentation uses neutral
   provenance labels and the owner-selected security contact.
5. The owner merges and changes public visibility separately. No script here
   merges, publishes, changes repository settings or registers a consumer.
   After the owner's merge, verify main before cleaning only this task's
   disposable resources; retain sanitized evidence.
