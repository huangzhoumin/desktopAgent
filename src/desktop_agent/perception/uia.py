from __future__ import annotations

from typing import Any

import uiautomation as auto

from desktop_agent.config import AgentConfig
from desktop_agent.models import Bounds, Observation, UIElement, WindowInfo, new_id, utc_now_iso
from desktop_agent.perception.process_map import resolve_app_alias

# Common control type name mapping
_ROLE_MAP = {
    "ButtonControl": "Button",
    "EditControl": "Edit",
    "TextControl": "Text",
    "ComboBoxControl": "ComboBox",
    "CheckBoxControl": "CheckBox",
    "RadioButtonControl": "RadioButton",
    "HyperlinkControl": "Hyperlink",
    "ListItemControl": "ListItem",
    "ListControl": "List",
    "MenuItemControl": "MenuItem",
    "MenuControl": "Menu",
    "TreeItemControl": "TreeItem",
    "TabItemControl": "TabItem",
    "DocumentControl": "Document",
    "PaneControl": "Pane",
    "WindowControl": "Window",
    "DataItemControl": "DataItem",
    "DataGridControl": "DataGrid",
    "SpinnerControl": "Spinner",
    "SliderControl": "Slider",
    "ScrollBarControl": "ScrollBar",
    "GroupControl": "Group",
    "ImageControl": "Image",
    "CalendarControl": "Calendar",
    "ToolBarControl": "ToolBar",
    "StatusBarControl": "StatusBar",
}


def _role_of(ctrl: auto.Control) -> str:
    name = type(ctrl).__name__
    return _ROLE_MAP.get(name, name.replace("Control", "") or "Unknown")


def _bounds_of(ctrl: auto.Control) -> Bounds | None:
    try:
        r = ctrl.BoundingRectangle
        if r is None:
            return None
        w = int(r.right - r.left)
        h = int(r.bottom - r.top)
        if w <= 0 or h <= 0:
            return None
        return Bounds(x=int(r.left), y=int(r.top), w=w, h=h)
    except Exception:
        return None


def _safe_get(getter, default=""):
    try:
        value = getter()
        return default if value is None else value
    except Exception:
        return default


