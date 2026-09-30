"""search() and research() with fake Gemini, fake homepages and a fake crawler."""

from tests.test_discovery import HYPS, _router
from tests.test_hypotheses import BRIEF
from worker import pipeline, sessions
from worker.crawl import CrawlResult, Page
from worker.discovery.domains import normalize_domain
from worker.schemas import Evidence, FoundList, Research
from worker.steps.contacts import FoundContact, FoundContacts
from worker.steps.hypotheses import HypothesisList
from worker.steps.screen import ScreenBatch, ScreenItem

QUIET = {"progress": lambda _: None}


def _otter_good_rest_unrelated(prompt: str) -> ScreenBatch:
    domains = [line[4:].split(" ")[0] for line in prompt.splitlines() if line.startswith("### ")]
    return ScreenBatch(results=[
        ScreenItem(domain=d, holder_type="by_product" if d == "otter.ai" else "unrelated",
                   data_owner="company", data_match=5 if d == "otter.ai" else 1, languages=["English"],
                   regions=[], volume_signal=4, reason="r")
        for d in domains
    ])


def _searched(fake_ask, monkeypatch):
    monkeypatch.setattr("worker.steps.screen.fetch_homepage_text", lambda url: "text")
    fake_ask.responses[HypothesisList] = HypothesisList(hypotheses=HYPS)
    fake_ask.responses[FoundList] = _router
    fake_ask.responses[ScreenBatch] = _otter_good_rest_unrelated
    return pipeline.search(sessions.create("meetings", BRIEF), **QUIET)


def test_search_stops_after_scoring(fake_ask, monkeypatch):
    s = _searched(fake_ask, monkeypatch)
    cands = s.discovery.candidates

    assert s.meta.stage == "done"
    assert cands[0].domain == "otter.ai" and cands[0].fit >= 3.5  # ranked by fit
    assert all(c.research is None and not c.contacts for c in cands)  # nothing researched
    assert not fake_ask.calls_for(Research) and not fake_ask.calls_for(FoundContacts)
    # snowball ran after screening, seeded from the one well-scored company
    snowball_prompts = [c.prompt for c in fake_ask.calls if "compete with" in c.prompt]
    assert len(snowball_prompts) == 1 and "compete with Otter" in snowball_prompts[0]
    assert "The buyer needs data in: English" in snowball_prompts[0]
    rival = next(c for c in cands if c.domain == "rival-otter.com")
    assert rival.screened and rival.excluded  # new snowball company was screened too
    for step in ("Hypotheses", "Discovery searches", "Screen", "Source checks",
                 "Snowball searches", "Screen new", "total"):
        assert step in s.meta.timings, step

    # everything is on disk: the session reopens without rerunning
    loaded = sessions.load(s.path)
    assert loaded.discovery.candidates == cands and loaded.meta.relevant == 1
    # sources are checked before screening, so every company has a verdict, not just kept ones
    domains = {c.domain for c in cands}
    for h in loaded.discovery.hits:
        assert h.source_checked == (normalize_domain(h.website) in domains), h
    f = pipeline.funnel(loaded.discovery)
    assert f.found > f.unique == len(cands) and (f.screened, f.relevant, f.researched) == (len(cands), 1, 0)
    csv_rows = pipeline.candidates_csv(cands, BRIEF).splitlines()
    assert "fit" in csv_rows[0] and "languages" in csv_rows[0] and "price,founded,data_period" in csv_rows[0]
    assert csv_rows[1].startswith("Otter,otter.ai,")


def test_research_covers_only_selected_and_rescores(fake_ask, monkeypatch):
    s = _searched(fake_ask, monkeypatch)
    before = next(c for c in s.discovery.candidates if c.domain == "otter.ai").fit
    monkeypatch.setattr("worker.steps.contacts.fetch_html", lambda url: "<p>ceo@otter.ai</p>")
    monkeypatch.setattr("worker.steps.contacts.fetch_html_quick", lambda url: "<p>nothing</p>")
    fake_ask.responses[FoundContacts] = FoundContacts(contacts=[
        FoundContact(name="Made Up", role="CEO", email="made.up@otter.ai", phone=None,
                     linkedin_url=None, source_url="https://otter.ai/team"),
    ])
    fake_ask.responses[Research] = Research(
        data_held="meeting recordings", licence="Internal use only",
        licence_evidence=[Evidence(quote="Recordings are only used internally by Otter", url="https://otter.ai")],
        volume_estimate=None, volume_score=None, volume_evidence=[])
    crawled = []

    def fake_crawl(url):
        crawled.append(url)
        return CrawlResult(website=url, homepage=Page(url=url, text="Recordings are only used internally by Otter."))

    s = pipeline.research(sessions.load(s.path), ["otter.ai"], crawler=fake_crawl, **QUIET)

    assert crawled == ["https://otter.ai"]
    assert len(fake_ask.calls_for(Research)) == 1 and len(fake_ask.calls_for(FoundContacts)) == 1
    otter = next(c for c in s.discovery.candidates if c.domain == "otter.ai")
    assert otter.research.licence == "Internal use only"
    assert otter.scores.licence == 1 and otter.fit == 2.0 < before  # re-scored and capped
    assert [(k.email, k.verified) for k in otter.contacts] == [
        ("ceo@otter.ai", True),  # from the website
        ("made.up@otter.ai", False),  # search result not found on its source page
    ]
    assert s.meta.selected == ["otter.ai"] and "Research" in s.meta.timings
    loaded = sessions.load(s.path)
    assert pipeline.funnel(loaded.discovery).with_contacts == 1
    assert sum(c.research is not None for c in loaded.discovery.candidates) == 1


def test_quota_mid_search_saves_partial_results(fake_ask, monkeypatch):
    from worker.llm import QuotaExceeded

    monkeypatch.setattr("worker.steps.screen.fetch_homepage_text", lambda url: "text")
    fake_ask.responses[HypothesisList] = HypothesisList(hypotheses=HYPS)
    fake_ask.responses[FoundList] = _router

    def quota(prompt):
        raise QuotaExceeded("limit: 20 requests per day")

    fake_ask.responses[ScreenBatch] = quota

    s = pipeline.search(sessions.create("meetings", BRIEF), **QUIET)  # must not raise

    assert s.meta.stage == "partial"
    assert any("quota" in e.lower() for e in s.meta.errors)
    loaded = sessions.load(s.path)
    assert loaded.meta.stage == "partial"
    assert loaded.discovery.hits and loaded.discovery.candidates  # discovery results kept
