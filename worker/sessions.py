"""Each search is a session folder under data/sessions/, so any past one reopens without
rerunning:

  data/sessions/20260930-1215-middle-east-meeting-audio/
    session.json     name, created, brief text, stage, timings, selected domains
    brief.json       the final Brief
    hypotheses.json
    discovery.json   hits + candidates (scores, research, contacts and email on each)

The timestamp prefix keeps folder names in creation order. Rerunning with an edited brief
always creates a new session, so old results are never overwritten.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from worker.discovery import Discovery
from worker.schemas import Brief, HolderHypothesis
from worker.scoring import is_relevant
from worker.steps.hypotheses import HypothesisList

SESSIONS_DIR = Path(__file__).resolve().parent.parent / "data" / "sessions"

# searching: started (or the app stopped mid-search); partial: the Gemini quota ran out
Stage = Literal["searching", "done", "partial", "failed"]


class SessionMeta(BaseModel):
    name: str
    created: datetime
    brief_text: str = ""
    stage: Stage = "searching"
    timings: dict[str, float] = {}  # seconds per step, in run order, plus "total"
    selected: list[str] = []  # domains picked for research
    errors: list[str] = []
    relevant: int = 0  # for the session list, so it needn't load every discovery.json


@dataclass
class Session:
    path: Path
    meta: SessionMeta
    brief: Brief
    hypotheses: list[HolderHypothesis] = field(default_factory=list)
    discovery: Discovery = field(default_factory=lambda: Discovery(hits=[], candidates=[]))


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
        _write(session.path / "hypotheses.json", HypothesisList(hypotheses=session.hypotheses))
    if session.discovery.hits or session.discovery.candidates:
        _write(session.path / "discovery.json", session.discovery)


def _read[T: BaseModel](path: Path, schema: type[T]) -> T | None:
    return schema.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else None


def load(path: Path | str) -> Session:
    path = Path(path)
    hyps = _read(path / "hypotheses.json", HypothesisList)
    return Session(
        path=path,
        meta=_read(path / "session.json", SessionMeta),
        brief=_read(path / "brief.json", Brief),
        hypotheses=hyps.hypotheses if hyps else [],
        discovery=_read(path / "discovery.json", Discovery) or Discovery(hits=[], candidates=[]),
    )


def list_all() -> list[tuple[Path, SessionMeta]]:
    """Every session, newest first. Folders that aren't sessions are skipped."""
    out = []
    for path in SESSIONS_DIR.glob("*/session.json"):
        try:
            out.append((path.parent, _read(path, SessionMeta)))
        except ValueError:  # half-written or from an older version: not worth failing the list
            continue
    return sorted(out, key=lambda s: (s[1].created, s[0].name), reverse=True)
