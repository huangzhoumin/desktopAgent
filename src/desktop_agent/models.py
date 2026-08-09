from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:8]}"


class TaskState(str, Enum):
    CREATED = "Created"
    PLANNING = "Planning"
    POLICY_CHECK = "PolicyCheck"
    AWAITING_CONFIRM = "AwaitingConfirm"
    EXECUTING = "Executing"
    VERIFY = "Verify"
    DEGRADED = "Degraded"
    SUCCEEDED = "Succeeded"
    FAILED = "Failed"
    CANCELLED = "Cancelled"


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    thought: str = ""
    call_id: str = field(default_factory=lambda: new_id("call"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "name": self.name,
            "arguments": self.arguments,
            "thought": self.thought,
        }


@dataclass
class TaskSummary:
    success: bool
    summary: str
    state: TaskState
    steps: int = 0
    task_id: str = ""
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "summary": self.summary,
            "state": self.state.value,
            "steps": self.steps,
            "task_id": self.task_id,
            "error": self.error,
        }



@dataclass
class Bounds:
    x: int
    y: int
    w: int
    h: int

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2


@dataclass
class WindowInfo:
    window_id: str
    title: str
    app: str
    process: str
    pid: int
    handle: int
    bounds: Bounds | None = None


@dataclass
class UIElement:
    element_id: str
    source: str
    app: str
    window_id: str
    role: str
    name: str
    automation_id: str = ""
    value: str = ""
    states: list[str] = field(default_factory=list)
    bounds: Bounds | None = None
    path: str = ""
    actions: list[str] = field(default_factory=list)
    confidence: float = 1.0
    raw_ref: dict[str, Any] = field(default_factory=dict)

    def to_summary(self) -> dict[str, Any]:
        data = {
            "element_id": self.element_id,
            "source": self.source,
            "role": self.role,
            "name": self.name,
            "automation_id": self.automation_id,
            "value": self.value,
            "states": self.states,
            "actions": self.actions,
            "confidence": self.confidence,
        }
        if self.bounds:
            data["bounds"] = asdict(self.bounds)
        return data


@dataclass
class Observation:
    obs_id: str
    timestamp: str
    foreground_window: WindowInfo | None
    elements: list[UIElement]
    screenshot_path: str | None = None
    notes: str = ""

    def to_summary(self, max_elements: int = 80) -> dict[str, Any]:
        fg = None
        if self.foreground_window:
            fg = {
                "window_id": self.foreground_window.window_id,
                "title": self.foreground_window.title,
                "app": self.foreground_window.app,
                "process": self.foreground_window.process,
                "pid": self.foreground_window.pid,
            }
        return {
            "obs_id": self.obs_id,
            "timestamp": self.timestamp,
            "foreground_window": fg,
            "element_count": len(self.elements),
            "elements": [e.to_summary() for e in self.elements[:max_elements]],
            "screenshot_path": self.screenshot_path,
            "notes": self.notes,
        }


@dataclass
class ActionResult:
    action: str
    ok: bool
    target: str | None = None
    error: dict[str, Any] | None = None
    latency_ms: int = 0
    detail: Any = None
    post_condition: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
