"""Explicit local entry point; it does not install or autostart a service."""

from __future__ import annotations

import argparse
import asyncio
import signal
from pathlib import Path

from .errors import ValidationError
from .mcp_server import run_stdio
from .profile import OperatorProfile
from .runtime import create_owned_service


async def _run(profile_path: Path) -> None:
    text = await asyncio.to_thread(profile_path.read_text, encoding="utf-8")
    service = create_owned_service(OperatorProfile.parse_json(text))
    stop_requested = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed_signals: list[signal.Signals] = []
    stdio_task: asyncio.Task[None] | None = None
    stop_task: asyncio.Task[bool] | None = None
    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(signum, stop_requested.set)
            except (NotImplementedError, RuntimeError):
                continue
            installed_signals.append(signum)
        stdio_task = asyncio.create_task(run_stdio(service))
        stop_task = asyncio.create_task(stop_requested.wait())
        done, _ = await asyncio.wait(
            (stdio_task, stop_task), return_when=asyncio.FIRST_COMPLETED
        )
        if stop_task in done:
            stdio_task.cancel()
        else:
            await stdio_task
    finally:
        try:
            for task in (stop_task, stdio_task):
                if task is not None:
                    task.cancel()
            await asyncio.gather(
                *(task for task in (stop_task, stdio_task) if task is not None),
                return_exceptions=True,
            )
        finally:
            try:
                for signum in installed_signals:
                    loop.remove_signal_handler(signum)
            finally:
                await service.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Codex MCP app-server bridge")
    parser.add_argument("--profile", type=Path, required=True)
    args = parser.parse_args()
    try:
        asyncio.run(_run(args.profile))
    except ValidationError as exc:
        parser.exit(2, f"codex bridge: {exc}\n")


if __name__ == "__main__":
    main()
