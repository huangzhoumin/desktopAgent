"""LLM e2e T02 Edge: open local form -> fill 3 fields -> Preview -> verify DOM."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _llm_common import (  # noqa: E402
    BROWSER_TOOLS,
    ensure_llm,
    read_browser_preview,
    run_llm_goal,
    write_llm_report,
)
from desktop_agent.config import load_config  # noqa: E402

FORM_HTML = ROOT / "evals" / "tasks" / "t02_form.html"
NAME = "DesktopAgent LLM-T02"
EMAIL = "llm-t02@example.com"
MESSAGE = "你好-Hello-LLM-T02-12345"


def build_goal(url: str) -> str:
    return (
        "不要 launch_app / list_windows。直接用 browser_*：\n"
        f"1) browser_navigate url={url}\n"
        f'2) browser_fill locator={{"css":"#name"}} value={NAME!r}\n'
        f'3) browser_fill locator={{"css":"#email"}} value={EMAIL!r}\n'
        f'4) browser_fill locator={{"css":"#message"}} value={MESSAGE!r}\n'
        '5) browser_click locator={"css":"#preview-btn"}\n'
        "6) browser_snapshot 确认 #preview 含上述三项后 done。\n"
        "不要点外网；不要关浏览器。"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM e2e T02 Edge form fill")
    parser.add_argument("--html", type=Path, default=FORM_HTML)
    parser.add_argument("--max-steps", type=int, default=12)
    parser.add_argument("--yes", action="store_true", default=True)
    parser.add_argument("--no-yes", action="store_true")
    args = parser.parse_args()
    auto_yes = bool(args.yes) and not args.no_yes

    html = args.html.resolve()
    if not html.exists():
        print(json.dumps({"ok": False, "error": f"HTML missing: {html}"}, ensure_ascii=False))
        return 1
    url = html.as_uri()

    cfg_probe = load_config()
    bad = ensure_llm(cfg_probe)
    if bad:
        print(json.dumps(bad, ensure_ascii=False))
        return 2

    def configure(cfg):
        # Stable eval: don't hijack the user's CDP Chrome tabs.
        cfg.browser.mode = "controlled"
        cfg.browser.fallback_to_controlled = True
        cfg.browser.controlled_channel = "msedge"

    orch, summary, elapsed_ms = run_llm_goal(
        build_goal(url),
        max_steps=args.max_steps,
        auto_yes=auto_yes,
        configure=configure,
        allowed_tools=BROWSER_TOOLS,
    )

    preview = ""
    try:
        preview = read_browser_preview(orch)
    finally:
        try:
            orch.runtime.browser.close()
        except Exception:
            pass

    disk_ok = NAME in preview and EMAIL in preview and MESSAGE in preview
    ok = bool(disk_ok)
    report = {
        "task": "LLM_T02_edge",
        "ok": ok,
        "html": str(html),
        "url": url,
        "name": NAME,
        "email": EMAIL,
        "message": MESSAGE,
        "llm_success": bool(summary.success),
        "llm_summary": summary.summary,
        "llm_steps": summary.steps,
        "elapsed_ms": elapsed_ms,
        "preview": preview[:500],
        "preview_ok": disk_ok,
        "trace": str(orch.runtime.trace.dir),
        "note": "ok requires #preview to contain name/email/message after LLM run.",
    }
    write_llm_report(orch.runtime.trace.dir, "llm_t02", report)
    if not ok and summary.success:
        print("[WARN] LLM called done but preview DOM did not contain expected fields.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
