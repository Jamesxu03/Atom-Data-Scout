import httpx

from tests.test_hypotheses import BRIEF, brief_with
from worker.brief_template import DISTRESSED, SELLERS, UNTAPPED, Pick
from worker.config import DISCOVERY
from worker.discovery import Discovery, Hit, channels, discover, merge, pick_seeds, snowball
from worker.discovery.domains import normalize_domain
from worker.llm import QuotaExceeded
from worker.schemas import (
    Candidate,
    Contact,
    EmailDraft,
    Found,
    FoundList,
    HolderHypothesis,
    Scores,
)

HYPS = [
    HolderHypothesis(category=f"cat {i}", rationale="r", search_queries=[f"query {i}"])
    for i in range(6)
]
QUIET = {"progress": lambda _: None}


def test_normalize_domain():
    assert normalize_domain("https://www.Otter.ai/pricing?x=1") == "otter.ai"
    assert normalize_domain("otter.ai") == "otter.ai"
    assert normalize_domain("app.fireflies.ai") == "app.fireflies.ai"
    assert normalize_domain("") is None
    assert normalize_domain("not a url") is None


def test_merge_dedupes_by_domain_and_keeps_all_sources():
    hits = [
        Hit(channel="product_search", name="Otter", website="https://otter.ai", source_url="s1", hypothesis="h1"),
        Hit(channel="snowball", name="Otter.ai", website="http://www.otter.ai/", source_url="s2", source_verified=True),
        Hit(channel="directory", name="Otter", website="otter.ai", source_url="s1", hypothesis="h2"),
        Hit(channel="directory", name="G2", website="https://www.g2.com", source_url="s3"),
        Hit(channel="marketplace", name="No site", website="", source_url="s4"),
    ]
    (otter,) = merge(hits)
    assert otter.domain == "otter.ai"
    assert otter.name == "Otter"  # first seen wins
    assert otter.channels == ["product_search", "snowball", "directory"]
    assert otter.source_urls == ["s1", "s2"]
    assert otter.hypotheses == ["h1", "h2"]
    assert otter.source_verified


def test_merge_carries_over_everything_it_does_not_build():
    hits = [Hit(channel="product_search", name="Otter", website="https://otter.ai", source_url="s")]
    (old,) = merge(hits)
    old.reason, old.holder_type, old.fit = "notetaker", "untapped", 4.2
    old.scores = Scores(data_match=5, coverage=5)
    old.contacts = [Contact(email="a@otter.ai", source_url="s")]
    old.email = EmailDraft(subject="Hello", body="Hi")
    (new,) = merge(hits + [Hit(channel="snowball", name="Otter", website="otter.ai", source_url="s2")], previous=[old])
    assert (new.reason, new.holder_type, new.fit, new.scores) == ("notetaker", "untapped", 4.2, old.scores)
    assert new.contacts == old.contacts and new.email == old.email
    assert new.channels == ["product_search", "snowball"]  # rebuilt from the hits


def test_merge_skips_excluded_domains():
    hits = [Hit(channel="product_search", name="Otter", website="https://app.otter.ai", source_url="s"),
            Hit(channel="product_search", name="Fireflies", website="https://fireflies.ai", source_url="s")]
    assert [c.domain for c in merge(hits, exclusions=["https://www.otter.ai/"])] == ["fireflies.ai"]


