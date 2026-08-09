from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer
from rich import print as rprint
from rich.panel import Panel
from rich.table import Table

from desktop_agent import __version__
from desktop_agent.config import load_config
from desktop_agent.tools.runtime import ToolRuntime

app = typer.Typer(
    name="desktop-agent",
    help="Windows Desktop UI Agent CLI",
    add_completion=False,
    no_args_is_help=True,
)


def _runtime(config_path: Optional[Path] = None) -> ToolRuntime:
    cfg = load_config(config_path)
    return ToolRuntime(cfg)


def _print_json(data) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        print(text)
    except UnicodeEncodeError:
        sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))


@app.command()
def version() -> None:
    """Show version."""
    rprint(f"desktop-agent {__version__}")


@app.command()
def doctor(
    config: Optional[Path] = typer.Option(None, "--config", help="Path to agent.yaml"),
) -> None:
    """Run environment health checks."""
    cfg = load_config(config)
    rt = ToolRuntime(cfg)
    rows: list[tuple[str, str, str]] = []

    # UIA
    try:
        wins = rt.perception.list_windows()
        rows.append(("UIA", "OK", f"{len(wins)} top-level windows"))
    except Exception as e:
        rows.append(("UIA", "FAIL", str(e)))

    # Whitelist
    rows.append(("Whitelist", "OK", f"{len(cfg.whitelist)} apps"))

    # Browser CDP attach / controlled fallback
    status = rt.browser.probe()
    if status.ok:
        rows.append(
            (
                "Browser CDP (mode B)",
                "OK",
                f"{status.version or 'connected'} @ {status.endpoint}; pages={len(status.pages or [])}",
            )
        )
    else:
        detail = f"not reachable at {status.endpoint}."
        if cfg.browser.fallback_to_controlled:
            detail += (
                f" fallback_to_controlled=ON (mode A / {cfg.browser.controlled_channel})"
            )
        else:
            detail += " Run scripts/start-browser-debug.ps1"
        rows.append(("Browser CDP (mode B)", "WARN", detail))
    rows.append(
        (
            "Browser mode A",
            "OK" if cfg.browser.fallback_to_controlled or cfg.browser.mode == "controlled" else "OFF",
            f"mode={cfg.browser.mode}; channel={cfg.browser.controlled_channel}; "
            f"fallback={cfg.browser.fallback_to_controlled}",
        )
    )

    # Excel / Word / WPS
    excel = rt.excel.probe()
    rows.append(("Excel COM", "OK" if excel.get("ok") else "WARN", json.dumps(excel, ensure_ascii=False)))
    word = rt.word.probe()
    rows.append(("Word COM", "OK" if word.get("ok") else "WARN", json.dumps(word, ensure_ascii=False)))
    wps = rt.wps.probe()
    rows.append(("WPS", "OK" if wps.get("ok") else "WARN", json.dumps(wps, ensure_ascii=False)))

    # LLM
    from desktop_agent.planner.llm_client import OpenAICompatibleClient

    llm = OpenAICompatibleClient(cfg.llm).probe()
    if llm.get("ok"):
        rows.append(
            (
                "LLM",
                "OK",
                f"{cfg.llm.model} @ {cfg.llm.api_base}",
            )
        )
    else:
        rows.append(
            (
                "LLM",
                "WARN",
                str(llm.get("error") or "not configured (set llm.* + DESKTOP_AGENT_API_KEY)"),
            )
        )

    # Traces dir
    try:
        cfg.traces_dir.mkdir(parents=True, exist_ok=True)
        rows.append(("Traces", "OK", str(cfg.traces_dir)))
    except Exception as e:
        rows.append(("Traces", "FAIL", str(e)))

    # OCR / VLM fallback
    ocr = rt.ocr.probe()
    if cfg.perception.enable_ocr_fallback:
        rows.append(
            (
                "OCR fallback",
                "OK" if ocr.get("ok") else "WARN",
                (
                    f"engine={ocr.get('engine')}"
                    if ocr.get("ok")
                    else f"{ocr.get('error')}; pip install 'desktop-agent[vision]'"
                ),
            )
        )
    else:
        rows.append(("OCR fallback", "OFF", "perception.enable_ocr_fallback=false"))

    vlm = rt.vlm.probe()
    if cfg.perception.enable_vlm_fallback:
        rows.append(
            (
                "VLM fallback",
                "OK" if vlm.get("ok") else "WARN",
                (
                    f"model={vlm.get('model')} @ {vlm.get('api_base')}"
                    if vlm.get("ok")
                    else str(vlm.get("error") or "unavailable")
                ),
            )
        )
    else:
        rows.append(("VLM fallback", "OFF", "perception.enable_vlm_fallback=false"))

    table = Table(title="desktop-agent doctor")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")
    for name, status_s, detail in rows:
        color = {"OK": "green", "WARN": "yellow", "FAIL": "red"}.get(status_s, "white")
        table.add_row(name, f"[{color}]{status_s}[/]", detail)
    rprint(table)


