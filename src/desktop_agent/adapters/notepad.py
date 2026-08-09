from __future__ import annotations

import subprocess
import tempfile
import time
from pathlib import Path

import uiautomation as auto

from desktop_agent.common.dialogs import FileDialogHelper
from desktop_agent.common.win32_window import (
    force_foreground,
    monitor_count,
    move_to_primary,
    primary_screen_size,
    window_rect,
)
from desktop_agent.errors import AdapterUnavailable, ActionRejected, ElementNotFound
from desktop_agent.models import ActionResult


class NotepadAdapter:
    """Closed-loop helpers for Windows Notepad via UIA (multi-monitor safe)."""

    def __init__(self):
        # Modern Notepad shares one process across tabs/windows — track HWND, not spawn PID.
        self._hwnd: int | None = None
        self._pid: int | None = None
        self._dialogs = FileDialogHelper()

    def launch(self, *, move_primary: bool = True) -> ActionResult:
        before = self._hwnd_set()
        subprocess.Popen(["notepad.exe"], close_fds=True)

        win = self._wait_new_window(before, timeout_s=4.0)
        if win is None and before:
            # Win11 may reuse one window as tabs — force a new window with Ctrl+N.
            existing = self._control_by_hwnd(next(iter(before)))
            if existing is not None:
                self._activate(existing)
                auto.SendKeys("{Ctrl}n", waitTime=0.15)
                win = self._wait_new_window(before, timeout_s=4.0)
        if win is None:
            win = self._wait_new_window(before, timeout_s=4.0)
        if win is None:
            raise AdapterUnavailable("Failed to launch Notepad window")

        self._hwnd = int(win.NativeWindowHandle or 0)
        self._pid = int(win.ProcessId)
        if move_primary:
            # Dual-screen: pin to primary so focus/keyboard don't land on the other display.
            move_to_primary(self._hwnd, x=60, y=60, w=1000, h=720)
            time.sleep(0.15)
            win = self._control_by_hwnd(self._hwnd) or win

        self._activate(win)
        rect = window_rect(self._hwnd) if self._hwnd else None
        return ActionResult(
            action="notepad_launch",
            ok=True,
            detail={
                "title": win.Name,
                "pid": self._pid,
                "hwnd": self._hwnd,
                "bounds": rect,
                "monitors": monitor_count(),
                "primary": primary_screen_size(),
            },
        )

    def _wait_new_window(self, before: set[int], timeout_s: float = 4.0):
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            for candidate in self._list_notepad_windows():
                hwnd = int(candidate.NativeWindowHandle or 0)
                if hwnd and hwnd not in before:
                    return candidate
            time.sleep(0.15)
        return None

    def focus(self) -> ActionResult:
        win = self._target_window()
        if win is None:
            raise AdapterUnavailable("Notepad window not found")
        self._activate(win)
        return ActionResult(
            action="notepad_focus",
            ok=True,
            detail={"title": win.Name, "hwnd": self._hwnd},
        )

    def attach_latest(self) -> ActionResult:
        """Bind to the newest Notepad window (e.g. after launch_app)."""
        wins = self._list_notepad_windows()
        if not wins:
            raise AdapterUnavailable("Notepad window not found")
        # Prefer untitled / freshly launched windows.
        preferred = None
        for w in wins:
            if self._is_untitled(str(w.Name or "")) and not self._is_settings_view(w):
                preferred = w
                break
        win = preferred or wins[-1]
        self._ensure_editing(win)
        self._hwnd = int(win.NativeWindowHandle or 0)
        self._pid = int(win.ProcessId)
        self._activate(win)
        return ActionResult(
            action="notepad_attach",
            ok=True,
            detail={"title": win.Name, "hwnd": self._hwnd, "pid": self._pid},
        )

    def type_text(self, text: str, clear: bool = True) -> ActionResult:
        win = self._target_window()
        if win is None:
            raise AdapterUnavailable("Notepad window not found")
        self._activate(win)
        self._ensure_editing(win)
        edit = self._find_editor(win)
        if edit is None:
            raise ElementNotFound("Notepad editor control not found (left settings?)")
        try:
            edit.SetFocus()
        except Exception:
            pass
        time.sleep(0.08)
        # Prefer ValuePattern — no mouse coordinates, multi-monitor safe.
        if clear:
            try:
                edit.GetValuePattern().SetValue(text)
                return ActionResult(
                    action="notepad_type_text",
                    ok=True,
                    detail={"via": "ValuePattern", "length": len(text), "hwnd": self._hwnd},
                )
            except Exception:
                auto.SendKeys("{Ctrl}a", waitTime=0.05)
        else:
            try:
                current = str(edit.GetValuePattern().Value)
                edit.GetValuePattern().SetValue(current + text)
                return ActionResult(
                    action="notepad_type_text",
                    ok=True,
                    detail={"via": "ValuePatternAppend", "length": len(text), "hwnd": self._hwnd},
                )
            except Exception:
                pass
        auto.SendKeys(self._escape(text), waitTime=0.01)
        return ActionResult(
            action="notepad_type_text",
            ok=True,
            detail={"via": "SendKeys", "length": len(text), "hwnd": self._hwnd},
        )

    def save_as(self, path: str | Path) -> ActionResult:
        path = Path(path).resolve()
        win = self._target_window()
        if win is None:
            raise AdapterUnavailable("Notepad window not found")
        self._activate(win)
        self._ensure_editing(win)
        time.sleep(0.12)

        def _strategy(name: str):
            # Escape menus / settings left by a previous failed attempt.
            self._dismiss_chrome_ui(win)
            self._open_strategy(name, win)

        result = self._dialogs.save_as(
            path,
            owner=win,
            owner_hwnd=self._hwnd,
            owner_pid=self._pid,
            open_strategies=[
                # Prefer shortcuts; Alt menus on Win11 Notepad can land in Settings.
                lambda: _strategy("ctrl_shift_s"),
                lambda: _strategy("ctrl_s_untitled"),
                lambda: _strategy("menu_click"),
            ],
            wait_file_s=6.0,
        )
        content = path.read_text(encoding="utf-8", errors="replace")
        detail = dict(result.detail or {})
        detail.update({"preview": content[:120], "hwnd": self._hwnd})
        return ActionResult(action="notepad_save_as", ok=True, detail=detail)

    def _open_strategy(self, name: str, win) -> None:
        self._activate(win)
        self._ensure_editing(win)
        time.sleep(0.05)
        if name == "ctrl_shift_s":
            auto.SendKeys("{Ctrl}{Shift}s", waitTime=0.12)
        elif name == "ctrl_s_untitled":
            self._ctrl_s_if_untitled(win)
        elif name == "menu_click":
            self._click_save_as_menu(win)

    def _ensure_editing(self, win) -> None:
        """Leave Win11 Notepad Settings / menus so the document editor is usable."""
        self._dismiss_chrome_ui(win)
        if self._is_settings_view(win):
            self._leave_settings(win)
            time.sleep(0.2)
            self._dismiss_chrome_ui(win)

    def _dismiss_chrome_ui(self, win) -> None:
        """Close flyouts/menus without navigating into Settings (avoid Alt chords)."""
        try:
            self._activate(win)
            auto.SendKeys("{Esc}", waitTime=0.05)
            time.sleep(0.05)
            auto.SendKeys("{Esc}", waitTime=0.05)
        except Exception:
            pass

    def _is_settings_view(self, win) -> bool:
        markers = (
            "应用主题",
            "App theme",
            "外观",
            "Appearance",
            "拼写检查",
            "Spelling",
            "打开文件时",
            "When Notepad starts",
        )
        for name in markers:
            try:
                ctrl = win.TextControl(searchDepth=16, Name=name)
                if ctrl.Exists(0.15, 0.05):
                    return True
            except Exception:
                continue
            try:
                ctrl = win.Control(searchDepth=16, Name=name)
                if ctrl.Exists(0.1, 0.05):
                    return True
            except Exception:
                continue
        # Settings page has no document editor.
        if self._find_editor(win) is None:
            for name in ("设置", "Settings"):
                try:
                    ctrl = win.TextControl(searchDepth=10, Name=name)
                    if ctrl.Exists(0.15, 0.05):
                        return True
                except Exception:
                    continue
        return False

    def _leave_settings(self, win) -> None:
        self._activate(win)
        # Title-bar back arrow (WinUI).
        for name in ("Back", "返回", "Navigate back", "上一步"):
            try:
                btn = win.ButtonControl(searchDepth=12, Name=name)
                if btn.Exists(0.25, 0.05):
                    self._invoke_or_click(btn)
                    time.sleep(0.25)
                    if not self._is_settings_view(win):
                        return
            except Exception:
                continue
        # Fallback: Escape / Alt+Left (do NOT use Ctrl+, which opens Settings).
        for keys in ("{Esc}", "{Alt}{Left}"):
            try:
                auto.SendKeys(keys, waitTime=0.1)
                time.sleep(0.2)
                if not self._is_settings_view(win):
                    return
            except Exception:
                continue
        # Still on Settings — close the window (discard) rather than strand the agent.
        try:
            self._hwnd = int(win.NativeWindowHandle or 0) or self._hwnd
            self._pid = int(win.ProcessId)
        except Exception:
            pass
        self.close(discard=True)

    def close_if_settings_vlm(self, vlm, *, min_confidence: float = 0.55) -> ActionResult:
        """Screenshot + VLM: if this is Notepad Settings, close the window immediately."""
        from desktop_agent.perception.capture import capture_screen

        wins = self._list_notepad_windows()
        if not wins:
            return ActionResult(
                action="notepad_close_if_settings_vlm",
                ok=True,
                detail={"closed": False, "reason": "no_notepad"},
            )

        win = wins[0]
        self._hwnd = int(win.NativeWindowHandle or 0)
        self._pid = int(win.ProcessId)
        self._activate(win)
        rect = window_rect(self._hwnd) if self._hwnd else None
        shot = Path(tempfile.gettempdir()) / "desktop-agent-notepad-vlm.png"
        bounds = None
        if rect:
            x, y, r, b = rect
            bounds = (x, y, max(1, r - x), max(1, b - y))
        cap = capture_screen(shot, scope="foreground" if bounds else "full", bounds=bounds)
        verdict = vlm.classify_page(
            cap.path,
            "Is this the Windows Notepad Settings page (设置), "
            "e.g. showing 外观/应用主题 or Appearance/App theme? "
            "Answer match=true only for Notepad Settings, not the editor.",
        )
        if not verdict.get("match") or float(verdict.get("confidence") or 0) < min_confidence:
            return ActionResult(
                action="notepad_close_if_settings_vlm",
                ok=True,
                detail={"closed": False, "vlm": verdict, "screenshot": cap.path},
            )
        closed = self.close(discard=True)
        return ActionResult(
            action="notepad_close_if_settings_vlm",
            ok=True,
            detail={
                "closed": True,
                "vlm": verdict,
                "screenshot": cap.path,
                "close": closed.detail,
            },
        )

    def read_editor_text(self) -> str:
        win = self._target_window()
        if win is None:
            raise AdapterUnavailable("Notepad window not found")
        edit = self._find_editor(win)
        if edit is None:
            raise ElementNotFound("Notepad editor control not found")
        try:
            return str(edit.GetValuePattern().Value)
        except Exception:
            return str(edit.Name or "")

    def close(self, discard: bool = True) -> ActionResult:
        win = self._target_window()
        if win is None:
            return ActionResult(action="notepad_close", ok=True, detail={"already_closed": True})
        self._activate(win)
        auto.SendKeys("{Alt}{F4}", waitTime=0.1)
        time.sleep(0.35)
        if discard:
            for name in ("Don't Save", "不保存", "Discard"):
                try:
                    btn = auto.ButtonControl(searchDepth=12, Name=name)
                    if btn.Exists(0.4, 0.1):
                        self._invoke_or_click(btn)
                        break
                except Exception:
                    continue
        self._hwnd = None
        return ActionResult(action="notepad_close", ok=True)

    def _ctrl_s_if_untitled(self, win=None):
        win = win or self._target_window()
        if win and self._is_untitled(str(win.Name or "")):
            auto.SendKeys("{Ctrl}s", waitTime=0.12)

    def _click_save_as_menu(self, win=None):
        win = win or self._target_window()
        if win is None:
            return
        for name in ("另存为...", "另存为", "Save As", "Save as"):
            try:
                item = win.MenuItemControl(searchDepth=18, Name=name)
                if item.Exists(0.25, 0.05):
                    self._invoke_or_click(item)
                    return
            except Exception:
                continue

    def _target_window(self):
        if self._hwnd:
            win = self._control_by_hwnd(self._hwnd)
            if win is not None:
                if self._is_settings_view(win):
                    self._leave_settings(win)
                return win
        # Fallback for interrupted sessions — skip Settings pages.
        for w in self._list_notepad_windows():
            if self._is_settings_view(w):
                continue
            if self._is_untitled(str(w.Name or "")):
                self._hwnd = int(w.NativeWindowHandle or 0)
                self._pid = int(w.ProcessId)
                return w
        for w in self._list_notepad_windows():
            if not self._is_settings_view(w):
                self._hwnd = int(w.NativeWindowHandle or 0)
                self._pid = int(w.ProcessId)
                return w
        wins = self._list_notepad_windows()
        return wins[0] if wins else None

    def _control_by_hwnd(self, hwnd: int):
        try:
            ctrl = auto.ControlFromHandle(int(hwnd))
            if ctrl:
                return ctrl
        except Exception:
            pass
        for w in self._list_notepad_windows():
            try:
                if int(w.NativeWindowHandle or 0) == int(hwnd):
                    return w
            except Exception:
                continue
        return None

    def _list_notepad_windows(self):
        found = []
        root = auto.GetRootControl()
        for win in root.GetChildren():
            try:
                title = str(win.Name or "")
                if not title:
                    continue
                # Skip zero-size / hidden junk windows
                rect = win.BoundingRectangle
                if rect and (rect.right - rect.left) <= 0:
                    continue
                if "记事本" in title or "Notepad" in title:
                    found.append(win)
                    continue
                if self._process_basename(int(win.ProcessId)).lower() == "notepad.exe":
                    found.append(win)
            except Exception:
                continue
        return found

    def _hwnd_set(self) -> set[int]:
        out: set[int] = set()
        for w in self._list_notepad_windows():
            try:
                hwnd = int(w.NativeWindowHandle or 0)
                if hwnd:
                    out.add(hwnd)
            except Exception:
                continue
        return out

    def _find_editor(self, win):
        for getter in (
            lambda: win.DocumentControl(searchDepth=14),
            lambda: win.EditControl(searchDepth=14),
        ):
            try:
                ctrl = getter()
                if ctrl and ctrl.Exists(0.25, 0.05):
                    return ctrl
            except Exception:
                continue
        stack = [win]
        while stack:
            cur = stack.pop()
            try:
                if type(cur).__name__ in {"DocumentControl", "EditControl"}:
                    return cur
                stack.extend(reversed(cur.GetChildren()))
            except Exception:
                continue
        return None

    def _activate(self, win) -> None:
        try:
            hwnd = int(win.NativeWindowHandle or 0) or self._hwnd or 0
            if hwnd:
                force_foreground(hwnd)
        except Exception:
            pass
        try:
            if hasattr(win, "SetActive"):
                win.SetActive()
        except Exception:
            pass
        try:
            win.SetFocus()
        except Exception:
            pass

    @staticmethod
    def _invoke_or_click(ctrl) -> None:
        try:
            ctrl.GetInvokePattern().Invoke()
            return
        except Exception:
            pass
        # Last resort; Click uses element center from UIA bounds (virtual-screen aware).
        ctrl.Click(simulateMove=False)

    @staticmethod
    def _is_untitled(title: str) -> bool:
        t = title.lower()
        return (
            "untitled" in t
            or "无标题" in title
            or title.strip() in {"记事本", "Notepad"}
            or title.startswith("*无标题")
            or title.startswith("*Untitled")
        )

    @staticmethod
    def _escape(text: str) -> str:
        out: list[str] = []
        for ch in text:
            if ch in "{}+^%~()":
                out.append("{" + ch + "}")
            else:
                out.append(ch)
        return "".join(out)

    @staticmethod
    def _process_basename(pid: int) -> str:
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return ""
            try:
                buf = ctypes.create_unicode_buffer(260)
                size = wintypes.DWORD(len(buf))
                fn = kernel32.QueryFullProcessImageNameW
                fn.argtypes = [
                    wintypes.HANDLE,
                    wintypes.DWORD,
                    wintypes.LPWSTR,
                    ctypes.POINTER(wintypes.DWORD),
                ]
                fn.restype = wintypes.BOOL
                if fn(handle, 0, buf, ctypes.byref(size)):
                    return buf.value.rsplit("\\", 1)[-1]
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return ""
        return ""
