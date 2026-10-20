# Release acceptance — 2026-10-20

**The two outstanding technical acceptance blockers are closed for candidate
`cd5d3f69e662752b8aeb6d5e681ec6e8f40051a7`.** The owner retains merge and public
visibility decisions. This is readiness for the documented source distribution
and tested offline environments, not universal-client or new live-provider
qualification.

## Candidate and evidence

The Windows and WSL2 checkouts were complete, clean and unchanged at tree
`0664bb99a7f8b01e7470ad9d5e31718e3c7a404e`; base was
`64056785b21b51e269098a47583111723845542d`. Review compared both hosts' 152
tracked-file hashes before/after with the committed source bytes and verified
all 508 delivered files. The delivery manifest SHA-256 is
`93c197d90d925256ce79679b20ac4c0e083d25b015dc141fae5145858964f63a`.
Raw receipts remain private; this sanitized record contains no fixture stores
or private host paths.

| Check | Accepted observation |
| --- | --- |
| Native Windows 11 Home build 26300, AMD64, local NTFS, ordinary user, Python 3.12.14 | 19 passed, one explicit Unix-only skip; runner, lint and 25 transfer checks passed |
| Three additional native lifecycle runs | One pass each, exit 0 each; no failed behavioral retry |
| Windows security regressions | 25 passed |
| Windows direct security | All seven checks passed; 37 bridge and 34 security-tool package/version entries, zero known advisories or missing/skipped packages |
| WSL2 Debian 13.5, x86_64, ext4, UID 1000, Python 3.13.5 | 535 passed, four Windows-only skips, one installed-runtime deselection; MCP smoke, lint, transfer and runner passed |
| WSL security | Five scans executed locally; two advisory checks accepted explicitly transferred fresh Windows evidence; runner exit 0 |
| Actual Windows-to-WSL forwarding | Seven groups passed; six fixture turns, three interrupts, 513-page reconstruction and owned cleanup |
| Prior local macOS and Linux Docker checks of this implementation | 535 passed each, four Windows-only skips, one installed-runtime deselection; MCP smoke, lint, transfer and direct/transferred security checks passed as recorded in the PR |

Both hosts used Gitleaks 8.30.1, Bandit 1.9.4, Ruff 0.16.9 and pip-audit 2.10.1.
All three secret-scan reports were empty on both hosts. Bandit reported 22
findings with raw exit 1; review recomputed all exact committed fingerprints,
with no new/stale findings or parse errors. This is reviewed acceptance, not a
finding-free Bandit scan. The 52 inherited broad test-file Ruff diagnostics
remain separate from focused lint.

## Closure of the Windows deadline failure

All four lifecycle executions reached every previously unreached assertion:
losing startup, persisted reads, independent-root continuity, adapter death
and restart/replay without resubmission, in addition to completion, pagination
and cancellation. Retained stores confirmed expiry as
`failed / deadline_exceeded`, `stop_intent=expiry` and `interrupt_sent=true`.
The original and retained deadlines matched in all four runs.

Each raw native request log contained five starts and two interrupts; final
states were two completed, one cancelled, one expired/failed and one unknown
after restart. The original `29b9daf` failure remains retained. The corrected
elapsed-time poller passed these observations without changing task deadlines
or runtime code; this is not proof of every possible scheduling outcome.

## Closure of the disconnected WSL advisory gap

The original Windows query began at `2026-10-20T17:30:49.170034+00:00`.
The WSL check began 38.131695 seconds later. Review re-executed receipt
validation at that recorded receiving time: exact commit, both locks,
pip-audit version, successful direct source checks, full inventories, source
summary/report digests and freshness all matched.

The source summary was byte-identical in the WSL receipt. Recipient reports
were reformatted JSON with identical parsed advisory contents; the recorded
source digests matched the original report bytes. These are **transferred
advisory results**, not local WSL network queries. No network/TLS policy
change or online WSL retry occurred. The earlier blocked query remains
blocked historical evidence. Dated zero-advisory observations are not a
permanent guarantee.

## Forwarding, cleanup and release decision

Native Windows Python drove actual `wsl.exe`, with the expected candidate.
Review matched the archived result's 2,097,244 bytes to SHA-256
`0a2cae42794877b88171d82fac18e41595839141c64471b9c558cc7f41c2ee68`.
The seven groups include cancellation, expiry, persistence, ownership,
restart and initiating-client-death cleanup. Final scoped Windows and WSL
inventories contained no owned processes or sockets. Linux temporary receipts
were archived before normal session exit. The shared delivery relies on the
owner's share access controls; it does not establish Windows ACL privacy on
SMB. No credentials or real authentication homes were included.

The existing Apache-2.0 owner decision and retained dependency notices remain
in force. No license decision is reopened by this acceptance. Git history and
earlier failed receipts remain intact. Unix peer-authentication and non-atomic
unlink limitations remain documented.

**Recommendation: accept the private PR for manual merge and public source
release within the documented scope.** The evidence-recording follow-up changes
documentation only; the PR verifies runtime, tests, tools and both locks against
the tested candidate. After merging, verify main before changing visibility and
cleaning only this task's disposable workspaces/authentication, retaining evidence.
No merge, publication or consumer activation is performed by this record.
