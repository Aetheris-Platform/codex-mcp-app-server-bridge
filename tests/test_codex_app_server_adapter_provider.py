"""Provider-leaf negative coverage through the real adapter service boundary."""

from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import dataclass
from typing import Literal

import pytest
from test_codex_app_server_adapter import FakeClient, _request, _service

MISSING = object()
Kind = Literal["thread", "turn", "terminal", "delta", "item", "history"]


@dataclass(frozen=True)
class LeafCase:
    kind: Kind
    path: tuple[str, ...]
    bad_type: object
    bad_value: object
    mode: Literal["missing", "type", "value"]


def _cases() -> list[LeafCase]:
    fields: list[tuple[Kind, tuple[str, ...], object, object]] = [
        ("thread", ("thread", "id"), 1, "foreign-thread"),
        ("turn", ("turn", "id"), 1, "foreign-turn"),
        ("terminal", ("threadId",), 1, "foreign-thread"),
        ("terminal", ("turn", "id"), 1, "foreign-turn"),
        ("terminal", ("turn", "status"), 1, "paused"),
        ("delta", ("threadId",), 1, "foreign-thread"),
        ("delta", ("turnId",), 1, "foreign-turn"),
        ("delta", ("itemId",), 1, "foreign-item"),
        ("delta", ("delta",), 1, "conflicting"),
        ("item", ("threadId",), 1, "foreign-thread"),
        ("item", ("turnId",), 1, "foreign-turn"),
        ("item", ("completedAtMs",), "1", -1),
        ("item", ("item", "id"), 1, "foreign-item"),
        ("item", ("item", "type"), 1, "future-item"),
        ("item", ("item", "text"), 1, "conflicting"),
        ("item", ("item", "phase"), 1, "commentary"),
        ("history", ("thread", "id"), 1, "foreign-thread"),
        ("history", ("thread", "turns"), {}, []),
        ("history", ("thread", "turns", "0", "id"), 1, "foreign-turn"),
        ("history", ("thread", "turns", "0", "status"), 1, "paused"),
        ("history", ("thread", "turns", "0", "itemsView"), 1, "summary"),
        ("history", ("thread", "turns", "0", "items"), {}, []),
        ("history", ("thread", "turns", "0", "items", "1", "id"), 1, "foreign-item"),
        ("history", ("thread", "turns", "0", "items", "1", "type"), 1, "future-item"),
        ("history", ("thread", "turns", "0", "items", "1", "text"), 1, "conflicting"),
        ("history", ("thread", "turns", "0", "items", "1", "phase"), 1, "commentary"),
        ("thread", ("cwd",), 1, "/foreign"),
        ("thread", ("model",), 1, "other-model"),
        ("thread", ("modelProvider",), 1, "other-provider"),
        ("thread", ("approvalPolicy",), 1, "always"),
        ("thread", ("approvalsReviewer",), 1, "other-reviewer"),
        ("thread", ("sandbox", "type"), 1, "dangerFullAccess"),
        ("thread", ("sandbox", "networkAccess"), 1, True),
        ("thread", ("sandbox", "writableRoots"), 1, ["/foreign"]),
        ("thread", ("sandbox", "excludeTmpdirEnvVar"), 1, False),
        ("thread", ("sandbox", "excludeSlashTmp"), 1, False),
    ]
    cases: list[LeafCase] = []
    for kind, path, bad_type, bad_value in fields:
        cases.extend([
            LeafCase(kind, path, bad_type, bad_value, "missing"),
            LeafCase(kind, path, bad_type, bad_value, "type"),
            LeafCase(kind, path, bad_type, bad_value, "value"),
        ])
    return cases


