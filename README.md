# Codex MCP app-server bridge

Clone the bridge, run it locally, and integrate it into your own development workflow. It connects an MCP-compatible client to your local Codex app-server so your tools can start coding tasks asynchronously, inspect progress, retrieve complete results in pages, and request cancellation. Task identity and history persist in a private local store.

**GitHub hosts the source code, not a running bridge service.** Developers pull the code onto their own machines, install its locked dependencies, create their own private configuration, and register the local executable with their MCP client. There is no GitHub-hosted endpoint to connect to and no GitHub Actions requirement for using the bridge. Codex model execution still requires the developer's configured provider access.

## Why use this bridge instead of the native app-server directly?

Use the bridge when your host already supports local stdio MCP tools and you want to delegate bounded coding tasks to Codex without implementing a native app-server client. For example, an editor, assistant or workflow runner that accepts MCP servers but has no native Codex app-server adapter can use this interface. Check the capabilities of your particular host; MCP support alone does not certify its integration with this bridge.

The native app-server has its own JSON-RPC protocol, thread/turn lifecycle, event notifications, history and interruption methods. Sharing JSON-RPC does not make it an MCP tool server. See the [native app-server documentation](https://learn.chatgpt.com/docs/app-server).

| Integration need | What this bridge supplies |
| --- | --- |
| Call Codex from an MCP host | Four stdio MCP tools, translated into native app-server requests |
| Submit work without holding one long tool call open | A durable task ID, bounded status calls and explicit task states |
| Handle a lost start reply safely | Persisted request-ID replay/conflict checks, with uncertainty instead of automatic resubmission |
| Retrieve output within tool-response limits | Canonical final-message bytes, base64 pages and a whole-result SHA-256 |
| Apply a local operating policy | Pinned executable/version/digest, explicit workspace/model/sandbox allowlists, admitted deadlines and checked provider permissions |
| Manage local runtime lifetime | Exclusive task-store ownership, an owned app-server child, guardian cleanup and restart reconciliation of retained records |
| Interpret cancellation and retained evidence | Separate stop acknowledgement from confirmed interruption, and report native-journal completeness when available |

These are packaged integration behaviors built on native Codex capabilities. The bridge does not make Codex smarter, eliminate model usage charges, or introduce native history/cancellation where none existed. It adds a Python environment and local state to maintain, and it deliberately exposes only a small part of the native API.

Use the native app-server directly when you are building a full Codex client and need native event streaming, interactive approvals, thread continuation/forking, or other native methods outside these four tools. With this bridge, each new task creates a new native thread and turn; there is no follow-up-message or raw-event-stream tool, and no remote HTTP MCP endpoint. Choose based on the interface and lifecycle behavior your host needs, rather than treating the bridge as universally preferable.

## Integrate into your workflow

1. **Get a local copy and select a platform:** clone this repository, select a reviewed commit, and run `python3 tools/integrate.py` (`py -3 tools/integrate.py` on Windows). Choose macOS, Linux, native Windows 11 or WSL2; follow the matching [platform guide](docs/platforms.md).
2. **Prepare it locally:** install the locked dependencies in the checkout's environment and create a private profile for your Codex executable, model, workspaces and task store.
3. **Connect your client:** configure your MCP-compatible developer tool to launch that checkout's Python module over stdio. The client starts the bridge, which owns its local Codex app-server child.
4. **Use it in your workflow:** call `codex_start`, retain the task ID, then use `codex_status`, `codex_result` and `codex_cancel` as needed.

The bridge checkout can live separately from the repositories you work on. Your client registration points to the bridge's absolute local path; each task's `cwd` selects a workspace allowed by your profile. Your workflow controls when tasks are submitted and what happens to their results.

The [developer integration contract](docs/integration-contract.md) specifies host/operator responsibilities, request and response fields, MCP result decoding, replay, polling, pagination, cancellation, errors and compatibility boundaries. The [operational contract](docs/contract.md) provides the deeper process-ownership and socket rules.

This is an independent project, not an official OpenAI product. Compatibility depends on the client's stdio MCP implementation and the pinned Codex runtime; universal client support is not claimed. This repository remains private pending an owner release decision.

## Supported environment

- Select **macOS, Linux, native Windows 11, or Windows 11 with WSL2**. Unix uses private sockets, process groups and `flock`; native Windows uses private stdio pipes, ACLs, file locks and Job Objects. See the [platform status matrix](docs/platforms.md#status-and-boundaries): native Windows passed bounded offline acceptance at `5fdff37`; WSL2 and forwarding still require owner-machine acceptance.
- Python 3.12+ is the declared requirement; local qualification uses 3.14.6. The dependency lock now includes macOS, Linux and Windows; other Python versions still need their own qualification.
- `uv` 0.12.18 was used for locked setup. Dependencies are `mcp==2.2.0`, `websockets==17.1`, and the exact transitive artifacts in `uv.lock`.
- An operator-selected Codex executable with the selected platform's local [app-server support](https://learn.chatgpt.com/docs/app-server), independently configured authentication, and an explicitly selected accessible model. The executable must have a stable absolute path without symlink components; a moving launcher is unsuitable.

Historical source records cover direct sockets in CLI 0.154.0, deterministic aliases in 0.156.0, and one macOS arm64 0.159.2 / `gpt-6.1-sol` completed turn under the former client identity. Following the offline extraction, a bounded live test of the standalone identity passed with CLI **0.159.2**, **`gpt-6.1-sol` / low**, and one programmatic stdio MCP client: task completion, duplicate prevention, paginated results, confirmed provider-turn cancellation, persistence and cleanup. This does not qualify every client/model or live deadline expiry. See the [live results and precise limits](docs/live-validation.md) and [extraction provenance](docs/extraction.md). Those live receipts predate the platform changes; see [current platform validation](docs/platform-validation.md) for the Windows offline result and pending WSL2/forwarding gates.

## Get and prepare your local checkout

Clone this repository with access to its private remote, choose a reviewed commit and platform, and create a separate environment. The commands below are for Unix; use the [native Windows PowerShell route](docs/platforms.md#windows) on Windows. No wheel release is needed.

```sh
git clone https://github.com/Aetheris-Platform/codex-mcp-app-server-bridge.git
cd codex-mcp-app-server-bridge
BRIDGE="$PWD"
python3 tools/integrate.py
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

**Setup does not create a live profile.** Native Windows uses [profile.windows.json](examples/profile.windows.json), `socket_path: null`, Windows paths and private ACLs as described in the [Windows guide](docs/platforms.md#windows). `/absolute/private/profile.json` is an example path, not a file shipped by this project. Do not run the live launch command until you have created a real profile with your chosen runtime, model, workspace and private storage paths.

On Unix, copy [the sanitized example](examples/profile.json), replace every placeholder, and keep the real profile outside version control with mode `0600`. Create its socket parent and data root with mode `0700` under your own account. All path components must be real, non-symlink directories. On macOS use canonical `/private/tmp`, not `/tmp`, for a temporary profile socket. Keep socket paths short enough for the OS Unix-socket limit.

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

To check the prepared checkout immediately without a live profile or provider call, run:

```sh
cd "$(python3 -c 'import os; print(os.path.realpath("/tmp"))')"
(umask 077
PYTHONPATH="$BRIDGE/src" "$BRIDGE/.venv/bin/python" \
  "$BRIDGE/tests/installed_wheel_smoke.py" --source-root "$BRIDGE/src" \
  --fixture "$BRIDGE/tests/fixtures/codex_app_server_adapter/native_server.py"
)
```

This creates and cleans up temporary fixture profiles and processes. It is an offline MCP check, not a live Codex session.

For a live launch, replace **both example paths** below with your checkout and an existing private profile:

```sh
BRIDGE=/absolute/path/codex-mcp-app-server-bridge
cd "$(python3 -c 'import os; print(os.path.realpath("/tmp"))')"
PYTHONPATH="$BRIDGE/src" "$BRIDGE/.venv/bin/python" \
  -m codex_app_server_adapter --profile /absolute/private/profile.json
```

For normal workflow integration, your MCP client launches this local process and speaks MCP JSON-RPC on stdin/stdout. Running it directly in a terminal starts the transport; it does not open an interactive coding prompt. Put the following registration in your client's local configuration, replacing the paths with your own:

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

## Validate your integration

Tests ship in this repository and apply to any developer tool that can launch a local **stdio MCP** server. They do not require a particular assistant, editor or model to operate the host.

| Stage | Included check | Success target |
| --- | --- | --- |
| Check the prepared checkout | [MCP fixture smoke](tests/installed_wheel_smoke.py), using the [launch command above](#launch-from-any-directory) | Exit 0 and `source checkout smoke passed: stdio, replay, status, pages, cancel, cleanup` |
| Check bridge regressions | [Offline suite](#offline-tests) | Zero failures; use the selected platform's [counts and exclusions](docs/platforms.md#included-test-routes) |
| Check your actual host integration | [Host acceptance procedure and report template](docs/integration-testing.md#2-test-your-actual-mcp-host) | Observe every applicable target through your configured host, including confirmed cancellation and verified result bytes; record untested checks explicitly |

The first two stages use deterministic fixtures and need no provider authentication or model usage. The host procedure defines a separate, opt-in two-task live check in a disposable workspace, plus non-model control checks. Passing the standalone smoke does **not** qualify your host's registration, tool decoding, timeouts or lifecycle. A host that supports only remote HTTP MCP cannot launch this local stdio bridge without an additional integration layer, which this repository does not provide.

## Offline tests

After selecting a platform and installing dev dependencies, run `.venv/bin/python tools/test_platform.py` (native Windows: `.venv\Scripts\python.exe tools/test_platform.py`). It uses the saved selection and retains logs/JUnit. For WSL2 and a Windows MCP host launching through `wsl.exe`, use the separate [two-stage acceptance procedure](docs/wsl-acceptance.md) and included Windows-side forwarding harness. The lower-level commands below are Unix-only.

```sh
cd "$BRIDGE"
UV_CACHE_DIR="$BRIDGE/.local/uv-cache" uv sync --locked --extra dev --no-install-project --no-python-downloads
umask 077
BRIDGE_TEST_ROOT=$(mktemp -d "$(python3 -c 'import os; print(os.path.realpath("/tmp"))')/cb.XXXXXX")
CODEX_HOME="$BRIDGE_TEST_ROOT/codex-home" PYTHONDONTWRITEBYTECODE=1 \
  "$BRIDGE/.venv/bin/python" -m pytest -q -m 'not installed_runtime' --basetemp "$BRIDGE_TEST_ROOT/tests"
"$BRIDGE/.venv/bin/ruff" check src tests/test_standalone_extraction.py tests/installed_wheel_smoke.py
cd "$(python3 -c 'import os; print(os.path.realpath("/tmp"))')"
PYTHONPATH="$BRIDGE/src" "$BRIDGE/.venv/bin/python" \
  "$BRIDGE/tests/installed_wheel_smoke.py" --source-root "$BRIDGE/src" \
  --fixture "$BRIDGE/tests/fixtures/codex_app_server_adapter/native_server.py"
```

Tests launch deterministic local fixtures, including owned sockets at unique hashed alias paths in the runtime's fixed per-user temporary rendezvous directory; they never attach to a shared daemon. They exercise accepted substitution/cleanup residuals intentionally. Use a disposable developer test environment and only remove temporary roots you created. The installed-runtime control is separately marked and requires `BRIDGE_TEST_CODEX_0156`; it is excluded above. No test result here establishes live-provider qualification. [Recorded results](docs/validation.md) distinguish passes, inherited failures and exclusions.

See [integration testing](docs/integration-testing.md) for fresh-checkout commands, expected output, host acceptance targets and how to classify failures or incomplete checks. The inherited full-test-file lint baseline has 52 diagnostics; the focused lint command above is expected to pass.

## Provenance and licensing

The implementation originated in Aetheris and was maintained as Central's standalone Python package before this extraction. [Transfer manifests](docs/provenance/transfer-manifest.json) pin committed inputs and per-file hashes. Runtime imports do not require either source checkout; no workflow core, formal verifier, role firing or product adoption machinery is included.

The remote's existing Apache-2.0 [LICENSE](LICENSE) is preserved unchanged. Central's original licensing notice is also retained. The owner must resolve its applicability to transferred code and required third-party distribution notices before public release; successful tests or the existing license file do not establish redistribution clearance. See [LICENSE-DISPOSITION.md](LICENSE-DISPOSITION.md) and [dependency notices](docs/third-party/README.md).
