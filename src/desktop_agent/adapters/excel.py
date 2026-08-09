from __future__ import annotations

from pathlib import Path

from desktop_agent.errors import AdapterUnavailable
from desktop_agent.models import ActionResult


class ExcelAdapter:
    def __init__(self):
        self._xl = None
        self._owned = False  # True if we Dispatch()'d Excel (may Quit on close)

    def probe(self) -> dict:
        try:
            import win32com.client

            xl = win32com.client.GetActiveObject("Excel.Application")
            count = xl.Workbooks.Count
            return {"ok": True, "workbooks": int(count), "mode": "active"}
        except Exception as e:
            return {"ok": False, "error": str(e), "hint": "Start Excel first, or use excel_open / excel_new"}

    def _get(self):
        if self._xl is not None:
            return self._xl
        try:
            import win32com.client

            self._xl = win32com.client.GetActiveObject("Excel.Application")
            self._owned = False
            return self._xl
        except Exception:
            try:
                import win32com.client

                self._xl = win32com.client.Dispatch("Excel.Application")
                self._xl.Visible = True
                self._owned = True
                return self._xl
            except Exception as e:
                raise AdapterUnavailable(f"Excel COM unavailable: {e}") from e

    def new(self) -> ActionResult:
        xl = self._get()
        xl.Visible = True
        xl.DisplayAlerts = False
        wb = xl.Workbooks.Add()
        return ActionResult(
            action="excel_new",
            ok=True,
            detail={"name": wb.Name, "workbooks": int(xl.Workbooks.Count)},
        )

    def get_range(self, range_addr: str, sheet: str | None = None) -> ActionResult:
        xl = self._get()
        if xl.Workbooks.Count == 0:
            raise AdapterUnavailable("No open workbook")
        wb = xl.ActiveWorkbook
        ws = wb.Worksheets(sheet) if sheet else wb.ActiveSheet
        value = ws.Range(range_addr).Value
        return ActionResult(
            action="excel_get_range",
            ok=True,
            detail={"range": range_addr, "sheet": sheet, "value": value},
        )

    def set_range(self, range_addr: str, value, sheet: str | None = None) -> ActionResult:
        xl = self._get()
        if xl.Workbooks.Count == 0:
            raise AdapterUnavailable("No open workbook")
        wb = xl.ActiveWorkbook
        ws = wb.Worksheets(sheet) if sheet else wb.ActiveSheet
        ws.Range(range_addr).Value = value
        return ActionResult(
            action="excel_set_range",
            ok=True,
            detail={"range": range_addr, "sheet": sheet, "value": value},
        )

    def save(self, path: str | None = None) -> ActionResult:
        if path:
            return self.save_as(path)
        xl = self._get()
        if xl.Workbooks.Count == 0:
            raise AdapterUnavailable("No open workbook")
        xl.ActiveWorkbook.Save()
        return ActionResult(action="excel_save", ok=True, detail={"path": xl.ActiveWorkbook.FullName})

    def save_as(self, path: str) -> ActionResult:
        xl = self._get()
        if xl.Workbooks.Count == 0:
            raise AdapterUnavailable("No open workbook")
        out = Path(path).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        xl.DisplayAlerts = False
        # 51 = xlOpenXMLWorkbook (.xlsx)
        xl.ActiveWorkbook.SaveAs(str(out), FileFormat=51)
        return ActionResult(action="excel_save", ok=True, detail={"path": str(out)})

    def open(self, path: str) -> ActionResult:
        xl = self._get()
        xl.Visible = True
        wb = xl.Workbooks.Open(str(Path(path).resolve()))
        return ActionResult(action="excel_open", ok=True, detail={"path": wb.FullName})

    def close(self, *, save: bool = False, quit_app: bool = False) -> ActionResult:
        xl = self._get()
        try:
            xl.DisplayAlerts = False
            if xl.Workbooks.Count > 0:
                xl.ActiveWorkbook.Close(SaveChanges=save)
            should_quit = xl.Workbooks.Count == 0 and (quit_app or self._owned)
            if should_quit:
                xl.Quit()
                self._xl = None
                self._owned = False
        except Exception as e:
            raise AdapterUnavailable(f"Excel close failed: {e}") from e
        return ActionResult(action="excel_close", ok=True, detail={"quit": should_quit})
