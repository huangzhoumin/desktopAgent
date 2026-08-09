"""Aggregate eval reports into a simple JSON/Markdown dashboard."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from desktop_agent.config import load_config  # noqa: E402

# task_id -> runner script
DEFAULT_SUITE = {
    "T01": "t01_notepad.py",
    "T02": "t02_edge.py",
    "T03": "t03_chrome.py",
    "T04": "t04_excel.py",
    "T05": "t05_word.py",
    "T06": "t06_excel_save_as.py",
    "T07": "t07_edge_download.py",
    "T08": "t08_excel_to_form.py",
    "T09": "t09_wps_sheets.py",
    "T10": "t10_wps_writer.py",
}


def _run_one(script: Path, extra: list[str]) -> dict:
    t0 = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, str(script), *extra],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    ms = int((time.perf_counter() - t0) * 1000)
    ok = proc.returncode == 0
    return {
        "ok": ok,
        "exit_code": proc.returncode,
        "ms": ms,
        "stdout_tail": (proc.stdout or "")[-1200:],
        "stderr_tail": (proc.stderr or "")[-800:],
    }


def _scan_reports(traces_dir: Path) -> list[dict]:
    reports = []
    if not traces_dir.exists():
        return reports
    for path in sorted(traces_dir.glob("*/*_report.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        data["_report_path"] = str(path)
        reports.append(data)
    return reports


def _latest_by_task(reports: list[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for r in reports:
        task = str(r.get("task") or "")
        if task and task not in latest:
            latest[task] = r
    return latest


def main() -> int:
    parser = argparse.ArgumentParser(description="Eval dashboard / suite runner")
    parser.add_argument(
        "--run",
        nargs="*",
        default=None,
        help="Task ids to run (e.g. T01 T04 T06). Default: do not run, only aggregate.",
    )
    parser.add_argument(
        "--suite",
        action="store_true",
        help="Run core suite T01-T08 (skipping unavailable apps as failures).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "evals" / "reports" / "dashboard.json",
        help="Write aggregate JSON here",
    )
    parser.add_argument(
        "--md",
        type=Path,
        default=ROOT / "evals" / "reports" / "dashboard.md",
        help="Write markdown summary here",
    )
    parser.add_argument(
        "--force-controlled",
        action="store_true",
        help="Pass --force-controlled to browser evals when supported",
    )
    args = parser.parse_args()

    cfg = load_config()
    run_ids: list[str] = []
    if args.suite:
        run_ids = ["T01", "T02", "T03", "T04", "T05", "T06", "T07", "T08"]
    elif args.run is not None:
        run_ids = [x.upper() for x in args.run]

    run_results: dict[str, dict] = {}
    for tid in run_ids:
        script_name = DEFAULT_SUITE.get(tid)
        if not script_name:
            run_results[tid] = {"ok": False, "error": "unknown task id"}
            continue
        script = ROOT / "evals" / "runners" / script_name
        extra: list[str] = []
        if args.force_controlled and tid in {"T03", "T07", "T08"}:
            extra.append("--force-controlled")
        print(f"=== Running {tid}: {script.name} ===")
        run_results[tid] = _run_one(script, extra)
        print(f"=== {tid} -> {'OK' if run_results[tid]['ok'] else 'FAIL'} ({run_results[tid]['ms']}ms) ===")

    reports = _scan_reports(cfg.traces_dir)
    latest = _latest_by_task(reports)

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "traces_dir": str(cfg.traces_dir),
        "ran": run_results,
        "latest_reports": {
            k: {
                "ok": v.get("ok"),
                "task": v.get("task"),
                "steps": len(v.get("steps") or []),
                "step_ok": sum(1 for s in (v.get("steps") or []) if s.get("ok")),
                "total_ms": sum(int(s.get("ms") or 0) for s in (v.get("steps") or [])),
                "report": v.get("_report_path"),
            }
            for k, v in latest.items()
        },
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# Desktop Agent Eval Dashboard",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "## Latest reports",
        "",
        "| Task | OK | Steps | Step OK | Total ms | Report |",
        "|---|---|---:|---:|---:|---|",
    ]
    for task, info in sorted(summary["latest_reports"].items()):
        lines.append(
            f"| {task} | {'✅' if info['ok'] else '❌'} | {info['steps']} | "
            f"{info['step_ok']} | {info['total_ms']} | `{info['report']}` |"
        )
    if run_results:
        lines += ["", "## This run", "", "| Task | OK | ms |", "|---|---|---:|"]
        for tid, info in run_results.items():
            lines.append(f"| {tid} | {'✅' if info.get('ok') else '❌'} | {info.get('ms', 0)} |")
    args.md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Wrote {args.out}")
    print(f"Wrote {args.md}")
    if run_results:
        return 0 if all(v.get("ok") for v in run_results.values()) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
