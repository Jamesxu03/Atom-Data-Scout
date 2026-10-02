"""Sellers, collectors and untapped holders: screen rules, priors, filters, research and loading
old sessions."""

from tests.test_hypotheses import BRIEF, brief_with
from worker import archetypes
from worker.brief_template import (
    COLLECTORS,
    CUSTOM_COLLECTION,
    CUSTOMER_EXCLUDE,
    SELLERS,
    UNTAPPED,
    Brief,
    Pick,
)
from worker.config import MUST_HAVE_CAP
from worker.crawl import CrawlResult, Page
from worker.discovery import pick_seeds, seed_types
from worker.schemas import Candidate, Evidence, Fact, Research, Scores
from worker.scoring import licence_prior, must_failures, prior_label
from worker.steps.research import build_prompt, verify_research
from worker.steps.screen import ScreenBatch, ScreenItem, screen


def _cand(domain="acme.com", **kw):
    return Candidate(name="Acme", website=f"https://{domain}", domain=domain, channels=["product_search"],
                     source_urls=[], **kw)


def _run(fake_ask, brief=BRIEF, home="We record meetings.", **item):
    """Screen one company whose model answer is a seller owning its data, with `item` overrides."""
    answer = dict(domain="acme.com", holder_type="seller", data_owner="company", data_match=5,
                  languages=["English"], regions=[], volume_signal=None, reason="r") | item
    c = _cand()
    fake_ask.responses[ScreenBatch] = lambda prompt: ScreenBatch(results=[ScreenItem(**answer)])
    screen(brief, [c], fetch=lambda _: home, progress=lambda _: None, source=lambda _: None)
    return c


def test_seller_that_also_collects_keeps_one_row_with_a_flag(fake_ask):
    c = _run(fake_ask, collects_to_order=True, archetype="service", selling_status="open_to_it")
    assert (c.holder_type, c.collects_to_order) == ("seller", True)
    assert (c.archetype, c.selling_status) == (None, None)  # untapped holders only
    assert "\"seller\": already holds" in fake_ask.calls[0].prompt and '"kept_by_law" (Kept by law)' in fake_ask.calls[0].prompt


def test_seller_of_its_customers_data_is_an_untapped_holder(fake_ask):
    c = _run(fake_ask, data_owner="customers")
    assert (c.holder_type, c.archetype, c.selling_status) == ("untapped", "service", "unclear")


def test_collectors_always_collect_and_ignore_customer_data_rules(fake_ask):
    no_customer_data = brief_with(customer_data=Pick(values=[CUSTOMER_EXCLUDE], importance="must"))
    c = _run(fake_ask, no_customer_data, holder_type="collector", data_owner="customers")
    assert c.collects_to_order and not c.excluded  # it would record new data for us
    u = _run(fake_ask, no_customer_data, holder_type="untapped", data_owner="customers")
    assert u.excluded


def test_short_fact_quotes_are_kept_when_they_contain_the_numbers(fake_ask):
    c = _run(fake_ask, home="Founded in 2016. We record meetings.",
             founded={"value": "2016", "quote": "Founded in 2016."},
             price={"value": "£500", "quote": "We record meetings."})  # quote lacks the number
    assert c.founded and c.founded.value == "2016"
    assert c.price is None


def test_priors_by_type():
    assert prior_label(_cand(holder_type="collector")) == "commissioned data, rights negotiable"
    assert licence_prior(_cand(holder_type="collector", data_owner="customers"), BRIEF) == 4
    law = _cand(holder_type="untapped", data_owner="company", archetype="kept_by_law")
    assert (licence_prior(law, BRIEF), prior_label(law)) == (2, "kept to meet regulation, likely needs consent")
    keen = _cand(holder_type="untapped", data_owner="company", archetype="service", selling_status="open_to_it")
    assert (licence_prior(keen, BRIEF), prior_label(keen)) == (4, "may be licensable, open to selling")


