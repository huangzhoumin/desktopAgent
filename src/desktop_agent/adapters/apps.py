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


class AppLauncher:
    """Start a whitelisted application by alias."""

    def __init__(self, allowed_aliases: set[str] | None = None):
        self.allowed_aliases = allowed_aliases

    def launch(self, app: str, *, args: list[str] | None = None) -> ActionResult:
        alias = (app or "").strip().lower()
        if alias.endswith(".exe"):
            alias = alias[: -4]
        # Normalize common names
        if alias in {"msedge", "microsoft-edge"}:
            alias = "edge"
        if alias in {"winword", "microsoft word"}:
            alias = "word"
        if alias == "et":
            alias = "wps"

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
        proc = subprocess.Popen(cmd, close_fds=True)
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
