"""Discovery rounds (WS3): what each round searches, and whether another one is worth it.

Calls are spent in slots: a channel ("product_search"), or a channel in one local market
("product_search|Germany (German)"). Each slot is on one side: marketplace and public sources
find ready sources; every other channel looks for untapped holders.

  round 1     every channel for the source types the brief wants, at its DISCOVERY budget,
              plus LOCAL_CALLS in each local market (WS2)
  rounds 2–3  only slots that found a new qualifying company in the round before. Each side's
              budget (its calls last round, at most ROUND_CALLS) is shared among them by what
              they found, so holder budget never flows to the ready channels or back. Every
              call is told the companies already found and the queries already tried, and
              product search gets the best organisation holders to find more like
  final       snowball from the best organisation holders, and the customers of tool vendors
              the screen found (pipeline.search() runs it once, after the last round)

A company qualifies when it is a fit (RELEVANT_FIT), meets every must-have and isn't a tool
vendor. A round adding fewer than ROUND_MIN_NEW stops the search.
"""

from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from worker import archetypes
from worker.brief_template import COLLECTION_OK, READY, UNTAPPED, Brief
from worker.config import (
    DISCOVERY,
    EXCLUDED_LIST_MAX,
    FILL_ARCHETYPES,
    LIKE_THESE,
    LOCAL_CALLS,
    RELEVANT_FIT,
    ROUND_CALLS,
    SEED_FIT,
    SNOWBALL_SEED_TYPES,
    UNCOVERED_MARKET_CALLS,
)
from worker.discovery import channels
from worker.discovery.channels import Context
from worker.discovery.core import Discovery, Progress, Task, run_tasks, slot
from worker.discovery.domains import normalize_domain
from worker.schemas import Candidate, HolderHypothesis, MarketPlan
from worker.scoring import must_failures

READY_CHANNELS = {"marketplace", "public_source"}
TRIED_SHOWN = 20  # most recent queries listed per slot


def side_of(s: str) -> str:
    return "ready" if s.split("|")[0] in READY_CHANNELS else "untapped"


def qualifies(c: Candidate, brief: Brief) -> bool:
    """A fit that meets every must-have and isn't a tool vendor."""
    return (not c.excluded and not c.killed and not c.vendor and c.holder_type in ("ready", "untapped")
            and c.fit is not None and c.fit >= RELEVANT_FIT and not must_failures(c, brief))


def finish_round(d: Discovery, brief: Brief, r: int) -> None:
    """Round r's new and qualifying companies, and what each slot found, once it is screened
    (in place). A company counts for a slot on its own side only."""
    stats = d.round(r)
    new = [c for c in d.candidates if c.first_round == r]
    good = [c for c in new if qualifies(c, brief)]
    slots: dict[str, set[str]] = defaultdict(set)
    for h in d.hits:
        if h.round == r:
            slots[normalize_domain(h.website) or ""].add(slot(h.channel, h.market))
    found = Counter(s for c in good for s in slots[c.domain] if side_of(s) == c.holder_type)
    stats.new, stats.qualifying, stats.yield_by_slot = len(new), len(good), dict(found)


def allocate(calls_by_slot: dict[str, int], yield_by_slot: dict[str, int]) -> dict[str, int]:
    """Next round's calls per slot: only slots that found something, each side's budget (its
    calls this round, at most ROUND_CALLS) shared in proportion to what each found."""
    out: dict[str, int] = {}
    for side, cap in ROUND_CALLS.items():
        found = {s: y for s, y in yield_by_slot.items() if y > 0 and side_of(s) == side}
        budget = min(cap, sum(n for s, n in calls_by_slot.items() if side_of(s) == side))
        ranked = sorted(found, key=lambda s: -found[s])[:budget]
        if not ranked:
            continue
        total = sum(found[s] for s in ranked)
        share = {s: budget * found[s] / total for s in ranked}
        alloc = {s: max(1, int(share[s])) for s in ranked}
        for s in sorted(ranked, key=lambda s: share[s] - int(share[s]), reverse=True):  # largest remainder
            if sum(alloc.values()) >= budget:
                break
            alloc[s] += 1
        while sum(alloc.values()) > budget:  # the minimum of one each overshot
            biggest = max(alloc, key=alloc.get)
            alloc[biggest] -= 1
        out.update({s: n for s, n in alloc.items() if n})
    return out


