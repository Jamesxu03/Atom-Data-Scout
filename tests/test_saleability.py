"""Ready sources and untapped holders: the saleability grade, its caps, the screen and research
rules behind it, emails, and loading sessions saved with earlier holder types."""

import csv
import io

import pytest

from tests.test_hypotheses import BRIEF, brief_with
from worker import archetypes, pipeline
from worker.brief_template import (
    CUSTOM_COLLECTION,
    CUSTOMER_CONSENT,
    CUSTOMER_EXCLUDE,
    CUSTOMER_LATER,
    OPEN_CONDITIONS,
    READY,
    UNTAPPED,
    Brief,
    Pick,
)
from worker.config import LOW_SALEABILITY_CAP, NOT_SELLABLE_CAP
from worker.crawl import CrawlResult, Page
from worker.discovery import pick_seeds, seed_types
from worker.schemas import Answer, Candidate, Evidence, Research, Saleability, Scores
from worker.scoring import answers, fit, grade, must_failures, saleability_label
from worker.steps.email import build_prompt as email_prompt
from worker.steps.research import build_prompt, verify_research
from worker.steps.screen import ScreenBatch, ScreenItem, screen

TOP = Scores(data_match=5, coverage=5, volume=5)


def _cand(domain="acme.com", **kw):
    return Candidate(name="Acme", website=f"https://{domain}", domain=domain, channels=["product_search"],
                     source_urls=[], reason="r", **kw)


def _sal(exists=None, allowed=None, willing=None, verified=False, **kw):
    def a(score):
        return Answer(score=score, reason=f"scored {score}", verified=verified and score is not None)

    return Saleability(exists=a(exists), allowed=a(allowed), willing=a(willing), **kw)


def _untapped(*scores, owner="company", **kw):
    return _cand(holder_type="untapped", data_owner=owner, saleability=_sal(*scores, **kw), scores=TOP)


# --- the grade --------------------------------------------------------------------------------

@pytest.mark.parametrize(("scores", "expected", "label"), [
    ((5, 4, 5), 4, "High (initial)"),
    ((5, 3, 4), 3, "Medium (initial)"),
    ((4, 2, 5), 2, "Low (initial)"),
    ((5, 5, 1), 1, "Not sellable (initial)"),
    ((5, 4, None), 4, "High (initial)"),  # two known answers are enough for High
    ((5, None, None), 3, "Medium (initial)"),  # one good answer says little about the others
    ((1, None, None), 1, "Not sellable (initial)"),  # one bad answer is enough
    ((None, None, None), None, "Unknown"),
])
def test_grade_is_the_lowest_answer(scores, expected, label):
    c = _untapped(*scores)
    assert (grade(c, BRIEF), saleability_label(c, BRIEF)) == (expected, label)


def test_grade_caps_the_fit():
    assert fit(_untapped(5, 1, 5).model_copy(update={"scores": TOP.model_copy(update={"licence": 1})}),
               BRIEF) == NOT_SELLABLE_CAP
    assert fit(_untapped(5, 2, 5).model_copy(update={"scores": TOP.model_copy(update={"licence": 2})}),
               BRIEF) == LOW_SALEABILITY_CAP
    medium = _untapped(5, 3, 5).model_copy(update={"scores": TOP.model_copy(update={"licence": 3})})
    assert fit(medium, BRIEF) == round((0.4 * 5 + 0.25 * 5 + 0.2 * 3 + 0.15 * 5), 2)  # no cap


def test_customers_data_needs_consent_unless_d6_says_otherwise():
    c = _untapped(5, 5, 5, owner="customers")
    assert answers(c, BRIEF)["allowed"] == 2  # D6 not set: needs consent
    assert answers(c, brief_with(customer_data=Pick(values=[CUSTOMER_CONSENT])))["allowed"] == 2
    assert answers(c, brief_with(customer_data=Pick(values=[CUSTOMER_EXCLUDE])))["allowed"] == 1
    assert answers(c, brief_with(customer_data=Pick(values=[CUSTOMER_LATER])))["allowed"] == 5
    unknown = _untapped(None, None, None, owner="customers")
    assert answers(unknown, BRIEF)["allowed"] == 2  # ownership alone answers "allowed"


def test_verified_research_answers_replace_the_screens():
    c = _untapped(4, 4, 3)
    checked = _sal(5, 1, None, verified=True, obstacle="terms forbid sharing", path="needs a new contract")
    c.research = Research(data_held="d", licence="Unclear", licence_evidence=[], volume_estimate=None,
                          volume_score=None, volume_evidence=[], saleability=checked)
    s = c.current_saleability()
    assert (s.exists.score, s.allowed.score, s.willing.score) == (5, 1, 3)  # willing: research found nothing
    assert (s.exists.verified, s.willing.verified) == (True, False)
    assert (s.obstacle, s.path) == ("terms forbid sharing", "needs a new contract")
    assert saleability_label(c, BRIEF) == "Not sellable (initial)"  # one answer is still the screen's
    c.saleability = None
    assert saleability_label(c, BRIEF) == "Not sellable (verified)"


