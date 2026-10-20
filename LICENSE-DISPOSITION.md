# License and dependency notices

## Owner decision — 2026-10-20

The owner confirmed Apache-2.0 for this standalone bridge, including the
extracted implementation, resolving the earlier internal-transfer licensing
question. The existing root [LICENSE](LICENSE) is retained unchanged. This
records the license decision; the repository remains private until the owner
separately authorizes publication.

The earlier internal-transfer record did not establish a public license.
Its exact source commit and original file digest remain in the
[transfer manifest](docs/provenance/transfer-manifest.json). The present document
supersedes that unresolved disposition; it does not alter third-party notices.

## Dependency notices

The bridge's locked runtime dependencies include `mcp==2.2.0`,
`websockets==17.1`, and Windows-only `pywin32==312`. The shared `uv.lock`
records direct/transitive versions, artifact URLs and hashes. Dependencies
retain their own licenses; the bridge's Apache-2.0 license does not relicense
them.

Actual license/NOTICE text and hashes for the recorded macOS installation
are retained in [the dependency inventory](docs/third-party/inventory.json).
The Windows acceptance session retained 16 pywin32 312 notices and one
colorama 0.4.6 license in [the Windows inventory](docs/third-party/windows/inventory.json).
Their distribution-relative filenames and original bytes are preserved.

Before distributing a new artifact, reconcile its exact dependency contents
and applicable notices. Build/setup tools and the operator-supplied Codex
executable are not bundled. This repository makes no license grant for those
separate products. Package publication, public visibility and consumer
activation remain separate owner decisions.
