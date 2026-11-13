import threading

from tests.test_hypotheses import brief_with
from worker.brief_template import COMMERCIAL, INTERNAL_ONLY, UNCLEAR, Pick
from worker.crawl import CrawlResult, Page
from worker.schemas import Candidate, Check, Evidence, Fact, Research
from worker.steps.research import research, verify_research
from worker.verify import quote_in_text, quote_on_page, value_in_quote

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


def test_quotes_on_different_lists_are_fetched_in_parallel():
    """A licence quote and a volume quote each wait for the other's fetch: checked one after
    the other, the first would time out and be dropped."""
    both_fetching = threading.Barrier(2, timeout=5)
    pages = {"https://a.example": "<p>Acme sells licences to its call recordings archive.</p>",
             "https://b.example": "<p>Acme has processed over 40 million customer calls.</p>"}

    def fetch(url):
        both_fetching.wait()
        return pages[url]

    lic = Evidence(quote="Acme sells licences to its call recordings archive", url="https://a.example")
    vol = Evidence(quote="Acme has processed over 40 million customer calls", url="https://b.example")
    r = verify_research(_research([lic], [vol], licence=COMMERCIAL), _crawl(), fetch)
    assert (r.licence_evidence, r.volume_evidence) == ([lic], [vol])


def test_quote_matching():
    assert quote_in_text("“We record support calls”  for quality", "we record support calls for quality.")
    assert not quote_in_text("We sell data", "We sell data to anyone")  # too short to prove anything
    assert quote_in_text("support calls for quality ... never shared with or sold", POLICY)
    assert quote_on_page("never shared with or sold", "<p>never&nbsp;shared <i>with</i> or sold</p>")
    assert not quote_on_page("used only internally and never", "<script>used only internally and never</script>")


def test_checks_need_a_verified_quote():
    shown = Check(category="deal_terms", verdict="fails", finding="identifiable recordings kept",
                  evidence=[Evidence(quote="We record support calls for quality", url="https://acme.com/privacy")])
    unshown = Check(category="permitted_use", verdict="meets", finding="licensed for training",
                    evidence=[Evidence(quote="We keep consent records for every speaker", url="https://acme.com")])
    r = verify_research(_research([], checks=[shown, unshown]), _crawl(), _no_fetch)
    assert [(ch.category, ch.verdict) for ch in r.checks] == [("deal_terms", "fails"), ("permitted_use", "unclear")]


def test_research_prompt_and_search(fake_ask):
    fake_ask.responses[Research] = _research(
        [Evidence(quote="Recordings are used only internally", url="https://acme.com/privacy")])
    c = Candidate(name="Acme", website="https://acme.com", domain="acme.com", channels=["product_search"],
                  source_urls=[], reason="contact centre")
    brief = brief_with(region=Pick(values=["Middle East (GCC)"]),
                       deal_terms=Pick(values=["Data processing agreement; processed in the UK"], importance="must"))
    r = research(brief, c, _crawl(), _no_fetch)
    assert r.licence == INTERNAL_ONLY
    (call,) = fake_ask.calls
    assert call.use_search
    assert "### privacy (https://acme.com/privacy)" in call.prompt and POLICY in call.prompt
    assert f'"{INTERNAL_ONLY}"' in call.prompt and "word for word" in call.prompt
    assert "- deal_terms: Deal terms: Data processing agreement; processed in the UK" in call.prompt
    assert "depending on where the buyer processes the data" in call.prompt and "PDPL" in call.prompt
    assert "Volume: 1,000 hours\n" in call.prompt
    assert "- speakers" not in call.prompt  # not a research check


def test_price_and_age_need_a_verified_quote_containing_their_numbers():
    crawl = _crawl()
    crawl.homepage = Page(url="https://acme.com", text="Acme was founded in 2009. Licences for our call "
                                                       "archive start at £2,500 per 1,000 hours.")
    r = _research([]).model_copy(update={
        "price": Fact(value="£2,500 per 1,000 hours", url="https://acme.com",
                      quote="Licences for our call archive start at £2,500 per 1,000 hours"),
        "founded": Fact(value="2011", quote="Acme was founded in 2009", url="https://acme.com"),
        "data_period": Fact(value="2015 to 2023", quote="Calls were recorded from 2015 to 2023",
                            url="https://gone.example"),
    })
    r = verify_research(r, crawl, _no_fetch)
    assert r.price.value == "£2,500 per 1,000 hours"
    assert r.founded is None  # the quote says 2009
    assert r.data_period is None  # page unreachable, so unverified


def test_value_in_quote():
    assert value_in_quote("$1,250.50 per hour", "Priced at $1250.50 an hour")
    assert value_in_quote("On request", "Pricing is available on request")
    assert not value_in_quote("2012", "Founded in 2014")


def test_candidate_prefers_research_facts():
    screen_price = Fact(value="$30 per hour", quote="Meeting audio costs $30 per hour", url="https://acme.com")
    c = Candidate(name="Acme", website="https://acme.com", domain="acme.com", channels=[], source_urls=[],
                  price=screen_price)
    assert c.fact("price") == screen_price and c.fact("founded") is None
    researched = Fact(value="$25 per hour", quote="Licences cost $25 per hour", url="https://acme.com/data")
    c.research = _research([]).model_copy(update={"price": researched})
    assert c.fact("price") == researched
