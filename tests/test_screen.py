import httpx
import pytest

from tests.test_hypotheses import BRIEF, brief_with
from worker.brief_template import CUSTOMER_EXCLUDE, READY, Pick
from worker.config import MUST_HAVE_CAP, NOT_SELLABLE_CAP, SCREEN_BATCH_SIZE
from worker.schemas import Candidate
from worker.scoring import is_relevant, rank
from worker.steps.screen import NO_HOMEPAGE, ScreenBatch, ScreenItem, Stated, screen, source_excerpt


def _cand(domain):
    return Candidate(name=domain, website=f"https://{domain}", domain=domain,
                     channels=["product_search"], source_urls=[])


def _score_by_prefix(prompt):
    """good-* -> data_match 5, mid-* -> 3, else 1 and unrelated; skips 'silent-*' to test missing
    answers. Evidence for English unless the domain has 'nolang'. Untapped holders answer
    exists 4, allowed 4, willing 3 (grade 3), except 'tech' ones: a tool maker that doesn't keep
    the data (exists 1)."""
    items = []
    for line in prompt.splitlines():
        if line.startswith("### "):
            domain = line[4:].split(" ")[0]
            if domain.startswith("silent"):
                continue
            match = {"good": 5, "mid": 3}.get(domain.split("-")[0], 1)
            holder = "unrelated" if match == 1 else "untapped"
            items.append(ScreenItem(domain=domain.upper(), holder_type=holder, data_owner="company",
                                    data_match=match, languages=[] if "nolang" in domain else ["english"],
                                    regions=[], volume_signal=None, reason=f"because {domain}",
                                    exists=1 if "tech" in domain else 4, allowed=4, willing=3))
    return ScreenBatch(results=items)


def test_screen_scores_and_excludes(fake_ask):
    fake_ask.responses[ScreenBatch] = _score_by_prefix
    cands = [_cand(d) for d in ["good-a.com", "mid-b.com", "no-c.com", "down.com", "silent-d.com"]]

    def fetch(url):
        if "down.com" in url:
            raise ConnectionError("refused")
        return "homepage text " * 500

    screen(BRIEF, cands, fetch=fetch, progress=lambda _: None)
    by = {c.domain: c for c in cands}

    good = by["good-a.com"]
    assert good.reason == "because good-a.com"
    assert (good.holder_type, good.data_owner) == ("untapped", "company")
    assert good.languages == ["English"]  # the buyer's spelling, not the model's
    # data_match 5, coverage 5, licence prior 3 (by-product owning its data), volume unknown
    assert (good.scores.data_match, good.scores.coverage, good.scores.licence, good.scores.volume) == (5, 5, 3, None)
    assert good.fit == round((0.4 * 5 + 0.25 * 5 + 0.2 * 3) / 0.85, 2)
    assert by["no-c.com"].excluded
    assert by["down.com"].fit is None and not by["down.com"].excluded  # unreachable is kept, not judged
    assert by["down.com"].reason.startswith("not screened: homepage couldn't be read")
    assert by["silent-d.com"].screened and by["silent-d.com"].fit is None  # model skipped it
    assert [c.domain for c in rank(cands)][:2] == ["good-a.com", "mid-b.com"]
    assert [c.domain for c in cands if is_relevant(c)] == ["good-a.com", "mid-b.com"]

    (call,) = fake_ask.calls  # one batch; unreachable homepage never sent to the model
    assert "down.com" not in call.prompt
    assert call.prompt.count("homepage text") < 2000  # text is truncated per company
    assert "data_match (1 to 5)" in call.prompt
    assert "Must have — Modality: Audio/speech" in call.prompt and "Speakers per recording: 3 to 5" in call.prompt
    assert "- modality: Modality: Audio/speech" in call.prompt  # must-haves listed by key
    assert "Volume: 1,000 hours" in call.prompt


