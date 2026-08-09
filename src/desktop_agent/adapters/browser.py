from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from desktop_agent.config import AgentConfig, ROOT
from desktop_agent.errors import AdapterUnavailable
from desktop_agent.models import ActionResult


@dataclass
class BrowserStatus:
    ok: bool
    endpoint: str
    version: str | None = None
    pages: list[dict[str, Any]] | None = None
    error: str | None = None
    mode: str = "attach"
    auto_started: bool = False


def isolated_chrome_profile_dir() -> Path:
    """Same profile used by scripts/start-chrome-debug-isolated.bat."""
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / "DesktopAgent" / "browser-debug-profile" / "chrome"


def find_chrome_executable() -> Path | None:
    env = os.environ
    candidates = [
        Path(env.get("ProgramFiles", r"C:\Program Files"))
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe",
        Path(env.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe",
        Path(env.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
    ]
    for path in candidates:
        if path and path.is_file():
            return path
    return None


class BrowserAdapter:
    """Playwright CDP attach (mode B) with controlled launch fallback (mode A)."""

    def __init__(self, config: AgentConfig):
        self.config = config
        self._pw = None
        self._browser = None
        self._context = None
        self._mode: str | None = None
        self._owned_process = False

    @property
    def endpoint(self) -> str:
        return self.config.cdp_endpoint

    @property
    def mode(self) -> str | None:
        return self._mode

    def probe(self, *, auto_start_isolated: bool = False) -> BrowserStatus:
        status = self._probe_once()
        if status.ok or not auto_start_isolated:
            return status
        return self.launch_isolated_debug()

    def _probe_once(self) -> BrowserStatus:
        endpoint = self.endpoint
        try:
            with httpx.Client(timeout=2.0) as client:
                version = client.get(f"{endpoint}/json/version").json()
                pages = client.get(f"{endpoint}/json/list").json()
            return BrowserStatus(
                ok=True,
                endpoint=endpoint,
                version=version.get("Browser") or version.get("BrowserVersion"),
                pages=[
                    {
                        "title": p.get("title"),
                        "url": p.get("url"),
                        "id": p.get("id"),
                        "type": p.get("type"),
                    }
                    for p in pages
                    if p.get("type") in {None, "page", "webview"}
                ],
                mode="attach",
            )
        except Exception as e:
            detail = str(e) or e.__class__.__name__
            # Chrome 136+ ignores --remote-debugging-port on the default profile.
            hint = (
                "Nothing listening on CDP (Chrome not started with debug port, "
                "or flags ignored). Use scripts/start-chrome-debug-isolated.bat "
                "(requires --user-data-dir; Chrome 136+)."
            )
            return BrowserStatus(
                ok=False,
                endpoint=endpoint,
                error=f"{detail}. {hint}",
                mode="attach",
            )

    def launch_isolated_debug(self, wait_s: float = 20.0) -> BrowserStatus:
        """Start Chrome with the isolated debug profile (same as the .bat helper)."""
        status = self._probe_once()
        if status.ok:
            return status

        chrome = find_chrome_executable()
        if chrome is None:
            return BrowserStatus(
                ok=False,
                endpoint=self.endpoint,
                error="Cannot find chrome.exe to auto-start isolated debug Chrome.",
                mode="attach",
                auto_started=False,
            )

        profile = isolated_chrome_profile_dir()
        profile.mkdir(parents=True, exist_ok=True)
        port = int(self.config.browser.cdp_port)
        args = [
            str(chrome),
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--start-maximized",
            "--new-window",
            "about:blank",
        ]
        popen_kwargs: dict[str, Any] = {
            "args": args,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "stdin": subprocess.DEVNULL,
        }
        if sys.platform == "win32":
            # Detach so probe CLI exit does not tear down Chrome.
            popen_kwargs["creationflags"] = (
                subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            )
            popen_kwargs["close_fds"] = True

        try:
            subprocess.Popen(**popen_kwargs)
        except Exception as e:
            return BrowserStatus(
                ok=False,
                endpoint=self.endpoint,
                error=f"Failed to launch isolated debug Chrome: {e}",
                mode="attach",
                auto_started=False,
            )

        deadline = time.time() + wait_s
        last = status
        while time.time() < deadline:
            last = self._probe_once()
            if last.ok:
                last.auto_started = True
                last.mode = "attach"
                return last
            time.sleep(0.5)

        last.auto_started = True
        last.error = (
            f"{last.error or 'CDP still unreachable'} "
            f"(auto-started {chrome} with profile {profile})"
        )
        return last

    def connect(self) -> BrowserStatus:
        prefer = (self.config.browser.mode or "attach").lower()
        if prefer == "controlled":
            return self._launch_controlled()

        status = self.probe()
        if status.ok:
            return self._connect_cdp(status)

        if self.config.browser.fallback_to_controlled:
            return self._launch_controlled(fallback_from=status.error)

        raise AdapterUnavailable(
            f"Cannot attach browser at {self.endpoint}: {status.error}. "
            "Start browser with scripts/start-browser-debug.ps1, "
            "or enable browser.fallback_to_controlled."
        )

    def _connect_cdp(self, status: BrowserStatus | None = None) -> BrowserStatus:
        status = status or self.probe()
        if not status.ok:
            raise AdapterUnavailable(
                f"Cannot attach browser at {self.endpoint}: {status.error}. "
                "Start browser with scripts/start-browser-debug.ps1"
            )
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise AdapterUnavailable("playwright not installed") from e

        self.close()
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp(self.endpoint)
        if self._browser.contexts:
            self._context = self._browser.contexts[0]
        else:
            self._context = self._browser.new_context()
        self._mode = "attach"
        self._owned_process = False
        status.mode = "attach"
        return status

    def _launch_controlled(self, fallback_from: str | None = None) -> BrowserStatus:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise AdapterUnavailable("playwright not installed") from e

        channel = (self.config.browser.controlled_channel or "chrome").lower()
        if channel in {"edge", "msedge"}:
            channel = "msedge"
        elif channel in {"chrome", "google", "google-chrome", "googlechrome"}:
            channel = "chrome"

        user_data = Path(self.config.browser.controlled_user_data_dir)
        if not user_data.is_absolute():
            user_data = ROOT / user_data
        user_data.mkdir(parents=True, exist_ok=True)

        self.close()
        self._pw = sync_playwright().start()
        try:
            self._context = self._pw.chromium.launch_persistent_context(
                str(user_data),
                channel=channel,
                headless=False,
                accept_downloads=True,
                no_viewport=True,
                args=[
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--start-maximized",
                    f"--remote-debugging-port={self.config.browser.cdp_port}",
                    f"--remote-debugging-address={self.config.browser.cdp_host}",
                ],
            )
        except Exception as e:
            raise AdapterUnavailable(
                f"Controlled browser launch failed ({channel}): {e}. "
                f"Attach error was: {fallback_from}" if fallback_from else str(e)
            ) from e

        self._browser = self._context.browser
        self._mode = "controlled"
        self._owned_process = True
        if not self._context.pages:
            self._context.new_page()

        # Wait briefly for CDP endpoint (best-effort; Playwright context is already usable).
        deadline = time.time() + 4.0
        version = f"controlled:{channel}"
        while time.time() < deadline:
            probed = self.probe()
            if probed.ok:
                version = probed.version or version
                break
            time.sleep(0.2)

        try:
            self._focus_os_window()
        except Exception:
            pass

        return BrowserStatus(
            ok=True,
            endpoint=self.endpoint,
            version=version,
            pages=[{"title": p.title(), "url": p.url} for p in self._context.pages],
            mode="controlled",
            error=f"fell back from attach: {fallback_from}" if fallback_from else None,
        )

    def close(self) -> None:
        try:
            if self._context and self._owned_process:
                self._context.close()
            elif self._browser and not self._owned_process:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._browser = None
        self._context = None
        self._pw = None
        self._mode = None
        self._owned_process = False

    def _ensure(self):
        if self._context is None:
            self.connect()
        return self._context

    def list_pages(self) -> list[dict[str, Any]]:
        ctx = self._ensure()
        pages = []
        for i, page in enumerate(ctx.pages):
            pages.append({"index": i, "url": page.url, "title": page.title()})
        return pages

    @staticmethod
    def _normalize_url(url: str) -> str:
        raw = (url or "").strip()
        if not raw:
            raise AdapterUnavailable("browser_navigate requires a non-empty url")
        lower = raw.lower()
        if lower.startswith(("http://", "https://", "file://", "about:", "data:")):
            return raw
        # Bare domains typed into an agent goal (reject search-like free text).
        if any(ch.isspace() for ch in raw) or "." not in raw:
            raise AdapterUnavailable(
                f"Refusing to open search-like text via browser_navigate: {raw!r}. "
                "Pass a full http(s) URL."
            )
        return "https://" + raw

    def _active_page(self):
        ctx = self._ensure()
        pages = list(ctx.pages or [])
        if not pages:
            return ctx.new_page()

        def _score(page) -> int:
            try:
                url = (page.url or "").lower()
            except Exception:
                url = ""
            if url.startswith(("http://", "https://", "file://")):
                return 5
            if url in {"", "about:blank"}:
                return 3
            if url.startswith(("chrome://", "edge://", "devtools:", "chrome-extension:")):
                return 0
            return 2

        best = max(pages, key=_score)
        if _score(best) == 0:
            return ctx.new_page()
        return best

    def _focus_os_window(self, *, title_hint: str = "") -> bool:
        """Bring the Chromium OS window to the foreground and maximize it."""
        try:
            from desktop_agent.common.win32_window import (
                find_browser_hwnd,
                force_foreground,
                maximize_window,
                move_to_primary_maximized,
            )
        except Exception:
            return False
        hwnd = find_browser_hwnd(title_substr=title_hint) if title_hint else None
        if not hwnd:
            hwnd = find_browser_hwnd()
        if not hwnd:
            return False
        try:
            # Controlled browser: park on primary + maximize so the tab fills the screen.
            if self._mode == "controlled":
                move_to_primary_maximized(hwnd)
            else:
                maximize_window(hwnd)
        except Exception:
            pass
        focused = bool(force_foreground(hwnd))
        try:
            # force_foreground must not leave a restored small window.
            maximize_window(hwnd)
        except Exception:
            pass
        return focused

    def navigate(self, url: str, wait_until: str = "domcontentloaded") -> ActionResult:
        target = self._normalize_url(url)
        wait = (wait_until or "domcontentloaded").strip().lower()
        if wait not in {"load", "domcontentloaded", "networkidle", "commit"}:
            wait = "domcontentloaded"

        ctx = self._ensure()
        page = self._active_page()
        try:
            page.bring_to_front()
        except Exception:
            pass

        attempts: list[str] = []
        for until in (wait, "load", "commit"):
            if until not in attempts:
                attempts.append(until)

        last_err: Exception | None = None
        final_wait = wait
        for attempt, until in enumerate(attempts):
            try:
                page.goto(target, wait_until=until, timeout=60_000)
                final_wait = until
                last_err = None
                break
            except Exception as e:
                last_err = e
                # Create a fresh page once if the current tab is wedged.
                if attempt == 0:
                    try:
                        page = ctx.new_page()
                        page.bring_to_front()
                    except Exception:
                        pass
                continue
        if last_err is not None:
            raise AdapterUnavailable(f"browser_navigate failed for {target}: {last_err}") from last_err

        # Brief settle — SPAs often keep mutating after domcontentloaded.
        try:
            page.wait_for_load_state("domcontentloaded", timeout=5_000)
        except Exception:
            pass

        title = ""
        current = target
        try:
            current = page.url
            title = page.title()
        except Exception:
            pass

        focused = self._focus_os_window(title_hint=title[:40] if title else "")
        return ActionResult(
            action="browser_navigate",
            ok=True,
            detail={
                "url": current,
                "requested_url": target,
                "title": title,
                "mode": self._mode,
                "wait_until": final_wait,
                "os_focused": focused,
            },
        )

    def fill(self, locator: dict[str, Any], value: str) -> ActionResult:
        page = self._active_page()
        loc = self._resolve_locator(page, locator)
        loc.fill(value)
        return ActionResult(
            action="browser_fill",
            ok=True,
            detail={"locator": locator, "value_len": len(value)},
        )

    def click(self, locator: dict[str, Any]) -> ActionResult:
        page = self._active_page()
        loc = self._resolve_locator(page, locator)
        loc.click()
        return ActionResult(action="browser_click", ok=True, detail={"locator": locator})

    def download(
        self,
        locator: dict[str, Any],
        path: str,
        *,
        timeout_ms: int = 15000,
    ) -> ActionResult:
        """Click a download trigger and save the file to path (Playwright download API)."""
        page = self._active_page()
        out = Path(path).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists():
            out.unlink()

        loc = self._resolve_locator(page, locator)
        with page.expect_download(timeout=timeout_ms) as di:
            loc.click()
        download = di.value
        download.save_as(str(out))
        suggested = download.suggested_filename
        return ActionResult(
            action="browser_download",
            ok=True,
            detail={
                "path": str(out),
                "bytes": out.stat().st_size if out.exists() else 0,
                "suggested_filename": suggested,
                "mode": self._mode,
            },
        )

    _SNAPSHOT_SELECTOR = (
        'input, textarea, select, button, a[href], [role="button"], [contenteditable="true"]'
    )

    def snapshot_interactive(self, limit: int = 80) -> dict[str, Any]:
        page = self._active_page()
        script = """
        () => {
          const sel = %s;
          const nodes = Array.from(document.querySelectorAll(sel));
          const esc = (s) => {
            if (window.CSS && CSS.escape) return CSS.escape(s);
            return String(s).replace(/[^a-zA-Z0-9_-]/g, '\\\\$&');
          };
          return nodes.slice(0, %d).map((el, i) => {
            const rect = el.getBoundingClientRect();
            const tag = el.tagName.toLowerCase();
            const type = (el.getAttribute('type') || '').toLowerCase();
            const placeholder = el.getAttribute('placeholder') || '';
            const aria = el.getAttribute('aria-label') || '';
            const attrName = el.getAttribute('name') || '';
            const label = aria || placeholder || attrName
              || (el.innerText || '').trim().slice(0, 80);
            const roleAttr = (el.getAttribute('role') || '').toLowerCase();
            const textTypes = new Set(['text', 'search', '', 'email', 'tel', 'url']);
            const isTextInput = (tag === 'input' && textTypes.has(type))
              || tag === 'textarea'
              || roleAttr === 'searchbox'
              || el.getAttribute('contenteditable') === 'true';
            let role = roleAttr;
            if (!role) {
              if (type === 'search' || roleAttr === 'searchbox') role = 'searchbox';
              else if (isTextInput) role = 'textbox';
              else if (tag === 'button' || roleAttr === 'button') role = 'button';
              else if (tag === 'a') role = 'link';
            }
            const inHeader = rect.y >= 0 && rect.y < 140 && rect.width >= 80 && rect.height >= 14;
            const kind = (isTextInput && inHeader) ? 'search_candidate' : '';
            let css = '';
            if (el.id) css = '#' + esc(el.id);
            else if (attrName && (tag === 'input' || tag === 'textarea' || tag === 'select'))
              css = tag + '[name="' + attrName.replace(/"/g, '\\\\"') + '"]';
            else if (placeholder && isTextInput)
              css = tag + '[placeholder="' + placeholder.replace(/"/g, '\\\\"') + '"]';
            else {
              const cls = String(el.className || '').trim().split(/\\s+/).filter(Boolean)[0];
              if (cls) css = tag + '.' + esc(cls);
            }
            return {
              index: i,
              tag: tag,
              type: type,
              role: role,
              kind: kind,
              name: label,
              placeholder: placeholder,
              css: css,
              value: el.value || '',
              href: el.getAttribute('href') || '',
              bounds: {x: rect.x, y: rect.y, w: rect.width, h: rect.height}
            };
          });
        }
        """ % (repr(self._SNAPSHOT_SELECTOR), limit)
        try:
            elements = page.evaluate(script)
        except Exception:
            elements = []
        url = ""
        title = ""
        try:
            url = page.url
            title = page.title()
        except Exception:
            pass
        return {
            "elements": elements,
            "url": url,
            "title": title,
            "mode": self._mode,
        }

    @classmethod
    def _resolve_locator(cls, page, locator: dict[str, Any]):
        if locator.get("index") is not None and str(locator.get("index")).strip() != "":
            idx = int(locator["index"])
            return page.locator(cls._SNAPSHOT_SELECTOR).nth(idx)
        if locator.get("css"):
            return page.locator(str(locator["css"])).first
        if locator.get("placeholder"):
            return page.get_by_placeholder(str(locator["placeholder"])).first
        if locator.get("label"):
            return page.get_by_label(str(locator["label"])).first
        role = str(locator.get("role") or "").strip()
        name = str(locator.get("name") or "").strip()
        if role and name:
            return page.get_by_role(role, name=name).first
        if role:
            return page.get_by_role(role).first
        if name:
            # Prefer form fields over buttons: snapshot "name" is often a placeholder.
            for candidate in ("searchbox", "textbox", "button", "link"):
                loc = page.get_by_role(candidate, name=name)
                try:
                    if loc.count() > 0:
                        return loc.first
                except Exception:
                    continue
            try:
                ph = page.get_by_placeholder(name)
                if ph.count() > 0:
                    return ph.first
            except Exception:
                pass
            return page.get_by_role("button", name=name).first
        raise AdapterUnavailable(f"Unsupported locator: locator={locator}")
