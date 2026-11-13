"""search() and research() with fake Gemini, fake homepages and a fake crawler."""

from tests.test_discovery import HYPS, _router
from tests.test_hypotheses import BRIEF
from worker import pipeline, sessions
from worker.crawl import CrawlResult, Page
from worker.discovery.domains import normalize_domain
from worker.schemas import (
    Answer,
    ConfirmFound,
    Evidence,
    FoundList,
    HolderHypothesis,
    MustCheck,
    Research,
    Saleability,
    SellingSignal,
)
from worker.steps.contacts import FoundContact, FoundContacts
from worker.steps.hypotheses import ArchetypeHypotheses, HypothesisList
from worker.steps.screen import ScreenBatch, ScreenItem

QUIET = {"progress": lambda _: None}


def _otter_good_rest_unrelated(prompt: str) -> ScreenBatch:
    domains = [line[4:].split(" ")[0] for line in prompt.splitlines() if line.startswith("### ")]
    return ScreenBatch(results=[
        ScreenItem(domain=d, holder_type="untapped" if d == "otter.ai" else "unrelated",
                   data_owner="company", data_match=5 if d == "otter.ai" else 1, languages=["English"],
                   regions=[], volume_signal=4, reason="r", exists=4, allowed=4, willing=3)
        for d in domains
    ])


HOME = "Otter records and transcribes team meetings. Our own staff record the sample meetings we keep."


def _crawl(url):
    return CrawlResult(website=url, homepage=Page(url=url, text=HOME))


def _ev(quote, url="https://otter.ai"):
    return [Evidence(quote=quote, url=url)]


CONFIRMED = ConfirmFound(
    data_held="team meeting recordings", data_evidence=_ev("Otter records and transcribes team meetings"),
    checks=[MustCheck(category="modality", verdict="meets", finding="audio",
                      evidence=_ev("Otter records and transcribes team meetings"))],
    data_owner="company", owner_evidence=_ev("Our own staff record the sample meetings we keep"),
    signals=[SellingSignal(kind="own_data", summary="recorded by its own staff",
                           evidence=_ev("Our own staff record the sample meetings we keep"))])


def _searched(fake_ask, monkeypatch, screen=_otter_good_rest_unrelated):
    monkeypatch.setattr("worker.steps.screen.fetch_homepage_text", lambda url: "text")
    fake_ask.responses[HypothesisList] = HypothesisList(hypotheses=HYPS)
    fake_ask.responses[FoundList] = _router
    fake_ask.responses[ScreenBatch] = screen
    fake_ask.responses[ConfirmFound] = CONFIRMED
    return pipeline.search(sessions.create("meetings", BRIEF), crawler=_crawl, **QUIET)


