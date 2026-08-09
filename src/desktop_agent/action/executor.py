from __future__ import annotations

import time
from typing import Any

import uiautomation as auto

from desktop_agent.errors import ActionRejected, ElementNotFound, ElementStale, WindowNotFound
from desktop_agent.models import ActionResult
from desktop_agent.perception.uia import UiaPerception


class ActionExecutor:
    def __init__(self, perception: UiaPerception):
        self.perception = perception

    def focus_window(self, window_id: str) -> ActionResult:
        t0 = time.perf_counter()
        ctrl = self.perception.get_window_control(window_id)
        info = self.perception._window_index.get(window_id)
        if ctrl is None or info is None:
            raise WindowNotFound(f"Unknown window_id: {window_id}")
        try:
            ctrl.SetFocus()
            try:
                ctrl.SetActive()
            except Exception:
                pass
            return ActionResult(
                action="focus_window",
                ok=True,
                target=window_id,
                latency_ms=int((time.perf_counter() - t0) * 1000),
                detail={"title": info.title},
            )
        except Exception as e:
            raise ActionRejected(str(e)) from e

    def click(self, target: str | dict[str, int], button: str = "left", click_count: int = 1) -> ActionResult:
        t0 = time.perf_counter()
        if isinstance(target, dict):
            x, y = int(target["x"]), int(target["y"])
            auto.Click(x, y, askAdmin=False)
            return ActionResult(
                action="click",
                ok=True,
                target=f"{x},{y}",
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )

        ctrl = self.perception.get_control(target)
        el = self.perception.get_element(target)
        if ctrl is None or el is None:
            raise ElementNotFound(f"Unknown element_id: {target}")
        try:
            if button == "left" and click_count == 1:
                try:
                    ctrl.GetInvokePattern().Invoke()
                except Exception:
                    ctrl.Click(simulateMove=False)
            elif click_count >= 2:
                ctrl.DoubleClick(simulateMove=False)
            elif button == "right":
                ctrl.RightClick(simulateMove=False)
            else:
                ctrl.Click(simulateMove=False)
            return ActionResult(
                action="click",
                ok=True,
                target=target,
                latency_ms=int((time.perf_counter() - t0) * 1000),
                detail={"name": el.name, "role": el.role},
            )
        except Exception as e:
            raise ElementStale(str(e)) from e

    def type_text(self, text: str, target: str | None = None, clear: bool = True) -> ActionResult:
        t0 = time.perf_counter()
        if target:
            ctrl = self.perception.get_control(target)
            el = self.perception.get_element(target)
            if ctrl is None or el is None:
                raise ElementNotFound(f"Unknown element_id: {target}")
            try:
                ctrl.SetFocus()
                if clear:
                    try:
                        vp = ctrl.GetValuePattern()
                        vp.SetValue(text)
                        return ActionResult(
                            action="type_text",
                            ok=True,
                            target=target,
                            latency_ms=int((time.perf_counter() - t0) * 1000),
                            detail={"via": "ValuePattern"},
                        )
                    except Exception:
                        auto.SendKeys("{Ctrl}a", waitTime=0.05)
                auto.SendKeys(self._escape_sendkeys(text), waitTime=0.02)
            except Exception as e:
                raise ActionRejected(str(e)) from e
        else:
            if clear:
                auto.SendKeys("{Ctrl}a", waitTime=0.05)
            auto.SendKeys(self._escape_sendkeys(text), waitTime=0.02)

        return ActionResult(
            action="type_text",
            ok=True,
            target=target,
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )

    def press_keys(self, keys: list[str]) -> ActionResult:
        t0 = time.perf_counter()
        mapped = []
        for k in keys:
            kl = k.lower()
            mapping = {
                "ctrl": "Ctrl",
                "control": "Ctrl",
                "alt": "Alt",
                "shift": "Shift",
                "enter": "Enter",
                "tab": "Tab",
                "esc": "Esc",
                "escape": "Esc",
                "backspace": "Backspace",
                "delete": "Delete",
                "win": "Win",
                "up": "Up",
                "down": "Down",
                "left": "Left",
                "right": "Right",
            }
            mapped.append(mapping.get(kl, k))
        if len(mapped) == 1:
            seq = "{" + mapped[0] + "}" if mapped[0] in {
                "Enter", "Tab", "Esc", "Backspace", "Delete", "Up", "Down", "Left", "Right"
            } else mapped[0]
        else:
            # modifier combo like Ctrl+s
            mods = [m for m in mapped[:-1]]
            last = mapped[-1]
            prefix = "".join("{" + m + "}" for m in mods)
            # uiautomation SendKeys uses {Ctrl}a style
            seq = "".join("{" + m + "}" for m in mods) + last
            if last.lower() in {"enter", "tab", "esc"}:
                seq = "".join("{" + m + "}" for m in mods) + "{" + last + "}"
            _ = prefix
        try:
            auto.SendKeys(seq, waitTime=0.05)
            return ActionResult(
                action="press_keys",
                ok=True,
                latency_ms=int((time.perf_counter() - t0) * 1000),
                detail={"keys": keys, "seq": seq},
            )
        except Exception as e:
            raise ActionRejected(str(e)) from e

    @staticmethod
    def _escape_sendkeys(text: str) -> str:
        out: list[str] = []
        for ch in text:
            if ch in "{}+^%~()":
                out.append("{" + ch + "}")
            else:
                out.append(ch)
        return "".join(out)

    def screenshot_foreground(self, path: str) -> ActionResult:
        t0 = time.perf_counter()
        try:
            from PIL import ImageGrab

            img = ImageGrab.grab(all_screens=True)
            img.save(path)
            return ActionResult(
                action="screenshot",
                ok=True,
                latency_ms=int((time.perf_counter() - t0) * 1000),
                detail={"path": path},
            )
        except Exception as e:
            raise ActionRejected(str(e)) from e