def round_one_calls(brief: Brief, plans: list[MarketPlan] = ()) -> dict[str, int]:
    """Round 1's calls per slot, for the source types the brief wants (all, if none picked)."""
    wanted = set(brief.active("sources"))
    ready, untapped = (not wanted or source in wanted for source in (READY, UNTAPPED))
    calls = {}
    if untapped:
        calls |= {"product_search": DISCOVERY["product_search_hypotheses"],
                  "directory": DISCOVERY["directory_hypotheses"],
                  "distressed": min(DISCOVERY["distressed_calls"], len(channels.DISTRESSED_ANGLES)),
                  **{ch: DISCOVERY[f"{ch}_calls"] for ch in ("signals", "suppliers", "records")}}
        for p in plans:
            calls |= {slot(ch, p.market.label): n for ch, n in LOCAL_CALLS.items()
                      if p.hooks and (ch != "suppliers" or p.hooks.tools)}
    if ready:
        calls |= {"marketplace": min(DISCOVERY["marketplace_calls"], len(channels.MARKETPLACE_ANGLES)),
                  "public_source": min(DISCOVERY["public_source_calls"], len(channels.PUBLIC_ANGLES))}
    return {s: n for s, n in calls.items() if n}


def _next[T](items: list[T], key: Callable[[T], str], used: list[str], n: int) -> list[T]:
    """n items, the ones not used yet first, then again from the start."""
    if not items:
        return []
    fresh = [x for x in items if key(x) not in used]
    rest = [x for x in items if key(x) in used]
    order = fresh + rest
    return [order[i % len(order)] for i in range(n)]


def vendors(d: Discovery) -> list[Candidate]:
    """Tool vendors the screen found, best data match first: their customers hold the data."""
    return sorted((c for c in d.candidates if c.vendor and not c.excluded and not c.killed),
                  key=lambda c: -(c.scores.data_match or 0))


