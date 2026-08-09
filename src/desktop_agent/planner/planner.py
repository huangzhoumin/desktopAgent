"""Tool-calling planner (ReAct-style, one primary tool per turn)."""

from __future__ import annotations

import re
from typing import Any, Protocol

from desktop_agent.adapters.apps import infer_launch_app_from_goal
from desktop_agent.config import AgentConfig
from desktop_agent.errors import AgentError
from desktop_agent.models import ToolCall
from desktop_agent.planner.llm_client import OpenAICompatibleClient, parse_tool_arguments
from desktop_agent.tools.schema import ALL_TOOLS, openai_tools


class PlannerError(AgentError):
    code = "LLM_INVALID_TOOL"


_URL_RE = re.compile(r"https?://[^\s\"'<>，。；、]+", re.IGNORECASE)


def _extract_urls(text: str) -> list[str]:
    found: list[str] = []
    for match in _URL_RE.findall(text or ""):
        cleaned = match.rstrip(".,;:)]}")
        if cleaned and cleaned not in found:
            found.append(cleaned)
    return found


SYSTEM_PROMPT = """You are a Windows desktop UI agent planner.
You solve the user's goal by calling tools one step at a time.

Rules:
1. Call exactly one tool per turn (or ask_user / done). Act by default — do not narrate or seek permission.
2. Prefer semantic tools: browser_* for pages, excel_* / word_* for Office, UIA find+click/type for generic apps.
   If the goal contains an http(s) URL, call browser_navigate with that URL first.
   Do NOT launch_app + type into the address bar / Google / Bing / 新标签页搜索框 to open websites.
3. Missing app window → call launch_app yourself immediately. Aliases: notepad(=记事本), excel, word, edge, chrome(=google/谷歌浏览器).
   Browser tasks use Google Chrome by default (browser_* tools); do not prefer Edge unless the goal asks for Edge.
   找不到窗口就自己 launch_app，禁止 ask_user 询问“要不要启动/是否已打开/可否尝试”。
   NEVER ask_user about launching or opening apps — just call launch_app.
4. After launch_app notepad: call notepad_type_text (then notepad_save_as if saving). Do NOT open Notepad Settings (设置), never click the gear, never press Ctrl+,. Do not focus Excel/Word when the goal is Notepad. For Word prefer word_new then word_type_text then word_save.
5. Never dump or request the full UIA tree; use get_ui_summary / find_elements with filters.
6. After mutating the UI, verify with get_ui_summary, find_elements, excel_get_range, browser_snapshot, or verify_file as needed.
7. Do not ask_user repeatedly. If the user already answered yes / approved / said to do it yourself, call the action tool immediately. ask_user is only for missing facts (name, path choice, captcha) — never for "should I open X?".
8. When the goal is complete (or impossible), call done with a short summary.
9. Stay within the application whitelist; do not attempt high-risk system changes.
10. element_id values are only valid from the latest observation — re-find if stale.
11. Seeing a Save As dialog is NOT success. For any save/download goal, call notepad_save_as / dialog_save_as / excel_save (etc.) and then verify_file (or wait_for file_exists/file_contains) before done. Do not call done if the file is missing.
12. Vision fallback: if find_elements returns nothing useful, call ocr_find (then click element_id). Use vlm_locate only after OCR fails / is unavailable. Prefer UIA/DOM/COM over vision.
13. Web page search / form fill (Bilibili, Google, etc.): ALWAYS prefer DOM.
   After browser_navigate, call browser_snapshot. In the snapshot, treat tag=input/textarea
   (type text/search/empty) as the on-page search/form field — even when name/placeholder is a
   hot-search keyword (e.g. 洛克王国…) and NOT the literal words 搜索框/search.
   Prefer elements with kind=search_candidate. Then browser_fill with locator.index / locator.css /
   locator.placeholder / role=searchbox|textbox — never find_elements/ocr_find/vlm_locate just to
   find an on-page search box that already appears as an input in the snapshot.
   After fill, submit via press_keys ["enter"] or browser_click the search button.
   Only use OCR/VLM for in-page controls when browser_snapshot has no suitable input/textarea.
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
    def __init__(
        self,
        config: AgentConfig,
        client: OpenAICompatibleClient | None = None,
        *,
        allowed_tools: set[str] | list[str] | None = None,
    ):
        self.config = config
        self.client = client or OpenAICompatibleClient(config.llm)
        self.allowed_tools = set(allowed_tools) if allowed_tools else None
        self.tools = openai_tools(self.allowed_tools)

    def next_action(
        self,
        goal: str,
        history: list[dict[str, Any]],
        *,
        adapter_hints: str = "",
    ) -> ToolCall:
        messages = self._build_messages(goal, history, adapter_hints=adapter_hints)
        message = self.client.chat(messages, tools=self.tools, tool_choice="required")
        try:
            return self._message_to_tool_call(message)
        except PlannerError as first_err:
            # Local models occasionally return prose despite tool_choice=required.
            nudge = {
                "role": "user",
                "content": (
                    "You must call exactly one tool now via tool_calls "
                    "(not plain text). If a save just failed, retry notepad_save_as / "
                    "excel_save / word_save or launch_app as appropriate. "
                    "Do not explain — call a tool."
                ),
            }
            retry_messages = messages + [nudge]
            message = self.client.chat(retry_messages, tools=self.tools, tool_choice="required")
            try:
                return self._message_to_tool_call(message)
            except PlannerError:
                raise first_err from None

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
        app = infer_launch_app_from_goal(goal)
        if app:
            system += (
                f"\n\nGoal-specific: this task needs app `{app}`. "
                f"If its window is missing, call launch_app with app={app} yourself — "
                f"do not ask_user. 找不到就自己 launch_app app={app}。"
            )
        urls = _extract_urls(goal)
        if urls:
            system += (
                "\n\nGoal-specific: open URL via browser_navigate first "
                f"(url={urls[0]}). Do not use launch_app + address/search box. "
                "Then browser_snapshot; if an input/textarea (esp. kind=search_candidate) exists, "
                "browser_fill it by index/css/placeholder (placeholder may be a trending keyword, "
                "not 搜索框) and press_keys Enter or click search. "
                "Do not call find_elements/ocr_find/vlm_locate for that on-page field."
            )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    f"Goal:\n{goal}\n\n"
                    "Start by calling tools. If you need an app window, launch_app first."
                ),
            },
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
                    q = item.get("question")
                    if q:
                        lines.append(
                            f"{i}. USER answered ask_user({_short(q, 120)}): {item.get('content')} "
                            "(do not ask_user again — call launch_app / the next action tool now)"
                        )
                    else:
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
        if self.allowed_tools is not None and name not in self.allowed_tools:
            raise PlannerError(
                f"Tool not allowed in this task: {name}",
                code="LLM_INVALID_TOOL",
            )

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

