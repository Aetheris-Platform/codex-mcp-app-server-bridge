# Platform setup and acceptance

One Python package (`codex-mcp-app-server-bridge`), one source checkout layout
and one dependency lock supply the same four MCP tools and task contract on
each target. Developers select their platform during setup; they do not choose
separate platform packages. Each environment gets its own virtual environment
and private profile. The interpreter's operating system selects the backend;
the platform selector selects and validates the setup/test route. It records
only a local preference and never installs dependencies, starts Codex,
authenticates or changes an MCP registration.

```sh
python3 tools/integrate.py
```

On native Windows use `py -3 tools/integrate.py`. For automation pass `--platform macos`, `linux`, `windows` or `wsl`. The selector refuses a mismatch with the interpreter's actual environment. For WSL2, run it **inside** the selected Linux distribution, even when the MCP host is a Windows application.

## Status and boundaries

[Recorded platform validation](platform-validation.md) lists executed results and retained failures.

| Target | Implementation | Recorded acceptance and limits |
| --- | --- | --- |
| macOS | Unix socket, flock and guardian backend | Offline PASS: 507 tests plus MCP smoke, arm64 / Python 3.14.6; earlier live evidence applies only to its recorded candidate/runtime/model |
| Linux | Same Unix backend; anonymous-pipe admission uses procfs | Offline Docker PASS: 507 tests plus MCP smoke, aarch64 / Python 3.14.6; additional WSL2 evidence below |
| Windows 11 native | Anonymous stdio pipes, Windows ACLs, byte-range locks and Job Objects | **Owner-run offline PASS at `5fdff37`** (Windows 11 build 26300 / Python 3.12.14); live runtime/model acceptance remains separate |
| WSL2 | Linux backend inside the distribution | **Owner-run offline PASS at `ade75b4`**: 507 passes, 4 Windows-only skips, 1 installed-runtime deselection; Debian 13.5 x86_64 / Python 3.13.5, ordinary Linux user |
| Windows-to-WSL2 | Windows MCP client launches the same Linux package through `wsl.exe` | **Separate owner-run offline PASS at `ade75b4`**: all 7 harness check groups; Windows 11 build 26300 / Python 3.12.14 initiating client |

Git is required for checkout and the line-ending regression test. Repository attributes keep source/provenance text at LF even with `core.autocrlf=true`; the transfer verifier still checks raw bytes.

