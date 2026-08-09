"""Read-only replay of local task traces (events.jsonl + screenshots)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


@dataclass
class TraceEvent:
    ts: str
    task_id: str
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    line: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "task_id": self.task_id,
            "type": self.type,
            "payload": self.payload,
            "line": self.line,
        }


@dataclass
class TraceReplay:
    path: Path
    events: list[TraceEvent]
    screenshots: list[Path]
    report: dict[str, Any] | None = None

    @classmethod
    def load(cls, trace_path: str | Path) -> TraceReplay:
        root = Path(trace_path)
        if root.is_file() and root.name == "events.jsonl":
            root = root.parent
        if not root.exists():
            raise FileNotFoundError(f"Trace not found: {root}")

        events_path = root / "events.jsonl"
        if not events_path.exists():
            raise FileNotFoundError(f"Missing events.jsonl under {root}")

        events: list[TraceEvent] = []
        with events_path.open("r", encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                events.append(
                    TraceEvent(
                        ts=str(data.get("ts") or ""),
                        task_id=str(data.get("task_id") or root.name),
                        type=str(data.get("type") or "unknown"),
                        payload=data.get("payload") if isinstance(data.get("payload"), dict) else {},
                        line=i,
                    )
                )

        shots = sorted(
            [
                p
                for p in root.iterdir()
                if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
            ]
        )

        report = None
        for candidate in sorted(root.glob("*_report.json")) + sorted(root.glob("report.json")):
            try:
                report = json.loads(candidate.read_text(encoding="utf-8"))
                break
            except Exception:
                continue

        return cls(path=root, events=events, screenshots=shots, report=report)

    def filter(
        self,
        *,
        event_type: str | None = None,
        limit: int | None = None,
    ) -> list[TraceEvent]:
        items = self.events
        if event_type:
            needle = event_type.lower()
            items = [e for e in items if e.type.lower() == needle]
        if limit is not None and limit > 0:
            items = items[:limit]
        return items

    def iter_events(
        self,
        *,
        event_type: str | None = None,
        limit: int | None = None,
    ) -> Iterator[TraceEvent]:
        yield from self.filter(event_type=event_type, limit=limit)

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        tool_names: dict[str, int] = {}
        errors: list[dict[str, Any]] = []
        goal = None
        done = None
        for ev in self.events:
            counts[ev.type] = counts.get(ev.type, 0) + 1
            if ev.type == "task_start":
                goal = ev.payload.get("goal")
            elif ev.type == "task_done":
                done = ev.payload
            elif ev.type == "tool_call":
                name = str(ev.payload.get("name") or "?")
                tool_names[name] = tool_names.get(name, 0) + 1
            elif ev.type in {"error", "planner_error"}:
                errors.append({"ts": ev.ts, "payload": ev.payload})

        success = None
        if isinstance(done, dict) and "success" in done:
            success = bool(done.get("success"))
        elif isinstance(self.report, dict) and "ok" in self.report:
            success = bool(self.report.get("ok"))

        return {
            "trace_dir": str(self.path),
            "task_id": self.events[0].task_id if self.events else self.path.name,
            "goal": goal,
            "event_count": len(self.events),
            "event_counts": counts,
            "tool_counts": tool_names,
            "error_count": len(errors),
            "errors": errors[:20],
            "screenshots": [str(p) for p in self.screenshots],
            "success": success,
            "done": done,
            "report": self.report,
        }

    def short_line(self, event: TraceEvent) -> str:
        p = event.payload
        if event.type == "tool_call":
            return f"{event.type} {p.get('name')} args={_clip(p.get('args'))}"
        if event.type == "tool_result":
            result = p.get("result") if isinstance(p.get("result"), dict) else {}
            ok = result.get("ok")
            return f"{event.type} {p.get('name')} ok={ok} {_clip(result)}"
        if event.type == "plan":
            return f"{event.type} {p.get('name')} {_clip(p.get('arguments'))}"
        if event.type == "thought":
            return f"{event.type} {_clip(p.get('text'))}"
        if event.type == "task_start":
            return f"{event.type} goal={_clip(p.get('goal'))}"
        if event.type == "error":
            return f"{event.type} {_clip(p.get('error') or p)}"
        return f"{event.type} {_clip(p)}"


def _clip(value: Any, n: int = 140) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
    text = text.replace("\n", " ")
    if len(text) > n:
        return text[: n - 1] + "…"
    return text