@dataclass
class Plan:
    """Everything a round's calls draw on."""

    brief: Brief
    hypotheses: list[HolderHypothesis]
    plans: list[MarketPlan]
    d: Discovery
    later: bool  # rounds 2 and 3: told what is already found

    def context(self, s: str, plan: MarketPlan | None) -> Context:
        if not self.later:
            return Context(market=plan and plan.market, hooks=plan and plan.hooks)
        ranked = sorted(self.d.candidates, key=lambda c: (c.fit is None, -(c.fit or 0)))
        examples = [c for c in ranked if c.holder_type == "untapped" and not c.vendor and not c.excluded
                    and not c.killed and (c.fit or 0) >= RELEVANT_FIT][:LIKE_THESE]
        return Context(
            market=plan and plan.market, hooks=plan and plan.hooks,
            excluded=tuple(f"{c.name} ({c.domain})" for c in ranked[:EXCLUDED_LIST_MAX]),
            tried=tuple(self.d.queries.get(s, [])[-TRIED_SHOWN:]),
            examples=tuple(f"{c.name} ({c.domain}): {c.reason}" for c in examples) if side_of(s) == "untapped" else (),
        )

    def holders(self, plan: MarketPlan | None) -> list[HolderHypothesis]:
        pool = plan.hooks.hypotheses if plan and plan.hooks else self.hypotheses
        return [h for h in pool if h.side == "organisation"] or pool

    def tasks(self, s: str, n: int) -> list[Task]:
        channel, _, label = s.partition("|")
        plan = next((p for p in self.plans if p.market.label == label), None) if label else None
        ctx, used, market = self.context(s, plan), self.d.used.get(s, []), label or None
        b, hyps = self.brief, self.hypotheses
        where = f" in {label}" if label else ""

        def task(text, run, **kw) -> Task:
            return Task(channel=channel, label=f"{text}{where}", run=run, market=market, **kw)

        if channel == "product_search":
            return [task(f"(a) product search: {h.category}", lambda q, h=h: channels.product_search(h, ctx, q),
                         hypothesis=h.category, item=h.category)
                    for h in _next(self.holders(plan), lambda h: h.category, used, n)]
        if channel == "directory":
            pool = [h for h in hyps if h.side == "vendor"] or hyps
            return [task(f"(c) directories: {h.category}", lambda q, h=h: channels.directory(h, ctx, q),
                         hypothesis=h.category, item=h.category)
                    for h in _next(pool, lambda h: h.category, used, n)]
        if channel == "marketplace":
            return [task(f"(d) data vendors: {a}", lambda q, a=a: channels.marketplace(b, a, ctx, q), item=a)
                    for a in _next(list(channels.MARKETPLACE_ANGLES), str, used, n)]
        if channel == "public_source":
            return [task(f"(e) public sources: {a}", lambda q, a=a: channels.public_source(b, a, ctx, q), item=a)
                    for a in _next(list(channels.PUBLIC_ANGLES), str, used, n)]
        if channel == "distressed":
            return [task(f"(f) {a}", lambda q, a=a: channels.distressed(b, hyps, a, ctx, q), item=a)
                    for a in _next(list(channels.DISTRESSED_ANGLES), str, used, n)]
        if channel in ("signals", "records"):
            fn = channels.signals if channel == "signals" else channels.records
            code = "g" if channel == "signals" else "i"
            # the first call looks across every kind of holder; later ones focus on one each
            focus: list[HolderHypothesis | None] = [None, *self.holders(plan)]
            return [task(f"({code}) {channel}" + (f": {h.category}" if h else ""),
                         lambda q, h=h: fn(b, hyps, h, ctx, q), item=h.category if h else "")
                    for h in _next(focus, lambda h: h.category if h else "", used, n)]
        if channel == "suppliers":
            if plan:  # one call on the tools most used in this market
                tools = plan.hooks.tools[:2] if plan.hooks else []
                return [task(f"(h) customers of {' and '.join(tools)}",
                             lambda q: channels.suppliers(b, tools, hyps, ctx, q), tool=" and ".join(tools),
                             item=" and ".join(tools))] if tools else []
            return self.supplier_tasks(n, ctx, used)
        raise ValueError(f"no tasks for slot {s}")

    def supplier_tasks(self, n: int, ctx: Context, used: list[str], found_only: bool = False) -> list[Task]:
        """One call per tool: vendors the screen found first, then the named tools for the
        brief's modality. Tools already searched are skipped."""
        found = [(c.name, [c.domain]) for c in vendors(self.d)]
        named = [] if found_only else [(t, []) for t in archetypes.supplier_tools(self.brief.chosen("modality"))]
        tools = [(t, doms) for t, doms in dict(found + named).items() if t not in used][:n]
        return [Task(channel="suppliers", label=f"(h) customers of {t}", tool=t, item=t,
                     run=lambda q, t=t, doms=doms: channels.suppliers(self.brief, [t], self.hypotheses, ctx, q, doms))
                for t, doms in tools]


def run_round(brief: Brief, hypotheses: list[HolderHypothesis], d: Discovery, r: int, calls: dict[str, int],
              plans: list[MarketPlan] = (), progress: Progress = print) -> Discovery:
    """Round r: the calls per slot given, all in parallel."""
    plan = Plan(brief, hypotheses, list(plans), d, later=r > 1)
    tasks = [t for s, n in calls.items() for t in plan.tasks(s, n)]
    return run_tasks(d, tasks, r, progress)


def discover(brief: Brief, hypotheses: list[HolderHypothesis], plans: list[MarketPlan] = (),
             progress: Progress = print) -> Discovery:
    """Round 1 on a new Discovery."""
    exclusions = brief.active("exclusions")
    d = Discovery(hits=[], candidates=[], exclusions=exclusions)
    return run_round(brief, hypotheses, d, 1, round_one_calls(brief, plans), plans, progress)


def next_calls(d: Discovery, brief: Brief, r: int, plans: list[MarketPlan] = ()) -> dict[str, int]:
    """Round r+1's calls: allocate() from round r, plus UNCOVERED_MARKET_CALLS for each local
    market whose must-have language nothing qualifying was found for (after round 1 only)."""
    stats = d.round(r)
    calls = allocate(stats.calls_by_slot, stats.yield_by_slot)
    if r == 1:
        for p in uncovered_markets(d, brief, plans):
            s = slot("product_search", p.market.label)
            calls[s] = calls.get(s, 0) + UNCOVERED_MARKET_CALLS
    return calls


