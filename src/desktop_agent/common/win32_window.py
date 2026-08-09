from __future__ import annotations

import ctypes
from ctypes import wintypes


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

SW_RESTORE = 9
HWND_TOP = 0
SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_SHOWWINDOW = 0x0040


def get_foreground_hwnd() -> int:
    return int(user32.GetForegroundWindow() or 0)


def force_foreground(hwnd: int) -> bool:
    """Best-effort foreground activation that works better across multi-monitor setups."""
    if not hwnd:
        return False
    hwnd = int(hwnd)
    user32.ShowWindow(hwnd, SW_RESTORE)

    fg = user32.GetForegroundWindow()
    cur_tid = kernel32.GetCurrentThreadId()
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    target_tid = user32.GetWindowThreadProcessId(hwnd, None)

    attached_fg = False
    attached_target = False
    try:
        if fg_tid and fg_tid != cur_tid:
            attached_fg = bool(user32.AttachThreadInput(cur_tid, fg_tid, True))
        if target_tid and target_tid != cur_tid:
            attached_target = bool(user32.AttachThreadInput(cur_tid, target_tid, True))

        user32.BringWindowToTop(hwnd)
        user32.SetWindowPos(hwnd, HWND_TOP, 0, 0, 0, 0, SWP_NOSIZE | SWP_SHOWWINDOW | 0x0002)
        ok = bool(user32.SetForegroundWindow(hwnd))
        return ok or get_foreground_hwnd() == hwnd
    finally:
        if attached_target:
            user32.AttachThreadInput(cur_tid, target_tid, False)
        if attached_fg:
            user32.AttachThreadInput(cur_tid, fg_tid, False)


def move_to_primary(hwnd: int, x: int = 80, y: int = 80, w: int = 960, h: int = 700) -> None:
    """Move/resize window onto the primary monitor working area."""
    if not hwnd:
        return
    # Primary monitor origin is (0,0) in virtual screen coords for the primary display.
    user32.SetWindowPos(int(hwnd), HWND_TOP, int(x), int(y), int(w), int(h), SWP_SHOWWINDOW)


def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    rect = wintypes.RECT()
    if not user32.GetWindowRect(int(hwnd), ctypes.byref(rect)):
        return None
    return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)


def primary_screen_size() -> tuple[int, int]:
    return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))


def monitor_count() -> int:
    return int(user32.GetSystemMetrics(80))
