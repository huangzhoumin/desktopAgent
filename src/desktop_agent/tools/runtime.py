from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from desktop_agent.action.executor import ActionExecutor
from desktop_agent.adapters.browser import BrowserAdapter
from desktop_agent.adapters.excel import ExcelAdapter
from desktop_agent.adapters.word import WordAdapter
from desktop_agent.adapters.wps import WpsAdapter
from desktop_agent.config import AgentConfig
from desktop_agent.errors import AgentError, PermissionDenied, TimeoutError_
from desktop_agent.memory.trace import TraceStore
from desktop_agent.models import ActionResult
from desktop_agent.perception.uia import UiaPerception
from desktop_agent.safety.policy import SafetyGuard


class ToolRuntime:
    def __init__(self, config: AgentConfig, trace: TraceStore | None = None):
        self.config = config
        self.trace = trace or TraceStore(config.traces_dir)
        self.perception = UiaPerception(config)
        self.action = ActionExecutor(self.perception)
        self.safety = SafetyGuard(config)
        self.browser = BrowserAdapter(config)
        self.excel = ExcelAdapter()
        self.word = WordAdapter()
        self.wps = WpsAdapter()
        self._last_obs = None

    def call(self, name: str, **kwargs) -> dict[str, Any]:
        self.trace.log("tool_call", {"name": name, "args": kwargs})
        try:
            result = self._dispatch(name, **kwargs)
            payload = result if isinstance(result, dict) else result.to_dict()
            self.trace.log("tool_result", {"name": name, "result": payload})
            return payload
        except AgentError as e:
            payload = {"ok": False, "error": e.to_dict()}
            self.trace.log("error", {"name": name, "error": e.to_dict()})
            return payload
        except Exception as e:
            payload = {"ok": False, "error": {"code": "AGENT_ERROR", "message": str(e)}}
            self.trace.log("error", {"name": name, "error": payload["error"]})
            return payload

    def _dispatch(self, name: str, **kwargs):
        if name == "list_windows":
            windows = self.perception.list_windows(kwargs.get("app_filter"))
            return {
                "ok": True,
                "windows": [
                    {
                        "window_id": w.window_id,
                        "title": w.title,
                        "app": w.app,
                        "process": w.process,
                        "pid": w.pid,
                        "allowed": self.safety.is_allowed_process(w.process),
                    }
                    for w in windows
                ],
            }
        if name == "focus_window":
            info = self.perception._window_index.get(kwargs["window_id"])
            if info:
                self.safety.assert_window_allowed(info)
            return self.action.focus_window(kwargs["window_id"])
        if name == "get_ui_summary":
            return self._ui_summary(
                max_elements=int(kwargs.get("max_elements", 80)),
                roles=kwargs.get("roles"),
            )
        if name == "find_elements":
            query = kwargs.get("query") or {}
            els = self.perception.find_elements(
                text=query.get("text"),
                role=query.get("role"),
                automation_id=query.get("automation_id"),
                top_k=int(kwargs.get("top_k", 5)),
            )
            return {"ok": True, "elements": [e.to_summary() for e in els]}
        if name == "click":
            self._assert_fg_allowed()
            target = kwargs["target"]
            return self.action.click(
                target,
                button=kwargs.get("button", "left"),
                click_count=int(kwargs.get("click_count", 1)),
            )
        if name == "type_text":
            self._assert_fg_allowed()
            return self.action.type_text(
                text=kwargs["text"],
                target=kwargs.get("target"),
                clear=bool(kwargs.get("clear", True)),
            )
        if name == "press_keys":
            self._assert_fg_allowed()
            return self.action.press_keys(list(kwargs["keys"]))
        if name == "screenshot":
            path = self.trace.screenshot_path("manual.png")
            return self.action.screenshot_foreground(str(path))
        if name == "browser_probe":
            status = self.browser.probe()
            return {
                "ok": status.ok,
                "endpoint": status.endpoint,
                "version": status.version,
                "pages": status.pages,
                "error": status.error,
            }
        if name == "browser_navigate":
            return self.browser.navigate(kwargs["url"], kwargs.get("wait_until", "domcontentloaded"))
        if name == "browser_fill":
            return self.browser.fill(kwargs["locator"], kwargs["value"])
        if name == "browser_click":
            return self.browser.click(kwargs["locator"])
        if name == "browser_snapshot":
            return {"ok": True, "elements": self.browser.snapshot_interactive()}
        if name == "excel_new":
            return self.excel.new()
        if name == "excel_get_range":
            return self.excel.get_range(kwargs["range"], kwargs.get("sheet"))
        if name == "excel_set_range":
            return self.excel.set_range(kwargs["range"], kwargs["value"], kwargs.get("sheet"))
        if name == "excel_save":
            return self.excel.save(kwargs.get("path"))
        if name == "excel_open":
            return self.excel.open(kwargs["path"])
        if name == "word_type_text":
            return self.word.type_text(kwargs["text"])
        if name == "word_save":
            return self.word.save(kwargs.get("path"))
        if name == "wps_probe":
            return self.wps.probe()
        if name == "wps_new":
            return self.wps.new()
        if name == "wps_set_cell":
            return self.wps.set_cell(kwargs["range"], kwargs["value"], kwargs.get("sheet"))
        if name == "wps_get_cell":
            return self.wps.get_cell(kwargs["range"], kwargs.get("sheet"))
        if name == "wps_save":
            return self.wps.save(kwargs.get("path"))
        if name == "wps_type_text":
            return self.wps.type_text(kwargs["text"])
        if name == "wps_save_document":
            return self.wps.save_document(kwargs.get("path"))
        if name == "wait_for":
            return self._wait_for(kwargs.get("condition") or {})
        raise AgentError(f"Unknown tool: {name}", code="LLM_INVALID_TOOL")

    def _wait_for(self, condition: dict[str, Any]) -> dict[str, Any]:
        ctype = str(condition.get("type") or "")
        timeout_ms = int(condition.get("timeout_ms") or self.config.runtime.step_timeout_ms)
        deadline = time.monotonic() + (timeout_ms / 1000.0)
        query = condition.get("query") or {}
        value = condition.get("value")

        if ctype == "timeout":
            time.sleep(max(0.0, timeout_ms / 1000.0))
            return {"ok": True, "condition": ctype, "waited_ms": timeout_ms}

        while True:
            if ctype == "element_exists":
                els = self.perception.find_elements(
                    text=query.get("text"),
                    role=query.get("role"),
                    automation_id=query.get("automation_id"),
                    top_k=1,
                )
                if els:
                    return {
                        "ok": True,
                        "condition": ctype,
                        "element": els[0].to_summary(),
                    }
            elif ctype == "element_gone":
                els = self.perception.find_elements(
                    text=query.get("text"),
                    role=query.get("role"),
                    automation_id=query.get("automation_id"),
                    top_k=1,
                )
                if not els:
                    return {"ok": True, "condition": ctype}
            elif ctype == "window_title_contains":
                needle = str(value or query.get("text") or "")
                fg = self.perception.get_foreground_window()
                if fg and needle and needle.lower() in (fg.title or "").lower():
                    return {
                        "ok": True,
                        "condition": ctype,
                        "title": fg.title,
                    }
            else:
                raise AgentError(f"Unsupported wait condition: {ctype}", code="LLM_INVALID_TOOL")

            if time.monotonic() >= deadline:
                raise TimeoutError_(f"wait_for timed out: {ctype}")
            time.sleep(0.25)

    def _ui_summary(self, max_elements: int = 80, roles: list[str] | None = None) -> dict[str, Any]:
        obs = self.perception.sense_foreground(roles=roles)
        self._last_obs = obs
        if self.config.screenshot_every_step:
            path = self.trace.screenshot_path(f"{obs.obs_id}.png")
            try:
                self.action.screenshot_foreground(str(path))
                obs.screenshot_path = str(path)
            except Exception:
                pass
        summary = obs.to_summary(max_elements=max_elements)
        if obs.foreground_window:
            summary["allowed"] = self.safety.is_allowed_process(obs.foreground_window.process)
        # mask password values
        for el in summary.get("elements", []):
            el["value"] = self.safety.mask_value(el.get("role", ""), el.get("name", ""), el.get("value", ""))
        summary["ok"] = True
        return summary

    def dump_sense(self, path: Path | None = None) -> dict[str, Any]:
        summary = self._ui_summary()
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            summary["dumped_to"] = str(path)
        return summary

    def _assert_fg_allowed(self) -> None:
        if not self.config.safety.enforce_whitelist:
            return
        fg = self.perception.get_foreground_window()
        if fg is None:
            return
        if not self.safety.is_allowed_process(fg.process):
            raise PermissionDenied(f"Foreground app not allowed: {fg.process}")