class ProviderClient(FakeClient):
    def __init__(self, case: LeafCase | None = None, *, unknown_info: bool = False) -> None:
        super().__init__()
        self.case = case
        self.unknown_info = unknown_info

    async def request(self, method: str, params: dict[str, object]) -> dict[str, object]:
        if method == "thread/start":
            response = await super().request(method, params)
            if self.case is not None and self.case.kind == "thread":
                _mutate(response, self.case.path, _case_bad(self.case))
            return response
        if method == "turn/start":
            response = await super().request(method, params)
            if self.case is not None and self.case.kind == "turn":
                _mutate(response, self.case.path, _case_bad(self.case))
            if self.case is not None and self.case.kind in {"terminal", "delta", "item"}:
                events = list(self.events._queue)
                self.events = asyncio.Queue()
                if self.case.kind == "delta":
                    delta = {
                        "threadId": "thread-opaque-1",
                        "turnId": "turn-opaque-1",
                        "itemId": "item-final",
                        "delta": "done",
                    }
                    _mutate(delta, self.case.path, _case_bad(self.case))
                    events.insert(0, (json.dumps({"method": "item/agentMessage/delta", "params": delta}).encode(), {"method": "item/agentMessage/delta", "params": delta}))
                elif self.case.kind == "item":
                    raw, event = events[0]
                    params_value = copy.deepcopy(event["params"])
                    _mutate(params_value, self.case.path, _case_bad(self.case))
                    event = {"method": "item/completed", "params": params_value}
                    raw = json.dumps(event).encode()
                    events[0] = (raw, event)
                else:
                    raw, event = events[-1]
                    params_value = copy.deepcopy(event["params"])
                    _mutate(params_value, self.case.path, _case_bad(self.case))
                    event = {"method": "turn/completed", "params": params_value}
                    events[-1] = (json.dumps(event).encode(), event)
                for event in events:
                    self.events.put_nowait(event)
            elif self.unknown_info:
                self.events._queue.insert(0, (b'{"method":"item/future","params":{"opaque":true}}', {"method": "item/future", "params": {"opaque": True}}))
            return response
        if method == "thread/read":
            response = copy.deepcopy(self._history)
            if self.case is not None and self.case.kind == "history":
                _mutate(response, self.case.path, _case_bad(self.case))
            return response
        return await super().request(method, params)


@pytest.mark.parametrize("case", _cases(), ids=lambda case: f"{case.kind}-{'-'.join(case.path)}-{case.mode}")
def test_each_consumed_provider_leaf_rejects_missing_type_and_value(
    tmp_path, case: LeafCase
) -> None:
    asyncio.run(_test_each_consumed_provider_leaf_rejects_missing_type_and_value(tmp_path, case))


async def _test_each_consumed_provider_leaf_rejects_missing_type_and_value(
    tmp_path, case: LeafCase
) -> None:
    client = ProviderClient(case)
    service = _service(tmp_path, client)
    request = _request(tmp_path / "worktree")
    if case.path[:1] == ("sandbox",):
        request["sandbox"] = "workspace-write"
    started = await service.start(request)
    status: dict[str, object] = {}
    cursor = 0
    for _ in range(20):
        status = await service.status(
            {"task_id": started["task_id"], "wait_seconds": 2, "cursor": cursor}
        )
        if status["state"] in {"completed", "failed", "unknown", "cancelled"}:
            break
        cursor = int(status["cursor"])
    optional = {
        ("sandbox", "writableRoots"),
        ("sandbox", "networkAccess"),
    }
    freeform_delta = case.kind == "delta" and case.path in {
        ("delta",),
        ("itemId",),
    }
    omitted_history_default = case.kind == "history" and case.path == (
        "thread", "turns", "0", "itemsView"
    )
    if (case.mode == "missing" and (case.path in optional or omitted_history_default)) or (
        freeform_delta and case.mode == "value"
    ):
        assert status["state"] == "completed"
    else:
        assert status["state"] != "completed"
    assert [method for method, _ in client.calls].count("turn/start") <= 1


def test_unknown_informational_event_is_retained_and_does_not_block_valid_result(tmp_path) -> None:
    asyncio.run(_test_unknown_informational_event_is_retained_and_does_not_block_valid_result(tmp_path))


async def _test_unknown_informational_event_is_retained_and_does_not_block_valid_result(tmp_path) -> None:
    client = ProviderClient(unknown_info=True)
    service = _service(tmp_path, client)
    started = await service.start(_request(tmp_path / "worktree"))
    status = await service.status({"task_id": started["task_id"], "wait_seconds": 2})
    assert status["state"] == "completed"
    assert any(b"item/future" in event.read_bytes() for event in (service.store.artifacts / str(started["task_id"]) / "events").iterdir())


def _case_bad(case: LeafCase) -> object:
    if case.mode == "missing":
        return MISSING
    return case.bad_type if case.mode == "type" else case.bad_value


def _mutate(value: dict[str, object], path: tuple[str, ...], bad: object) -> None:
    current: object = value
    for part in path[:-1]:
        current = current[int(part)] if part.isdigit() else current[part]  # type: ignore[index]
    last = path[-1]
    if last.isdigit():
        if bad is MISSING:
            del current[int(last)]  # type: ignore[index]
        else:
            current[int(last)] = bad  # type: ignore[index]
    elif bad is MISSING:
        del current[last]  # type: ignore[index]
    else:
        current[last] = bad
