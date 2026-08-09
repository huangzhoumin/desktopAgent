from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from desktop_agent.models import new_id, utc_now_iso


class TraceStore:
    def __init__(self, root: Path, task_id: str | None = None):
        self.task_id = task_id or new_id("tsk")
        self.dir = root / self.task_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.dir / "events.jsonl"

    def log(self, event_type: str, payload: dict[str, Any]) -> None:
        event = {
            "ts": utc_now_iso(),
            "task_id": self.task_id,
            "type": event_type,
            "payload": payload,
        }
        with self.events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def screenshot_path(self, name: str) -> Path:
        path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
