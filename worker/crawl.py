"""Fetch a company's homepage, privacy policy and terms as clean text.

httpx + trafilatura first; falls back to playwright for JS-rendered pages.
HTML is cached in data/cache/ so re-runs don't re-download: raw httpx responses and
browser-rendered pages are cached separately, so a JS shell cached by the cheap homepage
screen never stops the deep crawl from rendering the page.
Never raises for a bad site — failures are recorded in CrawlResult.errors.
"""

import hashlib
import ipaddress
import os
import re
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura
from pydantic import BaseModel

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"
USER_AGENT = "Mozilla/5.0 (compatible; AtomDataScout/0.1)"
MIN_TEXT_CHARS = 200  # less than this = probably a JS shell, try playwright
# 5s to connect, 10s per read: a site that slow is treated as unreachable ("maybe"), not waited on
HTTP_TIMEOUT = httpx.Timeout(10, connect=5)
RENDER_SETTLE_MS = 3000  # max wait for client-side rendering after DOMContentLoaded
CACHE_TTL_SECONDS = 7 * 24 * 3600  # re-fetch pages older than a week (policies change)
FAILURE_MEMO_SECONDS = 60  # a URL that just failed isn't re-requested by other threads

LINK_PATTERNS = {
    "privacy": re.compile(r"privacy|data[-_ ]?protection|gdpr", re.I),
    "terms": re.compile(r"terms|conditions|\btos\b|legal", re.I),
    "contact": re.compile(r"contact|get[-_ ]in[-_ ]touch", re.I),
}


class Page(BaseModel):
    url: str
    text: str


class CrawlResult(BaseModel):
    website: str
    homepage: Page | None = None
    privacy: Page | None = None
    terms: Page | None = None
    contact: Page | None = None
    errors: list[str] = []

    def pages(self) -> list[Page]:
        return [p for p in (self.homepage, self.privacy, self.terms, self.contact) if p]


def _cache_path(url: str, rendered: bool = False) -> Path:
    suffix = ".rendered.html" if rendered else ".html"
    return CACHE_DIR / (hashlib.sha256(url.encode()).hexdigest()[:24] + suffix)


def _read_cache(path: Path) -> str | None:
    """Cached HTML, or None if missing or older than CACHE_TTL_SECONDS."""
    try:
        if time.time() - path.stat().st_mtime > CACHE_TTL_SECONDS:
            return None
        return path.read_text(encoding="utf-8", errors="ignore")
    except FileNotFoundError:
        return None


def _write_cache(path: Path, html: str) -> None:
    """Write to a temp file then rename, so a reader never sees half a page."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(html)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# One fetch per URL at a time: threads asking for a page that's already being fetched wait
# for it (then read the cache) instead of hitting the site again — directory pages listing
# ten companies would otherwise be requested ten times at once and get us blocked.
_url_locks: dict[str, threading.Lock] = {}
_url_locks_guard = threading.Lock()
_recent_failures: dict[str, tuple[float, Exception]] = {}


# trafilatura parses with one module-level lxml parser, which isn't thread-safe: extracting in
# several threads at once corrupts libxml2's memory and aborts the whole process (no traceback).
# Extraction is quick next to the downloads, so it takes turns; fetching stays parallel.
_extract_lock = threading.Lock()


def extract_text(html: str, **options) -> str:
    """trafilatura.extract, one call at a time. "" when there's no main text."""
    with _extract_lock:
        return trafilatura.extract(html, **options) or ""


def _lock_for(url: str) -> threading.Lock:
    with _url_locks_guard:
        return _url_locks.setdefault(url, threading.Lock())


def check_public(url: str) -> None:
    """Raise ValueError unless url is http(s) to a public host. URLs come from search results and
    Gemini, so one pointing at this machine or the local network is never fetched."""
    parts = urlparse(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        raise ValueError(f"not a web address: {url}")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError(f"local address: {url}")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return  # a domain name
    if not ip.is_global:
        raise ValueError(f"private address: {url}")


def _fetch_httpx(url: str) -> str:
    check_public(url)
    with httpx.Client(follow_redirects=True, timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}) as c:
        r = c.get(url)
        r.raise_for_status()
        return r.text


class Browser:
    """One headless Chromium, started on first use and shared by every page of a crawl.

    Playwright's sync API is per-thread, so all browser work runs on one thread of its own:
    any thread may call render, and renders take turns.
    """

    def __init__(self):
        self._pw = self._browser = None
        self._thread = ThreadPoolExecutor(1, thread_name_prefix="browser")

    def render(self, url: str) -> str:
        return self._thread.submit(self._render, url).result()

    def _render(self, url: str) -> str:
        check_public(url)
        if self._browser is None:
            from playwright.sync_api import sync_playwright

            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch()
        page = self._browser.new_page(user_agent=USER_AGENT)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            try:  # until the network goes quiet, but never more than RENDER_SETTLE_MS
                page.wait_for_load_state("networkidle", timeout=RENDER_SETTLE_MS)
            except Exception:  # still busy (analytics, chat widgets): take what has rendered
                pass
            return page.content()
        finally:
            page.close()

    def close(self) -> None:
        if self._browser is not None:
            self._thread.submit(self._close).result()
        self._thread.shutdown()

    def _close(self) -> None:
        if self._browser is not None:
            self._browser.close()
            self._pw.stop()
            self._pw = self._browser = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _has_text(html: str) -> bool:
    return len(extract_text(html)) >= MIN_TEXT_CHARS


