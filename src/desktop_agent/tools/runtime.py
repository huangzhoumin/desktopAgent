from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from desktop_agent.action.executor import ActionExecutor
from desktop_agent.adapters.apps import AppLauncher
from desktop_agent.adapters.browser import BrowserAdapter
from desktop_agent.adapters.excel import ExcelAdapter
from desktop_agent.adapters.notepad import NotepadAdapter
from desktop_agent.adapters.word import WordAdapter
from desktop_agent.adapters.wps import WpsAdapter
from desktop_agent.common.dialogs import FileDialogHelper
from desktop_agent.config import AgentConfig
from desktop_agent.errors import AgentError, PermissionDenied, TimeoutError_
from desktop_agent.memory.trace import TraceStore
from desktop_agent.models import UIElement, new_id
from desktop_agent.perception.capture import capture_screen
from desktop_agent.perception.ocr import OcrEngine
from desktop_agent.perception.uia import UiaPerception
from desktop_agent.perception.vlm import VlmLocator
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
        self.notepad = NotepadAdapter()
        self.dialogs = FileDialogHelper()
        self.ocr = OcrEngine(config.perception.ocr_engine)
        self.vlm = VlmLocator(config.llm, model=config.perception.vlm_model or None)
        aliases = set(config.whitelist.values()) | set(config.whitelist.keys())
        # Normalize keys like notepad.exe -> notepad
        aliases |= {a.lower().removesuffix(".exe") for a in aliases}
        self.apps = AppLauncher(allowed_aliases=aliases)
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
        if name == "launch_app":
            return self.apps.launch(kwargs["app"], args=kwargs.get("args"))
        if name == "notepad_type_text":
            return self.notepad.type_text(
                kwargs["text"],
                clear=bool(kwargs.get("clear", True)),
            )
        if name == "notepad_save_as":
            # Closed-loop: open Save As, fill path, confirm, require file on disk.
            return self.notepad.save_as(kwargs["path"])
        if name == "dialog_save_as":
            owner, owner_hwnd, owner_pid = self._foreground_owner()
            return self.dialogs.save_as(
                kwargs["path"],
                owner=owner,
                owner_hwnd=owner_hwnd,
                owner_pid=owner_pid,
                timeout_s=float(kwargs.get("timeout_s") or 5.0),
                wait_file_s=float(kwargs.get("wait_file_s") or 6.0),
            )
        if name == "dialog_click_button":
            names = kwargs.get("names")
            if names:
                return self.dialogs.click_button(
                    names=list(names),
                    title_contains=kwargs.get("title_contains"),
                    timeout_s=float(kwargs.get("timeout_s") or 3.0),
                )
            return self.dialogs.handle_office_prompt(
                action=str(kwargs.get("action") or "yes"),
                timeout_s=float(kwargs.get("timeout_s") or 3.0),
                title_contains=kwargs.get("title_contains"),
                path=kwargs.get("path"),
            )
        if name == "verify_file":
            return self._verify_file(
                kwargs["path"],
                contains=kwargs.get("contains"),
                min_bytes=int(kwargs.get("min_bytes") or 0),
            )
        if name == "get_ui_summary":
            return self._ui_summary(
                max_elements=int(kwargs.get("max_elements", 80)),
                roles=kwargs.get("roles"),
            )
        if name == "find_elements":
            query = kwargs.get("query") or {}
            top_k = int(kwargs.get("top_k", 5))
            els = self.perception.find_elements(
                text=query.get("text"),
                role=query.get("role"),
                automation_id=query.get("automation_id"),
                top_k=top_k,
            )
            fallback = None
            if (
                not els
                and self.config.perception.enable_ocr_fallback
                and query.get("text")
            ):
                ocr_result = self._ocr_find(
                    text=str(query.get("text")),
                    top_k=top_k,
                    scope="foreground",
                )
                if ocr_result.get("ok"):
                    els = [
                        self.perception.get_element(e["element_id"])
                        for e in (ocr_result.get("elements") or [])
                        if self.perception.get_element(e["element_id"]) is not None
                    ]
                    fallback = "ocr"
            return {
                "ok": True,
                "elements": [e.to_summary() for e in els if e is not None],
                "fallback": fallback,
            }
        if name == "ocr_find":
            if not self.config.perception.enable_ocr_fallback:
                return {
                    "ok": False,
                    "error": {
                        "code": "PERMISSION_DENIED",
                        "message": "OCR fallback disabled (set perception.enable_ocr_fallback: true)",
                    },
                }
            return self._ocr_find(
                text=kwargs.get("text"),
                top_k=int(kwargs.get("top_k", 8)),
                scope=str(kwargs.get("scope") or "foreground"),
            )
        if name == "vlm_locate":
            if not self.config.perception.enable_vlm_fallback:
                return {
                    "ok": False,
                    "error": {
                        "code": "PERMISSION_DENIED",
                        "message": "VLM fallback disabled (set perception.enable_vlm_fallback: true)",
                    },
                }
            return self._vlm_locate(
                query=str(kwargs["query"]),
                top_k=int(kwargs.get("top_k", 3)),
                scope=str(kwargs.get("scope") or "foreground"),
            )
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
            scope = str(kwargs.get("scope") or "foreground")
            path = self.trace.screenshot_path("manual.png")
            cap = self._capture(path, scope=scope)
            return {
                "action": "screenshot",
                "ok": True,
                "detail": {
                    "path": cap.path,
                    "scope": cap.scope,
                    "origin": list(cap.origin),
                    "size": [cap.width, cap.height],
                    **cap.detail,
                },
            }
        if name == "browser_probe":
            auto_start = bool(kwargs.get("auto_start_isolated", True))
            status = self.browser.probe(auto_start_isolated=auto_start)
            payload = {
                "ok": status.ok,
                "endpoint": status.endpoint,
                "version": status.version,
                "pages": status.pages,
                "error": status.error,
                "mode": status.mode,
                "auto_started": status.auto_started,
                "configured_mode": self.config.browser.mode,
                "fallback_to_controlled": self.config.browser.fallback_to_controlled,
            }
            if status.auto_started and status.ok:
                payload["hint"] = (
                    "CDP was down; auto-started isolated debug Chrome "
                    "(same profile as scripts/start-chrome-debug-isolated.bat)."
                )
            elif not status.ok and self.config.browser.fallback_to_controlled:
                payload["hint"] = (
                    "CDP attach unavailable; browser_* tools will launch controlled browser (mode A)."
                )
            return payload
        if name == "browser_navigate":
            return self.browser.navigate(kwargs["url"], kwargs.get("wait_until", "domcontentloaded"))
        if name == "browser_fill":
            return self.browser.fill(kwargs["locator"], kwargs["value"])
        if name == "browser_click":
            return self.browser.click(kwargs["locator"])
        if name == "browser_download":
            return self.browser.download(
                kwargs["locator"],
                kwargs["path"],
                timeout_ms=int(kwargs.get("timeout_ms") or 15000),
            )
        if name == "browser_download_bar":
            return self.dialogs.browser_download_bar_action(
                action=str(kwargs.get("action") or "save"),
                path=kwargs.get("path"),
                timeout_s=float(kwargs.get("timeout_s") or 6.0),
                open_if_needed=bool(kwargs.get("open_if_needed", True)),
            )
        if name == "browser_snapshot":
            return {
                "ok": True,
                "elements": self.browser.snapshot_interactive(),
                "mode": self.browser.mode,
            }
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
            elif ctype == "file_exists":
                path = Path(str(value or query.get("path") or ""))
                if path and path.exists():
                    return {
                        "ok": True,
                        "condition": ctype,
                        "path": str(path),
                        "bytes": path.stat().st_size,
                    }
            elif ctype == "file_contains":
                path = Path(str(query.get("path") or ""))
                needle = str(value or query.get("contains") or "")
                if path and path.exists() and needle:
                    try:
                        text = path.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        text = ""
                    if needle in text:
                        return {
                            "ok": True,
                            "condition": ctype,
                            "path": str(path),
                            "bytes": path.stat().st_size,
                        }
            else:
                raise AgentError(f"Unsupported wait condition: {ctype}", code="LLM_INVALID_TOOL")

            if time.monotonic() >= deadline:
                raise TimeoutError_(f"wait_for timed out: {ctype}")
            time.sleep(0.25)

    def _verify_file(
        self,
        path: str | Path,
        *,
        contains: str | None = None,
        min_bytes: int = 0,
    ) -> dict[str, Any]:
        p = Path(path)
        if not p.exists():
            return {
                "ok": False,
                "error": {
                    "code": "ACTION_REJECTED",
                    "message": f"File not found: {p}",
                },
                "path": str(p),
            }
        size = p.stat().st_size
        preview = ""
        if contains is not None or min_bytes >= 0:
            try:
                preview = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                preview = ""
        if size < min_bytes:
            return {
                "ok": False,
                "error": {
                    "code": "ACTION_REJECTED",
                    "message": f"File too small: {size} < {min_bytes}",
                },
                "path": str(p),
                "bytes": size,
            }
        if contains is not None and contains not in preview:
            return {
                "ok": False,
                "error": {
                    "code": "ACTION_REJECTED",
                    "message": f"File does not contain expected text: {contains!r}",
                },
                "path": str(p),
                "bytes": size,
                "preview": preview[:200],
            }
        return {
            "ok": True,
            "path": str(p),
            "bytes": size,
            "preview": preview[:200],
        }

    def _foreground_owner(self):
        """Best-effort owner window for native file dialogs (LLM dialog_save_as)."""
        try:
            import uiautomation as auto

            fg = self.perception.get_foreground_window()
            if fg is None:
                return None, None, None
            hwnd = getattr(fg, "handle", None)
            pid = getattr(fg, "pid", None)
            title = str(getattr(fg, "title", "") or "")
            # If Save As is already focused, do not treat it as the owner tree root.
            if any(k in title for k in ("另存为", "Save As")):
                return None, None, int(pid) if pid else None
            owner = None
            if hwnd:
                try:
                    owner = auto.ControlFromHandle(int(hwnd))
                except Exception:
                    owner = None
            return owner, int(hwnd) if hwnd else None, int(pid) if pid else None
        except Exception:
            return None, None, None

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

    def _capture(self, path: str | Path, *, scope: str = "foreground"):
        bounds = None
        if (scope or "foreground").lower() == "foreground":
            fg = self.perception.get_foreground_window()
            if fg and fg.bounds:
                bounds = (fg.bounds.x, fg.bounds.y, fg.bounds.w, fg.bounds.h)
        return capture_screen(path, scope=scope, bounds=bounds)

    def _ocr_find(
        self,
        *,
        text: str | None = None,
        top_k: int = 8,
        scope: str = "foreground",
    ) -> dict[str, Any]:
        probe = self.ocr.probe()
        if not probe.get("ok"):
            return {
                "ok": False,
                "error": {
                    "code": "AGENT_ERROR",
                    "message": probe.get("error") or "OCR unavailable",
                },
                "hint": "pip install 'desktop-agent[vision]' (rapidocr-onnxruntime)",
            }
        path = self.trace.screenshot_path(f"ocr_{new_id('img')}.png")
        cap = self._capture(path, scope=scope)
        try:
            boxes = self.ocr.recognize(cap.path, origin=cap.origin)
        except Exception as e:
            return {"ok": False, "error": {"code": "AGENT_ERROR", "message": str(e)}}

        needle = (text or "").strip().lower()
        scored: list[tuple[float, UIElement]] = []
        fg = self.perception.get_foreground_window()
        app = fg.app if fg else "unknown"
        window_id = fg.window_id if fg else ""
        for box in boxes:
            if needle:
                low = box.text.lower()
                if needle == low:
                    score = 4.0 + box.confidence
                elif needle in low:
                    score = 2.0 + box.confidence
                else:
                    continue
            else:
                score = box.confidence
            el = UIElement(
                element_id=new_id("el"),
                source="ocr",
                app=app,
                window_id=window_id,
                role="Text",
                name=box.text,
                states=["visible"],
                bounds=box.bounds,
                path=f"OCR/{box.text[:40]}",
                actions=["click"],
                confidence=box.confidence,
                raw_ref={"engine": self.ocr.engine_name, "screenshot": cap.path},
            )
            self.perception.register_synthetic(el)
            scored.append((score, el))
        scored.sort(key=lambda x: x[0], reverse=True)
        picked = [el for _, el in scored[:top_k]]
        return {
            "ok": True,
            "engine": self.ocr.engine_name,
            "screenshot_path": cap.path,
            "scope": cap.scope,
            "element_count": len(picked),
            "elements": [e.to_summary() for e in picked],
        }

    def _vlm_locate(
        self,
        *,
        query: str,
        top_k: int = 3,
        scope: str = "foreground",
    ) -> dict[str, Any]:
        probe = self.vlm.probe()
        if not probe.get("ok"):
            return {
                "ok": False,
                "error": {
                    "code": "VLM_ERROR",
                    "message": probe.get("error") or "VLM unavailable",
                },
            }
        path = self.trace.screenshot_path(f"vlm_{new_id('img')}.png")
        cap = self._capture(path, scope=scope)
        try:
            matches = self.vlm.locate(cap.path, query, origin=cap.origin, top_k=top_k)
        except Exception as e:
            return {
                "ok": False,
                "error": {"code": getattr(e, "code", "VLM_ERROR"), "message": str(e)},
            }

        fg = self.perception.get_foreground_window()
        app = fg.app if fg else "unknown"
        window_id = fg.window_id if fg else ""
        elements: list[UIElement] = []
        for match in matches:
            el = UIElement(
                element_id=new_id("el"),
                source="vlm",
                app=app,
                window_id=window_id,
                role="Target",
                name=match.label,
                states=["visible"],
                bounds=match.bounds,
                path=f"VLM/{match.label[:40]}",
                actions=["click"],
                confidence=match.confidence,
                raw_ref={
                    "model": self.vlm.model,
                    "screenshot": cap.path,
                    "notes": match.notes,
                },
            )
            self.perception.register_synthetic(el)
            elements.append(el)
        return {
            "ok": True,
            "model": self.vlm.model,
            "screenshot_path": cap.path,
            "scope": cap.scope,
            "query": query,
            "element_count": len(elements),
            "elements": [e.to_summary() for e in elements],
            "notes": matches[0].notes if matches else "",
        }
