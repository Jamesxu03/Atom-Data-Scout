import pytest

from worker import crawl

JS_SHELL = "<html><head><title>App</title></head><body><div id=root></div></body></html>"
FULL = "<html><body><article>" + "<p>Real rendered content about meetings.</p>" * 20 + "</article></body></html>"


@pytest.fixture(autouse=True)
def tmp_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(crawl, "CACHE_DIR", tmp_path)


class FakeBrowser:
    def __init__(self, html=FULL):
        self.html, self.rendered = html, []

    def render(self, url):
        self.rendered.append(url)
        return self.html


def test_js_shell_cached_by_screen_still_gets_rendered_in_deep_crawl(monkeypatch):
    monkeypatch.setattr(crawl, "_fetch_httpx", lambda url: JS_SHELL)
    crawl.fetch_homepage_text("https://app.example")  # screen caches the shell
    browser = FakeBrowser()

    html = crawl.fetch_html("https://app.example", browser)

    assert browser.rendered == ["https://app.example"]
    assert "Real rendered content" in html
    # second call is served from the rendered cache
    assert crawl.fetch_html("https://app.example", FakeBrowser(html="x")) == html


def test_static_page_never_starts_browser(monkeypatch):
    monkeypatch.setattr(crawl, "_fetch_httpx", lambda url: FULL)
    browser = FakeBrowser()
    crawl.fetch_html("https://static.example", browser)
    assert browser.rendered == []


def test_fetch_error_and_render_error_raises_fetch_error(monkeypatch):
    def refuse(url):
        raise ConnectionError("refused")

    class BrokenBrowser:
        def render(self, url):
            raise RuntimeError("browser died")

    monkeypatch.setattr(crawl, "_fetch_httpx", refuse)
    with pytest.raises(ConnectionError):
        crawl.fetch_html("https://down.example", BrokenBrowser())


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://acme.com/privacy", True),
        ("https://www.acme.com/privacy", True),
        ("https://help.acme.com/contact", True),
        ("https://notacme.com/privacy", False),
        ("https://acme.com.evil.io/privacy", False),
        ("https://other.com/acme.com", False),
    ],
)
def test_same_site(url, ok):
    assert crawl.same_site(url, "https://www.acme.com") is ok


def test_concurrent_requests_for_one_url_fetch_it_once(monkeypatch):
    import threading
    import time as _time

    calls = []

    def slow_fetch(url):
        calls.append(url)
        _time.sleep(0.2)
        return FULL

    monkeypatch.setattr(crawl, "_fetch_httpx", slow_fetch)
    results = []
    threads = [threading.Thread(target=lambda: results.append(crawl.fetch_html_quick("https://g2.example/cat")))
               for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert calls == ["https://g2.example/cat"]
    assert results == [FULL] * 10


def test_recent_failure_is_not_retried_by_other_callers(monkeypatch):
    calls = []

    def blocked(url):
        calls.append(url)
        raise ConnectionError("403-style block")

    monkeypatch.setattr(crawl, "_fetch_httpx", blocked)
    for _ in range(5):
        with pytest.raises(ConnectionError):
            crawl.fetch_html_quick("https://blocks.example")
    assert len(calls) == 1


def test_cache_expires_after_ttl(monkeypatch, tmp_path):
    import os

    pages = iter(["<p>old policy</p>", "<p>new policy</p>"])
    monkeypatch.setattr(crawl, "_fetch_httpx", lambda url: next(pages))
    assert crawl.fetch_html_quick("https://acme.example/privacy") == "<p>old policy</p>"
    assert crawl.fetch_html_quick("https://acme.example/privacy") == "<p>old policy</p>"  # cached

    path = crawl._cache_path("https://acme.example/privacy")
    week_ago = path.stat().st_mtime - crawl.CACHE_TTL_SECONDS - 1
    os.utime(path, (week_ago, week_ago))
    assert crawl.fetch_html_quick("https://acme.example/privacy") == "<p>new policy</p>"
    assert not list(tmp_path.glob("*.tmp"))  # atomic writes leave no temp files behind
