"""Planner parsing tests with a fake LLM client."""

from __future__ import annotations

from desktop_agent.config import AgentConfig, LlmConfig
from desktop_agent.planner.llm_client import parse_tool_arguments
from desktop_agent.planner.planner import LlmPlanner


class FakeClient:
    def __init__(self, message: dict):
        self.message = message

    def chat(self, messages, tools=None, tool_choice="auto"):
        return self.message


def test_parse_tool_arguments_json():
    assert parse_tool_arguments('{"a": 1}') == {"a": 1}
    assert parse_tool_arguments({"b": 2}) == {"b": 2}
    assert parse_tool_arguments("") == {}


def test_llm_planner_extracts_tool_call():
    cfg = AgentConfig(llm=LlmConfig(api_base="http://x", model="m", api_key="k"))
    client = FakeClient(
        {
            "content": "I will list windows",
            "tool_calls": [
                {
                    "id": "call_1",
                    "function": {"name": "list_windows", "arguments": "{}"},
                }
            ],
        }
    )
    planner = LlmPlanner(cfg, client=client)  # type: ignore[arg-type]
    call = planner.next_action("list windows", [])
    assert call.name == "list_windows"
    assert call.thought == "I will list windows"
    assert call.call_id == "call_1"

