# Developer integration tests and success targets

Use this guide after cloning the bridge and again when integrating it into a new developer tool. It applies to any host that supports local stdio MCP. No named assistant, editor or reviewer is required. The [integration contract](integration-contract.md) defines the interface being checked.

There are two different conclusions: **the checkout passes its tests**, and **your actual host passes integration acceptance**. Record them separately. A generic test client cannot prove that your host retains its server process, decodes tools correctly or follows tasks to completion.

## Local maintainer acceptance

The maintainer acceptance process runs on owner-controlled machines, using the
tests shipped in this repository. It does not use or require GitHub Actions,
hosted runners, a self-hosted Actions agent, or Actions usage minutes. GitHub
stores the source and PR discussion; the owner reviews local evidence and merges
manually. The existing branch rules remain unchanged and do not automatically
enforce local test completion.

Run [local security acceptance](security-testing.md) alongside the platform
checks before release. It scans retained history and PR/candidate files, audits
both dependency locks and checks reviewed code-analysis findings. Advisory
queries need network access on the querying machine. A disconnected WSL host
may use the explicit [verified receipt-transfer procedure](security-testing.md#disconnected-wsl-advisory-checks);
its code/secret scans still run locally. No paid GitHub tools or model calls are involved.

After the selected environment's [locked development setup](platforms.md), run
the following from its checkout root:

| Route | Execution location | Command or procedure |
| --- | --- | --- |
| macOS | Maintainer's Mac | `.venv/bin/python tools/test_platform.py --platform macos` |
| Linux | Isolated local Docker container | `.venv/bin/python tools/test_platform.py --platform linux` inside the container |
| Native Windows 11 | Maintainer's Windows test machine | `.\.venv\Scripts\python.exe tools/test_platform.py --platform windows` |
| WSL2 | Linux distribution on that Windows machine | `.venv/bin/python tools/test_platform.py --platform wsl` |
| Windows-to-WSL2 forwarding | Windows-side client launching through actual `wsl.exe` | Follow both stages of the [forwarding acceptance procedure](wsl-acceptance.md) |

The Linux container needs its own Linux virtual environment, Git and locked
development dependencies; do not reuse a macOS environment. Use an owned
disposable container, a non-root test user, a source snapshot of the candidate,
and private writable fixture/receipt paths. Prepare dependencies separately,
then disable container networking during fixture execution. Preserve receipts
before removing the container. A Linux-container pass does not replace native
Windows or actual WSL2/forwarding acceptance.

For runtime, dependency, fixture or test-runner changes, run the applicable
platform matrix before merge and document any omitted route as NOT TESTED.
Review all supported routes before a release. For documentation-only changes,
check links, examples and transfer manifests, verify runtime/test/lock bytes
against the previously tested candidate, and cite that candidate's results
explicitly; do not describe carried-forward results as a new test run.

Each acceptance record must include:

- Candidate commit and whether the tested checkout was clean, plus any source
  snapshot/hash comparison used to carry earlier evidence forward.
- OS, architecture, Python version and dependency setup; for Linux, container
  image identity and test-user privilege; for WSL2, distribution and kernel.
- Commands, exit codes, test counts, expected and unexpected skips, and retained
  `summary.json`, JUnit and command logs. Keep failed attempts and their explanations.
- Separate forwarding results, cleanup observations and all untested limits.

Use the [platform success targets](platforms.md#included-test-routes). A zero
exit status without the expected tests is insufficient. Retain sanitized results
with the PR, and keep full private receipts in durable local storage. Archive
WSL temporary receipts before session exit as described in its procedure.
Remove only the test resources owned by that run after preserving evidence.

These checks use offline fixtures: no real Codex executable, authentication or
paid model turn is required. The developer's actual-host acceptance below is
separate and may include explicitly authorized live-provider usage. Integrators
are not required to adopt the maintainer's multi-platform process or configure
CI; they test the platform and host they actually use.

## 1. Run the included checkout checks

First run the [platform selector and test route](platforms.md). The low-level commands in this section are Unix commands. Native Windows uses `.venv\Scripts\python.exe tools/test_platform.py --platform windows`; WSL2 runs the Linux tests inside its distribution with `--platform wsl`.

After completing [locked setup](../README.md#get-and-prepare-your-local-checkout), start from the bridge checkout root:

```sh
BRIDGE="$(pwd -P)"
```

### Quick offline MCP smoke

This uses the real module entry point and actual MCP messages against a deterministic fake Codex executable. It creates private profiles/stores and owned fixture processes, launches from an unrelated directory, then cleans up its temporary workspace. It needs the locked runtime dependencies, but not pytest, real Codex authentication or a model call.

```sh
(
  set -e
  umask 077
  cd "$(python3 -c 'import os; print(os.path.realpath("/tmp"))')"
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$BRIDGE/src" \
    "$BRIDGE/.venv/bin/python" \
    "$BRIDGE/tests/installed_wheel_smoke.py" \
    --source-root "$BRIDGE/src" \
    --fixture "$BRIDGE/tests/fixtures/codex_app_server_adapter/native_server.py"
)
```

**Pass:** exit 0 and this final output:

```text
source checkout smoke passed: stdio, replay, status, pages, cancel, cleanup
```

The assertions cover tool discovery, duplicate request replay with one native turn, bounded status with advancing cursors, reconstruction of more than 2 MiB of output, digest verification, confirmed fixture cancellation, destination module/guardian paths and EOF cleanup. A nonzero exit, assertion, traceback or timeout is a failed/incomplete check, even if an earlier stage printed output. Inspect it before activating the integration. Do not loosen live profile permissions to make a fixture pass.

### Full offline regression suite

Install the locked development extras as an explicit setup step. Use the checkout-local `uv` from README setup if present, otherwise an existing `uv`:

```sh
if [ -x "$BRIDGE/.local/setup/bin/uv" ]; then
  BRIDGE_UV="$BRIDGE/.local/setup/bin/uv"
else
  BRIDGE_UV="$(command -v uv)"
fi
UV_CACHE_DIR="$BRIDGE/.local/uv-cache" "$BRIDGE_UV" sync \
  --project "$BRIDGE" --locked --extra dev --no-install-project --no-python-downloads
```

Run the suite in a fresh short temporary root. Retain that root and its XML while diagnosing any failure:

```sh
(
  set -e
  umask 077
  cd "$BRIDGE"
  BRIDGE_TEST_ROOT=$(mktemp -d "$(python3 -c 'import os; print(os.path.realpath("/tmp"))')/cb.XXXXXX")
  printf 'Test receipts: %s\n' "$BRIDGE_TEST_ROOT"
  CODEX_HOME="$BRIDGE_TEST_ROOT/codex-home" PYTHONDONTWRITEBYTECODE=1 \
    "$BRIDGE/.venv/bin/python" -m pytest -q -m 'not installed_runtime' \
    --basetemp "$BRIDGE_TEST_ROOT/tests" -p no:cacheprovider \
    --junitxml "$BRIDGE_TEST_ROOT/offline.xml"
)
```

**Pass:** exit 0, zero test failures/errors, and no unexplained skips. The current Unix platform target is **535 passed, 4 Windows-only skips, 1 deselected**, including 25 security-tool regressions and three polling regressions; native Windows has its own route below and runs the security-tool regressions separately. The original extraction baseline was 487 passes before platform tests were added. The deselected test is the explicitly opt-in installed Codex 0.156.0 alias check; it is not a hidden failure or a live-provider pass. Counts can change with later commits: record your tested commit and actual counts. Investigate a lower count rather than accepting it solely because the exit code is zero.

The suite covers validation negatives, task lifecycle, deadlines, cancellation, persistence, restart reconciliation, exclusive ownership, independent roots, losing-startup noninterference, shutdown, process death, guardian behavior, sockets and protocol compatibility. These are fixture observations, not real-provider guarantees. Unix socket/process facilities are required for this suite. Native Windows has a separate kernel/MCP route; passing Docker does not qualify WSL2 forwarding or native Windows. See the [platform test targets](platforms.md#included-test-routes).

### Static and transfer checks

```sh
(
  set -e
  cd "$BRIDGE"
  "$BRIDGE/.venv/bin/ruff" check src tools \
    tests/test_standalone_extraction.py tests/installed_wheel_smoke.py
  "$BRIDGE/.venv/bin/python" tools/verify_transfer.py
)
```

Run and inspect each command's exit status: focused Ruff should print `All checks passed!`; transfer verification should report `Verified 25 destination files; source comparison: False`. No source checkout is needed. The full inherited test-file lint baseline has 52 documented diagnostics, so `ruff check tests` is not an all-green acceptance target. Do not silently discard new diagnostics.

## 2. Test your actual MCP host

Perform these calls through the developer tool being integrated, using its real registration and tool-call path. The standalone smoke above is not a substitute. If the host cannot expose requests/results, preserve an instrumented host log or use its integration SDK to inspect them; inability to observe a target makes it **NOT TESTED**, not **PASS**. Do not ask a language model to guess a SHA-256: use a deterministic host-side decoder/checker for byte reconstruction and hashing.

This section is an **optional live-provider test**. It can consume model usage. Before running it, explicitly choose/authenticate the pinned runtime and model, authorize a task/usage budget, and prepare a disposable Git workspace with an isolated profile, Codex home, store and socket. Do not point acceptance tests at production workspaces or another adapter's root/session. The suggested bound is two new tasks, 120 seconds each, with no automatic retries or model substitution. Configuration checks, status reads, exact replay and result reads do not intentionally submit additional model turns.

### Success targets

| ID | Exercise through the actual host | Observable pass target |
| --- | --- | --- |
| H01 | Register and connect | Host launches the intended checkout's module from its normal working directory; MCP initialization succeeds and all four tools are discovered. An MCP host may prefix display names, but must route to the correct server/tool names |
| H02 | Decode an error before starting paid work | A status call with a newly generated unknown task UUID, or a deliberately invalid argument, is surfaced as an error. It is not presented as an empty successful coding result; the server remains usable |
| H03 | Start one small coding task | Save the exact request, including one UUID and original deadline. `codex_start` returns a durable task ID before coding finishes; the host keeps the server alive and later reaches `completed` with `provider_outcome=completed`. Independently check the generated deliverable/tests |
| H04 | Replay and conflict | Replay H03's exact saved request; the same task ID returns. Reusing its UUID with a changed prompt fails. Native records, if available for observation, show one thread/turn for H03; if not observed, label that native-count assertion NOT TESTED |
| H05 | Poll with cursors | Host passes each returned cursor into the next call, respects `wait_seconds` 0..30, and retains the original deadline/task identity. Record elapsed calls: waits are bounded by their requested duration/deadline plus measured transport/scheduling overhead, not by the full coding duration. Do not interpret cursor as percent complete |
| H06 | Reconstruct final output | Request H03's result with `limit_bytes=32` to force multiple pages. Base64-decode and concatenate bytes through `next_offset=null`; verify total length and SHA-256, then parse UTF-8/JSON. Confirm `complete=true` on a nonfinal page does not stop retrieval early |
| H07 | Cancel the second task | Once the second task has an acknowledged native turn, request cancellation. Observe the acknowledgement separately, then `cancelled / provider_interrupted`. Cancellation before dispatch does not pass this submitted-turn target. Completion before the cancel or an `unknown` outcome is INCONCLUSIVE, never a silent pass/retry |
| H08 | Restart after both tasks stop | Disconnect normally, restart through the same host/profile/store, and read both terminal statuses and H03's result. Exact H03 replay still returns the same task; its result bytes/hash are unchanged. No new native turn is created |
| H09 | Shut down and clean up | Normal host disconnection closes stdin and the bridge exits; verify its owned guardian/app-server and socket are gone. The persistent owner-lock file is expected to remain. Remove only the disposable resources you created, after retaining the report |

For H03, a suitable bounded prompt is to create a pure `add(a, b)` function and three standard-library tests (positive, negative, zero), execute those tests once, and summarize the result. Supply the workspace's actual interpreter path; prohibit package installation, network use, delegation and reading unrelated directories. Use the allowlisted workspace-write sandbox and chosen model. The host—not a specific assistant product—constructs and retains the request.

For H07, a suitable bounded prompt requests a 60-second local sleep followed by a marker-file write only if it finishes normally. Cancel after native turn submission and before completion; confirm interruption and absence of the marker. If interruption occurs before the sleep command starts, the evidence qualifies provider-turn cancellation only. To claim termination of an already-running tool command, you must separately observe that command starting and stopping. Do not spend another turn merely to turn an inconclusive result green without renewed authorization.

For H08, do not change the profile, runtime, workspace path or store between the two connections. Do not launch a second competing bridge while the first still owns the root. A host that automatically creates a fresh store each time cannot pass persistence using this procedure.

### What this procedure does not establish

Two successful live tasks do not test live deadline expiry, process-death recovery, hostile peers, every model, other operating systems, or every host feature. Those require separately scoped tests. The full offline suite already exercises deadline/death/ownership mechanisms with fixtures; report those separately. Neither `completed` nor a test report grants permission to merge, publish or activate a consumer. There is no new live-test CLI that silently submits tasks: your own host performs the calls above under your explicit budget.

## 3. Retain an integration result

For each target, use **PASS**, **FAIL**, **NOT TESTED**, or **INCONCLUSIVE**, with a short receipt/reason. A fully observed H01–H09 pass supports a bounded host-integration PASS for that recorded configuration. If a required target is unobserved/inconclusive, report PARTIAL; if behavior contradicts the contract, report FAIL. Do not sum fixture passes into a claim of live host success.

Copy this template into your own private test report:

```text
Bridge commit:
Host/tool name and version:
Host transport and registration identifier:
OS/architecture and Python version:
Codex exact version and executable SHA-256:
Model and reasoning configuration:
Authentication method (never credentials):
Isolated disposable workspace/store confirmed:
Authorized maximum tasks/deadlines and actual native turn count:

Checkout smoke: exit code, final output, receipt
Offline suite: passed/failed/skipped/deselected counts, JUnit receipt
Static/transfer checks: exit codes and limitations

H01 connection/discovery:
H02 error decoding:
H03 completion and independent deliverable checks:
H04 replay/conflict and native turn count:
H05 cursor/wait/deadline observations:
H06 pages/bytes/expected and computed SHA-256:
H07 cancellation acknowledgement and confirmed outcome:
H08 restart/persistence:
H09 owned-process/socket cleanup:

Overall: PASS / PARTIAL / FAIL (state the exact scope)
Untested behavior, failures and unexpected skips:
Sanitized receipts retained and disposable resources removed:
```

Record full private request/task identities and original inputs where needed to diagnose replay; omit credentials, personal paths, raw private journals and sensitive prompts from reports you distribute. Keep a failed attempt's receipt and explain it instead of replacing it with a later success. The repository's [recorded results](live-validation.md) are examples of bounded evidence, not acceptance of your own host integration.