Python 3.12+ is declared. Recorded offline acceptance used 3.14.6 on macOS/Linux Docker, 3.12.14 on Windows and 3.13.5 inside WSL2. See the [WSL2/forwarding report](wsl-offline-pass-2026-10-20.md) for exact identities and preserved setup failures. Other combinations require their own receipt. The lock resolves macOS, Linux and Windows packages. This is not a promise that arbitrary filesystem types, kernels, models or MCP clients work. Use local POSIX filesystems on Unix and a local NTFS volume on native Windows; network stores and WSL `/mnt/c` stores are outside this procedure. Follow [actual-host acceptance](integration-testing.md#2-test-your-actual-mcp-host) separately from the fixture tests.

## macos

Use the [README setup](../README.md#get-and-prepare-your-local-checkout) with a stable local Python interpreter, `--platform macos`, and the Unix [profile](../examples/profile.json). Socket parents and data roots must be private (0700), canonical, and free of symlink components. A temporary socket should use a short `/private/tmp/...` path. Keep the installed Codex executable and authentication independently configured.

After locked development setup:

```sh
.venv/bin/python tools/test_platform.py --platform macos
```

## linux

Install a local Python 3.12+ and `uv` as explicit prerequisites. `/proc` must be mounted for guardian pipe admission and the smoke test's owned-process inspection. Setup from the checkout:

```sh
python3 tools/integrate.py --platform linux
UV_CACHE_DIR="$PWD/.local/uv-cache" uv sync --locked --extra dev --no-install-project --no-python-downloads
.venv/bin/python tools/test_platform.py --platform linux
```

Use [profile.json](../examples/profile.json), with **Linux** executable/workspace paths and a short canonical socket path such as `/tmp/cb-USER/app.sock` in a newly created private directory. Create it yourself with mode 0700; do not reuse a shared socket or another adapter's store. The established deterministic alias is still derived under `/tmp/codex-daemon-UID`; it does not follow `TMPDIR` or `CODEX_HOME`.

Use the README's stdio MCP registration with the Linux checkout's `.venv/bin/python` and `PYTHONPATH` pointing at its `src` directory. No installation or download occurs at launch.

## windows

Use a standard Windows 11 user account, local NTFS storage, native Windows Python 3.12+, and a native `codex.exe` supporting `app-server --listen stdio://`. Do not run the Unix scripts, use a `.cmd`/moving launcher as the runtime, or apply Unix chmod instructions as an ACL substitute. Windows accepts `"socket_path": null`; a Unix socket path is rejected. There is no TCP fallback or listening port.

PowerShell setup, using an existing `uv` (or the checkout-local setup alternative below):

```powershell
py -3 tools/integrate.py --platform windows
$env:UV_CACHE_DIR = "$PWD\.local\uv-cache"
uv sync --locked --extra dev --no-install-project --no-python-downloads
& .\.venv\Scripts\python.exe tools/test_platform.py --platform windows
```

If `uv` is unavailable, install it into this checkout only, before syncing:

```powershell
py -3 -m venv .local\setup
& .\.local\setup\Scripts\python.exe -m pip install --cache-dir .local\pip-cache uv==0.12.18
$env:UV_CACHE_DIR = "$PWD\.local\uv-cache"
& .\.local\setup\Scripts\uv.exe sync --locked --extra dev --no-install-project --no-python-downloads
```

Copy [profile.windows.json](../examples/profile.windows.json) and replace every placeholder. Use UTF-8 **without a BOM**. For existing private directories, the bridge checks that the current user owns them and that their DACL grants access only to that user, SYSTEM or local administrators. It does not rewrite existing permissions. Newly created store directories receive a protected inheritable DACL for the user and SYSTEM before records are written. Administrators/SYSTEM remain privileged principals, not an adversarial isolation boundary. Junctions and other reparse-point paths are refused.

To prepare a new private profile directory explicitly, run from the checkout after setup (choose your actual path):

```powershell
$env:PYTHONPATH = "$PWD\src"
& .\.venv\Scripts\python.exe -c "from pathlib import Path; from codex_app_server_adapter.platform_files import make_private_directory; make_private_directory(Path(r'C:\Users\YOUR_USER\bridge-private'))"
```

Save your profile inside that directory; use a separate private Codex home if your integration requires isolated authentication. Select/authenticate the **native Windows executable** independently; the test runner uses only a generated fixture and needs no login.

Example local stdio registration for any MCP host accepting this configuration shape:

```json
{
  "mcpServers": {
    "codex-bridge": {
      "command": "C:\\Users\\YOUR_USER\\src\\codex-mcp-app-server-bridge\\.venv\\Scripts\\python.exe",
      "args": ["-m", "codex_app_server_adapter", "--profile", "C:\\Users\\YOUR_USER\\bridge-private\\profile.json"],
      "env": {
        "PYTHONPATH": "C:\\Users\\YOUR_USER\\src\\codex-mcp-app-server-bridge\\src",
        "CODEX_HOME": "C:\\Users\\YOUR_USER\\bridge-private\\codex-home"
      }
    }
  }
}
```

Adapt the host's registration syntax while preserving command/argument boundaries. Keep its stdin open across tool calls. The Windows backend uses one owned app-server per native connection because native stdio is a single-client transport. Logical disconnect drains its output without killing work; jobs remain session-owned until bridge shutdown. This can use more processes than the shared Unix socket backend. Close idle bridge sessions normally. Job membership is assigned atomically before the child executes; descendants inherit the job, breakaway is not enabled, and the adapter alone owns the kill-on-close handle. No PID scanning or attachment to an existing runtime is used. The bounded owner-run Windows acceptance at `5fdff37` exercised these kernel-level tests successfully; see the [recorded result](windows-offline-pass-2026-10-19.md).

## wsl

Choose `wsl` inside Linux even when a Windows application initiates MCP calls.
Choose `windows` when the bridge itself will run under native Windows Python.
These are deployment choices for the same package; do not share a virtual
environment between Windows and Linux. Forwarding is needed only when the MCP
client runs on Windows and the bridge/workspace runs inside WSL2.

Confirm an existing WSL2 distribution with `wsl.exe --list --verbose`. Installing/enabling WSL or changing a distribution is a separate machine setup action. Clone the repository **inside its Linux filesystem**, for example `/home/USER/src/...`, and follow the Linux instructions using `--platform wsl`. Use Linux Python, Linux Codex, Linux authentication, Linux workspace paths and a Linux private store. Do not mix a Windows `.venv`, `codex.exe`, drive path or profile with this route.

```sh
python3 tools/integrate.py --platform wsl
UV_CACHE_DIR="$PWD/.local/uv-cache" uv sync --locked --extra dev --no-install-project --no-python-downloads
.venv/bin/python tools/test_platform.py --platform wsl
```

A Windows MCP host that supports launching an external stdio command can use:

```json
{
  "mcpServers": {
    "codex-bridge-wsl": {
      "command": "C:\\Windows\\System32\\wsl.exe",
      "args": ["--distribution", "Ubuntu", "--exec", "/usr/bin/env",
        "PYTHONPATH=/home/USER/src/codex-mcp-app-server-bridge/src",
        "CODEX_HOME=/home/USER/bridge-private/codex-home",
        "/home/USER/src/codex-mcp-app-server-bridge/.venv/bin/python",
        "-m", "codex_app_server_adapter", "--profile", "/home/USER/bridge-private/profile.json"]
    }
  }
}
```

Replace the distribution and every path. Passing `--exec` and separate arguments avoids login-shell output corrupting MCP. Validate the host's forwarding, UTF-8, stdin lifetime and cancellation via the [host targets](integration-testing.md). An in-distribution fixture pass alone does not qualify Windows-to-WSL forwarding. See Microsoft's [WSL filesystem guidance](https://learn.microsoft.com/en-us/windows/wsl/filesystems).

Follow the two-stage [WSL2 and Windows forwarding acceptance procedure](wsl-acceptance.md). It includes a Windows-side generic MCP harness and checkout-local dependency setup that does not require `ensurepip`.

## Included test routes

Maintainers execute these routes locally: macOS on their Mac, Linux in an
isolated local Docker container, and native Windows 11 plus WSL2/forwarding on
their Windows test machine. No GitHub Actions workflow or hosted-runner quota
is required. See [local maintainer acceptance](integration-testing.md#local-maintainer-acceptance)
for candidate identity, evidence retention and manual merge review.
Developers integrating the bridge run the route for their own environment and
separately check their actual MCP host.

`tools/test_platform.py` runs tests from outside the checkout, creates a private disposable receipt root, and prints its location. It runs focused lint and the transfer check as well. It does not install dependencies, find or run real Codex, submit model work or delete prior receipts. `OFFLINE CHECKS PASSED` requires every command to exit zero; inspect counts/skips in `tests.xml` before claiming acceptance.

Unix runs the existing full suite plus new shared tests and source MCP smoke. Four Windows-kernel tests are explicitly skipped there; the installed-runtime test is deselected. Native Windows runs `test_windows_platform.py`, `test_platform_transport.py`, `test_platform_setup.py`, `test_platform_store.py` and `test_platform_deadline.py`: **16 expected passes, 1 explicit Unix-only companion skip** at this candidate. It does not claim that every inherited Unix fixture ran on Windows. The Windows MCP test itself covers completion, replay/conflict, status, multi-page UTF-8/hash reconstruction, cancellation, expiry, same-root startup refusal, independent roots, restart reconciliation and normal/process-death cleanup. A separate Job Object test checks descendant termination after owner death. ACL and junction negatives and real lock contention are also included.

The native Windows tests compile a disposable fixture `.exe` with the inbox Windows PowerShell C# compiler. This is offline test setup, never runtime behavior or a shipped native Codex binary. If the compiler is unavailable or policy blocks it, record the check as blocked; do not substitute a real Codex executable or disable machine policy. Five fixture turns in the MCP case are not paid model turns. Failures and unexpected skips must be retained, diagnosed and rerun only after a concrete correction.

The native Windows implementation follows Microsoft's [Job Object lifetime](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects) and [atomic job assignment](https://devblogs.microsoft.com/oldnewthing/20230209-00/?p=107812/) APIs. Existing Unix path-admission and compare/unlink limitations remain unchanged. Neither OS selection nor fixture success authorizes production adoption, a release, a merge or live provider usage.
