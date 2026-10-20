# Codex MCP app-server bridge

The package and MCP server identify this checkout as **1.0.0**. This version
update preserves the accepted tool interfaces and task behavior; it does not
publish a package or GitHub release. Platform evidence remains tied to its
recorded candidate, with [version-update checks](docs/version-1.0.0.md) recorded
separately.

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

**One package, with platform selection during setup.** Every platform uses the
`codex-mcp-app-server-bridge` Python package, the `codex_app_server_adapter`
module and the same four MCP tools. There are no separate Mac, Windows or Linux
bridge packages to choose. Select your platform to follow its dependency,
profile, launch and test instructions; the running operating system determines
the backend. Windows-to-WSL uses the same package installed in a Linux environment.

1. **Get a local copy and select a platform:** clone this repository, select a reviewed commit, and run `python3 tools/integrate.py` (`py -3 tools/integrate.py` on Windows). Choose macOS, Linux, native Windows 11 or WSL2; follow the matching [platform guide](docs/platforms.md).
2. **Prepare it locally:** install the locked dependencies in the checkout's environment and create a private profile for your Codex executable, model, workspaces and task store.
3. **Connect your client:** configure your MCP-compatible developer tool to launch that checkout's Python module over stdio. The client starts the bridge, which owns its local Codex app-server child.
4. **Use it in your workflow:** call `codex_start`, retain the task ID, then use `codex_status`, `codex_result` and `codex_cancel` as needed.

The bridge checkout can live separately from the repositories you work on. Your client registration points to the bridge's absolute local path; each task's `cwd` selects a workspace allowed by your profile. Your workflow controls when tasks are submitted and what happens to their results.

The [developer integration contract](docs/integration-contract.md) specifies host/operator responsibilities, request and response fields, MCP result decoding, replay, polling, pagination, cancellation, errors and compatibility boundaries. The [operational contract](docs/contract.md) provides the deeper process-ownership and socket rules.

This is an independent project, not an official OpenAI product. Compatibility depends on the client's stdio MCP implementation and the pinned Codex runtime; universal client support is not claimed. This repository remains private pending an owner release decision.

## Supported environment

