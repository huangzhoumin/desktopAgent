"""T02 Edge attach closed-loop: open local form -> fill 3 fields -> Preview."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from desktop_agent.config import load_config  # noqa: E402
from desktop_agent.memory.trace import TraceStore  # noqa: E402
from desktop_agent.tools.runtime import ToolRuntime  # noqa: E402

FORM_HTML = ROOT / "evals" / "tasks" / "t02_form.html"
NAME = "DesktopAgent T02"
EMAIL = "t02@example.com"
MESSAGE = "你好-Hello-12345"


def main() -> int:
    parser = argparse.ArgumentParser(description="T02 Edge form closed-loop verification")
    parser.add_argument(
        "--html",
        type=Path,
        default=FORM_HTML,
        help="Local HTML form path",
    )
    args = parser.parse_args()

    html = args.html.resolve()
    if not html.exists():
        print(f"[FAIL] HTML fixture missing: {html}")
        return 1

    url = html.as_uri()
    cfg = load_config()
    cfg.browser.fallback_to_controlled = True
    cfg.browser.controlled_channel = "msedge"
    trace = TraceStore(cfg.traces_dir, task_id=f"t02_{int(time.time())}")
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
        # Soft probe: ok=false is acceptable when mode A fallback is enabled.
        t0 = time.perf_counter()
        probe = rt.call("browser_probe")
        probe_item = {
            "step": "browser_probe",
            "ok": True,
            "ms": int((time.perf_counter() - t0) * 1000),
            "result": probe,
        }
        steps.append(probe_item)
        trace.log("eval_step", probe_item)
        print(f"[OK] browser_probe ({probe_item['ms']}ms) attach={probe.get('ok')}")
        if not probe.get("ok") and not cfg.browser.fallback_to_controlled:
            raise RuntimeError(
                f"Browser CDP not reachable at {probe.get('endpoint')}. "
                "Run scripts/start-browser-debug.ps1 first."
            )
        if not probe.get("ok"):
            print("  CDP attach unavailable; navigate will use controlled browser (mode A).")

        step("navigate", lambda: rt.call("browser_navigate", url=url))
        step("fill_name", lambda: rt.call("browser_fill", locator={"css": "#name"}, value=NAME))
        step("fill_email", lambda: rt.call("browser_fill", locator={"css": "#email"}, value=EMAIL))
        step(
            "fill_message",
            lambda: rt.call("browser_fill", locator={"css": "#message"}, value=MESSAGE),
        )
        step("click_preview", lambda: rt.call("browser_click", locator={"css": "#preview-btn"}))
        time.sleep(0.2)

        snap = step("snapshot", lambda: rt.call("browser_snapshot"))
        elements = snap.get("elements") or []
        preview_text = ""
        # Prefer reading preview node via page evaluate through adapter
        try:
            ctx = rt.browser._ensure()
            page = ctx.pages[0]
            preview_text = page.locator("#preview").inner_text()
        except Exception as e:
            preview_text = json.dumps(elements, ensure_ascii=False)
            print(f"  preview via snapshot fallback: {e}")

        step(
            "verify_preview",
            lambda: {
                "ok": NAME in preview_text and EMAIL in preview_text and MESSAGE in preview_text,
                "preview": preview_text,
            },
        )
        if not (NAME in preview_text and EMAIL in preview_text and MESSAGE in preview_text):
            raise RuntimeError(f"Preview mismatch: {preview_text!r}")

        ok = True
    except Exception:
        ok = False
    finally:
        try:
            rt.browser.close()
        except Exception:
            pass

    report = {
        "task": "T02_edge",
        "ok": ok,
        "html": str(html),
        "url": url,
        "trace": str(trace.dir),
        "steps": steps,
    }
    report_path = trace.dir / "t02_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("---")
    print(json.dumps({"ok": ok, "html": str(html), "report": str(report_path)}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
