"""Discovery rounds, local markets and suppliers per tool (WS1 to WS3), with fake Gemini."""

from tests.test_discovery import HYPS, QUIET, _router
from tests.test_hypotheses import BRIEF, brief_with
from worker.brief_template import READY, UNTAPPED, Pick
from worker.config import DISCOVERY, LOCAL_CALLS, ROUND_CALLS
from worker.discovery import (
    Discovery,
    Hit,
    discover,
    final_tasks,
    finish_round,
    merge,
    next_calls,
    round_one_calls,
    run_round,
    run_tasks,
)
from worker.discovery.channels import is_tool
from worker.discovery.rounds import allocate, uncovered_archetypes, uncovered_markets
from worker.markets import markets
from worker.schemas import (
    Found,
    FoundList,
    HolderHypothesis,
    LocalHooks,
    Market,
    MarketPlan,
    Scores,
)

GERMANY = Market(country="Germany", language="German", tld=".de", brief_language="German")
LOCAL_HYPS = [HolderHypothesis(category=f"Lokal {i}", rationale="r", search_queries=[f"anfrage {i}"])
              for i in range(3)]
PLAN = MarketPlan(market=GERMANY, hooks=LocalHooks(
    hypotheses=[h.model_copy(update={"market": GERMANY.label}) for h in LOCAL_HYPS], site_filter="site:.de",
    tender_portal="TED", retention_law="", associations=["BITKOM"], tools=["Gong", "Verint"]))


def _local_router(prompt: str) -> FoundList:
    if "Search Germany only, in German" in prompt:
        return FoundList(companies=[Found(name="Lokal GmbH", website="https://lokal.de", source_url="https://lokal.de/x")])
    return _router(prompt)


def test_markets_from_the_briefs_languages_and_regions():
    def labels(**picks):
        return [m.label for m in markets(brief_with(**{"languages": None, **picks}))]

    assert labels() == []  # neither named: English only, as before
    assert labels(languages=Pick(values=["English"])) == []  # every channel already searches in English
    assert labels(region=Pick(values=["UK"])) == ["United Kingdom (English)"]  # its domain and tender portal
    gulf = labels(languages=Pick(values=["Gulf Arabic"], importance="must"), region=Pick(values=["Middle East (GCC)"]))
    assert gulf == ["Saudi Arabia (Arabic)", "United Arab Emirates (Arabic)"]
    assert labels(languages=Pick(values=["German"])) == ["Germany (German)", "Austria (German)",
                                                         "Switzerland (German)"]
    # free text: a country as a region, and languages spoken there; capped at LOCAL_MARKETS
    india = labels(languages=Pick(values=["Hindi", "Tamil", "Bengali", "Marathi"], importance="must"),
                   region=Pick(values=["india"]))
    assert india == ["India (Hindi)", "India (Tamil)", "India (Bengali)"]
    assert labels(languages=Pick(values=["asia language"])) == []  # not a language we can place


def test_round_one_adds_native_searches_per_market(fake_ask):
    fake_ask.responses[FoundList] = _local_router
    d = discover(BRIEF, HYPS, [PLAN], **QUIET)

    local = [c.prompt for c in fake_ask.calls if "Search Germany only, in German" in c.prompt]
    assert len(local) == sum(LOCAL_CALLS.values())
    assert all("site:.de" in p and "BITKOM" in p for p in local)
    assert sum("- anfrage" in p for p in local) == LOCAL_CALLS["product_search"]  # the native queries
    assert sum('"Gong customer story"' in p and "Gong and Verint" in p for p in local) == 1  # both local tools
    assert sum("TED, the national tender portal" in p for p in local) == 1
    lokal = next(c for c in d.candidates if c.domain == "lokal.de")
    assert lokal.markets == ["Germany (German)"] and lokal.first_round == 1
    assert set(lokal.channels) == {"product_search", "suppliers", "records"}
    assert d.round(1).calls == len(fake_ask.calls) and d.round(1).queries == len(fake_ask.calls)
    assert d.queries["product_search|Germany (German)"]  # the searches Gemini ran there


def test_round_one_calls_follow_the_source_types():
    untapped_only = round_one_calls(brief_with(sources=Pick(values=[UNTAPPED])), [PLAN])
    assert "marketplace" not in untapped_only and untapped_only["suppliers|Germany (German)"] == 1
    ready_only = round_one_calls(brief_with(sources=Pick(values=[READY])), [PLAN])
    assert set(ready_only) == {"marketplace", "public_source"}  # local markets search for holders


def test_suppliers_drop_the_tool_itself():
    gong = Found(name="Gong", website="https://www.gong.io", source_url="s")
    customer = Found(name="Acme Insurance", website="https://acme-insurance.com", source_url="s")
    vendor_found = Found(name="Jiminny", website="https://jiminny.com", source_url="s")
    assert is_tool(gong, ["Gong"]) and not is_tool(customer, ["Gong"])
    assert is_tool(vendor_found, ["Jiminny"], ["jiminny.com"])


def test_suppliers_tag_hits_with_the_tool(fake_ask):
    def router(prompt):
        if "logo walls" in prompt:
            tool = prompt.split('"')[1].removesuffix(" customer story")
            return FoundList(companies=[
                Found(name=tool, website=f"https://{tool.lower()}.com", source_url="s"),  # the tool: dropped
                Found(name=f"Customer of {tool}", website=f"https://{tool.lower()}-customer.com", source_url="s")])
        return _router(prompt)

    fake_ask.responses[FoundList] = router
    d = discover(brief_with(sources=Pick(values=[UNTAPPED])), HYPS, **QUIET)
    domains = {c.domain: c for c in d.candidates}
    assert "gong.com" not in domains and domains["gong-customer.com"].tools == ["Gong"]
    assert d.used["suppliers"] and set(d.used["suppliers"]) == {"Gong", "NICE", "Verint", "Genesys"}


