"""Unit tests for isolated Chrome debug helpers."""

from __future__ import annotations

from pathlib import Path

from desktop_agent.adapters.browser import find_chrome_executable, isolated_chrome_profile_dir


def test_isolated_profile_dir_uses_localappdata(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert isolated_chrome_profile_dir() == tmp_path / "DesktopAgent" / "browser-debug-profile" / "chrome"


def test_find_chrome_executable(monkeypatch, tmp_path: Path):
    chrome = tmp_path / "Google" / "Chrome" / "Application" / "chrome.exe"
    chrome.parent.mkdir(parents=True)
    chrome.write_bytes(b"mz")
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "x86"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert find_chrome_executable() == chrome
