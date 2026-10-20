# WSL2 and Windows-to-WSL offline acceptance — 2026-10-20

Both targets passed at commit `ade75b43ef4a6a6183cfbe9bca035ca6c51caaad`,
tree `58aee55d355e4bd4e7015b946f4d5d8b1fa6f3e8`. These are separate offline
fixture results for the same Python package, not separate platform releases.

| Target | Recorded environment | Result |
| --- | --- | --- |
| WSL2 in-distribution suite | Debian 13.5, x86_64, kernel `6.6.87.2-microsoft-standard-WSL2`, ordinary Linux user UID 1000, Python 3.13.5, ext4 checkout | **PASS:** 507 passed, 4 Windows-only skipped, 1 installed-runtime deselected; lint, 25 transfer checks and source MCP smoke passed; runner exit 0 |
| Windows-to-WSL forwarding | Windows 11 Home build 26300, AMD64, non-elevated user, NTFS, native Python 3.12.14 initiating the generic MCP driver through `wsl.exe` | **PASS:** all seven check groups, six fixture turns, three expected interrupts; exit 0 and empty final owned-process/socket inventories |
| Native Windows bridge | Not rerun in this acceptance | Earlier [offline PASS](windows-offline-pass-2026-10-19.md) remains pinned to `5fdff37` |

## Observed behavior and verification

The actual Windows initiating client exercised discovery of the four MCP tools,
errors, exact replay and conflicting duplicate rejection, bounded status with
advancing cursors, complete UTF-8 result pagination/hash, confirmed cancellation,
deadline interruption, same-root losing-startup isolation, EOF cleanup and
persistent terminal reads without native traffic. It also exercised adapter
death/restart uncertainty, replay without resubmission, an independent root
continuing, and cleanup after abrupt Windows client death. `--local` was not
used. The destination adapter, runtime and guardian identities were checked.

The complete retained result was **2,097,244 bytes over 513 pages**, SHA-256
`0a2cae42794877b88171d82fac18e41595839141c64471b9c558cc7f41c2ee68`.
Inspection of the archived fixture result independently confirmed this hash and
length. Original request logs confirmed one turn per fixture case, six total,
and one interrupt each for cancellation, expiry and client-EOF shutdown.
Cancellation was followed through to confirmed fixture interruption; the
fixture's canned history does not establish live-provider reconciliation.

Both checkouts remained clean. All **132 tracked files** matched their committed
Git blobs, each other and their before/after inventories. The review independently
matched the two harness source inventories against a local Git archive of the
pinned commit, checked the JUnit counts, inspected the Windows command receipt,
verified archived native request counts/result bytes, and hash-verified all
**195 delivered evidence files** (80,773,981 bytes).

The receipt-manifest SHA-256 is
`dd74a06b62fee18ce8dd5fb301e77d7242e0d3d6dce930091d850d275d76dac0`.
The [machine record](provenance/wsl-offline-pass-2026-10-20.json) retains candidate
identity, check counts and key receipt hashes. The original private report is
`acceptance-wsl-ade75b4-2026-10-20-continuation/acceptance-report.md` in the owner's
receipt collection. Raw host paths, stores and logs remain in that private
collection rather than this repository.

## Retained setup and collection failures

An initial Linux uv download timed out. After owner authorization, the exact
pinned uv 0.12.18 archive was downloaded through Windows, transferred and
hash-verified again inside Linux. A later locked dependency download also failed;
its original log remains retained. The authorized alternative transferred
35 Linux/CPython 3.13-compatible wheels, checked every lock hash and size on both
sides, installed offline and confirmed the original locked sync offline. No
Python download, dependency-version substitution or global install occurred.

The first Stage A execution printed success, but its `/tmp` receipts disappeared
when WSL stopped. That attempt remains **incomplete evidence**, with console and
collection failure retained. An owner-authorized unchanged rerun archived the
entire receipt root to persistent ext4 before session exit; that rerun supplies
the reported Stage A PASS. Stage B used an owned stdin-held WSL session until
collection finished, then closed it normally. No distro shutdown/default or
shared-service change was made.

Both successful archives retain original regular-file payloads. One Stage A
socket node is represented by explicit path/mode inventory metadata because tar
cannot serialize a Unix socket; it is not a missing regular-file receipt.
Stage B's final socket inventory was empty. The original blocked attempt and
ancillary audit-wrapper error are retained separately. No shipped assertion,
source byte, lock version or test deadline was changed for acceptance.

## Limits and remaining decisions

This establishes the recorded offline WSL2 and generic Windows-to-WSL client
combination. It does not establish live-provider operation, arbitrary MCP hosts,
other distributions/filesystems, or a new native Windows run. No real Codex,
authentication, credentials copy, model turn or production registration was used.
No candidate bridge defect was observed in the executed scope.

The uv archive contains executables but no bundled license/NOTICE text; the
archive/content inventory and original locked wheels remain retained. Existing
dependency notices remain preserved. The owner subsequently confirmed
Apache-2.0 for the extracted implementation; see [license disposition](../LICENSE-DISPOSITION.md).
Live-provider qualification, consumer adoption, merge and publication remain
separate owner decisions. The repository stays private.
