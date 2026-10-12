"""Clients, where their searches sit on the board, and outreach to the companies the searches
found: who was emailed, what was sent, who replied. Kept in SQLite (data/crm.sqlite3), apart
from the session folders: a running search rewrites its session files whole, and would undo a
card moved meanwhile.

  clients     the buyers searches are run for, e.g. a meeting-transcription company
  searches    a session folder's client, and its stage and place on the client's board
  deals       one data holder worked for one client, whichever searches found it
  activities  what happened with a deal: an email sent, a reply, a call, a note, a stage change

Researched companies without a deal show in "To contact" until something is done with them;
the first move, draft or log creates the deal. Nothing here sends anything.
"""

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

CRM_DB = Path(__file__).resolve().parent.parent / "data" / "crm.sqlite3"
SCHEMA_VERSION = 1

SEARCH_STAGES = ["Sourcing", "Outreach", "In talks", "Won", "Dropped"]
DEAL_STAGES = ["To contact", "Emailed", "Replied", "Call scheduled", "Negotiating", "Won", "Dropped"]
TO_CONTACT = DEAL_STAGES[0]
# a logged activity moves its deal up to this stage, never back (a drag always can)
ADVANCES = {"sent": "Emailed", "reply": "Replied", "call": "Call scheduled"}
FOLLOW_UP_DAYS = 7  # an email unanswered this long is due a follow-up

