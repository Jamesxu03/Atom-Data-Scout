from tests.test_hypotheses import BRIEF
from worker.crawl import CrawlResult, Page
from worker.schemas import Candidate, Evidence, Research
from worker.steps.research import research, verify_research
from worker.verify import quote_in_text, quote_on_page

POLICY = ("We record support calls for quality and training purposes. Recordings are used only "
          "internally and are never shared with or sold to third parties.")


def _crawl():
    return CrawlResult(website="https://acme.com",
                       homepage=Page(url="https://acme.com", text="Acme runs contact centres for 300 brands."),
                       privacy=Page(url="https://acme.com/privacy", text=POLICY))


def _research(licence_evidence, volume_evidence=(), status="internal_only"):
    return Research(data_held="support call recordings", licence_status=status,
                    licence_evidence=list(licence_evidence), volume_estimate="millions of calls",
                    volume_score=4, volume_evidence=list(volume_evidence))


def _no_fetch(url):
    raise ConnectionError(url)


def test_verified_quote_from_crawled_page_is_kept():
    ev = Evidence(quote="Recordings are used only internally and are never shared", url="https://acme.com/privacy/")
    r = verify_research(_research([ev]), _crawl(), _no_fetch)
    assert r.licence_status == "internal_only" and len(r.licence_evidence) == 1


def test_unverified_quote_is_dropped_and_status_becomes_unknown():
    made_up = Evidence(quote="We license our call recordings to AI companies", url="https://acme.com/privacy")
    r = verify_research(_research([made_up], status="sellable"), _crawl(), _no_fetch)
    assert (r.licence_status, r.licence_evidence) == ("unknown", [])
    # no verified volume quote either: the estimate goes too
    assert (r.volume_estimate, r.volume_score) == (None, None)


def test_quote_from_search_is_checked_on_the_fetched_page():
    pages = {"https://news.example/acme": "<p>Acme has processed <b>over 40 million</b> customer calls.</p>"}
    ok = Evidence(quote="Acme has processed over 40 million customer calls", url="https://news.example/acme")
    wrong = Evidence(quote="Acme has processed over 90 million customer calls", url="https://news.example/acme")
    dead = Evidence(quote="Acme holds 10,000 hours of meetings", url="https://gone.example/")
    r = verify_research(_research([], [ok, wrong, dead], status="unknown"), _crawl(), pages.__getitem__)
    assert r.volume_evidence == [ok] and r.volume_score == 4


def test_quote_matching():
    assert quote_in_text("“We record support calls”  for quality", "we record support calls for quality.")
    assert not quote_in_text("We sell data", "We sell data to anyone")  # too short to prove anything
    assert quote_in_text("support calls for quality ... never shared with or sold", POLICY)
    assert quote_on_page("never shared with or sold", "<p>never&nbsp;shared <i>with</i> or sold</p>")
    assert not quote_on_page("used only internally and never", "<script>used only internally and never</script>")


def test_research_prompt_and_search(fake_ask):
    fake_ask.responses[Research] = _research(
        [Evidence(quote="Recordings are used only internally", url="https://acme.com/privacy")])
    c = Candidate(name="Acme", website="https://acme.com", domain="acme.com", channels=["product_search"],
                  source_urls=[], reason="contact centre")
    r = research(BRIEF, c, _crawl(), _no_fetch)
    assert r.licence_status == "internal_only"
    (call,) = fake_ask.calls
    assert call.use_search
    assert "### privacy (https://acme.com/privacy)" in call.prompt and POLICY in call.prompt
    assert '"internal_only"' in call.prompt and "word for word" in call.prompt
