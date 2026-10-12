"""Discovery quality against companies a person judged good (eval/gold_finds.csv), per list.

Run: uv run python -m eval.discovery_quality <brief> [session_dir ...] [--labels]
  brief: a file name in eval/briefs without .json, e.g. meeting_audio_en
  sessions: default every session named "eval <brief> ..." (eval/settings.py creates them)
  --labels: write eval/labels/<session>.csv with each list's top 20 and an empty label column
  for a person to fill in: good (fits and plausibly sells), fit (fits only) or poor

gold_finds.csv, one row per company a person judged:
  brief      the eval brief it belongs to
  name, website
  fit        good, partial or poor
  sellable   yes, maybe or no (from its pages or a reply: "open to discussing" is yes)
  found_by   manual (found by hand: no Data Scout session returned it) or scout (the tool did)
  evidence   one line and a link: why it fits and why it may sell
Aim for 15 to 25 manual rows per brief, the scout rows that turned out good, and a few known
bad results (famous vendors whose customers own the data) marked poor or no.

Ready sources and untapped holders are reported separately, never pooled. With several runs
(grounded search varies), each number is the mean.
"""

import csv
import sys
from pathlib import Path
from statistics import mean

from pydantic import BaseModel

from eval.discovery_recall import _norm_name
from worker import sessions
from worker.brief_template import Brief
from worker.discovery.domains import normalize_domain
from worker.schemas import Candidate
from worker.scoring import split, verdict
from worker.sessions import Session

EVAL = Path(__file__).parent
GOLD_CSV = EVAL / "gold_finds.csv"
BRIEFS = EVAL / "briefs"
LABELS = EVAL / "labels"
TOP = 20
SIDES = ("untapped", "ready")


class GoldRow(BaseModel):
    brief: str
    name: str
    website: str = ""
    fit: str = ""  # good, partial or poor
    sellable: str = ""  # yes, maybe or no
    found_by: str = ""  # manual or scout
    evidence: str = ""

    @property
    def good(self) -> bool:
        return self.fit == "good" and self.sellable in ("yes", "maybe")


def load_gold(brief: str, path: Path = GOLD_CSV) -> list[GoldRow]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = [GoldRow(**{k: (v or "").strip() for k, v in r.items() if k}) for r in csv.DictReader(f)]
    return [r for r in rows if r.brief == brief and r.name]


def load_brief(name: str) -> Brief:
    return Brief.model_validate_json((BRIEFS / f"{name}.json").read_text(encoding="utf-8"))


def find(row: GoldRow, candidates: list[Candidate]) -> Candidate | None:
    """By domain, then by name."""
    domain = normalize_domain(row.website)
    by_domain = next((c for c in candidates if domain and c.domain == domain), None)
    return by_domain or next((c for c in candidates if _norm_name(c.name) == _norm_name(row.name)), None)


def shown(c: Candidate) -> bool:
    return not c.excluded and not c.killed and c.fit is not None and not c.vendor


def labels(path: Path) -> dict[str, str]:
    """domain -> a person's label for one session, if the labels file is filled in."""
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {r["domain"]: r["label"].strip() for r in csv.DictReader(f) if (r.get("label") or "").strip()}


def write_labels(s: Session) -> Path:
    LABELS.mkdir(exist_ok=True)
    path = LABELS / f"{s.path.name}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["list", "rank", "name", "domain", "website", "verdict", "reason", "label"])
        for side in SIDES:
            for i, c in enumerate([c for c in split(s.discovery.candidates)[side] if shown(c)][:TOP], 1):
                reason = c.confirmation.reason if c.confirmation else c.reason
                w.writerow([side, i, c.name, c.domain, c.website, verdict(c) or "", reason, ""])
    return path


