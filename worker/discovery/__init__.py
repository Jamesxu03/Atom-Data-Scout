"""Discovery: search channels in parallel, check sources, merge and dedupe by domain.

Two phases, so snowballing only starts from companies the relevance screen confirmed:
  discover()  — (a) product search, (c) directories, (d) marketplaces
  snowball()  — (b) "alternatives to X" from explicit seeds + screened "yes" companies

Run: uv run python -m worker.discovery [run_dir]   (needs spec.json + hypotheses.json; phase 1 only)
"""

from collections.abc import Callable

from pydantic import BaseModel

from worker.config import DISCOVERY, PARALLEL_FETCHES, PARALLEL_LLM_CALLS
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
    hypothesis: str | None = None
    seed: str | None = None


class Discovery(BaseModel):
    hits: list[Hit]
    candidates: list[Candidate]
    errors: list[str] = []
    partial: bool = False  # quota ran out before every channel call finished


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
            keep = old[domain].model_dump(include={"relevance", "relevance_reason", "contacts"}) if domain in old else {}
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


def _check_sources(hits: list[Hit]) -> None:
    """Resolve redirects and confirm each source page lists the company (in place, parallel)."""
    batch = run_parallel(
        [(str(i), lambda h=h: channels.verify_source(Found(**h.model_dump(include={"name", "website", "source_url"}))))
         for i, h in enumerate(hits)],
        workers=PARALLEL_FETCHES,
        progress=lambda _: None,
    )
    for label, (url, ok) in batch.results:
        hits[int(label)].source_url, hits[int(label)].source_verified = url, ok


def _run_channels(tasks, progress: Progress) -> tuple[list[Hit], list[str], bool]:
    """tasks: (label, fn returning list[Found], Hit tags). Returns hits, errors, partial."""
    tags = {label: t for label, _, t in tasks}
    batch = run_parallel([(label, fn) for label, fn, _ in tasks], PARALLEL_LLM_CALLS, progress)
    hits = [Hit(**tags[label], **f.model_dump()) for label, found in batch.results for f in found]
    progress(f"Checking {len(hits)} source pages")
    _check_sources(hits)
    errors = batch.errors + ([f"stopped early, quota: {batch.quota_error}"] if batch.quota_error else [])
    return hits, errors, batch.quota_error is not None


def discover(spec: DataSpec, hypotheses: list[HolderHypothesis], progress: Progress = print) -> Discovery:
    """Phase 1: product search, directories and marketplaces, all calls in parallel."""
    tasks = [
        (f"(a) product search: {h.category}", lambda h=h: channels.product_search(h),
         {"channel": "product_search", "hypothesis": h.category})
        for h in hypotheses[: DISCOVERY["product_search_hypotheses"]]
    ] + [
        (f"(c) directories: {h.category}", lambda h=h: channels.directory(h),
         {"channel": "directory", "hypothesis": h.category})
        for h in hypotheses[: DISCOVERY["directory_hypotheses"]]
    ] + [
        (f"(d) data marketplaces #{i + 1}", lambda: channels.marketplace(spec), {"channel": "marketplace"})
        for i in range(DISCOVERY["marketplace_calls"])
    ]
    hits, errors, partial = _run_channels(tasks, progress)
    return Discovery(hits=hits, candidates=merge(hits), errors=errors, partial=partial)


def pick_seeds(candidates: list[Candidate], explicit: list[tuple[str, str]] = ()) -> list[tuple[str, str]]:
    """Explicit seeds first, then screened "yes" companies, best-corroborated first."""
    yes = sorted(
        (c for c in candidates if c.relevance == "yes"),
        key=lambda c: (len(c.channels), len(c.hypotheses), len(c.source_urls), c.source_verified),
        reverse=True,
    )
    seen = {normalize_domain(w) for _, w in explicit}
    auto = [(c.name, c.website) for c in yes if c.domain not in seen]
    return (list(explicit) + auto)[: DISCOVERY["snowball_seeds"]]


def snowball(d: Discovery, seeds: list[tuple[str, str]], progress: Progress = print) -> Discovery:
    """Phase 2: competitors of the seeds. Returns a new Discovery with everything merged."""
    tasks = [
        (f"(b) alternatives to {name}", lambda n=name, w=website: channels.snowball(n, w),
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
    print(f"\n{len(d.hits)} hits -> {len(d.candidates)} unique domains")
    for c in d.candidates:
        print(f"  {c.domain:30} {'✓' if c.source_verified else '?'} {','.join(c.channels)}")
    for e in d.errors:
        print("ERROR", e)
    print("saved", runs.save(run_dir, "discovery", d))