def test_screen_batches(fake_ask):
    fake_ask.responses[ScreenBatch] = _score_by_prefix
    cands = [_cand(f"good-{i}.com") for i in range(SCREEN_BATCH_SIZE * 2 + 1)]
    screen(BRIEF, cands, fetch=lambda _: "text", progress=lambda _: None)
    assert len(fake_ask.calls) == 3
    assert all(c.scores.data_match == 5 for c in cands)


def _connect_error(cause: Exception):
    """httpx.ConnectError the way httpx raises it: chained from the socket/ssl error."""
    import httpx

    try:
        try:
            raise cause
        except Exception as inner:
            raise httpx.ConnectError(str(inner)) from inner
    except httpx.ConnectError as e:
        return e


def test_only_dns_failure_counts_as_dead_site(fake_ask):
    import socket
    import ssl

    import httpx

    fake_ask.responses[ScreenBatch] = _score_by_prefix
    names = ["made-up.com", "refused.com", "bad-cert.com", "blocked.com", "slow.com"]
    cands = [_cand(d) for d in names]
    req = httpx.Request("GET", "https://blocked.com")
    errors = {
        "made-up": _connect_error(socket.gaierror(8, "nodename nor servname provided")),
        "refused": _connect_error(ConnectionRefusedError(61, "Connection refused")),
        "bad-cert": _connect_error(ssl.SSLCertVerificationError("certificate has expired")),
        "blocked": httpx.HTTPStatusError("403", request=req, response=httpx.Response(403, request=req)),
        "slow": httpx.ReadTimeout("timed out"),
    }

    def fetch(url):
        raise next(e for key, e in errors.items() if key in url)

    screen(BRIEF, cands, fetch=fetch, progress=lambda _: None)
    by = {c.domain: c for c in cands}
    assert (by["made-up.com"].excluded, by["made-up.com"].reason) == (True, "dead website: domain doesn't exist")
    for d in names[1:]:  # real companies do these to crawlers: keep them, unscored
        assert not by[d].excluded and by[d].fit is None and by[d].screened, d
    assert fake_ask.calls == []  # nothing reachable, nothing sent to the model


def test_only_unscreened_candidates_are_screened(fake_ask):
    fake_ask.responses[ScreenBatch] = _score_by_prefix
    done = _cand("good-old.com")
    done.reason, done.excluded = "screened earlier", True
    new = _cand("good-new.com")
    fetched = []
    screen(BRIEF, [done, new], fetch=lambda url: fetched.append(url) or "text", progress=lambda _: None)
    assert fetched == ["https://good-new.com"]
    assert done.reason == "screened earlier" and new.scores.data_match == 5


def test_rank_by_fit_then_corroboration():
    def scored(domain, fit, channels=("product_search",), excluded=False):
        c = _cand(domain)
        c.reason, c.fit, c.channels, c.excluded = "r", fit, list(channels), excluded
        return c

    famous = scored("famous.com", 3.5, ["product_search", "directory", "snowball"])
    niche = scored("niche.com", 3.5)
    vendor = scored("vendor.com", 4.6, ["marketplace"])
    unknown = scored("unknown.com", None, ["product_search", "directory", "snowball", "marketplace"])
    dropped = scored("dropped.com", 4.9, excluded=True)
    order = [c.domain for c in rank([unknown, niche, dropped, famous, vendor])]
    assert order == ["vendor.com", "famous.com", "niche.com", "unknown.com", "dropped.com"]


def test_tool_maker_without_the_data_is_not_sellable(fake_ask):
    fake_ask.responses[ScreenBatch] = _score_by_prefix
    tech = _cand("good-tech.com")  # pages are all about the data type, but it doesn't keep any
    screen(BRIEF, [tech], fetch=lambda _: "text", progress=lambda _: None)
    assert tech.holder_type == "untapped" and tech.saleability.exists.score == 1
    assert tech.scores.licence == 1 and tech.fit == NOT_SELLABLE_CAP


