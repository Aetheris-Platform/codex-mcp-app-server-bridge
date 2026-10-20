# Standalone bridge contract

For application developers integrating an MCP host, start with the [developer integration contract](integration-contract.md). This document supplies the underlying operational guarantees and accepted residuals.

This standalone contract defines the bridge's transport, task and resource-ownership behavior. Its native client/thread identity is `codex-mcp-app-server-bridge`. No external verifier or workflow system is required.

The bridge provides local transport, task persistence and native-journal capture. It grants no workflow, review, merge or release authority. Operators own private profiles, endpoints and activation.

## Interface and results

The stdio MCP interface exposes exactly `codex_start`, `codex_status`,
`codex_result`, and `codex_cancel` to MCP hosts. It uses native JSON-RPC over
WebSocket connections through Unix sockets on macOS/Linux/WSL2 or private stdio pipes on native
Windows, explicit model and worktree selection,
and no removed `codex mcp-server` path or model fallback. A valid start returns
a durable task ID within its start bound, without waiting for coding; later
calls address the same provider thread and turn. A complete result larger
than an inline reply is returned in bounded byte pages with the digest of the
full unchanged result. A truncated display is never the authoritative output.

## Exactly-once dispatch and uncertainty

Dispatch intent and bound inputs are persisted exclusively before provider
submission. Identical request-ID replay resolves the existing task; a reused
ID with different worktree or other bound inputs fails before invocation.
Lost acknowledgement is uncertainty, not permission to submit a second turn.
Provider evidence may reconcile that uncertainty read-only; no automatic new
work is started. A cancellation accepted before a delayed start response
remains durable: no unsubmitted turn is started, and an already-submitted turn
is interrupted or reported uncertain without false success.

## Deadlines, permissions and cancellation

The originally admitted task deadline and sandbox remain bound across status
polling, reconnection, and replay. Tool-call timeouts do not replace or renew
that deadline. A bounded status wait that ends while work runs leaves the
same task addressable at its original deadline. Before turn submission, the
effective provider thread permissions must remain within the operator
ceiling, including writable roots and approval routing. Broader roots,
automatic approval, or an unsupported escalation fail before any turn; model
requests and host auto-approval do not create human authority. Cancel reports
request, acknowledgement, confirmed termination, and unknown outcome
distinctly; acknowledgement alone is not confirmed cancellation.

## Evidence custody and non-authority

Retain original provider output and available journals with truthful source
metadata, identities and digests. A displayed final answer with an incomplete
journal retains its evidence limitation; result availability and journal
completeness are reported independently. The bridge does not rewrite source
metadata or claim evidence it did not capture. Applications decide how to use
the result; journal capture grants no approval or workflow authority.
The canonical result is assembled from explicit final-answer items in native
full history, once each in history order with original text. Commentary,
partial history, streaming duplicates and unknown output phases are not
complete final output.

## Guardian, process ownership and recovery

The adapter retains a cooperative lifetime claim on the private local task-store
data root, independent of profile filename or registration name. The supported
POSIX adapter uses a persistent private `.adapter-owner.lock` inode and
nonblocking exclusive `flock`. Only missing-root and lock-file creation may
precede acquisition; CLI probing, store initialization, record repair and restart
reconciliation must follow it. Contention refuses without invoking owner cleanup
or modifying existing task/artifact/socket state. The descriptor is not inherited
by guardian/provider children and is retained until requests, workers and owned
runtime cleanup stop. Repeated shutdown and stale service calls cannot mutate
a successor's store. Constructor/setup failures release the new claim.

Shutdown does not unlink the lock file. Kernel state, never a PID/stale-file
heuristic, controls ownership. A successor after process death may reconcile
nonterminal records using the existing unknown/adapter_restarted behavior; this
does not prove provider termination and does not authorize task resubmission.
The existing socket-custody limitations remain. This is not a new daemon,
distributed lock, native Windows qualification claim, peer authentication, or defense against malicious
same-user pathname replacement. Noncooperating old adapters must be stopped
before cutover. No profile/task schema, native evidence profile, runtime policy
or dependency change is made.

