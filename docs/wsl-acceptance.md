# WSL2 and Windows-to-WSL offline acceptance

Current owner-machine acceptance passed both stages at `cd5d3f6` on 2026-10-20;
see [release acceptance](release-acceptance-2026-10-20.md) for results and limits.
The [earlier `ade75b4` record](wsl-offline-pass-2026-10-20.md) remains historical. The steps
below are reusable acceptance instructions for a chosen candidate.

These are two separate tests. First run the Linux bridge suite inside WSL2;
then run a Windows Python MCP client that launches the bridge through `wsl.exe`.
Neither test uses installed Codex, authentication or a provider. No registration
in a developer tool is needed. Passing this generic client does not qualify all
MCP hosts or live models.

Use an existing WSL2 distribution and ordinary Linux user. Do not install a
distribution, change its default user, install system packages or use
`wsl --shutdown` for test cleanup. Keep the checkout, Python environment,
profiles and stores on the Linux filesystem, not `/mnt/c`. The Windows driver
uses a separate checkout of exactly the same commit on local NTFS. Existing
clones, receipts and other sessions remain untouched.

Both routes use the same Python package. The additional Windows checkout here
is for the independent acceptance driver and byte comparison; forwarding in a
developer workflow uses the bridge installed inside WSL2.

## Local dependency setup without ensurepip

A distribution's missing `ensurepip` does not require installing distro packages.
Use a pinned standalone uv binary in the new checkout's `.local/setup/bin`, with
an existing Linux Python 3.12+ interpreter. The owner-machine handoff pins the
archive URL and SHA-256. Download and verify it during explicit setup, preserve
its bundled notices, then run from the new Linux checkout:

```sh
UV_CACHE_DIR="$PWD/.local/uv-cache" .local/setup/bin/uv sync \
  --locked --extra dev --no-install-project --no-python-downloads \
  --python /usr/bin/python3
```

This creates a checkout-local `.venv`; neither pip nor ensurepip is needed to
bootstrap uv. No package download happens when the bridge or tests launch.
Do not change the lock, install a wheel or install Python globally. If the
existing interpreter, architecture, network or TLS prerequisites are unsuitable,
report the precise blocker. See the [official standalone installation options](https://docs.astral.sh/uv/getting-started/installation/).

## Stage A: inside WSL2

```sh
.venv/bin/python tools/integrate.py --platform wsl
.venv/bin/python tools/test_platform.py --platform wsl
```

The current target is **535 passes, four Windows-only skips and one
installed-runtime deselection**, plus passing focused lint, all 25 transfer
checks and actual source MCP smoke. The runner must exit 0 and print
`OFFLINE CHECKS PASSED`. Retain its entire printed receipt directory. A Docker
pass does not replace this WSL2 run.

## Stage B: Windows initiating client

Run this from Windows PowerShell, with the existing native Windows Python.
The driver uses only the Python standard library. Replace the example values
with the chosen Linux paths, ordinary Linux user and full reviewed commit.
Use a new, private receipt directory; it must not already exist.

```powershell
$Bridge = 'C:\Users\YOUR_USER\src\bridge-acceptance'
$Commit = 'FULL_40_CHARACTER_COMMIT'
$LinuxBridge = '/home/YOUR_USER/src/bridge-acceptance'
py -3 "$Bridge\tests\wsl_forwarding.py" `
  --distribution OADW-Test --linux-user YOUR_USER `
  --linux-checkout $LinuxBridge --linux-python "$LinuxBridge/.venv/bin/python" `
  --expected-commit $Commit `
  --receipts "$Bridge\.local\wsl-forwarding-receipts"
```

`wsl_forwarding.py` launches `wsl.exe` with separate arguments and `--exec`.
Its Linux fixture helper creates a private `/tmp/cbf-*` root, then replaces
itself with the destination's actual `python -m codex_app_server_adapter`
entry point, running from outside the checkout. The helper's setup/control
output uses separate processes and never shares the MCP stream. The adapter,
guardian and runtime imports must resolve into that checkout. Both checkouts'
tracked bytes must match the pinned commit and each other.

The six fixture turns exercise:

- Initialization, discovery of exactly four tools, unknown-task errors, request
  replay and conflicting duplicate rejection.
- Bounded status with advancing cursors; more than 2 MiB of UTF-8 output,
  reconstructed across 513 pages with exact length and SHA-256. Individual
  pages are byte fragments, not independent JSON documents.
- Cancellation acknowledgement followed by confirmed fixture interruption,
  repeated cancellation without another interrupt, and deadline expiry without
  changing the original deadline.
- A competing startup failing before native traffic/store changes, destination
  guardian identity, EOF shutdown, persisted terminal reads with no native traffic.
- Adapter SIGKILL, owned-runtime cleanup, an independent root continuing,
  restart uncertainty and replay without resubmission. The fixture's canned
  read-only history is not live-provider evidence.
- Killing an owned **Windows client subprocess**, leaving its forwarded stdin
  open at death, then verifying Linux adapter/guardian/fixture and socket cleanup.
  This probes actual forwarding behavior; it does not redefine logical native
  disconnect as task cancellation or introduce a new runtime guarantee.

The helper uses pidfds plus recorded process start identity and exact profile
arguments to kill only its own adapters. It does not kill by name, terminate a
WSL distribution or stop another session. Assertions, existing timeout semantics,
private permissions and process ownership must not be relaxed to get a pass.

## Receipt retention on WSL2

Check the filesystem backing `/tmp` before starting. On the accepted WSL host
it was tmpfs: a successful initial run's receipts disappeared when WSL stopped.
Archive the runner's entire printed receipt root to a private directory on
persistent Linux storage before the invoking WSL command exits. For forwarding,
keep an explicitly owned WSL session open until the named fixture root is
archived, then close only that session normally. Never terminate the distro to
clean up. Record any collection failure separately; a console PASS without the
required retained evidence is incomplete. Socket nodes need explicit path/mode
inventory metadata because tar cannot preserve them as ordinary file payloads.

If Linux downloads fail, stop and retain the error. An explicitly authorized
setup alternative can transfer the pinned uv archive and exact locked Linux
wheels through Windows, verify their lock hashes and sizes again inside Linux,
and confirm locked offline sync. This does not permit version substitutions,
credential copying, network-policy changes or dependency fetching at launch.

## Receipts, limits and failure handling

Require exit 0, `summary.json` verdict `PASS`, every listed check, and an empty
final owned-process/socket inventory. Retain stderr logs, `linux-state.json`,
before/after source hashes and the Linux fixture root named in the summary.
The six native request logs must contain exactly six `turn/start` calls total;
cancellation, expiry and client-EOF shutdown each send one interrupt. Source
checkouts must remain unchanged. Hash all copied receipts.

The harness has a 360-second hard limit, plus bounded failure cleanup during
ordinary exceptions. A hard timeout is **FAIL/incomplete cleanup**, never a
pass; inspect only the exact recorded owned processes and preserve evidence.
Failure cleanup is recorded separately and cannot convert a failed check into
success. Test data stays available for review; no automatic file deletion occurs.
Do not fix and retry the candidate during acceptance. Report failures with their
receipts first. Keep WSL2 and forwarding verdicts separate.

`--local` runs the same driver directly on Linux for harness development. It
cannot run on macOS/Windows and is explicitly labelled **not WSL acceptance**.
It does not test the `wsl.exe` boundary. The seven portable harness tests check
argument boundaries, source tampering, process-ownership negatives and corrupt
pagination; they are likewise not Windows/WSL runtime qualification.
