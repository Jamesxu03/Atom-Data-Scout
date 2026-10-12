"""Confirmation (WS4): fit and saleability confirmed with quotes before the default view."""

import pytest

from tests.test_hypotheses import BRIEF, brief_with
from worker.brief_template import (
    COMMERCIAL,
    CUSTOMER_CONSENT,
    CUSTOMER_EXCLUDE,
    INTERNAL_ONLY,
    RESTRICTED,
    UNCLEAR,
    Pick,
)
from worker.config import CONFIRM_TOP
from worker.crawl import CrawlResult, Page
from worker.schemas import (
    Blocker,
    Candidate,
    ConfirmFound,
    Evidence,
    MustCheck,
    Scores,
    SellingSignal,
    Trigger,
)
from worker.steps import confirm

EV = [Evidence(quote="a quote that was verified", url="https://acme.com")]
MUSTS = brief_with(languages=Pick(values=["English"], importance="must"))


def _cand(holder="untapped", **kw) -> Candidate:
    return Candidate(name="Acme", website="https://acme.com", domain="acme.com", channels=["product_search"],
                     source_urls=[], reason="records meetings", holder_type=holder, data_owner="company",
                     scores=Scores(data_match=5), fit=4.0, **kw)


def _found(checks=("modality", "languages"), fails=(), owner="company", signals=1, blockers=(), licence=None,
           data=True) -> ConfirmFound:
    """Findings as verify() leaves them: anything listed has a verified quote."""
    return ConfirmFound(
        data_held="meeting audio", data_evidence=EV if data else [],
        checks=[MustCheck(category=k, verdict="fails" if k in fails else "meets", finding="f", evidence=EV)
                for k in (*checks, *fails)],
        data_owner=owner, owner_evidence=EV,
        licence=licence, licence_evidence=EV if licence else [],
        signals=[SellingSignal(kind="reuse_terms", summary="terms allow training models", evidence=EV)] * signals,
        blockers=[Blocker(kind=k, summary=f"blocker: {k}", evidence=EV) for k in blockers])


@pytest.mark.parametrize(("found", "brief", "expected"), [
    (_found(), MUSTS, ("confirmed", "Fits; terms allow training models")),
    (_found(signals=0), MUSTS, ("possible", "Fits; no sign yet that it would sell")),
    (_found(checks=("modality",)), MUSTS, ("unconfirmed", "Its pages don't show: Languages and dialects")),
    (_found(checks=("languages",), data=False), MUSTS, ("unconfirmed", "Its pages don't show it holds this kind of data")),
    # the volume target is a total several sellers make up, and a holder's terms only cover its clients
    (_found(fails=("volume", "permitted_use")), MUSTS, ("confirmed", "Fits; terms allow training models")),
    (_found(fails=("speakers",)), MUSTS, ("rejected", "Contradicts a must-have: Speakers per recording")),
    (_found(blockers=("never_share",)), MUSTS, ("rejected", "blocker: never_share")),
    (_found(owner="customers"), brief_with(customer_data=Pick(values=[CUSTOMER_EXCLUDE])),
     ("rejected", "Its customers own the data, which the brief excludes")),
    # customers' data the brief accepts with consent: a sign it would sell still confirms it
    (_found(owner="customers"), brief_with(customer_data=Pick(values=[CUSTOMER_CONSENT])),
     ("confirmed", "Fits; terms allow training models")),
    (_found(owner="unclear"), BRIEF, ("possible", "Fits; who owns the data is unclear")),
])
def test_untapped_verdicts(found, brief, expected):
    assert confirm.decide(brief, _cand(), found) == expected


def test_regulator_only_is_an_obstacle_once_a_trigger_is_verified():
    found = _found(blockers=("regulator_only",))
    assert confirm.decide(BRIEF, _cand(), found)[0] == "rejected"
    distressed = _cand(needs_trigger=True, triggers=[Trigger(kind="distress", date="2025", summary="in administration",
                                                             evidence=EV)])
    assert confirm.decide(BRIEF, distressed, found) == ("confirmed", "Fits; terms allow training models")
    law_only = _cand(needs_trigger=True, triggers=[Trigger(kind="law", date="2025", summary="Data Act", evidence=EV)])
    assert confirm.decide(BRIEF, law_only, _found(signals=0)) == ("possible", "Fits; Data Act")  # never confirmed


@pytest.mark.parametrize(("licence", "brief", "expected"), [
    (COMMERCIAL, brief_with(licence=Pick(values=[COMMERCIAL])), "confirmed"),
    (RESTRICTED, brief_with(licence=Pick(values=[COMMERCIAL])), "rejected"),
    (INTERNAL_ONLY, BRIEF, "rejected"),
    (UNCLEAR, brief_with(licence=Pick(values=[COMMERCIAL])), "possible"),
    (RESTRICTED, brief_with(licence=None), "confirmed"),  # D1 empty: any stated licence
])
def test_ready_source_verdicts_follow_the_licence(licence, brief, expected):
    found = _found(signals=0, licence=licence)
    assert confirm.decide(brief, _cand("ready"), found)[0] == expected


