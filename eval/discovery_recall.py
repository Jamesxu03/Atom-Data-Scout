"""What share of the companies in eval/seed_companies.csv did discovery find, via which
channel, and where did the ranking put them?

Run: uv run python -m eval.discovery_recall [session_dir]   (defaults to the newest session)

Matches by domain; seeds with no website are matched by name. Rank positions are in the
session's ranked list (1 = top), so they show whether the known-good companies end up near
the top.

Also measures what the search is for: how many of the untapped companies are real holders
rather than tool vendors (whose customers hold the data), and how many holders reach the top 25.
"""

import csv
import re
import sys
from collections import Counter
from pathlib import Path
from typing import get_args

from pydantic import BaseModel

from worker import sessions
from worker.discovery import Discovery
from worker.discovery.domains import normalize_domain
from worker.schemas import Candidate, Channel
from worker.scoring import is_relevant, rank

SEEDS_CSV = Path(__file__).parent / "seed_companies.csv"


class Seed(BaseModel):
    name: str
    website: str = ""
    type: str = ""  # expected type: ready, untapped (a holder) or vendor (untapped, makes the tool)
    notes: str = ""


class SeedMatch(BaseModel):
    seed: Seed
    candidate: Candidate | None
    rank: int | None = None  # 1-based position in the ranked list


def load_seeds(path: Path = SEEDS_CSV) -> list[Seed]:
    with path.open(newline="", encoding="utf-8") as f:
        return [Seed(**{k: (v or "").strip() for k, v in row.items() if k}) for row in csv.DictReader(f)
                if (row.get("name") or "").strip()]


def _norm_name(name: str) -> str:
    """Lowercased, without legal suffixes or punctuation; letters of any script are kept, so
    two different Japanese or Arabic names never both become ""."""
    name = re.sub(r"\b(ltd|limited|inc|plc|llc|gmbh|corp|co)\b\.?", "", name.lower())
    return re.sub(r"[\W_]", "", name)


def match(seeds: list[Seed], candidates: list[Candidate]) -> list[SeedMatch]:
    ranked = rank(candidates)
    position = {c.domain: i for i, c in enumerate(ranked, 1)}
    by_domain = {c.domain: c for c in ranked}
    by_name = {_norm_name(c.name): c for c in ranked}
    out = []
    for s in seeds:
        domain = normalize_domain(s.website)
        c = by_domain.get(domain) if domain else None
        if c is None:
            c = by_name.get(_norm_name(s.name))
        out.append(SeedMatch(seed=s, candidate=c, rank=position[c.domain] if c else None))
    return out


def screened_type(c: Candidate) -> str | None:
    """The screen's type in the seed file's terms: tool vendors are their own type."""
    return "vendor" if c.vendor else c.holder_type


def holder_lines(d: Discovery, top: int = 25) -> list[str]:
    """Share of tool vendors among untapped companies, and real holders near the top."""
    untapped = [c for c in d.candidates if c.holder_type == "untapped" and not c.excluded]
    if not untapped:
        return []
    vendors = sum(c.vendor for c in untapped)
    holders = [c for c in rank(d.candidates)[:top] if c.holder_type == "untapped" and not c.vendor and not c.excluded]
    share = f"{100 * vendors / len(untapped):.0f}%"
    return ["", f"Untapped companies: {len(untapped)}, of which tool vendors: {vendors} ({share})",
            f"Untapped holders (not vendors) in the top {top}: {len(holders)}"]


def _screen_outcome(c: Candidate) -> str:
    if c.excluded:
        return "excluded"
    if c.fit is None:
        return "unscored"
    return "relevant" if is_relevant(c) else "below threshold"


def report(seeds: list[Seed], d: Discovery) -> str:
    if not seeds:
        return f"No seeds in {SEEDS_CSV} yet — add rows (name,website,notes) and re-run."
    matches = match(seeds, d.candidates)
    found = [m for m in matches if m.candidate]
    n = len(seeds)

    def pct(k: int) -> str:
        return f"{k}/{n} ({100 * k / n:.0f}%)"

    lines = [f"Discovery recall: {pct(len(found))} of seed companies found", "", "By channel:"]
    for ch in get_args(Channel):
        k = sum(ch in m.candidate.channels for m in found)
        only = sum(m.candidate.channels == [ch] for m in found)
        lines.append(f"  {ch:15} {pct(k):>12}   only this channel: {only}")

    outcomes = Counter(_screen_outcome(m.candidate) for m in found)
    lines += ["", "Screen on found seeds (excluded or below threshold = wrongly dropped):"]
    lines += [f"  {k:16} {v}" for k, v in outcomes.most_common()]

    typed = [m for m in found if m.seed.type]
    if typed:
        wrong = [m for m in typed if screened_type(m.candidate) != m.seed.type]
        lines += ["", f"Holder type matches the expected one for {len(typed) - len(wrong)}/{len(typed)} found seeds"]
        lines += [f"  - {m.seed.name}: expected {m.seed.type}, screened {screened_type(m.candidate) or 'unscreened'}"
                  for m in wrong]

    lines += holder_lines(d)

    ranks = sorted(m.rank for m in found)
    if ranks:
        lines += ["", (f"Rank of found seeds among {len(d.candidates)}: median {ranks[len(ranks) // 2]}, "
                       f"top 10: {sum(r <= 10 for r in ranks)}, top 25: {sum(r <= 25 for r in ranks)}")]

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
    lines += ["", "Found (rank, fit):"] + [
        f"  #{m.rank:<4} {m.seed.name:30} {m.candidate.fit if m.candidate.fit is not None else '?':<5} "
        f"{','.join(m.candidate.channels)}"
        for m in sorted(found, key=lambda m: m.rank)
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        session_dir = Path(sys.argv[1])
    elif all_sessions := sessions.list_all():
        session_dir = all_sessions[0][0]
    else:
        sys.exit("No sessions yet — run a search in the app first.")
    session = sessions.load(session_dir)
    print(f"Session: {session_dir}\n")
    print(report(load_seeds(), session.discovery))
