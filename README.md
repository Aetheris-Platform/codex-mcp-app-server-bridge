# Codex MCP app-server bridge

Connect an MCP-compatible developer tool to Codex without holding one tool call open for an entire coding task. Start work asynchronously, inspect progress, retrieve the complete result in pages, and request cancellation. Task identity and history persist in a private local store.

This is an independent project, not an official OpenAI product. Compatibility depends on the client's stdio MCP implementation and the pinned Codex runtime; universal client support is not claimed. This repository remains private pending an owner release decision.

## Supported environment

- macOS with a local filesystem supporting Unix sockets, POSIX process groups and `flock`. The extraction was tested on macOS arm64 with Python 3.14.6. Linux and Windows are **not qualified or claimed supported**.
- Python 3.12+ is the declared requirement; this extraction tested only 3.14.6. The inherited dependency lock is macOS-only.
- `uv` 0.12.18 was used for locked setup. Dependencies are `mcp==2.2.0`, `websockets==17.1`, and the exact transitive artifacts in `uv.lock`.
- An operator-selected Codex executable with local Unix WebSocket [app-server support](https://learn.chatgpt.com/docs/app-server), independently configured authentication, and an explicitly selected accessible model. The executable must have a stable absolute path without symlink components; a moving launcher is unsuitable.

Historical source records cover direct sockets in CLI 0.154.0, deterministic aliases in 0.156.0, and one macOS arm64 0.159.2 / `gpt-6.1-sol` completed turn. They use the former client identity and do **not** qualify this standalone identity, every model, or live cancellation/deadlines. This extraction ran offline fixtures only. See [provenance and qualification](docs/extraction.md).

## Install from a checkout

Clone this repository with access to its private remote, choose a reviewed commit, and create a separate environment. No wheel release is needed.

```sh
git clone https://github.com/Aetheris-Platform/codex-mcp-app-server-bridge.git
cd codex-mcp-app-server-bridge
BRIDGE="$PWD"
UV_CACHE_DIR="$BRIDGE/.local/uv-cache" uv sync --locked --no-dev --no-install-project --no-python-downloads
```

Use an existing `uv`, or install it only into a checkout-local setup environment:

```sh
python3 -m venv "$BRIDGE/.local/setup"
"$BRIDGE/.local/setup/bin/python" -m pip install --cache-dir "$BRIDGE/.local/pip-cache" uv==0.12.18
UV_CACHE_DIR="$BRIDGE/.local/uv-cache" "$BRIDGE/.local/setup/bin/uv" sync \
  --locked --no-dev --no-install-project --no-python-downloads
```

Dependency installation is a setup step. Runtime launch never invokes `uv`, `pip`, a downloader, or a build. The checkout must remain available at the configured path.

An optional installed command is declared as `codex-mcp-app-server-bridge --profile /absolute/private/profile.json`. Local installation/build is optional and separate from a package release; see [licensing disposition](LICENSE-DISPOSITION.md) before distributing any artifact.

## Private operator profile

Copy [the sanitized example](examples/profile.json), replace every placeholder, and keep the real profile outside version control with mode `0600`. Create its socket parent and data root with mode `0700` under your own account. All path components must be real, non-symlink directories. On macOS use canonical `/private/tmp`, not `/tmp`, for a temporary profile socket. Keep socket paths short enough for the OS Unix-socket limit.

```json
{
  "runtime_path": "/absolute/private/bin/codex",
  "runtime_version": "EXACT OUTPUT OF codex --version",
  "runtime_sha256": "REPLACE_WITH_64_HEX_SHA256_OF_EXECUTABLE",
  "socket_path": "/absolute/private/run/app.sock",
  "allowed_worktree_roots": ["/absolute/workspaces"],
  "allowed_models": ["EXPLICIT_MODEL_ID"],
  "allowed_sandboxes": ["read-only"],
  "data_root": "/absolute/private/state"
}
```

Record the exact executable's `--version` output and SHA-256 (`shasum -a 256 /absolute/private/bin/codex`). The adapter verifies both before dispatch. Do not put credentials in the profile. Authentication remains the operator's responsibility. `CODEX_HOME`, when explicitly supplied, selects Codex's private home and the trusted `sessions/` root for native-journal capture; otherwise Codex's ordinary user home applies. Use an independently provisioned private home when isolation is required. This project neither provisions nor copies credentials.

One data root has one cooperative adapter owner, even if two profiles have different names or socket paths. Concurrent independent adapters need distinct roots **and** endpoints. Do not run an older nonlocking adapter on the same root.

## Launch from any directory

```sh
BRIDGE=/absolute/path/codex-mcp-app-server-bridge
cd /private/tmp
PYTHONPATH="$BRIDGE/src" "$BRIDGE/.venv/bin/python" \
  -m codex_app_server_adapter --profile /absolute/private/profile.json
```

The process speaks MCP JSON-RPC on stdin/stdout; normally an MCP client launches it. A generic registration shape is:

```json
{
  "mcpServers": {
    "codex-bridge": {
      "command": "/absolute/path/codex-mcp-app-server-bridge/.venv/bin/python",
      "args": ["-m", "codex_app_server_adapter", "--profile", "/absolute/private/profile.json"],
      "env": {
        "PYTHONPATH": "/absolute/path/codex-mcp-app-server-bridge/src",
        "CODEX_HOME": "/absolute/private/codex-home"
      }
    }
  }
}
```

Adapt the enclosing registration format to your client. Saving an example does not register or activate anything. The native initialize name and thread source are truthfully `codex-mcp-app-server-bridge`; the MCP server name remains the generic `codex-app-server`. Downstream verifiers expecting the former product identity must be adopted separately.

## Four tools

| Tool | Arguments | Purpose |
| --- | --- | --- |
| `codex_start` | `request_id`, `prompt`, `cwd`, `model`, `sandbox`, `deadline_utc` | Persist intent and promptly return a task ID |
| `codex_status` | `task_id`, `wait_seconds=0`, `cursor=0` | Read current progress, optionally wait up to 30 seconds |
| `codex_result` | `task_id`, `offset_bytes=0`, `limit_bytes=4096` | Retrieve a base64-encoded byte page, maximum 4096 bytes |
| `codex_cancel` | `task_id` | Persist stop intent and request interruption |

Request and task IDs are UUID strings. Prompt is nonempty and at most 1,000,000 UTF-8 bytes. `cwd` must be a real absolute directory within an allowed root. Model selection is explicit and allowlisted, with no fallback. Sandbox is `read-only` or allowlisted `workspace-write`. The timezone-aware deadline must be in the future and no more than one hour after admission. Unknown fields and invalid types are rejected.

Retain the task ID after start. Replaying the **identical** request ID and inputs returns the same task; changing bound inputs conflicts. A lost acknowledgement is uncertainty, never a reason to submit another request ID blindly. Polling and replay do not renew the original deadline.

States are `starting`, `running`, `cancel-requested`, `completed`, `failed`, `cancelled`, and `unknown`. Completion describes transport/output, not successful tests, approval, or delivery. After a restart, nonterminal records become `unknown / adapter_restarted`; retained provider IDs may permit read-only reconciliation within the original deadline. There is no automatic resubmission or promise that deadline enforcement continued during downtime.

### Status cursors

Pass the **returned cursor** on the next status call:

```python
status = call("codex_status", {"task_id": task_id})
while status["state"] in {"starting", "running", "cancel-requested"}:
    status = call("codex_status", {
        "task_id": task_id,
        "wait_seconds": 30,
        "cursor": status["cursor"],
    })
```

`call` above represents your client's MCP tool call and decoded JSON result. A stale cursor returns immediately if newer events are already retained. A future cursor is invalid. Waits end at the requested bound or original deadline; an eligible unknown task with provider IDs can reconcile read-only during a positive wait. In an in-process async integration, always yield to the event loop; repeatedly polling a stale cursor can starve the reader. Automatic client notifications or wakeups are not guaranteed.

### Result pagination

Wait for a terminal status before reading the final result. Decode each `chunk` from base64 and concatenate **bytes**, following `next_offset` until it is `null`. Check the combined byte length against `total_bytes` and its SHA-256 against `output_sha256`. Then decode UTF-8 and parse JSON. The result is a `codex-final-messages-v1` envelope containing `messages` with original `id` and `text`, not plain answer text. UTF-8 characters can span pages. `complete` describes task completion, **not** whether the current page is last. If no result exists, `complete` is false and `next_offset` remains the requested offset: return to status instead of spinning on empty pages.

### Cancellation and shutdown

An acknowledgement is not confirmed termination. Continue observing `cancel-requested`; `cancelled / provider_interrupted` confirms provider interruption, while `cancelled_before_dispatch` means no turn was submitted. `unknown` preserves an unconfirmed outcome. A task that races to provider completion after stop intent is not relabeled as successful cancellation.

Closing stdin or sending SIGINT/SIGTERM stops admission and requests bounded shutdown. Worker draining may take five seconds; the guardian allows five seconds for TERM and five for KILL, plus scheduling margin. Adapter death closes a liveness pipe so a surviving guardian can clean up only its own child. Guardian death/wedge or machine failure can leave resources behind. Nothing promises survival across host shutdown.

## Security boundaries and troubleshooting

The profile binds executable, paths, model and sandbox. Permissions returned by app-server are checked before a turn. Provider selection remains `openai`, approval policy `never`, reviewer `user`; unsupported escalation is denied. Workspace-write stays within the bound cwd with network disabled and temporary-root exclusions. This is a local trusted-account tool, not a multi-user network service.

- **Runtime mismatch:** verify the exact versioned executable and profile; do not bypass the digest or silently update it.
- **Data root already owned:** use a different private root or coordinate shutdown with its owner. Never delete the persistent `.adapter-owner.lock`; its existence is not proof of a live owner.
- **Existing socket / changed identity:** stop and inspect ownership. Do not reuse a Desktop/shared daemon or delete another session's socket. An intact alias with a missing socket is intentionally left for operator resolution.
- **Socket permission/path failure:** use short canonical paths and private directories. Test fixtures need local Unix-socket permissions. Do not loosen a live profile to make a test pass.
- **Unknown state / incomplete output:** retain task identity and inspect reason/journal limitations. Use bounded read-only reconciliation when eligible; do not retry by inventing a new task ID.
- **Missing journal:** output can exist without complete native evidence. Native-journal capture is transport custody, not a formal verifier or approval mechanism. Prompts, results and journals may contain private data; protect the entire store and do not commit it.
- **No response in a client:** verify the interpreter, absolute `PYTHONPATH`, stdin/stdout transport and locked environment. Keep diagnostics off stdout. No dependency fetching belongs in the launch command.

Direct sockets and deterministic aliases are admitted. Alias admission checks a captured **path**, not the connected peer: a substitution restored before the post-connect check is undetectable. Identity comparison followed by unlink is **not atomic**; a replacement in that window can be removed. Same-user inode reuse and noncooperating processes remain outside the assurance boundary. See the [complete contract](docs/contract.md).

## Offline tests

```sh
cd "$BRIDGE"
UV_CACHE_DIR="$BRIDGE/.local/uv-cache" uv sync --locked --extra dev --no-install-project --no-python-downloads
umask 077
BRIDGE_TEST_ROOT=$(mktemp -d /private/tmp/cb.XXXXXX)
CODEX_HOME="$BRIDGE_TEST_ROOT/codex-home" PYTHONDONTWRITEBYTECODE=1 \
  "$BRIDGE/.venv/bin/python" -m pytest -q -m 'not installed_runtime' --basetemp "$BRIDGE_TEST_ROOT/tests"
"$BRIDGE/.venv/bin/ruff" check src tests/test_standalone_extraction.py tests/installed_wheel_smoke.py
cd /private/tmp
PYTHONPATH="$BRIDGE/src" "$BRIDGE/.venv/bin/python" \
  "$BRIDGE/tests/installed_wheel_smoke.py" --source-root "$BRIDGE/src" \
  --fixture "$BRIDGE/tests/fixtures/codex_app_server_adapter/native_server.py"
```

Tests launch deterministic local fixtures, including owned sockets at unique hashed alias paths in the runtime's fixed per-user temporary rendezvous directory; they never attach to a shared daemon. They exercise accepted substitution/cleanup residuals intentionally. Use a disposable developer test environment and only remove temporary roots you created. The installed-runtime control is separately marked and requires `BRIDGE_TEST_CODEX_0156`; it is excluded above. No test result here establishes live-provider qualification. [Recorded results](docs/validation.md) distinguish passes, inherited failures and exclusions.

## Provenance and licensing

The implementation originated in Aetheris and was maintained as Central's standalone Python package before this extraction. [Transfer manifests](docs/provenance/transfer-manifest.json) pin committed inputs and per-file hashes. Runtime imports do not require either source checkout; no workflow core, formal verifier, role firing or product adoption machinery is included.

The remote's existing Apache-2.0 [LICENSE](LICENSE) is preserved unchanged. Central's original licensing notice is also retained. The owner must resolve its applicability to transferred code and required third-party distribution notices before public release; successful tests or the existing license file do not establish redistribution clearance. See [LICENSE-DISPOSITION.md](LICENSE-DISPOSITION.md) and [dependency notices](docs/third-party/README.md).
