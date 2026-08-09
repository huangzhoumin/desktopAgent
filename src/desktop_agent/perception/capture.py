"""Screenshot helpers for OCR/VLM (multi-monitor / DPI-aware)."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from desktop_agent.common.dpi import ensure_dpi_aware


@dataclass
class CaptureResult:
    path: str
    origin: tuple[int, int]
    width: int
    height: int
    scope: str
    detail: dict[str, Any]


def virtual_screen_origin() -> tuple[int, int]:
    ensure_dpi_aware()
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        # SM_XVIRTUALSCREEN=76, SM_YVIRTUALSCREEN=77
        return int(user32.GetSystemMetrics(76)), int(user32.GetSystemMetrics(77))
    except Exception:
        return 0, 0


def virtual_screen_size() -> tuple[int, int]:
    ensure_dpi_aware()
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        # SM_CXVIRTUALSCREEN=78, SM_CYVIRTUALSCREEN=79
        return int(user32.GetSystemMetrics(78)), int(user32.GetSystemMetrics(79))
    except Exception:
        return 0, 0


def virtual_screen_rect() -> tuple[int, int, int, int]:
    """Return (left, top, width, height) of the virtual desktop in physical pixels."""
    ox, oy = virtual_screen_origin()
    w, h = virtual_screen_size()
    return ox, oy, w, h


def _grab_pil(*, bbox: tuple[int, int, int, int] | None = None):
    from PIL import ImageGrab

    if bbox is None:
        return ImageGrab.grab(all_screens=True)
    return ImageGrab.grab(bbox=bbox, all_screens=True)


def _grab_bitblt(left: int, top: int, width: int, height: int) -> Any:
    """Capture a screen rectangle via GDI BitBlt (physical pixels, multi-monitor safe)."""
    from PIL import Image

    if width <= 0 or height <= 0:
        raise ValueError(f"invalid capture size: {width}x{height}")

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    gdi32 = ctypes.windll.gdi32  # type: ignore[attr-defined]

    hdc_screen = user32.GetDC(0)
    if not hdc_screen:
        raise OSError("GetDC failed")
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
    if not hdc_mem or not hbmp:
        if hbmp:
            gdi32.DeleteObject(hbmp)
        if hdc_mem:
            gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(0, hdc_screen)
        raise OSError("CreateCompatibleDC/Bitmap failed")

    old = gdi32.SelectObject(hdc_mem, hbmp)
    ok = gdi32.BitBlt(hdc_mem, 0, 0, width, height, hdc_screen, left, top, 0x00CC0020)
    gdi32.SelectObject(hdc_mem, old)

    if not ok:
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(0, hdc_screen)
        raise OSError("BitBlt failed")

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = width
    bmi.bmiHeader.biHeight = -height  # top-down
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0  # BI_RGB

    buf_size = width * height * 4
    buf = (ctypes.c_ubyte * buf_size)()
    lines = gdi32.GetDIBits(hdc_mem, hbmp, 0, height, ctypes.byref(buf), ctypes.byref(bmi), 0)

    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(0, hdc_screen)

    if lines != height:
        raise OSError(f"GetDIBits returned {lines}, expected {height}")

    img = Image.frombuffer("RGBA", (width, height), bytes(buf), "raw", "BGRA", 0, 1)
    return img.convert("RGB")


def _grab_region(left: int, top: int, width: int, height: int) -> tuple[Any, str]:
    """Grab (left,top,width,height); prefer BitBlt, fall back to ImageGrab."""
    ensure_dpi_aware()
    try:
        return _grab_bitblt(left, top, width, height), "bitblt"
    except Exception:
        img = _grab_pil(bbox=(left, top, left + width, top + height))
        return img, "imagegrab"


def _grab_full() -> tuple[Any, tuple[int, int], str]:
    ensure_dpi_aware()
    ox, oy, vw, vh = virtual_screen_rect()
    if vw > 0 and vh > 0:
        img, backend = _grab_region(ox, oy, vw, vh)
        # Reject truncated virtual-desktop captures.
        if img.width >= int(vw * 0.95) and img.height >= int(vh * 0.95):
            return img, (ox, oy), backend
        # Retry alternate backend.
        try:
            if backend == "bitblt":
                img = _grab_pil(None)
                backend = "imagegrab-retry"
            else:
                img = _grab_bitblt(ox, oy, vw, vh)
                backend = "bitblt-retry"
        except Exception:
            pass
        return img, (ox, oy), backend

    img = _grab_pil(None)
    return img, virtual_screen_origin(), "imagegrab"


def capture_screen(
    path: str | Path,
    *,
    scope: str = "full",
    bounds: tuple[int, int, int, int] | None = None,
) -> CaptureResult:
    """Capture screenshot and return mapping origin for image→screen coords.

    bounds: optional (x, y, w, h) in screen coordinates for foreground crop.
    """
    ensure_dpi_aware()
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    scope_l = (scope or "full").lower()

    if scope_l == "foreground" and bounds is not None:
        x, y, w, h = (int(v) for v in bounds)
        if w > 0 and h > 0:
            img, backend = _grab_region(x, y, w, h)
            # If crop is truncated (DPI mismatch), grab full virtual desktop and crop.
            if img.width < int(w * 0.9) or img.height < int(h * 0.9):
                full, origin, backend = _grab_full()
                ox, oy = origin
                left = max(0, x - ox)
                top = max(0, y - oy)
                right = min(full.width, left + w)
                bottom = min(full.height, top + h)
                img = full.crop((left, top, right, bottom))
                backend = f"{backend}+crop"
            img.save(out)
            return CaptureResult(
                path=str(out),
                origin=(x, y),
                width=img.width,
                height=img.height,
                scope="foreground",
                detail={"bbox": [x, y, x + w, y + h], "backend": backend, "requested": [x, y, w, h]},
            )

    img, origin, backend = _grab_full()
    vw, vh = virtual_screen_size()
    img.save(out)
    return CaptureResult(
        path=str(out),
        origin=origin,
        width=img.width,
        height=img.height,
        scope="full",
        detail={
            "virtual_origin": list(origin),
            "virtual_size": [vw, vh],
            "backend": backend,
        },
    )
