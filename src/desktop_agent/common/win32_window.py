from __future__ import annotations

import ctypes
from ctypes import wintypes


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

SW_MAXIMIZE = 3
SW_SHOW = 5
SW_MINIMIZE = 6
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
    # Only restore when minimized — SW_RESTORE would undo a maximized window.
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    else:
        user32.ShowWindow(hwnd, SW_SHOW)

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


def maximize_window(hwnd: int) -> bool:
    """Maximize a top-level window on its current monitor (keeps browser chrome UI)."""
    if not hwnd:
        return False
    hwnd = int(hwnd)
    return bool(user32.ShowWindow(hwnd, SW_MAXIMIZE))


def move_to_primary(hwnd: int, x: int = 80, y: int = 80, w: int = 960, h: int = 700) -> None:
    """Move/resize window onto the primary monitor working area."""
    if not hwnd:
        return
    # Primary monitor origin is (0,0) in virtual screen coords for the primary display.
    user32.SetWindowPos(int(hwnd), HWND_TOP, int(x), int(y), int(w), int(h), SWP_SHOWWINDOW)


def move_to_primary_maximized(hwnd: int) -> bool:
    """Place window on the primary monitor and maximize it to fill the screen."""
    if not hwnd:
        return False
    hwnd = int(hwnd)
    # Park on primary first so maximize lands on the agent-facing display.
    sw, sh = primary_screen_size()
    # Use a temporary restored size near primary origin, then maximize.
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetWindowPos(hwnd, HWND_TOP, 40, 40, min(800, sw), min(600, sh), SWP_SHOWWINDOW)
    return maximize_window(hwnd)


def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    rect = wintypes.RECT()
    if not user32.GetWindowRect(int(hwnd), ctypes.byref(rect)):
        return None
    return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)


def primary_screen_size() -> tuple[int, int]:
    return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))


def monitor_count() -> int:
    return int(user32.GetSystemMetrics(80))


def find_top_level_hwnd_by_pid(pid: int) -> int | None:
    """Return a visible top-level HWND owned by pid (best-effort)."""
    if not pid:
        return None
    result = ctypes.c_void_p(0)

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _enum(hwnd, _lparam):  # type: ignore[misc]
        if not user32.IsWindowVisible(hwnd):
            return True
        got = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(got))
        if int(got.value) != int(pid):
            return True
        # Skip owned tool windows when possible.
        if user32.GetWindow(hwnd, 4):  # GW_OWNER=4
            return True
        result.value = hwnd
        return False

    user32.EnumWindows(_enum, 0)
    return int(result.value) if result.value else None


def find_browser_hwnd(*, pids: set[int] | None = None, title_substr: str = "") -> int | None:
    """Find a visible Chrome/Edge top-level window, optionally filtered by pid/title."""
    needle = (title_substr or "").strip().lower()
    matches: list[tuple[int, int]] = []  # (score, hwnd)

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _enum(hwnd, _lparam):  # type: ignore[misc]
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.GetWindow(hwnd, 4):  # GW_OWNER
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pids and int(pid.value) not in pids:
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value or ""
        low = title.lower()
        if not pids and ("chrome" not in low and "edge" not in low):
            return True
        score = 1
        if needle and needle in low:
            score += 10
        if pids and int(pid.value) in pids:
            score += 5
        matches.append((score, int(hwnd)))
        return True

    user32.EnumWindows(_enum, 0)
    if not matches:
        return None
    matches.sort(key=lambda x: x[0], reverse=True)
    return matches[0][1]
