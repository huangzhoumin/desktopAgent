"""OCR fallback for when UIA/DOM structure is insufficient.

Backends (auto-detected):
- rapidocr: optional `rapidocr-onnxruntime`
- windows: optional WinRT `Windows.Media.Ocr` packages
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from desktop_agent.models import Bounds


@dataclass
class OcrBox:
    text: str
    confidence: float
    bounds: Bounds  # screen coordinates
    raw: dict[str, Any] | None = None


class OcrBackend(Protocol):
    name: str

    def probe(self) -> dict[str, Any]: ...

    def recognize(self, image_path: Path, *, origin: tuple[int, int] = (0, 0)) -> list[OcrBox]: ...


class RapidOcrBackend:
    name = "rapidocr"

    def probe(self) -> dict[str, Any]:
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore

            _ = RapidOCR
            return {"ok": True, "engine": self.name}
        except Exception as e:
            return {"ok": False, "engine": self.name, "error": str(e)}

    def recognize(self, image_path: Path, *, origin: tuple[int, int] = (0, 0)) -> list[OcrBox]:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore

        engine = RapidOCR()
        result, _ = engine(str(image_path))
        boxes: list[OcrBox] = []
        ox, oy = origin
        if not result:
            return boxes
        for item in result:
            # item: [box(4 points), text, score]
            if not item or len(item) < 3:
                continue
            pts, text, score = item[0], str(item[1] or ""), float(item[2] or 0.0)
            xs = [float(p[0]) for p in pts]
            ys = [float(p[1]) for p in pts]
            x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
            boxes.append(
                OcrBox(
                    text=text.strip(),
                    confidence=max(0.0, min(1.0, score)),
                    bounds=Bounds(
                        x=int(ox + x0),
                        y=int(oy + y0),
                        w=max(1, int(x1 - x0)),
                        h=max(1, int(y1 - y0)),
                    ),
                    raw={"points": pts},
                )
            )
        return boxes


class WindowsOcrBackend:
    name = "windows"

    def probe(self) -> dict[str, Any]:
        try:
            self._import_winrt()
            return {"ok": True, "engine": self.name}
        except Exception as e:
            return {"ok": False, "engine": self.name, "error": str(e)}

    @staticmethod
    def _import_winrt():
        # winrt-* package layout (Windows App SDK Python projections)
        from winrt.windows.media.ocr import OcrEngine  # type: ignore

        return OcrEngine

    def recognize(self, image_path: Path, *, origin: tuple[int, int] = (0, 0)) -> list[OcrBox]:
        import asyncio

        return asyncio.run(self._recognize_async(image_path, origin=origin))

    async def _recognize_async(
        self, image_path: Path, *, origin: tuple[int, int]
    ) -> list[OcrBox]:
        from winrt.windows.graphics.imaging import BitmapDecoder  # type: ignore
        from winrt.windows.media.ocr import OcrEngine  # type: ignore
        from winrt.windows.storage import StorageFile, FileAccessMode  # type: ignore

        engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            raise RuntimeError("Windows.Media.Ocr engine unavailable for current languages")

        file = await StorageFile.get_file_from_path_async(str(image_path.resolve()))
        stream = await file.open_async(FileAccessMode.READ)
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        result = await engine.recognize_async(bitmap)

        ox, oy = origin
        boxes: list[OcrBox] = []
        for line in result.lines:
            text = str(getattr(line, "text", "") or "").strip()
            if not text:
                continue
            # Prefer word-level boxes when present for tighter click targets.
            words = list(getattr(line, "words", []) or [])
            if words:
                for word in words:
                    wtext = str(getattr(word, "text", "") or "").strip()
                    rect = getattr(word, "bounding_rect", None)
                    if not wtext or rect is None:
                        continue
                    boxes.append(
                        OcrBox(
                            text=wtext,
                            confidence=0.85,
                            bounds=Bounds(
                                x=int(ox + rect.x),
                                y=int(oy + rect.y),
                                w=max(1, int(rect.width)),
                                h=max(1, int(rect.height)),
                            ),
                        )
                    )
            else:
                rect = getattr(line, "bounding_rect", None)
                if rect is None:
                    continue
                boxes.append(
                    OcrBox(
                        text=text,
                        confidence=0.8,
                        bounds=Bounds(
                            x=int(ox + rect.x),
                            y=int(oy + rect.y),
                            w=max(1, int(rect.width)),
                            h=max(1, int(rect.height)),
                        ),
                    )
                )
        return boxes


class OcrEngine:
    """Facade that picks an available backend."""

    def __init__(self, preferred: str = "auto"):
        self.preferred = (preferred or "auto").lower()
        self._backend: OcrBackend | None = None
        self._init_error: str | None = None
        self._select_backend()

    def _select_backend(self) -> None:
        order: list[OcrBackend]
        if self.preferred == "rapidocr":
            order = [RapidOcrBackend(), WindowsOcrBackend()]
        elif self.preferred == "windows":
            order = [WindowsOcrBackend(), RapidOcrBackend()]
        else:
            order = [RapidOcrBackend(), WindowsOcrBackend()]

        errors: list[str] = []
        for backend in order:
            status = backend.probe()
            if status.get("ok"):
                self._backend = backend
                self._init_error = None
                return
            errors.append(f"{backend.name}: {status.get('error')}")
        self._backend = None
        self._init_error = (
            "No OCR backend available. Install optional vision deps: "
            "`pip install rapidocr-onnxruntime` "
            "(or WinRT OCR packages). Details: " + "; ".join(errors)
        )

    @property
    def available(self) -> bool:
        return self._backend is not None

    @property
    def engine_name(self) -> str | None:
        return self._backend.name if self._backend else None

    def probe(self) -> dict[str, Any]:
        if self._backend is None:
            return {"ok": False, "error": self._init_error or "OCR unavailable"}
        return {"ok": True, "engine": self._backend.name, "preferred": self.preferred}

    def recognize(self, image_path: str | Path, *, origin: tuple[int, int] = (0, 0)) -> list[OcrBox]:
        if self._backend is None:
            raise RuntimeError(self._init_error or "OCR unavailable")
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"OCR image not found: {path}")
        boxes = self._backend.recognize(path, origin=origin)
        return [b for b in boxes if b.text]
