# Developer integration contract

This is the caller-facing contract for the current `0.1.0` checkout interface. It documents existing behavior; it does not add tools or change runtime semantics. Use it with the [setup instructions](../README.md#get-and-prepare-your-local-checkout) and [operational contract](contract.md).

## Parties and responsibilities

| Party | Responsibility |
| --- | --- |
| Operator | Install locked dependencies, select and authenticate the pinned Codex executable, prepare the private profile/store and Unix socket directories where applicable, choose allowed workspaces/models/sandboxes and protect credentials and retained data |
| MCP host/application | Launch the local bridge, initialize MCP, persist the original request and returned task ID, advance status cursors, assemble result pages, handle uncertainty and follow cancellation through to an outcome |
| Bridge | Validate admission, persist task identity, translate the four MCP tools to native Codex, enforce its configured constraints, retain outcomes and manage only its owned runtime resources |
| Native Codex/provider | Execute model turns under its own authentication, permissions and availability; supply native events/history and interruption outcomes |

The operator can configure reasoning effort in the selected Codex home's `config.toml`. It is not a `codex_start` argument. No tool accepts credentials, a provider override, fallback model, approval decision or workflow authorization. Output does not approve a review, merge or release; the consuming workflow makes those decisions.

## Connection and lifetime

The host starts `python -m codex_app_server_adapter --profile <existing-private-profile>` using absolute interpreter/profile paths and `PYTHONPATH` pointing to the checkout's `src` directory. See the [registration example](../README.md#launch-from-any-directory); adapt its enclosing configuration format to the host. Installation and authentication are separate setup steps, never runtime downloads.

Use standard MCP initialization and tool discovery over stdin/stdout. The bridge reports server name `codex-app-server`, version `0.1.0`; the tested MCP protocol revision is `2025-06-18`. The native client identity is `codex-mcp-app-server-bridge`. Native thread/turn RPC methods are not accepted as MCP tools.

Keep the bridge process and its stdin alive while tasks run. Asynchronous submission means a tool call can return before the task completes; it does not make the task independent of the adapter process. Closing stdin initiates bounded shutdown and interruption of owned work on every platform; Unix also installs SIGINT/SIGTERM shutdown handlers. Native Windows hosts should close stdin for graceful shutdown, not assume Unix signal behavior. Forced termination triggers owned-process cleanup but is not confirmation of a provider-turn outcome. An MCP host that kills a server after each tool call cannot preserve this lifecycle.

One data root admits one cooperating adapter owner, even with different profile filenames. Hosts that launch separate bridge processes need distinct data roots and, on Unix, distinct socket endpoints. Native Windows uses owned pipes and requires `socket_path: null`. Task IDs address records in the configured store, not a global service. Preserve the same compatible profile/runtime and store when restarting; changing profile contents is not a transparent way to resume or replay old tasks. Do not edit records or delete the persistent ownership lock to bypass contention.

## Requests

`tools/list` supplies the request schemas. Arguments are JSON objects; unknown fields, duplicate keys and wrong types are rejected. Integers are strict: booleans and numeric strings are not integers. Use these exact tool names:

| Tool | Required arguments | Optional arguments and defaults |
| --- | --- | --- |
| `codex_start` | `request_id`, `prompt`, `cwd`, `model`, `sandbox`, `deadline_utc` | None |
| `codex_status` | `task_id` | `wait_seconds=0` (integer 0..30), `cursor=0` (nonnegative integer) |
| `codex_result` | `task_id` | `offset_bytes=0` (nonnegative integer), `limit_bytes=4096` (integer 1..4096) |
| `codex_cancel` | `task_id` | None |

For a new start:

- `request_id` is a caller-generated UUID string. Persist the exact request before sending it.
- `prompt` is nonempty and at most 1,000,000 UTF-8 bytes.
- `cwd` is an existing absolute, non-symlink directory within an operator-allowed root.
- `model` is an explicitly allowed model ID. There is no automatic fallback.
- `sandbox` is `read-only` or `workspace-write`, and must be allowed by the profile. Writable roots are constrained to the bound workspace and task-tool network access is disabled; Codex still needs provider connectivity for model execution.
- `deadline_utc` is an ISO-8601 timestamp with a timezone, strictly in the future and at most one hour after admission. Construct it when creating the request, then retain it unchanged for replay.

For example, an application can construct its request using its own configured workspace/model:

```python
from datetime import UTC, datetime, timedelta
from uuid import uuid4

request = {
    "request_id": str(uuid4()),
    "prompt": "Inspect the project and summarize the existing tests.",
    "cwd": configured_workspace,
    "model": configured_model,
    "sandbox": "read-only",
    "deadline_utc": (datetime.now(UTC) + timedelta(minutes=2)).isoformat(),
}
# Persist request before calling codex_start. Do not rebuild it on a timeout.
```

The configured variables must refer to your actual allowed workspace and model. This snippet constructs a request; it does not install, register, authenticate or invoke the bridge.

## MCP result decoding and errors

A successful tool call carries the payload object in `structuredContent` and an equivalent JSON serialization in a text content block. Prefer the structured object when your MCP SDK exposes it; otherwise parse that text as JSON once. SDKs may map wire field names to language-specific names.

Tool errors have `isError: true` and a human-readable text message. Inspect that flag before attempting to decode a success payload. Unknown task, invalid input, conflicting replay and policy refusal are examples. There is no published machine-readable error-code taxonomy for these tool errors; do not depend on exact diagnostic wording. Invalid MCP/JSON-RPC envelopes can instead fail at the protocol layer. A broken pipe, timeout or lost reply is a transport failure, not proof that a start or interrupt was never delivered.

Successful tool replies are checked against a 16 KiB serialized bound, including their MCP wrapper. The result-page byte limit applies to decoded result bytes, not the JSON/base64 wrapper. Tool-level success means the request was handled; inspect task state to determine whether the coding work succeeded.

## Task identity and status response

Start, status and cancel return the same status shape:

| Field | Meaning |
| --- | --- |
| `task_id`, `request_id` | Bridge task UUID and the original request UUID |
| `state` | `starting`, `running`, `cancel-requested`, `completed`, `failed`, `cancelled` or `unknown` |
| `reason` | Nullable diagnostic explaining a state or limitation |
| `thread_id`, `turn_id` | Nullable native identifiers; retain for diagnostics, not as inputs to another bridge tool |
| `turn_submitted` | Whether the bridge recorded acknowledged native turn submission; false alone does not prove an ambiguous submission never arrived |
| `stop_intent`, `stop_requested_at_utc` | Nullable durable stop intent (`cancel` or `expiry`) and its timestamp |
| `deadline_utc` | Originally admitted deadline normalized to UTC |
| `cursor` | Nonnegative retained native-event sequence for subsequent status waits |
| `provider_outcome` | Nullable native outcome: `completed`, `failed` or `interrupted` |
| `native_evidence_status` | `complete`, `incomplete` or `unavailable` for captured native-journal evidence; independent of the result's availability |

An initial `starting` reply is not task completion. A new task creates a new native thread and one turn; there is no tool for continuing a conversation, steering a running turn or forking a native thread.

Replaying the same request UUID with identical bound inputs returns the existing task. The implementation normalizes `cwd`; applications should still preserve the original request exactly, including the deadline string. Changed input or a different runtime/profile conflicts. After a lost start reply, replay that same request rather than inventing a new UUID. This is durable duplicate prevention, not an unconditional exactly-once execution guarantee across uncertain provider failures.

`completed` means a complete final-message result was retained, not that its proposed code is correct or tests passed. Verify the deliverable separately. `failed` and `unknown` do not imply rollback of workspace edits. `unknown` explicitly means an outcome remains unresolved; do not treat it as either successful completion or permission to resubmit.

## Polling and result retrieval

Call status with the last returned cursor. A stale cursor returns promptly when newer events exist; a cursor beyond the retained sequence is invalid. A positive wait ends on a newer event or state/reason change, or at the earlier of the requested wait and original deadline. The cursor is not a byte offset, percentage complete or native event-stream subscription. Status returns a summary, not raw progress text. Stop normal completion polling when the task reaches an outcome; polling an already-completed task with a current cursor and positive wait can consume the full wait bound.

Eligible `unknown` tasks with retained native IDs can trigger read-only history reconciliation on status; a positive wait can await it within the same bounds. Reconciliation never submits a new turn. Uncertainty that remains needs an operator/application decision. Bound your own polling policy and yield to the event loop in an asynchronous host. Completion notifications, UI wakeups and automatic follow-up prompts are the host's responsibility, not a bridge promise.

Result replies contain `task_id`, `complete`, `format`, `output_sha256`, `total_bytes`, `offset_bytes`, `chunk`, and `next_offset`:

1. For a completed task, request the first page at offset 0.
2. Base64-decode `chunk` and append **bytes**. Follow `next_offset` until it is `null`. Never compute the next offset from the length of the base64 text.
3. Verify the total byte length and whole-result SHA-256 against `total_bytes` and `output_sha256`.
4. Decode UTF-8 and parse the complete JSON only after assembling all pages. Characters and JSON values can cross page boundaries.

`complete` describes task completion, not whether this is the final page. If no result is retained, `complete` is false, format/hash/length are null, the chunk is empty, and `next_offset` equals the requested offset: return to status/diagnosis instead of spinning on result pages. An offset beyond the end is invalid; an offset exactly at the end returns an empty final page.

The result format is `codex-final-messages-v1`:

```json
{"format":"codex-final-messages-v1","messages":[{"id":"native-message-id","text":"Original final answer text"}]}
```

Messages preserve native final-answer text and history order. They are not a transcript of every progress event, a file archive or a diff API. Workspace edits remain in the task workspace. Do not manufacture final output from commentary or partial streaming text when full output is unavailable.

## Cancellation, timeouts and recovery

Call `codex_cancel` with the retained task ID, then inspect status. `cancel-requested` acknowledges stop intent; it is not confirmation of termination. `cancelled / provider_interrupted` confirms native interruption. `cancelled / cancelled_before_dispatch` records that no turn was submitted. A task that races to completion after stop intent is not reported as successful cancellation. Cancelling an already-completed task does not undo its edits. Cancellation and deadlines are not transaction rollback or hard billing caps.

A host RPC timeout does not extend or replace the task deadline, automatically cancel the task, or permit a new submission. Keep the original task/request identity and inspect or cancel through the same profile/store. After an adapter restart, nonterminal records become `unknown / adapter_restarted`; native IDs may permit reconciliation. Persistence does not guarantee work or deadline enforcement survives adapter/guardian/machine failure. Follow the [operational recovery rules](contract.md#guardian-process-ownership-and-recovery) before resolving stale resources; never stop another session or delete an unverified socket.

## Compatibility and integration acceptance

Pin a reviewed bridge commit, locked dependencies and the exact native runtime. The `0.1.0` interface does not promise that every future runtime or store version is compatible; this extraction supplies no general migration tool. Do not use the private task-record schema as an application API. Preserve unfamiliar diagnostics and fail visibly on unsupported states rather than silently interpreting them as success.

Before activating a host integration, follow the [client-neutral integration test procedure and success targets](integration-testing.md). Verify the host can keep a stdio server alive, discover all four tools, decode success/errors, retain request/task identity, advance cursors, reconstruct multiple result pages, and distinguish cancellation acknowledgement from a confirmed outcome. Start with the included offline fixture smoke; real-provider checks require your selected runtime/model/authentication and usage authorization. MCP support alone does not prove that a particular host can launch local stdio servers or correctly handle this lifecycle.

The [recorded validation](live-validation.md) identifies the actually tested macOS/runtime/model/client combination. Platform implementations and their separate qualification status are defined in [platforms.md](platforms.md). Native Windows uses stdio pipes and kernel Job Objects; Unix retains sockets and the guardian. No universal client, shared network-service, automatic notification or unattended daemon support is implied. Path admission does not authenticate the connected peer, and compare/unlink is non-atomic. This bridge is independent of OpenAI and grants no workflow approval authority.

The Windows backend owns one app-server per native connection; logical disconnect drains its output and does not claim cancellation. All jobs remain owned by the bridge session and are terminated during session cleanup or by Windows when the adapter dies. Windows profiles require `socket_path: null` and checked private ACLs. WSL2 uses the Linux backend, with Linux paths and credentials. The same task IDs, replay, pagination, states and deadlines apply on every route.