def test_no_evidence_for_a_must_have_language_caps_fit(fake_ask):
    fake_ask.responses[ScreenBatch] = _score_by_prefix
    brief = brief_with(languages=Pick(values=["English"], importance="must"))
    local, global_tool = _cand("good-local.com"), _cand("good-nolang.com")
    screen(brief, [local, global_tool], fetch=lambda _: "text", progress=lambda _: None)
    assert local.languages == ["English"] and local.fit > MUST_HAVE_CAP
    assert (global_tool.languages, global_tool.scores.coverage, global_tool.fit) == ([], 1, MUST_HAVE_CAP)
    assert not is_relevant(global_tool)
    assert '"100+ languages" is not evidence' in fake_ask.calls[0].prompt


def test_no_language_evidence_without_must_have_only_lowers_coverage(fake_ask):
    fake_ask.responses[ScreenBatch] = _score_by_prefix
    c = _cand("good-nolang.com")
    screen(BRIEF, [c], fetch=lambda _: "text", progress=lambda _: None)
    assert c.scores.coverage == 1 and c.fit > MUST_HAVE_CAP


def test_only_the_briefs_languages_and_regions_count(fake_ask):
    def answer(prompt):
        return ScreenBatch(results=[
            ScreenItem(domain="tilde.ee", holder_type="ready", data_owner="company", data_match=5,
                       languages=[" latvian", "French"], regions=["baltics "], volume_signal=4,
                       reason="Baltic speech data"),
            ScreenItem(domain="global.com", holder_type="untapped", data_owner="customers", data_match=5,
                       languages=["French", "German"], regions=["Europe"], volume_signal=9,
                       reason="notetaker"),
        ])

    fake_ask.responses[ScreenBatch] = answer
    brief = brief_with(languages=Pick(values=["Estonian", "Latvian"]), region=Pick(values=["Baltics"]))
    tilde, other = _cand("tilde.ee"), _cand("global.com")
    screen(brief, [tilde, other], fetch=lambda _: "text", progress=lambda _: None)
    assert (tilde.languages, tilde.regions, tilde.scores.coverage) == (["Latvian"], ["Baltics"], 5)
    assert (other.languages, other.regions, other.scores.coverage) == ([], [], 1)
    assert other.scores.volume == 5  # out-of-range answers are clamped


def test_coverage_unknown_when_brief_names_no_languages_or_regions(fake_ask):
    fake_ask.responses[ScreenBatch] = _score_by_prefix
    c = _cand("good-nolang.com")
    screen(brief_with(languages=None), [c], fetch=lambda _: "text", progress=lambda _: None)
    assert c.scores.coverage is None


def test_ready_source_working_on_customers_data_is_untapped(fake_ask):
    def answer(prompt):
        return ScreenBatch(results=[
            ScreenItem(domain="agency.lt", holder_type="ready", data_owner="customers", data_match=4,
                       languages=["English"], regions=[], volume_signal=None, exists=4, allowed=4, willing=4,
                       reason="interpreting and transcription agency"),
            ScreenItem(domain="data.com", holder_type="ready", data_owner="company", data_match=5,
                       languages=["English"], regions=[], volume_signal=None, reason="sells meeting datasets"),
        ])

    fake_ask.responses[ScreenBatch] = answer
    agency, seller = _cand("agency.lt"), _cand("data.com")
    screen(BRIEF, [agency, seller], fetch=lambda _: "text", progress=lambda _: None)
    # its customers own the data: "allowed" is at most 2, so the grade is low
    assert (agency.holder_type, agency.data_owner, agency.scores.licence) == ("untapped", "customers", 2)
    assert (seller.holder_type, seller.saleability, seller.scores.licence) == ("ready", None, 4)


