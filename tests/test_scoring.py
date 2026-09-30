import pytest

from tests.test_hypotheses import BRIEF, brief_with
from worker.brief_template import (
    COMMERCIAL,
    CUSTOMER_CONSENT,
    CUSTOMER_LATER,
    EXCLUSIVE,
    INTERNAL_ONLY,
    OPEN,
    OPEN_CONDITIONS,
    RESTRICTED,
    UNCLEAR,
    Pick,
)
from worker.config import FIT_WEIGHTS, INTERNAL_ONLY_CAP, MUST_HAVE_CAP, TECHNOLOGY_CAP
from worker.schemas import Candidate, Check, Evidence, Research, Scores
from worker.scoring import coverage, fit, licence_from_found, licence_prior, must_failures, rescore

QUOTE = [Evidence(quote="q", url="u")]


def _cand(**kw):
    return Candidate(name="Acme", website="https://acme.com", domain="acme.com", channels=["product_search"],
                     source_urls=[], reason="r", **kw)


def _research(licence=UNCLEAR, volume_score=None, checks=()):
    return Research(data_held="calls", licence=licence, licence_evidence=[], volume_estimate=None,
                    volume_score=volume_score, volume_evidence=[], checks=list(checks))


def test_weights_sum_to_one():
    assert sum(FIT_WEIGHTS.values()) == pytest.approx(1.0)


def test_unknown_scores_neither_help_nor_hurt():
    assert fit(_cand(scores=Scores(data_match=4)), BRIEF) == 4.0
    assert fit(_cand(scores=Scores(data_match=4, coverage=2)), BRIEF) == round((0.4 * 4 + 0.25 * 2) / 0.65, 2)
    all_known = Scores(data_match=5, coverage=3, licence=4, volume=1)
    assert fit(_cand(scores=all_known), BRIEF) == round(0.4 * 5 + 0.25 * 3 + 0.2 * 4 + 0.15 * 1, 2)
    assert fit(_cand(scores=Scores(coverage=5, licence=5)), BRIEF) is None  # no data match: not screened


TOP = Scores(data_match=5, coverage=5, licence=5, volume=5)


def test_caps():
    assert fit(_cand(scores=TOP, holder_type="technology"), BRIEF) == TECHNOLOGY_CAP
    assert fit(_cand(scores=TOP, research=_research(INTERNAL_ONLY)), BRIEF) == INTERNAL_ONLY_CAP
    must_lang = brief_with(languages=Pick(values=["English"], importance="must"))
    assert fit(_cand(scores=TOP), must_lang) == MUST_HAVE_CAP  # no language evidence
    assert fit(_cand(scores=TOP, languages=["English"]), must_lang) == 5.0
    assert fit(_cand(scores=TOP), BRIEF) == 5.0  # languages only nice to have: coverage, no cap


def test_must_have_contradictions():
    assert must_failures(_cand(scores=TOP, contradicts=["modality"]), BRIEF) == ["modality"]
    assert must_failures(_cand(scores=TOP, contradicts=["speakers"]), BRIEF) == []  # nice to have
    must_licence = brief_with(licence=Pick(values=[COMMERCIAL, OPEN], importance="must"))
    assert must_failures(_cand(scores=TOP, research=_research(RESTRICTED)), must_licence) == ["licence"]
    assert must_failures(_cand(scores=TOP, research=_research(EXCLUSIVE)), must_licence) == []
    # unclear is only a contradiction when the buyer leaves "Unclear" out of a must-have D1
    assert must_failures(_cand(scores=TOP, research=_research(UNCLEAR)), must_licence) == ["licence"]
    keep_unclear = brief_with(licence=Pick(values=[COMMERCIAL, UNCLEAR], importance="must"))
    assert must_failures(_cand(scores=TOP, research=_research(UNCLEAR)), keep_unclear) == []
    assert must_failures(_cand(scores=TOP, research=_research(UNCLEAR)), BRIEF) == []  # D1 nice to have
    docs = brief_with(documents=Pick(values=["Per-person consent records"], importance="must"))
    failed = Check(category="documents", verdict="fails", finding="no consent records", evidence=QUOTE)
    assert must_failures(_cand(scores=TOP, research=_research(checks=[failed])), docs) == ["documents"]
    unclear = failed.model_copy(update={"verdict": "unclear"})
    assert must_failures(_cand(scores=TOP, research=_research(checks=[unclear])), docs) == []
    must_volume = brief_with(volume=Pick(amount=1000, unit="hours", importance="must"))
    assert must_failures(_cand(scores=Scores(data_match=5, volume=1)), must_volume) == ["volume"]


def test_coverage():
    both = brief_with(languages=Pick(values=["Arabic"]), region=Pick(values=["Middle East (GCC)"]))
    assert coverage(both, ["Arabic"], ["Middle East (GCC)"]) == 5
    assert coverage(both, ["Arabic"], []) == 3
    assert coverage(both, [], []) == 1
    assert coverage(BRIEF, ["English"], []) == 5  # only languages set: they're all that count
    assert coverage(brief_with(region=Pick(values=["Global"])), ["English"], []) == 5  # Global: no preference
    assert coverage(brief_with(languages=None), [], []) is None


@pytest.mark.parametrize(("holder", "owner", "score"), [
    ("vendor", "company", 4), ("public", "unclear", 3), ("by_product", "company", 3),
    ("by_product", "customers", 2), ("technology", "company", 2), ("by_product", "unclear", None),
    ("unrelated", "company", None), (None, None, None),
])
def test_licence_prior(holder, owner, score):
    assert licence_prior(_cand(holder_type=holder, data_owner=owner), BRIEF) == score


def test_customer_data_choice_sets_the_prior():
    held_for_customers = _cand(holder_type="by_product", data_owner="customers")
    assert licence_prior(held_for_customers, brief_with(customer_data=Pick(values=[CUSTOMER_CONSENT]))) == 2
    assert licence_prior(held_for_customers, brief_with(customer_data=Pick(values=[CUSTOMER_LATER]))) is None


@pytest.mark.parametrize(("found", "score"), [
    (OPEN, 5), (OPEN_CONDITIONS, 5), (COMMERCIAL, 5), (EXCLUSIVE, 5),  # exclusive can also be non-exclusive
    (RESTRICTED, 1), (INTERNAL_ONLY, 1), (UNCLEAR, None),
])
def test_licence_found_against_what_the_buyer_accepts(found, score):
    assert licence_from_found(found, BRIEF) == score


def test_research_replaces_priors_unless_it_found_nothing():
    c = _cand(holder_type="by_product", data_owner="customers", languages=["English"],
              scores=Scores(data_match=5, volume=3))
    rescore(c, BRIEF)
    assert (c.scores.licence, c.scores.volume) == (2, 3)
    c.research = _research(COMMERCIAL, volume_score=5)
    rescore(c, BRIEF)
    assert (c.scores.licence, c.scores.volume, c.fit) == (5, 5, 5.0)
    c.research = _research(UNCLEAR)
    rescore(c, BRIEF)
    assert c.scores.licence == 2  # back to the prior, not dropped
    rescore(c, brief_with(licence=Pick(values=[COMMERCIAL], importance="any")))
    assert c.scores.licence is None  # licence set to Any: ignored
