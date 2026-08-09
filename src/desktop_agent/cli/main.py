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

    # Browser CDP attach
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
        rows.append(
            (
                "Browser CDP (mode B)",
                "WARN",
                f"not reachable at {status.endpoint}. Run scripts/start-browser-debug.ps1",
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
def browser_probe(config: Optional[Path] = typer.Option(None, "--config")) -> None:
    """Probe CDP attach endpoint (mode B)."""
    rt = _runtime(config)
    _print_json(rt.call("browser_probe"))


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


@app.command("eval-t02")
def eval_t02(
    html: Optional[Path] = typer.Option(None, "--html", help="Local HTML form path"),
) -> None:
    """Run T02 Edge attach form fill verification (no LLM). Requires debug Edge."""
    args: list[str] = []
    if html:
        args.extend(["--html", str(html)])
    _run_eval_script("t02_edge.py", args)


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


@app.command()
def run(
    goal: str = typer.Argument(..., help="Natural language task"),
    config: Optional[Path] = typer.Option(None, "--config"),
    yes: bool = typer.Option(False, "--yes", help="Auto-confirm policy prompts"),
    max_steps: Optional[int] = typer.Option(None, "--max-steps", help="Override max tool rounds"),
) -> None:
    """Run a natural-language task via the LLM orchestrator."""
    from desktop_agent.orchestrator import Orchestrator

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

    rprint(Panel(goal, title="desktop-agent run"))
    orch = Orchestrator.create(cfg, ask_user_fn=ask_user, confirm_fn=confirm)
    summary = orch.run(goal)
    _print_json(summary.to_dict())
    rprint(f"Trace: {orch.runtime.trace.dir}")
    raise typer.Exit(code=0 if summary.success else 1)

if __name__ == "__main__":
    app()
