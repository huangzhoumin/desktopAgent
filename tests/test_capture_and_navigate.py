"""Tests for multi-monitor capture + browser URL helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from desktop_agent.adapters.browser import BrowserAdapter
from desktop_agent.common.dpi import ensure_dpi_aware
from desktop_agent.errors import AdapterUnavailable
from desktop_agent.perception.capture import capture_screen, virtual_screen_rect
from desktop_agent.planner.planner import _extract_urls


def test_extract_urls_from_goal():
    urls = _extract_urls("打开 https://www.bilibili.com，再搜索")
    assert urls == ["https://www.bilibili.com"]


def test_normalize_url_adds_https():
    assert BrowserAdapter._normalize_url("www.bilibili.com") == "https://www.bilibili.com"
    assert BrowserAdapter._normalize_url("https://example.com/a") == "https://example.com/a"


def test_normalize_url_rejects_search_text():
    with pytest.raises(AdapterUnavailable):
        BrowserAdapter._normalize_url("凡人修仙传")


class _FakeLoc:
    def __init__(self, label: str, count: int = 1):
        self.label = label
        self._count = count
        self.first = self

    def count(self) -> int:
        return self._count

    def nth(self, idx: int):
        return _FakeLoc(f"{self.label}[{idx}]")


class _FakePage:
    def __init__(self):
        self.calls: list[tuple] = []

    def locator(self, sel: str):
        self.calls.append(("locator", sel))
        return _FakeLoc(f"css:{sel}")

    def get_by_placeholder(self, text: str):
        self.calls.append(("placeholder", text))
        return _FakeLoc(f"ph:{text}")

    def get_by_label(self, text: str):
        self.calls.append(("label", text))
        return _FakeLoc(f"label:{text}")

    def get_by_role(self, role: str, name: str | None = None):
        self.calls.append(("role", role, name))
        # Simulate placeholder-as-name matching a searchbox, not a button.
        if role == "searchbox" and name == "洛克王国远行商人":
            return _FakeLoc(f"role:{role}:{name}", count=1)
        if role in {"textbox", "button", "link"} and name == "洛克王国远行商人":
            return _FakeLoc(f"role:{role}:{name}", count=0)
        if name is None:
            return _FakeLoc(f"role:{role}", count=1)
        return _FakeLoc(f"role:{role}:{name}", count=0)


def test_resolve_locator_prefers_index_and_placeholder():
    page = _FakePage()
    loc = BrowserAdapter._resolve_locator(page, {"index": 10})
    assert loc.label.endswith("[10]")
    assert page.calls[0][0] == "locator"

    page2 = _FakePage()
    loc2 = BrowserAdapter._resolve_locator(page2, {"placeholder": "洛克王国远行商人"})
    assert loc2.label == "ph:洛克王国远行商人"


def test_resolve_locator_name_prefers_searchbox_over_button():
    page = _FakePage()
    loc = BrowserAdapter._resolve_locator(page, {"name": "洛克王国远行商人"})
    assert loc.label == "role:searchbox:洛克王国远行商人"
    roles = [c[1] for c in page.calls if c[0] == "role"]
    assert roles[0] == "searchbox"
    assert "button" not in roles  # stopped after searchbox hit


def test_snapshot_search_candidate_hint_in_runtime(monkeypatch):
    from desktop_agent.config import AgentConfig
    from desktop_agent.memory.trace import TraceStore
    from desktop_agent.tools.runtime import ToolRuntime

    class _Browser:
        mode = "controlled"

        def snapshot_interactive(self):
            return {
                "elements": [
                    {
                        "index": 10,
                        "tag": "input",
                        "type": "text",
                        "kind": "search_candidate",
                        "name": "洛克王国远行商人",
                        "placeholder": "洛克王国远行商人",
                        "css": 'input[placeholder="洛克王国远行商人"]',
                    }
                ],
                "url": "https://www.bilibili.com/",
                "title": "bilibili",
                "mode": "controlled",
            }

    cfg = AgentConfig()
    rt = ToolRuntime(cfg, trace=TraceStore(cfg.traces_dir, task_id="tsk_test_dom"))
    monkeypatch.setattr(rt, "browser", _Browser())
    out = rt.call("browser_snapshot")
    assert out["ok"] is True
    assert "search_candidate" in out["hint"]
    assert "example_index=10" in out["hint"]
    assert "ocr_find" in out["hint"]


def test_dpi_aware_virtual_screen_matches_capture(tmp_path: Path):
    ensure_dpi_aware()
    ox, oy, vw, vh = virtual_screen_rect()
    assert vw > 0 and vh > 0
    out = tmp_path / "full.png"
    cap = capture_screen(out, scope="full")
    assert Path(cap.path).exists()
    # Allow 5% slack for transient desktop composition differences.
    assert cap.width >= int(vw * 0.95)
    assert cap.height >= int(vh * 0.95)
    assert cap.detail.get("backend")
