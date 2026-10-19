# Bounded live integration — 2026-10-19

**PASS within the scope below.** The owner-authorized test exercised the actual four-tool MCP interface at candidate `bd68b8e2fb11f050733f9682feb5db3972ea7aa8` (tree `8378a51e313b46b925b13ca8e71b20b1d201e9c6`). This report adds evidence; it does not change the tested runtime implementation.

## Environment and isolation

- macOS arm64, Python 3.14.6, the existing locked checkout environment, and a programmatic stdio MCP client launched outside the checkout.
- Existing versioned `codex-cli 0.159.2`, SHA-256 `16593cc2f422d5f398a8e40f550ebbaf1245392528957be342c295920a300704`.
- `gpt-6.1-sol`, low reasoning. An authenticated native `model/list` and `config/read` preflight confirmed model availability and configuration without submitting a turn. Both actual turn journals independently recorded this model and effort.
- A newly created private Codex home with a fresh owner-completed ChatGPT login, file credential storage, and no copied credentials or shared MCP configuration. Separate private profile, socket directory, task store and disposable Git workspace; umask 077.
- Exactly two native turns, each admitted with a 120-second deadline. No harness retries, model substitution, installed-runtime changes, source-repository changes or consumer activation.

## Executed results

| Check | Actual result |
| --- | --- |
| Coding task | Completed in 12.77 seconds; created a pure addition function and three standard-library unit tests |
| Generated tests | Provider ran the requested tests once: 3 passed. Source was inspected and the same three tests passed in an independent local execution |
| Duplicate prevention | Identical start replay returned the same task; native records contain one thread and one turn for that task |
| Bounded status | Advanced the returned cursor, with one-second waits; maximum observed call duration 1.009 seconds |
| Result reconstruction | 338 bytes reconstructed across 11 pages, with matching SHA-256 `f25629092e8e86faeb840eafe3d5429ab97d895e6cf7943287cea139bbf29297` |
| Cancellation | Acknowledgement was `cancel-requested`, followed by `cancelled / provider_interrupted`; second task reached that state in 3.89 seconds |
| Cancellation confirmation | Separate native journal recorded `turn_aborted`; no completion marker file was created. A repeated cancel remained terminal |
| Persistence | Restart retained both terminal statuses, the first request's task identity, and identical result bytes/digest; these reads and replay did not start a native runtime |
| Import identity | Adapter/runtime imports and live guardian command resolved to the destination checkout |
| Ownership cleanup | Normal stdin EOF exited 0; owned guardian, app-server and socket were absent after the live session and after the read-only restart |
| Turn budget | Two isolated native journals, each with exactly one `task_started`; both recorded `gpt-6.1-sol` and low effort |

The coding task's initial file-discovery command returned exit 1 because no matching source/instruction files existed yet. This was retained as a native command result, not mistaken for a unit-test failure or concealed by a rerun. The subsequent coding/test command completed successfully.

The completed task captured its native journal through the bridge with `native_evidence_status=complete`. The cancelled task reported `native_evidence_status=unavailable`; its interruption was confirmed by the MCP terminal status and a separate read of the isolated Codex journal. These are distinct evidence routes, not a claim that both tasks captured journals through the bridge.

## Independent offline review of the same candidate

Claude Code and a separate owner-dispatched Grok session each executed the complete selected offline suite: **487 passed, 0 failed, 1 installed-runtime test deselected**. Both also passed the real MCP fixture smoke from an unrelated directory, destination-only transfer verification, and focused runtime/tooling lint.

Claude's additional probes recorded 96 passes and one test-author assertion error: its comparison incorrectly treated the intentionally retained ownership lock as an unexpected file. That failed assertion remains in its receipts and is not counted as a pass. Grok authored and executed eight adversarial probes; all eight passed. Neither reviewer reproduced a new product defect. The 52 inherited test-file Ruff diagnostics remain; these sessions confirmed their documented count without redoing the original Central comparison.

Claude's MCP transport timed out while its local process continued; its eventual on-disk report and receipts were verified after that process exited. The automated Grok requests timed out without usable results; the later manual session supplied the successful execution evidence. All 80 candidate files in both review snapshots matched the candidate. These are development review records, not formal approvals or sealed attestations.

## Limits and retention

This establishes one small live completion and one confirmed provider-turn cancellation for this runtime/model/effort, platform and programmatic stdio client. Cancellation occurred before the proposed shell command began, so termination of an already-running tool command was not demonstrated. Neither task reached its deadline: deadline preservation was observed, but live expiry enforcement was not tested. Live crash recovery, other clients, other Python versions, Linux/Windows, other models, and a rebuilt wheel remain outside this run. Existing fixture evidence for those lifecycle mechanisms is described separately in [offline validation](validation.md).

Path admission still does not authenticate the connected peer, and identity comparison followed by unlink remains non-atomic. No resilience redesign, routing fallback or new qualification claim changes those accepted boundaries.

[Sanitized machine-readable results](provenance/live-validation-2026-10-19.json) include the candidate, executable digest, outcomes, observed bounds, native turn counts and hashes of private local receipts. Credentials, raw journals, full transcripts, personal filesystem paths and private task stores are excluded from the committed report. The original receipts remain in ignored local storage until the owner-approved post-merge cleanup; hashes alone do not promise permanent retention of those originals.

The live processes were cleaned up immediately. Disposable workspace, private home/credentials and test-store removal is deferred until the owner manually merges the private PR and local `main` is pulled and verified. Preserve this sanitized report when cleaning up. Public release, licensing clearance, package publication and consumer activation remain separate decisions.
