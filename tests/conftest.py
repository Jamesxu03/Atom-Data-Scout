import pytest

from tests.fake_llm import FakeAsk


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Tests never fetch real pages; a test that needs pages patches in its own fetcher."""
    import httpx

    def blocked(url, *a, **kw):
        raise httpx.ConnectError(f"network disabled in tests: {url}")

    monkeypatch.setattr("worker.crawl._fetch_httpx", blocked)
    monkeypatch.setattr(httpx, "head", blocked)
    monkeypatch.setattr("worker.crawl._recent_failures", {})

    def no_browser(self, url):
        raise RuntimeError(f"browser disabled in tests: {url}")

    monkeypatch.setattr("worker.crawl.Browser.render", no_browser)


@pytest.fixture(autouse=True)
def tmp_sessions(monkeypatch, tmp_path):
    """Sessions are written under the test's temp folder, never data/sessions/."""
    monkeypatch.setattr("worker.sessions.SESSIONS_DIR", tmp_path / "sessions")
    return tmp_path / "sessions"


@pytest.fixture(autouse=True)
def tmp_crm(monkeypatch, tmp_path):
    """Clients and outreach go to a database in the test's temp folder, never data/crm.sqlite3."""
    monkeypatch.setattr("worker.crm.CRM_DB", tmp_path / "crm.sqlite3")
    return tmp_path / "crm.sqlite3"


@pytest.fixture
def fake_ask(monkeypatch) -> FakeAsk:
    """Replace worker.llm.ask for the duration of a test."""
    fake = FakeAsk()
    monkeypatch.setattr("worker.llm.ask", fake)
    return fake


def pytest_collection_modifyitems(config, items):
    import os

    if os.getenv("RUN_LIVE"):
        return
    skip = pytest.mark.skip(reason="live Gemini call — set RUN_LIVE=1 to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
