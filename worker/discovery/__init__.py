"""Discovery: search channels in parallel, check sources, merge and dedupe by domain.

Two phases, so snowballing only starts from companies the screen scored well:
  discover()       — (a) product search, (c) directories, (d) data vendors (marketplaces,
                     collection services, published datasets), (e) public sources (open
                     corpora, public bodies that publish recordings), (f) distressed or
                     insolvent companies. With source types (E1) picked, only their channels run.
  snowball()       — (b) "alternatives to X" from explicit seeds + well-scored by-product
                     holders, limited to competitors working in the buyer's languages/regions
  check_sources()  — confirm source pages before screening, which reads them too

Domains in the brief's exclusions never become candidates.
"""

from collections.abc import Callable

from pydantic import BaseModel

from worker.brief_template import (
    BY_PRODUCT,
    DISTRESSED,
    MARKETPLACES,
    PUBLIC_BODIES,
    RESEARCH_INSTITUTES,
    TECHNOLOGY,
    VENDORS,
    Brief,
)
from worker.config import (
    DISCOVERY,
    PARALLEL_FETCHES,
    PARALLEL_LLM_CALLS,
    SEED_FIT,
    SNOWBALL_SEED_TYPES,
)
from worker.discovery import channels
from worker.discovery.domains import is_aggregator, is_excluded, normalize_domain
from worker.parallel import run_parallel
from worker.schemas import Candidate, Channel, Found, HolderHypothesis

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
    seed: str | None = None


class Discovery(BaseModel):
    hits: list[Hit]
    candidates: list[Candidate]
    exclusions: list[str] = []  # the brief's, kept so every later merge skips them too
    errors: list[str] = []
    partial: bool = False  # quota ran out before every channel call finished


# Built from the hits on every merge; every other field is carried over from `previous`
BUILT_BY_MERGE = {"name", "website", "domain", "channels", "source_urls", "hypotheses", "source_verified"}


def merge(hits: list[Hit], previous: list[Candidate] = (), exclusions: list[str] = ()) -> list[Candidate]:
    """Dedupe by domain, keeping every channel, source URL and hypothesis that found it.

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
                name=h.name, website=h.website, domain=domain, channels=[], source_urls=[], **keep
            )
        if h.channel not in c.channels:
            c.channels.append(h.channel)
        if h.source_url and h.source_url not in c.source_urls:
            c.source_urls.append(h.source_url)
        if h.hypothesis and h.hypothesis not in c.hypotheses:
            c.hypotheses.append(h.hypothesis)
        c.source_verified = c.source_verified or h.source_verified
    return list(by_domain.values())


def check_sources(d: Discovery, domains: set[str], progress: Progress = print) -> Discovery:
    """Resolve redirects and confirm source pages list the company, for hits on `domains`.

    Each hit is checked once; call again after snowballing and only the new hits are fetched.
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


def _run_channels(tasks, progress: Progress) -> tuple[list[Hit], list[str], bool]:
    """tasks: (label, fn returning list[Found], Hit tags). Returns hits, errors, partial."""
    tags = {label: t for label, _, t in tasks}
    batch = run_parallel([(label, fn) for label, fn, _ in tasks], PARALLEL_LLM_CALLS, progress)
    hits = [Hit(**tags[label], **f.model_dump()) for label, found in batch.results for f in found]
    errors = batch.errors + ([f"stopped early, quota: {batch.quota_error}"] if batch.quota_error else [])
    return hits, errors, batch.quota_error is not None


# E1 source type -> the channel angles that look for it
ANGLE_SOURCES = {
    "marketplace listings": {MARKETPLACES},
    "collection services": {VENDORS},
    "published datasets": {VENDORS, RESEARCH_INSTITUTES},
    "open corpora": {RESEARCH_INSTITUTES, PUBLIC_BODIES},
    "public recordings": {PUBLIC_BODIES},
}


def discover(brief: Brief, hypotheses: list[HolderHypothesis], progress: Progress = print) -> Discovery:
    """Phase 1: every channel for the source types the brief wants (all, if none picked), all
    calls in parallel."""
    wanted = set(brief.active("sources"))

    def looks_for(*sources: str) -> bool:
        return not wanted or bool(wanted & set(sources))

    def angles(names, budget):
        return [a for a in names if looks_for(*ANGLE_SOURCES[a])][:budget]

    products = looks_for(BY_PRODUCT, TECHNOLOGY)
    tasks = [
        (f"(a) product search: {h.category}", lambda h=h: channels.product_search(h),
         {"channel": "product_search", "hypothesis": h.category})
        for h in (hypotheses[: DISCOVERY["product_search_hypotheses"]] if products else [])
    ] + [
        (f"(c) directories: {h.category}", lambda h=h: channels.directory(h),
         {"channel": "directory", "hypothesis": h.category})
        for h in (hypotheses[: DISCOVERY["directory_hypotheses"]] if products else [])
    ] + [
        (f"(d) data vendors: {angle}", lambda a=angle: channels.marketplace(brief, a), {"channel": "marketplace"})
        for angle in angles(channels.MARKETPLACE_ANGLES, DISCOVERY["marketplace_calls"])
    ] + [
        (f"(e) public sources: {angle}", lambda a=angle: channels.public_source(brief, a),
         {"channel": "public_source"})
        for angle in angles(channels.PUBLIC_ANGLES, DISCOVERY["public_source_calls"])
    ] + [
        ("(f) distressed companies", lambda: channels.distressed(brief, hypotheses), {"channel": "distressed"})
        for _ in range(DISCOVERY["distressed_calls"] if looks_for(DISTRESSED) else 0)
    ]
    exclusions = brief.active("exclusions")
    hits, errors, partial = _run_channels(tasks, progress)
    return Discovery(hits=hits, candidates=merge(hits, exclusions=exclusions),
                     exclusions=exclusions, errors=errors, partial=partial)


def _good_seed(c: Candidate) -> bool:
    """A well-scored by-product holder with evidence for the brief's languages or regions
    (coverage None: the brief names neither)."""
    return (
        c.holder_type in SNOWBALL_SEED_TYPES and not c.excluded and (c.fit or 0) >= SEED_FIT
        and (c.scores.coverage is None or c.scores.coverage >= 3)
    )


def pick_seeds(candidates: list[Candidate], explicit: list[tuple[str, str]] = ()) -> list[tuple[str, str]]:
    """Explicit seeds first, then good seeds by fit, best-corroborated first on ties."""
    good = sorted(
        (c for c in candidates if _good_seed(c)),
        key=lambda c: (c.fit, c.corroboration()), reverse=True,
    )
    seen = {normalize_domain(w) for _, w in explicit}
    auto = [(c.name, c.website) for c in good if c.domain not in seen]
    return (list(explicit) + auto)[: DISCOVERY["snowball_seeds"]]


def snowball(
    d: Discovery, seeds: list[tuple[str, str]], languages: list[str] = (), regions: list[str] = (),
    progress: Progress = print,
) -> Discovery:
    """Phase 2: competitors of the seeds that work in `languages` / `regions` (any, if empty).
    Returns a new Discovery with everything merged."""
    tasks = [
        (f"(b) alternatives to {name}",
         lambda n=name, w=website: channels.snowball(n, w, languages, regions),
         {"channel": "snowball", "seed": name})
        for name, website in seeds
    ]
    hits, errors, partial = _run_channels(tasks, progress)
    all_hits = d.hits + hits
    return d.model_copy(update={
        "hits": all_hits,
        "candidates": merge(all_hits, d.candidates, d.exclusions),
        "errors": d.errors + errors,
        "partial": d.partial or partial,
    })

