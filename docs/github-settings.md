# GitHub settings comparison — 2026-10-19

Authenticated repository-admin API reads compared `Aetheris-Platform/open-lifecycle-traceability-standard` with `Aetheris-Platform/codex-mcp-app-server-bridge`. Only destination repository-level non-secret settings were changed. The reference was read-only. Applied controls were read back; this is a bounded comparison, not a complete-match claim.

| Control | Reference | Destination/result |
| --- | --- | --- |
| Visibility and identity | Public reference | **Private**, own name/description/URLs; explicit exception required by owner |
| Issues/projects/wiki/discussions/template | On/off/off/on/off | Matched; wiki off and discussions on applied |
| Merge methods | Merge commits only | Matched; squash/rebase disabled |
| Auto-merge / delete merged branch / update branch | Off/on/on | Matched; last two enabled |
| Merge message/title; signoff; forking | PR_TITLE/MERGE_MESSAGE; off; on | Already equal; no access grants |
| Main branch | `main` | `main`; no branch translation needed |
| Active repository ruleset | Main Branch Protection | Created destination ruleset `25405154`; read-back matches applicable rules |
| Rules | Block deletion/non-fast-forward; signed commits; PR required; merge only; zero required approvals; extra approval for unattributed changes | Matched; existing repository-admin role bypass (role ID 5, always) preserved in equivalent rule; no users/teams/apps added |
| Copilot automatic code review rule | Present, review_on_push=false, draft review=false | **Omitted**: activating an external automated reviewer and potential usage/billing requires separate authorization |
| Classic branch protection | None (explicit branch-not-protected 404) | None; protection is supplied by the ruleset |
| Actions permissions | Enabled, all actions, SHA pinning not required | Already equal; no workflow code copied or run |
| Workflow token permissions | Read; may approve PR reviews | Already equal |
| Actions artifact/log retention | 90 days, maximum 90 | Equal, unchanged |
| Fork contributor approval | First-time contributors | Private destination API 422: public-only control, not copied |
| Private fork workflows | Public reference API 422 | Destination disabled; no write tokens/secrets/variables sent, preserved |
| External workflow access | Public reference API 422 | Destination `none`, preserved; no new access granted |
| Selected Actions list | 409 because all allowed | Same; not applicable |
| Dependabot alerts/security fixes | Enabled/enabled | Enabled and verified; updates not paused |
| Secret scanning/push protection | Enabled/enabled (public repo) | **Disabled**: private-repository entitlement/cost not established; no paid security feature enabled |
| Non-provider patterns / validity checks | Disabled | Disabled, matched |
| AI secret detection/delegated dismissal/delegated bypass | Disabled fields returned | Fields absent from private repository response; not independently configurable/verified here |
| Code scanning default setup | Not configured | GET 403: Code Security must be enabled. No purchase/enablement; not a verified match |
| Interaction limits | Empty/no restriction | API 405: unavailable for private repositories |
| Pages | 404 | 404; no site evidenced; API result alone cannot distinguish every access/availability condition |
| Environments | Zero | Zero |
| Private vulnerability reporting | Disabled | 404 on private destination; inaccessible/not applicable, not claimed equal |
| Topics | Standard-specific subjects | Not copied: unrelated subject matter, not repository controls |

Repository secret values, credentials, deploy keys, tokens and secret-bearing webhook configuration were neither inventoried nor copied. Collaborator/team access, organization-wide settings, installed apps, billing and custom properties governed at organization level were not changed; no equivalent access-control or full organization-policy match is claimed. Repository ruleset inventory included inherited rules and returned no additional parent rules. No PR, code push, release or package publication occurred.

Sanitized machine receipts: [settings evidence](provenance/github-settings.json). They intentionally exclude keys, hooks, secrets and credentials.

Endpoint scope checked against [GitHub Actions permissions documentation](https://docs.github.com/en/rest/actions/permissions).

Description follow-up: updated the destination About text to say developers clone and run the bridge locally, then integrate it into their own workflows. Read-back confirmed private visibility. GitHub is the source distribution location; it is not a hosted bridge endpoint. No code was pushed.
