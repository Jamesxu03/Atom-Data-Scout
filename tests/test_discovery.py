from tests.test_hypotheses import SPEC
from worker.config import DISCOVERY
from worker.discovery import Hit, discover, merge
from worker.discovery.domains import normalize_domain
from worker.schemas import Found, FoundList, HolderHypothesis

HYPS = [
    HolderHypothesis(category=f"cat {i}", rationale="r", search_queries=[f"query {i}"])
    for i in range(6)
]


def test_normalize_domain():
    assert normalize_domain("https://www.Otter.ai/pricing?x=1") == "otter.ai"
    assert normalize_domain("otter.ai") == "otter.ai"
    assert normalize_domain("app.fireflies.ai") == "app.fireflies.ai"
    assert normalize_domain("") is None
    assert normalize_domain("not a url") is None


def test_merge_dedupes_by_domain_and_keeps_all_sources():
    hits = [
        Hit(channel="product_search", name="Otter", website="https://otter.ai", source_url="s1", hypothesis="h1"),
        Hit(channel="snowball", name="Otter.ai", website="http://www.otter.ai/", source_url="s2"),
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


def _router(prompt: str) -> FoundList:
    """Fake search results keyed off which channel prompt this is."""
    if "compete with" in prompt:
        seed = prompt.split("compete with ")[1].split(" (")[0]
        return FoundList(companies=[Found(name=f"Rival of {seed}", website=f"https://rival-{seed}.com", source_url=f"https://blog.com/{seed}-alternatives")])
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


def test_discover_runs_all_channels_within_budget(fake_ask):
    fake_ask.responses[FoundList] = _router
    d = discover(SPEC, HYPS, seeds=[("Fireflies", "https://fireflies.ai")], progress=lambda _: None)

    n_a = DISCOVERY["product_search_hypotheses"]
    n_b = DISCOVERY["snowball_seeds"]
    n_c = DISCOVERY["directory_hypotheses"]
    n_d = DISCOVERY["marketplace_calls"]
    assert len(fake_ask.calls) == n_a + n_b + n_c + n_d
    assert all(c.use_search for c in fake_ask.calls)

    by_domain = {c.domain: c for c in d.candidates}
    assert set(by_domain["otter.ai"].channels) == {"product_search", "directory"}
    assert "g2.com" not in by_domain  # directories are sources, not candidates
    assert by_domain["dataco.io"].channels == ["marketplace"]
    # explicit seed is snowballed first, then discovered companies
    snowball_seeds = [h.seed for h in d.hits if h.channel == "snowball"]
    assert snowball_seeds[0] == "Fireflies"
    assert snowball_seeds[1] == "Otter"
    assert len(d.hits) > len(d.candidates)


def test_failed_channel_call_is_recorded_not_fatal(fake_ask):
    def flaky(prompt):
        if "Datarade" in prompt:
            raise RuntimeError("boom")
        return _router(prompt)

    fake_ask.responses[FoundList] = flaky
    d = discover(SPEC, HYPS, progress=lambda _: None)
    assert d.candidates
    assert len(d.errors) == 1 and "marketplaces" in d.errors[0]
