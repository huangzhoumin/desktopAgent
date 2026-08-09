from __future__ import annotations

import subprocess
import time
from pathlib import Path

import uiautomation as auto

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

    def type_text(self, text: str, clear: bool = True) -> ActionResult:
        win = self._target_window()
        if win is None:
            raise AdapterUnavailable("Notepad window not found")
        self._activate(win)
        edit = self._find_editor(win)
        if edit is None:
            raise ElementNotFound("Notepad editor control not found")
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
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()

        win = self._target_window()
        if win is None:
            raise AdapterUnavailable("Notepad window not found")
        self._activate(win)
        time.sleep(0.12)

        dialog = self._open_save_as_dialog(win)
        if dialog is None:
            raise ActionRejected("Save As dialog did not appear")

        # Keep dialog interaction on the same HWND tree; avoid cross-screen mouse targets.
        self._fill_save_dialog(dialog, path)
        time.sleep(0.4)
        self._dismiss_confirm_yes(owner_hwnd=self._hwnd, timeout_s=2.5)

        for _ in range(25):
            if path.exists():
                break
            time.sleep(0.2)
        if not path.exists():
            raise ActionRejected(f"File not created: {path}")

        content = path.read_text(encoding="utf-8", errors="replace")
        return ActionResult(
            action="notepad_save_as",
            ok=True,
            detail={
                "path": str(path),
                "bytes": path.stat().st_size,
                "preview": content[:120],
                "hwnd": self._hwnd,
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

    def _open_save_as_dialog(self, win):
        strategies = (
            ("ctrl_shift_s", lambda: auto.SendKeys("{Ctrl}{Shift}s", waitTime=0.12)),
            ("ctrl_s_untitled", self._ctrl_s_if_untitled),
            ("alt_f_a", self._alt_file_save_as),
            ("menu_click", self._click_save_as_menu),
        )
        for name, fn in strategies:
            self._activate(win)
            time.sleep(0.05)
            try:
                fn(win)
            except TypeError:
                fn()
            except Exception:
                continue
            dialog = self._wait_save_dialog(win, timeout_s=2.8)
            if dialog is not None:
                dialog._strategy = name  # type: ignore[attr-defined]
                return dialog
        return None

    def _ctrl_s_if_untitled(self, win=None):
        win = win or self._target_window()
        if win and self._is_untitled(str(win.Name or "")):
            auto.SendKeys("{Ctrl}s", waitTime=0.12)

    def _alt_file_save_as(self, win=None):
        auto.SendKeys("{Alt}", waitTime=0.08)
        time.sleep(0.12)
        auto.SendKeys("f", waitTime=0.08)
        time.sleep(0.25)
        auto.SendKeys("a", waitTime=0.08)

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

    def _wait_save_dialog(self, notepad_win, timeout_s: float = 5.0):
        deadline = time.time() + timeout_s
        keywords = ("另存为", "Save As")
        soft_keywords = ("保存", "Save")
        owner_hwnd = int(notepad_win.NativeWindowHandle or 0)
        owner_pid = int(notepad_win.ProcessId)

        while time.time() < deadline:
            # 1) Child windows of our specific notepad HWND (common on Win11)
            try:
                for child in notepad_win.GetChildren():
                    hit = self._match_dialog(child, keywords) or self._match_dialog(
                        child, soft_keywords, require_filename_edit=True
                    )
                    if hit is not None:
                        return hit
                    try:
                        for nested in child.GetChildren():
                            hit = self._match_dialog(nested, keywords) or self._match_dialog(
                                nested, soft_keywords, require_filename_edit=True
                            )
                            if hit is not None:
                                return hit
                    except Exception:
                        pass
            except Exception:
                pass

            # 2) Top-level dialogs belonging to the same process (may open on either monitor)
            try:
                root = auto.GetRootControl()
                for top in root.GetChildren():
                    try:
                        if int(top.ProcessId) != owner_pid:
                            continue
                        top_hwnd = int(top.NativeWindowHandle or 0)
                        if top_hwnd == owner_hwnd:
                            continue
                        hit = self._match_dialog(top, keywords) or self._match_dialog(
                            top, soft_keywords, require_filename_edit=True
                        )
                        if hit is not None:
                            return hit
                    except Exception:
                        continue
            except Exception:
                pass
            time.sleep(0.12)
        return None

    def _match_dialog(self, ctrl, keywords, require_filename_edit: bool = False):
        try:
            title = str(getattr(ctrl, "Name", "") or "")
            if not title or not any(k in title for k in keywords):
                return None
            if require_filename_edit and self._find_filename_edit(ctrl) is None:
                return None
            return ctrl
        except Exception:
            return None

    def _fill_save_dialog(self, dialog, path: Path) -> None:
        full = str(path)
        try:
            hwnd = int(dialog.NativeWindowHandle or 0)
            if hwnd:
                force_foreground(hwnd)
        except Exception:
            pass

        edit = self._find_filename_edit(dialog)
        if edit is None:
            raise ElementNotFound("Save dialog filename edit box not found")

        try:
            edit.SetFocus()
        except Exception:
            pass
        time.sleep(0.08)
        try:
            edit.GetValuePattern().SetValue(full)
        except Exception:
            auto.SendKeys("{Ctrl}a", waitTime=0.05)
            auto.SendKeys(self._escape(full), waitTime=0.02)
        time.sleep(0.12)

        for name in ("保存", "Save"):
            try:
                btn = dialog.ButtonControl(searchDepth=18, Name=name)
                if btn.Exists(0.35, 0.05):
                    self._invoke_or_click(btn)
                    return
            except Exception:
                continue
        auto.SendKeys("{Enter}", waitTime=0.1)

    def _find_filename_edit(self, dialog):
        candidates = [
            {"AutomationId": "1001"},
            {"AutomationId": "FileNameControlHost"},
            {"Name": "文件名:"},
            {"Name": "File name:"},
            {"Name": "文件名"},
        ]
        for kwargs in candidates:
            try:
                cand = dialog.EditControl(searchDepth=22, **kwargs)
                if cand.Exists(0.25, 0.05):
                    return cand
            except Exception:
                continue

        edits = []
        stack = [dialog]
        seen = 0
        while stack and seen < 400:
            cur = stack.pop()
            seen += 1
            try:
                if type(cur).__name__ == "EditControl":
                    edits.append(cur)
                stack.extend(cur.GetChildren())
            except Exception:
                continue
        return edits[-1] if edits else None

    def _dismiss_confirm_yes(self, owner_hwnd: int | None, timeout_s: float = 2.0) -> None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            for name in ("是", "Yes", "确认", "OK"):
                try:
                    btn = auto.ButtonControl(searchDepth=14, Name=name)
                    if btn.Exists(0.2, 0.05):
                        self._invoke_or_click(btn)
                        return
                except Exception:
                    continue
            time.sleep(0.1)

    def _target_window(self):
        if self._hwnd:
            win = self._control_by_hwnd(self._hwnd)
            if win is not None:
                return win
        # Fallback for interrupted sessions
        for w in self._list_notepad_windows():
            if self._is_untitled(str(w.Name or "")):
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
