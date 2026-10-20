# Version 1.0.0

This change starts from merged main
`75d986066a5d876480b74ec5d67fd12e07b67c7f` and sets the distribution metadata,
root lock entry and MCP `serverInfo.version` to `1.0.0`. The MCP server name
remains `codex-app-server`; the Python module remains
`codex_app_server_adapter`. The developer contract and security-maintenance
scope now refer to 1.0.0 and the latest released 1.0.x, respectively.

No tool schema, task lifecycle, transport, store format or registry dependency
changes. Checkout-based use still needs no wheel installation or package
publication. The native app-server client metadata retains its existing
independently defined version `1`; it is not the MCP server's package version.
The non-distributed maintainer security-tool project also retains its separate
`0.1.0` project version and unchanged lock.

## Verification and qualification

The existing package-boundary check now verifies package/root-lock version
agreement. The actual stdio MCP smoke asserts `serverInfo.version == "1.0.0"`
for both source and installed-wheel routes, in addition to its existing replay,
status, pagination, cancellation and cleanup assertions.

The version-update PR records the exact checks executed and candidate identity,
including wheel metadata and local offline/security results. The
[accepted platform evidence](release-acceptance-2026-10-20.md) remains pinned to
`cd5d3f6`; it is not relabeled as Windows or WSL execution of 1.0.0. The version
change introduces no platform-specific behavior. No new live-provider, Windows
or WSL run is claimed unless separately recorded in the PR.

This is preparation of version 1.0.0, not an assertion that it has been released.
The owner controls merging, public visibility, tags and package/release
publication. Prior failed and passing receipts and Git history remain intact.