def run_metrics(s: Session, gold: list[GoldRow]) -> dict[str, float | None]:
    """One session's numbers. None where there is nothing to measure."""
    lists = split(s.discovery.candidates)
    ranked = {side: [c for c in lists[side] if shown(c)] for side in SIDES}
    position = {c.domain: (side, i) for side in SIDES for i, c in enumerate(ranked[side], 1)}
    out: dict[str, float | None] = {}

    def share(hits: int, total: int) -> float | None:
        return hits / total if total else None

    for found_by in ("manual", "scout"):
        rows = [r for r in gold if r.found_by == found_by and r.good]
        out[f"recall {found_by}"] = share(sum(find(r, s.discovery.candidates) is not None for r in rows), len(rows))
    good = [r for r in gold if r.good]
    out["confirmed good finds"] = sum(verdict(c) in ("confirmed", "possible")
                                      for r in good if (c := find(r, s.discovery.candidates)))
    ranks = [position[c.domain][1] for r in good if (c := find(r, s.discovery.candidates)) and c.domain in position]
    out["median rank of good gold rows"] = sorted(ranks)[len(ranks) // 2] if ranks else None
    bad = [r for r in gold if r.fit == "poor" or r.sellable == "no"]
    out["known bad in a top 20"] = sum(1 for r in bad if (c := find(r, s.discovery.candidates))
                                       and c.domain in position and position[c.domain][1] <= TOP)
    labelled = labels(LABELS / f"{s.path.name}.csv")
    brief = s.brief
    for side in SIDES:
        top = ranked[side][:TOP]
        marked = [labelled[c.domain] for c in top if c.domain in labelled]
        out[f"{side}: precision of top {TOP}"] = share(marked.count("good"), len(marked))
        out[f"{side}: local share of top {TOP}"] = (
            share(sum(bool(c.languages or c.regions) for c in top), len(top)) if brief.languages or brief.regions
            else None)
        out[f"{side}: confirmed or possible in top {TOP}"] = sum(verdict(c) in ("confirmed", "possible") for c in top)
    untapped = [c for c in lists["untapped"] if not c.excluded and not c.killed and c.fit is not None]
    out[f"untapped: organisation share of top {TOP}"] = share(
        sum(not c.vendor for c in untapped[:TOP]), len(untapped[:TOP]))
    calls = sum(r.calls for r in s.discovery.rounds)
    out["grounded discovery calls"] = calls
    out["Google queries per discovery call"] = share(sum(r.queries for r in s.discovery.rounds), calls)
    out["rounds"] = len([r for r in s.discovery.rounds if r.calls])
    out["minutes"] = s.meta.timings.get("total", 0) / 60
    return out


def report(brief: str, runs: list[Session], gold: list[GoldRow]) -> str:
    if not runs:
        return f'No sessions for "{brief}": run eval/settings.py or pass session folders.'
    manual = sum(r.found_by == "manual" for r in gold)
    lines = [f"Brief: {brief}, {len(runs)} run(s), {len(gold)} gold rows ({manual} found by hand)"]
    if not gold:
        lines.append(f"No gold rows for this brief in {GOLD_CSV.name} yet: recall needs them.")
    per_run = [run_metrics(s, gold) for s in runs]
    for key in per_run[0]:
        values = [m[key] for m in per_run if m[key] is not None]
        if not values:
            lines.append(f"  {key:45} —")
            continue
        avg = mean(values)
        text = f"{100 * avg:.0f}%" if ("share" in key or "precision" in key or "recall" in key) else f"{avg:.1f}"
        lines.append(f"  {key:45} {text}")
    missed = [r for r in gold if r.good and r.found_by == "manual"
              and not any(find(r, s.discovery.candidates) for s in runs)]
    if missed:
        lines += ["", "Good companies found by hand that no run found:"] + [f"  - {r.name} {r.website}" for r in missed]
    return "\n".join(lines)


def eval_sessions(brief: str) -> list[Session]:
    return [sessions.load(p) for p, meta in sessions.list_all() if meta.name.startswith(f"eval {brief} ")]


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    brief_name, dirs = args[0], args[1:]
    runs = [sessions.load(d) for d in dirs] if dirs else eval_sessions(brief_name)
    if "--labels" in sys.argv:
        for s in runs:
            print(f"Wrote {write_labels(s)}")
    print(report(brief_name, runs, load_gold(brief_name)))
