"""T07 Edge download: trigger local download and save with chosen filename."""

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
from desktop_agent.config import load_config  # noqa: E402
from desktop_agent.memory.trace import TraceStore  # noqa: E402
from desktop_agent.tools.runtime import ToolRuntime  # noqa: E402

HTML = ROOT / "evals" / "tasks" / "t07_download.html"
MARKER = "DesktopAgent-T07-你好-Hello-12345"


def main() -> int:
    parser = argparse.ArgumentParser(description="T07 Edge download filename verification")
    parser.add_argument("--html", type=Path, default=HTML)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--force-controlled",
        action="store_true",
        help="Force controlled browser (mode A) so downloads are interceptable",
    )
    args = parser.parse_args()

    html = args.html.resolve()
    if not html.exists():
        print(f"[FAIL] HTML fixture missing: {html}")
        return 1

    out = args.out or Path(tempfile.gettempdir()) / "desktop-agent-t07-saved.txt"
    url = html.as_uri()
    cfg = load_config()
    # Downloads are most reliable under Playwright-managed context (mode A).
    # Default to controlled unless user explicitly wants attach-only.
    cfg.browser.mode = "controlled"
    cfg.browser.fallback_to_controlled = True
    cfg.browser.controlled_channel = "msedge"
    if not args.force_controlled:
        # Still allow attach if already up AND user didn't ask for controlled;
        # keep controlled as default for determinism.
        pass

    trace = TraceStore(cfg.traces_dir, task_id=f"t07_{int(time.time())}")
    rt = ToolRuntime(cfg, trace=trace)
    steps: list[dict] = []
    step = make_stepper(trace, steps)

    ok = False
    try:
        if out.exists():
            out.unlink()

        step("navigate", lambda: rt.call("browser_navigate", url=url))
        dl = step(
            "download",
            lambda: rt.call(
                "browser_download",
                locator={"css": "#download-btn"},
                path=str(out),
                timeout_ms=20000,
            ),
        )
        if not out.exists():
            # Fallback: native Save As dialog (if browser surfaced one).
            step(
                "dialog_save_as_fallback",
                lambda: rt.call("dialog_save_as", path=str(out), timeout_s=6.0),
            )

        text = out.read_text(encoding="utf-8", errors="replace")
        step(
            "verify_file",
            lambda: {
                "ok": MARKER in text and out.exists(),
                "path": str(out),
                "content": text,
                "download_detail": dl.get("detail"),
            },
        )
        if MARKER not in text:
            raise RuntimeError(f"File content mismatch: {text!r}")
        ok = True
    except Exception:
        ok = False
    finally:
        try:
            rt.browser.close()
        except Exception:
            pass

    write_report(trace.dir, "T07_edge_download", ok, steps, html=str(html), out=str(out))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
