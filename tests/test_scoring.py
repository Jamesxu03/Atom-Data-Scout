import pytest

from tests.test_hypotheses import BRIEF
from worker.config import FIT_WEIGHTS, INTERNAL_ONLY_CAP, MUST_HAVE_CAP, TECHNOLOGY_CAP
from worker.schemas import Candidate, Research, Scores
from worker.scoring import coverage, fit, licence_from_status, licence_prior, rescore


def _cand(**kw):
    return Candidate(name="Acme", website="https://acme.com", domain="acme.com", channels=["product_search"],
                     source_urls=[], reason="r", **kw)


def _research(status="unknown", volume_score=None):
    return Research(data_held="calls", licence_status=status, licence_evidence=[], volume_estimate=None,
                    volume_score=volume_score, volume_evidence=[])


def test_weights_sum_to_one():
    assert sum(FIT_WEIGHTS.values()) == pytest.approx(1.0)


def test_unknown_scores_neither_help_nor_hurt():
    assert fit(_cand(scores=Scores(data_match=4)), BRIEF) == 4.0
    assert fit(_cand(scores=Scores(data_match=4, coverage=2)), BRIEF) == round((0.4 * 4 + 0.25 * 2) / 0.65, 2)
    all_known = Scores(data_match=5, coverage=3, licence=4, volume=1)
    assert fit(_cand(scores=all_known), BRIEF) == round(0.4 * 5 + 0.25 * 3 + 0.2 * 4 + 0.15 * 1, 2)
    assert fit(_cand(scores=Scores(coverage=5, licence=5)), BRIEF) is None  # no data match: not screened


def test_caps():
    top = Scores(data_match=5, coverage=5, licence=5, volume=5)
    assert fit(_cand(scores=top, holder_type="technology"), BRIEF) == TECHNOLOGY_CAP
    assert fit(_cand(scores=top, research=_research("internal_only")), BRIEF) == INTERNAL_ONLY_CAP
    must = BRIEF.model_copy(update={"must_have": ["languages"]})
    assert fit(_cand(scores=top), must) == MUST_HAVE_CAP  # no language evidence
    assert fit(_cand(scores=top, languages=["English"]), must) == 5.0
    # a must-have region only caps when the brief names regions
    assert fit(_cand(scores=top), BRIEF.model_copy(update={"must_have": ["regions"]})) == 5.0


def test_coverage():
    both = BRIEF.model_copy(update={"languages": ["Arabic"], "regions": ["Gulf"]})
    assert coverage(both, ["Arabic"], ["Gulf"]) == 5
    assert coverage(both, ["Arabic"], []) == 3
    assert coverage(both, [], []) == 1
    assert coverage(BRIEF, ["English"], []) == 5  # only languages named: they're all that count
    assert coverage(BRIEF.model_copy(update={"languages": []}), [], []) is None


@pytest.mark.parametrize(("holder", "owner", "score"), [
    ("vendor", "company", 4), ("public", "unclear", 3), ("by_product", "company", 3),
    ("by_product", "customers", 2), ("technology", "company", 2), ("by_product", "unclear", None),
    ("unrelated", "company", None), (None, None, None),
])
def test_licence_prior(holder, owner, score):
    assert licence_prior(holder, owner) == score


@pytest.mark.parametrize(("status", "training_only", "commercial"), [
    ("sellable", 5, 5), ("open", 5, 5), ("open_restricted", 4, 2), ("needs_consent", 2, 2),
    ("internal_only", 1, 1), ("unknown", None, None),
])
def test_licence_from_status(status, training_only, commercial):
    assert licence_from_status(status, BRIEF) == training_only
    assert licence_from_status(status, BRIEF.model_copy(update={"licence": "commercial"})) == commercial


def test_research_replaces_priors_unless_it_found_nothing():
    c = _cand(holder_type="by_product", data_owner="customers", languages=["English"],
              scores=Scores(data_match=5, volume=3))
    rescore(c, BRIEF)
    assert (c.scores.licence, c.scores.volume) == (2, 3)
    c.research = _research("sellable", volume_score=5)
    rescore(c, BRIEF)
    assert (c.scores.licence, c.scores.volume, c.fit) == (5, 5, 5.0)
    c.research = _research("unknown")
    rescore(c, BRIEF)
    assert c.scores.licence == 2  # back to the prior, not dropped
