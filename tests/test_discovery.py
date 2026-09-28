import httpx

from tests.test_hypotheses import SPEC
from worker.config import DISCOVERY
from worker.discovery import Discovery, Hit, channels, discover, merge, pick_seeds, snowball
from worker.discovery.domains import normalize_domain
from worker.llm import QuotaExceeded
from worker.schemas import Candidate, Found, FoundList, HolderHypothesis

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


def test_merge_carries_over_screening():
    hits = [Hit(channel="product_search", name="Otter", website="https://otter.ai", source_url="s")]
    (old,) = merge(hits)
    old.relevance, old.relevance_reason = "yes", "notetaker"
    (new,) = merge(hits + [Hit(channel="snowball", name="Otter", website="otter.ai", source_url="s2")], previous=[old])
    assert (new.relevance, new.relevance_reason) == ("yes", "notetaker")
    assert new.channels == ["product_search", "snowball"]


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
    if "Datarade" in prompt:
        return FoundList(companies=[Found(name="DataCo", website="https://dataco.io", source_url="https://datarade.ai/x")])
    query = prompt.split("- query ")[1].split("\n")[0]
    return FoundList(companies=[
        Found(name="Otter", website="https://otter.ai", source_url="https://google.com/1"),
        Found(name=f"Co{query}", website=f"https://co{query}.com", source_url="https://google.com/2"),
    ])


def test_discover_runs_phase_one_channels_within_budget(fake_ask):
    fake_ask.responses[FoundList] = _router
    d = discover(SPEC, HYPS, **QUIET)

    expected = DISCOVERY["product_search_hypotheses"] + DISCOVERY["directory_hypotheses"] + DISCOVERY["marketplace_calls"]
    assert len(fake_ask.calls) == expected
    assert all(c.use_search for c in fake_ask.calls)
    assert not any("compete with" in c.prompt for c in fake_ask.calls)  # no snowball yet

    by_domain = {c.domain: c for c in d.candidates}
    assert set(by_domain["otter.ai"].channels) == {"product_search", "directory"}
    assert "g2.com" not in by_domain  # directories are sources, not candidates
    assert by_domain["dataco.io"].channels == ["marketplace"]
    assert len(d.hits) > len(d.candidates)
    assert not d.partial


def test_pick_seeds_uses_explicit_then_screened_yes():
    def cand(domain, relevance, channels):
        return Candidate(name=domain, website=f"https://{domain}", domain=domain, channels=channels,
                         source_urls=[], relevance=relevance)

    cands = [
        cand("maybe.com", "maybe", ["product_search", "directory"]),
        cand("one.com", "yes", ["product_search"]),
        cand("two.com", "yes", ["product_search", "directory"]),
        cand("unscreened.com", None, ["product_search", "directory", "marketplace"]),
        cand("fireflies.ai", "yes", ["directory"]),
    ]
    seeds = pick_seeds(cands, explicit=[("Fireflies", "https://fireflies.ai")])
    assert [w for _, w in seeds] == ["https://fireflies.ai", "https://two.com", "https://one.com"][: DISCOVERY["snowball_seeds"]]


def test_snowball_adds_competitors_and_keeps_labels(fake_ask):
    fake_ask.responses[FoundList] = _router
    d = discover(SPEC, HYPS, **QUIET)
    otter = next(c for c in d.candidates if c.domain == "otter.ai")
    otter.relevance = "yes"

    d2 = snowball(d, [("Otter", "https://otter.ai")], **QUIET)
    by_domain = {c.domain: c for c in d2.candidates}
    assert by_domain["rival-otter.com"].channels == ["snowball"]
    assert by_domain["rival-otter.com"].relevance is None  # new: needs screening
    assert by_domain["otter.ai"].relevance == "yes"  # kept
    assert [h.seed for h in d2.hits if h.channel == "snowball"] == ["Otter"]


def test_failed_channel_call_is_recorded_not_fatal(fake_ask):
    def flaky(prompt):
        if "Datarade" in prompt:
            raise RuntimeError("boom")
        return _router(prompt)

    fake_ask.responses[FoundList] = flaky
    d = discover(SPEC, HYPS, **QUIET)
    assert d.candidates
    assert len(d.errors) == 1 and "marketplaces" in d.errors[0]
    assert not d.partial


def test_quota_mid_discovery_keeps_hits_and_marks_partial(fake_ask):
    def quota_on_directories(prompt):
        if "directory category pages" in prompt:
            raise QuotaExceeded("daily cap")
        return _router(prompt)

    fake_ask.responses[FoundList] = quota_on_directories
    d = discover(SPEC, HYPS, **QUIET)  # must not raise
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
    assert channels._mentions("<p>We love Read AI</p>", "Read AI", "https://www.read.ai")


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
