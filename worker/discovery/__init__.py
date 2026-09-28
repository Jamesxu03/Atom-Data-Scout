"""Discovery: search channels in parallel, check sources, merge and dedupe by domain.

Two phases, so snowballing only starts from companies the relevance screen confirmed:
  discover()       — (a) product search, (c) directories, (d) data vendors (marketplaces,
                     collection services, published datasets), (e) public sources (open
                     corpora, public bodies that publish recordings)
  snowball()       — (b) "alternatives to X" from explicit seeds + screened "yes" by-product
                     holders, limited to competitors working in the buyer's languages
  check_sources()  — confirm source pages before screening, which reads them too

Run: uv run python -m worker.discovery [run_dir]   (needs spec.json + hypotheses.json; phase 1 only)
"""

from collections.abc import Callable

from pydantic import BaseModel

from worker.config import DISCOVERY, PARALLEL_FETCHES, PARALLEL_LLM_CALLS, SNOWBALL_SEED_TYPES
from worker.discovery import channels
from worker.discovery.domains import is_aggregator, normalize_domain
from worker.parallel import run_parallel
from worker.schemas import Candidate, Channel, DataSpec, Found, HolderHypothesis

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
    errors: list[str] = []
    partial: bool = False  # quota ran out before every channel call finished


SCREENED_FIELDS = {"relevance", "relevance_reason", "holder_type", "data_owner", "languages", "contacts"}


def merge(hits: list[Hit], previous: list[Candidate] = ()) -> list[Candidate]:
    """Dedupe by domain, keeping every channel, source URL and hypothesis that found it.

    Screening results and contacts already on `previous` candidates are carried over.
    """
    old = {c.domain: c for c in previous}
    by_domain: dict[str, Candidate] = {}
    for h in hits:
        domain = normalize_domain(h.website)
        if not domain or is_aggregator(domain):
            continue
        c = by_domain.get(domain)
        if c is None:
            keep = old[domain].model_dump(include=SCREENED_FIELDS) if domain in old else {}
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
    return d.model_copy(update={"candidates": merge(d.hits, previous=d.candidates)})


def _run_channels(tasks, progress: Progress) -> tuple[list[Hit], list[str], bool]:
    """tasks: (label, fn returning list[Found], Hit tags). Returns hits, errors, partial."""
    tags = {label: t for label, _, t in tasks}
    batch = run_parallel([(label, fn) for label, fn, _ in tasks], PARALLEL_LLM_CALLS, progress)
    hits = [Hit(**tags[label], **f.model_dump()) for label, found in batch.results for f in found]
    errors = batch.errors + ([f"stopped early, quota: {batch.quota_error}"] if batch.quota_error else [])
    return hits, errors, batch.quota_error is not None


def discover(spec: DataSpec, hypotheses: list[HolderHypothesis], progress: Progress = print) -> Discovery:
    """Phase 1: product search, directories, data vendors and public sources, all calls in parallel."""
    tasks = [
        (f"(a) product search: {h.category}", lambda h=h: channels.product_search(h),
         {"channel": "product_search", "hypothesis": h.category})
        for h in hypotheses[: DISCOVERY["product_search_hypotheses"]]
    ] + [
        (f"(c) directories: {h.category}", lambda h=h: channels.directory(h),
         {"channel": "directory", "hypothesis": h.category})
        for h in hypotheses[: DISCOVERY["directory_hypotheses"]]
    ] + [
        (f"(d) data vendors: {angle}", lambda a=angle: channels.marketplace(spec, a), {"channel": "marketplace"})
        for angle in list(channels.MARKETPLACE_ANGLES)[: DISCOVERY["marketplace_calls"]]
    ] + [
        (f"(e) public sources: {angle}", lambda a=angle: channels.public_source(spec, a),
         {"channel": "public_source"})
        for angle in list(channels.PUBLIC_ANGLES)[: DISCOVERY["public_source_calls"]]
    ]
    hits, errors, partial = _run_channels(tasks, progress)
    return Discovery(hits=hits, candidates=merge(hits), errors=errors, partial=partial)


def pick_seeds(candidates: list[Candidate], explicit: list[tuple[str, str]] = ()) -> list[tuple[str, str]]:
    """Explicit seeds first, then screened "yes" companies of SNOWBALL_SEED_TYPES,
    best-corroborated first. "yes" already implies evidence for the buyer's languages."""
    yes = sorted(
        (c for c in candidates if c.relevance == "yes" and c.holder_type in SNOWBALL_SEED_TYPES),
        key=Candidate.corroboration, reverse=True,
    )
    seen = {normalize_domain(w) for _, w in explicit}
    auto = [(c.name, c.website) for c in yes if c.domain not in seen]
    return (list(explicit) + auto)[: DISCOVERY["snowball_seeds"]]


def snowball(
    d: Discovery, seeds: list[tuple[str, str]], languages: list[str] = (), progress: Progress = print
) -> Discovery:
    """Phase 2: competitors of the seeds that work in `languages` (any, if empty).
    Returns a new Discovery with everything merged."""
    tasks = [
        (f"(b) alternatives to {name}", lambda n=name, w=website: channels.snowball(n, w, languages),
         {"channel": "snowball", "seed": name})
        for name, website in seeds
    ]
    hits, errors, partial = _run_channels(tasks, progress)
    all_hits = d.hits + hits
    return Discovery(
        hits=all_hits,
        candidates=merge(all_hits, previous=d.candidates),
        errors=d.errors + errors,
        partial=d.partial or partial,
    )


if __name__ == "__main__":
    import sys
    from pathlib import Path

    from worker import runs
    from worker.steps.hypotheses import HypothesisList

    run_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else runs.latest_run()
    spec = runs.load(run_dir, "spec", DataSpec)
    hyps = runs.load(run_dir, "hypotheses", HypothesisList).hypotheses
    d = discover(spec, hyps)
    d = check_sources(d, {c.domain for c in d.candidates})
    print(f"\n{len(d.hits)} hits -> {len(d.candidates)} unique domains")
    for c in d.candidates:
        print(f"  {c.domain:30} {'✓' if c.source_verified else '?'} {','.join(c.channels)}")
    for e in d.errors:
        print("ERROR", e)
    print("saved", runs.save(run_dir, "discovery", d))
