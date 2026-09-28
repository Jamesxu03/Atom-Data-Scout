"""What share of the companies in eval/seed_companies.csv did discovery find, and via which channel?

Run: uv run python -m eval.discovery_recall [run_dir]   (defaults to the latest run)

Matches by domain; seeds with no website are matched by name.
"""

import csv
import re
import sys
from collections import Counter
from pathlib import Path

from pydantic import BaseModel

from worker import runs
from worker.discovery import Discovery
from worker.discovery.domains import normalize_domain
from worker.schemas import Candidate

SEEDS_CSV = Path(__file__).parent / "seed_companies.csv"
CHANNELS = ["product_search", "snowball", "directory", "marketplace"]


class Seed(BaseModel):
    name: str
    website: str = ""
    notes: str = ""


class SeedMatch(BaseModel):
    seed: Seed
    candidate: Candidate | None


def load_seeds(path: Path = SEEDS_CSV) -> list[Seed]:
    with path.open(newline="", encoding="utf-8") as f:
        return [Seed(**{k: (v or "").strip() for k, v in row.items() if k}) for row in csv.DictReader(f)
                if (row.get("name") or "").strip()]


def _norm_name(name: str) -> str:
    name = re.sub(r"\b(ltd|limited|inc|plc|llc|gmbh|corp|co)\b\.?", "", name.lower())
    return re.sub(r"[^a-z0-9]", "", name)


def match(seeds: list[Seed], candidates: list[Candidate]) -> list[SeedMatch]:
    by_domain = {c.domain: c for c in candidates}
    by_name = {_norm_name(c.name): c for c in candidates}
    out = []
    for s in seeds:
        domain = normalize_domain(s.website)
        c = by_domain.get(domain) if domain else None
        if c is None:
            c = by_name.get(_norm_name(s.name))
        out.append(SeedMatch(seed=s, candidate=c))
    return out


def report(seeds: list[Seed], d: Discovery) -> str:
    if not seeds:
        return f"No seeds in {SEEDS_CSV} yet — add rows (name,website,notes) and re-run."
    matches = match(seeds, d.candidates)
    found = [m for m in matches if m.candidate]
    n = len(seeds)
    pct = lambda k: f"{k}/{n} ({100 * k / n:.0f}%)"

    lines = [f"Discovery recall: {pct(len(found))} of seed companies found", "", "By channel:"]
    for ch in CHANNELS:
        k = sum(ch in m.candidate.channels for m in found)
        only = sum(m.candidate.channels == [ch] for m in found)
        lines.append(f"  {ch:15} {pct(k):>12}   only this channel: {only}")

    labels = Counter(m.candidate.relevance or "unscreened" for m in found)
    if any(k != "unscreened" for k in labels):
        lines += ["", "Relevance screen on found seeds (no = wrongly dropped):"]
        lines += [f"  {k:10} {v}" for k, v in labels.most_common()]

    snowball_seeds = {_norm_name(h.seed) for h in d.hits if h.seed}
    leaked = [
        m.seed.name for m in matches
        if _norm_name(m.seed.name) in snowball_seeds
        or (m.candidate and _norm_name(m.candidate.name) in snowball_seeds)
    ]
    if leaked:
        lines += ["", f"WARNING: used as snowball seeds, so recall is inflated: {', '.join(leaked)}"]

    missed = [m.seed for m in matches if not m.candidate]
    if missed:
        lines += ["", "Missed:"] + [f"  - {s.name} {s.website}".rstrip() for s in missed]
    lines += ["", "Found:"] + [
        f"  - {m.seed.name:30} {','.join(m.candidate.channels):35} {m.candidate.relevance or ''}"
        for m in found
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    run_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else runs.latest_run()
    if not (run_dir / "discovery.json").exists():
        sys.exit(f"{run_dir} has no discovery.json — run discovery first (python -m worker.discovery).")
    print(f"Run: {run_dir}\n")
    print(report(load_seeds(), runs.load(run_dir, "discovery", Discovery)))
