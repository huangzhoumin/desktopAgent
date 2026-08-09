"""Generic Windows common-file-dialog helpers (Save As / Open)."""

from __future__ import annotations

import time
from pathlib import Path

import uiautomation as auto

from desktop_agent.common.win32_window import force_foreground
from desktop_agent.errors import ActionRejected, ElementNotFound
from desktop_agent.models import ActionResult


class FileDialogHelper:
    """Fill and confirm a native Save As / Open file dialog via UIA."""

    SAVE_KEYWORDS = ("另存为", "Save As")
    SAVE_SOFT = ("保存", "Save")
    OPEN_KEYWORDS = ("打开", "Open")

    def wait_dialog(
        self,
        *,
        owner=None,
        owner_hwnd: int | None = None,
        owner_pid: int | None = None,
        keywords: tuple[str, ...] | None = None,
        soft_keywords: tuple[str, ...] | None = None,
        timeout_s: float = 5.0,
    ):
        keywords = keywords or self.SAVE_KEYWORDS
        soft_keywords = soft_keywords if soft_keywords is not None else self.SAVE_SOFT
        deadline = time.time() + timeout_s

        while time.time() < deadline:
            # Prefer children of the owner window (Win11 common dialogs).
            owners = []
            if owner is not None:
                owners.append(owner)
            elif owner_hwnd:
                try:
                    ctrl = auto.ControlFromHandle(int(owner_hwnd))
                    if ctrl is not None:
                        owners.append(ctrl)
                except Exception:
                    pass
            for own in owners:
                hit = self._scan_children(own, keywords, soft_keywords)
                if hit is not None:
                    return hit

            # Top-level dialogs (optionally filtered by process).
            try:
                root = auto.GetRootControl()
                for top in root.GetChildren():
                    try:
                        top_hwnd = int(top.NativeWindowHandle or 0)
                        if owner_hwnd and top_hwnd == int(owner_hwnd):
                            continue
                        if owner_pid is not None and int(top.ProcessId) != int(owner_pid):
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

    def fill_and_confirm(self, dialog, path: str | Path, *, confirm_names: tuple[str, ...] = ("保存", "Save")) -> None:
        path = Path(path)
        full = str(path.resolve()) if path.is_absolute() else str(path)
        try:
            hwnd = int(dialog.NativeWindowHandle or 0)
            if hwnd:
                force_foreground(hwnd)
        except Exception:
            pass

        edit = self.find_filename_edit(dialog)
        if edit is None:
            raise ElementNotFound("File dialog filename edit box not found")

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
        time.sleep(0.15)

        for name in confirm_names:
            try:
                btn = dialog.ButtonControl(searchDepth=18, Name=name)
                if btn.Exists(0.35, 0.05):
                    self._invoke_or_click(btn)
                    time.sleep(0.4)
                    return
            except Exception:
                continue
        auto.SendKeys("{Enter}", waitTime=0.1)
        time.sleep(0.4)

    def save_as(
        self,
        path: str | Path,
        *,
        owner=None,
        owner_hwnd: int | None = None,
        owner_pid: int | None = None,
        open_strategies: list | None = None,
        timeout_s: float = 5.0,
        wait_file_s: float = 5.0,
    ) -> ActionResult:
        """Run optional open strategies, fill Save As dialog, wait for file."""
        path = Path(path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()

        if owner is not None and owner_hwnd is None:
            try:
                owner_hwnd = int(owner.NativeWindowHandle or 0) or None
            except Exception:
                owner_hwnd = None
        if owner is not None and owner_pid is None:
            try:
                owner_pid = int(owner.ProcessId)
            except Exception:
                owner_pid = None

        if open_strategies:
            last_err: Exception | None = None
            for fn in open_strategies:
                try:
                    fn()
                except Exception:
                    continue
                dialog = self.wait_dialog(
                    owner=owner,
                    owner_hwnd=owner_hwnd,
                    owner_pid=owner_pid,
                    timeout_s=min(2.8, timeout_s),
                )
                if dialog is None:
                    continue
                try:
                    self.fill_and_confirm(dialog, path)
                    self.dismiss_confirm_yes(timeout_s=2.5)
                    self._wait_file(path, wait_file_s)
                    return ActionResult(
                        action="dialog_save_as",
                        ok=True,
                        detail={"path": str(path), "bytes": path.stat().st_size},
                    )
                except Exception as e:
                    last_err = e
                    continue
            if last_err is not None:
                raise last_err
            raise ActionRejected("Save As dialog did not appear")

        dialog = self.wait_dialog(
            owner=owner,
            owner_hwnd=owner_hwnd,
            owner_pid=owner_pid,
            timeout_s=timeout_s,
        )
        if dialog is None:
            raise ActionRejected("Save As dialog did not appear")
        self.fill_and_confirm(dialog, path)
        self.dismiss_confirm_yes(timeout_s=2.5)
        self._wait_file(path, wait_file_s)
        return ActionResult(
            action="dialog_save_as",
            ok=True,
            detail={"path": str(path), "bytes": path.stat().st_size},
        )

    def find_filename_edit(self, dialog):
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

    def dismiss_confirm_yes(self, timeout_s: float = 2.0) -> None:
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

    def _scan_children(self, owner, keywords, soft_keywords):
        try:
            for child in owner.GetChildren():
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
        return None

    def _match_dialog(self, ctrl, keywords, require_filename_edit: bool = False):
        try:
            title = str(getattr(ctrl, "Name", "") or "")
            if not title or not any(k in title for k in keywords):
                return None
            if require_filename_edit and self.find_filename_edit(ctrl) is None:
                return None
            return ctrl
        except Exception:
            return None

    def _wait_file(self, path: Path, timeout_s: float) -> None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if path.exists():
                return
            time.sleep(0.2)
        if not path.exists():
            raise ActionRejected(f"File not created: {path}")

    @staticmethod
    def _invoke_or_click(ctrl) -> None:
        try:
            ctrl.GetInvokePattern().Invoke()
            return
        except Exception:
            pass
        try:
            ctrl.Click(simulateMove=False)
        except Exception:
            rect = ctrl.BoundingRectangle
            auto.Click(rect.xcenter(), rect.ycenter())

    @staticmethod
    def _escape(text: str) -> str:
        out: list[str] = []
        for ch in text:
            if ch in "{}+^%~()":
                out.append("{" + ch + "}")
            else:
                out.append(ch)
        return "".join(out)
