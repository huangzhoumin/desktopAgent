"""Unit tests for M2 orchestrator state machine (no real LLM / UI)."""

from __future__ import annotations

from pathlib import Path

from desktop_agent.config import AgentConfig, LlmConfig, RuntimeConfig, SafetyConfig
from desktop_agent.models import ToolCall
from desktop_agent.orchestrator import Orchestrator
from desktop_agent.planner import ScriptedPlanner
from desktop_agent.safety.policy import SafetyGuard


class FakeRuntime:
    def __init__(self, results: dict[str, dict] | None = None):
        self.results = results or {}
        self.calls: list[tuple[str, dict]] = []
        self.trace = _FakeTrace()

    def call(self, name: str, **kwargs):
        self.calls.append((name, kwargs))
        if name in self.results:
            return self.results[name]
        return {"ok": True, "name": name, "args": kwargs}


class _FakeTrace:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []
        self.dir = Path(".")

    def log(self, event_type: str, payload: dict) -> None:
        self.events.append((event_type, payload))


def _cfg(tmp_path: Path | None = None) -> AgentConfig:
    return AgentConfig(
        traces_dir=tmp_path or Path("."),
        llm=LlmConfig(api_base="http://example", model="m", api_key="k"),
        runtime=RuntimeConfig(max_steps=20),
        safety=SafetyConfig(confirm_submit=True, confirm_coordinate_clicks=True),
    )


def test_done_success():
    rt = FakeRuntime()
    orch = Orchestrator(
        config=_cfg(),
        runtime=rt,  # type: ignore[arg-type]
        planner=ScriptedPlanner(
            [ToolCall(name="done", arguments={"summary": "all good", "success": True})]
        ),
    )
    summary = orch.run("noop")
    assert summary.success
    assert summary.state.value == "Succeeded"
    assert summary.summary == "all good"


def test_tool_then_done():
    rt = FakeRuntime()
    orch = Orchestrator(
        config=_cfg(),
        runtime=rt,  # type: ignore[arg-type]
        planner=ScriptedPlanner(
            [
                ToolCall(name="list_windows", arguments={}),
                ToolCall(name="done", arguments={"summary": "listed", "success": True}),
            ]
        ),
    )
    summary = orch.run("list windows")
    assert summary.success
    assert summary.steps == 1
    assert rt.calls[0][0] == "list_windows"
    states = [e[1]["to"] for e in rt.trace.events if e[0] == "state"]
    assert "Planning" in states
    assert "Executing" in states
    assert "Verify" in states


def test_ask_user_then_done():
    answers = iter(["张三"])

    def ask(q, options):
        return next(answers)

    rt = FakeRuntime()
    orch = Orchestrator(
        config=_cfg(),
        runtime=rt,  # type: ignore[arg-type]
        planner=ScriptedPlanner(
            [
                ToolCall(name="ask_user", arguments={"question": "姓名?"}),
                ToolCall(name="done", arguments={"summary": "got name", "success": True}),
            ]
        ),
        ask_user_fn=ask,
    )
    summary = orch.run("fill form")
    assert summary.success
    assert any(h.get("kind") == "user" and h.get("content") == "张三" for h in orch.history)


def test_policy_confirm_denied():
    rt = FakeRuntime()
    orch = Orchestrator(
        config=_cfg(),
        runtime=rt,  # type: ignore[arg-type]
        planner=ScriptedPlanner(
            [
                ToolCall(
                    name="click",
                    arguments={"target": {"x": 10, "y": 20}},
                )
            ]
        ),
        safety=SafetyGuard(_cfg()),
        confirm_fn=lambda reason: False,
    )
    summary = orch.run("click coords")
    assert not summary.success
    assert summary.error and summary.error["code"] == "USER_CANCELLED"


def test_max_steps():
    rt = FakeRuntime()
    cfg = _cfg()
    cfg.runtime.max_steps = 2
    orch = Orchestrator(
        config=cfg,
        runtime=rt,  # type: ignore[arg-type]
        planner=ScriptedPlanner(
            [
                ToolCall(name="list_windows", arguments={}),
                ToolCall(name="list_windows", arguments={}),
                ToolCall(name="list_windows", arguments={}),
            ]
        ),
    )
    summary = orch.run("loop")
    assert not summary.success
    assert summary.error and summary.error["code"] == "TIMEOUT"

