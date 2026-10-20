# Security policy

## Report a vulnerability privately

Email **[security@aetherioncore.io](mailto:security@aetherioncore.io)**.
Do not disclose an unpatched vulnerability through a public issue, PR or discussion.
Include the bridge commit/version, OS and Python version, a minimal reproduction,
expected and observed behavior, and your assessment of the impact. Remove tokens,
credentials, private profiles, prompts, journals and unrelated workspace data.
Use synthetic inputs wherever possible. Do not send working credentials.

Maintainers will assess reproducibility and impact and coordinate a fix and
public disclosure with the reporter. This project does not promise a response
or remediation SLA, a bug bounty, or authorization to test third-party systems.

## Supported scope

Before the first public release, security fixes target the current reviewed
candidate. After release, fixes target the latest released 0.1.x version;
older commits and locally modified forks are not separately maintained.
Report the exact commit even when the package version is unchanged. Platform
and runtime qualification is limited to the configurations in
[platform validation](docs/platform-validation.md).

The bridge launches local Codex processes under the operator's account. Protect
private profiles, stores and authentication, and grant access only to trusted
local clients. Sandbox requests and cancellation are not transaction rollback
or hard billing limits. Unix path admission does not authenticate the connected
peer; identity comparison followed by unlink is not atomic. These accepted
boundaries are detailed in the [integration contract](docs/integration-contract.md).

## Maintainer checks

Maintainers run [local security checks](docs/security-testing.md) and
[platform acceptance](docs/integration-testing.md#local-maintainer-acceptance).
No paid GitHub security product, GitHub Actions workflow or model turn is
required. Passing these checks does not establish absence of vulnerabilities.
Git history is preserved by owner decision; it is scanned before release.