@app.command("list-windows")
def list_windows(
    app_filter: Optional[str] = typer.Option(None, "--app"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    """List visible top-level windows."""
    rt = _runtime(config)
    _print_json(rt.call("list_windows", app_filter=app_filter))


@app.command()
def sense(
    dump: Optional[Path] = typer.Option(None, "--dump", help="Write JSON snapshot"),
    max_elements: int = typer.Option(80, "--max-elements"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    """Sense foreground window UI tree summary."""
    rt = _runtime(config)
    if dump:
        data = rt.dump_sense(dump)
    else:
        data = rt.call("get_ui_summary", max_elements=max_elements)
    _print_json(data)


@app.command()
def click(
    name: Optional[str] = typer.Option(None, "--name", help="Element name contains/equals"),
    role: Optional[str] = typer.Option(None, "--role"),
    element_id: Optional[str] = typer.Option(None, "--id"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    """Click a UI element found by name/role or element id."""
    rt = _runtime(config)
    target = element_id
    if not target:
        found = rt.call(
            "find_elements",
            query={"text": name, "role": role},
            top_k=1,
        )
        els = found.get("elements") or []
        if not els:
            raise typer.Exit(code=1)
        target = els[0]["element_id"]
        rprint(Panel(f"Clicking {els[0].get('role')} '{els[0].get('name')}' ({target})"))
    _print_json(rt.call("click", target=target))


@app.command("type-text")
def type_text(
    text: str = typer.Argument(...),
    name: Optional[str] = typer.Option(None, "--name"),
    role: Optional[str] = typer.Option("Edit", "--role"),
    element_id: Optional[str] = typer.Option(None, "--id"),
    clear: bool = typer.Option(True, "--clear/--no-clear"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    """Type text into an element or the focused control."""
    rt = _runtime(config)
    target = element_id
    if not target and name:
        found = rt.call(
            "find_elements",
            query={"text": name, "role": role},
            top_k=1,
        )
        els = found.get("elements") or []
        if not els:
            rprint("[red]Element not found[/]")
            raise typer.Exit(code=1)
        target = els[0]["element_id"]
    _print_json(rt.call("type_text", text=text, target=target, clear=clear))


@app.command("press-keys")
def press_keys(
    keys: list[str] = typer.Argument(..., help="e.g. ctrl s"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    """Send key combination."""
    rt = _runtime(config)
    _print_json(rt.call("press_keys", keys=keys))


@app.command("browser-probe")
def browser_probe(
    config: Optional[Path] = typer.Option(None, "--config"),
    no_auto_start: bool = typer.Option(
        False,
        "--no-auto-start",
        help="Do not auto-launch scripts/start-chrome-debug-isolated Chrome when CDP is down.",
    ),
) -> None:
    """Probe CDP attach endpoint (mode B). Auto-starts isolated debug Chrome if needed."""
    rt = _runtime(config)
    _print_json(rt.call("browser_probe", auto_start_isolated=not no_auto_start))


@app.command("browser-open")
def browser_open(
    url: str = typer.Argument(...),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    """Navigate attached browser to URL."""
    rt = _runtime(config)
    _print_json(rt.call("browser_navigate", url=url))


@app.command("excel-set")
def excel_set(
    range_addr: str = typer.Argument(..., metavar="RANGE"),
    value: str = typer.Argument(...),
    sheet: Optional[str] = typer.Option(None, "--sheet"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    """Set Excel range value via COM."""
    rt = _runtime(config)
    _print_json(rt.call("excel_set_range", **{"range": range_addr, "value": value, "sheet": sheet}))


@app.command("excel-get")
def excel_get(
    range_addr: str = typer.Argument(..., metavar="RANGE"),
    sheet: Optional[str] = typer.Option(None, "--sheet"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    """Get Excel range value via COM."""
    rt = _runtime(config)
    _print_json(rt.call("excel_get_range", **{"range": range_addr, "sheet": sheet}))


def _run_eval_script(script_name: str, extra_args: list[str] | None = None) -> None:
    import subprocess
    from desktop_agent.config import ROOT

    script = ROOT / "evals" / "runners" / script_name
    cmd = [sys.executable, str(script), *(extra_args or [])]
    raise typer.Exit(subprocess.call(cmd))


@app.command("eval-t01")
def eval_t01(
    out: Optional[Path] = typer.Option(None, "--out", help="Output txt path"),
    keep_open: bool = typer.Option(False, "--keep-open"),
) -> None:
    """Run T01 Notepad closed-loop verification (no LLM)."""
    args: list[str] = []
    if out:
        args.extend(["--out", str(out)])
    if keep_open:
        args.append("--keep-open")
    _run_eval_script("t01_notepad.py", args)


@app.command("eval-llm-t01")
def eval_llm_t01(
    out: Optional[Path] = typer.Option(None, "--out", help="Output txt path"),
    marker: Optional[str] = typer.Option(None, "--marker", help="Exact text that must be saved"),
    max_steps: int = typer.Option(20, "--max-steps"),
    no_yes: bool = typer.Option(False, "--no-yes", help="Disable auto-confirm"),
) -> None:
    """Run LLM e2e T01 Notepad Save As; pass only if the file exists with marker text."""
    args: list[str] = ["--max-steps", str(max_steps)]
    if out:
        args.extend(["--out", str(out)])
    if marker:
        args.extend(["--marker", marker])
    if no_yes:
        args.append("--no-yes")
    _run_eval_script("llm_t01_notepad.py", args)


@app.command("eval-llm-t02")
def eval_llm_t02(
    html: Optional[Path] = typer.Option(None, "--html", help="Local HTML form path"),
    max_steps: int = typer.Option(12, "--max-steps"),
    no_yes: bool = typer.Option(False, "--no-yes", help="Disable auto-confirm"),
) -> None:
    """Run LLM e2e T02 Edge form fill; pass only if #preview contains expected fields."""
    args: list[str] = ["--max-steps", str(max_steps)]
    if html:
        args.extend(["--html", str(html)])
    if no_yes:
        args.append("--no-yes")
    _run_eval_script("llm_t02_edge.py", args)


@app.command("eval-llm-t03")
def eval_llm_t03(
    html: Optional[Path] = typer.Option(None, "--html", help="Local HTML form path"),
    max_steps: int = typer.Option(12, "--max-steps"),
    no_yes: bool = typer.Option(False, "--no-yes", help="Disable auto-confirm"),
    no_force_controlled: bool = typer.Option(
        False, "--no-force-controlled", help="Allow CDP attach instead of controlled Chrome"
    ),
) -> None:
    """Run LLM e2e T03 Chrome form fill (default controlled mode A)."""
    args: list[str] = ["--max-steps", str(max_steps)]
    if html:
        args.extend(["--html", str(html)])
    if no_yes:
        args.append("--no-yes")
    if no_force_controlled:
        args.append("--no-force-controlled")
    _run_eval_script("llm_t03_chrome.py", args)


@app.command("eval-llm-t04")
def eval_llm_t04(
    out: Optional[Path] = typer.Option(None, "--out", help="Output xlsx path"),
    max_steps: int = typer.Option(10, "--max-steps"),
    no_yes: bool = typer.Option(False, "--no-yes", help="Disable auto-confirm"),
    keep_open: bool = typer.Option(False, "--keep-open"),
) -> None:
    """Run LLM e2e T04 Excel write A1:B2 + save; pass only if disk values match."""
    args: list[str] = ["--max-steps", str(max_steps)]
    if out:
        args.extend(["--out", str(out)])
    if no_yes:
        args.append("--no-yes")
    if keep_open:
        args.append("--keep-open")
    _run_eval_script("llm_t04_excel.py", args)


@app.command("eval-llm-t05")
def eval_llm_t05(
    out: Optional[Path] = typer.Option(None, "--out", help="Output docx path"),
    marker: Optional[str] = typer.Option(None, "--marker"),
    max_steps: int = typer.Option(10, "--max-steps"),
    no_yes: bool = typer.Option(False, "--no-yes", help="Disable auto-confirm"),
    keep_open: bool = typer.Option(False, "--keep-open"),
) -> None:
    """Run LLM e2e T05 Word type + save; pass only if docx contains marker."""
    args: list[str] = ["--max-steps", str(max_steps)]
    if out:
        args.extend(["--out", str(out)])
    if marker:
        args.extend(["--marker", marker])
    if no_yes:
        args.append("--no-yes")
    if keep_open:
        args.append("--keep-open")
    _run_eval_script("llm_t05_word.py", args)


@app.command("eval-t02")
def eval_t02(
    html: Optional[Path] = typer.Option(None, "--html", help="Local HTML form path"),
) -> None:
    """Run T02 Edge attach form fill verification (no LLM). Requires debug Edge."""
    args: list[str] = []
    if html:
        args.extend(["--html", str(html)])
    _run_eval_script("t02_edge.py", args)


@app.command("eval-t03")
def eval_t03(
    html: Optional[Path] = typer.Option(None, "--html", help="Local HTML form path"),
    force_controlled: bool = typer.Option(False, "--force-controlled"),
) -> None:
    """Run T03 Chrome form fill (attach or controlled mode A)."""
    args: list[str] = []
    if html:
        args.extend(["--html", str(html)])
    if force_controlled:
        args.append("--force-controlled")
    _run_eval_script("t03_chrome.py", args)


@app.command("eval-t04")
def eval_t04(
    out: Optional[Path] = typer.Option(None, "--out", help="Output xlsx path"),
    keep_open: bool = typer.Option(False, "--keep-open"),
) -> None:
    """Run T04 Excel write A1:B2 + save verification (no LLM)."""
    args: list[str] = []
    if out:
        args.extend(["--out", str(out)])
    if keep_open:
        args.append("--keep-open")
    _run_eval_script("t04_excel.py", args)


@app.command("eval-t05")
def eval_t05(
    out: Optional[Path] = typer.Option(None, "--out", help="Output docx path"),
    keep_open: bool = typer.Option(False, "--keep-open"),
) -> None:
    """Run T05 Word type + save verification (no LLM)."""
    args: list[str] = []
    if out:
        args.extend(["--out", str(out)])
    if keep_open:
        args.append("--keep-open")
    _run_eval_script("t05_word.py", args)


@app.command("eval-t06")
def eval_t06(
    src: Optional[Path] = typer.Option(None, "--src", help="Seed workbook path"),
    out: Optional[Path] = typer.Option(None, "--out", help="Save-as destination"),
    keep_open: bool = typer.Option(False, "--keep-open"),
) -> None:
    """Run T06 Excel open/modify/save-as verification (no LLM)."""
    args: list[str] = []
    if src:
        args.extend(["--src", str(src)])
    if out:
        args.extend(["--out", str(out)])
    if keep_open:
        args.append("--keep-open")
    _run_eval_script("t06_excel_save_as.py", args)


@app.command("eval-t07")
def eval_t07(
    html: Optional[Path] = typer.Option(None, "--html"),
    out: Optional[Path] = typer.Option(None, "--out"),
    force_controlled: bool = typer.Option(False, "--force-controlled"),
) -> None:
    """Run T07 Edge download + save filename verification (no LLM)."""
    args: list[str] = []
    if html:
        args.extend(["--html", str(html)])
    if out:
        args.extend(["--out", str(out)])
    if force_controlled:
        args.append("--force-controlled")
    _run_eval_script("t07_edge_download.py", args)


@app.command("eval-t08")
def eval_t08(
    html: Optional[Path] = typer.Option(None, "--html"),
    workbook: Optional[Path] = typer.Option(None, "--workbook"),
    keep_open: bool = typer.Option(False, "--keep-open"),
    force_controlled: bool = typer.Option(False, "--force-controlled"),
) -> None:
    """Run T08 Excel value -> web form verification (no LLM)."""
    args: list[str] = []
    if html:
        args.extend(["--html", str(html)])
    if workbook:
        args.extend(["--workbook", str(workbook)])
    if keep_open:
        args.append("--keep-open")
    if force_controlled:
        args.append("--force-controlled")
    _run_eval_script("t08_excel_to_form.py", args)


@app.command("eval-t09")
def eval_t09(
    out: Optional[Path] = typer.Option(None, "--out", help="Output xlsx path"),
    keep_open: bool = typer.Option(False, "--keep-open"),
) -> None:
    """Run T09 WPS Sheets write + save verification (no LLM)."""
    args: list[str] = []
    if out:
        args.extend(["--out", str(out)])
    if keep_open:
        args.append("--keep-open")
    _run_eval_script("t09_wps_sheets.py", args)


@app.command("eval-t10")
def eval_t10(
    out: Optional[Path] = typer.Option(None, "--out", help="Output docx path"),
    keep_open: bool = typer.Option(False, "--keep-open"),
) -> None:
    """Run T10 WPS Writer type + save verification (no LLM)."""
    args: list[str] = []
    if out:
        args.extend(["--out", str(out)])
    if keep_open:
        args.append("--keep-open")
    _run_eval_script("t10_wps_writer.py", args)


@app.command("eval-t11")
def eval_t11(
    out: Optional[Path] = typer.Option(None, "--out", help="Local xlsx save path"),
    keep_open: bool = typer.Option(False, "--keep-open"),
    discard: bool = typer.Option(False, "--discard", help="Click Don't Save instead"),
) -> None:
    """Run T11 Office save-prompt: More options -> local Save As (no LLM)."""
    args: list[str] = []
    if out:
        args.extend(["--out", str(out)])
    if keep_open:
        args.append("--keep-open")
    if discard:
        args.append("--discard")
    _run_eval_script("t11_office_prompt.py", args)


@app.command("eval-t12")
def eval_t12(
    html: Optional[Path] = typer.Option(None, "--html"),
    out: Optional[Path] = typer.Option(None, "--out", help="Local save path"),
) -> None:
    """Run T12 browser download-bar / Save As UIA verification (no LLM)."""
    args: list[str] = []
    if html:
        args.extend(["--html", str(html)])
    if out:
        args.extend(["--out", str(out)])
    _run_eval_script("t12_download_bar.py", args)


@app.command("eval-dashboard")
def eval_dashboard(
    suite: bool = typer.Option(False, "--suite", help="Run T01-T08 then aggregate"),
    llm_suite: bool = typer.Option(False, "--llm-suite", help="Run LLM_T01-LLM_T05 then aggregate"),
    run: Optional[list[str]] = typer.Option(None, "--run", help="Task ids to run, e.g. T01"),
    force_controlled: bool = typer.Option(False, "--force-controlled"),
    out: Optional[Path] = typer.Option(None, "--out"),
    md: Optional[Path] = typer.Option(None, "--md"),
) -> None:
    """Aggregate eval reports; optionally run a suite first."""
    args: list[str] = []
    if suite:
        args.append("--suite")
    if llm_suite:
        args.append("--llm-suite")
    if run:
        args.append("--run")
        args.extend(run)
    if force_controlled:
        args.append("--force-controlled")
    if out:
        args.extend(["--out", str(out)])
    if md:
        args.extend(["--md", str(md)])
    _run_eval_script("dashboard.py", args)


@app.command()
def replay(
    trace: Path = typer.Argument(..., help="Trace dir or events.jsonl path"),
    event_type: Optional[str] = typer.Option(
        None, "--type", help="Filter by event type, e.g. tool_call"
    ),
    limit: int = typer.Option(0, "--limit", help="Max events to show (0 = all)"),
    as_json: bool = typer.Option(False, "--json", help="Print machine-readable summary + events"),
    summary_only: bool = typer.Option(False, "--summary", help="Only print summary"),
) -> None:
    """Read-only replay of a local task trace (events.jsonl + screenshots)."""
    from desktop_agent.memory.replay import TraceReplay

    try:
        rp = TraceReplay.load(trace)
    except FileNotFoundError as e:
        rprint(f"[red]{e}[/]")
        raise typer.Exit(code=1) from e

    summary = rp.summary()
    events = rp.filter(event_type=event_type, limit=limit or None)

    if as_json:
        _print_json(
            {
                "summary": summary,
                "events": [e.to_dict() for e in events],
            }
        )
        return

    title = f"replay {summary.get('task_id')}"
    goal = summary.get("goal") or "(no goal recorded)"
    success = summary.get("success")
    status = "unknown" if success is None else ("ok" if success else "failed")
    rprint(
        Panel(
            f"dir: {summary['trace_dir']}\n"
            f"status: {status}\n"
            f"events: {summary['event_count']}  errors: {summary['error_count']}\n"
            f"screenshots: {len(summary.get('screenshots') or [])}\n"
            f"goal: {goal}",
            title=title,
        )
    )

    if summary.get("tool_counts"):
        tools = Table(title="tool_call counts")
        tools.add_column("tool")
        tools.add_column("count")
        for name, count in sorted(summary["tool_counts"].items(), key=lambda x: (-x[1], x[0])):
            tools.add_row(name, str(count))
        rprint(tools)

    if summary_only:
        return

    table = Table(title="events")
    table.add_column("#", justify="right")
    table.add_column("ts")
    table.add_column("type")
    table.add_column("detail")
    for ev in events:
        table.add_row(str(ev.line), ev.ts, ev.type, rp.short_line(ev))
    rprint(table)

    if summary.get("screenshots"):
        rprint("[dim]screenshots:[/]")
        for p in summary["screenshots"]:
            rprint(f"  {p}")


@app.command()
def run(
    goal: str = typer.Argument(..., help="Natural language task"),
    config: Optional[Path] = typer.Option(None, "--config"),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        envvar="DESKTOP_AGENT_YES",
        help="Auto-confirm policy prompts and auto-act instead of ask_user permission loops",
    ),
    max_steps: Optional[int] = typer.Option(None, "--max-steps", help="Override max tool rounds"),
) -> None:
    """Run a natural-language task via the LLM orchestrator."""
    from desktop_agent.adapters.apps import (
        infer_launch_app_from_goal,
        infer_launch_app_from_question,
    )
    from desktop_agent.orchestrator import Orchestrator

    # Unmistakable build marker (plain print survives theme/encoding issues).
    # Note: do not put bare [tags] in Rich strings — they are markup.
    print("BUILD=act-first", flush=True)
    rprint(f"[bold yellow]BUILD=act-first[/]  module={__file__}")

    cfg = load_config(config)
    if max_steps is not None:
        cfg.runtime.max_steps = max_steps
        cfg.llm.max_tool_rounds = max_steps

    if not cfg.llm.configured:
        rprint(
            Panel(
                "[red]LLM not configured[/]\n"
                "Set `llm.api_base` and `llm.model` in configs/agent.yaml,\n"
                "and export DESKTOP_AGENT_API_KEY.",
                title="desktop-agent run",
            )
        )
        raise typer.Exit(code=2)

    def ask_user(question: str, options: list[str] | None) -> str:
        # Belt-and-suspenders: never block the terminal on launch-permission asks.
        alias = infer_launch_app_from_question(question or "", goal=goal) or (
            infer_launch_app_from_goal(goal) if yes else None
        )
        if alias:
            rprint(
                f"[yellow]auto-act[/]: skip ask_user → launch_app app={alias}\n"
                f"[dim]question was:[/] {question}"
            )
            return f"yes, launch {alias}"
        if yes:
            rprint(Panel(question, title="ask_user"))
            if options:
                rprint("Options: " + ", ".join(options))
                for opt in options:
                    low = opt.lower()
                    if low in {
                        "y",
                        "yes",
                        "是",
                        "好",
                        "可以",
                        "需要",
                        "ok",
                        "true",
                    } or any(tok in opt for tok in ("启动", "打开", "launch", "open")):
                        rprint(f"[yellow]auto-answer[/]: {opt}")
                        return opt
                rprint(f"[yellow]auto-answer[/]: {options[0]}")
                return options[0]
            rprint("[yellow]auto-answer[/]: yes")
            return "yes"
        rprint(Panel(question, title="ask_user"))
        if options:
            rprint("Options: " + ", ".join(options))
        try:
            return input("> ").strip()
        except EOFError:
            return "cancel"

    def confirm(reason: str) -> bool:
        if yes:
            rprint(f"[yellow]auto-confirm[/]: {reason}")
            return True
        rprint(Panel(reason, title="confirm"))
        try:
            ans = input("Proceed? [y/N] ").strip().lower()
        except EOFError:
            return False
        return ans in {"y", "yes", "是", "ok"}

    title = "desktop-agent run | act-first" + (" | --yes" if yes else "")
    rprint(Panel(goal, title=title))
    if yes:
        rprint(
            "[bold yellow]--yes ON[/]: ask_user tool disabled; "
            "missing apps auto launch_app"
        )
    else:
        rprint("[red]WARNING: --yes not set; permission prompts may appear[/]")
    orch = Orchestrator.create(
        cfg,
        ask_user_fn=ask_user,
        confirm_fn=confirm,
        auto_yes=yes,
    )
    summary = orch.run(goal)
    _print_json(summary.to_dict())
    rprint(f"Trace: {orch.runtime.trace.dir}")
    raise typer.Exit(code=0 if summary.success else 1)

if __name__ == "__main__":
    app()
