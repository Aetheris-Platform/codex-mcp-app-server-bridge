# Local security and release preparation — 2026-10-20

Candidate: `d15d57617ed27347ecd537761849bbd7a1aefbc7`. The owner selected local
checks, preservation of Git history and manual control of merge/publication.
No paid GitHub tool, Actions job, provider turn, shared registration or installed
Codex executable was used. The repository remains private.

## Results

| Check | Observed result |
| --- | --- |
| Pinned local security runner, clean candidate | PASS, all seven checks accepted |
| Gitleaks 8.30.1 | Zero findings in reachable history, PR commit range and candidate files |
| Synthetic secret negative probe | Detected and fully redacted; scanner exited 1 as expected |
| pip-audit 2.10.1 | 37 bridge-lock and 34 security-tool-lock package/version entries checked; zero known advisories or skipped/missing entries after correction |
| Bandit 1.9.4 | 21 individually reviewed findings; zero unreviewed findings or parse errors |
| Ruff 0.16.9 | Focused production/tools/security-test target passed |
| macOS 27.0.1 arm64, Python 3.14.6 | 519 passed, 4 Windows-only skips, 1 installed-runtime deselection; MCP smoke, lint, transfer and runner exit 0 |
| Linux Docker aarch64, Python 3.14.6, UID 501, network disabled | Same 519/4/1 result, MCP smoke/lint/transfer and runner exit 0 |
| Security-tool regressions | 12 passes, included in both Unix totals; incomplete audits, changed/stale exceptions and archive traversal/symlink cases covered |
| Source transfer | 25 destination files verified, including optional comparison with exact committed source blobs |
| Retained dependency notices | 57 notice texts verified against current inventories |

The Linux run used an isolated source snapshot; runtime, tool, test and lock
bytes were compared with the candidate. The macOS final run and security run
used a clean committed checkout. A preliminary macOS run and development
security run also passed, but are not substituted for the clean-candidate runs.
The [machine record](provenance/security-validation-2026-10-20.json) retains
input/receipt hashes, exact environments, tool versions, base commit and counts.
Raw reports remain private under ignored local storage.

After the evidence was committed at `1f2280e4618550a4af2ee393c90790128510548e`,
Gitleaks flagged six receipt-digest lines in the evidence JSON as generic API
keys. That failed run is retained. Each digest was recomputed from its scanner
receipt and confirmed to be a SHA-256, not a credential. The configuration now
requires the exact evidence path and exact line/value for those reviewed false
positives; other secrets in that file remain detectable. The later PR records
the final candidate's rerun separately from the original candidate above.
Focused scanner probes accepted the six exact receipt lines and detected a
changed digest, the same values under another path and a synthetic key added
to the evidence path, with findings fully redacted.

Bandit's raw exit was 1 because it reports the 21 reviewed uses. The runner
accepted their exact fingerprints and reasons from the committed review file;
it did not label the raw scan finding-free. New findings, changed source or
stale exceptions fail the runner. The 52 historical test-file Ruff diagnostics
remain separate from the focused passing lint target.

## Dependency correction

The initial audit found two advisories in PyJWT 2.14.0:
`PYSEC-2026-4141` (CVE-2026-101918) and `PYSEC-2026-4183`
(CVE-2026-102275). Only PyJWT changed in the bridge lock, to 2.15.0. The initial
failed audit is retained. The updated LICENSE and AUTHORS.rst inventory is
bound to the installed package; the license text itself was unchanged.
See the [upstream release](https://github.com/jpadilla/pyjwt/releases/tag/2.15.0).

Two additional local probes observed the corrected package reject a deeply
nested unsigned payload with DecodeError and a mismatched OKP key with
InvalidKeyError. These are dependency-level observations, not evidence of an
exploitable bridge authentication endpoint or live-provider qualification.
The bridge source was unchanged.

The imported AUTHORS.rst has a seven-equals RST heading on line 2; Git's
`diff --check` flags it as a possible conflict marker. It is verified upstream
notice content, not an unresolved merge conflict, and its bytes were preserved.
All other changed files pass diff hygiene.

## Subsequent owner-machine acceptance

The initial `29b9daf` run failed native expiry polling and had network-blocked
WSL advisory queries. The corrected `cd5d3f6` rerun subsequently passed native
Windows, WSL2, actual forwarding, Windows direct security and explicit
transferred-advisory acceptance on WSL. See
[reviewed release acceptance](release-acceptance-2026-10-20.md) for identities,
counts and raw-versus-reviewed security outcomes. Earlier failures and blocked
queries are preserved; no result is retroactively relabeled.

The reporting contact is in [SECURITY.md](../SECURITY.md). The owner retains
responsibility for merging and making the repository public. Keep existing
history, source provenance and notices; do not rewrite or force-push. Cleanup
of the earlier disposable live-authentication workspace remains deferred until
the owner's merge and subsequent main verification. No public release or
consumer activation occurred.
