# Platform implementation and validation — updated 2026-10-20

The owner expanded the extraction scope to macOS, Linux, native Windows 11 and Windows 11 with WSL2, with platform selection before integration. This candidate keeps the common MCP/task code and the established Unix backend, adds native Windows filesystem/process/stdio primitives, and ships separate platform test routes. The repository remains private. Native Windows, WSL2 and actual Windows-to-WSL forwarding passed bounded offline acceptance at `cd5d3f6`; merge remains the owner’s decision. See [current release acceptance](release-acceptance-2026-10-20.md). Earlier candidate results and failures remain recorded below.

## Current release acceptance — 2026-10-20

Candidate `cd5d3f69e662752b8aeb6d5e681ec6e8f40051a7` passed native Windows
(19 passes/one skip and three additional lifecycle passes), WSL2
(535 passes/four skips/one deselection), actual forwarding, Windows direct
security and WSL transferred-advisory acceptance. macOS and Linux Docker
also passed 535 tests each. The updated PyJWT 2.15.0 lock is covered.
Review verified all 508 delivered receipt hashes and both checkouts' 152
tracked files. See [release acceptance](release-acceptance-2026-10-20.md).
The prior native failure and blocked WSL network queries remain historical;
the owner controls merge and public visibility.

## WSL2 and Windows-to-WSL offline pass — 2026-10-20

Owner acceptance of `ade75b43ef4a6a6183cfbe9bca035ca6c51caaad` passed both
stages separately. WSL2: **507 passed, four Windows-only skips, one installed-runtime
deselection**, plus lint, 25 transfer checks and MCP smoke. Forwarding: **all seven
check groups passed** through actual `wsl.exe`, with six fixture turns, three
interrupts and empty final owned-process/socket inventories. The initiating
Windows interpreter was 3.12.14; Debian 13.5 WSL2 used Python 3.13.5 as an ordinary
Linux user. Both runners exited 0. All 132 tracked files matched the committed
source on both hosts; all 195 delivered evidence files were hash-verified.

See the [sanitized acceptance report](wsl-offline-pass-2026-10-20.md) and
[machine record](provenance/wsl-offline-pass-2026-10-20.json). Earlier network/setup
failures and an attempt with lost temporary receipts remain retained. The
successful continuation used unchanged candidate bytes. This is offline fixture
evidence, not live-provider or universal-client qualification.

## Forwarding harness development validation — historical 2026-10-19

The new generic Windows-side driver and Linux fixture helper were exercised
in **local Linux development mode**, inside a network-disabled Docker container
as UID 501. All seven lifecycle check groups passed, including six fixture turns,
513-page UTF-8 reconstruction and initiating-client-death cleanup. This does
**not** execute `wsl.exe` or establish WSL2 support. The complete macOS and Linux
platform runners each passed **507 tests, four Windows-only skips and one
installed-runtime deselection**, plus MCP smoke, lint and all 25 transfer checks.
No bridge runtime source changed from the Windows-passed `5fdff37` candidate.

The initial container setup was blocked by Docker mount ownership in Git;
a process-local safe.directory setting allowed that known read-only mount.
The first behavioral run caught a harness polling-count race at deadline;
the driver now yields briefly and uses an elapsed-time bound. Both unsuccessful
receipts remain retained; the bridge was not changed to accommodate the test.
The [machine record](provenance/wsl-harness-validation-2026-10-19.json) pins tested
harness bytes and receipt hashes. Actual WSL2 and forwarding were pending at that point; the 2026-10-20 owner run above supplies their separate evidence.

## Native Windows offline pass

Owner acceptance at `5fdff37e353ec11ec8bb7e237bd2d7658522673d` passed: **16 passed, one expected Unix-only skip**, runner/lint/25-file transfer all exit 0. Windows 11 Home build 26300, AMD64/NTFS, ordinary Windows user, Python 3.12.14. All 126 tracked files remained byte-identical. See the [sanitized result](windows-offline-pass-2026-10-19.md). WSL2 was blocked at dependency setup; Windows-to-WSL forwarding was not run. The [new two-stage procedure](wsl-acceptance.md) addresses those separate targets.

The following correction entries retain their historical status at the time of each run.

## Current deadline correction

The corrected snapshot passes both full macOS and Linux Docker UID 501 runners:
**500 passed, 4 skipped, 1 deselected**, plus actual MCP smoke, focused lint and
all 25 transfer checks; runner exits **0**. Native Windows awaits a fresh full
run with **16 passed, 1 Unix-only skip** as its target. See the
[deadline correction](windows-deadline-rerun-2026-10-19.md).

## Latest Windows deadline result

Owner acceptance at `19b8b18` recorded **13 passed, 1 failed, 1 skipped**. Actual
MCP completion, paginated results, cancellation and all persistence regressions
passed. Deadline expiry missed interruption, leaving the remaining lifecycle
sequence unreached. See [the deadline finding and correction](windows-deadline-rerun-2026-10-19.md).
The corrected Windows target is **16 passes, 1 Unix-only skip**; a fresh owner-run
result is still required. WSL2 and forwarding remain untested.

## Previous persistence correction

