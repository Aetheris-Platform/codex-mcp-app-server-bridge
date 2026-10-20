"""Offline JSONL Codex fixture for native Windows pipes; no provider access."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from uuid import uuid4


def send(message: dict) -> None:
    sys.stdout.buffer.write(json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


def main() -> None:
    history_root = Path(os.environ["BRIDGE_FIXTURE_HISTORY_DIR"])
    thread_id = ""
    turn_id = ""
    for raw in sys.stdin.buffer:
        message = json.loads(raw)
        with Path(os.environ["BRIDGE_FIXTURE_REQUEST_LOG"]).open("a", encoding="utf-8") as log:
            log.write(json.dumps({**message, "fixture_pid": os.getpid()}) + "\n")
        if "id" not in message:
            continue
        method = message["method"]
        params = message.get("params", {})
        request_id = message["id"]
        if method == "initialize":
            result = {"serverInfo": {"name": "fixture", "version": "bridge-native-fixture/1"}}
        elif method == "thread/start":
            thread_id = str(uuid4())
            result = {"thread": {"id": thread_id}, "cwd": params["cwd"],
                      "model": params["model"], "modelProvider": "openai",
                      "approvalPolicy": "never", "approvalsReviewer": "user",
                      "sandbox": {"type": "readOnly", "networkAccess": False}}
        elif method == "turn/start":
            turn_id = str(uuid4())
            prompt = params["input"][0]["text"]
            turn = {"id": turn_id, "status": "completed", "itemsView": "full",
                    "items": [{"id": "fixture-item", "type": "agentMessage", "phase": "final_answer",
                               "text": "fixture ✓ " + ("x" * (2 * 1024 * 1024) if "large" in prompt else "done")}]}
            (history_root / f"{thread_id}.json").write_text(json.dumps({"thread": {"id": thread_id, "turns": [turn]}}), encoding="utf-8")
            send({"id": request_id, "result": {"turn": {"id": turn_id}}})
            if "hold" not in prompt:
                send({"method": "turn/completed", "params": {"threadId": thread_id, "turn": turn}})
            continue
        elif method == "thread/read":
            result = json.loads((history_root / f"{params['threadId']}.json").read_text(encoding="utf-8"))
        elif method == "turn/interrupt":
            send({"id": request_id, "result": {}})
            send({"method": "turn/completed", "params": {"threadId": params["threadId"],
                  "turn": {"id": params["turnId"], "status": "interrupted"}}})
            continue
        else:
            send({"id": request_id, "error": {"code": -32601, "message": "unsupported fixture method"}})
            continue
        send({"id": request_id, "result": result})


if __name__ == "__main__":
    main()
