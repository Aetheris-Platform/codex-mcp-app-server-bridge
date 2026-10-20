"""Bounded acceptance polling for clients whose status replies can be immediate."""

from __future__ import annotations

import asyncio


async def poll_status(fetch, *, submitted=False, budget_seconds=30.0, interval=0.05):
    value = {}
    try:
        async with asyncio.timeout(budget_seconds):
            while True:
                # Enforce the existing individual-response bound independently.
                value = await asyncio.wait_for(fetch(value.get("cursor", 0)), 5.0)
                if ((submitted and value["turn_submitted"])
                        or value["state"] in {"completed", "cancelled", "failed", "unknown"}):
                    return value
                # Deadline-limited long polls may return immediately. A reply
                # count is not elapsed time and must not starve the worker.
                await asyncio.sleep(interval)
    except TimeoutError as exc:
        raise AssertionError(f"task did not reach expected state within {budget_seconds}s; last status: {value}") from exc
