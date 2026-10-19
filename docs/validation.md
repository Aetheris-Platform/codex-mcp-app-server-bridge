# Offline validation — 2026-10-19

Environment: macOS arm64, Python 3.14.6, uv 0.12.18, destination-local locked dependencies. All native work used deterministic fixture executables, private task roots/profiles and uniquely owned sockets/processes. No installed Codex runtime, model/provider turn, source environment or consumer registration was used.

## Results and retained failures

| Run | Actual result | Disposition |
| --- | --- | --- |
| Existing bridge suite | 481 passed, 1 failed, 1 installed-runtime test deselected; 62.99 s | Permission-negative fixture failed under private umask |
| Exact unchanged source negative | Same single failure | `mkdir(mode=0755)` became 0700 under umask 077; not an extraction runtime regression |
| Affected guardian/native, corrected permission negative, new extraction checks | 92 passed, 1 failed, 1 deselected; 29.32 s | New identity test incorrectly expected version `0.1.0`, while preserved native client version is `1` |
| Corrected extraction checks | 2 passed; 0.26 s | Assertion now binds the actual inherited version; runtime unchanged |
| Distinct final case accounting | **484 selected cases have a passing result; 1 installed-runtime case excluded** | Union of receipts, not a claim of one all-green full-suite invocation |
| Real MCP source smoke, unrelated cwd | Passed | Four tools, one thread/turn despite replay, bounded status with advancing cursor, >2 MiB pages/hash, matching native identity, confirmed fixture interruption, EOF cleanup |
| Source-read-denied MCP smoke | Passed | OS sandbox denied both source checkouts to adapter/guardian/provider; negative read controls independently confirmed PermissionError for both roots; sanitized environment, exact destination import paths and guardian command path |
| Optional wheel/entry-point smoke | Passed | Separate destination-local environment, no source PYTHONPATH, unrelated cwd; same MCP fixture assertions |
| Runtime/new tooling lint | Passed | `ruff check src tools tests/test_standalone_extraction.py tests/installed_wheel_smoke.py` |
| Inherited test lint | 52 diagnostics | Same file/code/message multiset as committed source; no unrelated lint cleanup or suppression |
| Dependency boundary | Passed | Parsed lock differs only in root distribution name; all dependency versions, URLs, artifact hashes and markers unchanged |
| Package boundary | Passed | Wheel contains only 13 expected bridge modules and distribution metadata/notices, including guardian; module bytes match destination source |
| Source provenance | Passed | 25 transferred destination files verified; all 29 inventoried Central inputs rehashed from exact committed blobs, including 4 deliberate exclusions |
| Compilation, diff hygiene and leakage | Passed | Runtime compilation; authored diff hygiene; no credential/private-home/LAN patterns in deliverable files; existing LICENSE unchanged |

Earlier test-harness attempts remain retained, not relabeled as passes:

- The first source-denial harness put its observer inside the OS sandbox; `/bin/ps` was denied after the result case passed. The final harness applies the same source denial to the adapter and descendants, while its external observer keeps the guardian-path assertion.
- Building the optional wheel generated checkout `egg-info`; the prior smoke mistook this for an installed wheel. Its check now inspects the interpreter's installation directory, while retaining exact source-module checks. The initial failed receipt remains. Both final source and wheel routes passed.
- The initial setup attempt could not reach PyPI inside the network sandbox. The authorized network-enabled retry installed uv only into `.local/setup`, followed by locked `.venv` setup. This was not a runtime launch.
- The new verification helper initially had one Ruff annotation on the wrong line; it was corrected before final lint. Runtime source was clean throughout.

Machine-readable case accounting and SHA-256s of the local logs/XML are in [test-results.json](provenance/test-results.json). Original receipts remain in the ignored destination-local `.local/results/` directory for owner review. They are development-session evidence, not sealed independent review or provider qualification.

## Coverage map

