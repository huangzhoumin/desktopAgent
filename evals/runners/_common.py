"""Shared helpers for no-LLM eval runners."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable


def make_stepper(trace, steps: list[dict]):
    def step(name: str, fn: Callable[[], Any]):
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
            if not item["ok"]:
                raise RuntimeError(payload.get("error") or payload)
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

    return step


def write_report(trace_dir: Path, task: str, ok: bool, steps: list[dict], **extra) -> Path:
    report = {"task": task, "ok": ok, "trace": str(trace_dir), "steps": steps, **extra}
    path = trace_dir / f"{task.lower()}_report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("---")
    print(json.dumps({"ok": ok, "report": str(path), **{k: extra[k] for k in list(extra)[:3]}}, ensure_ascii=False))
    return path


def normalize_excel_value(value):
    if isinstance(value, (list, tuple)):
        return [normalize_excel_value(v) for v in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value
