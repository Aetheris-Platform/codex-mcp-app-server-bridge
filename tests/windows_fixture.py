"""Build a disposable .exe fixture with the Windows inbox C# compiler.

This setup helper is used only by tests. Runtime launch never compiles or fetches.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def build_fixture(root: Path) -> Path:
    script = Path(__file__).parent / "fixtures/codex_app_server_adapter/stdio_server.py"
    source = root / "Fixture.cs"
    executable = root / "fixture.exe"
    source.write_text(
        "using System; using System.Diagnostics;\n"
        "public class Fixture { public static int Main(string[] args) {\n"
        'if (args.Length == 1 && args[0] == "--version") { Console.WriteLine("bridge-native-fixture/1"); return 0; }\n'
        'if (args.Length != 3 || args[0] != "app-server" || args[1] != "--listen" || args[2] != "stdio://") return 2;\n'
        "var info = new ProcessStartInfo(); info.UseShellExecute = false;\n"
        f"info.FileName = {json.dumps(sys.executable)};\n"
        f"info.Arguments = {json.dumps(chr(34) + str(script) + chr(34))};\n"
        "var child = Process.Start(info); child.WaitForExit(); return child.ExitCode; } }\n",
        encoding="utf-8",
    )
    # Literal PowerShell paths; no execution-policy change or script installation.
    source_literal = str(source).replace("'", "''")
    output_literal = str(executable).replace("'", "''")
    command = ("$ErrorActionPreference = 'Stop'; "
               f"Add-Type -Path '{source_literal}' -OutputAssembly '{output_literal}' "
               "-OutputType ConsoleApplication")
    powershell = Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    subprocess.run(  # noqa: S603 - owned test source, inbox compiler; no network
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", command],
        check=True, capture_output=True, timeout=60,
    )
    return executable
