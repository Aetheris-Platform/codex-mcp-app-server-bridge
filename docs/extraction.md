# Extraction and provenance

This owner-authorized extraction is a separate developer repository. It changes source location and truthful client branding; it does not migrate a consumer, qualify a provider, or change workflow authority.

## Pinned inputs

| Input | Exact identity | Treatment |
| --- | --- | --- |
| Implementation source committed HEAD | `7a55276bf76e35ecd1004b79a1256da4cca5bc53` | Source of every transferred implementation/test/lock file |
| Implementation source bridge `src` tree | `10ea2a3d556c0a074adcb198cf2dab4a6b11b83d` | 13 Python modules including ownership and guardian |
| Implementation source bridge lock SHA-256 | `790d9cd24d23aa3cce35998e3f3329b68ca58087d829a907b0037c5ead187802` | Initial extraction preserved dependency graph/artifact hashes and renamed the root project; later platform/security changes are recorded separately |
| Design source checkout HEAD | `54848f1c9bdd190c5d06bae4d4a73f69ae9d2299` | ADR/context; existing `.gitignore` edit left untouched |
| Design source locally available origin/main snapshot | `589ca38086f9b98790c9a651305974b001c1da2a` | Committed successor records for socket alias and notification qualification missing at checkout HEAD; no fetch, checkout or edit |
| Prior design-source to implementation-source transfer | `844f56c77d2f73d9b6f4e78bca69fd94ccce49d5` | Historical attribution in retained Implementation source `source-transfer.json`; not the current extraction input |
| Destination remote initial main | `64056785b21b51e269098a47583111723845542d` | Cloned and preserved; existing LICENSE unchanged |

The current [transfer manifest](provenance/transfer-manifest.json) records exact committed source paths, SHA-256s, destinations and final destination SHA-256s. Excluded Implementation source tools/docs are identified individually. The [context manifest](provenance/context-manifest.json) hashes the relevant ADR, full base/D3a design, contracts, amendments, qualification and review records. Those are audit references, not runtime dependencies. Historical `prior-source-transfer.json` describes the earlier transfer only and is retained unchanged; its paths/hashes are not this repository's current inventory.

Source repositories were read through Git blobs and read-only status commands. Implementation source remained at its initial clean HEAD; Design source remained at its initial HEAD with the same preexisting modified path. A moving remote-tracking ref observed in another session is not substituted for the pinned context snapshot. No source repository pull, branch switch, clean, runtime/environment setup or modification was performed.

## Initial extraction changes and boundaries

- Distribution and optional executable: `codex-mcp-app-server-bridge`; the generic Python module remains `codex_app_server_adapter`.
- Native initialize `clientInfo.name` and `threadSource`: `codex-mcp-app-server-bridge`; human-readable title/CLI help generalized. The inherited native client version value `1` and generic MCP server name `codex-app-server` remain unchanged. No downstream identity is impersonated.
- Test-only environment keys use `BRIDGE_`, fixture version is `bridge-native-fixture/1`, and the personal installed-CLI path is replaced by explicit opt-in `BRIDGE_TEST_CODEX_0156`. The installed check is marked separately. Fake model names are fixture inputs, not qualification claims.
- The MCP smoke advances status cursors, verifies one thread/turn submission from fixture request capture, checks matching truthful client/thread identity, waits for confirmed fixture interruption, reconstructs more than 2 MiB by byte pages/digest, and checks destination adapter/guardian paths and cleanup.
- One inherited unsafe-directory fixture now explicitly sets mode 0755, so its negative assertion is meaningful under umask 077. The unchanged source fails identically without that fixture correction; runtime admission was not changed.
- Neutral README, examples and contract replace product-specific installation/activation instructions. Setup is locked and separate from runtime launch. Existing runtime code has no product imports or product-specific environment variables. `CODEX_HOME` remains the actual upstream session-root selection; `modelProvider=openai`, policy `never`, and reviewer `user` remain deliberate permission constraints.

At the initial extraction, runtime changes were description/identity strings in four modules. Later startup and platform changes are documented separately in [platform validation](platform-validation.md). Task identity, persistence, result construction, deadlines, cancellation, permissions, ownership, native-journal handling, direct sockets, alias derivation, process cleanup and disconnect/streaming semantics retain the source implementation. No routing, fallback, resilience redesign or hardening is added.

Excluded: `workflow_core`, formal evidence verifier, role firing, trackers, plan approval, adoption preflight, consumer launchers, product governance hooks and activation machinery. Native journal validation in `store.py` checks retained transport custody and shape; it does not approve a workflow or certify provider identity.

## Historical qualification and accepted limits

Design source ADR 0018 and the base/D3a design establish the asynchronous four-tool interface and bounded guardian. The alias amendment accepts exactly direct sockets and the deterministic 0.156.0 alias. It explicitly retains both path-versus-peer and non-atomic compare/unlink residuals. The notification record diagnosed an in-process probe starving its event loop with a stale cursor; it did not require an adapter repair. Its two 0.156.0 model completions and cancellation were bounded historical observations, not new runs here.

The implementation source's ownership amendment and corrected shutdown at `3a6e46ce4c9fd725752bf3e0eec02949f0c74d97` add root-wide lifetime exclusion, acquisition before probing/reconciliation, noninheritance, and drain-before-release semantics. The current input includes those corrections. Historical failed runs and review limitations remain in their source records, not rewritten as current successes.

The implementation source's 0.159.2 record reports one macOS arm64 `gpt-6.1-sol` completion under the former native identity, and distinguishes later verifier-only journal replay from Bridge execution. We read those committed reports; we did not reproduce their private provider artifacts. No inference extends their live qualification to the standalone identity. Original private journals, credentials and task stores are not copied.

Path admission does not authenticate the connected peer. A restored substitution can evade re-admission; WebSocket handshakes can reach a substitute. Identity comparison and unlink are separate, non-atomic operations. Guardian loss can leave child/socket resources. Cooperative locking does not protect against malicious same-user path replacement or a nonlocking old adapter. These are retained limitations, not extraction regressions repaired here.

## Owner release gates

The owner confirmed Apache-2.0 for the extracted implementation on 2026-10-20; see [license disposition](../LICENSE-DISPOSITION.md). Review the exact third-party distribution notices and separately authorize release publication, public visibility and consumer activation. Live runtime/model qualification requires its own bounded authorization. The repository must remain private until that owner decision. No paid turn is necessary to complete this offline extraction, so none was requested or run.

## Follow-up: missing profile launch diagnostic

The owner encountered the example `/absolute/private/profile.json` path verbatim. Dependency setup does not create that file. The README now places an executable offline smoke before the live launch example and explicitly labels both live paths as placeholders. The entry point catches profile-read failures before service construction and reports a concise error on stderr with exit code 2. Missing files, directory paths and invalid UTF-8 are tested through the actual module entry point from an unrelated directory; stdout remains empty and no runtime/store is created. This narrowly extends the initial identity-only runtime diff with startup diagnostics, not lifecycle or profile-schema changes. The original source hash is preserved and the destination manifest hash updated.

## Neutral source identifiers — 2026-10-20

At the owner's request, the distributed documentation uses `implementation-source`
and `design-source` instead of source product/repository names. Exact commits,
source-relative paths and blob hashes remain unchanged, so the optional
read-only source comparison still works when supplied the source checkout.
The prior transfer record was renamed without changing its bytes. These
identifiers are audit labels, not dependencies or public repository URLs.
Earlier Git history is retained; this cleanup does not rewrite that history.
