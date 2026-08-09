"""Windows DPI awareness helpers (multi-monitor / high-DPI safe)."""

from __future__ import annotations

import ctypes
from typing import Any


_DPI_READY = False
_DPI_MODE: str | None = None


def ensure_dpi_aware() -> str:
    """Enable Per-Monitor DPI awareness so screen metrics match physical pixels.

    Without this, GetSystemMetrics / EnumDisplayMonitors report logical sizes while
    UIA bounds and ImageGrab often use physical pixels — dual-monitor crops and
    VLM/OCR click mapping then miss or clip the secondary display.
    """
    global _DPI_READY, _DPI_MODE
    if _DPI_READY and _DPI_MODE:
        return _DPI_MODE

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = (DPI_AWARENESS_CONTEXT)-4
    try:
        if bool(user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))):
            _DPI_READY = True
            _DPI_MODE = "per_monitor_v2"
            return _DPI_MODE
    except Exception:
        pass

    try:
        shcore = ctypes.windll.shcore  # type: ignore[attr-defined]
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        hr = int(shcore.SetProcessDpiAwareness(2))
        if hr == 0:  # S_OK
            _DPI_READY = True
            _DPI_MODE = "per_monitor"
            return _DPI_MODE
    except Exception:
        pass

    try:
        if bool(user32.SetProcessDPIAware()):
            _DPI_READY = True
            _DPI_MODE = "system"
            return _DPI_MODE
    except Exception:
        pass

    _DPI_READY = True
    _DPI_MODE = "unaware"
    return _DPI_MODE


def dpi_status() -> dict[str, Any]:
    mode = ensure_dpi_aware()
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    return {
        "mode": mode,
        "primary": (int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))),
        "virtual_origin": (int(user32.GetSystemMetrics(76)), int(user32.GetSystemMetrics(77))),
        "virtual_size": (int(user32.GetSystemMetrics(78)), int(user32.GetSystemMetrics(79))),
        "monitors": int(user32.GetSystemMetrics(80)),
    }