The latest corrected snapshot passes both full macOS and Linux Docker UID 501
runners: **498 passed, 4 skipped, 1 deselected**, plus MCP smoke, lint and all
25 transfer checks; runner exits **0**. Native Windows still requires a fresh
run with a target of **14 passed, 1 Unix-only skip**.

## Historical Windows persistence rerun — 2026-10-19

Owner acceptance at `950e79e` recorded **10 passed, 1 failed, 1 skipped**; lint
and all 25 transfer checks passed. Real Windows ACL, lock and descendant-death
tests now pass, but MCP lifecycle fails before turn submission on artifact
directory permissions. See [the persistence correction and rerun record](windows-store-rerun-2026-10-19.md).
Native Windows remains unqualified; WSL2 and forwarding remain untested.

## First corrected Windows candidate regression

After the owner-run failures, the corrected candidate passed the complete macOS
and Linux Docker UID 501 runners: **495 passed, 4 skipped, 1 deselected**, actual
MCP smoke, focused lint and all 25 transfer checks; both runner exits were **0**.
See [corrections and retained failures](windows-acceptance-2026-10-19.md).
Native Windows and WSL2 required a new owner-run result at that checkpoint; subsequent acceptance is recorded above.

## Earlier platform candidate checks

| Environment | Regression suite | MCP smoke | Focused lint |
| --- | --- | --- | --- |
| macOS 27.0.1 arm64, Python 3.14.6 | **494 passed, 4 Windows-only skipped, 1 installed-runtime deselected** | Passed | Passed |
| Linux 7.0.14 LinuxKit aarch64/glibc 2.41, Python 3.14.6, Docker root | **494 passed, 4 skipped, 1 deselected** | Passed | Passed |
| Same Linux Docker image, unprivileged UID 501 | **494 passed, 4 skipped, 1 deselected** | Passed | Passed |
| Native Windows 11 Home build 26300, AMD64, Python 3.12.14, candidate 69be672 | **6 passed, 4 failed, 1 skipped** in diagnostic commands; runner failed before dispatch | Not reached | Passed |
| WSL2 / Windows-to-WSL forwarding | **Not run here** | Pending | Source lint only |

The Linux runs used the existing local `python:3.14-slim` image, owned disposable containers with an init process, a read-only source snapshot, and destination-local dependencies. Network was disabled during tests. Package installation occurred only during explicit preparation. No installed Codex executable, provider/model turn, shared consumer configuration, CLI installation or credential was changed. macOS used the existing checkout environment and private short fixture paths.

The portable stdio tests include real-pipe fixture completion and interruption on Unix, reader cancellation ownership, UTF-8 framing, and the distinction between logical disconnect and process termination. These do not validate Windows ACL or Job Object calls. The Windows-specific route must execute on Windows 11 and report its own results; it is not replaced by these portable tests.

## Failures retained and corrections

The first Linux attempt recorded **23 failed, 464 passed, 1 deselected**. Diagnostic reproduction found that Linux anonymous pipes have link count 1, while the inherited macOS guardian required 0. Linux admission now additionally checks the kernel procfs descriptor target for an anonymous pipe; named FIFO negatives remain. That initial attempt mounted the working checkout during development, so it is not immutable-candidate qualification. Subsequent runs used isolated snapshots.

The next macOS/Linux snapshot runs each recorded **1 failed, 486 passed, 1 deselected**: the unavailable-lock test patched the former `ownership.fcntl` implementation. Its negative assertion was preserved and moved to the platform locking boundary. New portable test development also caught fixture-buffering and stale-cursor mistakes; those test-harness errors were corrected before the final snapshot.

The final platform runner executions each exited **1**, despite passing suite/lint/smoke/count checks: the Linux smoke update had left its destination hash stale in the transfer manifest. The manifest was corrected, and a separate transfer verification passed for all **25 files**. The final candidate's runtime, tools and tests were byte-compared against the tested snapshot and were unchanged. Original failed transfer receipts were retained; no runner result was rewritten as a successful invocation. A full suite was not repeated for a hash-only manifest correction.

The [machine record](provenance/platform-validation-2026-10-19.json) retains counts, hashes of the private receipts and per-file hashes of the tested implementation. Raw private paths and fixture stores are not committed. Source commit/hash provenance is retained in the extraction manifest; changed destination entries retain their earlier extraction hash separately.

## Remaining gates

Native Windows, WSL2 and Windows-to-WSL forwarding acceptance are complete for
the recorded `cd5d3f6` configuration, including the updated lock and security
tools. Historical failures and dependency notices remain retained. Actual
developer-host adoption, live-provider tests, merge and publication remain
separate owner decisions.

The prior macOS live report applies to its recorded earlier code/runtime/model combination. No live Linux, Windows or WSL2 result is claimed. Other Python versions, native x86 Linux outside the tested WSL2 environment, other distributions/filesystems and actual developer-host configurations need their own bounded evidence. The established peer-authentication and non-atomic unlink limitations remain for Unix. Native Windows uses local inherited pipes and jobs with a distinct implementation whose bounded kernel tests passed on the recorded Windows host. The owner confirmed Apache-2.0 on 2026-10-20; exact artifact notices and publication remain separate release considerations.