def _router(prompt: str) -> FoundList:
    """Fake search results keyed off which channel prompt this is."""
    if "compete with" in prompt:
        seed = prompt.split("compete with ")[1].split(" (")[0]
        return FoundList(companies=[Found(name=f"Rival of {seed}", website=f"https://rival-{seed.lower()}.com", source_url=f"https://blog.com/{seed}-alternatives")])
    if "directory category pages" in prompt:
        return FoundList(companies=[
            Found(name="Otter", website="https://otter.ai", source_url="https://www.g2.com/categories/x"),
            Found(name="G2", website="https://g2.com", source_url="https://www.g2.com/categories/x"),
        ])
    if "already produce or sell data" in prompt:
        return FoundList(companies=[Found(name="DataCo", website="https://dataco.io", source_url="https://datarade.ai/x")])
    if "insolvency notices" in prompt:
        return FoundList(companies=[Found(name="Bust Co", website="https://bust.example",
                                          source_url="https://gazette.example/notice")])
    if "not only companies" in prompt:
        return FoundList(companies=[Found(name="Parliament", website="https://parliament.example",
                                          source_url="https://parliament.example/sessions")])
    for phrase, name in (("signs on their own pages", "Callcentre"), ("logo walls", "Toolcustomer"),
                         ("public tenders", "Tenderwinner")):
        if phrase in prompt:
            return FoundList(companies=[Found(name=name, website=f"https://{name.lower()}.example",
                                              source_url=f"https://{name.lower()}.example/privacy")])
    query = prompt.split("- query ")[1].split("\n")[0]
    return FoundList(companies=[
        Found(name="Otter", website="https://otter.ai", source_url="https://google.com/1"),
        Found(name=f"Co{query}", website=f"https://co{query}.com", source_url="https://google.com/2"),
    ])


def test_discover_runs_phase_one_channels_within_budget(fake_ask):
    fake_ask.responses[FoundList] = _router
    d = discover(BRIEF, HYPS, **QUIET)

    angles = min(DISCOVERY["marketplace_calls"], len(channels.MARKETPLACE_ANGLES))
    public = min(DISCOVERY["public_source_calls"], len(channels.PUBLIC_ANGLES))
    untapped = DISCOVERY["signals_calls"] + DISCOVERY["suppliers_calls"] + DISCOVERY["records_calls"]
    expected = (DISCOVERY["product_search_hypotheses"] + DISCOVERY["directory_hypotheses"] + angles + public
                + DISCOVERY["distressed_calls"] + untapped)
    assert len(fake_ask.calls) == expected
    assert all(c.use_search for c in fake_ask.calls)
    assert not any("compete with" in c.prompt for c in fake_ask.calls)  # no snowball yet
    vendor_prompts = [c.prompt for c in fake_ask.calls if "already produce or sell data" in c.prompt]
    assert len(set(vendor_prompts)) == angles  # each call searches from a different angle
    public_prompts = [c.prompt for c in fake_ask.calls if "not only companies" in c.prompt]
    assert len(set(public_prompts)) == public
    assert all("Also search in English" in p for p in public_prompts)
    assert all("Languages and dialects: English" in p for p in vendor_prompts + public_prompts)

    by_domain = {c.domain: c for c in d.candidates}
    assert set(by_domain["otter.ai"].channels) == {"product_search", "directory"}
    assert "g2.com" not in by_domain  # directories are sources, not candidates
    assert by_domain["dataco.io"].channels == ["marketplace"]
    assert by_domain["parliament.example"].channels == ["public_source"]
    assert by_domain["bust.example"].channels == ["distressed"]
    assert [by_domain[f"{n}.example"].channels for n in ("callcentre", "toolcustomer", "tenderwinner")] == [
        ["signals"], ["suppliers"], ["records"]]
    assert len(d.hits) > len(d.candidates)
    assert not d.partial


def test_discover_only_runs_channels_for_the_source_types_picked(fake_ask):
    fake_ask.responses[FoundList] = _router
    discover(brief_with(sources=Pick(values=[UNTAPPED])), HYPS, **QUIET)
    assert not any("already produce or sell data" in c.prompt for c in fake_ask.calls)
    assert not any("not only companies" in c.prompt or "insolvency notices" in c.prompt for c in fake_ask.calls)
    assert len(fake_ask.calls) == DISCOVERY["product_search_hypotheses"] + DISCOVERY["directory_hypotheses"] + 3
    (signals,) = [c.prompt for c in fake_ask.calls if "signs on their own pages" in c.prompt]
    assert "Kept by law" in signals and "we retain recordings for" in signals  # the archetype checklist
    (suppliers,) = [c.prompt for c in fake_ask.calls if "logo walls" in c.prompt]
    assert "call-recording and contact-centre platforms" in suppliers  # tools for the audio modality

    fake_ask.calls.clear()
    d = discover(brief_with(sources=Pick(values=[SELLERS, DISTRESSED]), exclusions=Pick(values=["dataco.io"])),
                 HYPS, **QUIET)
    prompts = [c.prompt for c in fake_ask.calls]
    assert not any("- query " in p for p in prompts)  # no product search or directories
    assert sum("already produce or sell data" in p for p in prompts) == 1  # published datasets
    assert not any("signs on their own pages" in p for p in prompts)  # no untapped-holder channels
    assert sum("insolvency notices" in p for p in prompts) == 1
    assert "dataco.io" not in {c.domain for c in d.candidates} and d.exclusions == ["dataco.io"]