- Select **macOS, Linux, native Windows 11, or Windows 11 with WSL2**. Unix uses private sockets, process groups and `flock`; native Windows uses private stdio pipes, ACLs, file locks and Job Objects. Native Windows, WSL2 and actual Windows-to-WSL forwarding passed bounded offline acceptance at `cd5d3f6`. See the [platform status matrix](docs/platforms.md#status-and-boundaries) and [reviewed release acceptance](docs/release-acceptance-2026-10-20.md) for exact environments, test counts and scope.
- Python 3.12+ is the declared requirement. Recorded offline tests used 3.14.6 on macOS/Linux Docker, 3.12.14 on native Windows and the Windows forwarding client, and 3.13.5 inside WSL2. Other combinations need their own qualification.
- `uv` 0.12.18 was used for locked setup. Dependencies are `mcp==2.2.0`, `websockets==17.1`, Windows-only `pywin32==312`, and the exact transitive artifacts in the shared `uv.lock`. Locked setup selects the dependencies applicable to its environment.
- An operator-selected Codex executable with the selected platform's local [app-server support](https://learn.chatgpt.com/docs/app-server), independently configured authentication, and an explicitly selected accessible model. The executable must have a stable absolute path without symlink components; a moving launcher is unsuitable.

Historical source records cover direct sockets in CLI 0.154.0, deterministic aliases in 0.156.0, and one macOS arm64 0.159.2 / `gpt-6.1-sol` completed turn under the former client identity. Following the offline extraction, a bounded live test of the standalone identity passed with CLI **0.159.2**, **`gpt-6.1-sol` / low**, and one programmatic stdio MCP client: task completion, duplicate prevention, paginated results, confirmed provider-turn cancellation, persistence and cleanup. This does not qualify every client/model or live deadline expiry. See the [live results and precise limits](docs/live-validation.md) and [extraction provenance](docs/extraction.md). Those live receipts predate the platform changes; see [current platform validation](docs/platform-validation.md) for the native Windows, WSL2 and forwarding offline passes. These passes do not extend the earlier live-provider qualification.

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

One data root has one cooperative adapter owner, even if two profiles have different names or socket paths. Concurrent independent adapters need distinct data roots; Unix adapters also need distinct socket endpoints. Native Windows uses owned pipes with `socket_path: null`. Do not run an older nonlocking adapter on the same root.

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

Adapt the enclosing registration format to your client. Saving an example does not register or activate anything. The native initialize name and thread source are truthfully `codex-mcp-app-server-bridge`; the MCP server name remains the generic `codex-app-server`.

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

Closing stdin stops admission and requests bounded shutdown on every platform. On Unix, SIGINT/SIGTERM also request that shutdown. Worker draining may take five seconds; the Unix guardian allows five seconds for TERM and five for KILL, plus scheduling margin. Unix adapter death closes a liveness pipe so a surviving guardian can clean up only its own child. Native Windows uses owned Job Objects for child/descendant cleanup; close stdin for graceful shutdown rather than assuming Unix signal behavior. Forced process termination does not confirm a provider-turn outcome. Guardian death/wedge or machine failure can leave resources behind. Nothing promises survival across host shutdown.

## Security boundaries and troubleshooting

The profile binds executable, paths, model and sandbox. Permissions returned by app-server are checked before a turn. Provider selection remains `openai`, approval policy `never`, reviewer `user`; unsupported escalation is denied. Workspace-write stays within the bound cwd with network disabled and temporary-root exclusions. This is a local trusted-account tool, not a multi-user network service.

- **Runtime mismatch:** verify the exact versioned executable and profile; do not bypass the digest or silently update it.
- **Data root already owned:** use a different private root or coordinate shutdown with its owner. Never delete the persistent `.adapter-owner.lock`; its existence is not proof of a live owner.
- **Existing socket / changed identity:** stop and inspect ownership. Do not reuse a Desktop/shared daemon or delete another session's socket. An intact alias with a missing socket is intentionally left for operator resolution.
- **Socket permission/path failure:** use short canonical paths and private directories. Test fixtures need local Unix-socket permissions. Do not loosen a live profile to make a test pass.
- **Unknown state / incomplete output:** retain task identity and inspect reason/journal limitations. Use bounded read-only reconciliation when eligible; do not retry by inventing a new task ID.
- **Missing journal:** output can exist without complete native evidence. Native-journal capture is transport custody, not a formal verifier or approval mechanism. Prompts, results and journals may contain private data; protect the entire store and do not commit it.
- **No response in a client:** verify the interpreter, absolute `PYTHONPATH`, stdin/stdout transport and locked environment. Keep diagnostics off stdout. No dependency fetching belongs in the launch command.

On Unix, direct sockets and deterministic aliases are admitted. Alias admission checks a captured **path**, not the connected peer: a substitution restored before the post-connect check is undetectable. Identity comparison followed by unlink is **not atomic**; a replacement in that window can be removed. Same-user inode reuse and noncooperating processes remain outside the assurance boundary. See the [complete contract](docs/contract.md).

## Validate your integration

Tests ship in this repository and apply to any developer tool that can launch a local **stdio MCP** server. They do not require a particular assistant, editor or model to operate the host.

| Stage | Included check | Success target |
| --- | --- | --- |
| Check the prepared checkout | [MCP fixture smoke](tests/installed_wheel_smoke.py), using the [launch command above](#launch-from-any-directory) | Exit 0 and `source checkout smoke passed: stdio, replay, status, pages, cancel, cleanup` |
| Check bridge regressions | [Offline suite](#offline-tests) | Zero failures; use the selected platform's [counts and exclusions](docs/platforms.md#included-test-routes) |
| Check your actual host integration | [Host acceptance procedure and report template](docs/integration-testing.md#2-test-your-actual-mcp-host) | Observe every applicable target through your configured host, including confirmed cancellation and verified result bytes; record untested checks explicitly |

The first two stages use deterministic fixtures and need no provider authentication or model usage. The host procedure defines a separate, opt-in two-task live check in a disposable workspace, plus non-model control checks. Passing the standalone smoke does **not** qualify your host's registration, tool decoding, timeouts or lifecycle. A host that supports only remote HTTP MCP cannot launch this local stdio bridge without an additional integration layer, which this repository does not provide.

## Maintainer acceptance runs locally

Maintainers run the included tests on their own macOS and Windows 11 machines,
with Linux tested in an isolated local Docker environment and WSL2/forwarding
tested on the Windows machine. **Maintainer testing does not depend on GitHub
Actions, hosted runners or an Actions usage allowance.** Test code lives in this
repository; the test processes run on maintainer-controlled machines.

Before merging, the owner reviews results tied to the candidate commit,
including failures, skips and platform limits. Existing main-branch protections
remain in place; they do not automatically execute or enforce these local tests.
See the [local maintainer acceptance procedure](docs/integration-testing.md#local-maintainer-acceptance)
for commands, evidence and documentation-only changes. Integrators separately
test their own MCP host using the guide above; they do not need to configure CI.

Maintainers also run [local security checks](docs/security-testing.md): pinned
secret scanning, dependency auditing, code analysis and Ruff. Tool installations
and raw receipts stay under ignored `.local/`; reusable scripts, versions and
reviewed exceptions are committed. These checks use no paid GitHub product or
Actions minutes. See [SECURITY.md](SECURITY.md) for private vulnerability reporting
and [security validation](docs/security-validation-2026-10-20.md) for the initial
audit and dependency correction. The corrected candidate passed
native Windows and three lifecycle repeats, WSL2, actual forwarding, Windows
direct security and WSL transferred-advisory acceptance. See the
[reviewed release acceptance](docs/release-acceptance-2026-10-20.md).

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

The bridge is a standalone extraction of an existing implementation. [Transfer manifests](docs/provenance/transfer-manifest.json) retain exact source commits and per-file hashes under neutral source identifiers. Runtime imports do not require a source checkout or external workflow system.

The owner has confirmed **Apache-2.0** for this bridge, including the extracted implementation. The existing [LICENSE](LICENSE) is unchanged. Dependencies retain their own licenses and notices; see [LICENSE-DISPOSITION.md](LICENSE-DISPOSITION.md) and [dependency notices](docs/third-party/README.md). This licensing decision does not publish the repository or authorize a release.
