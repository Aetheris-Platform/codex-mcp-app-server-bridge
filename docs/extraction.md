# Extraction and provenance

This owner-authorized extraction is a separate developer repository. It changes source location and truthful client branding; it does not migrate a consumer, qualify a provider, or change workflow authority.

## Pinned inputs

| Input | Exact identity | Treatment |
| --- | --- | --- |
| Central committed HEAD | `7a55276bf76e35ecd1004b79a1256da4cca5bc53` | Source of every transferred implementation/test/lock file |
| Central bridge `src` tree | `10ea2a3d556c0a074adcb198cf2dab4a6b11b83d` | 13 Python modules including ownership and guardian |
| Central bridge lock SHA-256 | `790d9cd24d23aa3cce35998e3f3329b68ca58087d829a907b0037c5ead187802` | Dependency graph/artifact hashes unchanged; root project renamed |
| Aetheris checkout HEAD | `54848f1c9bdd190c5d06bae4d4a73f69ae9d2299` | ADR/context; existing `.gitignore` edit left untouched |
| Aetheris locally available origin/main snapshot | `589ca38086f9b98790c9a651305974b001c1da2a` | Committed successor records for socket alias and notification qualification missing at checkout HEAD; no fetch, checkout or edit |
| Prior Aetheris-to-Central transfer | `844f56c77d2f73d9b6f4e78bca69fd94ccce49d5` | Historical attribution in retained Central `source-transfer.json`; not the current extraction input |
| Destination remote initial main | `64056785b21b51e269098a47583111723845542d` | Cloned and preserved; existing LICENSE unchanged |

The current [transfer manifest](provenance/transfer-manifest.json) records exact committed source paths, SHA-256s, destinations and final destination SHA-256s. Excluded Central tools/docs are identified individually. The [context manifest](provenance/context-manifest.json) hashes the relevant ADR, full base/D3a design, contracts, amendments, qualification and review records. Those are audit references, not runtime dependencies. Historical `central-source-transfer.json` describes the earlier transfer only and is retained unchanged; its paths/hashes are not this repository's current inventory.

Source repositories were read through Git blobs and read-only status commands. Central remained at its initial clean HEAD; Aetheris remained at its initial HEAD with the same preexisting modified path. A moving remote-tracking ref observed in another session is not substituted for the pinned context snapshot. No source repository pull, branch switch, clean, runtime/environment setup or modification was performed.

## Changes and boundaries

- Distribution and optional executable: `codex-mcp-app-server-bridge`; the generic Python module remains `codex_app_server_adapter`.
- Native initialize `clientInfo.name` and `threadSource`: `codex-mcp-app-server-bridge`; human-readable title/CLI help generalized. The inherited native client version value `1` and generic MCP server name `codex-app-server` remain unchanged. No downstream identity is impersonated.
- Test-only environment keys use `BRIDGE_`, fixture version is `bridge-native-fixture/1`, and the personal installed-CLI path is replaced by explicit opt-in `BRIDGE_TEST_CODEX_0156`. The installed check is marked separately. Fake model names are fixture inputs, not qualification claims.
- The MCP smoke advances status cursors, verifies one thread/turn submission from fixture request capture, checks matching truthful client/thread identity, waits for confirmed fixture interruption, reconstructs more than 2 MiB by byte pages/digest, and checks destination adapter/guardian paths and cleanup.
- One inherited unsafe-directory fixture now explicitly sets mode 0755, so its negative assertion is meaningful under umask 077. The unchanged source fails identically without that fixture correction; runtime admission was not changed.
- Neutral README, examples and contract replace product-specific installation/activation instructions. Setup is locked and separate from runtime launch. Existing runtime code has no product imports or product-specific environment variables. `CODEX_HOME` remains the actual upstream session-root selection; `modelProvider=openai`, policy `never`, and reviewer `user` remain deliberate permission constraints.

All runtime changes are description/identity strings in four modules. Task identity, persistence, result construction, deadlines, cancellation, permissions, ownership, native-journal handling, direct sockets, alias derivation, process cleanup and disconnect/streaming semantics retain the source implementation. No routing, fallback, resilience redesign or hardening is added.

Excluded: `workflow_core`, formal evidence verifier, role firing, trackers, plan approval, adoption preflight, consumer launchers, product governance hooks and activation machinery. Native journal validation in `store.py` checks retained transport custody and shape; it does not approve a workflow or certify provider identity.

## Historical qualification and accepted limits

Aetheris ADR 0018 and the base/D3a design establish the asynchronous four-tool interface and bounded guardian. The alias amendment accepts exactly direct sockets and the deterministic 0.156.0 alias. It explicitly retains both path-versus-peer and non-atomic compare/unlink residuals. The notification record diagnosed an in-process probe starving its event loop with a stale cursor; it did not require an adapter repair. Its two 0.156.0 model completions and cancellation were bounded historical observations, not new runs here.

Central's ownership amendment and corrected shutdown at `3a6e46ce4c9fd725752bf3e0eec02949f0c74d97` add root-wide lifetime exclusion, acquisition before probing/reconciliation, noninheritance, and drain-before-release semantics. The current input includes those corrections. Historical failed runs and review limitations remain in their source records, not rewritten as current successes.

Central's 0.159.2 record reports one macOS arm64 `gpt-6.1-sol` completion under the former native identity, and distinguishes later verifier-only journal replay from Bridge execution. We read those committed reports; we did not reproduce their private provider artifacts. No inference extends their live qualification to the standalone identity. Original private journals, credentials and task stores are not copied.

Path admission does not authenticate the connected peer. A restored substitution can evade re-admission; WebSocket handshakes can reach a substitute. Identity comparison and unlink are separate, non-atomic operations. Guardian loss can leave child/socket resources. Cooperative locking does not protect against malicious same-user path replacement or a nonlocking old adapter. These are retained limitations, not extraction regressions repaired here.

## Owner release gates

Review the local commits and tests; decide source licensing/attribution and exact third-party distribution notices; separately authorize any live runtime/model qualification, code push/PR, package or release publication, public visibility, and consumer/verifier adoption. The repository must remain private until that owner decision. No paid turn is necessary to complete this offline extraction, so none was requested or run.