| Requirement | Exercised checks |
| --- | --- |
| Start, replay, full result and pagination | Source/installed MCP smoke; `test_real_boundaries_retain_complete_history_and_paginate`; `test_large_native_history_is_losslessly_paginated` |
| Bounded status and cursor semantics | Matrix negatives and `test_restarted_status_zero_wait_is_prompt_and_wait30_is_bounded`; smoke advances returned cursor |
| Cancellation and deadlines | Cancel-before-dispatch, delayed thread/turn ack, blocked read/recovery, saved stop-intent tests; `test_deadline_is_not_renewed_by_idempotent_replay`; source smoke confirms fixture provider interruption |
| Restart reconciliation and persistence | `test_known_ids_reconcile_read_only_after_restart`; `test_restart_preserves_terminal_bytes_and_request_idempotency`; altered/pending artifacts and journal negatives |
| Root exclusivity, independent roots, losing startup | `test_loser_different_profile_cannot_touch_owner_or_probe_runtime`; `test_loser_creates_no_store_children_or_cli_probe`; `test_independent_roots_and_stale_service_handles` |
| Death and lock noninheritance | `test_death_releases_lock_with_exec_child_alive_and_restart_reconciles`; `test_adapter_sigkill_closes_liveness_pipe_and_guardian_cleans_child` |
| Normal shutdown and release ordering | EOF/SIGTERM/SIGINT module-entry tests; shielded shutdown, draining late interrupts, reentrant cleanup and successor noninterference cases |
| Guardian ownership and failures | Descriptor rejection/noninheritance, pre-spawn EOF, startup EOF/data, TERM/KILL bounds, exact owned socket cleanup, guardian loss and failure latching |
| Direct sockets / deterministic alias | Existing positive, wrong-target/owner/mode/type, path substitution, restored substitution, identity changes, dangling alias and non-atomic unlink residual tests unchanged |
| Native permission and protocol semantics | Provider permission negatives, frame validation, unknown phases, complete history versus streaming, lost connection/acknowledgement tests retained |
| Generalization | Explicit native identity/thread-source assertions, neutral package/entry-point and import-boundary checks; test fixture key/version changes exercised in native/guardian suite and MCP smokes |

The wrong-owner socket fixture injects metadata because this account cannot safely create another user's socket. Alias tests use unique task-derived socket names in the runtime's fixed per-user temporary rendezvous directory; they do not attach to other sessions or change shared directory permissions. Residual substitution and compare/unlink tests intentionally demonstrate accepted limitations.

## Reproduction

Use README setup and offline test commands with a fresh short temporary root and umask 077. Run `python tools/verify_transfer.py` without source access to check destination bytes; optionally supply `--source-repository` for read-only original Git-blob comparison. The optional wheel was built locally with `uv build --wheel --out-dir .local/dist`, then installed with hash-locked exported runtime requirements in `.local/wheel-env`; no package was published. Build-system requirement `setuptools>=68` is inherited and not version-locked by the runtime lock: reproducible release builds and their notices remain a release decision.

Full test-file lint is not clean; it retains the 52 source diagnostics. Python 3.12/3.13, Linux, Windows, universal clients, real-provider cancellation/deadlines and the standalone identity's live compatibility are not established by these offline checks.

Local wheel SHA-256 (not published): `776f0b0422b9432554df84f6f3806eea23af17d9d845281c9c0d90b756154e39`. The wheel excludes dependencies, tests, local environments, caches and source checkouts. The runtime lock is the supported checkout installation path.

Verbatim upstream `cffi` and `pycparser` license texts retain their original trailing whitespace; full Git whitespace checking reports those notice bytes. Authored/extracted code and documentation pass the scoped whitespace check. Notice hashes remain exact; the texts were not reformatted to conceal that result.

## Follow-up: missing profile diagnostic

After the owner attempted the placeholder launch path, the entry point gained bounded profile-read diagnostics and the README gained an immediately runnable offline check. Focused extraction/CLI tests: **5 passed**, including three new real-entry-point negatives (missing path, directory, invalid UTF-8). Runtime/affected-test Ruff, transfer manifest and authored diff hygiene passed. The exact README offline source smoke passed again from `/private/tmp`, covering stdio/replay/status/pages/cancel/cleanup with disposable fixtures. No live profile or Codex runtime was created. The earlier wheel and original 484-case receipts remain historical extraction artifacts; this follow-up validated the source entry point without rebuilding that wheel or repeating the unaffected suite.
