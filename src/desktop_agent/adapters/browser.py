from __future__ import annotations

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

    def probe(self) -> BrowserStatus:
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
            return BrowserStatus(ok=False, endpoint=endpoint, error=str(e), mode="attach")

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

        channel = (self.config.browser.controlled_channel or "msedge").lower()
        if channel in {"edge", "msedge"}:
            channel = "msedge"
        elif channel == "chrome":
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
                args=[
                    "--no-first-run",
                    "--no-default-browser-check",
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

    def navigate(self, url: str, wait_until: str = "domcontentloaded") -> ActionResult:
        ctx = self._ensure()
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.bring_to_front()
        page.goto(url, wait_until=wait_until)
        return ActionResult(
            action="browser_navigate",
            ok=True,
            detail={"url": page.url, "title": page.title(), "mode": self._mode},
        )

    def fill(self, locator: dict[str, str], value: str) -> ActionResult:
        ctx = self._ensure()
        page = ctx.pages[0] if ctx.pages else None
        if page is None:
            raise AdapterUnavailable("No browser page open")
        loc = self._resolve_locator(page, locator)
        loc.fill(value)
        return ActionResult(
            action="browser_fill",
            ok=True,
            detail={"locator": locator, "value_len": len(value)},
        )

    def click(self, locator: dict[str, str]) -> ActionResult:
        ctx = self._ensure()
        page = ctx.pages[0] if ctx.pages else None
        if page is None:
            raise AdapterUnavailable("No browser page open")
        loc = self._resolve_locator(page, locator)
        loc.click()
        return ActionResult(action="browser_click", ok=True, detail={"locator": locator})

    def download(
        self,
        locator: dict[str, str],
        path: str,
        *,
        timeout_ms: int = 15000,
    ) -> ActionResult:
        """Click a download trigger and save the file to path (Playwright download API)."""
        ctx = self._ensure()
        page = ctx.pages[0] if ctx.pages else None
        if page is None:
            raise AdapterUnavailable("No browser page open")
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

    def snapshot_interactive(self, limit: int = 80) -> list[dict[str, Any]]:
        ctx = self._ensure()
        page = ctx.pages[0] if ctx.pages else None
        if page is None:
            return []
        script = """
        () => {
          const nodes = Array.from(document.querySelectorAll(
            'input, textarea, select, button, a[href], [role="button"], [contenteditable="true"]'
          ));
          return nodes.slice(0, %d).map((el, i) => {
            const rect = el.getBoundingClientRect();
            const label = el.getAttribute('aria-label')
              || el.getAttribute('placeholder')
              || el.getAttribute('name')
              || (el.innerText || '').trim().slice(0, 80);
            return {
              index: i,
              tag: el.tagName.toLowerCase(),
              type: el.getAttribute('type') || '',
              name: label,
              value: el.value || '',
              href: el.getAttribute('href') || '',
              bounds: {x: rect.x, y: rect.y, w: rect.width, h: rect.height}
            };
          });
        }
        """ % limit
        return page.evaluate(script)

    @staticmethod
    def _resolve_locator(page, locator: dict[str, str]):
        if locator.get("css"):
            return page.locator(locator["css"]).first
        if locator.get("label"):
            return page.get_by_label(locator["label"]).first
        if locator.get("role") and locator.get("name"):
            return page.get_by_role(locator["role"], name=locator["name"]).first
        if locator.get("name"):
            return page.get_by_role("button", name=locator["name"]).first
        raise AdapterUnavailable(f"Unsupported locator: {locator}")