def _scored(d: Discovery, fits: dict[str, float], holder="untapped", vendor=()) -> None:
    for c in d.candidates:
        c.reason, c.holder_type, c.fit = "r", holder, fits.get(c.domain, 1.0)
        c.vendor = c.domain in vendor
        c.scores = Scores(data_match=5 if c.domain in vendor else 3)


def test_finish_round_counts_new_qualifying_companies_per_slot_and_side():
    hits = [Hit(channel="product_search", name="A", website="https://a.com", source_url="s"),
            Hit(channel="marketplace", name="A", website="https://a.com", source_url="s"),
            Hit(channel="signals", name="B", website="https://b.com", source_url="s"),
            Hit(channel="signals", name="V", website="https://v.com", source_url="s")]
    d = Discovery(hits=hits, candidates=merge(hits))
    _scored(d, {"a.com": 4.0, "b.com": 2.0, "v.com": 2.0}, vendor={"v.com"})
    finish_round(d, BRIEF, 1)
    stats = d.round(1)
    assert (stats.new, stats.qualifying) == (3, 1)
    # A is an untapped holder: it counts for product search, not for the ready-source channel
    assert stats.yield_by_slot == {"product_search": 1}


def test_allocate_keeps_each_sides_budget_on_its_side():
    calls = {"product_search": 5, "signals": 1, "suppliers": 4, "records": 1, "marketplace": 3, "public_source": 2}
    found = {"product_search": 3, "suppliers": 1, "marketplace": 2}
    out = allocate(calls, found)
    assert set(out) == {"product_search", "suppliers", "marketplace"}  # only slots that found something
    assert sum(n for s, n in out.items() if s != "marketplace") == ROUND_CALLS["untapped"]
    assert out["product_search"] > out["suppliers"] >= 1
    assert out["marketplace"] == ROUND_CALLS["ready"]
    assert allocate(calls, {"marketplace": 0}) == {}  # nothing found: no next round


def test_later_rounds_are_told_what_is_found_and_what_was_tried(fake_ask):
    fake_ask.responses[FoundList] = _router
    d = discover(BRIEF, HYPS, **QUIET)
    _scored(d, {"otter.ai": 4.5, "coquery 0.com": 3.0})
    fake_ask.calls.clear()

    d2 = run_round(BRIEF, HYPS, d, 2, {"product_search": 2, "suppliers": 1}, **QUIET)
    prompts = [c.prompt for c in fake_ask.calls]
    assert len(prompts) == 3
    assert all("Already found, do not return any of these: Otter (otter.ai)" in p for p in prompts)
    assert all("Queries already tried here: search" in p for p in prompts)
    product = [p for p in prompts if "- query" in p]
    # hypotheses round 1 didn't search come first, and good holders are shown as examples
    assert {p.split("- query ")[1][0] for p in product} == {"5", "0"}
    assert all("find more organisations like these:\n- Otter (otter.ai): r" in p for p in product)
    # tools continue from where round 1 stopped
    assert any('"Calabrio customer story"' in p or '"Five9 customer story"' in p for p in prompts)
    assert all(h.round == 2 for h in d2.hits[len(d.hits):])


def test_next_calls_add_searches_for_an_uncovered_must_have_language():
    d = Discovery(hits=[], candidates=[])
    d.round(1).calls_by_slot = {"product_search": 5}
    d.round(1).yield_by_slot = {"product_search": 4}
    must_german = brief_with(languages=Pick(values=["German"], importance="must"))
    assert uncovered_markets(d, must_german, [PLAN]) == [PLAN]
    calls = next_calls(d, must_german, 1, [PLAN])
    # a side gets at most what it spent this round (and ROUND_CALLS)
    assert calls == {"product_search": 5, "product_search|Germany (German)": 2}
    assert uncovered_markets(d, brief_with(languages=Pick(values=["German"])), [PLAN]) == []  # not a must-have


def test_uncovered_archetypes_are_the_ones_with_no_qualifying_holder():
    hits = [Hit(channel="product_search", name="A", website="https://a.com", source_url="s")]
    d = Discovery(hits=hits, candidates=merge(hits))
    _scored(d, {"a.com": 4.0})
    d.candidates[0].archetype = "service"
    keys = [a.key for a in uncovered_archetypes(d, BRIEF)]
    assert "service" not in keys and keys[0] == "kept_by_law" and len(keys) <= 4


def test_final_round_snowballs_and_searches_customers_of_vendors_found(fake_ask):
    fake_ask.responses[FoundList] = _router
    hits = [Hit(channel="directory", name="Jiminny", website="https://jiminny.com", source_url="s"),
            Hit(channel="product_search", name="Holder", website="https://holder.com", source_url="s",
                market="Germany (German)")]
    d = Discovery(hits=hits, candidates=merge(hits), used={"suppliers": ["Gong"]})
    _scored(d, {"holder.com": 4.0}, vendor={"jiminny.com"})
    tasks = final_tasks(d, BRIEF, HYPS, [("Holder", "https://holder.com")], [PLAN])
    assert [(t.channel, t.tool, t.market) for t in tasks] == [
        ("snowball", None, "Germany (German)"), ("suppliers", "Jiminny", None)]
    d2 = run_tasks(d, tasks, 2, **QUIET)
    snowball_prompt = next(c.prompt for c in fake_ask.calls if "organisations similar to Holder" in c.prompt)
    assert "Search Germany only, in German" in snowball_prompt  # the seed's market, in its language
    assert d2.used["suppliers"] == ["Gong", "Jiminny"]
    assert DISCOVERY["vendor_customer_calls"] >= 1