def test_pick_seeds_uses_explicit_then_well_scored_by_product_holders():
    def cand(domain, fit, channels, holder="untapped", coverage=5):
        return Candidate(name=domain, website=f"https://{domain}", domain=domain, channels=channels,
                         source_urls=[], reason="r", fit=fit, holder_type=holder,
                         scores=Scores(data_match=5, coverage=coverage))

    cands = [
        cand("low.com", 3.0, ["product_search", "directory"]),
        cand("one.com", 4.0, ["product_search"]),
        cand("two.com", 4.0, ["product_search", "directory"]),  # same fit, better corroborated
        cand("best.com", 4.8, ["product_search"]),
        cand("unscreened.com", None, ["product_search", "directory", "marketplace"], holder=None),
        cand("fireflies.ai", 4.5, ["directory"]),
        # high fit, but no evidence of the buyer's languages: its alternatives would be too
        cand("global.com", 4.6, ["product_search", "directory"], coverage=1),
        # well corroborated, but alternatives to a tracking-hardware maker drift off topic
        cand("tracker.com", 3.0, ["product_search", "directory", "snowball"], holder="technology"),
        cand("vendor.com", 4.9, ["product_search", "directory", "marketplace"], holder="seller"),
    ]
    seeds = pick_seeds(cands, explicit=[("Fireflies", "https://fireflies.ai")])
    assert [w for _, w in seeds] == ["https://fireflies.ai", "https://best.com", "https://two.com",
                                     "https://one.com"][: DISCOVERY["snowball_seeds"]]


def test_snowball_adds_competitors_and_keeps_scores(fake_ask):
    fake_ask.responses[FoundList] = _router
    d = discover(BRIEF, HYPS, **QUIET)
    otter = next(c for c in d.candidates if c.domain == "otter.ai")
    otter.reason, otter.fit = "notetaker", 4.0

    d2 = snowball(d, [("Otter", "https://otter.ai")], **QUIET)
    by_domain = {c.domain: c for c in d2.candidates}
    assert by_domain["rival-otter.com"].channels == ["snowball"]
    assert not by_domain["rival-otter.com"].screened  # new: needs screening
    assert (by_domain["otter.ai"].reason, by_domain["otter.ai"].fit) == ("notetaker", 4.0)  # kept
    assert [h.seed for h in d2.hits if h.channel == "snowball"] == ["Otter"]
    assert "buyer needs data in" not in fake_ask.calls[-1].prompt  # no languages given


def test_snowball_limited_to_the_briefs_languages_and_regions(fake_ask):
    fake_ask.responses[FoundList] = _router
    d = Discovery(hits=[], candidates=[], exclusions=["rival-beey.com"])
    d2 = snowball(d, [("Beey", "https://beey.io")], languages=["Estonian", "Slovak"], regions=["Baltics"], **QUIET)
    (call,) = fake_ask.calls
    assert "The buyer needs data in: Estonian, Slovak" in call.prompt
    assert '"100+ languages"' in call.prompt  # global tools' generic claim doesn't count
    assert "The buyer needs data from: Baltics" in call.prompt
    assert d2.candidates == []  # the only competitor found is excluded


def test_failed_channel_call_is_recorded_not_fatal(fake_ask):
    def flaky(prompt):
        if "Datarade" in prompt:
            raise RuntimeError("boom")
        return _router(prompt)

    fake_ask.responses[FoundList] = flaky
    d = discover(BRIEF, HYPS, **QUIET)
    assert d.candidates
    assert len(d.errors) == 1 and "marketplace listings" in d.errors[0]
    assert not d.partial


