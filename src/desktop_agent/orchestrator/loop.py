"""LLM agent loop / orchestrator state machine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from desktop_agent.adapters.apps import (
    infer_launch_app_from_goal,
    infer_launch_app_from_question,
)
from desktop_agent.config import AgentConfig
from desktop_agent.memory.trace import TraceStore
from desktop_agent.models import TaskState, TaskSummary, ToolCall, new_id
from desktop_agent.planner.planner import LlmPlanner, Planner
from desktop_agent.safety.policy import PolicyDecision, SafetyGuard
from desktop_agent.tools.runtime import ToolRuntime
from desktop_agent.tools.schema import CONTROL_TOOLS

UserAskFn = Callable[[str, list[str] | None], str]
UserConfirmFn = Callable[[str], bool]

_AFFIRMATIVE = frozenset(
    {
        "y",
        "yes",
        "ok",
        "true",
        "是",
        "好",
        "好的",
        "可以",
        "需要",
        "行",
        "嗯",
        "对",
        "允许",
        "同意",
        "继续",
        "启动",
        "打开",
        "launch",
        "go",
        "proceed",
    }
)


@dataclass
class Orchestrator:
    config: AgentConfig
    runtime: ToolRuntime
    planner: Planner
    safety: SafetyGuard | None = None
    ask_user_fn: UserAskFn | None = None
    confirm_fn: UserConfirmFn | None = None
    # When True (--yes): never stall on permission-style ask_user; act or fail.
    auto_yes: bool = False
    state: TaskState = TaskState.CREATED
    goal: str = ""
    task_id: str = field(default_factory=lambda: new_id("tsk"))
    history: list[dict[str, Any]] = field(default_factory=list)
    steps: int = 0
    ask_user_rounds: int = 0
    pending_call: ToolCall | None = None
    last_result: dict[str, Any] | None = None
    last_error: dict[str, Any] | None = None
    adapter_hints: str = (
        "If 记事本/Notepad is missing, call launch_app app=notepad (never ask_user). "
        "Prefer excel_* COM for Excel cells; "
        "word_new + word_type_text + word_save for Word; "
        "browser_navigate / browser_fill / browser_click / browser_snapshot for web forms "
        "(attach or controlled fallback; prefer css locators like #name); "
        "for Notepad: launch_app notepad then ONLY notepad_type_text + notepad_save_as "
        "(never open 设置/Settings/gear; if stuck there Esc/Back then continue); "
        "dialog_save_as only when a native Save As dialog is already visible; "
        "after any save, call verify_file before done — a visible 另存为 dialog alone is not success; "
        "UIA get_ui_summary/find_elements/click/type_text for generic apps; "
        "if UIA cannot find a control, use ocr_find then click its element_id; "
        "vlm_locate only when OCR also fails and VLM fallback is enabled."
    )

    def __post_init__(self) -> None:
        if self.safety is None:
            self.safety = SafetyGuard(self.config)

    @classmethod
    def create(
        cls,
        config: AgentConfig,
        *,
        planner: Planner | None = None,
        runtime: ToolRuntime | None = None,
        ask_user_fn: UserAskFn | None = None,
        confirm_fn: UserConfirmFn | None = None,
        task_id: str | None = None,
        allowed_tools: set[str] | list[str] | None = None,
        auto_yes: bool = False,
    ) -> Orchestrator:
        tid = task_id or new_id("tsk")
        trace = TraceStore(config.traces_dir, task_id=tid)
        rt = runtime or ToolRuntime(config, trace=trace)
        tools = set(allowed_tools) if allowed_tools is not None else None
        if auto_yes:
            # --yes cannot supply real answers; drop ask_user so the model must act.
            from desktop_agent.tools.schema import ALL_TOOLS

            tools = set(tools if tools is not None else ALL_TOOLS)
            tools.discard("ask_user")
        pl = planner or LlmPlanner(config, allowed_tools=tools)
        return cls(
            config=config,
            runtime=rt,
            planner=pl,
            ask_user_fn=ask_user_fn,
            confirm_fn=confirm_fn,
            task_id=tid,
            auto_yes=auto_yes,
        )

    def run(self, goal: str) -> TaskSummary:
        self.goal = goal
        self.state = TaskState.CREATED
        self.runtime.trace.log("task_start", {"goal": goal, "task_id": self.task_id})
        self._transition(TaskState.PLANNING)

        max_steps = self.config.runtime.max_steps
        while self.state == TaskState.PLANNING:
            if self.steps >= max_steps:
                return self._fail("Max steps exceeded", code="TIMEOUT")

            try:
                call = self.planner.next_action(
                    self.goal,
                    self.history,
                    adapter_hints=self.adapter_hints,
                )
            except Exception as e:
                err = {"code": getattr(e, "code", "LLM_ERROR"), "message": str(e)}
                self.history.append({"kind": "error", "error": err})
                self.runtime.trace.log("planner_error", err)
                return self._fail(str(e), code=err["code"])

            self.runtime.trace.log("plan", call.to_dict())
            if call.thought:
                self.runtime.trace.log("thought", {"text": call.thought})

            summary = self._handle_planned_call(call)
            if summary is not None:
                return summary

        if self.state == TaskState.SUCCEEDED:
            return TaskSummary(
                success=True,
                summary=(self.last_result or {}).get("summary", "done"),
                state=self.state,
                steps=self.steps,
                task_id=self.task_id,
            )
        if self.state == TaskState.CANCELLED:
            return self._fail("Cancelled by user", code="USER_CANCELLED", state=TaskState.CANCELLED)
        return self._fail(
            (self.last_error or {}).get("message", "Task failed"),
            code=(self.last_error or {}).get("code", "AGENT_ERROR"),
        )

    def _handle_planned_call(self, call: ToolCall) -> TaskSummary | None:
        if call.name == "done":
            success = bool(call.arguments.get("success", True))
            summary = str(call.arguments.get("summary", ""))
            self.last_result = {"summary": summary, "success": success}
            self._transition(TaskState.SUCCEEDED if success else TaskState.FAILED)
            self.runtime.trace.log("task_done", self.last_result)
            if not success:
                self.last_error = {"code": "TASK_FAILED", "message": summary}
                return TaskSummary(
                    success=False,
                    summary=summary,
                    state=TaskState.FAILED,
                    steps=self.steps,
                    task_id=self.task_id,
                    error=self.last_error,
                )
            return TaskSummary(
                success=True,
                summary=summary,
                state=TaskState.SUCCEEDED,
                steps=self.steps,
                task_id=self.task_id,
            )

        if call.name == "ask_user":
            return self._handle_ask_user(call)

        if call.name not in CONTROL_TOOLS:
            self.pending_call = call
            self._transition(TaskState.POLICY_CHECK)
            element = None
            if call.name == "click":
                target = (call.arguments or {}).get("target")
                if isinstance(target, str):
                    element = self.runtime.perception.get_element(target)
            decision = (
                self.safety.check_tool(call, element=element)
                if self.safety
                else PolicyDecision(allow=True)
            )
            self.runtime.trace.log("policy", decision.to_dict())
            if decision.reject or not decision.allow:
                return self._fail(decision.reason or "Policy rejected", code="PERMISSION_DENIED")
            if decision.require_confirm:
                self._transition(TaskState.AWAITING_CONFIRM)
                ok = self._confirm(decision.reason or f"Confirm tool: {call.name}")
                if not ok:
                    return self._fail("User denied confirmation", code="USER_CANCELLED")
            return self._execute_and_verify(call)

        return self._fail(f"Unhandled control tool: {call.name}", code="LLM_INVALID_TOOL")

    def _handle_ask_user(self, call: ToolCall) -> TaskSummary | None:
        # Weak local models often ask "要不要启动记事本?" instead of launch_app.
        # Redirect those questions into a real launch so the loop cannot stall.
        args = call.arguments or {}
        question = str(args.get("question") or args.get("message") or args.get("prompt") or "")
        if not question and call.thought:
            question = str(call.thought)
        alias = infer_launch_app_from_question(question, goal=self.goal)
        if not alias:
            goal_alias = infer_launch_app_from_goal(self.goal)
            # --yes, or any window/launch stall while the goal names an app.
            stall_tokens = (
                "窗口",
                "启动",
                "打开",
                "launch",
                "open",
                "找不到",
                "未找到",
                "没找到",
                "没有找到",
                "手动",
                "是否",
                "要不要",
                "需要我",
            )
            if goal_alias and (self.auto_yes or any(tok in question for tok in stall_tokens)):
                alias = goal_alias
        if alias:
            self.runtime.trace.log(
                "ask_user_redirect",
                {"question": question, "launch_app": alias, "auto_yes": self.auto_yes},
            )
            redirected = ToolCall(
                name="launch_app",
                arguments={"app": alias},
                thought=f"auto-launch after ask_user about {alias}",
            )
            return self._handle_planned_call(redirected)

        self.ask_user_rounds += 1
        # Count toward max_steps so ask loops cannot run forever.
        self.steps += 1
        # Two permission-style stalls is enough; then act from the goal or fail.
        max_asks = 2
        if self.ask_user_rounds >= max_asks:
            alias = infer_launch_app_from_goal(self.goal)
            if alias:
                self.runtime.trace.log(
                    "ask_user_redirect",
                    {
                        "question": question,
                        "launch_app": alias,
                        "reason": "max_ask_user_rounds",
                    },
                )
                redirected = ToolCall(
                    name="launch_app",
                    arguments={"app": alias},
                    thought=f"auto-launch after repeated ask_user ({alias})",
                )
                return self._handle_planned_call(redirected)
            return self._fail(
                "Too many ask_user rounds without a clear action",
                code="ASK_USER_LOOP",
            )

        return self._ask_user(call)

    def _execute_and_verify(self, call: ToolCall) -> TaskSummary | None:
        self._transition(TaskState.EXECUTING)
        self.steps += 1
        result = self.runtime.call(call.name, **(call.arguments or {}))
        self.last_result = result
        self.history.append(
            {
                "kind": "tool",
                "name": call.name,
                "arguments": call.arguments,
                "result": result,
            }
        )

        self._transition(TaskState.VERIFY)
        ok = bool(result.get("ok", True)) and not result.get("error")
        if not ok:
            err = result.get("error") or {"code": "ACTION_REJECTED", "message": "tool failed"}
            self.last_error = err if isinstance(err, dict) else {"code": "ACTION_REJECTED", "message": str(err)}
            self.history.append({"kind": "error", "error": self.last_error})
            # Feed error back to planner for retry / ask_user / done.
            self._transition(TaskState.PLANNING)
            return None

        self._transition(TaskState.PLANNING)
        return None

    def _ask_user(self, call: ToolCall) -> TaskSummary | None:
        self._transition(TaskState.AWAITING_CONFIRM)
        question = str(call.arguments.get("question", "Need your input"))
        options = call.arguments.get("options")
        if options is not None and not isinstance(options, list):
            options = [str(options)]
        self.runtime.trace.log("ask_user", {"question": question, "options": options})

        if self.ask_user_fn is None:
            return self._fail("ask_user required but no UI callback wired", code="AGENT_ERROR")

        answer = self.ask_user_fn(question, options)
        self.history.append({"kind": "user", "content": answer, "question": question})
        self.runtime.trace.log("user_answer", {"answer": answer})
        if str(answer).strip().lower() in {"cancel", "abort", "退出", "取消"}:
            self._transition(TaskState.CANCELLED)
            return self._fail("Cancelled by user", code="USER_CANCELLED", state=TaskState.CANCELLED)

        if self._is_affirmative(answer) or self.auto_yes:
            # Steer weak models away from re-asking; they must call an action tool next.
            alias = infer_launch_app_from_question(question, goal=self.goal) or infer_launch_app_from_goal(
                self.goal
            )
            hint = f"call launch_app app={alias}" if alias else "call the needed action tool"
            self.history.append(
                {
                    "kind": "user",
                    "content": (
                        f"Approved. Do not ask_user again about this. {hint} immediately, "
                        "then continue the goal."
                    ),
                    "question": question,
                }
            )

        self._transition(TaskState.PLANNING)
        return None

    @staticmethod
    def _is_affirmative(answer: str) -> bool:
        text = str(answer or "").strip().lower()
        if not text:
            return False
        if text in _AFFIRMATIVE:
            return True
        # Phrases like "是的，你自己打开记事本"
        return any(
            text.startswith(p) or p in text
            for p in ("是的", "需要", "可以", "你自己", "打开", "启动", "yes", "launch", "open")
        )

    def _confirm(self, reason: str) -> bool:
        self.runtime.trace.log("confirm_request", {"reason": reason})
        if self.confirm_fn is None:
            # Fall back to ask_user style if only one callback is provided.
            if self.ask_user_fn is not None:
                ans = self.ask_user_fn(f"{reason}\nProceed? [y/N]", ["y", "n"])
                ok = str(ans).strip().lower() in {"y", "yes", "是", "ok", "允许"}
            else:
                ok = False
        else:
            ok = bool(self.confirm_fn(reason))
        self.runtime.trace.log("confirm_result", {"ok": ok})
        return ok

    def _transition(self, new_state: TaskState) -> None:
        prev = self.state
        self.state = new_state
        self.runtime.trace.log(
            "state",
            {"from": prev.value, "to": new_state.value, "steps": self.steps},
        )

    def _fail(
        self,
        message: str,
        *,
        code: str,
        state: TaskState = TaskState.FAILED,
    ) -> TaskSummary:
        self.last_error = {"code": code, "message": message}
        self.state = state
        self.runtime.trace.log("task_failed", self.last_error)
        return TaskSummary(
            success=False,
            summary=message,
            state=state,
            steps=self.steps,
            task_id=self.task_id,
            error=self.last_error,
        )
