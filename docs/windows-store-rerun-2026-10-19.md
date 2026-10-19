# Second native Windows acceptance and persistence correction

The owner-run offline acceptance of `950e79e5c604eadbb7b1856d587cba7d96f72f0b`
(tree `f4d65e743b14067c3ec5c669f571f22479515985`) remains **FAIL**: the shipped
runner exited 1 with **10 passed, 1 failed, 1 Unix-only skip**. Lint and all 25
raw transfer checks passed. The host remained Windows 11 Home build 26300,
AMD64, local NTFS, non-elevated, Python 3.12.14 and pywin32 312.

All 180 inventoried receipt files matched their supplied hashes and sizes.
The receipt manifest SHA-256 is
`df88ba9fe68a6f8871490bda28f5d52063395c9993aaba182cf5cfadefbc7ecd`.
Before/after inventories agree for all 120 tracked files. The reporter also
verified all checkout bytes against committed blobs with autocrlf still enabled.
Original receipts and a private destination-local copy are retained. Checksums
provide integrity checks, not signatures or independent authentication.

## Executed coverage and remaining failure

Real Windows ACL/world-access/junction checks, exclusive locks and reuse, and
child/descendant death cleanup passed. The actual MCP test initialized, discovered
four tools and exercised start, duplicate replay, conflict rejection and status.
The native fixture reached `thread/start`, but **no `turn/start`**. Task state
became `unknown` because its artifact directory failed the strict ACL check.
Successful completion, result paging, cancellation, expiry, normal EOF cleanup,
restart and the MCP bystander sequence were not reached. Discovery of a tool
does not establish that it was exercised.

Saved ACL inspection showed that root/tasks/artifacts directories used the
expected user/SYSTEM DACL. The per-task directory instead had an OWNER RIGHTS
allow ACE, created by `Path.mkdir(mode=0o700)` on this Python/Windows combination.
The validator correctly refused that descriptor under the existing contract.
This was not an observed world-access grant.

## Correction and regression scope

All four remaining store artifact-directory creation sites now use the existing
`make_private_directory` helper, retaining their subsequent ACL and reparse-point
checks. Existing directories are not rewritten or repaired. The failed Windows
store remains untouched.

Inspection also identified three artifact metadata writers using native Windows
backslashes where the record contract expects forward slashes. They now call
`as_posix()` for persisted relative paths. The validators and record schema are
unchanged. This adjacent defect was reproduced separately using Windows path
string semantics on a real local filesystem; it was not reached by the reported
Windows lifecycle run.

Three new parameterized store tests exercise a freshly created event directory,
result directory and native-journal directory independently; they verify reload,
canonical relative paths, content hashes/bytes and directory privacy. They use
real Windows ACL validation when run on Windows. On Unix, they emulate only
Windows relative-path encoding and verify Unix permission bits. The three tests
failed against the old store implementation and passed against the corrected
implementation. This is not a substitute for the Windows kernel rerun.

The next success targets are **14 passes, 1 Unix-only skip** on native Windows
and **498 passes, 4 Windows-only skips, 1 installed-runtime deselection** on Unix,
plus all runner checks. No Windows success is claimed for the correction yet.

WSL2 remains untested: owner-designated OADW-Test still lacks ensurepip and uv.
No distro prerequisites were installed. Windows-to-WSL forwarding remains
untested. The reported failed-run cleanup found no owned fixture process left;
it does not establish the unreached normal-shutdown path. No live provider,
authentication, registration, consumer, or global environment was changed.

## Corrected snapshot regression results

macOS 27.0.1 arm64/Python 3.14.6 and Linux Docker aarch64/Python 3.14.6
(unprivileged UID 501) both ran the immutable corrected snapshot. Each full
platform runner exited **0** with **498 passed, 4 skipped, 1 installed-runtime
deselected**; actual source-module MCP smoke, focused lint and all 25 transfer
checks passed. Linux used a network-disabled owned container with read-only
source/dependencies. These runs do not qualify Windows ACLs or the full Windows
lifecycle. No provider task or installed Codex executable was started.

The [machine record](provenance/windows-store-correction-2026-10-19.json) binds
tested source and retained receipt hashes. The owner must rerun the corrected
pinned Windows candidate before accepting native Windows support or merging.