def test_ready_sources_show_their_licence():
    c = _cand(holder_type="ready", data_owner="company")
    assert saleability_label(c, BRIEF) == "prior: offered, licence not checked"
    assert saleability_label(c.model_copy(update={"collector": True}), BRIEF) == "prior: records to order"
    c.research = Research(data_held="d", licence=OPEN_CONDITIONS, licence_evidence=[], volume_estimate=None,
                          volume_score=None, volume_evidence=[])
    assert saleability_label(c, BRIEF) == f"checked: {OPEN_CONDITIONS}"


def test_csv_exports_the_grade_and_answers():
    rows = list(csv.DictReader(io.StringIO(pipeline.candidates_csv(
        [_untapped(5, 2, 4, path="contact the administrators"), _cand("r.com", holder_type="ready")], BRIEF))))
    assert {k: rows[0][k] for k in ("saleability", "exists", "allowed", "willing", "path")} == {
        "saleability": "Low (initial)", "exists": "5", "allowed": "2", "willing": "4",
        "path": "contact the administrators"}
    assert (rows[1]["saleability"], rows[1]["exists"]) == ("prior: offered, licence not checked", "")


# --- screen -----------------------------------------------------------------------------------

def _run(fake_ask, brief=BRIEF, **item):
    """Screen one company; the model's answer is an untapped holder, with `item` overrides."""
    answer = {"domain": "acme.com", "holder_type": "untapped", "data_owner": "company", "data_match": 5,
              "languages": ["English"], "regions": [], "volume_signal": None, "reason": "r"} | item
    c = _cand()
    c.reason = None
    fake_ask.responses[ScreenBatch] = lambda prompt: ScreenBatch(results=[ScreenItem(**answer)])
    screen(brief, [c], fetch=lambda _: "We record meetings.", progress=lambda _: None, source=lambda _: None)
    return c


def test_screen_types_and_initial_answers(fake_ask):
    c = _run(fake_ask, exists=5, allowed=3, willing=2, obstacle="a big brand", path="ask about a pilot")
    assert c.holder_type == "untapped" and not c.saleability.exists.verified
    assert (c.saleability.exists.score, c.saleability.obstacle) == (5, "a big brand")
    assert c.scores.licence == 2 and saleability_label(c, BRIEF) == "Low (initial)"
    prompt = fake_ask.calls[0].prompt
    assert "willing: would they sell?" in prompt and "deleted within 30 days" in prompt

    ready = _run(fake_ask, holder_type="ready", exists=5, allowed=5, willing=5)
    assert (ready.holder_type, ready.saleability, ready.scores.licence) == ("ready", None, 4)
    collector = _run(fake_ask, holder_type="ready", collector=True, data_owner="customers")
    assert (collector.holder_type, collector.collector) == ("ready", True)  # records new data: stays ready
    unrelated = _run(fake_ask, holder_type="unrelated", data_match=1)
    assert (unrelated.holder_type, unrelated.excluded) == (None, True)


def test_source_type_must_have_excludes_the_other_type(fake_ask):
    assert _run(fake_ask, brief_with(sources=Pick(values=[READY], importance="must"))).excluded
    assert not _run(fake_ask, brief_with(sources=Pick(values=[UNTAPPED], importance="must"))).excluded
    assert not _run(fake_ask, brief_with(sources=Pick(values=[READY]))).excluded  # nice to have


def test_collectors_fail_a_must_have_delivery_without_custom_collection():
    existing_only = brief_with(delivery=Pick(values=["One-off dataset"], importance="must"))
    collector = _cand(holder_type="ready", collector=True, scores=Scores(data_match=5))
    assert must_failures(collector, existing_only) == ["delivery"]
    assert must_failures(_cand(holder_type="ready", scores=Scores(data_match=5)), existing_only) == []
    assert must_failures(collector, brief_with(delivery=Pick(values=[CUSTOM_COLLECTION], importance="must"))) == []


def test_collectors_seed_snowball_only_when_custom_collection_is_wanted():
    custom = brief_with(delivery=Pick(values=[CUSTOM_COLLECTION]))
    assert (seed_types(BRIEF), seed_types(custom)) == (["untapped"], ["untapped", "collector"])
    cands = [_cand("c.com", holder_type="ready", collector=True, fit=4.5, scores=Scores(data_match=5, coverage=5)),
             _cand("u.com", holder_type="untapped", fit=4.0, scores=Scores(data_match=5, coverage=5))]
    assert [w for _, w in pick_seeds(cands, types=seed_types(BRIEF))] == ["https://u.com"]
    assert [w for _, w in pick_seeds(cands, types=seed_types(custom))] == ["https://c.com", "https://u.com"]


