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
    CONFIRM_SAVE_NAMES = (
        "保存(&S)",
        "保存(S)",
        "保存",
        "Save (&S)",
        "&Save",
        "Save",
    )

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
                        # Do not skip owner_hwnd when the foreground window IS the
                        # Save As dialog (common once Ctrl+Shift+S has opened it).
                        hit = self._match_dialog(top, keywords) or self._match_dialog(
                            top, soft_keywords, require_filename_edit=True
                        )
                        if hit is None:
                            continue
                        if owner_hwnd and top_hwnd == int(owner_hwnd):
                            return hit
                        if owner_pid is not None and int(top.ProcessId) != int(owner_pid):
                            # Still accept clearly titled Save As dialogs from any pid.
                            if self._match_dialog(top, keywords) is None:
                                continue
                        return hit
                    except Exception:
                        continue
            except Exception:
                pass
            time.sleep(0.12)
        return None

    def fill_and_confirm(
        self,
        dialog,
        path: str | Path,
        *,
        confirm_names: tuple[str, ...] | None = None,
    ) -> None:
        path = Path(path)
        full = str(path.resolve()) if path.is_absolute() else str(path)
        confirm_names = confirm_names or self.CONFIRM_SAVE_NAMES
        try:
            hwnd = int(dialog.NativeWindowHandle or 0)
            if hwnd:
                force_foreground(hwnd)
        except Exception:
            pass

        edit = self.find_filename_edit(dialog)
        if edit is None:
            raise ElementNotFound("File dialog filename edit box not found")

        self._set_filename(edit, full)
        time.sleep(0.2)

        if not self._click_confirm(dialog, confirm_names):
            # Enter in the filename field usually confirms Save As.
            try:
                edit.SetFocus()
            except Exception:
                pass
            auto.SendKeys("{Enter}", waitTime=0.12)
            time.sleep(0.45)

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
                    self._complete_save(dialog, path, wait_file_s=wait_file_s)
                    return ActionResult(
                        action="dialog_save_as",
                        ok=True,
                        detail={"path": str(path), "bytes": path.stat().st_size},
                    )
                except Exception as e:
                    last_err = e
                    self._dismiss_open_dialog(dialog)
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
        self._complete_save(dialog, path, wait_file_s=wait_file_s)
        return ActionResult(
            action="dialog_save_as",
            ok=True,
            detail={"path": str(path), "bytes": path.stat().st_size},
        )

    def _complete_save(self, dialog, path: Path, *, wait_file_s: float) -> None:
        """Fill, confirm, dismiss overwrite prompts, and require the file on disk."""
        self.fill_and_confirm(dialog, path)
        self.dismiss_confirm_yes(timeout_s=2.5)
        if self._wait_file(path, min(2.0, wait_file_s)):
            return

        # Retry confirms: Win11 dialogs often label the button "保存(&S)", and the
        # first Invoke can no-op if the filename ComboBox value was not committed.
        for keys in ("{Alt}s", "{Enter}"):
            try:
                auto.SendKeys(keys, waitTime=0.12)
            except Exception:
                continue
            self.dismiss_confirm_yes(timeout_s=1.5)
            if self._wait_file(path, 2.0):
                return

        # One more fill+confirm pass against whatever dialog is still up.
        still = self.wait_dialog(timeout_s=1.2)
        if still is not None:
            self.fill_and_confirm(still, path)
            self.dismiss_confirm_yes(timeout_s=2.0)
            if self._wait_file(path, max(2.0, wait_file_s - 2.0)):
                return

        if not path.exists():
            raise ActionRejected(f"File not created: {path}")

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
            # Win11 common dialogs often expose the filename as a ComboBox host.
            try:
                host = dialog.ComboBoxControl(searchDepth=22, **kwargs)
                if host.Exists(0.25, 0.05):
                    edit = self._first_edit_descendant(host)
                    if edit is not None:
                        return edit
                    return host
            except Exception:
                continue

        edits = []
        combos = []
        stack = [dialog]
        seen = 0
        while stack and seen < 400:
            cur = stack.pop()
            seen += 1
            try:
                kind = type(cur).__name__
                if kind == "EditControl":
                    edits.append(cur)
                elif kind == "ComboBoxControl":
                    combos.append(cur)
                stack.extend(cur.GetChildren())
            except Exception:
                continue
        if edits:
            return edits[-1]
        if combos:
            nested = self._first_edit_descendant(combos[-1])
            return nested or combos[-1]
        return None

    def dismiss_confirm_yes(self, timeout_s: float = 2.0) -> None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            for name in ("是(&Y)", "是(Y)", "是", "Yes", "&Yes", "确认", "OK"):
                try:
                    btn = auto.ButtonControl(searchDepth=14, Name=name)
                    if btn.Exists(0.2, 0.05):
                        self._invoke_or_click(btn)
                        return
                except Exception:
                    continue
            # Partial name match for localized overwrite prompts.
            try:
                root = auto.GetRootControl()
                for top in root.GetChildren():
                    title = str(getattr(top, "Name", "") or "")
                    if not any(k in title for k in ("确认", "Confirm", "替换", "Replace", "已存在")):
                        continue
                    for btn in self._iter_buttons(top, limit=30):
                        label = str(getattr(btn, "Name", "") or "")
                        if any(k in label for k in ("是", "Yes", "确定", "OK")):
                            self._invoke_or_click(btn)
                            return
            except Exception:
                pass
            time.sleep(0.1)

    def _set_filename(self, edit, full: str) -> None:
        try:
            edit.SetFocus()
        except Exception:
            pass
        time.sleep(0.08)

        set_ok = False
        try:
            edit.GetValuePattern().SetValue(full)
            set_ok = True
        except Exception:
            pass

        # Commit ComboBox / Edit value; re-read and fall back to SendKeys if needed.
        current = self._read_value(edit)
        if not set_ok or not self._path_value_matches(current, full):
            auto.SendKeys("{Ctrl}a", waitTime=0.05)
            auto.SendKeys(self._escape(full), waitTime=0.02)
            time.sleep(0.1)
            current = self._read_value(edit)
            if current and not self._path_value_matches(current, full):
                # Last resort: retype once more.
                auto.SendKeys("{Ctrl}a", waitTime=0.05)
                auto.SendKeys(self._escape(full), waitTime=0.02)

    def _click_confirm(self, dialog, confirm_names: tuple[str, ...]) -> bool:
        # Exact names first (includes 保存(&S) variants).
        for name in confirm_names:
            try:
                btn = dialog.ButtonControl(searchDepth=18, Name=name)
                if btn.Exists(0.35, 0.05):
                    self._invoke_or_click(btn)
                    time.sleep(0.45)
                    return True
            except Exception:
                continue

        # Fuzzy: any button whose name contains Save/保存 but is not Cancel.
        for btn in self._iter_buttons(dialog, limit=80):
            label = str(getattr(btn, "Name", "") or "")
            low = label.lower()
            if any(bad in label for bad in ("取消", "Cancel", "不保存", "Don't")):
                continue
            if "保存" in label or low.strip() in {"save", "&save"} or "save" == low.replace("&", "").strip():
                try:
                    self._invoke_or_click(btn)
                    time.sleep(0.45)
                    return True
                except Exception:
                    continue
        return False

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

    def _wait_file(self, path: Path, timeout_s: float) -> bool:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if path.exists() and path.stat().st_size >= 0:
                # Require non-empty for text saves when possible; zero-byte is still "created".
                return True
            time.sleep(0.2)
        return path.exists()

    def _dismiss_open_dialog(self, dialog) -> None:
        try:
            auto.SendKeys("{Esc}", waitTime=0.1)
            time.sleep(0.2)
        except Exception:
            pass
        for name in ("取消", "Cancel"):
            try:
                btn = dialog.ButtonControl(searchDepth=12, Name=name)
                if btn.Exists(0.2, 0.05):
                    self._invoke_or_click(btn)
                    return
            except Exception:
                continue

    @staticmethod
    def _first_edit_descendant(ctrl):
        stack = list(reversed(list(ctrl.GetChildren()) if hasattr(ctrl, "GetChildren") else []))
        seen = 0
        while stack and seen < 80:
            cur = stack.pop()
            seen += 1
            try:
                if type(cur).__name__ == "EditControl":
                    return cur
                stack.extend(reversed(list(cur.GetChildren())))
            except Exception:
                continue
        return None

    @staticmethod
    def _iter_buttons(root, limit: int = 80):
        stack = [root]
        seen = 0
        while stack and seen < limit:
            cur = stack.pop()
            seen += 1
            try:
                if type(cur).__name__ == "ButtonControl":
                    yield cur
                stack.extend(cur.GetChildren())
            except Exception:
                continue

    @staticmethod
    def _read_value(edit) -> str:
        try:
            return str(edit.GetValuePattern().Value or "")
        except Exception:
            try:
                return str(edit.Name or "")
            except Exception:
                return ""

    @staticmethod
    def _path_value_matches(current: str, expected: str) -> bool:
        if not current:
            return False
        cur = current.strip().strip('"').lower().replace("/", "\\")
        exp = expected.strip().strip('"').lower().replace("/", "\\")
        if cur == exp:
            return True
        # Dialog may show only the file name after navigating to the folder.
        # Use explicit backslash split so Linux unit tests can still validate
        # Windows-style paths.
        cur_name = cur.rstrip("\\").rsplit("\\", 1)[-1]
        exp_name = exp.rstrip("\\").rsplit("\\", 1)[-1]
        return bool(cur_name) and cur_name == exp_name

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
