"""Deterministic deadline-boundary checks of the shared service and real store."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from test_platform_store import _record

from codex_app_server_adapter.ownership import DataRootOwnership
from codex_app_server_adapter.profile import OperatorProfile
from codex_app_server_adapter.service import AdapterService
from codex_app_server_adapter.store import TaskStore


@pytest.mark.parametrize("confirmation", [False, True], ids=["silent", "interrupted"])
def test_deadline_crossed_in_event_wait_requests_interrupt_before_termination(tmp_path, confirmation):
    root = tmp_path.resolve()
    runtime = Path(sys.executable).resolve()

    async def exercise():
        ownership = DataRootOwnership(root / "data")
        store = TaskStore(root / "data")
        record = _record(root)
        record["provider_outcome"] = None
        task = record["task_id"]
        now = [0.0]

        class Client:
            waits = 0
            interrupts = 0

            async def request(self, method, params):
                assert method == "turn/interrupt"  # No resubmission or history read.
                assert params == {"threadId": record["thread_id"], "turnId": record["turn_id"]}
                retained = store.load(task)
                assert retained["stop_intent"] == "expiry"
                assert retained["interrupt_sent"] is True
                self.interrupts += 1
                return {}

            async def next_event(self, timeout_seconds):
                self.waits += 1
                if self.waits == 1:
                    assert now[0] < 0.5
                    assert store.load(task)["last_event_sequence"] == 2
                    now[0] = 1.0  # Cross the deadline during the event wait, without sleeps.
                    return None
                assert self.waits == 2  # Bound the silent-provider path as well.
                assert self.interrupts == 1
                if not confirmation:
                    return None
                message = {"method": "turn/completed", "params": {
                    "threadId": record["thread_id"],
                    "turn": {"id": record["turn_id"], "status": "interrupted"},
                }}
                return json.dumps(message).encode(), message

        client = Client()
        profile = OperatorProfile(
            runtime_path=runtime, runtime_version="not-executed",
            runtime_sha256="0" * 64, socket_path=None if os.name == "nt" else root / "unused.sock",
            allowed_worktree_roots=(root,), allowed_models=("fixture",),
            allowed_sandboxes=("read-only",), data_root=store.root,
        )
        service = AdapterService(profile, store, lambda: client, ownership=ownership)
        try:
            store.create(record)
            # RPC responses already consume event sequence numbers before streaming.
            store.append_event(task, b'{"method":"thread/start","result":{}}')
            store.append_event(task, b'{"method":"turn/start","result":{}}')
            service._monotonic = lambda: now[0]
            service._monotonic_deadlines[task] = 0.5
            await asyncio.wait_for(service._consume_events(
                task, client, record["thread_id"], record["turn_id"]), 3)
            final = store.load(task)
            assert final["stop_intent"] == "expiry"
            assert final["interrupt_sent"] is True
            assert final["deadline_utc"] == record["deadline_utc"]
            assert client.interrupts == 1
            assert client.waits == 2
            expected = ("failed", "deadline_exceeded") if confirmation else ("unknown", "unconfirmed_termination")
            assert (final["state"], final["reason"]) == expected
        finally:
            await service.shutdown()

    asyncio.run(exercise())
