"""Discovery: run all channels, merge and dedupe by domain.

Run: uv run python -m worker.discovery [run_dir]   (needs spec.json + hypotheses.json)
"""

from collections.abc import Callable

from pydantic import BaseModel

from worker.config import DISCOVERY
from worker.discovery import channels
from worker.discovery.domains import is_aggregator, normalize_domain
from worker.llm import QuotaExceeded
from worker.schemas import Candidate, Channel, DataSpec, Found, HolderHypothesis


class Hit(BaseModel):
    """One raw result from one channel, before merging."""

    channel: Channel
    name: str
    website: str
    source_url: str
    hypothesis: str | None = None
    seed: str | None = None


class Discovery(BaseModel):
    hits: list[Hit]
    candidates: list[Candidate]
    errors: list[str] = []


def merge(hits: list[Hit]) -> list[Candidate]:
    """Dedupe by domain, keeping every channel, source URL and hypothesis that found it."""
    by_domain: dict[str, Candidate] = {}
    for h in hits:
        domain = normalize_domain(h.website)
        if not domain or is_aggregator(domain):
            continue
        c = by_domain.get(domain)
        if c is None:
            c = by_domain[domain] = Candidate(
                name=h.name, website=h.website, domain=domain, channels=[], source_urls=[]
            )
        if h.channel not in c.channels:
            c.channels.append(h.channel)
        if h.source_url and h.source_url not in c.source_urls:
            c.source_urls.append(h.source_url)
        if h.hypothesis and h.hypothesis not in c.hypotheses:
            c.hypotheses.append(h.hypothesis)
    return list(by_domain.values())


def discover(
    spec: DataSpec,
    hypotheses: list[HolderHypothesis],
    seeds: list[tuple[str, str]] = (),
    progress: Callable[[str], None] = print,
) -> Discovery:
    """seeds: optional (name, website) pairs to snowball from, used before discovered ones."""
    hits: list[Hit] = []
    errors: list[str] = []

    def run(label: str, fn, *args, channel: Channel, hypothesis=None, seed=None) -> None:
        progress(label)
        try:
            found: list[Found] = fn(*args)
        except QuotaExceeded:
            raise  # every later call would fail too
        except Exception as e:  # one failed channel call shouldn't sink the run
            errors.append(f"{label}: {type(e).__name__}: {str(e)[:200]}")
            return
        hits.extend(
            Hit(channel=channel, hypothesis=hypothesis, seed=seed, **f.model_dump()) for f in found
        )

    for h in hypotheses[: DISCOVERY["product_search_hypotheses"]]:
        run(f"(a) product search: {h.category}", channels.product_search, h,
            channel="product_search", hypothesis=h.category)

    # Snowball from given seeds first, then from the companies product search found
    seed_list = list(seeds) + [(c.name, c.website) for c in merge(hits)]
    for name, website in seed_list[: DISCOVERY["snowball_seeds"]]:
        run(f"(b) alternatives to {name}", channels.snowball, name, website,
            channel="snowball", seed=name)

    for h in hypotheses[: DISCOVERY["directory_hypotheses"]]:
        run(f"(c) directories: {h.category}", channels.directory, h,
            channel="directory", hypothesis=h.category)

    for _ in range(DISCOVERY["marketplace_calls"]):
        run("(d) data marketplaces", channels.marketplace, spec, channel="marketplace")

    return Discovery(hits=hits, candidates=merge(hits), errors=errors)


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
        print(f"  {c.domain:30} {','.join(c.channels)}")
    for e in d.errors:
        print("ERROR", e)
    print("saved", runs.save(run_dir, "discovery", d))
