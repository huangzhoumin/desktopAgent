"""Planner parsing tests with a fake LLM client."""

from __future__ import annotations

from desktop_agent.config import AgentConfig, LlmConfig
from desktop_agent.planner.llm_client import parse_tool_arguments
from desktop_agent.planner.planner import SYSTEM_PROMPT, LlmPlanner


class FakeClient:
    def __init__(self, message: dict):
        self.message = message
        self.last_messages = None

    def chat(self, messages, tools=None, tool_choice="auto"):
        self.last_messages = messages
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


def test_system_prompt_prefers_dom_for_web_search():
    assert "kind=search_candidate" in SYSTEM_PROMPT
    assert "hot-search" in SYSTEM_PROMPT or "hot-search keyword" in SYSTEM_PROMPT
    assert "vlm_locate" in SYSTEM_PROMPT


def test_url_goal_injects_dom_first_hint():
    cfg = AgentConfig(llm=LlmConfig(api_base="http://x", model="m", api_key="k"))
    client = FakeClient(
        {
            "content": "",
            "tool_calls": [
                {
                    "id": "call_nav",
                    "function": {
                        "name": "browser_navigate",
                        "arguments": '{"url":"https://www.bilibili.com"}',
                    },
                }
            ],
        }
    )
    planner = LlmPlanner(cfg, client=client)  # type: ignore[arg-type]
    call = planner.next_action(
        "打开 https://www.bilibili.com，搜索凡人修仙传",
        [],
    )
    assert call.name == "browser_navigate"
    system = client.last_messages[0]["content"]
    assert "search_candidate" in system
    assert "Do not call find_elements/ocr_find/vlm_locate" in system
    assert "browser_navigate" in system

