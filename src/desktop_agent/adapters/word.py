from __future__ import annotations

from pathlib import Path

from desktop_agent.errors import AdapterUnavailable
from desktop_agent.models import ActionResult


class WordAdapter:
    def __init__(self):
        self._word = None
        self._owned = False

    def probe(self) -> dict:
        try:
            import win32com.client

            word = win32com.client.GetActiveObject("Word.Application")
            count = word.Documents.Count
            return {"ok": True, "documents": int(count), "mode": "active"}
        except Exception as e:
            return {"ok": False, "error": str(e), "hint": "Start Word first"}

    def _get(self):
        if self._word is not None:
            return self._word
        try:
            import win32com.client

            self._word = win32com.client.GetActiveObject("Word.Application")
            self._owned = False
            return self._word
        except Exception:
            try:
                import win32com.client

                self._word = win32com.client.Dispatch("Word.Application")
                self._word.Visible = True
                self._owned = True
                return self._word
            except Exception as e:
                raise AdapterUnavailable(f"Word COM unavailable: {e}") from e

    def new(self) -> ActionResult:
        word = self._get()
        word.Visible = True
        word.DisplayAlerts = 0
        doc = word.Documents.Add()
        return ActionResult(
            action="word_new",
            ok=True,
            detail={"name": doc.Name, "documents": int(word.Documents.Count)},
        )

    def type_text(self, text: str) -> ActionResult:
        word = self._get()
        if word.Documents.Count == 0:
            word.Documents.Add()
        word.Selection.TypeText(text)
        return ActionResult(action="word_type_text", ok=True, detail={"length": len(text)})

    def read_text(self) -> str:
        word = self._get()
        if word.Documents.Count == 0:
            return ""
        return str(word.ActiveDocument.Content.Text or "")

    def save(self, path: str | None = None) -> ActionResult:
        word = self._get()
        if word.Documents.Count == 0:
            raise AdapterUnavailable("No open document")
        doc = word.ActiveDocument
        word.DisplayAlerts = 0
        if path:
            out = Path(path).resolve()
            out.parent.mkdir(parents=True, exist_ok=True)
            # 16 = wdFormatDocumentDefault (.docx)
            try:
                doc.SaveAs2(str(out), FileFormat=16)
            except Exception:
                doc.SaveAs(str(out), FileFormat=16)
            return ActionResult(action="word_save", ok=True, detail={"path": str(out)})
        doc.Save()
        return ActionResult(action="word_save", ok=True, detail={"path": doc.FullName})

    def close(self, *, save: bool = False, quit_app: bool = False) -> ActionResult:
        word = self._get()
        try:
            word.DisplayAlerts = 0
            if word.Documents.Count > 0:
                word.ActiveDocument.Close(SaveChanges=save)
            should_quit = word.Documents.Count == 0 and (quit_app or self._owned)
            if should_quit:
                word.Quit()
                self._word = None
                self._owned = False
        except Exception as e:
            raise AdapterUnavailable(f"Word close failed: {e}") from e
        return ActionResult(action="word_close", ok=True, detail={"quit": should_quit})
