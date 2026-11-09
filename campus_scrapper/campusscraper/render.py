"""Real-browser fetching with Playwright (Chromium).

Used when (a) a page is an empty JavaScript shell, or (b) a site's firewall
answers 401/403/429/503 to non-browser clients. One browser context is kept for
the whole run so cookies set by the site's firewall are reused.

Install once:  pip install playwright  &&  python -m playwright install chromium
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

from .config import ScrapeConfig

CHALLENGE_RE = re.compile(r"cf-chl|challenge-platform|Just a moment\.\.\.|Checking your browser|"
                          r"_Incapsula_|ddos-guard|perimeterx|px-captcha", re.I)
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


class BrowserUnavailable(RuntimeError):
    pass


@dataclass
class BrowserResponse:
    status: int
    url: str
    html: str
    content_type: str
    text: str  # raw body text (for robots.txt)


class BrowserRenderer:
    def __init__(self, cfg: ScrapeConfig):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserUnavailable(
                "Playwright is not installed. Run: pip install -r requirements.txt "
                "and then: python -m playwright install chromium") from exc
        self._pw = sync_playwright().start()
        args = ["--disable-blink-features=AutomationControlled"]
        headless = not cfg.show_browser
        try:
            # 'chromium' channel = full Chromium in the new headless mode (closer to a normal browser).
            self._browser = self._pw.chromium.launch(headless=headless, channel="chromium", args=args)
        except Exception:
            try:
                self._browser = self._pw.chromium.launch(headless=headless, args=args)
            except Exception as exc:
                self._pw.stop()
                raise BrowserUnavailable(
                    f"Chromium could not start ({exc}). Run: python -m playwright install chromium") from exc
        self._context = self._browser.new_context(
            user_agent=BROWSER_UA, locale="en-IN", viewport={"width": 1366, "height": 900},
            extra_http_headers={"Accept-Language": "en-IN,en;q=0.9"},
            service_workers="block" if cfg.strict_public_network else "allow",
        )
        self._context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        # Skip heavy resources; page text and data are all that is needed.
        self._context.route(re.compile(r"\.(?:png|jpe?g|gif|webp|svg|ico|woff2?|ttf|otf|mp4|webm|mp3)(?:\?|$)", re.I),
                            lambda route: route.abort())
        if cfg.strict_public_network and not cfg.allow_private_hosts:
            from .network_guard import public_destination
            def guard(route):
                try:
                    public_destination(route.request.url)
                    if route.request.resource_type in {"image", "font", "media"}:
                        route.abort()
                    else:
                        route.continue_()
                except Exception:
                    route.abort()
            self._context.route("**/*", guard)
        self._timeout = int(cfg.timeout_seconds * 1000)

    def fetch(self, url: str, expand: bool = True) -> BrowserResponse:
        page = self._context.new_page()
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=self._timeout)
            status = resp.status if resp else 0
            ctype = (resp.headers.get("content-type", "") if resp else "").split(";")[0].strip().lower()
            if ctype.startswith("text/plain") or url.endswith("robots.txt"):
                body = resp.text() if resp else ""
                return BrowserResponse(status, page.url, body, ctype or "text/plain", body)
            # Firewall interstitials usually clear themselves within a few seconds.
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and CHALLENGE_RE.search(page.content()[:30000]):
                page.wait_for_timeout(1000)
                status = 200
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            if expand:
                self._expand(page)
            html = page.content()
            if status in (401, 403, 429, 503) and not CHALLENGE_RE.search(html[:30000]) and _has_content(html):
                status = 200  # interstitial passed; the final document is the real page
            return BrowserResponse(status, page.url, html, ctype or "text/html", "")
        finally:
            page.close()

    @staticmethod
    def _expand(page) -> None:
        """Scroll and press 'load more' a few times so lazy lists show more doctors."""
        for _ in range(6):
            before = page.evaluate("document.body ? document.body.scrollHeight : 0")
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(700)
            button = page.locator("button:has-text('Load More'), a:has-text('Load More'), "
                                  "button:has-text('View More'), button:has-text('Show More')").first
            try:
                if button.count() and button.is_visible():
                    button.click(timeout=3000)
                    page.wait_for_timeout(1500)
                    continue
            except Exception:
                pass
            if page.evaluate("document.body ? document.body.scrollHeight : 0") <= before:
                break

    def render(self, url: str) -> str:
        return self.fetch(url).html

    def close(self) -> None:
        try:
            self._context.close()
            self._browser.close()
        finally:
            self._pw.stop()


def _has_content(html: str) -> bool:
    text = re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", html, flags=re.S | re.I)
    return len(text.split()) > 60
