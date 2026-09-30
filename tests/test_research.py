from tests.test_hypotheses import brief_with
from worker.brief_template import COMMERCIAL, INTERNAL_ONLY, UNCLEAR, Pick
from worker.crawl import CrawlResult, Page
from worker.schemas import Candidate, Check, Evidence, Research
from worker.steps.research import research, verify_research
from worker.verify import quote_in_text, quote_on_page

POLICY = ("We record support calls for quality and training purposes. Recordings are used only "
          "internally and are never shared with or sold to third parties.")


def _crawl():
    return CrawlResult(website="https://acme.com",
                       homepage=Page(url="https://acme.com", text="Acme runs contact centres for 300 brands."),
                       privacy=Page(url="https://acme.com/privacy", text=POLICY))


def _research(licence_evidence, volume_evidence=(), licence=INTERNAL_ONLY, checks=()):
    return Research(data_held="support call recordings", licence=licence,
                    licence_evidence=list(licence_evidence), volume_estimate="millions of calls",
                    volume_score=4, volume_evidence=list(volume_evidence), checks=list(checks))


def _no_fetch(url):
    raise ConnectionError(url)


def test_verified_quote_from_crawled_page_is_kept():
    ev = Evidence(quote="Recordings are used only internally and are never shared", url="https://acme.com/privacy/")
    r = verify_research(_research([ev]), _crawl(), _no_fetch)
    assert r.licence == INTERNAL_ONLY and len(r.licence_evidence) == 1


def test_unverified_quote_is_dropped_and_status_becomes_unknown():
    made_up = Evidence(quote="We license our call recordings to AI companies", url="https://acme.com/privacy")
    r = verify_research(_research([made_up], licence=COMMERCIAL), _crawl(), _no_fetch)
    assert (r.licence, r.licence_evidence) == (UNCLEAR, [])
    # no verified volume quote either: the estimate goes too
    assert (r.volume_estimate, r.volume_score) == (None, None)


def test_quote_from_search_is_checked_on_the_fetched_page():
    pages = {"https://news.example/acme": "<p>Acme has processed <b>over 40 million</b> customer calls.</p>"}
    ok = Evidence(quote="Acme has processed over 40 million customer calls", url="https://news.example/acme")
    wrong = Evidence(quote="Acme has processed over 90 million customer calls", url="https://news.example/acme")
    dead = Evidence(quote="Acme holds 10,000 hours of meetings", url="https://gone.example/")
    r = verify_research(_research([], [ok, wrong, dead], licence=UNCLEAR), _crawl(), pages.__getitem__)
    assert r.volume_evidence == [ok] and r.volume_score == 4


def test_quote_matching():
    assert quote_in_text("“We record support calls”  for quality", "we record support calls for quality.")
    assert not quote_in_text("We sell data", "We sell data to anyone")  # too short to prove anything
    assert quote_in_text("support calls for quality ... never shared with or sold", POLICY)
    assert quote_on_page("never shared with or sold", "<p>never&nbsp;shared <i>with</i> or sold</p>")
    assert not quote_on_page("used only internally and never", "<script>used only internally and never</script>")


def test_checks_need_a_verified_quote():
    shown = Check(category="personal_data", verdict="fails", finding="identifiable recordings kept",
                  evidence=[Evidence(quote="We record support calls for quality", url="https://acme.com/privacy")])
    unshown = Check(category="documents", verdict="meets", finding="consent records available",
                    evidence=[Evidence(quote="We keep consent records for every speaker", url="https://acme.com")])
    r = verify_research(_research([], checks=[shown, unshown]), _crawl(), _no_fetch)
    assert [(ch.category, ch.verdict) for ch in r.checks] == [("personal_data", "fails"), ("documents", "unclear")]


def test_research_prompt_and_search(fake_ask):
    fake_ask.responses[Research] = _research(
        [Evidence(quote="Recordings are used only internally", url="https://acme.com/privacy")])
    c = Candidate(name="Acme", website="https://acme.com", domain="acme.com", channels=["product_search"],
                  source_urls=[], reason="contact centre")
    brief = brief_with(region=Pick(values=["Middle East (GCC)"]), processing=Pick(values=["UK"]),
                       documents=Pick(values=["Data processing agreement"], importance="must"),
                       seller_share=Pick(values=["10%"]))
    r = research(brief, c, _crawl(), _no_fetch)
    assert r.licence == INTERNAL_ONLY
    (call,) = fake_ask.calls
    assert call.use_search
    assert "### privacy (https://acme.com/privacy)" in call.prompt and POLICY in call.prompt
    assert f'"{INTERNAL_ONLY}"' in call.prompt and "word for word" in call.prompt
    assert "- documents: Documents required: Data processing agreement" in call.prompt
    assert "- processing: Where the buyer will process it: UK" in call.prompt and "PDPL" in call.prompt
    assert "one seller should hold 10% of it" in call.prompt
    assert "- speakers" not in call.prompt  # not a research check
