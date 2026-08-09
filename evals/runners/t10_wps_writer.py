"""T10 WPS Writer closed-loop: type text -> save -> verify."""

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

MARKER = "DesktopAgent-T10-你好-Hello-12345"


def main() -> int:
    parser = argparse.ArgumentParser(description="T10 WPS Writer closed-loop verification")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output docx path (default: temp file)",
    )
    parser.add_argument("--keep-open", action="store_true", help="Do not close WPS Writer at end")
    args = parser.parse_args()

    out = args.out or Path(tempfile.gettempdir()) / "desktop-agent-wps-t10.docx"
    cfg = load_config()
    trace = TraceStore(cfg.traces_dir, task_id=f"t10_{int(time.time())}")
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

        step("type_text", lambda: rt.call("wps_type_text", text=MARKER))
        time.sleep(0.2)

        doc_text = rt.wps.read_text()
        step(
            "verify_document",
            lambda: {
                "ok": MARKER in doc_text.replace("\r", ""),
                "document_text": doc_text[:200],
                "expected_substr": MARKER,
            },
        )
        if MARKER not in doc_text.replace("\r", ""):
            raise RuntimeError(f"Document text mismatch: {doc_text!r}")

        step("save", lambda: rt.call("wps_save_document", path=str(out)))
        step(
            "verify_file",
            lambda: {
                "ok": out.exists() and out.stat().st_size > 0,
                "path": str(out),
                "size": out.stat().st_size if out.exists() else 0,
            },
        )
        if not out.exists() or out.stat().st_size <= 0:
            raise RuntimeError(f"Output file missing or empty: {out}")

        ok = True
    except Exception:
        ok = False
    finally:
        if not args.keep_open:
            try:
                rt.wps.close_writer(save=False, quit_app=True)
            except Exception:
                pass

    report = {
        "task": "T10_wps_writer",
        "ok": ok,
        "out": str(out),
        "trace": str(trace.dir),
        "steps": steps,
    }
    report_path = trace.dir / "t10_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("---")
    print(json.dumps({"ok": ok, "out": str(out), "report": str(report_path)}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
