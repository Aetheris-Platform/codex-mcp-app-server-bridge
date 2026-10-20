# Native expiry polling and disconnected advisory acceptance

## Reviewed owner-machine result

Candidate `29b9dafd2f64097ddcf55ce8e5350b776cfd249f` was tested on native
Windows 11/Python 3.12.14 and WSL2 Debian/Python 3.13.5. Review verified all
343 delivered file hashes and both hosts' 147 tracked files against committed
bytes, before and after execution. The delivery manifest SHA-256 is
`e6fa50811155ffc0f74d595f806cb293d4a030e12a8d8bcb4c00dfbd6877922e`.

- Native Windows: 15 passed, one failed, one Unix-only skip. The lifecycle test
  exhausted 30 status replies while the expiry task still reported running.
  Later lifecycle assertions were not reached. The failure is retained.
- Windows security: all seven checks passed; 12 separate regressions passed.
- WSL2: 519 passed, four Windows-only skips, one installed-runtime deselection;
  MCP smoke, lint and transfer passed.
- WSL security: five local checks passed; two advisory queries failed because
  the network was unreachable. Their vulnerability counts are unavailable.
- Actual Windows-to-WSL forwarding: all seven groups passed, six fixture turns,
  three interrupts, 513 pages and matching result hash; owned cleanup passed.

The WSL and Windows advisory inventories were byte-identical; Windows checked
all 37 bridge and 34 security-tool package/version entries without advisories.
This does not turn the failed WSL query into a pass.

## Bounded correction

`codex_status` limits its wait by the task deadline and can return immediately.
Counting 30 replies therefore cannot enforce a 30-second observation window.
The native acceptance helper now uses a 30-second elapsed-time budget, a
five-second bound per reply and a 50-ms pause between nonterminal replies.
Timeout failures retain the last status. It does not extend task deadlines,
resubmit work, retry failed tests or change bridge runtime scheduling.

Expiry assertions retain the unchanged deadline and expiry stop intent and
add an explicit `failed / deadline_exceeded` assertion for the confirming
fixture. Regressions cover more than 30 immediate replies, a task that never
becomes terminal and a hung status call. Existing deadline-worker tests retain
the silent-provider and confirmed-interruption cases.

The poll-count defect is established by code and focused regression. Whether
it fully explains this Windows failure requires the pinned native rerun;
no Windows pass is claimed from tests on macOS/Linux.

For disconnected WSL, the [security procedure](security-testing.md#disconnected-wsl-advisory-checks)
now supports explicit, trusted receipt transfer from a connected maintainer
machine. Exact commit/locks/tool version, full inventory, successful direct
queries, receipt digests and a 24-hour freshness bound are required. Local
scans still run on WSL. Reports label this as transferred evidence, never a
local query. No network policy, TLS setting, credentials or shared runtime
changes are needed.

## Remaining acceptance

Current targets are 535 Unix passes with four skips/one deselection; native
Windows 19 passes/one skip; separate Windows security regressions 25 passes.
The private PR records executed local results and the pinned Windows rerun.
The rerun must complete the previously unreached lifecycle assertions and
exercise direct Windows plus transferred WSL security evidence.
Do not merge or publish until the owner accepts those receipts. Existing
history, earlier failures, licensing decision and release control are preserved.
