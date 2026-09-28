"""Full live-mode run with fake Gemini, fake homepages and a fake crawler."""

from tests.test_discovery import HYPS, _router
from tests.test_hypotheses import SPEC
from tests.test_screen import _label_by_prefix
from worker import pipeline, runs
from worker.crawl import CrawlResult, Page
from worker.schemas import DataSpec, FoundList
from worker.steps.contacts import FoundContact, FoundContacts
from worker.steps.hypotheses import HypothesisList
from worker.steps.screen import ScreenBatch


def test_live_run_funnel(fake_ask, monkeypatch, tmp_path):
    monkeypatch.setattr(runs, "RUNS_DIR", tmp_path)
    monkeypatch.setattr("worker.steps.screen.fetch_homepage_text", lambda url: "text")
    fake_ask.responses[DataSpec] = SPEC
    fake_ask.responses[HypothesisList] = HypothesisList(hypotheses=HYPS)
    fake_ask.responses[FoundList] = _router
    # label otter.ai yes, everything else no
    fake_ask.responses[ScreenBatch] = lambda p: _label_by_prefix(p.replace("### otter.ai", "### yes-otter.ai"))

    monkeypatch.setattr("worker.steps.contacts.fetch_html", lambda url: "<p>ceo@otter.ai</p>")
    monkeypatch.setattr("worker.steps.contacts.fetch_html_quick", lambda url: "<p>nothing</p>")
    fake_ask.responses[FoundContacts] = FoundContacts(contacts=[
        FoundContact(name="Made Up", role="CEO", email="made.up@otter.ai", phone=None,
                     linkedin_url=None, source_url="https://otter.ai/team"),
    ])
    crawled = []

    def fake_crawl(url):
        crawled.append(url)
        return CrawlResult(website=url, homepage=Page(url=url, text="hi"))

    out = pipeline.run("brief", progress=lambda _: None, crawler=fake_crawl)

    f = out.funnel
    assert f.found > f.deduped > 0
    assert f.relevant == sum(c.relevance in ("yes", "maybe") for c in out.candidates)
    assert f.profiled == len(crawled) <= pipeline.MAX_PROFILED
    otter = next(c for c in out.candidates if c.domain == "otter.ai")
    assert [(k.email, k.verified) for k in otter.contacts] == [
        ("ceo@otter.ai", True),  # from the website
        ("made.up@otter.ai", False),  # search result not found on its source page
    ]
    assert f.with_contacts == 1
    for name in ("spec", "hypotheses", "discovery", "output"):
        assert (tmp_path / out.run_dir.split("/")[-1] / f"{name}.json").exists()
