# Platform implementation and validation — 2026-10-19

The owner expanded the extraction scope to macOS, Linux, native Windows 11 and Windows 11 with WSL2, with platform selection before integration. This candidate keeps the common MCP/task code and the established Unix backend, adds native Windows filesystem/process/stdio primitives, and ships separate platform test routes. The repository remains private. The first owner-run Windows acceptance failed; a corrected candidate needs a new run. WSL2 qualification and merge remain pending. See [the Windows report and corrections](windows-acceptance-2026-10-19.md).

## Corrected Windows candidate regression

After the owner-run failures, the corrected candidate passed the complete macOS
and Linux Docker UID 501 runners: **495 passed, 4 skipped, 1 deselected**, actual
MCP smoke, focused lint and all 25 transfer checks; both runner exits were **0**.
See [corrections and retained failures](windows-acceptance-2026-10-19.md).
Native Windows and WSL2 still require a new owner-run result.

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

Rerun the corrected native Windows 11 candidate as a standard user on local NTFS and retain fresh receipts. The first run's binding and CRLF failures remain recorded; its 17 Windows dependency notices have been retained. Run the WSL2 route separately and verify the Windows host's stdio forwarding. The supplied handoff is pinned to the pushed candidate; it does not authorize real Codex/provider calls, changes to shared configuration, fixes without reporting them, or merge/publication.

The prior macOS live report applies to its recorded earlier code/runtime/model combination. No live Linux, Windows or WSL2 result is claimed. Other Python versions, x86 Linux, other distributions/filesystems and actual developer-host configurations need their own bounded evidence. The established peer-authentication and non-atomic unlink limitations remain for Unix. Native Windows uses local inherited pipes and jobs with a distinct implementation that awaits its kernel tests. Licensing/notice clearance remains an owner gate before public release.
