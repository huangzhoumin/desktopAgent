"""T09 WPS Sheets closed-loop: new -> write cell -> save -> verify."""

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

MARKER = "DesktopAgent-T09-你好-Hello-12345"


def _normalize(value):
    if isinstance(value, (list, tuple)):
        if len(value) == 1:
            return _normalize(value[0])
        return [_normalize(v) for v in value]
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="T09 WPS Sheets closed-loop verification")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output xlsx path (default: temp file)",
    )
    parser.add_argument("--keep-open", action="store_true", help="Do not close WPS at end")
    args = parser.parse_args()

    out = args.out or Path(tempfile.gettempdir()) / "desktop-agent-wps-t09.xlsx"
    cfg = load_config()
    trace = TraceStore(cfg.traces_dir, task_id=f"t09_{int(time.time())}")
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

        probe = step("wps_probe", lambda: rt.call("wps_probe"))
        if not (probe.get("sheets") or {}).get("ok") and not probe.get("ok"):
            # allow dispatch path in later steps; probe is informational
            print("  note: WPS not active yet; will try Dispatch")

        step("new", lambda: rt.call("wps_new"))
        step("set_cell", lambda: rt.call("wps_set_cell", **{"range": "A1", "value": MARKER}))
        step("save", lambda: rt.call("wps_save", path=str(out)))

        live = step("verify_live", lambda: rt.call("wps_get_cell", **{"range": "A1"}))
        live_val = _normalize(live.get("detail", {}).get("value"))
        if live_val != MARKER:
            raise RuntimeError(f"Live cell mismatch: {live_val!r}")
        if not out.exists():
            raise RuntimeError(f"Output file missing: {out}")

        ok = True
    except Exception:
        ok = False
    finally:
        if not args.keep_open:
            try:
                rt.wps.close_sheets(save=False, quit_app=True)
            except Exception:
                pass

    report = {
        "task": "T09_wps_sheets",
        "ok": ok,
        "out": str(out),
        "trace": str(trace.dir),
        "steps": steps,
    }
    report_path = trace.dir / "t09_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("---")
    print(json.dumps({"ok": ok, "out": str(out), "report": str(report_path)}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