def test_search_stops_after_scoring(fake_ask, monkeypatch):
    s = _searched(fake_ask, monkeypatch)
    cands = s.discovery.candidates

    assert s.meta.stage == "done"
    assert cands[0].domain == "otter.ai" and cands[0].fit >= 3.5  # ranked by fit
    assert all(c.research is None and not c.contacts for c in cands)  # nothing researched
    assert not fake_ask.calls_for(Research) and not fake_ask.calls_for(FoundContacts)
    # snowball ran after screening, seeded from the one well-scored company
    snowball_prompts = [c.prompt for c in fake_ask.calls if "organisations similar to" in c.prompt]
    assert len(snowball_prompts) == 1 and "organisations similar to Otter" in snowball_prompts[0]
    assert "The buyer needs data in: English" in snowball_prompts[0]
    rival = next(c for c in cands if c.domain == "rival-otter.com")
    assert rival.screened and rival.excluded  # new snowball company was screened too
    for step in ("Hypotheses", "Discovery searches", "Screen", "Source checks",
                 "Snowball searches", "Screen new", "Confirmation", "total"):
        assert step in s.meta.timings, step
    # one round (it found one qualifying company, too few for another), then the snowball
    assert [(r.round, r.qualifying) for r in s.discovery.rounds] == [(1, 1), (2, 0)]
    # the only fit was confirmed with quotes from its pages: owns the data, recorded by its staff
    otter = cands[0]
    assert (otter.confirmation.verdict, otter.confirmation.reason) == ("confirmed", "Fits; recorded by its own staff")
    assert len(fake_ask.calls_for(ConfirmFound)) == 1

    # everything is on disk: the session reopens without rerunning
    loaded = sessions.load(s.path)
    assert loaded.discovery.candidates == cands and loaded.meta.relevant == 1
    # sources are checked before screening, so every company has a verdict, not just kept ones
    domains = {c.domain for c in cands}
    for h in loaded.discovery.hits:
        assert h.source_checked == (normalize_domain(h.website) in domains), h
    f = pipeline.funnel(loaded.discovery)
    assert f.found > f.unique == len(cands) and (f.screened, f.relevant, f.researched) == (len(cands), 1, 0)
    assert (f.relevant_untapped, f.relevant_ready, f.confirmed, len(f.rounds)) == (1, 0, 1, 2)
    csv_rows = pipeline.candidates_csv(cands, BRIEF).splitlines()
    assert "fit" in csv_rows[0] and "languages" in csv_rows[0] and "price,founded,data_period" in csv_rows[0]
    assert csv_rows[0].endswith("verdict,verdict_reason,archetype,needs_trigger,triggers,markets,customer_of,first_round")
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
    internal = Evidence(quote="Recordings are only used internally by Otter", url="https://otter.ai")
    fake_ask.responses[Research] = Research(
        data_held="meeting recordings", licence="Unclear", licence_evidence=[],
        volume_estimate=None, volume_score=None, volume_evidence=[],
        saleability=Saleability(
            exists=Answer(score=5, reason="kept for years", evidence=[
                Evidence(quote="Otter keeps every recording for ten years", url="https://news.example/otter")]),
            allowed=Answer(score=1, reason="terms say internal use only", evidence=[internal]),
            willing=Answer(score=None, reason="nothing found"),
            obstacle="Terms say recordings are only used internally"))
    crawled = []

    def fake_crawl(url):
        crawled.append(url)
        return CrawlResult(website=url, homepage=Page(url=url, text="Recordings are only used internally by Otter."))

    s = pipeline.research(sessions.load(s.path), ["otter.ai"], crawler=fake_crawl, **QUIET)

    assert crawled == ["https://otter.ai"]
    assert len(fake_ask.calls_for(Research)) == 1 and len(fake_ask.calls_for(FoundContacts)) == 1
    otter = next(c for c in s.discovery.candidates if c.domain == "otter.ai")
    sal = otter.current_saleability()
    # allowed: verified on the crawled page; exists: the news page can't be fetched in tests, so
    # it falls back to the screen's initial 4; willing: research found nothing, screen said 3
    assert (sal.allowed.score, sal.allowed.verified) == (1, True)
    assert (sal.exists.score, sal.exists.verified, sal.willing.score) == (4, False, 3)
    assert sal.obstacle == "Terms say recordings are only used internally"
    assert otter.scores.licence == 1 and otter.fit == 2.0 < before  # grade "not sellable" caps the fit
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


def test_search_runs_another_round_while_rounds_find_qualifying_companies(fake_ask, monkeypatch):
    def most_good(prompt):
        domains = [line[4:].split(" ")[0] for line in prompt.splitlines() if line.startswith("### ")]
        return ScreenBatch(results=[
            ScreenItem(domain=d, holder_type="untapped", data_owner="company", data_match=5, languages=["English"],
                       regions=[], volume_signal=None, reason=f"holds {d}", exists=4, allowed=4, willing=3)
            for d in domains])

    fake_ask.responses[ArchetypeHypotheses] = ArchetypeHypotheses(hypotheses=[HolderHypothesis(
        category="Dubbing studios", rationale="r", search_queries=["query dub"])])
    s = _searched(fake_ask, monkeypatch, screen=most_good)
    rounds = {r.round: r for r in s.discovery.rounds}
    # archetypes nothing was found for get a hypothesis, searched first in round 2
    assert s.hypotheses[0].category == "Dubbing studios"
    assert any("- query dub" in c.prompt and "Already found" in c.prompt for c in fake_ask.calls)
    assert rounds[1].qualifying >= 3 and rounds[2].calls > 0  # round 1 found enough to try again
    later = [c.prompt for c in fake_ask.calls if "Already found, do not return any of these" in c.prompt]
    assert later and len(later) == rounds[2].calls
    # round 2 found too few new ones, so the search stopped there and snowballed
    assert rounds[2].qualifying < 3 and max(rounds) == 3
    assert all(h.round == 3 for h in s.discovery.hits if h.channel == "snowball")
