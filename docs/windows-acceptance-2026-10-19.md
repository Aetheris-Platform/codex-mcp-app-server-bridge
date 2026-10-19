# First native Windows acceptance and corrections

The owner supplied an independently executed offline Windows 11 report for
`69be6729a2e10df0f80d77d0deb2edff20687a5c`, tree
`9ee60f1979110d79a9e5d899e418827bb2f50267`. **Native Windows: FAIL. WSL2 and
Windows-to-WSL forwarding: NOT TESTED.** This report does not qualify the
corrected candidate; a new pinned Windows run is required before merge.

The host was Windows 11 Home build 26300, AMD64, local NTFS, a non-elevated
user and existing Python 3.12.14, with locked pywin32 312. The shipped platform
runner exited 1 during private receipt-directory setup, before test dispatch.
Separate diagnostic execution of its unchanged commands recorded **6 passed,
4 failed, 1 expected Unix-only skip**, lint exit 0 and transfer exit 1. The
actual MCP lifecycle never started, and no native fixture turn ran.

All 66 supplied receipt files matched their size/hash inventory. The inventory
SHA-256 is `da764c9895ea1620442fcd4391a2a781823e0e781024846d884570fae6cc8ead`.
Its detached checksum detects transfer corruption; it is not a digital signature
or independent authentication. The initial/final inventories of 99 tracked
checkout files matched. Clean Git status was retained. Private raw reports,
JUnit, diagnostics and failed-run logs were copied into the destination's
ignored local evidence area without modifying the owner-supplied originals.

## Corrections

- Both token-query sites incorrectly treated pywin32's `PyHANDLE` as a context
  manager. They now use `try/finally` and explicit `Close`, including when a
  token query fails. The existing ACL assertions are unchanged.
- pywin32 312 rejects `None` for the Job Object name. A typed
  `CreateJobObjectW(NULL, NULL)` binding now returns an owned raw handle,
  explicitly closed by `JobProcess.close`. Microsoft's [API contract](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-createjobobjectw)
  specifies an unnamed job and a non-inheritable handle for these arguments.
  Atomic child assignment and kill-on-close behavior remain required by the
  existing Windows kernel tests; source inspection cannot establish a pass.
- `core.autocrlf=true` changed all 25 provenance-checked working files to CRLF.
  The Windows diagnostic found that all committed blobs still matched their
  expected hashes. Repository attributes now keep text checkouts at LF while
  preserving retained third-party notice bytes. A real disposable Git-index
  checkout regression verifies the 25 files with autocrlf enabled, then changes
  one to CRLF and confirms the strict verifier rejects it. The verifier does
  not normalize or ignore mismatches.

The new regression raises the expected native Windows result to **11 passes,
1 Unix-only skip**; Unix expects **495 passes, 4 Windows-only skips, 1
installed-runtime deselection**. These are success targets, not Windows results.

## WSL and licensing

The owner designated the existing `OADW-Test` WSL2 distribution. Read-only checks
found Debian 13.5, Linux 6.6.87.2 WSL2 x86_64, ext4, Python 3.13.5, default UID 0,
no uv and no `ensurepip`. The prescribed venv bootstrap was blocked. No distro
installation, default-user change or global package installation was performed.
WSL acceptance requires an available local setup route or separately authorized
prerequisite provisioning. Windows-to-WSL forwarding remains a separate gate.

The supplied receipts included 16 pywin32 notices and one colorama license.
Their exact bytes and original distribution-relative filenames are now retained
in [the Windows notice inventory](third-party/windows/inventory.json). This
completes retention for those reported installed artifacts, not public licensing
clearance. Existing source attribution and owner release decisions remain.

The reported cleanup found no owned fixture processes remaining, but no native
child had successfully started, so it does not prove Job Object cleanup. No
live Codex/provider call, authentication, registration, or consumer activation
was part of this Windows acceptance session.

## Regression checks after correction

An immutable snapshot of the corrected runtime, tools and tests ran on macOS
27.0.1 arm64/Python 3.14.6 and on Linux Docker aarch64/Python 3.14.6 as UID 501.
Both platform runners exited **0**, with **495 passed, 4 Windows-only skipped,
1 installed-runtime deselected**, plus successful actual MCP fixture smoke,
focused lint and all 25 transfer checks. The Linux container had networking
disabled and read-only source/dependencies. Git was added only to a disposable
local test image for the new checkout-conversion regression.

An earlier sandbox-restricted macOS run exited 1 with **48 failed, 447 passed,
4 skipped, 1 deselected** and socket/process permission errors. Its receipts
remain separate. The successful rerun used the same code with local socket and
process access; no failing assertion was removed. The attempted interruption
found the earlier pytest process had already exited and killed no process.

The [machine record](provenance/windows-corrections-2026-10-19.json) binds tested
source bytes and receipt hashes. These Unix checks do not execute Windows token
or Job Object APIs. The existing Windows tests must run again on the corrected
candidate. No live-provider call or consumer registration was involved.
