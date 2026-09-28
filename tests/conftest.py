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
