"""T06 Excel: open existing workbook -> modify cell -> Save As new file -> verify."""

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

SEED = [["T06", "seed"], ["keep", 1]]
NEW_B2 = 2026


def main() -> int:
    parser = argparse.ArgumentParser(description="T06 Excel open/modify/save-as verification")
    parser.add_argument("--src", type=Path, default=None, help="Seed workbook path")
    parser.add_argument("--out", type=Path, default=None, help="Save-as destination")
    parser.add_argument("--keep-open", action="store_true")
    args = parser.parse_args()

    tmp = Path(tempfile.gettempdir())
    src = args.src or tmp / "desktop-agent-excel-t06-src.xlsx"
    out = args.out or tmp / "desktop-agent-excel-t06-out.xlsx"

    cfg = load_config()
    trace = TraceStore(cfg.traces_dir, task_id=f"t06_{int(time.time())}")
    rt = ToolRuntime(cfg, trace=trace)
    steps: list[dict] = []
    step = make_stepper(trace, steps)

    ok = False
    try:
        for p in (src, out):
            if p.exists():
                p.unlink()

        step("seed_new", lambda: rt.call("excel_new"))
        step("seed_values", lambda: rt.call("excel_set_range", **{"range": "A1:B2", "value": SEED}))
        step("seed_save", lambda: rt.call("excel_save", path=str(src)))
        step("seed_close", lambda: rt.excel.close(save=False, quit_app=False))

        step("open", lambda: rt.call("excel_open", path=str(src)))
        step("set_b2", lambda: rt.call("excel_set_range", **{"range": "B2", "value": NEW_B2}))
        step("save_as", lambda: rt.call("excel_save", path=str(out)))

        live = step("verify_live_b2", lambda: rt.call("excel_get_range", **{"range": "B2"}))
        live_val = normalize_excel_value(live.get("detail", {}).get("value"))
        if live_val != NEW_B2:
            raise RuntimeError(f"Live B2 mismatch: {live_val!r} != {NEW_B2!r}")

        if not args.keep_open:
            step("close_before_reopen", lambda: rt.excel.close(save=False, quit_app=False))

        step("reopen_out", lambda: rt.call("excel_open", path=str(out)))
        disk = step("verify_disk_b2", lambda: rt.call("excel_get_range", **{"range": "B2"}))
        disk_val = normalize_excel_value(disk.get("detail", {}).get("value"))
        if disk_val != NEW_B2:
            raise RuntimeError(f"Disk B2 mismatch: {disk_val!r} != {NEW_B2!r}")
        if not out.exists():
            raise RuntimeError(f"Output missing: {out}")
        ok = True
    except Exception:
        ok = False
    finally:
        if not args.keep_open:
            try:
                rt.excel.close(save=False, quit_app=True)
            except Exception:
                pass

    write_report(trace.dir, "T06_excel_save_as", ok, steps, src=str(src), out=str(out))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
