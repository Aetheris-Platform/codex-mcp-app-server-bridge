"""The acceptance client must tolerate quick replies without hiding stuck tasks."""

import asyncio

import pytest
from mcp_polling import poll_status


def test_more_than_thirty_immediate_replies_can_reach_expiry():
    async def exercise():
        calls = 0

        async def fetch(cursor):
            nonlocal calls
            assert cursor == (0 if calls == 0 else 3)
            calls += 1
            return {"cursor": 3, "turn_submitted": True,
                    "state": "running" if calls <= 35 else "failed",
                    "stop_intent": None if calls <= 35 else "expiry"}

        result = await poll_status(fetch, budget_seconds=5, interval=0.001)
        assert calls == 36
        assert (result["state"], result["stop_intent"]) == ("failed", "expiry")

    asyncio.run(exercise())


@pytest.mark.parametrize("hung", [False, True])
def test_never_terminal_or_hung_status_is_bounded(hung):
    async def exercise():
        async def fetch(cursor):
            if hung:
                await asyncio.Event().wait()
            return {"cursor": 1, "state": "running", "turn_submitted": True}

        with pytest.raises(AssertionError, match="did not reach expected state"):
            await poll_status(fetch, budget_seconds=0.03, interval=0.001)

    asyncio.run(exercise())