class UiaPerception:
    def __init__(self, config: AgentConfig):
        self.config = config
        self._window_index: dict[str, WindowInfo] = {}
        self._element_index: dict[str, UIElement] = {}
        self._runtime_refs: dict[str, Any] = {}

    def list_windows(self, app_filter: str | None = None) -> list[WindowInfo]:
        results: list[WindowInfo] = []
        root = auto.GetRootControl()
        for win in root.GetChildren():
            try:
                if not win.IsTopLevel():
                    continue
                if not win.Name:
                    continue
                if not win.IsEnabled:
                    continue
                pid = int(win.ProcessId)
                process = self._process_name(pid)
                app = resolve_app_alias(process, self.config)
                if app_filter and app_filter.lower() not in {app.lower(), process.lower()}:
                    continue
                wid = new_id("win")
                info = WindowInfo(
                    window_id=wid,
                    title=str(win.Name),
                    app=app,
                    process=process,
                    pid=pid,
                    handle=int(win.NativeWindowHandle),
                    bounds=_bounds_of(win),
                )
                self._window_index[wid] = info
                self._runtime_refs[wid] = win
                results.append(info)
            except Exception:
                continue
        return results

    def get_foreground_window(self) -> WindowInfo | None:
        try:
            ctrl = auto.GetForegroundControl()
            # climb to top-level window
            win = ctrl
            while win and win.GetParentControl() and not win.IsTopLevel():
                win = win.GetParentControl()
            if win is None:
                return None
            pid = int(win.ProcessId)
            process = self._process_name(pid)
            wid = new_id("win")
            info = WindowInfo(
                window_id=wid,
                title=str(win.Name or ""),
                app=resolve_app_alias(process, self.config),
                process=process,
                pid=pid,
                handle=int(win.NativeWindowHandle or 0),
                bounds=_bounds_of(win),
            )
            self._window_index[wid] = info
            self._runtime_refs[wid] = win
            return info
        except Exception:
            return None

    def sense_foreground(
        self,
        *,
        max_nodes: int | None = None,
        roles: list[str] | None = None,
    ) -> Observation:
        self._element_index.clear()
        window = self.get_foreground_window()
        elements: list[UIElement] = []
        notes = ""
        if window is None:
            notes = "no foreground window"
            return Observation(
                obs_id=new_id("obs"),
                timestamp=utc_now_iso(),
                foreground_window=None,
                elements=[],
                notes=notes,
            )

        root = self._runtime_refs.get(window.window_id)
        limit = max_nodes or self.config.uia_max_nodes
        role_filter = {r.lower() for r in roles} if roles else None

        if root is not None:
            self._walk(root, window, elements, limit, role_filter, path="Window")

        obs = Observation(
            obs_id=new_id("obs"),
            timestamp=utc_now_iso(),
            foreground_window=window,
            elements=elements,
            notes=notes or f"uia nodes={len(elements)}",
        )
        return obs

    def find_elements(
        self,
        *,
        text: str | None = None,
        role: str | None = None,
        automation_id: str | None = None,
        top_k: int = 5,
        refresh: bool = True,
    ) -> list[UIElement]:
        if refresh or not self._element_index:
            self.sense_foreground()
        hits: list[tuple[float, UIElement]] = []
        for el in self._element_index.values():
            score = 0.0
            if automation_id and el.automation_id.lower() == automation_id.lower():
                score += 5
            if role and el.role.lower() == role.lower():
                score += 2
            if text:
                t = text.lower()
                if t == el.name.lower():
                    score += 4
                elif t in el.name.lower():
                    score += 2
                elif t in (el.value or "").lower():
                    score += 1
            if score > 0:
                hits.append((score, el))
        hits.sort(key=lambda x: x[0], reverse=True)
        return [el for _, el in hits[:top_k]]

    def get_element(self, element_id: str) -> UIElement | None:
        return self._element_index.get(element_id)

    def get_control(self, element_id: str):
        return self._runtime_refs.get(element_id)

    def get_window_control(self, window_id: str):
        return self._runtime_refs.get(window_id)

    def _walk(
        self,
        ctrl: auto.Control,
        window: WindowInfo,
        out: list[UIElement],
        limit: int,
        role_filter: set[str] | None,
        path: str,
    ) -> None:
        if len(out) >= limit:
            return
        try:
            if ctrl.IsOffscreen:
                # still walk children sometimes offscreen parents exist
                pass
            role = _role_of(ctrl)
            name = str(_safe_get(lambda: ctrl.Name, "") or "")
            automation_id = str(_safe_get(lambda: ctrl.AutomationId, "") or "")
            bounds = _bounds_of(ctrl)
            enabled = bool(_safe_get(lambda: ctrl.IsEnabled, False))
            offscreen = bool(_safe_get(lambda: ctrl.IsOffscreen, True))
            value = ""
            try:
                value = str(ctrl.GetValuePattern().Value)
            except Exception:
                value = ""

            interesting = role in {
                "Button",
                "Edit",
                "ComboBox",
                "CheckBox",
                "RadioButton",
                "Hyperlink",
                "ListItem",
                "MenuItem",
                "TabItem",
                "DataItem",
                "Document",
                "TreeItem",
            } or bool(name) or bool(automation_id)

            if interesting and (not role_filter or role.lower() in role_filter):
                if not offscreen or role in {"Edit", "Document"}:
                    eid = new_id("el")
                    actions = []
                    if role in {"Button", "Hyperlink", "MenuItem", "TabItem", "ListItem"}:
                        actions.append("click")
                    if role in {"Edit", "Document", "ComboBox", "Spinner"}:
                        actions.extend(["click", "type", "set_value"])
                    states = []
                    if enabled:
                        states.append("enabled")
                    if not offscreen:
                        states.append("visible")
                    el = UIElement(
                        element_id=eid,
                        source="uia",
                        app=window.app,
                        window_id=window.window_id,
                        role=role,
                        name=name,
                        automation_id=automation_id,
                        value=value,
                        states=states,
                        bounds=bounds,
                        path=path,
                        actions=actions,
                        confidence=0.95,
                        raw_ref={"handle": int(getattr(ctrl, "NativeWindowHandle", 0) or 0)},
                    )
                    out.append(el)
                    self._element_index[eid] = el
                    self._runtime_refs[eid] = ctrl
        except Exception:
            pass

        try:
            children = ctrl.GetChildren()
        except Exception:
            return
        for idx, child in enumerate(children):
            if len(out) >= limit:
                return
            child_role = _role_of(child)
            child_name = str(_safe_get(lambda: child.Name, "") or "")[:40]
            child_path = f"{path}/{child_role}"
            if child_name:
                child_path += f"[{child_name}]"
            else:
                child_path += f"[{idx}]"
            self._walk(child, window, out, limit, role_filter, child_path)

    @staticmethod
    def _process_name(pid: int) -> str:
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return f"pid_{pid}"
            try:
                buf = ctypes.create_unicode_buffer(260)
                size = wintypes.DWORD(len(buf))
                QueryFullProcessImageNameW = kernel32.QueryFullProcessImageNameW
                QueryFullProcessImageNameW.argtypes = [
                    wintypes.HANDLE,
                    wintypes.DWORD,
                    wintypes.LPWSTR,
                    ctypes.POINTER(wintypes.DWORD),
                ]
                QueryFullProcessImageNameW.restype = wintypes.BOOL
                if QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                    path = buf.value
                    return path.rsplit("\\", 1)[-1]
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            pass
        return f"pid_{pid}"
