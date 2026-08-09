"""Tool-calling planner (ReAct-style, one primary tool per turn)."""

from __future__ import annotations

from typing import Any, Protocol

from desktop_agent.config import AgentConfig
from desktop_agent.errors import AgentError
from desktop_agent.models import ToolCall
from desktop_agent.planner.llm_client import OpenAICompatibleClient, parse_tool_arguments
from desktop_agent.tools.schema import ALL_TOOLS, openai_tools


class PlannerError(AgentError):
    code = "LLM_INVALID_TOOL"


SYSTEM_PROMPT = """You are a Windows desktop UI agent planner.
You solve the user's goal by calling tools one step at a time.

Rules:
1. Call exactly one tool per turn (or ask_user / done).
2. Prefer semantic tools: browser_* for pages, excel_* / word_* for Office, UIA find+click/type for generic apps.
3. If an app window is missing, call launch_app (notepad/excel/word/edge/chrome) instead of ask_user.
4. After launch_app: list_windows -> focus_window -> type_text/click. For Notepad, type_text without target is OK once focused.
5. Never dump or request the full UIA tree; use get_ui_summary / find_elements with filters.
6. After mutating the UI, verify with get_ui_summary, find_elements, excel_get_range, or browser_snapshot as needed.
7. Do not ask_user repeatedly for the same blocker — retry focus/find/type first; ask_user only when truly stuck.
8. When the goal is complete (or impossible), call done with a short summary.
9. Stay within the application whitelist; do not attempt high-risk system changes.
10. element_id values are only valid from the latest observation — re-find if stale.
"""


class Planner(Protocol):
    def next_action(
        self,
        goal: str,
        history: list[dict[str, Any]],
        *,
        adapter_hints: str = "",
    ) -> ToolCall: ...


class LlmPlanner:
    def __init__(self, config: AgentConfig, client: OpenAICompatibleClient | None = None):
        self.config = config
        self.client = client or OpenAICompatibleClient(config.llm)
        self.tools = openai_tools()

    def next_action(
        self,
        goal: str,
        history: list[dict[str, Any]],
        *,
        adapter_hints: str = "",
    ) -> ToolCall:
        messages = self._build_messages(goal, history, adapter_hints=adapter_hints)
        message = self.client.chat(messages, tools=self.tools, tool_choice="required")
        return self._message_to_tool_call(message)

    def _build_messages(
        self,
        goal: str,
        history: list[dict[str, Any]],
        *,
        adapter_hints: str,
    ) -> list[dict[str, Any]]:
        system = SYSTEM_PROMPT
        if adapter_hints:
            system += f"\n\nAdapter hints:\n{adapter_hints}"

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Goal:\n{goal}"},
        ]

        # Compact recent history (keep last N observations/actions).
        recent = history[-12:]
        if recent:
            lines: list[str] = ["Recent steps:"]
            for i, item in enumerate(recent, 1):
                kind = item.get("kind", "event")
                if kind == "tool":
                    lines.append(
                        f"{i}. TOOL {item.get('name')} args={_short(item.get('arguments'))} "
                        f"-> {_short(item.get('result'))}"
                    )
                elif kind == "user":
                    lines.append(f"{i}. USER: {item.get('content')}")
                elif kind == "error":
                    lines.append(f"{i}. ERROR: {_short(item.get('error'))}")
                else:
                    lines.append(f"{i}. {kind}: {_short(item)}")
            messages.append({"role": "user", "content": "\n".join(lines)})
        return messages

    def _message_to_tool_call(self, message: dict[str, Any]) -> ToolCall:
        thought = (message.get("content") or "") or ""
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            raise PlannerError("LLM returned no tool_calls", code="LLM_INVALID_TOOL")

        tc = tool_calls[0]
        fn = tc.get("function") or {}
        name = str(fn.get("name") or "").strip()
        if name not in ALL_TOOLS:
            raise PlannerError(f"Unknown tool from LLM: {name}", code="LLM_INVALID_TOOL")

        args = parse_tool_arguments(fn.get("arguments"))
        return ToolCall(
            name=name,
            arguments=args,
            thought=thought.strip(),
            call_id=str(tc.get("id") or ToolCall().call_id),
        )


class ScriptedPlanner:
    """Deterministic planner for tests / offline demos."""

    def __init__(self, calls: list[ToolCall]):
        self._calls = list(calls)
        self._i = 0

    def next_action(
        self,
        goal: str,
        history: list[dict[str, Any]],
        *,
        adapter_hints: str = "",
    ) -> ToolCall:
        if self._i >= len(self._calls):
            return ToolCall(name="done", arguments={"summary": "script exhausted", "success": False})
        call = self._calls[self._i]
        self._i += 1
        return call


def _short(value: Any, limit: int = 400) -> str:
    text = repr(value)
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text

