"""LLM e2e T04 Excel: new workbook -> write A1:B2 -> save -> verify on disk."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _llm_common import (  # noqa: E402
    EXCEL_TOOLS,
    ensure_llm,
    normalize_excel_value,
    quit_office_apps,
    run_llm_goal,
    write_llm_report,
)
from desktop_agent.config import load_config  # noqa: E402
from desktop_agent.tools.runtime import ToolRuntime  # noqa: E402
from desktop_agent.memory.trace import TraceStore  # noqa: E402

VALUES = [["LLM-T04", "你好"], ["Hello", 12345]]


def build_goal(out: Path) -> str:
    # Forward slashes avoid JSON backslash escaping confusion for local LLMs.
    path = out.as_posix()
    return (
        "只用 excel_* COM（不要点单元格 UI；不要传 sheet=工作簿名）：\n"
        "1) excel_new 一次\n"
        f"2) excel_set_range range=A1:B2 value={json.dumps(VALUES, ensure_ascii=False)}\n"
        f"3) excel_save path={path}\n"
        "4) excel_get_range range=A1:B2 校验后 done。\n"
        "保存失败就再 excel_save 同一路径；未落盘不要 done。"
    )


def _verify_disk(path: Path) -> tuple[bool, object]:
    if not path.exists() or path.stat().st_size <= 0:
        return False, None
    cfg = load_config()
    rt = ToolRuntime(cfg, trace=TraceStore(cfg.traces_dir, task_id="llm_t04_verify"))
    try:
        rt.call("excel_open", path=str(path))
        got = rt.call("excel_get_range", **{"range": "A1:B2"})
        val = normalize_excel_value((got.get("detail") or {}).get("value"))
        return val == VALUES, val
    except Exception as e:
        return False, str(e)
    finally:
        try:
            rt.excel.close(save=False, quit_app=True)
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM e2e T04 Excel write+save")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--max-steps", type=int, default=10)
    parser.add_argument("--yes", action="store_true", default=True)
    parser.add_argument("--no-yes", action="store_true")
    parser.add_argument("--keep-open", action="store_true")
    args = parser.parse_args()
    auto_yes = bool(args.yes) and not args.no_yes

    out = (args.out or Path(tempfile.gettempdir()) / "desktop-agent-llm-t04.xlsx").resolve()
    if out.exists():
        out.unlink()
    prep = quit_office_apps(excel=True)
    print(f"[prep] office={prep}", flush=True)

    bad = ensure_llm(load_config())
    if bad:
        print(json.dumps({**bad, "out": str(out)}, ensure_ascii=False))
        return 2

    orch, summary, elapsed_ms = run_llm_goal(
        build_goal(out),
        max_steps=args.max_steps,
        auto_yes=auto_yes,
        allowed_tools=EXCEL_TOOLS,
    )

    if not args.keep_open:
        try:
            orch.runtime.excel.close(save=False, quit_app=True)
        except Exception:
            pass

    disk_ok, disk_val = _verify_disk(out)
    ok = bool(disk_ok)
    report = {
        "task": "LLM_T04_excel",
        "ok": ok,
        "out": str(out),
        "expected": VALUES,
        "disk_value": disk_val,
        "llm_success": bool(summary.success),
        "llm_summary": summary.summary,
        "llm_steps": summary.steps,
        "elapsed_ms": elapsed_ms,
        "disk_exists": out.exists(),
        "trace": str(orch.runtime.trace.dir),
        "note": "ok requires reopened workbook A1:B2 to match expected values.",
    }
    write_llm_report(orch.runtime.trace.dir, "llm_t04", report)
    if not ok and summary.success:
        print("[WARN] LLM claimed success but disk workbook values mismatch.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