def test_screen_sends_homepage_and_source_page(fake_ask):
    fake_ask.responses[ScreenBatch] = _score_by_prefix
    with_src, without = _cand("good-a.com"), _cand("good-b.com")
    excerpts = {"good-a.com": "Source page on its own site (x):\nmultilingual speech data collection"}
    screen(BRIEF, [with_src, without], fetch=lambda url: f"translation services {url}",
           source=lambda c: excerpts.get(c.domain), progress=lambda _: None)
    (call,) = fake_ask.calls
    a, b = call.prompt.split("### good-a.com")[1].split("### good-b.com")
    assert "translation services" in a and "multilingual speech data collection" in a
    assert "Source page" not in b


PARAGRAPH = "<p>{}</p>"


def _page(*paragraphs):
    return "<html><body><article>" + "".join(PARAGRAPH.format(p) for p in paragraphs) + "</article></body></html>"


def test_source_excerpt_prefers_own_site_and_quotes_other_sites_around_the_mention():
    filler = [f"Paragraph {i} is about transcription tools in general and nothing specific." for i in range(40)]
    pages = {
        "https://acme.com/speech-data": _page(
            "Speech data collection for AI training.",
            "We record consented multi-speaker conversations in Estonian, Latvian and Lithuanian, "
            "with verbatim transcripts and speaker labels, for companies building speech models.",
        ),
        "https://blog.example/best": _page(*filler, "Acme offers Estonian meeting transcription for councils."),
        "https://blog.example/unrelated": _page(*filler),
    }

    def fetch(url):
        if url not in pages:
            raise httpx.ConnectError("down")
        return pages[url]

    c = _cand("acme.com")
    c.name = "Acme"

    c.source_urls = ["https://blog.example/best", "https://acme.com/speech-data"]
    own = source_excerpt(c, fetch)
    assert own.startswith("Source page on its own site (https://acme.com/speech-data)")
    assert "Estonian, Latvian and Lithuanian" in own

    c.source_urls = ["https://acme.com/gone", "https://blog.example/best"]  # own page down: use the other
    other = source_excerpt(c, fetch)
    assert other.startswith("Source page on another site (https://blog.example/best)")
    assert "Acme offers Estonian meeting transcription" in other
    assert "Paragraph 0 " not in other  # only the passage around the mention

    c.source_urls = ["https://blog.example/unrelated"]  # doesn't mention the company
    assert source_excerpt(c, fetch) is None

    c.source_urls = ["https://acme.com/", "https://www.acme.com"]  # the homepage is already sent
    assert source_excerpt(c, lambda url: pytest.fail(f"fetched {url}")) is None


def _answer(**kw):
    item = dict(domain="acme.com", holder_type="untapped", data_owner="customers", data_match=5,
                languages=["English"], regions=[], volume_signal=3, reason="call recording service")
    return lambda prompt: ScreenBatch(results=[ScreenItem(**{**item, **kw})])


def test_only_must_have_contradictions_cap(fake_ask):
    fake_ask.responses[ScreenBatch] = _answer(contradicts=["modality", "speakers", "made_up"])
    c = _cand("acme.com")
    screen(BRIEF, [c], fetch=lambda _: "text", progress=lambda _: None)
    assert c.contradicts == ["modality"]  # speakers is only nice to have; unknown keys dropped
    assert c.fit == MUST_HAVE_CAP


def test_must_have_filters_exclude(fake_ask):
    fake_ask.responses[ScreenBatch] = _answer()
    vendors_only = brief_with(sources=Pick(values=[READY], importance="must"))
    c = _cand("acme.com")
    screen(vendors_only, [c], fetch=lambda _: "text", progress=lambda _: None)
    assert c.excluded and "not a source type the buyer accepts" in c.reason

    no_customer_data = brief_with(customer_data=Pick(values=[CUSTOMER_EXCLUDE], importance="must"))
    c = _cand("acme.com")
    screen(no_customer_data, [c], fetch=lambda _: "text", progress=lambda _: None)
    assert c.excluded and "customers' data" in c.reason

    nice = brief_with(sources=Pick(values=[READY]), customer_data=Pick(values=[CUSTOMER_EXCLUDE]))
    c = _cand("acme.com")
    screen(nice, [c], fetch=lambda _: "text", progress=lambda _: None)
    assert not c.excluded and c.scores.licence == 1  # nice to have: "allowed" is 1, not dropped


