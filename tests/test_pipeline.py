"""Full live-mode run with fake Gemini, fake homepages and a fake crawler."""

from tests.test_discovery import HYPS, _router
from tests.test_hypotheses import SPEC
from worker import pipeline, runs
from worker.crawl import CrawlResult, Page
from worker.discovery import Discovery
from worker.discovery.domains import normalize_domain
from worker.schemas import DataSpec, FoundList
from worker.steps.contacts import FoundContact, FoundContacts
from worker.steps.hypotheses import HypothesisList
from worker.steps.screen import ScreenBatch, ScreenItem


def _otter_yes_rest_no(prompt: str) -> ScreenBatch:
    domains = [line[4:].split(" ")[0] for line in prompt.splitlines() if line.startswith("### ")]
    return ScreenBatch(results=[
        ScreenItem(domain=d, holder_type="by_product" if d == "otter.ai" else "unrelated",
                   data_owner="customers", label="yes" if d == "otter.ai" else "no", reason="r")
        for d in domains
    ])


def test_live_run_funnel(fake_ask, monkeypatch, tmp_path):
    monkeypatch.setattr(runs, "RUNS_DIR", tmp_path)
    monkeypatch.setattr("worker.steps.screen.fetch_homepage_text", lambda url: "text")
    fake_ask.responses[DataSpec] = SPEC
    fake_ask.responses[HypothesisList] = HypothesisList(hypotheses=HYPS)
    fake_ask.responses[FoundList] = _router
    fake_ask.responses[ScreenBatch] = _otter_yes_rest_no

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
    # snowball ran after screening, seeded from the one "yes" company
    snowball_prompts = [c.prompt for c in fake_ask.calls if "compete with" in c.prompt]
    assert len(snowball_prompts) == 1 and "compete with Otter" in snowball_prompts[0]
    rival = next(c for c in out.candidates if c.domain == "rival-otter.com")
    assert rival.relevance == "no"  # new snowball company was screened too
    assert not out.partial

    otter = next(c for c in out.candidates if c.domain == "otter.ai")
    assert [(k.email, k.verified) for k in otter.contacts] == [
        ("ceo@otter.ai", True),  # from the website
        ("made.up@otter.ai", False),  # search result not found on its source page
    ]
    assert f.with_contacts == 1
    for step in ("Analyse brief", "Hypotheses", "Discovery searches", "Screen", "Source checks",
                 "Snowball searches", "Screen new", "Deep crawl + contacts", "total"):
        assert step in out.timings, step
    # sources are only checked for companies the screen kept
    d = runs.load(tmp_path / out.run_dir.split("/")[-1], "discovery", Discovery)
    kept = {c.domain for c in out.candidates if c.relevance in ("yes", "maybe")}
    for h in d.hits:
        if normalize_domain(h.website) in kept:
            assert h.source_checked, h
        elif h.website and "g2.com" not in h.website:
            assert not h.source_checked, h
    for name in ("spec", "hypotheses", "discovery", "output"):
        assert (tmp_path / out.run_dir.split("/")[-1] / f"{name}.json").exists()


def test_quota_mid_run_saves_partial_results(fake_ask, monkeypatch, tmp_path):
    from worker.llm import QuotaExceeded

    monkeypatch.setattr(runs, "RUNS_DIR", tmp_path)
    monkeypatch.setattr("worker.steps.screen.fetch_homepage_text", lambda url: "text")
    fake_ask.responses[DataSpec] = SPEC
    fake_ask.responses[HypothesisList] = HypothesisList(hypotheses=HYPS)
    fake_ask.responses[FoundList] = _router

    def quota(prompt):
        raise QuotaExceeded("limit: 20 requests per day")

    fake_ask.responses[ScreenBatch] = quota

    out = pipeline.run("brief", progress=lambda _: None)  # must not raise

    assert out.partial
    assert any("quota" in e.lower() for e in out.errors)
    assert out.funnel.found > 0 and out.funnel.deduped > 0  # discovery results kept
    assert out.funnel.profiled == 0
    run_dir = tmp_path / out.run_dir.split("/")[-1]
    assert (run_dir / "output.json").exists() and (run_dir / "discovery.json").exists()