Kind = Literal["sent", "reply", "call", "note", "stage"]
Outcome = Literal["interested", "not_now", "declined"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS searches (
    session TEXT PRIMARY KEY,                       -- the session's folder name
    client_id TEXT NOT NULL REFERENCES clients(id),
    stage TEXT,                                     -- null: follows its deals (search_stage)
    position REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS deals (
    id INTEGER PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES clients(id),
    domain TEXT NOT NULL,
    name TEXT NOT NULL,
    session TEXT NOT NULL,                          -- the search it was first worked from
    stage TEXT NOT NULL,
    position REAL NOT NULL DEFAULT 0,
    updated TEXT NOT NULL,
    UNIQUE (client_id, domain)
);
CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY,
    deal_id INTEGER NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    at TEXT NOT NULL,
    contact TEXT NOT NULL DEFAULT '',               -- the email address (or name) it was with
    subject TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    outcome TEXT,
    gmail_thread TEXT,
    gmail_message TEXT UNIQUE                       -- a synced Gmail message is logged once
);
CREATE INDEX IF NOT EXISTS activities_deal ON activities (deal_id, at);
CREATE INDEX IF NOT EXISTS deals_session ON deals (session);
"""


class Client(BaseModel):
    id: str
    name: str
    note: str = ""
    created: datetime


class Placement(BaseModel):
    """A search on its client's board."""

    session: str
    client_id: str
    stage: str | None  # None until dragged: follows its deals
    position: float


class Deal(BaseModel):
    id: int
    client_id: str
    domain: str
    name: str
    session: str
    stage: str
    position: float
    updated: datetime
    sent: int = 0  # emails logged as sent
    replies: int = 0
    calls: int = 0
    last_sent: datetime | None = None
    last_reply: datetime | None = None
    last_activity: datetime | None = None
    last_contact: str = ""  # who the last email went to


class Activity(BaseModel):
    id: int
    deal_id: int
    kind: Kind
    at: datetime
    contact: str = ""
    subject: str = ""
    body: str = ""
    outcome: Outcome | None = None
    gmail_thread: str | None = None
    gmail_message: str | None = None


class Stats(BaseModel):
    contacted: int = 0  # deals with an email sent
    replied: int = 0
    calls: int = 0
    last_activity: datetime | None = None


def now() -> datetime:
    return datetime.now().astimezone().replace(microsecond=0)


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    """One transaction: committed when the block ends, rolled back if it raises."""
    CRM_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(CRM_DB, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA foreign_keys = ON")
        if con.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
            con.execute("PRAGMA journal_mode = WAL")  # readers don't wait for a writer
            con.executescript(SCHEMA)
            con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        with con:
            yield con
    finally:
        con.close()


# --- clients ----------------------------------------------------------------------------------

def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "client"


def create_client(name: str, note: str = "") -> Client:
    name = name.strip()
    if not name:
        raise ValueError("A client needs a name")
    with _db() as con:
        base = _slug(name)
        taken = {r[0] for r in con.execute("SELECT id FROM clients WHERE id = ? OR id LIKE ?", (base, f"{base}-%"))}
        cid, n = base, 1
        while cid in taken:
            n += 1
            cid = f"{base}-{n}"
        client = Client(id=cid, name=name, note=note.strip(), created=now())
        con.execute("INSERT INTO clients (id, name, note, created) VALUES (?, ?, ?, ?)",
                    (client.id, client.name, client.note, client.created.isoformat()))
    return client


def clients() -> list[Client]:
    """Every client, by name."""
    with _db() as con:
        return [Client(**r) for r in con.execute("SELECT * FROM clients ORDER BY name COLLATE NOCASE")]


def get_client(client_id: str) -> Client | None:
    with _db() as con:
        r = con.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    return Client(**r) if r else None


def set_note(client_id: str, note: str) -> None:
    with _db() as con:
        con.execute("UPDATE clients SET note = ? WHERE id = ?", (note.strip(), client_id))


# --- searches on the board --------------------------------------------------------------------

def assign_search(session: str, client_id: str) -> None:
    """Put a search on a client's board (new ones first), or move it to another client's."""
    with _db() as con:
        top = con.execute("SELECT MIN(position) FROM searches WHERE client_id = ?", (client_id,)).fetchone()[0]
        con.execute(
            "INSERT INTO searches (session, client_id, position) VALUES (?, ?, ?) "
            "ON CONFLICT (session) DO UPDATE SET client_id = excluded.client_id, stage = NULL, "
            "position = excluded.position",
            (session, client_id, (top if top is not None else 1) - 1))


def unassign_search(session: str) -> None:
    """Take a search off its client's board. Its outreach stays with the client."""
    with _db() as con:
        con.execute("DELETE FROM searches WHERE session = ?", (session,))


def client_of(session: str) -> str | None:
    with _db() as con:
        r = con.execute("SELECT client_id FROM searches WHERE session = ?", (session,)).fetchone()
    return r[0] if r else None


def placements(client_id: str | None = None) -> list[Placement]:
    """One client's searches, or every assigned search, in board order."""
    with _db() as con:
        rows = con.execute(
            "SELECT * FROM searches" + (" WHERE client_id = ?" if client_id else "") + " ORDER BY position",
            (client_id,) if client_id else ())
        return [Placement(**r) for r in rows]


def move_search(session: str, stage: str, order: list[str] | None = None) -> None:
    """Drop a search in a column; order is that column's searches top to bottom, after the drop."""
    if stage not in SEARCH_STAGES:
        raise ValueError(f"Unknown stage: {stage}")
    with _db() as con:
        con.execute("UPDATE searches SET stage = ? WHERE session = ?", (stage, session))
        for i, name in enumerate(order or []):
            con.execute("UPDATE searches SET position = ? WHERE session = ?", (i, name))


def search_stage(placement: Placement, deals: list[Deal]) -> str:
    """Where a search sits: where it was dragged, or else where its deals have got to."""
    if placement.stage:
        return placement.stage
    if any(DEAL_STAGES.index(d.stage) >= DEAL_STAGES.index("Replied") and d.stage != "Dropped" for d in deals):
        return "In talks"
    if any(d.sent for d in deals):
        return "Outreach"
    return "Sourcing"


# --- deals and activity -----------------------------------------------------------------------

DEAL_QUERY = """
SELECT d.*,
       COALESCE(SUM(a.kind = 'sent'), 0) AS sent,
       COALESCE(SUM(a.kind = 'reply'), 0) AS replies,
       COALESCE(SUM(a.kind = 'call'), 0) AS calls,
       MAX(CASE WHEN a.kind = 'sent' THEN a.at END) AS last_sent,
       MAX(CASE WHEN a.kind = 'reply' THEN a.at END) AS last_reply,
       MAX(a.at) AS last_activity,
       COALESCE((SELECT contact FROM activities s WHERE s.deal_id = d.id AND s.kind = 'sent'
                 ORDER BY s.at DESC, s.id DESC LIMIT 1), '') AS last_contact
FROM deals d LEFT JOIN activities a ON a.deal_id = d.id
"""


def _deals(con: sqlite3.Connection, where: str, args: tuple) -> list[Deal]:
    rows = con.execute(f"{DEAL_QUERY} WHERE {where} GROUP BY d.id ORDER BY d.position, d.id", args)
    return [Deal(**r) for r in rows]


def deals(client_id: str, domains: list[str] | None = None, session: str | None = None) -> list[Deal]:
    """A client's deals: all, those for these domains, or those first worked from a search."""
    where, args = "d.client_id = ?", (client_id,)
    if session is not None:
        where, args = where + " AND d.session = ?", (*args, session)
    found = []
    with _db() as con:
        if domains is None:
            return _deals(con, where, args)
        for i in range(0, len(domains), 500):  # SQLite caps the number of parameters
            chunk = domains[i:i + 500]
            found += _deals(con, f"{where} AND d.domain IN ({','.join('?' * len(chunk))})", (*args, *chunk))
    return sorted(found, key=lambda d: (d.position, d.id))


def get_deal(deal_id: int) -> Deal | None:
    with _db() as con:
        found = _deals(con, "d.id = ?", (deal_id,))
    return found[0] if found else None


def _ensure(con: sqlite3.Connection, client_id: str, session: str, domain: str, name: str) -> int:
    con.execute(
        "INSERT INTO deals (client_id, domain, name, session, stage, position, updated) VALUES (?, ?, ?, ?, ?, "
        "(SELECT COALESCE(MAX(position), 0) + 1 FROM deals WHERE client_id = ?), ?) "
        "ON CONFLICT (client_id, domain) DO NOTHING",
        (client_id, domain, name, session, TO_CONTACT, client_id, now().isoformat()))
    return con.execute("SELECT id FROM deals WHERE client_id = ? AND domain = ?", (client_id, domain)).fetchone()[0]


def ensure_deal(client_id: str, session: str, domain: str, name: str) -> Deal:
    """The client's deal with this company, created in To contact if there is none."""
    with _db() as con:
        deal_id = _ensure(con, client_id, session, domain, name)
    return get_deal(deal_id)


def move_deal(client_id: str, session: str, domain: str, name: str, stage: str,
              order: list[str] | None = None) -> None:
    """Drop a company in a column; order is the domains of that column top to bottom, after the
    drop (companies without a deal yet keep theirs, and sort after). A change of stage is logged."""
    if stage not in DEAL_STAGES:
        raise ValueError(f"Unknown stage: {stage}")
    with _db() as con:
        deal_id = _ensure(con, client_id, session, domain, name)
        before = con.execute("SELECT stage FROM deals WHERE id = ?", (deal_id,)).fetchone()[0]
        con.execute("UPDATE deals SET stage = ?, updated = ? WHERE id = ?", (stage, now().isoformat(), deal_id))
        for i, d in enumerate(order or []):
            con.execute("UPDATE deals SET position = ? WHERE client_id = ? AND domain = ?", (i, client_id, d))
        if stage != before:
            _insert(con, deal_id, "stage", now(), body=f"{before} → {stage}")


def _insert(con: sqlite3.Connection, deal_id: int, kind: Kind, at: datetime, **fields) -> int:
    cols = ["deal_id", "kind", "at", *fields]
    cur = con.execute(f"INSERT INTO activities ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                      (deal_id, kind, at.isoformat(), *fields.values()))
    return cur.lastrowid


def log(client_id: str, session: str, domain: str, name: str, kind: Kind, at: datetime | None = None, *,
        contact: str = "", subject: str = "", body: str = "", outcome: Outcome | None = None,
        gmail_thread: str | None = None, gmail_message: str | None = None) -> Activity:
    """Record what happened with a company. Sent, reply and call move its deal up (never back)."""
    at = at or now()
    with _db() as con:
        deal_id = _ensure(con, client_id, session, domain, name)
        activity_id = _insert(con, deal_id, kind, at, contact=contact.strip(), subject=subject, body=body,
                              outcome=outcome, gmail_thread=gmail_thread, gmail_message=gmail_message)
        stage = con.execute("SELECT stage FROM deals WHERE id = ?", (deal_id,)).fetchone()[0]
        target = ADVANCES.get(kind)
        if target and DEAL_STAGES.index(target) > DEAL_STAGES.index(stage):
            con.execute("UPDATE deals SET stage = ?, updated = ? WHERE id = ?", (target, now().isoformat(), deal_id))
        row = con.execute("SELECT * FROM activities WHERE id = ?", (activity_id,)).fetchone()
    return Activity(**row)


def delete_activity(activity_id: int) -> None:
    """Undo a mistaken log. The deal's stage stays where it is."""
    with _db() as con:
        con.execute("DELETE FROM activities WHERE id = ?", (activity_id,))


def timeline(deal_id: int) -> list[Activity]:
    """What happened with a deal, newest first."""
    with _db() as con:
        rows = con.execute("SELECT * FROM activities WHERE deal_id = ? ORDER BY at DESC, id DESC", (deal_id,))
        return [Activity(**r) for r in rows]


def recent(client_id: str, limit: int = 20) -> list[tuple[Activity, str, str]]:
    """A client's latest activity, newest first, with each one's company name and search."""
    with _db() as con:
        rows = con.execute(
            "SELECT a.*, d.name AS company, d.session AS search FROM activities a JOIN deals d ON d.id = a.deal_id "
            "WHERE d.client_id = ? ORDER BY a.at DESC, a.id DESC LIMIT ?", (client_id, limit))
        out = []
        for r in rows:
            row = dict(r)
            company, search = row.pop("company"), row.pop("search")
            out.append((Activity(**row), company, search))
        return out


def follow_up_due(d: Deal) -> bool:
    """Emailed, no reply since, and the last email is FOLLOW_UP_DAYS old."""
    return (d.stage == "Emailed" and d.last_sent is not None and (now() - d.last_sent).days >= FOLLOW_UP_DAYS
            and (d.last_reply is None or d.last_reply < d.last_sent))


def stats(found: list[Deal]) -> Stats:
    return Stats(
        contacted=sum(d.sent > 0 for d in found),
        replied=sum(d.replies > 0 for d in found),
        calls=sum(d.calls > 0 for d in found),
        last_activity=max((d.last_activity for d in found if d.last_activity), default=None),
    )