def test_quota_mid_discovery_keeps_hits_and_marks_partial(fake_ask):
    def quota_on_directories(prompt):
        if "directory category pages" in prompt:
            raise QuotaExceeded("daily cap")
        return _router(prompt)

    fake_ask.responses[FoundList] = quota_on_directories
    d = discover(BRIEF, HYPS, **QUIET)  # must not raise
    assert d.partial
    assert d.candidates  # results from calls that finished are kept
    assert any("quota" in e for e in d.errors)


def test_verify_source(monkeypatch):
    pages = {"https://blog.example/best": "<p>Top tools: Fireflies.ai, Otter</p>",
             "https://blog.example/other": "<p>Nothing relevant</p>"}

    def fetch(url):
        if url not in pages:
            raise httpx.ConnectError("down")
        return pages[url]

    monkeypatch.setattr(channels, "fetch_html_quick", fetch)
    ff = dict(name="Fireflies.ai", website="https://fireflies.ai")
    assert channels.verify_source(Found(**ff, source_url="https://blog.example/best")) == ("https://blog.example/best", True)
    assert channels.verify_source(Found(**ff, source_url="https://blog.example/other"))[1] is False
    assert channels.verify_source(Found(**ff, source_url="https://gone.example/x"))[1] is False
    # by full name when the domain label is too short to be meaningful
    assert channels.mention_at("<p>We love Read AI</p>", "Read AI", "https://www.read.ai") >= 0


def test_verify_source_resolves_grounding_redirect(monkeypatch):
    class Resp:
        headers = {"location": "https://real.example/page"}

    monkeypatch.setattr(httpx, "head", lambda url, **kw: Resp())
    redirect = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQ"
    f = Found(name="X", website="https://x.com", source_url=redirect)
    assert channels.verify_source(f) == ("https://real.example/page", True)


def test_unresolvable_redirect_is_kept_but_unverified(monkeypatch):
    def boom(url, **kw):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "head", boom)
    redirect = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQ"
    assert channels.verify_source(Found(name="X", website="https://x.com", source_url=redirect)) == (redirect, False)


def test_check_sources_only_for_given_domains_and_only_once(monkeypatch):
    from worker.discovery import check_sources

    fetched = []

    def fake_page(url):
        fetched.append(url)
        return url + "#real", "<p>Keep, Keep2 and Keep3 are great</p>", False

    monkeypatch.setattr(channels, "check_source_page", fake_page)
    hits = [
        Hit(channel="product_search", name="Keep", website="https://keep.com", source_url="s1"),
        Hit(channel="product_search", name="Drop", website="https://drop.com", source_url="s2"),
    ]
    d = Discovery(hits=hits, candidates=merge(hits))
    d = check_sources(d, {"keep.com"}, **QUIET)
    d = check_sources(d, {"keep.com"}, **QUIET)  # second call: nothing new to fetch

    assert fetched == ["s1"]
    by = {c.domain: c for c in d.candidates}
    assert by["keep.com"].source_verified and by["keep.com"].source_urls == ["s1#real"]
    assert not by["drop.com"].source_verified and by["drop.com"].source_urls == ["s2"]


def test_shared_directory_page_is_fetched_once(monkeypatch):
    from worker.discovery import check_sources

    fetched = []

    def fake_page(url):
        fetched.append(url)
        return url, "<p>" + " ".join(f"Vendor{i}" for i in range(0, 10, 2)) + "</p>", False

    monkeypatch.setattr(channels, "check_source_page", fake_page)
    g2 = "https://www.g2.com/categories/meeting-assistants"
    hits = [Hit(channel="directory", name=f"Vendor{i}", website=f"https://vendor{i}.com", source_url=g2)
            for i in range(10)]
    d = Discovery(hits=hits, candidates=merge(hits))
    d = check_sources(d, {c.domain for c in d.candidates}, **QUIET)

    assert fetched == [g2]  # ten companies, one request
    verified = sorted(c.domain for c in d.candidates if c.source_verified)
    assert verified == [f"vendor{i}.com" for i in range(0, 10, 2)]  # only those named on the page