# --- research ---------------------------------------------------------------------------------

CRAWL = CrawlResult(website="https://acme.com", homepage=Page(
    url="https://acme.com", text="We keep every call recording for seven years."))


def test_research_asks_untapped_holders_to_search_the_news():
    p = build_prompt(BRIEF, _cand(holder_type="untapped"), CRAWL)
    assert "news and public records" in p and "insolvency, administration or liquidation notices" in p
    assert "regulator actions, fines or lawsuits" in p and "two or more independent" in p
    name = _cand(holder_type="untapped").name
    assert f'"{name} lawsuit"' in p and "cite at least one of them for willing" in p
    assert "Never write or guess a URL" in " ".join(p.split())  # made-up news URLs lose their quotes
    ready = build_prompt(BRIEF, _cand(holder_type="ready"), CRAWL)
    assert '"Commercial, non-exclusive"' in ready and "Leave saleability null" in ready
    assert "rights\n   assigned to the client" in build_prompt(BRIEF, _cand(holder_type="ready", collector=True), CRAWL)


def test_saleability_answers_need_a_quote_found_on_the_cited_page():
    news = {"https://news.example/acme": "<p>Acme Ltd has entered administration, the administrators said.</p>"}
    fetched = []

    def news_fetch(url):
        fetched.append(url)
        return news[url]

    r = Research(data_held="calls", licence="Unclear", licence_evidence=[], volume_estimate=None,
                 volume_score=None, volume_evidence=[], saleability=Saleability(
                     exists=Answer(score=5, reason="kept 7 years", evidence=[
                         Evidence(quote="We keep every call recording for seven years", url="https://acme.com")]),
                     allowed=Answer(score=4, reason="made up", evidence=[
                         Evidence(quote="Acme licenses its recordings to anyone at all", url="https://acme.com")]),
                     willing=Answer(score=5, reason="in administration", evidence=[
                         Evidence(quote="Acme Ltd has entered administration", url="https://news.example/acme")]),
                     path="contact the administrators"))
    s = verify_research(r, CRAWL, lambda url: "", news_fetch).saleability
    assert (s.exists.score, s.exists.verified) == (5, True)  # on the crawled page
    assert (s.allowed.score, s.allowed.verified, s.allowed.evidence) == (None, False, [])  # not on the page
    assert (s.willing.score, s.willing.verified) == (5, True)  # on the news page
    assert fetched == ["https://acme.com", "https://news.example/acme"] and s.path == "contact the administrators"


# --- email ------------------------------------------------------------------------------------

def test_email_ask_follows_type_licence_and_saleability():
    def ask(c):
        return email_prompt(BRIEF, c, None).split("Ask:")[1].split("\n\nRules")[0]

    assert "the price" in ask(_cand(holder_type="ready"))
    published = _cand(holder_type="ready", research=Research(
        data_held="d", licence=OPEN_CONDITIONS, licence_evidence=[], volume_estimate=None, volume_score=None,
        volume_evidence=[]))
    assert "licence terms" in ask(published)
    assert "who would own the result" in ask(_cand(holder_type="ready", collector=True))
    keen = _untapped(5, 4, 5, path="contact the administrators")
    assert "on what terms they would license" in ask(keen) and "contact the administrators" in ask(keen)
    shy = _untapped(5, 4, 2, owner="customers")
    assert "may not think of it as something to sell" in ask(shy) and "customers' consent" in ask(shy)


# --- old sessions -----------------------------------------------------------------------------

@pytest.mark.parametrize(("old", "holder", "collector", "excluded"), [
    ("vendor", "ready", False, False), ("seller", "ready", False, False), ("public", "ready", False, False),
    ("collector", "ready", True, False), ("by_product", "untapped", False, False),
    ("technology", "untapped", False, False), ("unrelated", None, False, True),
])
def test_old_holder_types_load(old, holder, collector, excluded):
    c = Candidate.model_validate({"name": "A", "website": "https://a.com", "domain": "a.com", "channels": [],
                                  "source_urls": [], "holder_type": old, "archetype": "service",
                                  "selling_status": "open_to_it"})  # dropped fields are ignored
    assert (c.holder_type, c.collector, c.excluded) == (holder, collector, excluded)


def test_old_source_labels_load():
    old = Brief.model_validate({"description": "d", "picks": {"sources": {"values": [
        "Data vendors and collection companies", "Marketplace listings", "Technology providers with internal data",
        "Distressed, closed or for-sale companies"]}}})
    assert old.pick("sources").values == [READY, UNTAPPED]


def test_archetypes_stay_an_internal_checklist():
    audio = archetypes.guide(["Audio/speech"])
    assert "- Kept by law: regulation makes it record" in audio and "fleet dashcams" not in audio
    assert "fleet dashcams" in archetypes.guide(["Video"])
    assert "speech-to-text APIs" in archetypes.supplier_tools(["Audio/speech"])
