"""T08 Mixed: read a value from Excel and fill it into a local web form."""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import make_stepper, normalize_excel_value, write_report  # noqa: E402
from desktop_agent.config import load_config  # noqa: E402
from desktop_agent.memory.trace import TraceStore  # noqa: E402
from desktop_agent.tools.runtime import ToolRuntime  # noqa: E402

FORM_HTML = ROOT / "evals" / "tasks" / "t02_form.html"
CELL_VALUE = "DesktopAgent-T08-从Excel来"


def main() -> int:
    parser = argparse.ArgumentParser(description="T08 Excel -> web form verification")
    parser.add_argument("--html", type=Path, default=FORM_HTML)
    parser.add_argument("--workbook", type=Path, default=None)
    parser.add_argument("--keep-open", action="store_true")
    parser.add_argument("--force-controlled", action="store_true")
    args = parser.parse_args()

    html = args.html.resolve()
    if not html.exists():
        print(f"[FAIL] HTML fixture missing: {html}")
        return 1

    workbook = args.workbook or Path(tempfile.gettempdir()) / "desktop-agent-excel-t08.xlsx"
    url = html.as_uri()
    cfg = load_config()
    if args.force_controlled:
        cfg.browser.mode = "controlled"
    cfg.browser.fallback_to_controlled = True

    trace = TraceStore(cfg.traces_dir, task_id=f"t08_{int(time.time())}")
    rt = ToolRuntime(cfg, trace=trace)
    steps: list[dict] = []
    step = make_stepper(trace, steps)

    ok = False
    try:
        if workbook.exists():
            workbook.unlink()

        step("excel_new", lambda: rt.call("excel_new"))
        step(
            "excel_set_a1",
            lambda: rt.call("excel_set_range", **{"range": "A1", "value": CELL_VALUE}),
        )
        step("excel_save", lambda: rt.call("excel_save", path=str(workbook)))
        got = step("excel_get_a1", lambda: rt.call("excel_get_range", **{"range": "A1"}))
        value = normalize_excel_value(got.get("detail", {}).get("value"))
        if value != CELL_VALUE:
            raise RuntimeError(f"Excel A1 mismatch: {value!r}")

        step("navigate", lambda: rt.call("browser_navigate", url=url))
        step(
            "fill_name_from_excel",
            lambda: rt.call("browser_fill", locator={"css": "#name"}, value=str(value)),
        )
        step(
            "fill_email",
            lambda: rt.call("browser_fill", locator={"css": "#email"}, value="t08@example.com"),
        )
        step(
            "fill_message",
            lambda: rt.call("browser_fill", locator={"css": "#message"}, value="from excel"),
        )
        step("preview", lambda: rt.call("browser_click", locator={"css": "#preview-btn"}))
        time.sleep(0.2)

        ctx = rt.browser._ensure()
        preview = ctx.pages[0].locator("#preview").inner_text()
        step(
            "verify_preview",
            lambda: {"ok": CELL_VALUE in preview, "preview": preview},
        )
        if CELL_VALUE not in preview:
            raise RuntimeError(f"Preview missing excel value: {preview!r}")
        ok = True
    except Exception:
        ok = False
    finally:
        if not args.keep_open:
            try:
                rt.excel.close(save=False, quit_app=True)
            except Exception:
                pass
        try:
            rt.browser.close()
        except Exception:
            pass

    write_report(
        trace.dir,
        "T08_excel_to_form",
        ok,
        steps,
        html=str(html),
        workbook=str(workbook),
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
