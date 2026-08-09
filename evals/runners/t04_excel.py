"""T04 Excel closed-loop: new workbook -> write A1:B2 -> save -> verify."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from desktop_agent.config import load_config  # noqa: E402
from desktop_agent.memory.trace import TraceStore  # noqa: E402
from desktop_agent.tools.runtime import ToolRuntime  # noqa: E402

VALUES = [["T04", "你好"], ["Hello", 12345]]


def _normalize(value):
    """COM may return tuples / floats; normalize for comparison."""
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="T04 Excel closed-loop verification")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output xlsx path (default: temp file)",
    )
    parser.add_argument("--keep-open", action="store_true", help="Do not close Excel workbook at end")
    args = parser.parse_args()

    out = args.out or Path(tempfile.gettempdir()) / "desktop-agent-excel-t04.xlsx"
    cfg = load_config()
    trace = TraceStore(cfg.traces_dir, task_id=f"t04_{int(time.time())}")
    rt = ToolRuntime(cfg, trace=trace)

    steps: list[dict] = []

    def step(name: str, fn):
        t0 = time.perf_counter()
        try:
            result = fn()
            payload = result.to_dict() if hasattr(result, "to_dict") else result
            item = {
                "step": name,
                "ok": True if payload.get("ok", True) else False,
                "ms": int((time.perf_counter() - t0) * 1000),
                "result": payload,
            }
            steps.append(item)
            trace.log("eval_step", item)
            if not item["ok"]:
                raise RuntimeError(payload.get("error") or payload)
            print(f"[OK] {name} ({item['ms']}ms)")
            return payload
        except Exception as e:
            item = {
                "step": name,
                "ok": False,
                "ms": int((time.perf_counter() - t0) * 1000),
                "error": str(e),
            }
            steps.append(item)
            trace.log("eval_step", item)
            print(f"[FAIL] {name}: {e}")
            raise

    ok = False
    try:
        if out.exists():
            out.unlink()

        step("new", lambda: rt.call("excel_new"))
        step("set_range", lambda: rt.call("excel_set_range", **{"range": "A1:B2", "value": VALUES}))
        step("save", lambda: rt.call("excel_save", path=str(out)))

        live = step("verify_live", lambda: rt.call("excel_get_range", **{"range": "A1:B2"}))
        live_val = _normalize(live.get("detail", {}).get("value"))
        if live_val != VALUES:
            raise RuntimeError(f"Live range mismatch: {live_val!r} != {VALUES!r}")

        if not args.keep_open:
            step("close_before_reopen", lambda: rt.excel.close(save=False, quit_app=False))

        step("reopen", lambda: rt.call("excel_open", path=str(out)))
        disk = step("verify_disk", lambda: rt.call("excel_get_range", **{"range": "A1:B2"}))
        disk_val = _normalize(disk.get("detail", {}).get("value"))
        if disk_val != VALUES:
            raise RuntimeError(f"Disk range mismatch: {disk_val!r} != {VALUES!r}")
        if not out.exists():
            raise RuntimeError(f"Output file missing: {out}")

        ok = True
    except Exception:
        ok = False
    finally:
        if not args.keep_open:
            try:
                rt.excel.close(save=False, quit_app=True)
            except Exception:
                pass

    report = {
        "task": "T04_excel",
        "ok": ok,
        "out": str(out),
        "trace": str(trace.dir),
        "steps": steps,
    }
    report_path = trace.dir / "t04_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("---")
    print(json.dumps({"ok": ok, "out": str(out), "report": str(report_path)}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
