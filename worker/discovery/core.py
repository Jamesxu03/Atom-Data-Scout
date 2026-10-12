"""Discovery's data and plumbing: hits, merging them into candidates by domain, source checks,
and running channel calls in parallel."""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel

from worker.config import PARALLEL_FETCHES, PARALLEL_LLM_CALLS
from worker.discovery import channels
from worker.discovery.domains import is_aggregator, is_excluded, normalize_domain
from worker.parallel import run_parallel
from worker.schemas import Candidate, Channel, Found, RoundStats

Progress = Callable[[str], None]


class Hit(BaseModel):
    """One raw result from one channel, before merging."""

    channel: Channel
    name: str
    website: str
    source_url: str
    source_verified: bool = False  # source page confirmed to list this company
    source_checked: bool = False  # check_sources has looked at this hit
    hypothesis: str | None = None
    seed: str | None = None  # snowball: the company it is similar to
    tool: str | None = None  # suppliers: the tool whose customer stories name it
    market: str | None = None  # local channels: the market searched, e.g. "Germany (German)"
    round: int = 1


class Discovery(BaseModel):
    hits: list[Hit]
    candidates: list[Candidate]
    exclusions: list[str] = []  # the brief's, kept so every later merge skips them too
    errors: list[str] = []
    partial: bool = False  # quota ran out before every channel call finished
    rounds: list[RoundStats] = []
    queries: dict[str, list[str]] = {}  # slot -> Google searches Gemini ran there
    used: dict[str, list[str]] = {}  # slot -> hypotheses, angles or tools already searched there

    def round(self, r: int) -> RoundStats:
        """Round r's stats, added if new."""
        found = next((s for s in self.rounds if s.round == r), None)
        if found is None:
            found = RoundStats(round=r)
            self.rounds.append(found)
        return found


# Built from the hits on every merge; every other field is carried over from `previous`
BUILT_BY_MERGE = {"name", "website", "domain", "channels", "source_urls", "hypotheses", "source_verified",
                  "first_round", "markets", "tools"}


def merge(hits: list[Hit], previous: list[Candidate] = (), exclusions: list[str] = ()) -> list[Candidate]:
    """Dedupe by domain, keeping every channel, source URL, hypothesis, market and tool that
    found it, and the first round that did.

    Everything else on `previous` candidates (screen, scores, research, contacts, email) is
    carried over. Aggregators and excluded domains are skipped.
    """
    old = {c.domain: c for c in previous}
    by_domain: dict[str, Candidate] = {}
    for h in hits:
        domain = normalize_domain(h.website)
        if not domain or is_aggregator(domain) or is_excluded(domain, exclusions):
            continue
        c = by_domain.get(domain)
        if c is None:
            keep = old[domain].model_dump(exclude=BUILT_BY_MERGE) if domain in old else {}
            c = by_domain[domain] = Candidate(
                name=h.name, website=h.website, domain=domain, channels=[], source_urls=[], first_round=h.round,
                **keep,
            )
        if h.channel not in c.channels:
            c.channels.append(h.channel)
        if h.source_url and h.source_url not in c.source_urls:
            c.source_urls.append(h.source_url)
        for value, field in ((h.hypothesis, c.hypotheses), (h.market, c.markets), (h.tool, c.tools)):
            if value and value not in field:
                field.append(value)
        c.source_verified = c.source_verified or h.source_verified
        c.first_round = min(c.first_round or h.round, h.round)
    return list(by_domain.values())


def check_sources(d: Discovery, domains: set[str], progress: Progress = print) -> Discovery:
    """Resolve redirects and confirm source pages list the company, for hits on `domains`.

    Each hit is checked once; call again after a later round and only the new hits are fetched.
    """
    todo = [h for h in d.hits if not h.source_checked and normalize_domain(h.website) in domains]
    urls = list(dict.fromkeys(h.source_url for h in todo))  # a directory page lists many companies
    if urls:
        progress(f"Checking {len(urls)} source pages for {len(todo)} results")
        batch = run_parallel(
            [(url, lambda url=url: channels.check_source_page(url)) for url in urls],
            workers=PARALLEL_FETCHES,
            progress=lambda _: None,
        )
        pages = dict(batch.results)
        for h in todo:
            if h.source_url in pages:
                found = Found(**h.model_dump(include={"name", "website", "source_url"}))
                h.source_url, h.source_verified = channels.verify_source(found, pages[h.source_url])
            h.source_checked = True
    return d.model_copy(update={"candidates": merge(d.hits, d.candidates, d.exclusions)})


def slot(channel: str, market: str | None = None) -> str:
    """Where a call spends budget: a channel, or a channel in one local market."""
    return f"{channel}|{market}" if market else channel


@dataclass
class Task:
    """One grounded channel call. `run` gets a list to record the searches Gemini ran in."""

    channel: Channel
    label: str
    run: Callable[[list[str]], list[Found]]
    market: str | None = None
    hypothesis: str | None = None
    tool: str | None = None
    seed: str | None = None
    item: str | None = None  # what it used up (hypothesis, angle, tool), so later rounds try others

    @property
    def slot(self) -> str:
        return slot(self.channel, self.market)


def run_tasks(d: Discovery, tasks: list[Task], round_no: int, progress: Progress = print) -> Discovery:
    """Run the calls in parallel and merge what they found into d (a new Discovery). A failed
    call is recorded in errors; if the quota runs out, the rest are skipped and d is partial."""
    found_queries: list[list[str]] = [[] for _ in tasks]
    labels, seen = [], Counter()
    for t in tasks:  # unique, since errors are reported by label
        seen[t.label] += 1
        labels.append(t.label if seen[t.label] == 1 else f"{t.label} ({seen[t.label]})")
    batch = run_parallel(
        [(label, lambda i=i, t=t: (i, t.run(found_queries[i]))) for i, (t, label) in enumerate(zip(tasks, labels))],
        PARALLEL_LLM_CALLS, progress,
    )
    hits = [
        Hit(channel=tasks[i].channel, hypothesis=tasks[i].hypothesis, seed=tasks[i].seed, tool=tasks[i].tool,
            market=tasks[i].market, round=round_no, **f.model_dump())
        for _, (i, found) in batch.results for f in found
    ]
    ran = {i for _, (i, _) in batch.results} | {i for i, label in enumerate(labels) if any(
        e.startswith(f"{label}: ") for e in batch.errors)}
    errors = batch.errors + ([f"stopped early, quota: {batch.quota_error}"] if batch.quota_error else [])
    out = d.model_copy(deep=True)
    stats = out.round(round_no)
    for i in sorted(ran):
        t = tasks[i]
        stats.calls += 1
        stats.calls_by_slot[t.slot] = stats.calls_by_slot.get(t.slot, 0) + 1
        stats.queries += len(found_queries[i])
        out.queries.setdefault(t.slot, []).extend(found_queries[i])
        if t.item is not None:
            out.used.setdefault(t.slot, []).append(t.item)
    stats.hits += len(hits)
    all_hits = out.hits + hits
    return out.model_copy(update={
        "hits": all_hits,
        "candidates": merge(all_hits, out.candidates, out.exclusions),
        "errors": out.errors + errors,
        "partial": out.partial or batch.quota_error is not None,
    })
