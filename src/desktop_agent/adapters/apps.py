"""Launch whitelist apps for the agent loop."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from desktop_agent.errors import ActionRejected, AdapterUnavailable
from desktop_agent.models import ActionResult

# alias -> (executable candidates or shell command pieces)
_APP_LAUNCHERS: dict[str, list[str]] = {
    "notepad": ["notepad.exe"],
    "excel": ["excel.exe"],
    "word": ["winword.exe"],
    "edge": [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        "msedge.exe",
    ],
    "chrome": [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        "chrome.exe",
    ],
}

# Natural-language / locale aliases -> canonical launcher key
_ALIAS_NORMALIZE: dict[str, str] = {
    "notepad": "notepad",
    "记事本": "notepad",
    "notepad.exe": "notepad",
    "excel": "excel",
    "excel.exe": "excel",
    "表格": "excel",
    "电子表格": "excel",
    "word": "word",
    "winword": "word",
    "winword.exe": "word",
    "microsoft word": "word",
    "文档": "word",
    "edge": "edge",
    "msedge": "edge",
    "msedge.exe": "edge",
    "microsoft-edge": "edge",
    "微软浏览器": "edge",
    "chrome": "chrome",
    "chrome.exe": "chrome",
    "google": "chrome",
    "google chrome": "chrome",
    "googlechrome": "chrome",
    "谷歌": "chrome",
    "谷歌浏览器": "chrome",
    "et": "wps",
}


def normalize_app_alias(app: str) -> str:
    """Map user/LLM app names to a canonical launcher alias."""
    raw = (app or "").strip()
    if not raw:
        return ""
    key = raw.lower()
    if key in _ALIAS_NORMALIZE:
        return _ALIAS_NORMALIZE[key]
    # Preserve original for Chinese keys that are case-sensitive in the map
    if raw in _ALIAS_NORMALIZE:
        return _ALIAS_NORMALIZE[raw]
    if key.endswith(".exe"):
        key = key[: -4]
        if key in _ALIAS_NORMALIZE:
            return _ALIAS_NORMALIZE[key]
    return key


_APP_MARKERS: tuple[tuple[str, str], ...] = (
    ("记事本", "notepad"),
    ("notepad", "notepad"),
    ("excel", "excel"),
    ("电子表格", "excel"),
    ("word", "word"),
    ("winword", "word"),
    ("谷歌浏览器", "chrome"),
    ("google chrome", "chrome"),
    ("googlechrome", "chrome"),
    ("chrome", "chrome"),
    ("google", "chrome"),
    ("谷歌", "chrome"),
    ("edge", "edge"),
    ("msedge", "edge"),
    ("微软浏览器", "edge"),
)

# ask_user phrasings that mean "should I launch / is it open?" rather than real Q&A
_LAUNCH_INTENT_TOKENS: tuple[str, ...] = (
    "启动",
    "打开",
    "launch",
    "start",
    "运行",
    "唤起",
    "找不到",
    "未找到",
    "没有找到",
    "没找到",
    "没有打开",
    "没开",
    "重新",
    "尝试",
    "other method",
    "not found",
    "open",
    "手动",
    "是否",
    "可否",
    "可以吗",
    "要不要",
    "需不需要",
    "需要我",
    "为您",
    "帮你",
    "confirm",
    "should i",
    "may i",
    "can i",
    "do you want",
    "shall i",
    "already",
    "怎么办",
    "允许",
)


def _find_app_alias(text: str) -> str | None:
    q = (text or "").strip().lower()
    if not q:
        return None
    for key, name in _APP_MARKERS:
        if key in q:
            return name
    return None


def infer_launch_app_from_goal(goal: str) -> str | None:
    """Best-effort app alias mentioned in the user goal (for auto-act fallbacks)."""
    return _find_app_alias(goal)


def infer_launch_app_from_question(question: str, *, goal: str = "") -> str | None:
    """If ask_user is really 'should I open X?', return the app alias to launch."""
    q = (question or "").strip().lower()
    if not q:
        return None
    alias = _find_app_alias(q)
    goal_alias = _find_app_alias(goal) if goal else None

    if alias and any(token in q for token in _LAUNCH_INTENT_TOKENS):
        return alias
    # Same app as the goal + any question about it → just launch (weak models stall here).
    if alias and goal_alias and alias == goal_alias:
        return alias
    # Goal names an app; question is a missing-window / permission stall without naming it.
    if goal_alias and any(token in q for token in _LAUNCH_INTENT_TOKENS):
        if any(
            tok in q
            for tok in (
                "窗口",
                "应用",
                "程序",
                "window",
                "app",
                "找不到",
                "未找到",
                "没有找到",
                "没找到",
                "not found",
            )
        ):
            return goal_alias
    return None


class AppLauncher:
    """Start a whitelisted application by alias."""

    def __init__(self, allowed_aliases: set[str] | None = None):
        self.allowed_aliases = allowed_aliases

    def launch(self, app: str, *, args: list[str] | None = None) -> ActionResult:
        alias = normalize_app_alias(app)
        if not alias:
            raise ActionRejected("Missing app alias")

        if self.allowed_aliases is not None and alias not in self.allowed_aliases:
            # Also allow if any whitelist alias matches
            raise ActionRejected(f"App not in whitelist: {app}")

        candidates = _APP_LAUNCHERS.get(alias)
        if not candidates:
            # Try PATH / bare name
            candidates = [f"{alias}.exe"]

        exe = self._resolve(candidates)
        if exe is None:
            raise AdapterUnavailable(f"Cannot find executable for app={app}")

        cmd = [exe, *(args or [])]
        # close_fds=True can interfere with some Win32 GUI startups on Windows.
        proc = subprocess.Popen(cmd, close_fds=(os.name != "nt"))
        time.sleep(0.6)
        return ActionResult(
            action="launch_app",
            ok=True,
            detail={"app": alias, "exe": exe, "pid": proc.pid, "args": args or []},
        )

    @staticmethod
    def _resolve(candidates: list[str]) -> str | None:
        for c in candidates:
            p = Path(c)
            if p.is_file():
                return str(p)
            found = shutil.which(c)
            if found:
                return found
        return None
