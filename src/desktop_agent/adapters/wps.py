from __future__ import annotations

from pathlib import Path

from desktop_agent.errors import AdapterUnavailable
from desktop_agent.models import ActionResult


class WpsAdapter:
    """WPS adapter: COM for Sheets (KET) / Writer (Kwps), else UIA fallback note."""

    SHEETS_CANDIDATES = [
        "KET.Application",
        "ket.Application",
        "ET.Application",
    ]
    WRITER_CANDIDATES = [
        "Kwps.Application",
        "kwps.Application",
        "WPS.Application",
        "wps.Application",
    ]
    # Legacy mixed list kept for probe()
    CANDIDATES = SHEETS_CANDIDATES + WRITER_CANDIDATES

    def __init__(self):
        self._sheets = None
        self._sheets_progid: str | None = None
        self._sheets_owned = False
        self._writer = None
        self._writer_progid: str | None = None
        self._writer_owned = False
        self._last_wb = None
        self._last_doc = None

    def probe(self) -> dict:
        processes = self._running_wps_processes()
        sheets = self._try_com(self.SHEETS_CANDIDATES, allow_dispatch=False, kind="sheets")
        writer = self._try_com(self.WRITER_CANDIDATES, allow_dispatch=False, kind="writer")
        return {
            "ok": bool(processes) or bool(sheets.get("ok")) or bool(writer.get("ok")),
            "processes": processes,
            "sheets": sheets,
            "writer": writer,
            "note": "If COM unavailable, use UIA tools (get_ui_summary/click/type_text).",
        }

    def _try_com(self, candidates: list[str], *, allow_dispatch: bool, kind: str) -> dict:
        try:
            import win32com.client
        except Exception as e:
            return {"ok": False, "error": f"pywin32 missing: {e}"}

        errors = []
        for progid in candidates:
            try:
                app = win32com.client.GetActiveObject(progid)
                self._bind(kind, app, progid, owned=False)
                return {"ok": True, "progid": progid, "mode": "active"}
            except Exception as e:
                errors.append(f"{progid}/active: {e}")
        if allow_dispatch:
            for progid in candidates:
                try:
                    app = win32com.client.Dispatch(progid)
                    try:
                        app.Visible = True
                    except Exception:
                        pass
                    self._bind(kind, app, progid, owned=True)
                    return {"ok": True, "progid": progid, "mode": "dispatch"}
                except Exception as e:
                    errors.append(f"{progid}/dispatch: {e}")
        return {"ok": False, "error": "; ".join(errors[-4:])}

    def _bind(self, kind: str, app, progid: str, *, owned: bool) -> None:
        if kind == "sheets":
            self._sheets = app
            self._sheets_progid = progid
            self._sheets_owned = owned
        else:
            self._writer = app
            self._writer_progid = progid
            self._writer_owned = owned

    def new(self) -> ActionResult:
        app = self._ensure_sheets(allow_dispatch=True)
        try:
            app.Visible = True
        except Exception:
            pass
        try:
            app.DisplayAlerts = False
        except Exception:
            pass
        wb = app.Workbooks.Add()
        self._last_wb = wb
        name = None
        try:
            name = wb.Name
        except Exception:
            pass
        return ActionResult(
            action="wps_new",
            ok=True,
            detail={"name": name, "progid": self._sheets_progid},
        )

    def _active_workbook(self, app):
        if self._last_wb is not None:
            try:
                _ = self._last_wb.Name
                return self._last_wb
            except Exception:
                self._last_wb = None
        try:
            wb = app.ActiveWorkbook
            if wb is not None:
                self._last_wb = wb
                return wb
        except Exception:
            pass
        count = int(app.Workbooks.Count)
        if count <= 0:
            raise AdapterUnavailable("No open WPS workbook")
        wb = app.Workbooks(count)
        self._last_wb = wb
        return wb

    def _worksheet(self, wb, sheet: str | None = None):
        if sheet:
            try:
                return wb.Worksheets(sheet)
            except Exception:
                return wb.Sheets(sheet)
        try:
            return wb.ActiveSheet
        except Exception:
            pass
        try:
            return wb.Worksheets(1)
        except Exception:
            return wb.Sheets(1)

    def set_cell(self, range_addr: str, value, sheet: str | None = None) -> ActionResult:
        app = self._ensure_sheets(allow_dispatch=True)
        try:
            if int(app.Workbooks.Count) == 0:
                self.new()
            wb = self._active_workbook(app)
            ws = self._worksheet(wb, sheet)
            ws.Range(range_addr).Value = value
            return ActionResult(
                action="wps_set_cell",
                ok=True,
                detail={"range": range_addr, "value": value, "progid": self._sheets_progid},
            )
        except AdapterUnavailable:
            raise
        except Exception as e:
            raise AdapterUnavailable(f"WPS set_cell failed: {e}") from e

    def get_cell(self, range_addr: str, sheet: str | None = None) -> ActionResult:
        app = self._ensure_sheets(allow_dispatch=True)
        try:
            wb = self._active_workbook(app)
            ws = self._worksheet(wb, sheet)
            value = ws.Range(range_addr).Value
            return ActionResult(
                action="wps_get_cell",
                ok=True,
                detail={"range": range_addr, "value": value, "progid": self._sheets_progid},
            )
        except AdapterUnavailable:
            raise
        except Exception as e:
            raise AdapterUnavailable(f"WPS get_cell failed: {e}") from e

    def save(self, path: str | None = None) -> ActionResult:
        app = self._ensure_sheets(allow_dispatch=True)
        try:
            try:
                app.DisplayAlerts = False
            except Exception:
                pass
            wb = self._active_workbook(app)
            if path:
                out = Path(path).resolve()
                out.parent.mkdir(parents=True, exist_ok=True)
                saved = False
                for kwargs in (
                    {"FileName": str(out), "FileFormat": 51},
                    {"FileName": str(out)},
                    {},
                ):
                    try:
                        if kwargs:
                            wb.SaveAs(**kwargs)
                        else:
                            wb.SaveAs(str(out))
                        saved = True
                        break
                    except Exception:
                        continue
                if not saved:
                    raise AdapterUnavailable("WPS SaveAs failed for all variants")
                return ActionResult(
                    action="wps_save",
                    ok=True,
                    detail={"path": str(out), "progid": self._sheets_progid},
                )
            wb.Save()
            return ActionResult(
                action="wps_save",
                ok=True,
                detail={"path": getattr(wb, "FullName", None), "progid": self._sheets_progid},
            )
        except AdapterUnavailable:
            raise
        except Exception as e:
            raise AdapterUnavailable(f"WPS save failed: {e}") from e

    def type_text(self, text: str) -> ActionResult:
        app = self._ensure_writer(allow_dispatch=True)
        try:
            try:
                app.Visible = True
            except Exception:
                pass
            if int(app.Documents.Count) == 0:
                self._last_doc = app.Documents.Add()
            else:
                try:
                    self._last_doc = app.ActiveDocument
                except Exception:
                    self._last_doc = app.Documents(int(app.Documents.Count))
            app.Selection.TypeText(text)
            return ActionResult(
                action="wps_type_text",
                ok=True,
                detail={"length": len(text), "progid": self._writer_progid},
            )
        except Exception as e:
            raise AdapterUnavailable(f"WPS type_text failed: {e}") from e

    def read_text(self) -> str:
        app = self._ensure_writer(allow_dispatch=True)
        doc = self._last_doc
        if doc is None:
            if int(app.Documents.Count) == 0:
                return ""
            try:
                doc = app.ActiveDocument
            except Exception:
                doc = app.Documents(int(app.Documents.Count))
        return str(doc.Content.Text or "")

    def save_document(self, path: str | None = None) -> ActionResult:
        app = self._ensure_writer(allow_dispatch=True)
        try:
            try:
                app.DisplayAlerts = 0
            except Exception:
                pass
            doc = self._last_doc
            if doc is None:
                if int(app.Documents.Count) == 0:
                    raise AdapterUnavailable("No open WPS document")
                try:
                    doc = app.ActiveDocument
                except Exception:
                    doc = app.Documents(int(app.Documents.Count))
            if path:
                out = Path(path).resolve()
                out.parent.mkdir(parents=True, exist_ok=True)
                saved = False
                for call in (
                    lambda: doc.SaveAs2(str(out), FileFormat=16),
                    lambda: doc.SaveAs(str(out), FileFormat=16),
                    lambda: doc.SaveAs(FileName=str(out)),
                    lambda: doc.SaveAs(str(out)),
                ):
                    try:
                        call()
                        saved = True
                        break
                    except Exception:
                        continue
                if not saved:
                    raise AdapterUnavailable("WPS document SaveAs failed for all variants")
                return ActionResult(
                    action="wps_save_document",
                    ok=True,
                    detail={"path": str(out), "progid": self._writer_progid},
                )
            doc.Save()
            return ActionResult(
                action="wps_save_document",
                ok=True,
                detail={"path": getattr(doc, "FullName", None), "progid": self._writer_progid},
            )
        except AdapterUnavailable:
            raise
        except Exception as e:
            raise AdapterUnavailable(f"WPS save_document failed: {e}") from e

    def close_sheets(self, *, save: bool = False, quit_app: bool = False) -> ActionResult:
        app = self._sheets
        if app is None:
            return ActionResult(action="wps_close_sheets", ok=True, detail={"skipped": True})
        try:
            try:
                app.DisplayAlerts = False
            except Exception:
                pass
            wb = self._last_wb
            if wb is None and getattr(app, "Workbooks", None) and app.Workbooks.Count > 0:
                try:
                    wb = app.ActiveWorkbook
                except Exception:
                    wb = app.Workbooks(int(app.Workbooks.Count))
            if wb is not None:
                wb.Close(SaveChanges=save)
            self._last_wb = None
            should_quit = int(app.Workbooks.Count) == 0 and (quit_app or self._sheets_owned)
            if should_quit:
                app.Quit()
                self._sheets = None
                self._sheets_owned = False
        except Exception as e:
            raise AdapterUnavailable(f"WPS close sheets failed: {e}") from e
        return ActionResult(action="wps_close_sheets", ok=True, detail={})

    def close_writer(self, *, save: bool = False, quit_app: bool = False) -> ActionResult:
        app = self._writer
        if app is None:
            return ActionResult(action="wps_close_writer", ok=True, detail={"skipped": True})
        try:
            try:
                app.DisplayAlerts = 0
            except Exception:
                pass
            if getattr(app, "Documents", None) and app.Documents.Count > 0:
                app.ActiveDocument.Close(SaveChanges=save)
            should_quit = app.Documents.Count == 0 and (quit_app or self._writer_owned)
            if should_quit:
                app.Quit()
                self._writer = None
                self._writer_owned = False
        except Exception as e:
            raise AdapterUnavailable(f"WPS close writer failed: {e}") from e
        return ActionResult(action="wps_close_writer", ok=True, detail={})

    def _ensure_sheets(self, *, allow_dispatch: bool):
        if self._sheets is not None:
            return self._sheets
        result = self._try_com(self.SHEETS_CANDIDATES, allow_dispatch=allow_dispatch, kind="sheets")
        if not result.get("ok"):
            raise AdapterUnavailable(
                "WPS Sheets COM unavailable; fall back to UIA. " + str(result.get("error"))
            )
        return self._sheets

    def _ensure_writer(self, *, allow_dispatch: bool):
        if self._writer is not None:
            return self._writer
        result = self._try_com(self.WRITER_CANDIDATES, allow_dispatch=allow_dispatch, kind="writer")
        if not result.get("ok"):
            raise AdapterUnavailable(
                "WPS Writer COM unavailable; fall back to UIA. " + str(result.get("error"))
            )
        return self._writer

    @staticmethod
    def _running_wps_processes() -> list[str]:
        names = {"wps.exe", "et.exe", "wpp.exe", "wpscloud.exe"}
        found: list[str] = []
        try:
            import subprocess

            out = subprocess.check_output(
                ["tasklist", "/FO", "CSV", "/NH"],
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
            for line in out.splitlines():
                parts = line.strip().strip('"').split('","')
                if not parts:
                    continue
                proc = parts[0].lower()
                if proc in names and proc not in found:
                    found.append(proc)
        except Exception:
            pass
        return found
