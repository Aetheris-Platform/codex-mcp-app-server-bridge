# Internal Transfer And Dependency Notices

The product owner approved moving the committed bridge implementation from
Aetheris PR #873 into this separately packaged Central developer tool. This
is an internal repository transfer, not a public licensing or distribution
decision. Neither the source bridge directory nor the Aetheris or Central
repository root contains a LICENSE/NOTICE file for the bridge. The wheel is
therefore a local implementation artifact only until a separate package
release decision establishes the applicable rights and notice text.

The bridge's direct runtime requirements are `mcp==2.2.0` and
`websockets==17.1`. Their selected versions and all transitive versions,
artifact URLs and hashes are in the bridge-only `uv.lock`. In the locked
installed environment, package metadata reports MIT for `mcp` and
BSD-3-Clause for `websockets`. Transitive package metadata reports MIT,
MIT-0, BSD-3-Clause, Apache-2.0, Apache-2.0 OR BSD-3-Clause, and PSF-2.0
expressions; these are metadata observations, not a legal clearance or a
substitute for the actual license texts. Before any distribution, verify the
exact locked dependency artifacts and required third-party notices, resolve
the bridge's own license disposition, and retain the release evidence.

This check is bounded to this internal transfer and the actual dependencies
of this wheel. It does not initiate a broader repository licensing program.

## Standalone extraction — 2026-10-19

The destination remote already contained an Apache-2.0 LICENSE at initial commit
`64056785b21b51e269098a47583111723845542d`; its bytes are preserved. This extraction
does not invent or remove a license and does not infer that this file resolves
the earlier transferred-code rights question. The original notice above is
historical: the destination, unlike the source roots inspected then, has LICENSE.

Before public release, the owner must confirm rights and applicability of the
existing license to the Aetheris/Central code, identify any required author notices,
and approve the third-party notice set for the exact distributed artifacts.
Actual installed locked dependency license/NOTICE texts and hashes are retained
in `docs/third-party/`; metadata observations are not legal clearance.
No public release, package publication or consumer activation is authorized.

## Platform follow-up

Native Windows now declares `pywin32==312` directly (also required by the locked
MCP dependency on Windows). The cross-platform lock additionally selects
Windows-only `colorama==0.4.6`. The historical installed-notice inventory above
was gathered on macOS and does not establish retention of these Windows wheel
notices. The owner-run Windows test retained 16 pywin32 notice texts and one colorama
license text. Their original bytes, distribution-relative filenames and hashes
are now retained in `docs/third-party/windows/inventory.json`. Owner review of the
expanded artifact/notice set remains part of the existing public-release gate.
