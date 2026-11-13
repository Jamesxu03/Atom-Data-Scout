"""Big brands and regulated institutions (WS6): capped until a dated trigger is verified."""

from datetime import date

from tests.test_hypotheses import BRIEF
from worker.config import TRIGGER_CAP, TRIGGER_YEARS
from worker.crawl import CrawlResult
from worker.schemas import (
    Answer,
    Candidate,
    Evidence,
    LawSignal,
    Market,
    MarketPlan,
    Saleability,
    Scores,
    Trigger,
)
from worker.scoring import answers, rescore
from worker.steps import triggers
from worker.steps.research import dated, recent, verify_triggers

YEAR = date.today().year
NEWS = f"https://news.example/{YEAR}/03/bank-administration"
PAGES = {NEWS: f"<p>In March {YEAR} the bank entered administration and put its call centre up for sale.</p>",
         "https://news.example/old": "<p>In 2009 the bank sold its call centre to a rival operator.</p>"}


def _bank(domain="bigbank.example", **kw) -> Candidate:
    return Candidate(name="Big Bank", website=f"https://{domain}", domain=domain, channels=["distressed"],
                     source_urls=[], reason="retail bank recording advisory calls", holder_type="untapped",
                     data_owner="company", languages=["English"], **{"needs_trigger": True, **kw},
                     saleability=Saleability(exists=Answer(score=5), allowed=Answer(score=4), willing=Answer(score=1)),
                     scores=Scores(data_match=5, coverage=5))


def _distress(quote=f"In March {YEAR} the bank entered administration and put its call centre up for sale",
              url=NEWS, when=str(YEAR), ongoing=False) -> Trigger:
    return Trigger(kind="distress", date=when, ongoing=ongoing, summary="entered administration",
                   evidence=[Evidence(quote=quote, url=url)])


def test_capped_until_a_trigger_and_the_trigger_sets_willing():
    bank = _bank()
    rescore(bank, BRIEF)
    assert bank.fit == TRIGGER_CAP  # a screen 1 on willing for being a bank no longer decides it
    bank.triggers = [_distress()]
    rescore(bank, BRIEF)
    assert answers(bank, BRIEF)["willing"] == 5 and bank.fit > 3.5
    law = Trigger(kind="law", date=str(YEAR), ongoing=True, summary="Data Act", evidence=[])
    bank.triggers = [law]
    rescore(bank, BRIEF)
    assert answers(bank, BRIEF)["willing"] == 3 and bank.fit > TRIGGER_CAP


def test_a_trigger_needs_a_recent_dated_quote_found_on_the_page():
    fetch = PAGES.__getitem__
    crawl = CrawlResult(website="https://bigbank.example")
    kept = verify_triggers([_distress()], crawl, fetch)
    assert [t.summary for t in kept] == ["entered administration"]
    # the year is in the address instead of the quote: still dated
    in_url = _distress(quote="the bank entered administration and put its call centre up for sale")
    assert dated(in_url, in_url.evidence[0]) and verify_triggers([in_url], crawl, fetch)
    # not on the page, too old, or undated: dropped
    assert not verify_triggers([_distress(quote=f"In {YEAR} the bank was bought by a hedge fund overnight")],
                               crawl, fetch)
    old = _distress(quote="In 2009 the bank sold its call centre to a rival operator",
                    url="https://news.example/old", when="2009")
    assert not recent(old) and not verify_triggers([old], crawl, fetch)
    assert recent(old.model_copy(update={"ongoing": True}))  # still going on counts
    assert recent(_distress(when=str(YEAR - TRIGGER_YEARS)))


def test_check_attaches_the_markets_laws_that_cover_it(fake_ask):
    law = LawSignal(title="Data Sharing Act", date=str(YEAR), summary="banks may share call data for AI research",
                    sectors=["banks"], evidence=[Evidence(quote="Banks may share recordings", url="https://gov.example")])
    fake_ask.responses[triggers.TriggerFound] = triggers.TriggerFound(triggers=[_distress()], laws=[1, 1, 7])
    found = triggers.check(BRIEF, _bank(), [law], PAGES.__getitem__)
    assert [(t.kind, t.summary) for t in found] == [
        ("distress", "entered administration"), ("law", "Data Sharing Act: banks may share call data for AI research")]
    (call,) = fake_ask.calls
    assert call.use_search and '"Big Bank administration"' in call.prompt
    assert "1. Data Sharing Act" in call.prompt and "covers banks" in call.prompt


def test_check_all_checks_the_best_unchecked_and_lifts_the_cap(fake_ask):
    fake_ask.responses[triggers.TriggerFound] = triggers.TriggerFound(triggers=[_distress()], laws=[])
    bank, other = _bank(), _bank(domain="other.example", needs_trigger=False)
    weaker = _bank(domain="weak.example")
    weaker.scores = Scores(data_match=2)
    for c in (bank, other, weaker):
        rescore(c, BRIEF)
    assert [c.domain for c in triggers.to_check([weaker, other, bank])] == ["bigbank.example", "weak.example"]
    errors, quota = triggers.check_all(BRIEF, [bank, other], [], lambda _: None, PAGES.__getitem__)
    assert (errors, quota) == ([], None)
    assert bank.trigger_checked and bank.fit > TRIGGER_CAP and not other.trigger_checked
    assert triggers.to_check([bank]) == []  # checked once


def test_law_signals_keep_recent_laws_with_verified_quotes(fake_ask):
    page = {"https://gov.example/act": f"<p>The Data Sharing Act {YEAR} lets banks share recordings for research.</p>"}
    fake_ask.responses[triggers.LawList] = triggers.LawList(laws=[
        LawSignal(title="Data Sharing Act", date=str(YEAR), summary="s", sectors=["banks"], evidence=[Evidence(
            quote=f"The Data Sharing Act {YEAR} lets banks share recordings for research",
            url="https://gov.example/act")]),
        LawSignal(title="Old Act", date="2001", summary="s", sectors=[], evidence=[]),
        LawSignal(title="Made Up Act", date=str(YEAR), summary="s", sectors=[], evidence=[Evidence(
            quote="This sentence is on no page at all", url="https://gov.example/act")]),
    ])
    laws = triggers.law_signals(BRIEF, Market(country="Saudi Arabia", language="Arabic"), page.__getitem__)
    assert [law.title for law in laws] == ["Data Sharing Act"]
    assert "Search in Arabic and English" in fake_ask.calls[0].prompt


def test_laws_for_a_company_come_from_the_markets_that_found_it():
    law_sa = LawSignal(title="SA", date="2025", summary="", sectors=[], evidence=[])
    law_de = LawSignal(title="DE", date="2025", summary="", sectors=[], evidence=[])
    plans = [MarketPlan(market=Market(country="Saudi Arabia", language="Arabic"), laws=[law_sa]),
             MarketPlan(market=Market(country="Germany", language="German"), laws=[law_de])]
    assert triggers.laws_for(_bank(markets=["Germany (German)"]), plans) == [law_de]
    assert triggers.laws_for(_bank(), plans) == [law_sa, law_de]  # found by no local search: every market's
