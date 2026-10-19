# Third native Windows acceptance and deadline correction

Owner-run offline acceptance of `19b8b18554bc887aab1ee6e8bae4016b93c3c64a`
(tree `4c5b1a7c0fb0cf4eaf9fc7556da22304488fdca4`) recorded **13 passed,
1 failed, 1 expected Unix-only skip**, runner exit 1. Lint and all 25 transfer
checks passed. This remains a failed acceptance run, not Windows qualification.

The host was Windows 11 Home build 26300, AMD64, local NTFS, non-elevated,
Python 3.12.14 and pywin32 312. All 123 tracked files matched their committed
blobs and remained unchanged. All 207 inventoried receipt files matched their
supplied hashes and sizes. The receipt manifest SHA-256 is
`b13645905d7c43ee26f0e601d38ba272c1c45edb8232a44222980bce94d7f7f1`.
Checksums establish transfer integrity, not signatures. Original receipts and
private destination-local copies of all three Windows failures remain retained.

## Coverage reached

Windows ACL/lock/descendant-death checks, all three artifact persistence
regressions, platform selection and line-ending verification passed. The actual
MCP case completed a task, reconstructed 2,097,245 UTF-8 bytes over 513 pages,
verified its hash and successfully cancelled a second task with confirmed
provider interruption. Read-only inspection independently checked the retained
result bytes/hash and native traffic: three simulated turns and one interrupt.
No real Codex executable or provider/model task ran.

The third task expired without sending an interrupt: `unknown`, reason
`unconfirmed_termination`, `stop_intent=null`, `interrupt_sent=false` and event
sequence 2. The test failed before same-root startup refusal, normal EOF cleanup,
persisted terminal reads, restart/replay and MCP bystander checks. Those remain
unqualified on Windows. Final reported process inspection found no owned fixture
process remaining, which does not substitute for the unreached normal EOF path.

## Shared-service defect and correction

The deadline can cross during `next_event(0.2)`. The no-event branch then saw an
expired deadline and a positive event sequence, and returned unknown before the
next loop iteration could record expiry and send interruption. The sequence
already included the thread/start and turn/start responses; it was not proof
that stopping had been attempted.

A deterministic FakeClient reproduction on macOS confirmed this behavior without
wall-clock sleeps or a provider. Read-only comparison found the event loop was
identical to the pinned Central source at
`7a55276bf76e35ecd1004b79a1256da4cca5bc53`. Central remains untouched.

The no-event branch now requires the persisted interruption-attempt flag before
classifying termination as unconfirmed. If the deadline crossed during the wait,
the next loop iteration records expiry and requests interruption first. Existing
deadlines, stop-intent precedence, interrupt deduplication, terminal classification
and bounded silence handling are retained; no routing or retry redesign is added.

Two new cross-platform deterministic tests use the real store and shared service,
two retained RPC-response events, and a controlled monotonic clock. They cross
the deadline inside the event wait and cover both silence and an interruption
event. They assert expiry is persisted before the request, exactly one interrupt,
unchanged deadline, bounded waits and the existing outcome for each case. Both
failed against the old service and pass against the correction. A synthetic
service test does not qualify the Windows process/stdio lifecycle.

The next native Windows success target is **16 passed, 1 Unix-only skip**, plus
all runner checks. Unix expects **500 passed, 4 Windows-only skips, 1
installed-runtime deselection**. Native Windows still requires a fresh pinned
run through the complete lifecycle before merge.

WSL2 remains untested: designated OADW-Test lacks ensurepip and uv. No distro
package or default-user changes were made. Windows-to-WSL forwarding remains
separately untested. The repository stays private; licensing, public release,
live-provider qualification and consumer activation remain separate gates.

## Corrected snapshot regression results

Both full platform runners exited **0**: macOS 27.0.1 arm64/Python 3.14.6 and
Linux Docker aarch64/Python 3.14.6 as UID 501 each recorded **500 passed,
4 Windows-only skipped, 1 installed-runtime deselected**, plus successful actual
source-module MCP smoke, focused lint and all 25 transfer checks. Linux used a
network-disabled owned container and read-only source/dependencies. No real
Codex/model call or shared-environment change was involved.

The [machine record](provenance/windows-deadline-correction-2026-10-19.json)
binds the tested source bytes and retained receipt hashes. These are Unix
regression results; Windows still requires its next full pinned acceptance run.