def test_select_takes_the_best_of_each_list():
    def cand(i, holder, fit, **kw):
        return Candidate(name=f"c{i}", website=f"https://c{i}.com", domain=f"c{i}.com", channels=[], source_urls=[],
                         reason="r", holder_type=holder, scores=Scores(data_match=4), fit=fit, **kw)

    cands = [cand(i, "untapped", 3.0 + i / 100) for i in range(30)] + [cand(100 + i, "ready", 4.0) for i in range(15)]
    cands += [cand(200, "untapped", 4.9, vendor=True), cand(201, "untapped", 4.9, needs_trigger=True),
              cand(202, "untapped", 2.0), cand(203, "untapped", 4.9, killed=True)]
    picked = confirm.select(cands)
    untapped = [c for c in picked if c.holder_type == "untapped"]
    assert len(untapped) == CONFIRM_TOP["untapped"] and untapped[0].domain == "c29.com"  # best fit first
    assert sum(c.holder_type == "ready" for c in picked) == CONFIRM_TOP["ready"]
    assert not {"c200.com", "c201.com", "c202.com", "c203.com"} & {c.domain for c in picked}


def test_confirm_keeps_only_verified_quotes(fake_ask):
    page = "Acme records English team meetings. Our own staff record every meeting we keep."
    crawl = CrawlResult(website="https://acme.com", homepage=Page(url="https://acme.com", text=page))
    good = [Evidence(quote="Acme records English team meetings", url="https://acme.com")]
    made_up = [Evidence(quote="Acme licenses all of its data to AI labs", url="https://acme.com")]
    fake_ask.responses[ConfirmFound] = ConfirmFound(
        data_held="meetings", data_evidence=good,
        checks=[MustCheck(category="modality", verdict="meets", finding="audio", evidence=good),
                MustCheck(category="languages", verdict="meets", finding="English", evidence=made_up)],
        data_owner="company", owner_evidence=[Evidence(quote="Our own staff record every meeting we keep",
                                                       url="https://acme.com")],
        licence=COMMERCIAL, licence_evidence=good,  # untapped: ignored
        signals=[SellingSignal(kind="licensing", summary="licenses to AI labs", evidence=made_up)],
        blockers=[Blocker(kind="never_share", summary="never shares", evidence=made_up)])
    result = confirm.confirm(MUSTS, _cand(), crawl, fetch=lambda url: "<p>nothing here</p>")
    # languages lost its quote, so it is unclear; the made-up signal and blocker are gone
    assert (result.verdict, result.reason) == ("unconfirmed", "Its pages don't show: Languages and dialects")
    assert {ch.category: ch.verdict for ch in result.checks} == {"modality": "meets", "languages": "unclear"}
    assert (result.signals, result.blockers, result.licence, result.data_owner) == ([], [], None, "company")
    (call,) = fake_ask.calls
    assert call.use_search and "- languages: Languages and dialects: English" in call.prompt
    assert '"we do not sell your personal information" is not a blocker' in call.prompt
    assert "- exclusions" not in call.prompt and "Pages that don't mention it are \"unclear\"" in call.prompt


def test_the_screens_evidence_counts_for_languages_and_a_clear_data_match():
    found = _found(checks=("modality",))  # no quote saying "English", nor one for the domain
    must_domain = brief_with(languages=Pick(values=["English"], importance="must"),
                             domain=Pick(values=["Meetings/conversation"], importance="must"))
    english = _cand(languages=["English"])
    assert confirm.decide(must_domain, english, found)[0] == "confirmed"  # data match 5: clearly this data
    assert confirm.decide(must_domain, _cand(), found) == ("unconfirmed", "Its pages don't show: Languages and dialects")
    weak = _cand(languages=["English"])
    weak.scores = Scores(data_match=3)
    assert confirm.decide(must_domain, weak, found) == ("unconfirmed", "Its pages don't show: Domain")
    # a ready source's licence terms still reject on permitted use
    assert confirm.decide(MUSTS, _cand("ready"), _found(fails=("permitted_use",), licence=COMMERCIAL))[0] == "rejected"


def test_verdicts_follow_todays_rules_on_load():
    c = _cand(languages=["English"])
    c.confirmation = confirm.Confirmation(verdict="rejected", reason="Contradicts a must-have: Volume target",
                                          data_evidence=EV, checks=_found(fails=("volume",)).checks, data_owner="company",
                                          owner_evidence=EV, signals=_found().signals)
    confirm.redecide(MUSTS, c)
    assert (c.confirmation.verdict, c.confirmation.reason) == ("confirmed", "Fits; terms allow training models")
