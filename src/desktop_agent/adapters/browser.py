from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from desktop_agent.config import AgentConfig
from desktop_agent.errors import AdapterUnavailable
from desktop_agent.models import ActionResult


@dataclass
class BrowserStatus:
    ok: bool
    endpoint: str
    version: str | None = None
    pages: list[dict[str, Any]] | None = None
    error: str | None = None


class BrowserAdapter:
    """Playwright CDP attach (mode B) with optional controlled fallback later."""

    def __init__(self, config: AgentConfig):
        self.config = config
        self._pw = None
        self._browser = None
        self._context = None

    @property
    def endpoint(self) -> str:
        return self.config.cdp_endpoint

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
                    {"title": p.get("title"), "url": p.get("url"), "id": p.get("id"), "type": p.get("type")}
                    for p in pages
                    if p.get("type") in {None, "page", "webview"}
                ],
            )
        except Exception as e:
            return BrowserStatus(ok=False, endpoint=endpoint, error=str(e))

    def connect(self):
        status = self.probe()
        if not status.ok:
            raise AdapterUnavailable(
                f"Cannot attach browser at {self.endpoint}: {status.error}. "
                "Start browser with scripts/start-browser-debug.ps1"
            )
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise AdapterUnavailable("playwright not installed") from e

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp(self.endpoint)
        if self._browser.contexts:
            self._context = self._browser.contexts[0]
        else:
            self._context = self._browser.new_context()
        return status

    def close(self) -> None:
        try:
            if self._browser:
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
            detail={"url": page.url, "title": page.title()},
        )

    def fill(self, locator: dict[str, str], value: str) -> ActionResult:
        ctx = self._ensure()
        page = ctx.pages[0] if ctx.pages else None
        if page is None:
            raise AdapterUnavailable("No browser page open")
        loc = self._resolve_locator(page, locator)
        loc.fill(value)
        return ActionResult(action="browser_fill", ok=True, detail={"locator": locator, "value_len": len(value)})

    def click(self, locator: dict[str, str]) -> ActionResult:
        ctx = self._ensure()
        page = ctx.pages[0] if ctx.pages else None
        if page is None:
            raise AdapterUnavailable("No browser page open")
        loc = self._resolve_locator(page, locator)
        loc.click()
        return ActionResult(action="browser_click", ok=True, detail={"locator": locator})

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
