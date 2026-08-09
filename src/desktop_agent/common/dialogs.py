"""Generic Windows common-file-dialog helpers (Save As / Open) + shell prompts."""

from __future__ import annotations

import time
from pathlib import Path

import uiautomation as auto

from desktop_agent.common.win32_window import force_foreground
from desktop_agent.errors import ActionRejected, ElementNotFound
from desktop_agent.models import ActionResult

# Office / shell message-box button aliases
AFFIRMATIVE_BUTTONS = (
    "是(&Y)",
    "是(Y)",
    "是",
    "Yes",
    "&Yes",
    "确定",
    "OK",
    "保存(&S)",
    "保存(S)",
    "保存",
    "Save",
    "&Save",
    "重试",
    "Retry",
    "打开",
    "Open",
)
NEGATIVE_BUTTONS = (
    "不保存(&N)",
    "不保存(N)",
    "不保存",
    "Don't Save",
    "Don\u2019t Save",
    "&Don't Save",
    "否(&N)",
    "否(N)",
    "否",
    "No",
    "&No",
    "取消",
    "Cancel",
)
OFFICE_PROMPT_TITLE_HINTS = (
    "Microsoft Excel",
    "Microsoft Word",
    "Excel",
    "Word",
    "WPS",
    "提示",
    "Warning",
    "Confirm",
    "确认",
    "另存为",
    "Save As",
    "保存对此文件所做的更改",
    "Save changes to",
    "要保存",
)
MORE_OPTIONS_NAMES = (
    "更多选项…",  # Excel uses unicode ellipsis
    "更多选项...",
    "更多选项",
    "More options…",
    "More options...",
    "More options",
)
DISCARD_BUTTON_NAMES = (
    "不保存(&N)",
    "不保存(N)",
    "不保存",
    "Don't Save",
    "Don\u2019t Save",
    "&Don't Save",
    "否(&N)",
    "否(N)",
    "否",
    "No",
    "&No",
)
SAVE_PROMPT_BUTTON_NAMES = (
    "保存(&S)",
    "保存(S)",
    "保存",
    "Save",
    "&Save",
    "是(&Y)",
    "是(Y)",
    "是",
    "Yes",
    "&Yes",
)


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
    FILENAME_LABELS = ("文件名:", "文件名：", "File name:", "File name")

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

    def click_button(
        self,
        *,
        names: tuple[str, ...] | list[str] | None = None,
        title_contains: str | None = None,
        timeout_s: float = 3.0,
        optional: bool = False,
    ) -> ActionResult:
        """Click a shell / Office message-box or dialog button by name."""
        names = tuple(names or AFFIRMATIVE_BUTTONS)
        deadline = time.time() + timeout_s
        last_seen = ""
        while time.time() < deadline:
            hosts = self._iter_prompt_hosts(title_contains=title_contains)
            for host in hosts:
                try:
                    last_seen = str(getattr(host, "Name", "") or "")
                except Exception:
                    last_seen = ""
                for name in names:
                    btn = self._find_named_button(host, name, depth=18)
                    if btn is not None:
                        self._invoke_or_click(btn)
                        return ActionResult(
                            action="dialog_click_button",
                            ok=True,
                            detail={"button": name, "dialog_title": last_seen},
                        )
            # Global fallback (some prompts are top-level without useful parent).
            for name in names:
                try:
                    btn = auto.ButtonControl(searchDepth=14, Name=name)
                    if btn.Exists(0.15, 0.05):
                        self._invoke_or_click(btn)
                        return ActionResult(
                            action="dialog_click_button",
                            ok=True,
                            detail={"button": name, "dialog_title": last_seen or None},
                        )
                except Exception:
                    continue
            time.sleep(0.12)
        if optional:
            return ActionResult(
                action="dialog_click_button",
                ok=True,
                detail={"skipped": True, "reason": "no matching button"},
            )
        raise ActionRejected(
            f"Dialog button not found among {names}"
            + (f" (last dialog={last_seen!r})" if last_seen else "")
        )

    def find_prompt_dialog(self, title_contains: str | None = None):
        """Find a likely Office / shell save prompt (top-level or nested in app)."""
        for host in self._iter_prompt_hosts(title_contains=title_contains):
            if self._host_has_save_prompt(host):
                return host
        return None

    def handle_office_prompt(
        self,
        *,
        action: str = "yes",
        timeout_s: float = 3.0,
        title_contains: str | None = None,
        path: str | Path | None = None,
    ) -> ActionResult:
        """Dismiss common Office prompts (save/overwrite/protected view).

        When action is save/yes and ``path`` is set, prefer the modern Excel
        "More options..." route into a classic Save As filled with that local path
        (avoids OneDrive default location).
        """
        action_l = (action or "yes").strip().lower()
        if (
            path
            and action_l in {"yes", "y", "ok", "save", "affirm", "是", "确定", "保存"}
        ):
            return self.save_office_prompt_local(
                path,
                timeout_s=timeout_s,
                title_contains=title_contains,
            )
        if action_l in {"yes", "y", "ok", "save", "affirm", "是", "确定", "保存"}:
            names = AFFIRMATIVE_BUTTONS
        elif action_l in {"no", "n", "discard", "don't save", "dont_save", "否", "不保存"}:
            names = NEGATIVE_BUTTONS
        elif action_l in {"cancel", "取消"}:
            names = ("取消", "Cancel")
        else:
            names = (action,)
        return self.click_button(
            names=names, title_contains=title_contains, timeout_s=timeout_s
        )

    def save_office_prompt_local(
        self,
        path: str | Path,
        *,
        timeout_s: float = 8.0,
        title_contains: str | None = None,
    ) -> ActionResult:
        """Save from Office close/save prompt to a local path via More options.

        Excel's modern flyout defaults to OneDrive. Clicking More options opens a
        classic common-file dialog *embedded in the Excel window* (no separate
        top-level 另存为 title), so we fill that host directly.
        """
        out = Path(path).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists():
            try:
                out.unlink()
            except OSError:
                pass

        deadline = time.time() + max(3.0, float(timeout_s))
        opened_more = False
        prompt_host = None
        while time.time() < deadline:
            host = self.find_prompt_dialog(title_contains=title_contains)
            if host is None and title_contains:
                host = self.find_prompt_dialog()
            if host is not None:
                prompt_host = host
                for name in MORE_OPTIONS_NAMES:
                    btn = self._find_named_button(host, name, depth=20)
                    if btn is not None:
                        self._invoke_or_click(btn)
                        opened_more = True
                        break
                if opened_more:
                    break
                link = self._find_named_control(host, MORE_OPTIONS_NAMES, depth=20)
                if link is not None:
                    self._invoke_or_click(link)
                    opened_more = True
                    break
            time.sleep(0.15)

        if not opened_more:
            raise ActionRejected(
                "Office prompt 'More options' not found; cannot force local Save As"
            )

        # Embedded Save As lives under the Excel/Word window, not as a titled dialog.
        embed = None
        embed_deadline = time.time() + max(4.0, float(timeout_s))
        while time.time() < embed_deadline:
            embed = self._find_embedded_save_as_host(prefer=prompt_host)
            if embed is not None:
                break
            # Top-level common dialog fallback (some builds detach it).
            embed = self.wait_dialog(timeout_s=0.35)
            if embed is not None:
                break
            time.sleep(0.12)

        if embed is None:
            raise ActionRejected(
                "Classic Save As (embedded) did not appear after More options"
            )

        self._complete_save(embed, out, wait_file_s=8.0)
        return ActionResult(
            action="dialog_click_button",
            ok=True,
            detail={
                "via": "more_options_embedded_save_as",
                "path": str(out),
                "bytes": out.stat().st_size if out.exists() else 0,
            },
        )

    def _find_embedded_save_as_host(self, prefer=None):
        """Find Excel/Word window that currently hosts a classic Save As UI."""
        candidates = []
        if prefer is not None:
            candidates.append(prefer)
        try:
            root = auto.GetRootControl()
            for top in root.GetChildren():
                title = str(getattr(top, "Name", "") or "")
                if (" - Excel" in title) or (" - Word" in title) or ("另存为" in title):
                    candidates.append(top)
        except Exception:
            pass

        for host in candidates:
            if self._looks_like_save_as_host(host):
                return host
        return None

    def _looks_like_save_as_host(self, host) -> bool:
        # Must expose a filename field labeled 文件名 / File name.
        has_filename = False
        for label in self.FILENAME_LABELS:
            try:
                edit = host.EditControl(searchDepth=20, Name=label)
                if edit.Exists(0.05, 0.02):
                    has_filename = True
                    break
            except Exception:
                pass
            try:
                combo = host.ComboBoxControl(searchDepth=20, Name=label)
                if combo.Exists(0.05, 0.02):
                    has_filename = True
                    break
            except Exception:
                pass
        if not has_filename:
            return False
        # And a Save confirm button (not just ribbon Save).
        for name in ("保存(S)", "保存(&S)", "保存", "Save", "&Save"):
            btn = self._find_named_button(host, name, depth=16)
            if btn is not None:
                return True
        return False

    def _iter_prompt_hosts(self, title_contains: str | None = None):
        """Yield top-level and nested hosts that may contain an Office save prompt."""
        hosts = []
        try:
            root = auto.GetRootControl()
            for top in root.GetChildren():
                try:
                    title = str(getattr(top, "Name", "") or "")
                    if not title:
                        continue
                    if title_contains and title_contains.lower() not in title.lower():
                        # Still allow nested search under main Excel/Word when filter is Excel.
                        main_app = (" - Excel" in title) or title.endswith(" - Word")
                        if not (
                            main_app
                            and title_contains
                            and title_contains.lower()
                            in ("excel", "word", "microsoft excel", "microsoft word")
                        ):
                            # Modern prompt title itself.
                            if not any(
                                h.lower() in title.lower()
                                for h in OFFICE_PROMPT_TITLE_HINTS
                            ):
                                continue
                    hint_ok = any(
                        h.lower() in title.lower() for h in OFFICE_PROMPT_TITLE_HINTS
                    ) or bool(title_contains and title_contains.lower() in title.lower())
                    main_app = (" - Excel" in title) or (" - Word" in title)
                    if hint_ok and not main_app:
                        hosts.append(top)
                    if main_app:
                        # Modern "Save changes?" UI is often nested under the app window.
                        hosts.append(top)
                        try:
                            for child in top.GetChildren():
                                hosts.append(child)
                        except Exception:
                            pass
                except Exception:
                    continue
        except Exception:
            pass
        return hosts

    def _host_has_save_prompt(self, host) -> bool:
        has_discard = any(
            self._find_named_button(host, name, depth=14) is not None
            for name in DISCARD_BUTTON_NAMES
        )
        has_save = any(
            self._find_named_button(host, name, depth=14) is not None
            for name in SAVE_PROMPT_BUTTON_NAMES
        )
        return has_discard and has_save

    def _find_named_button(self, host, name: str, *, depth: int = 14):
        try:
            btn = host.ButtonControl(searchDepth=depth, Name=name)
            if btn.Exists(0.05, 0.02):
                return btn
        except Exception:
            pass
        # Partial match for localized accelerator variants.
        try:
            for btn in self._iter_buttons(host, limit=60):
                label = str(getattr(btn, "Name", "") or "")
                if label == name or name in label:
                    return btn
        except Exception:
            pass
        return None

    def _find_named_control(self, host, names: tuple[str, ...] | list[str], *, depth: int = 14):
        for name in names:
            for factory in (
                lambda n=name: host.HyperlinkControl(searchDepth=depth, Name=n),
                lambda n=name: host.TextControl(searchDepth=depth, Name=n),
                lambda n=name: host.ButtonControl(searchDepth=depth, Name=n),
            ):
                try:
                    ctrl = factory()
                    if ctrl.Exists(0.05, 0.02):
                        return ctrl
                except Exception:
                    continue
        return None

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