def fetch_html_quick(url: str) -> str:
    """Cached httpx fetch with no browser fallback — for cheap checks. Raises on failure."""
    if not url.startswith("http"):
        url = "https://" + url
    path = _cache_path(url)
    with _lock_for(url):
        if (html := _read_cache(path)) is not None:
            return html
        failed = _recent_failures.get(url)
        if failed and time.time() - failed[0] < FAILURE_MEMO_SECONDS:
            raise failed[1]
        try:
            html = _fetch_httpx(url)
        except Exception as e:
            _recent_failures[url] = (time.time(), e)
            raise
        _write_cache(path, html)
        return html


def fetch_html(url: str, browser: Browser | None = None) -> str:
    """HTML for url: rendered cache, else httpx (cached), else render with playwright.

    The browser is only used when httpx fails or returns a JS shell with no main text.
    """
    rendered_path = _cache_path(url, rendered=True)
    if (html := _read_cache(rendered_path)) is not None:
        return html

    raw, raw_error = "", None
    try:
        raw = fetch_html_quick(url)
    except Exception as e:  # blocked or JS-only site: the browser may still get it
        raw_error = e
    if raw and _has_text(raw):
        return raw

    try:
        if browser is not None:
            html = browser.render(url)
        else:
            with Browser() as b:
                html = b.render(url)
    except Exception as render_error:
        if raw:  # a thin page beats nothing
            return raw
        raise (raw_error or render_error) from None  # the httpx error is usually more telling
    _write_cache(rendered_path, html)
    return html


def fetch_rendered(url: str, browser: Browser) -> str:
    """Browser-rendered HTML for url, cached with the deep crawl's renders. Raises on failure."""
    if not url.startswith("http"):
        url = "https://" + url
    path = _cache_path(url, rendered=True)
    if (html := _read_cache(path)) is not None:
        return html
    html = browser.render(url)
    _write_cache(path, html)
    return html


def fetch_homepage_text(website: str) -> str:
    """Cheap homepage fetch for screening: httpx only (no browser), cached. Raises on failure.

    Falls back to <title> + meta description when there's no main text (JS-heavy sites).
    """
    html = fetch_html_quick(website)
    text = extract_text(html)
    if len(text) < MIN_TEXT_CHARS:
        title = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        desc = re.search(
            r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']*)', html, re.I
        )
        text = " ".join(m.group(1).strip() for m in (title, desc) if m) + "\n" + text
    if not text.strip():
        raise ValueError("no text on homepage")
    return text.strip()


def same_site(url: str, base_url: str) -> bool:
    """url is on base_url's site or a subdomain of it (notacme.com is not acme.com)."""
    host = (urlparse(url).hostname or "").lower()
    base = (urlparse(base_url).hostname or "").lower().removeprefix("www.")
    return bool(base) and (host == base or host.endswith("." + base))


def _find_link(html: str, base_url: str, kind: str) -> str | None:
    pattern = LINK_PATTERNS[kind]
    links = re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.I | re.S)
    # prefer a match in the URL itself, then fall back to the link label
    for match_label in (False, True):
        for href, label in links:
            target = re.sub(r"<[^>]+>", "", label) if match_label else href
            if not pattern.search(target):
                continue
            url = urljoin(base_url, href)
            if same_site(url, base_url):
                return url
    return None


def _page(url: str, browser: Browser) -> Page:
    html = fetch_html(url, browser)
    text = extract_text(html, include_links=False)
    if not text:
        raise ValueError("no main text extracted")
    return Page(url=url, text=text)


def crawl(website: str) -> CrawlResult:
    if not website.startswith("http"):
        website = "https://" + website
    result = CrawlResult(website=website)

    with Browser() as browser:  # only launched if some page needs rendering
        try:
            home_html = fetch_html(website, browser)
            result.homepage = _page(website, browser)
        except Exception as e:
            result.errors.append(f"homepage: {type(e).__name__}: {e}")
            return result

        links = {}
        for kind in ("privacy", "terms", "contact"):
            if link := _find_link(home_html, website, kind):
                links[kind] = link
            else:
                result.errors.append(f"{kind}: no link found on homepage")
        with ThreadPoolExecutor(3) as pool:  # fetched side by side; renders take turns in the browser
            futures = {kind: pool.submit(_page, link, browser) for kind, link in links.items()}
        for kind, future in futures.items():
            try:
                setattr(result, kind, future.result())
            except Exception as e:
                result.errors.append(f"{kind}: {type(e).__name__}: {e}")
    return result

