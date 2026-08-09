"""Screenshot helpers for OCR/VLM (screen-coordinate aware)."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class CaptureResult:
    path: str
    origin: tuple[int, int]
    width: int
    height: int
    scope: str
    detail: dict[str, Any]


def virtual_screen_origin() -> tuple[int, int]:
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        # SM_XVIRTUALSCREEN=76, SM_YVIRTUALSCREEN=77
        return int(user32.GetSystemMetrics(76)), int(user32.GetSystemMetrics(77))
    except Exception:
        return 0, 0


def capture_screen(
    path: str | Path,
    *,
    scope: str = "full",
    bounds: tuple[int, int, int, int] | None = None,
) -> CaptureResult:
    """Capture screenshot and return mapping origin for image→screen coords.

    bounds: optional (x, y, w, h) in screen coordinates for foreground crop.
    """
    from PIL import ImageGrab

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    scope_l = (scope or "full").lower()

    if scope_l == "foreground" and bounds is not None:
        x, y, w, h = bounds
        if w > 0 and h > 0:
            bbox = (x, y, x + w, y + h)
            img = ImageGrab.grab(bbox=bbox, all_screens=True)
            img.save(out)
            return CaptureResult(
                path=str(out),
                origin=(x, y),
                width=img.width,
                height=img.height,
                scope="foreground",
                detail={"bbox": list(bbox)},
            )

    # Full virtual desktop
    origin = virtual_screen_origin()
    img = ImageGrab.grab(all_screens=True)
    img.save(out)
    return CaptureResult(
        path=str(out),
        origin=origin,
        width=img.width,
        height=img.height,
        scope="full",
        detail={"virtual_origin": list(origin)},
    )
