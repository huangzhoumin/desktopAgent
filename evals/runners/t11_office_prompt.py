"""T11 Office prompt: dirty Excel + Alt+F4 -> save to local path via More options."""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import make_stepper, write_report  # noqa: E402
from desktop_agent.common.win32_window import force_foreground  # noqa: E402
from desktop_agent.config import load_config  # noqa: E402
from desktop_agent.memory.trace import TraceStore  # noqa: E402
from desktop_agent.tools.runtime import ToolRuntime  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="T11 Office prompt local save via dialog_click_button"
    )
    parser.add_argument("--keep-open", action="store_true")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Local xlsx path (default: temp file)",
    )
    parser.add_argument(
        "--discard",
        action="store_true",
        help="Click Don't Save instead of saving locally",
    )
    args = parser.parse_args()

    out = args.out or Path(tempfile.gettempdir()) / "desktop-agent-t11.xlsx"
    out = out.resolve()
    if out.exists():
        out.unlink()

    cfg = load_config()
    trace = TraceStore(cfg.traces_dir, task_id=f"t11_{int(time.time())}")
    rt = ToolRuntime(cfg, trace=trace)
    steps: list[dict] = []
    step = make_stepper(trace, steps)

    ok = False
    try:
        # Clear leftover workbooks / prompts from prior runs.
        try:
            xl0 = rt.excel._get()
            xl0.DisplayAlerts = False
            while int(xl0.Workbooks.Count) > 0:
                xl0.ActiveWorkbook.Close(SaveChanges=False)
            try:
                rt.call("dialog_click_button", action="no", timeout_s=1.0)
            except Exception:
                pass
        except Exception:
            pass

        step("excel_new", lambda: rt.call("excel_new"))
        step(
            "set_cell",
            lambda: rt.call(
                "excel_set_range", **{"range": "A1", "value": "DesktopAgent-T11"}
            ),
        )

        xl = rt.excel._get()
        xl.DisplayAlerts = True
        xl.Visible = True
        hwnd = int(getattr(xl, "Hwnd", 0) or 0)
        step(
            "force_foreground_excel",
            lambda: {"ok": bool(hwnd) and force_foreground(hwnd), "hwnd": hwnd},
        )
        time.sleep(0.35)

        # Close via UI so Excel shows the modern save prompt (often defaults to OneDrive).
        step("alt_f4", lambda: rt.call("press_keys", keys=["alt", "f4"]))
        time.sleep(0.7)

        prompt = rt.dialogs.find_prompt_dialog(title_contains="Excel")
        if prompt is None:
            prompt = rt.dialogs.find_prompt_dialog()

        if prompt is None:
            raise RuntimeError(
                "Excel save prompt not found after Alt+F4 "
                "(ensure DisplayAlerts=True and workbook is dirty)"
            )

        if args.discard:
            step(
                "dialog_click_dont_save",
                lambda: rt.call(
                    "dialog_click_button",
                    action="no",
                    timeout_s=6.0,
                ),
            )
        else:
            # Prefer More options -> classic Save As with an explicit local path.
            step(
                "dialog_save_local",
                lambda: rt.call(
                    "dialog_click_button",
                    action="save",
                    path=str(out),
                    timeout_s=10.0,
                ),
            )
            time.sleep(0.3)
            step(
                "verify_local_file",
                lambda: rt.call("verify_file", path=str(out), min_bytes=1),
            )

        time.sleep(0.4)
        prompt = rt.dialogs.find_prompt_dialog(title_contains="Excel")
        if prompt is None:
            prompt = rt.dialogs.find_prompt_dialog()
        step(
            "verify_prompt_gone",
            lambda: {"ok": prompt is None, "prompt_still_visible": prompt is not None},
        )
        if prompt is not None:
            raise RuntimeError(
                "Office save prompt still visible after dialog_click_button"
            )

        try:
            wb_count = int(rt.excel._get().Workbooks.Count)
        except Exception:
            wb_count = 0
        # After local save+close, workbook should be gone; after discard too.
        step(
            "verify_workbook_closed",
            lambda: {"ok": wb_count == 0, "workbooks": wb_count},
        )
        if wb_count != 0:
            raise RuntimeError(f"Workbook still open after prompt handling: {wb_count}")

        ok = True
    except Exception as e:
        print(f"[FAIL] {e}")
        ok = False
    finally:
        if not args.keep_open:
            try:
                xl = rt.excel._get()
                xl.DisplayAlerts = False
                rt.excel.close(save=False, quit_app=True)
            except Exception:
                pass

    write_report(
        trace.dir,
        "T11_office_prompt",
        ok,
        steps,
        out=str(out),
        mode="discard" if args.discard else "local_save",
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
