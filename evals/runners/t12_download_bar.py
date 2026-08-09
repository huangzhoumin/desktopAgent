"""T12 Download bar / Save As shell: browser click + browser_download_bar UIA."""

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
from desktop_agent.adapters.notepad import NotepadAdapter  # noqa: E402
from desktop_agent.config import load_config  # noqa: E402
from desktop_agent.memory.trace import TraceStore  # noqa: E402
from desktop_agent.tools.runtime import ToolRuntime  # noqa: E402

HTML = ROOT / "evals" / "tasks" / "t07_download.html"
MARKER = "DesktopAgent-T07-你好-Hello-12345"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="T12 browser download bar / Save As shell (no LLM)"
    )
    parser.add_argument("--html", type=Path, default=HTML)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    html = args.html.resolve()
    if not html.exists():
        print(f"[FAIL] HTML fixture missing: {html}")
        return 1

    out = (args.out or Path(tempfile.gettempdir()) / "desktop-agent-t12-saved.txt").resolve()
    shell_out = (Path(tempfile.gettempdir()) / "desktop-agent-t12-shell.txt").resolve()
    url = html.as_uri()
    cfg = load_config()
    cfg.browser.mode = "controlled"
    cfg.browser.fallback_to_controlled = True
    cfg.browser.controlled_channel = "msedge"

    trace = TraceStore(cfg.traces_dir, task_id=f"t12_{int(time.time())}")
    rt = ToolRuntime(cfg, trace=trace)
    steps: list[dict] = []
    step = make_stepper(trace, steps)
    np = NotepadAdapter()

    ok = False
    try:
        if out.exists():
            out.unlink()
        if shell_out.exists():
            shell_out.unlink()

        step("navigate", lambda: rt.call("browser_navigate", url=url))
        # Click without Playwright expect_download — may surface shelf / auto-download.
        step(
            "click_download",
            lambda: rt.call("browser_click", locator={"css": "#download-btn"}),
        )
        time.sleep(0.6)

        try:
            # Shelf-only first: don't blast Save As shortcuts into the browser.
            step(
                "browser_download_bar",
                lambda: rt.call(
                    "browser_download_bar",
                    action="save",
                    path=str(out),
                    timeout_s=4.0,
                    open_if_needed=False,
                ),
            )
        except Exception as e:
            steps.append(
                {
                    "step": "browser_download_bar",
                    "ok": False,
                    "ms": 0,
                    "error": str(e),
                    "soft": True,
                }
            )
            print(f"[SOFT] browser_download_bar: {e}")

        if out.exists() and MARKER in out.read_text(encoding="utf-8", errors="replace"):
            text = out.read_text(encoding="utf-8", errors="replace")
            step(
                "verify_download_file",
                lambda: {
                    "ok": True,
                    "path": str(out),
                    "content": text[:200],
                },
            )
        else:
            # Deterministic shell path: Notepad Save As via the same UIA helper.
            # (Controlled Edge often auto-accepts downloads without a shelf.)
            step("notepad_launch", np.launch)
            step("notepad_type", lambda: np.type_text(MARKER, clear=True))
            step("focus_notepad", np.focus)
            time.sleep(0.25)
            step(
                "shell_save_via_download_bar",
                lambda: rt.call(
                    "browser_download_bar",
                    action="save",
                    path=str(shell_out),
                    timeout_s=8.0,
                ),
            )
            text = shell_out.read_text(encoding="utf-8", errors="replace")
            step(
                "verify_shell_file",
                lambda: {
                    "ok": MARKER in text and shell_out.exists(),
                    "path": str(shell_out),
                    "content": text,
                },
            )
            if MARKER not in text:
                raise RuntimeError(f"Shell save content mismatch: {text!r}")

        ok = True
    except Exception as e:
        print(f"[FAIL] {e}")
        ok = False
    finally:
        try:
            rt.browser.close()
        except Exception:
            pass
        try:
            np.close(discard=True)
        except Exception:
            pass

    write_report(
        trace.dir,
        "T12_download_bar",
        ok,
        steps,
        out=str(out),
        shell_out=str(shell_out),
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
