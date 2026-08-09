"""Shared pytest fixtures / platform stubs.

Real UIA / COM only work on Windows. Cloud Linux agents still run pure unit tests
by stubbing uiautomation and common win32 helpers before importing desktop_agent.
"""

from __future__ import annotations

import sys
import types


def _ensure_uia_stub() -> None:
    if "uiautomation" in sys.modules:
        return
    auto_stub = types.ModuleType("uiautomation")

    def _missing(name):
        def _fn(*args, **kwargs):
            raise RuntimeError(f"uiautomation stub called: {name}")

        return _fn

    for name in (
        "GetRootControl",
        "ControlFromHandle",
        "ButtonControl",
        "EditControl",
        "ComboBoxControl",
        "SendKeys",
        "Click",
        "UIAutomationInitializerInThread",
    ):
        setattr(auto_stub, name, _missing(name))
    sys.modules["uiautomation"] = auto_stub


def _ensure_win32_stubs() -> None:
    win32_mods = {
        "win32gui": {},
        "win32process": {},
        "win32api": {},
        "win32con": {},
        "pythoncom": {},
        "win32com": {},
        "win32com.client": {
            "Dispatch": lambda *a, **k: None,
            "GetActiveObject": lambda *a, **k: None,
        },
    }
    for name, attrs in win32_mods.items():
        if name not in sys.modules:
            mod = types.ModuleType(name)
            for k, v in attrs.items():
                setattr(mod, k, v)
            sys.modules[name] = mod
    if "win32com" in sys.modules and "win32com.client" in sys.modules:
        sys.modules["win32com"].client = sys.modules["win32com.client"]

    # win32_window loads user32.dll at import time — stub on non-Windows.
    if sys.platform.startswith("win"):
        return
    if "desktop_agent.common.win32_window" not in sys.modules:
        stub = types.ModuleType("desktop_agent.common.win32_window")
        stub.force_foreground = lambda *a, **k: None
        stub.monitor_count = lambda: 1
        stub.move_to_primary = lambda *a, **k: None
        stub.primary_screen_size = lambda: (1920, 1080)
        stub.window_rect = lambda *a, **k: None
        stub.get_foreground_hwnd = lambda: 0
        sys.modules["desktop_agent.common.win32_window"] = stub


_ensure_uia_stub()
_ensure_win32_stubs()
