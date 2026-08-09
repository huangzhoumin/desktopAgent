"""Shared helpers for LLM e2e eval runners."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from desktop_agent.adapters.notepad import NotepadAdapter
from desktop_agent.config import AgentConfig, load_config
from desktop_agent.orchestrator import Orchestrator
from desktop_agent.models import TaskSummary


def close_stray_notepads(*, max_windows: int = 8) -> int:
    """Best-effort close leftover Notepad windows so LLM T01 starts clean."""
    closed = 0
    np = NotepadAdapter()
    for _ in range(max_windows):
        wins = np._list_notepad_windows()
        if not wins:
            break
        try:
            win = wins[0]
            np._hwnd = int(win.NativeWindowHandle or 0)
            np._pid = int(win.ProcessId)
            np.close(discard=True)
            closed += 1
            time.sleep(0.25)
        except Exception:
            break
    return closed


def quit_office_apps(*, excel: bool = False, word: bool = False) -> dict[str, str]:
    """Best-effort COM quit so LLM Office evals start from a clean app."""
    out: dict[str, str] = {}
    if excel:
        try:
            import win32com.client

            xl = win32com.client.GetActiveObject("Excel.Application")
            xl.DisplayAlerts = False
            while int(xl.Workbooks.Count) > 0:
                xl.Workbooks(1).Close(SaveChanges=False)
            xl.Quit()
            out["excel"] = "quit"
        except Exception as e:
            out["excel"] = f"skip:{e}"
    if word:
        try:
            import win32com.client

            app = win32com.client.GetActiveObject("Word.Application")
            app.DisplayAlerts = 0
            while int(app.Documents.Count) > 0:
                app.Documents(1).Close(SaveChanges=False)
            app.Quit()
            out["word"] = "quit"
        except Exception as e:
            out["word"] = f"skip:{e}"
    return out


def make_auto_callbacks(auto_yes: bool) -> tuple[Callable, Callable]:
    def ask_user(question: str, options: list[str] | None) -> str:
        print(f"[ask_user] {question}")
        if options:
            print("  options:", ", ".join(options))
        if auto_yes:
            if options:
                for opt in options:
                    low = opt.lower()
                    if low in {"y", "yes", "是", "ok", "true"} or "启动" in opt or "launch" in low:
                        print(f"  auto-answer: {opt}")
                        return opt
                print(f"  auto-answer: {options[0]}")
                return options[0]
            print("  auto-answer: yes")
            return "yes"
        try:
            return input("> ").strip()
        except EOFError:
            return "cancel"

    def confirm(reason: str) -> bool:
        print(f"[confirm] {reason}")
        if auto_yes:
            print("  auto-confirm: yes")
            return True
        try:
            return input("Proceed? [y/N] ").strip().lower() in {"y", "yes", "是", "ok"}
        except EOFError:
            return False

    return ask_user, confirm


def ensure_llm(cfg: AgentConfig) -> dict | None:
    if cfg.llm.configured:
        return None
    return {
        "ok": False,
        "error": "LLM not configured (llm.api_base/model + DESKTOP_AGENT_API_KEY)",
    }


# Compact tool allowlists keep local qwen3:8b on-task (full catalog is too tempting).
BROWSER_TOOLS = {
    "browser_navigate",
    "browser_fill",
    "browser_click",
    "browser_snapshot",
    "browser_probe",
    "done",
    "ask_user",
}
EXCEL_TOOLS = {
    "launch_app",
    "excel_new",
    "excel_open",
    "excel_set_range",
    "excel_get_range",
    "excel_save",
    "verify_file",
    "done",
    "ask_user",
}
WORD_TOOLS = {
    "launch_app",
    "word_new",
    "word_type_text",
    "word_save",
    "verify_file",
    "done",
    "ask_user",
}
NOTEPAD_TOOLS = {
    "launch_app",
    "list_windows",
    "focus_window",
    "notepad_type_text",
    "notepad_save_as",
    "verify_file",
    "done",
    "ask_user",
}


def run_llm_goal(
    goal: str,
    *,
    max_steps: int,
    auto_yes: bool,
    configure: Callable[[AgentConfig], None] | None = None,
    task_id: str | None = None,
    llm_timeout_s: float | None = 300.0,
    allowed_tools: set[str] | list[str] | None = None,
) -> tuple[Orchestrator, TaskSummary, int]:
    cfg = load_config()
    cfg.runtime.max_steps = int(max_steps)
    cfg.llm.max_tool_rounds = int(max_steps)
    if llm_timeout_s is not None:
        cfg.llm.timeout_s = float(llm_timeout_s)
    if configure:
        configure(cfg)
    ask_user, confirm = make_auto_callbacks(auto_yes)
    orch = Orchestrator.create(
        cfg,
        ask_user_fn=ask_user,
        confirm_fn=confirm,
        task_id=task_id,
        allowed_tools=allowed_tools,
        auto_yes=auto_yes,
    )
    print(f"[goal]\n{goal}\n", flush=True)
    t0 = time.perf_counter()
    summary = orch.run(goal)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    return orch, summary, elapsed_ms


def write_llm_report(
    trace_dir: Path,
    task: str,
    report: dict[str, Any],
) -> Path:
    path = trace_dir / f"{task.lower()}_report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("---")
    print(json.dumps({**report, "report": str(path)}, ensure_ascii=False, indent=2))
    return path


def normalize_excel_value(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return [normalize_excel_value(v) for v in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def read_browser_preview(orch: Orchestrator) -> str:
    """Best-effort read of #preview from the orchestrator's browser session."""
    try:
        ctx = orch.runtime.browser._ensure()
        page = ctx.pages[0]
        return page.locator("#preview").inner_text()
    except Exception as e:
        return f"<preview_read_failed: {e}>"
