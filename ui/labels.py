"""Labels and short texts for tables and headers, shared by every view."""

import re
from datetime import date, datetime

from worker import crm
from worker.schemas import Candidate
from worker.scoring import verdict

HOLDER_LABELS = {"ready": "Ready source", "untapped": "Untapped", None: "Not screened"}
LIST_TITLES = {"untapped": "Untapped holders", "ready": "Ready sources", None: "Excluded and not screened"}
VERDICT_LABELS = {"confirmed": "✅ Confirmed", "possible": "🟡 Possible", "unconfirmed": "❔ Unconfirmed",
                  "rejected": "❌ Rejected", None: ""}
TRIGGER_LABELS = {"distress": "Distress", "precedent": "Past data deal", "law": "Law"}
QUESTION_LABELS = {"exists": "Exists?", "allowed": "Allowed?", "willing": "Willing?"}
VERDICT_ICONS = {"meets": "✅", "fails": "❌", "unclear": "❔"}
LEGAL_NOTE = "Licence status is a screening flag from public pages, not legal advice."
FACT_LABELS = {"price": "Price", "data_period": "Data years", "founded": "Founded"}
FACT_HELP = "Only when a page states it; the quote is checked on the page. Research looks again with Google Search."


def fmt(x: float | None) -> str:
    return "?" if x is None else f"{x:.1f}" if isinstance(x, float) else str(x)


def fact_text(c: Candidate, key: str) -> str:
    """The stated value, with the company's age added to a founding year."""
    if not (f := c.fact(key)):
        return "?"
    if key == "founded" and (year := re.search(r"\b(1[89]\d\d|20\d\d)\b", f.value)):
        return f"{year[1]} ({date.today().year - int(year[1])} years)"
    return f.value


def type_label(c: Candidate) -> str:
    if c.excluded and c.holder_type is None:
        return "Excluded"
    return HOLDER_LABELS[c.holder_type] + (" · records to order" if c.collector else " · tool vendor" if c.vendor else "")


def obstacle_text(c: Candidate) -> str:
    """The main obstacle, and how to buy it anyway, for untapped holders."""
    s = c.current_saleability()
    if not s or not (s.obstacle or s.path):
        return ""
    return " → ".join(x for x in (s.obstacle, s.path) if x)


def confirmation_text(c: Candidate) -> str:
    return VERDICT_LABELS[verdict(c)]


def trigger_text(c: Candidate) -> str:
    """Untapped holders: the verified triggers, or why a big or regulated one is capped."""
    if c.triggers:
        return "; ".join(f"{TRIGGER_LABELS[t.kind]} {t.date}: {t.summary}" for t in c.triggers)
    if c.needs_trigger:
        return "Big or regulated: no trigger found" if c.trigger_checked else "Big or regulated: not checked yet"
    return ""


def kill_text(c: Candidate) -> str:
    return f"Killed: {c.kill_reason}" if c.kill_reason else "Killed"


def change_text(now: float | None, before: float | None) -> str:
    if now is None or before is None:
        return ""
    return "same" if abs(now - before) < 0.05 else f"{now - before:+.1f}"


def rank_text(before: int | None, now: int | None) -> str:
    if now is None:
        return "killed"
    return f"#{now}" if before in (None, now) else f"#{before} → #{now}"


def ago(when: datetime | None) -> str:
    """today, yesterday, 3 days ago; empty if never."""
    if when is None:
        return ""
    days = (crm.now().date() - when.astimezone().date()).days
    return "today" if days <= 0 else "yesterday" if days == 1 else f"{days} days ago"