Platform amendment (2026-10-19): the preceding paragraph describes the extracted
POSIX implementation. The owner subsequently requested macOS, Linux, native
Windows 11 and WSL2 integration. [Platform setup and acceptance](platforms.md)
defines that extension and its evidence limits. Linux admits anonymous pipe
descriptors using the kernel procfs target, because its anonymous pipes have
link count 1. Named FIFOs remain rejected. Existing Unix socket/alias admission,
guardian ownership and cleanup rules below remain unchanged.

Native Windows profiles use `socket_path: null`; there is no socket listener,
alias or TCP fallback. Private DACL validation replaces Unix uid/mode checks,
reparse points are refused, and a persistent file with an exclusive nonblocking
Windows kernel byte-range lock enforces the same data-root ownership order.
Each native stdio connection has a child assigned atomically to its own private
kill-on-close Job Object before execution. Child descendants inherit that job;
the adapter's job handle is not inherited. Logical connection closure drains
output and leaves the child owned until session shutdown, preserving the fact
that disconnect does not confirm cancellation. Kernel handle closure on adapter
death terminates jobs. Normal shutdown requests job termination and checks that
all members exited. This uses more processes than Unix's shared socket backend;
it does not add task continuation, routing fallback or an unattended service.
Native Windows kernel behavior and Windows-to-WSL forwarding require separate
Windows 11 receipts before qualification. The macOS live receipt is not evidence
for these new routes or this changed candidate.

The operator profile pins an executable by absolute path, SHA-256 and exact
`--version` output. Runtime drift blocks dispatch. The adapter owns one
session-scoped local guardian, app-server child and endpoint; it does not
install an unattended service or promise survival across host shutdown.
The adapter creates an anonymous liveness pipe, keeps its sole write end,
and launches the guardian in a separate process session with only the read
end inherited. The guardian starts lazily, checks adapter liveness before
and immediately after spawning exactly one verified app-server child in its
own process group, and bounds socket startup at five seconds. The provider
inherits neither pipe end; unrelated descriptors are closed. The pipe is a
lifetime signal, not a command channel, and no model input reaches it. The
adapter remains the admission, deadline, and task-state owner. The helper
adds no PID file, process-table scan, public listener, fifth MCP tool,
shared daemon, dependency, or host-specific hook.

The guardian retains the direct child handle, watches both adapter loss and
child exit, and may terminate only that child's process group. It accepts
no caller-supplied PID or PGID and must reap the child before its identity
could be reused. On adapter input close, admission stops and the adapter
first attempts task interruption, then closes its pipe and waits for the
guardian. Pipe EOF, unexpected pipe data, or guardian TERM/INT initiates
child cleanup: send TERM, wait at most five seconds, send KILL if needed,
then bound the final wait at five seconds. The adapter's guardian wait covers
those bounds plus scheduling margin. Cleanup failure remains observable;
unconfirmed termination cannot become confirmed cancellation or success.

If the adapter is forcibly killed, closure of its sole pipe writer triggers
the same bounded child cleanup and removal of only the matching owned
endpoint. A socket is removed only if its captured device/inode, type, and
ownership still match; a replacement or unexpected symlink is left untouched.
For the admitted 0.156.0 alias, cleanup instead requires the captured link
and its physical socket to match as specified below. The
guardian exits after cleanup, startup failure, or observed child exit; it
never restarts itself or Codex. Repeated MCP calls share the
one runtime. Only a later explicit adapter request may use the existing
runtime recreation path after cleanup; orphaned or uncertain work is never
silently adopted. If the guardian is killed, wedged, or lost to machine
failure, cleanup is not guaranteed, retained work stays uncertain, and
stale resources fail admission. There is no guardian-of-guardian, task
store, credential store, or manufactured terminal evidence in the helper.

## Endpoint topology, admission and residuals

Exactly two endpoint shapes are admitted at the requested profile socket:

1. A direct, non-symlink private Unix socket, preserving the 0.154.0 device
   and inode identity rule. Tightening permissions on the same socket does
   not create a new socket; a rebind does.
