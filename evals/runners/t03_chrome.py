"""T03 Chrome form fill — same fixture as T02, prefers Chrome / controlled channel."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import make_stepper, write_report  # noqa: E402
from desktop_agent.config import load_config  # noqa: E402
from desktop_agent.memory.trace import TraceStore  # noqa: E402
from desktop_agent.tools.runtime import ToolRuntime  # noqa: E402

FORM_HTML = ROOT / "evals" / "tasks" / "t02_form.html"
NAME = "DesktopAgent T03"
EMAIL = "t03@example.com"
MESSAGE = "你好-Hello-Chrome-12345"


def main() -> int:
    parser = argparse.ArgumentParser(description="T03 Chrome form closed-loop verification")
    parser.add_argument("--html", type=Path, default=FORM_HTML)
    parser.add_argument(
        "--force-controlled",
        action="store_true",
        help="Force mode A controlled Chrome (ignores existing CDP attach)",
    )
    args = parser.parse_args()

    html = args.html.resolve()
    if not html.exists():
        print(f"[FAIL] HTML fixture missing: {html}")
        return 1

    url = html.as_uri()
    cfg = load_config()
    cfg.browser.controlled_channel = "chrome"
    if args.force_controlled:
        cfg.browser.mode = "controlled"
    else:
        cfg.browser.fallback_to_controlled = True

    trace = TraceStore(cfg.traces_dir, task_id=f"t03_{int(time.time())}")
    rt = ToolRuntime(cfg, trace=trace)
    steps: list[dict] = []
    step = make_stepper(trace, steps)

    ok = False
    try:
        if args.force_controlled:
            cfg.browser.mode = "controlled"
            step(
                "force_controlled",
                lambda: {"ok": True, "mode": "controlled", "channel": "chrome"},
            )
        else:
            # Prefer attach if Chrome CDP is up; otherwise mode A launches Chrome.
            probe = step("browser_probe", lambda: rt.call("browser_probe"))
            if (
                not probe.get("ok")
                and not cfg.browser.fallback_to_controlled
                and cfg.browser.mode != "controlled"
            ):
                raise RuntimeError(
                    f"Browser CDP not reachable at {probe.get('endpoint')}. "
                    "Run scripts/start-browser-debug.ps1 -Browser chrome, or pass --force-controlled."
                )

        step("navigate", lambda: rt.call("browser_navigate", url=url))
        mode = rt.browser.mode or "unknown"
        step("fill_name", lambda: rt.call("browser_fill", locator={"css": "#name"}, value=NAME))
        step("fill_email", lambda: rt.call("browser_fill", locator={"css": "#email"}, value=EMAIL))
        step(
            "fill_message",
            lambda: rt.call("browser_fill", locator={"css": "#message"}, value=MESSAGE),
        )
        step("click_preview", lambda: rt.call("browser_click", locator={"css": "#preview-btn"}))
        time.sleep(0.2)

        ctx = rt.browser._ensure()
        page = ctx.pages[0]
        preview_text = page.locator("#preview").inner_text()
        step(
            "verify_preview",
            lambda: {
                "ok": NAME in preview_text and EMAIL in preview_text and MESSAGE in preview_text,
                "preview": preview_text,
                "mode": mode,
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

    write_report(trace.dir, "T03_chrome", ok, steps, html=str(html), url=url)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
