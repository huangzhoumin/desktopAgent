"""LLM e2e T01 Notepad: open -> type marker -> Save As path -> verify file on disk.

Success is judged by the saved file contents, NOT by the model merely opening
the Save As dialog or calling done.
"""

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
from desktop_agent.orchestrator import Orchestrator  # noqa: E402

MARKER = "DesktopAgent-LLM-T01-你好-Hello-12345"


def build_goal(out: Path, marker: str) -> str:
    return (
        f"打开记事本，清空后输入以下全文（不要改动）：\n{marker}\n"
        f"然后另存为到这个路径（必须真正写入磁盘）：\n{out}\n"
        "保存完成后用工具确认该文件存在且包含上述文本，再结束。"
        "仅仅弹出「另存为」对话框不算完成。"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM e2e T01 Notepad Save As verification")
    parser.add_argument("--out", type=Path, default=None, help="Destination txt path")
    parser.add_argument("--marker", default=MARKER, help="Exact text that must be saved")
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument(
        "--yes",
        action="store_true",
        default=True,
        help="Auto-confirm policy / ask_user prompts (default on)",
    )
    parser.add_argument("--no-yes", action="store_true", help="Disable auto-confirm")
    args = parser.parse_args()
    auto_yes = bool(args.yes) and not args.no_yes

    out = (args.out or Path(tempfile.gettempdir()) / "desktop-agent-llm-t01.txt").resolve()
    marker = str(args.marker)
    if out.exists():
        out.unlink()

    cfg = load_config()
    cfg.runtime.max_steps = int(args.max_steps)
    cfg.llm.max_tool_rounds = int(args.max_steps)

    if not cfg.llm.configured:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "LLM not configured (llm.api_base/model + DESKTOP_AGENT_API_KEY)",
                    "out": str(out),
                },
                ensure_ascii=False,
            )
        )
        return 2

    def ask_user(question: str, options: list[str] | None) -> str:
        print(f"[ask_user] {question}")
        if options:
            print("  options:", ", ".join(options))
        if auto_yes:
            if options:
                for opt in options:
                    low = opt.lower()
                    if low in {"y", "yes", "是", "ok", "true"} or "启动" in opt or "launch" in low:
                        print(f"  auto-answer: {opt}")
                        return opt
                print(f"  auto-answer: {options[0]}")
                return options[0]
            print("  auto-answer: yes")
            return "yes"
        try:
            return input("> ").strip()
        except EOFError:
            return "cancel"

    def confirm(reason: str) -> bool:
        print(f"[confirm] {reason}")
        if auto_yes:
            print("  auto-confirm: yes")
            return True
        try:
            return input("Proceed? [y/N] ").strip().lower() in {"y", "yes", "是", "ok"}
        except EOFError:
            return False

    goal = build_goal(out, marker)
    print(f"[goal]\n{goal}\n")
    orch = Orchestrator.create(cfg, ask_user_fn=ask_user, confirm_fn=confirm)
    t0 = time.perf_counter()
    summary = orch.run(goal)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    disk_ok = False
    content = ""
    if out.exists():
        content = out.read_text(encoding="utf-8", errors="replace")
        disk_ok = marker in content

    # Ground truth is the file. LLM may claim success after only opening Save As.
    ok = bool(disk_ok)
    report = {
        "task": "LLM_T01_notepad",
        "ok": ok,
        "out": str(out),
        "marker": marker,
        "llm_success": bool(summary.success),
        "llm_summary": summary.summary,
        "llm_steps": summary.steps,
        "elapsed_ms": elapsed_ms,
        "disk_exists": out.exists(),
        "disk_contains_marker": disk_ok,
        "disk_preview": content[:200],
        "trace": str(orch.runtime.trace.dir),
        "note": (
            "ok requires the destination file to exist and contain the marker; "
            "seeing Save As alone fails this eval."
        ),
    }
    report_path = orch.runtime.trace.dir / "llm_t01_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("---")
    print(json.dumps({**report, "report": str(report_path)}, ensure_ascii=False, indent=2))
    if not ok and summary.success:
        print(
            "[WARN] LLM called done successfully but file was not saved correctly "
            "(Save As dialog without persistence)."
        )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
