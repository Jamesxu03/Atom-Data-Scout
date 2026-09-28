"""Fetch a company's homepage, privacy policy and terms as clean text.

httpx + trafilatura first; falls back to playwright for JS-rendered pages.
Raw HTML is cached in data/cache/ so re-runs don't re-download.
Never raises for a bad site — failures are recorded in CrawlResult.errors.
"""

import hashlib
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura
from pydantic import BaseModel

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"
USER_AGENT = "Mozilla/5.0 (compatible; AtomDataScout/0.1)"
MIN_TEXT_CHARS = 200  # less than this = probably a JS shell, try playwright

LINK_PATTERNS = {
    "privacy": re.compile(r"privacy|data[-_ ]?protection|gdpr", re.I),
    "terms": re.compile(r"terms|conditions|\btos\b|legal", re.I),
}


class Page(BaseModel):
    url: str
    text: str


class CrawlResult(BaseModel):
    website: str
    homepage: Page | None = None
    privacy: Page | None = None
    terms: Page | None = None
    errors: list[str] = []


def _cache_path(url: str) -> Path:
    return CACHE_DIR / (hashlib.sha256(url.encode()).hexdigest()[:24] + ".html")


def _fetch_httpx(url: str) -> str:
    with httpx.Client(follow_redirects=True, timeout=20, headers={"User-Agent": USER_AGENT}) as c:
        r = c.get(url)
        r.raise_for_status()
        return r.text


def _fetch_playwright(url: str) -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(user_agent=USER_AGENT)
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(3000)  # give client-side rendering a moment
            return page.content()
        finally:
            browser.close()


def fetch_html(url: str) -> str:
    """Return HTML for url, using cache; httpx first, playwright if text looks empty."""
    path = _cache_path(url)
    if path.exists():
        return path.read_text(encoding="utf-8", errors="ignore")

    html = ""
    try:
        html = _fetch_httpx(url)
    except Exception:
        pass
    if len(trafilatura.extract(html) or "") < MIN_TEXT_CHARS:
        html = _fetch_playwright(url) or html  # let playwright errors propagate

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return html


def _find_link(html: str, base_url: str, kind: str) -> str | None:
    pattern = LINK_PATTERNS[kind]
    base_host = urlparse(base_url).netloc
    links = re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.I | re.S)
    # prefer a match in the URL itself, then fall back to the link label
    for match_label in (False, True):
        for href, label in links:
            target = re.sub(r"<[^>]+>", "", label) if match_label else href
            if not pattern.search(target):
                continue
            url = urljoin(base_url, href)
            # stay on the company's own site (or a subdomain of it)
            if urlparse(url).netloc.endswith(base_host.removeprefix("www.")):
                return url
    return None


def _page(url: str) -> Page:
    html = fetch_html(url)
    text = trafilatura.extract(html, include_links=False) or ""
    if not text:
        raise ValueError("no main text extracted")
    return Page(url=url, text=text)


def crawl(website: str) -> CrawlResult:
    if not website.startswith("http"):
        website = "https://" + website
    result = CrawlResult(website=website)

    try:
        home_html = fetch_html(website)
        result.homepage = _page(website)
    except Exception as e:
        result.errors.append(f"homepage: {type(e).__name__}: {e}")
        return result

    for kind in ("privacy", "terms"):
        link = _find_link(home_html, website, kind)
        if not link:
            result.errors.append(f"{kind}: no link found on homepage")
            continue
        try:
            setattr(result, kind, _page(link))
        except Exception as e:
            result.errors.append(f"{kind}: {type(e).__name__}: {e}")
    return result


if __name__ == "__main__":
    import sys

    r = crawl(sys.argv[1] if len(sys.argv) > 1 else "https://otter.ai")
    for kind in ("homepage", "privacy", "terms"):
        p = getattr(r, kind)
        print(f"{kind:9} {p.url if p else '-':60} {len(p.text) if p else 0:>7} chars")
    for e in r.errors:
        print("ERROR", e)