2. A 0.156.0 deterministic symlink to the independently derived physical
   socket at `<realpath("/tmp")>/codex-daemon-<euid>/<sha256(realpath(parent)
   + "/" + name)>`. The rendezvous directory must be a non-symlink,
   user-owned directory with mode exactly 0700. The target must be a
   non-symlink Unix socket owned by the current user with no group/other
   permission bits. No other symlink, foreign daemon, or shared instance is
   admitted.

The alias link identity and physical socket identity are both captured. The
client connects to the captured physical socket, then re-admits that endpoint
after connection and before any JSON-RPC request. A re-created/re-pointed
alias or re-bound socket changes identity and refuses reuse. A substitution
still present at re-admission is refused before protocol traffic, although
the WebSocket handshake may have reached the substitute. If a substitute
serves the connection and the captured socket is restored before the check,
path-based admission cannot detect it: the substitute receives the handshake.
This accepted residual is not peer authentication.

Cleanup removes the alias and physical socket only while the captured link
and the socket it names still match. A missing socket under an intact alias,
replacement entry, or any other mismatch is left for the operator. Lock
files, other entries and the rendezvous directory are never removed. The
identity comparison and separate unlinks are not atomic: a replacement
inside the cleanup window can be removed, and a reused socket inode cannot
be distinguished. No stronger protection is claimed. A dangling alias at
the requested path refuses startup until the operator resolves it.

## Status cursor and bounded waiting

`codex_status` accepts a task ID, optional `wait_seconds` in 0..30 (default
0), and optional non-negative `cursor` (default 0). Every reply reports the
retained event sequence as its cursor; callers pass that cursor next time.
A cursor beyond retained sequence is invalid. Zero wait returns current
status immediately. With positive wait, a task marked `unknown` for
read-only reconciliation (`adapter_restarted`, a `reconciliation_` reason,
`output_phase_unknown`, or `full history is unavailable`) and retaining
thread/turn IDs schedules and awaits that reconciliation. Otherwise, if no
event exceeds the supplied cursor, wait for a newer event or a state/reason
change; if newer events already exist, return immediately. Every wait ends
at the earlier of its requested duration and remaining admitted deadline.
No wait renews the task deadline. This section documents existing behavior,
not a new notification or resilience promise.

## Host interface and notification claims

The host retains the task ID, waits boundedly or inspects progress, and
retrieves the complete result or records an explicit human decision. A
successful start is not completion. Continuous polling and a new user prompt
after every short call are not required. Automatic notification may be
claimed only after that host integration demonstrates it. The prior apparent
notification failure was an in-process probe polling with a stale cursor
while starving its own event loop; it did not establish an adapter repair.

## Profile and task-record compatibility

The strict operator profile fields remain `runtime_path`, `runtime_version`,
`runtime_sha256`, `socket_path`, `allowed_worktree_roots`, `allowed_models`,
`allowed_sandboxes`, and `data_root`. The private task-record schema remains
version `1`, with its current request, request digest, worktree component
identity, runtime/profile hashes, deadline, state/reason, thread/turn,
submission/stop flags, event sequence, completed items, provider outcome,
result, pending event, native journal and artifact fields. The package
relocation does not migrate, rewrite or invalidate records or journals. A
controlled restart must still read retained tasks from the same private
profile/store. The standalone native client and thread-source literal is
`codex-mcp-app-server-bridge`. Consumers must use that truthful identity.

## Source scenario coverage

The consolidated sections above preserve the transport and lifecycle scenarios
from the pinned source specs. External workflow-adoption requirements are
excluded. Base: start before completion, large result, lost
acknowledgement, conflicting request ID, bounded status timeout, cancel
acknowledgement, broader permissions, incomplete journal, truthful source
metadata, adapter shutdown, adapter kill, guardian loss, runtime drift, host
follow-through, inherited permissions, cancellation/start race, and
commentary versus final output. Socket amendment: deterministic alias,
direct socket, unsafe layout, alias/socket replacement, connect-window
substitution, reverted substitution, missing socket under captured alias,
direct permission transition, and dangling alias. Status amendment: cursor
advancement, already-retained events, restarted-task reconciliation, and
future cursor rejection. Pinned source requirements and their hashes are indexed in
[the context manifest](provenance/context-manifest.json).
