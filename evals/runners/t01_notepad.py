"""T01 Notepad closed-loop: launch -> type -> save as -> verify file content."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from desktop_agent.adapters.notepad import NotepadAdapter  # noqa: E402
from desktop_agent.config import load_config  # noqa: E402
from desktop_agent.memory.trace import TraceStore  # noqa: E402
from desktop_agent.tools.runtime import ToolRuntime  # noqa: E402

MARKER = "DesktopAgent-T01-你好-Hello-12345"


def main() -> int:
    parser = argparse.ArgumentParser(description="T01 Notepad closed-loop verification")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output txt path (default: temp file)",
    )
    parser.add_argument("--keep-open", action="store_true", help="Do not close Notepad at end")
    args = parser.parse_args()

    out = args.out or Path(tempfile.gettempdir()) / "desktop-agent-notepad-t01.txt"
    cfg = load_config()
    trace = TraceStore(cfg.traces_dir, task_id=f"t01_{int(time.time())}")
    rt = ToolRuntime(cfg, trace=trace)
    np = NotepadAdapter()

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
        # Clean previous file
        if out.exists():
            out.unlink()

        launch_result = step("launch", np.launch)
        print(
            "  launch detail:",
            {
                "hwnd": launch_result.get("detail", {}).get("hwnd"),
                "bounds": launch_result.get("detail", {}).get("bounds"),
                "monitors": launch_result.get("detail", {}).get("monitors"),
                "primary": launch_result.get("detail", {}).get("primary"),
            },
        )
        time.sleep(0.3)

        # Sense via tool runtime (also validates whitelist path)
        step("sense", lambda: rt.call("get_ui_summary", max_elements=40))

        step("type_text", lambda: np.type_text(MARKER, clear=True))
        time.sleep(0.2)

        editor_text = np.read_editor_text()
        step(
            "verify_editor",
            lambda: {
                "ok": MARKER in editor_text,
                "editor_text": editor_text,
                "expected_substr": MARKER,
            },
        )
        if MARKER not in editor_text:
            raise RuntimeError(f"Editor text mismatch: {editor_text!r}")

        step("save_as", lambda: np.save_as(out))

        disk = out.read_text(encoding="utf-8", errors="replace")
        step(
            "verify_file",
            lambda: {
                "ok": MARKER in disk and out.exists(),
                "path": str(out),
                "content": disk,
            },
        )
        if MARKER not in disk:
            raise RuntimeError(f"File content mismatch: {disk!r}")

        ok = True
    except Exception:
        ok = False
    finally:
        if not args.keep_open:
            try:
                np.close(discard=True)
            except Exception:
                pass

    report = {
        "task": "T01_notepad",
        "ok": ok,
        "out": str(out),
        "trace": str(trace.dir),
        "steps": steps,
    }
    report_path = trace.dir / "t01_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("---")
    print(json.dumps({"ok": ok, "out": str(out), "report": str(report_path)}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
