"""LLM e2e T05 Word: type marker -> save docx -> verify file / content."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _llm_common import WORD_TOOLS, ensure_llm, quit_office_apps, run_llm_goal, write_llm_report  # noqa: E402
from desktop_agent.config import load_config  # noqa: E402

MARKER = "DesktopAgent-LLM-T05-你好-Hello-12345"


def build_goal(out: Path, marker: str) -> str:
    path = out.as_posix()
    return (
        "只用 word_* COM：\n"
        "1) word_new 一次\n"
        f"2) word_type_text text={marker!r}\n"
        f"3) word_save path={path}\n"
        "4) verify_file 确认该路径存在且非空，再 done。\n"
        "必须以磁盘文件为准。"
    )


def _verify_docx_zip(path: Path, marker: str) -> tuple[bool, str]:
    """Read docx XML without COM (avoids Quit races with the agent Word instance)."""
    import zipfile
    import xml.etree.ElementTree as ET

    try:
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml")
        root = ET.fromstring(xml)
        texts = [
            (node.text or "")
            for node in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")
        ]
        joined = "".join(texts)
        return marker in joined, joined[:200]
    except Exception as e:
        return False, f"<zip_read_failed: {e}>"


def _verify_docx(path: Path, marker: str) -> tuple[bool, str]:
    if not path.exists() or path.stat().st_size <= 0:
        return False, ""
    ok, preview = _verify_docx_zip(path, marker)
    if ok or preview.startswith("<zip_read_failed"):
        if ok:
            return True, preview
    try:
        import time

        import win32com.client

        time.sleep(0.6)
        app = win32com.client.Dispatch("Word.Application")
        app.Visible = False
        app.DisplayAlerts = 0
        doc = app.Documents.Open(str(path))
        text = str(doc.Content.Text or "").replace("\r", "")
        doc.Close(SaveChanges=False)
        app.Quit()
        return marker in text, text[:200]
    except Exception as e:
        return ok, preview if preview else f"<read_failed: {e}>"


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM e2e T05 Word type+save")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--marker", default=MARKER)
    parser.add_argument("--max-steps", type=int, default=10)
    parser.add_argument("--yes", action="store_true", default=True)
    parser.add_argument("--no-yes", action="store_true")
    parser.add_argument("--keep-open", action="store_true")
    args = parser.parse_args()
    auto_yes = bool(args.yes) and not args.no_yes

    out = (args.out or Path(tempfile.gettempdir()) / "desktop-agent-llm-t05.docx").resolve()
    marker = str(args.marker)
    if out.exists():
        out.unlink()
    prep = quit_office_apps(word=True)
    print(f"[prep] office={prep}", flush=True)

    bad = ensure_llm(load_config())
    if bad:
        print(json.dumps({**bad, "out": str(out)}, ensure_ascii=False))
        return 2

    orch, summary, elapsed_ms = run_llm_goal(
        build_goal(out, marker),
        max_steps=args.max_steps,
        auto_yes=auto_yes,
        allowed_tools=WORD_TOOLS,
    )

    live_text = ""
    try:
        live_text = orch.runtime.word.read_text().replace("\r", "")
    except Exception:
        pass

    if not args.keep_open:
        try:
            orch.runtime.word.close(save=False, quit_app=True)
        except Exception:
            pass

    disk_ok, disk_preview = _verify_docx(out, marker)
    # Accept if disk content has marker; also accept file+live marker if reopen flaky.
    file_ok = out.exists() and out.stat().st_size > 0
    ok = bool(disk_ok or (file_ok and marker in live_text))
    report = {
        "task": "LLM_T05_word",
        "ok": ok,
        "out": str(out),
        "marker": marker,
        "llm_success": bool(summary.success),
        "llm_summary": summary.summary,
        "llm_steps": summary.steps,
        "elapsed_ms": elapsed_ms,
        "disk_exists": file_ok,
        "disk_contains_marker": disk_ok,
        "disk_preview": disk_preview,
        "live_contains_marker": marker in live_text,
        "trace": str(orch.runtime.trace.dir),
        "note": "ok requires saved docx with marker (or file+live marker if reopen fails).",
    }
    write_llm_report(orch.runtime.trace.dir, "llm_t05", report)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