def test_volume_left_empty_is_ignored(fake_ask):
    fake_ask.responses[ScreenBatch] = _answer(volume_signal=1)
    c = _cand("acme.com")
    screen(brief_with(volume=None), [c], fetch=lambda _: "text",
           progress=lambda _: None)
    assert c.scores.volume is None


def test_price_and_age_kept_only_with_a_quote_on_the_page(fake_ask):
    home = "Acme records meetings for teams. Our meeting audio costs $30 per audio hour."
    src = ("Source page on another site (https://listings.example/acme), passage mentioning it:\n"
           "Acme meeting recordings were collected from 2018 to 2024 across the UK.")
    fake_ask.responses[ScreenBatch] = _answer(
        price=Stated(value="$30 per audio hour", quote="Our meeting audio costs $30 per audio hour"),
        founded=Stated(value="2012", quote="Acme was founded in 2012 by two engineers"),  # not on the pages
        data_period=Stated(value="2016 to 2024", quote="Acme meeting recordings were collected from 2018 to 2024"),
    )
    c = _cand("acme.com")
    screen(BRIEF, [c], fetch=lambda _: home, progress=lambda _: None, source=lambda _: src)
    assert (c.price.value, c.price.url) == ("$30 per audio hour", "https://acme.com")
    assert c.founded is None
    assert c.data_period is None  # 2016 isn't in the quote, so the quote doesn't back the value

    fake_ask.responses[ScreenBatch] = _answer(data_period=Stated(
        value="2018 to 2024", quote="Acme meeting recordings were collected from 2018 to 2024"))
    c = _cand("acme.com")
    screen(BRIEF, [c], fetch=lambda _: home, progress=lambda _: None, source=lambda _: src)
    assert (c.data_period.value, c.data_period.url) == ("2018 to 2024", "https://listings.example/acme")


def test_short_fact_quotes_are_kept_when_they_contain_the_numbers(fake_ask):
    fake_ask.responses[ScreenBatch] = _answer(founded={"value": "2016", "quote": "Founded in 2016."},
                                              price={"value": "£500", "quote": "We record calls."})
    c = _cand("acme.com")
    screen(BRIEF, [c], fetch=lambda _: "Founded in 2016. We record calls.", progress=lambda _: None,
           source=lambda _: None)
    assert c.founded and c.founded.value == "2016"  # 3 words, but the quote contains the year
    assert c.price is None  # the quote doesn't contain the price


def test_blocked_homepage_is_judged_from_the_page_it_was_found_on(fake_ask):
    fake_ask.responses[ScreenBatch] = _score_by_prefix
    blocked, lost = _cand("good-blocked.com"), _cand("good-lost.com")

    def fetch(url):
        request = httpx.Request("GET", url)
        raise httpx.HTTPStatusError("403", request=request, response=httpx.Response(403, request=request))

    def source(c):
        return "Source page on another site (https://news.example/a), passage mentioning it:\nx" if c is blocked else None

    screen(BRIEF, [blocked, lost], fetch=fetch, progress=lambda _: None, source=source)
    assert blocked.fit is not None
    assert blocked.reason.endswith("(homepage blocks automated access (HTTP 403): judged from the page it was found on)")
    assert (lost.fit, lost.excluded) == (None, False)
    assert lost.reason == ("not screened: homepage blocks automated access (HTTP 403), "
                           "and no other page about it was found")
    (call,) = fake_ask.calls
    assert NO_HOMEPAGE in call.prompt and "good-lost.com" not in call.prompt