def test_collectors_fail_a_must_have_delivery_without_custom_collection():
    existing_only = brief_with(delivery=Pick(values=["One-off dataset"], importance="must"))
    collector = _cand(holder_type="collector", scores=Scores(data_match=5))
    assert must_failures(collector, existing_only) == ["delivery"]
    assert must_failures(_cand(holder_type="seller", scores=Scores(data_match=5)), existing_only) == []
    wants_custom = brief_with(delivery=Pick(values=[CUSTOM_COLLECTION], importance="must"))
    assert must_failures(collector, wants_custom) == []


def test_collectors_seed_snowball_only_when_custom_collection_is_wanted():
    assert seed_types(BRIEF) == ["untapped"]
    custom = brief_with(delivery=Pick(values=[CUSTOM_COLLECTION]))
    assert seed_types(custom) == ["untapped", "collector"]
    cands = [_cand("c.com", holder_type="collector", reason="r", fit=4.5, scores=Scores(data_match=5, coverage=5)),
             _cand("u.com", holder_type="untapped", reason="r", fit=4.0, scores=Scores(data_match=5, coverage=5))]
    assert [w for _, w in pick_seeds(cands, types=seed_types(BRIEF))] == ["https://u.com"]
    assert [w for _, w in pick_seeds(cands, types=seed_types(custom))] == ["https://c.com", "https://u.com"]


def test_source_type_filter_maps_to_the_new_types():
    sellers = brief_with(sources=Pick(values=[SELLERS], importance="must"))
    assert sellers.accepts_holder("seller") and not sellers.accepts_holder("collector")
    both = brief_with(sources=Pick(values=[COLLECTORS, UNTAPPED], importance="must"))
    assert both.accepts_holder("collector") and both.accepts_holder("untapped") and not both.accepts_holder("seller")


def test_old_sessions_and_briefs_get_the_new_types():
    assert _cand(holder_type="vendor").holder_type == "seller"
    assert _cand(holder_type="by_product").holder_type == "untapped"
    old = Brief.model_validate({"description": "d", "picks": {"sources": {"values": [
        "Data vendors and collection companies", "By-product holders (their service generates the data)",
        "Distressed or insolvent companies"]}}})
    assert old.pick("sources").values == [SELLERS, COLLECTORS, UNTAPPED, "Distressed, closed or for-sale companies"]


def test_archetype_guide_follows_the_modality():
    audio = archetypes.guide(["Audio/speech"])
    assert "bank and broker trader calls" in audio and "fleet dashcams" not in audio
    assert "fleet dashcams" in archetypes.guide(["Video"])
    assert "shut-down startups" in audio and "shut-down startups" in archetypes.guide(["Text"])  # any modality
    assert len(archetypes.ARCHETYPES) == 10 and "speech-to-text APIs" in archetypes.supplier_tools(["Audio/speech"])


def test_research_asks_by_type_and_verifies_the_selling_status():
    crawl = CrawlResult(website="https://acme.com", homepage=Page(
        url="https://acme.com", text="We partner with AI companies to license our call archive."))
    law = _cand(holder_type="untapped", archetype="kept_by_law", reason="broker")
    p = build_prompt(BRIEF, law, crawl)
    assert "untapped holder (Kept by law)" in p and "regulatory record-keeping" in p
    assert "who owns data it collects for a client" in build_prompt(BRIEF, _cand(holder_type="collector"), crawl)

    r = Research(data_held="calls", licence="Unclear", licence_evidence=[], volume_estimate=None, volume_score=None,
                 volume_evidence=[], selling_status="open_to_it",
                 selling_evidence=[Evidence(quote="We partner with AI companies to license our call archive",
                                            url="https://acme.com")],
                 founded=Fact(value="2016", quote="Founded in 2016", url="https://acme.com"))
    kept = verify_research(r, crawl, lambda url: "<p>Founded in 2016</p>")
    assert kept.selling_status == "open_to_it" and kept.founded.value == "2016"
    made_up = r.model_copy(update={"selling_evidence": [Evidence(quote="We sell our recordings to anyone at all",
                                                                 url="https://acme.com")]})
    assert verify_research(made_up, crawl, lambda url: "").selling_status == "unclear"


def test_capped_collector_fit(fake_ask):
    existing_only = brief_with(delivery=Pick(values=["One-off dataset"], importance="must"))
    c = _run(fake_ask, existing_only, holder_type="collector")
    assert c.fit == MUST_HAVE_CAP