def uncovered_markets(d: Discovery, brief: Brief, plans: list[MarketPlan]) -> list[MarketPlan]:
    """Local markets for a must-have language that no qualifying company shows evidence for."""
    if not brief.must("languages"):
        return []
    covered = {lang for c in d.candidates if qualifies(c, brief) for lang in c.languages}
    return [p for p in plans if p.hooks and p.market.brief_language and p.market.brief_language not in covered]


def uncovered_archetypes(d: Discovery, brief: Brief) -> list[archetypes.Archetype]:
    """Reasons to hold this data that no qualifying untapped holder has yet (at most FILL_ARCHETYPES)."""
    covered = {c.archetype for c in d.candidates if c.holder_type == "untapped" and qualifies(c, brief)}
    return [a for a in archetypes.with_examples(brief.chosen("modality")) if a.key not in covered][:FILL_ARCHETYPES]


def seed_types(brief: Brief) -> list[str]:
    """Untapped holders; collectors too when the buyer wants data recorded to order, since a
    collector's competitors are other collectors."""
    return SNOWBALL_SEED_TYPES + (["collector"] if COLLECTION_OK in brief.active("collection") else [])


def _seed_type(c: Candidate) -> str | None:
    """Tool vendors never seed: organisations similar to a vendor are more vendors."""
    return "vendor" if c.vendor else "collector" if c.collector else c.holder_type


def _good_seed(c: Candidate, types: list[str]) -> bool:
    """A well-scored holder of a seed type with evidence for the brief's languages or regions
    (coverage None: the brief names neither)."""
    return (
        _seed_type(c) in types and not c.excluded and (c.fit or 0) >= SEED_FIT
        and (c.scores.coverage is None or c.scores.coverage >= 3)
    )


def pick_seeds(
    candidates: list[Candidate], explicit: list[tuple[str, str]] = (), types: list[str] = SNOWBALL_SEED_TYPES
) -> list[tuple[str, str]]:
    """Explicit seeds first, then good seeds by fit, best-corroborated first on ties."""
    good = sorted(
        (c for c in candidates if _good_seed(c, types)),
        key=lambda c: (c.fit, c.corroboration()), reverse=True,
    )
    seen = {normalize_domain(w) for _, w in explicit}
    auto = [(c.name, c.website) for c in good if c.domain not in seen]
    return (list(explicit) + auto)[: DISCOVERY["snowball_seeds"]]


def final_tasks(d: Discovery, brief: Brief, hypotheses: list[HolderHypothesis], seeds: list[tuple[str, str]],
                plans: list[MarketPlan] = ()) -> list[Task]:
    """Snowball from the seeds (in a seed's local market and language, when a local search
    found it) and the customers of vendors the screen found that no round searched yet."""
    by_domain = {c.domain: c for c in d.candidates}
    by_label = {p.market.label: p for p in plans}
    tasks = []
    for name, website in seeds:
        c = by_domain.get(normalize_domain(website) or "")
        plan = next((by_label[m] for m in (c.markets if c else []) if m in by_label), None)
        ctx = Context(market=plan and plan.market, hooks=plan and plan.hooks)
        tasks.append(Task(channel="snowball", label=f"(b) organisations like {name}", seed=name,
                          market=plan and plan.market.label,
                          run=lambda q, n=name, w=website, ctx=ctx: channels.snowball(
                              n, w, brief.languages, brief.regions, ctx, q)))
    plan = Plan(brief, hypotheses, list(plans), d, later=False)
    tasks += plan.supplier_tasks(DISCOVERY["vendor_customer_calls"], Context(), d.used.get("suppliers", []),
                                 found_only=True)
    return tasks


def snowball(
    d: Discovery, seeds: list[tuple[str, str]], languages: list[str] = (), regions: list[str] = (),
    progress: Progress = print,
) -> Discovery:
    """Organisations similar to the seeds that work in `languages` / `regions` (any, if empty),
    as a round of its own after the last one."""
    r = max((s.round for s in d.rounds), default=0) + 1
    tasks = [Task(channel="snowball", label=f"(b) organisations like {name}", seed=name,
                  run=lambda q, n=name, w=website: channels.snowball(n, w, languages, regions, queries=q))
             for name, website in seeds]
    return run_tasks(d, tasks, r, progress)
