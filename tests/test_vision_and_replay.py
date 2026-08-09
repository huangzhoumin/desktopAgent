"""Unit tests for OCR/VLM helpers and trace replay (no live desktop)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from desktop_agent.config import AgentConfig, LlmConfig, PerceptionConfig, SafetyConfig
from desktop_agent.memory.replay import TraceReplay
from desktop_agent.models import Bounds, ToolCall, UIElement
from desktop_agent.perception.vlm import VlmError, _parse_json_object
from desktop_agent.safety.policy import SafetyGuard


def test_parse_vlm_json_plain_and_fenced():
    data = _parse_json_object('{"matches":[{"label":"OK","x":1,"y":2,"w":3,"h":4,"confidence":0.9}]}')
    assert data["matches"][0]["label"] == "OK"

    fenced = """```json
{"matches": [], "notes": "none"}
```"""
    assert _parse_json_object(fenced)["notes"] == "none"


def test_parse_vlm_json_rejects_garbage():
    with pytest.raises(VlmError):
        _parse_json_object("not json at all")


def test_safety_requires_confirm_for_ocr_click():
    cfg = AgentConfig(
        safety=SafetyConfig(confirm_coordinate_clicks=True),
        perception=PerceptionConfig(min_confidence_to_act=0.75),
    )
    guard = SafetyGuard(cfg)
    el = UIElement(
        element_id="el_1",
        source="ocr",
        app="notepad",
        window_id="win_1",
        role="Text",
        name="Save",
        bounds=Bounds(10, 10, 40, 20),
        confidence=0.9,
    )
    decision = guard.check_tool(ToolCall(name="click", arguments={"target": "el_1"}), element=el)
    assert decision.allow
    assert decision.require_confirm


def test_safety_requires_confirm_for_low_confidence_vlm_even_without_coord_flag():
    cfg = AgentConfig(
        safety=SafetyConfig(confirm_coordinate_clicks=False),
        perception=PerceptionConfig(min_confidence_to_act=0.85),
    )
    guard = SafetyGuard(cfg)
    el = UIElement(
        element_id="el_2",
        source="vlm",
        app="edge",
        window_id="win_2",
        role="Target",
        name="Download",
        bounds=Bounds(1, 1, 10, 10),
        confidence=0.4,
    )
    decision = guard.check_tool(ToolCall(name="click", arguments={"target": "el_2"}), element=el)
    assert decision.require_confirm


def test_trace_replay_summary(tmp_path: Path):
    root = tmp_path / "tsk_demo"
    root.mkdir()
    events = [
        {
            "ts": "2026-08-09T00:00:00+00:00",
            "task_id": "tsk_demo",
            "type": "task_start",
            "payload": {"goal": "do thing"},
        },
        {
            "ts": "2026-08-09T00:00:01+00:00",
            "task_id": "tsk_demo",
            "type": "tool_call",
            "payload": {"name": "list_windows", "args": {}},
        },
        {
            "ts": "2026-08-09T00:00:02+00:00",
            "task_id": "tsk_demo",
            "type": "tool_result",
            "payload": {"name": "list_windows", "result": {"ok": True}},
        },
        {
            "ts": "2026-08-09T00:00:03+00:00",
            "task_id": "tsk_demo",
            "type": "task_done",
            "payload": {"summary": "done", "success": True},
        },
    ]
    (root / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n",
        encoding="utf-8",
    )
    (root / "obs.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    rp = TraceReplay.load(root)
    summary = rp.summary()
    assert summary["task_id"] == "tsk_demo"
    assert summary["goal"] == "do thing"
    assert summary["success"] is True
    assert summary["tool_counts"]["list_windows"] == 1
    assert summary["event_count"] == 4
    assert len(summary["screenshots"]) == 1

    only_calls = rp.filter(event_type="tool_call")
    assert len(only_calls) == 1
    assert "list_windows" in rp.short_line(only_calls[0])


def test_ocr_disabled_returns_permission(monkeypatch):
    from desktop_agent.tools.runtime import ToolRuntime

    cfg = AgentConfig(
        llm=LlmConfig(api_base="http://x", model="m", api_key="k"),
        perception=PerceptionConfig(enable_ocr_fallback=False),
        whitelist={},
    )
    # Avoid real TraceStore side effects in unexpected dirs
    rt = ToolRuntime(cfg)
    out = rt.call("ocr_find", text="Save")
    assert out["ok"] is False
    assert out["error"]["code"] == "PERMISSION_DENIED"


def test_vlm_disabled_returns_permission():
    from desktop_agent.tools.runtime import ToolRuntime

    cfg = AgentConfig(
        llm=LlmConfig(api_base="http://x", model="m", api_key="k"),
        perception=PerceptionConfig(enable_vlm_fallback=False),
        whitelist={},
    )
    rt = ToolRuntime(cfg)
    out = rt.call("vlm_locate", query="Save button")
    assert out["ok"] is False
    assert out["error"]["code"] == "PERMISSION_DENIED"
