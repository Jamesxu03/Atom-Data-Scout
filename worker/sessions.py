"""Each search is a session folder under data/sessions/, so any past one reopens without
rerunning:

  data/sessions/20260930-1215-middle-east-meeting-audio/
    session.json     name, created, brief text, stage, timings, selected domains
    brief.json       the final Brief: every category's choices, importance, stated or inferred
    hypotheses.json  the main ones, and any added for archetypes nothing was found for
    markets.json     local markets searched natively: their search hooks and law signals
    discovery.json   hits + candidates (scores, research, contacts and email on each), rounds

The timestamp prefix keeps folder names in creation order. Rerunning or re-comparing
(worker/compare.py) with an edited brief always creates a new session, so old results are
never overwritten.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from worker.brief_template import Brief, from_legacy
from worker.discovery import Discovery
from worker.schemas import HolderHypothesis, MarketPlan
from worker.scoring import is_relevant, rank, rescore
from worker.steps.confirm import redecide

SESSIONS_DIR = Path(__file__).resolve().parent.parent / "data" / "sessions"

# searching: started (or the app stopped mid-search); confirming: the list is ready and the best
# candidates are being confirmed; partial: the Gemini quota ran out
Stage = Literal["searching", "confirming", "done", "partial", "failed"]


class BriefChange(BaseModel):
    """One line of what a compare session changed in the brief (worker/compare.py)."""

    key: str  # a category key, or "description" / "notes"
    label: str
    before: str
    after: str
    exact: bool  # re-scored in Python; False: Gemini judged it against the old brief


class SessionMeta(BaseModel):
    name: str
    created: datetime
    brief_text: str = ""
    stage: Stage = "searching"
    timings: dict[str, float] = {}  # seconds per step, in run order, plus "total"
    selected: list[str] = []  # domains picked for research
    errors: list[str] = []
    relevant: int = 0  # for the session list, so it needn't load every discovery.json
    # Compare sessions: another session's researched companies under an edited brief. Fits,
    # changes and provisional reasons are counted from the original search, across re-comparisons.
    parent: str | None = None  # folder name of the session compared from
    parent_name: str = ""
    parent_fit: dict[str, float | None] = {}  # domain -> fit in the original search
    changes: list[BriefChange] = []
    provisional: dict[str, list[str]] = {}  # domain -> why its fit rests on judgements of an older brief


@dataclass
class Session:
    path: Path
    meta: SessionMeta
    brief: Brief
    hypotheses: list[HolderHypothesis] = field(default_factory=list)
    discovery: Discovery = field(default_factory=lambda: Discovery(hits=[], candidates=[]))
    markets: list[MarketPlan] = field(default_factory=list)


class MarketPlans(BaseModel):
    markets: list[MarketPlan]


class AnyHypotheses(BaseModel):
    """hypotheses.json: the main 5 to 10, plus any a later round added for archetypes."""

    hypotheses: list[HolderHypothesis]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:50] or "session"


def create(name: str, brief: Brief, brief_text: str = "") -> Session:
    now = datetime.now()
    base = SESSIONS_DIR / f"{now:%Y%m%d-%H%M}-{_slug(name)}"
    path, n = base, 1
    while path.exists():
        n += 1
        path = base.with_name(f"{base.name}-{n}")
    path.mkdir(parents=True)
    session = Session(path=path, meta=SessionMeta(name=name, created=now, brief_text=brief_text), brief=brief)
    save(session)
    return session


def _write(path: Path, obj: BaseModel) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(obj.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(path)  # a reader never sees half a file


def save(session: Session) -> None:
    session.meta.relevant = sum(is_relevant(c) for c in session.discovery.candidates)
    _write(session.path / "session.json", session.meta)
    _write(session.path / "brief.json", session.brief)
    if session.hypotheses:
        _write(session.path / "hypotheses.json", AnyHypotheses(hypotheses=session.hypotheses))
    if session.markets:
        _write(session.path / "markets.json", MarketPlans(markets=session.markets))
    if session.discovery.hits or session.discovery.candidates:
        _write(session.path / "discovery.json", session.discovery)


def _read[T: BaseModel](path: Path, schema: type[T]) -> T | None:
    return schema.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else None


def _read_brief(path: Path) -> Brief:
    data = json.loads(path.read_text(encoding="utf-8"))
    return from_legacy(data) if "data_type" in data else Brief.model_validate(data)  # made before the template


def load(path: Path | str) -> Session:
    path = Path(path)
    hyps = _read(path / "hypotheses.json", AnyHypotheses)
    plans = _read(path / "markets.json", MarketPlans)
    brief = _read_brief(path / "brief.json")
    discovery = _read(path / "discovery.json", Discovery) or Discovery(hits=[], candidates=[])
    for c in discovery.candidates:  # today's rules, from the stored screen, research and confirmation results
        rescore(c, brief)
        redecide(brief, c)
    discovery.candidates = rank(discovery.candidates)
    return Session(
        path=path,
        meta=_read(path / "session.json", SessionMeta),
        brief=brief,
        hypotheses=hyps.hypotheses if hyps else [],
        discovery=discovery,
        markets=plans.markets if plans else [],
    )


def load_meta(path: Path | str) -> SessionMeta | None:
    """Only session.json: cheap enough to poll while a job runs."""
    return _read(Path(path) / "session.json", SessionMeta)


def list_all() -> list[tuple[Path, SessionMeta]]:
    """Every session, newest first. Folders that aren't sessions are skipped."""
    out = []
    for path in SESSIONS_DIR.glob("*/session.json"):
        try:
            out.append((path.parent, _read(path, SessionMeta)))
        except ValueError:  # half-written or from an older version: not worth failing the list
            continue
    return sorted(out, key=lambda s: (s[1].created, s[0].name), reverse=True)
