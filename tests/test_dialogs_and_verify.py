"""Unit tests for Save As helper matching and file verification (no real UI)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from desktop_agent.common.dialogs import FileDialogHelper
from desktop_agent.config import AgentConfig, LlmConfig, RuntimeConfig, SafetyConfig
from desktop_agent.errors import ActionRejected
from desktop_agent.memory.trace import TraceStore
from desktop_agent.tools.runtime import ToolRuntime


def test_confirm_save_names_include_accelerator():
    names = FileDialogHelper.CONFIRM_SAVE_NAMES
    assert "保存(&S)" in names
    assert "Save" in names


def test_path_value_matches_filename_only():
    assert FileDialogHelper._path_value_matches(r"C:\Temp\a.txt", r"C:\Temp\a.txt")
    assert FileDialogHelper._path_value_matches("a.txt", r"C:\Temp\a.txt")
    assert not FileDialogHelper._path_value_matches("b.txt", r"C:\Temp\a.txt")
    assert not FileDialogHelper._path_value_matches("", r"C:\Temp\a.txt")


def test_escape_sendkeys_specials():
    assert FileDialogHelper._escape(r"C:\a(b).txt") == r"C:\a{(}b{)}.txt"
    assert FileDialogHelper._escape("x%y") == "x{%}y"


def test_verify_file_ok_and_missing(tmp_path: Path):
    cfg = AgentConfig(
        traces_dir=tmp_path / "traces",
        llm=LlmConfig(api_base="http://x", model="m", api_key="k"),
        runtime=RuntimeConfig(),
        safety=SafetyConfig(enforce_whitelist=False),
    )
    rt = ToolRuntime(cfg, trace=TraceStore(cfg.traces_dir, task_id="t_verify"))
    target = tmp_path / "note.txt"
    missing = rt.call("verify_file", path=str(target), contains="hello")
    assert missing["ok"] is False

    target.write_text("hello LLM-T01", encoding="utf-8")
    ok = rt.call("verify_file", path=str(target), contains="LLM-T01")
    assert ok["ok"] is True
    assert ok["bytes"] > 0

    bad = rt.call("verify_file", path=str(target), contains="missing-marker")
    assert bad["ok"] is False


def test_wait_for_file_exists_and_contains(tmp_path: Path):
    cfg = AgentConfig(
        traces_dir=tmp_path / "traces",
        llm=LlmConfig(api_base="http://x", model="m", api_key="k"),
        runtime=RuntimeConfig(step_timeout_ms=2000),
        safety=SafetyConfig(enforce_whitelist=False),
    )
    rt = ToolRuntime(cfg, trace=TraceStore(cfg.traces_dir, task_id="t_wait"))
    target = tmp_path / "wait.txt"
    target.write_text("marker-xyz", encoding="utf-8")

    exists = rt.call(
        "wait_for",
        condition={"type": "file_exists", "value": str(target), "timeout_ms": 1000},
    )
    assert exists["ok"] is True

    contains = rt.call(
        "wait_for",
        condition={
            "type": "file_contains",
            "query": {"path": str(target)},
            "value": "marker-xyz",
            "timeout_ms": 1000,
        },
    )
    assert contains["ok"] is True


def test_wait_for_file_exists_timeout(tmp_path: Path):
    cfg = AgentConfig(
        traces_dir=tmp_path / "traces",
        llm=LlmConfig(api_base="http://x", model="m", api_key="k"),
        runtime=RuntimeConfig(),
        safety=SafetyConfig(enforce_whitelist=False),
    )
    rt = ToolRuntime(cfg, trace=TraceStore(cfg.traces_dir, task_id="t_wait_fail"))
    missing = tmp_path / "nope.txt"
    result = rt.call(
        "wait_for",
        condition={"type": "file_exists", "value": str(missing), "timeout_ms": 300},
    )
    assert result["ok"] is False
    err = result.get("error") or {}
    assert err.get("code") == "TIMEOUT" or "timed out" in str(err).lower()


def test_llm_t01_goal_requires_real_save():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from evals.runners.llm_t01_notepad import build_goal

    goal = build_goal(Path(r"C:\Temp\desktop-agent-llm-t01.txt"), "MARK")
    assert "另存为" in goal
    assert "真正写入磁盘" in goal
    assert "不算完成" in goal
    assert "MARK" in goal


def test_complete_save_raises_when_file_missing(monkeypatch, tmp_path: Path):
    helper = FileDialogHelper()
    path = tmp_path / "missing.txt"

    monkeypatch.setattr(helper, "fill_and_confirm", lambda *a, **k: None)
    monkeypatch.setattr(helper, "dismiss_confirm_yes", lambda *a, **k: None)
    monkeypatch.setattr(helper, "_wait_file", lambda *a, **k: False)
    monkeypatch.setattr(helper, "wait_dialog", lambda *a, **k: None)

    class _DummyAuto:
        @staticmethod
        def SendKeys(*a, **k):
            return None

    monkeypatch.setattr("desktop_agent.common.dialogs.auto", _DummyAuto)

    with pytest.raises(ActionRejected, match="File not created"):
        helper._complete_save(object(), path, wait_file_s=0.1)
