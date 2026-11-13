"""Compare researched companies under an edited brief, and kill the ones ruled out.

After research the buyer may relax or tighten the brief, e.g. accept a licence that turned out
too strict. preview() re-scores a session's researched companies under a working brief and a
working list of kills, in Python only: nothing is searched, researched or written. save() then
copies them into a new session, so the original is never changed.

Fit is recomputed from what the screen and research stored (worker/scoring.py), which is exact
for what is judged in Python:
- licence (D1): research stored the licence it found; whether D1 accepts it is a rule
- customers' data (D3), data recorded to order (C3), source types (E1), exclusions (E2, which kill)
- Must switches turned off, and categories emptied
- languages or regions removed: the rest keep their evidence
- Must switched on for the volume target or a category research checks (recency, permitted
  use, deal terms): research checks those whatever their importance

Anything else Gemini judged against the old brief. A changed value sets that judgement aside,
so it counts as unknown until the company is researched again, and the company's fit is
provisional, with the reasons:
- research checks of the category become unclear, and the screen's contradiction is dropped
- a changed volume target makes the volume score unknown
- added languages or regions weren't looked for, so they have no evidence
- Must switched on for a category only the screen judges: it only checked the must-haves it had
- a changed description, notes or data category (A and the modality add-ons): data match was
  screened against the old ones and is kept
"""

from dataclasses import dataclass

from worker import sessions
from worker.brief_template import CATEGORIES, SECTION_OF, Brief
from worker.discovery import Discovery
from worker.discovery.domains import is_excluded
from worker.schemas import Candidate, CheckedCategory
from worker.scoring import rank, rescore
from worker.sessions import BriefChange, Session

# Judged in Python from stored labels and flags, so any change re-scores exactly
PYTHON_JUDGED = {"licence", "customer_data", "collection", "sources", "exclusions"}
# Research checks these whenever they are set, so their verdicts don't depend on Must
RESEARCH_CHECKED = set(CheckedCategory.__args__)
COVERAGE = ("languages", "region")
# What data match was screened against, besides the description and notes
DATA_KEYS = {k for k, s in SECTION_OF.items() if s.code == "A" or s.modalities}
TEXTS = (("description", "What data"), ("notes", "Notes"))
EXCLUDED = "In the brief's exclusions"

Kills = dict[str, str]  # domain -> reason ("" for none)


def _value(b: Brief, key: str) -> tuple | None:
    """What the category asks for, without its Must switch; None when it's empty."""
    if not b.is_set(key):
        return None
    p = b.pick(key)
    return tuple(b.chosen(key)), p.amount, p.amount_to, p.unit if p.amount is not None else None


def _describe(b: Brief, key: str) -> str:
    return f"{b.value_text(key)} (must)" if b.must(key) else b.value_text(key) if b.is_set(key) else ""


def _exact(key: str, old: Brief, new: Brief) -> bool:
    if key in PYTHON_JUDGED or not new.is_set(key):
        return True
    if key in COVERAGE:
        return set(new.active(key)) <= set(old.active(key))  # removed only: the rest keep their evidence
    if _value(old, key) != _value(new, key):
        return False
    return not new.must(key) or old.must(key) or key in RESEARCH_CHECKED or key == "volume"


def _stale(old: Brief, new: Brief) -> set[str]:
    """Categories whose value Gemini judged and the new brief changed."""
    return {k for k in CATEGORIES if k not in PYTHON_JUDGED and new.is_set(k) and _value(old, k) != _value(new, k)}


_ORDER = [k for k, _ in TEXTS] + list(CATEGORIES)


def changes(old: Brief, new: Brief) -> list[BriefChange]:
    """What changed from old to new, in template order, each marked exact or not."""
    out = [BriefChange(key=k, label=label, before=getattr(old, k).strip(), after=getattr(new, k).strip(), exact=False)
           for k, label in TEXTS if getattr(old, k).strip() != getattr(new, k).strip()]
    for key, cat in CATEGORIES.items():
        before, after = _describe(old, key), _describe(new, key)
        if before != after:
            out.append(BriefChange(key=key, label=f"{cat.code} {cat.label}", before=before, after=after,
                                   exact=_exact(key, old, new)))
    return out


def _chain(earlier: list[BriefChange], later: list[BriefChange]) -> list[BriefChange]:
    """Changes from the original brief: earlier ones, then later ones on top."""
    merged = {ch.key: ch for ch in earlier}
    for ch in later:
        first = merged.get(ch.key)
        merged[ch.key] = first.model_copy(update={"after": ch.after, "exact": first.exact and ch.exact}) if first else ch
    return sorted((ch for ch in merged.values() if ch.before != ch.after), key=lambda ch: _ORDER.index(ch.key))


def _set_aside(c: Candidate, old: Brief, new: Brief) -> list[str]:
    """Drop what was judged against old values the new brief no longer asks for (in place).
    Returns why the company's fit is now provisional."""
    stale, label = _stale(old, new), (lambda k: CATEGORIES[k].label)
    reasons = []
    data = [name for k, name in TEXTS if getattr(old, k).strip() != getattr(new, k).strip()]
    data += [label(k) for k in CATEGORIES if k in stale and k in DATA_KEYS]
    if data:
        reasons.append(f"Data match was screened against the earlier {', '.join(data).lower()}")
    reasons += [f"New {label(k).lower()} weren't looked for" for k in COVERAGE
                if set(new.active(k)) - set(old.active(k))]
    reasons += [f"{label(k)} as a must-have wasn't checked by the screen" for k in CATEGORIES
                if k not in stale and k not in COVERAGE and not _exact(k, old, new)]
    c.languages = [x for x in c.languages if x in new.languages]
    c.regions = [x for x in c.regions if x in new.regions]
    reasons += [f"{label(k)}: the screen's contradiction was set aside" for k in c.contradicts if k in stale]
    c.contradicts = [k for k in c.contradicts if k not in stale and k != "sources"]
    if not new.accepts_holder(c.holder_type):  # E1 as a must-have: shown capped rather than hidden
        c.contradicts.append("sources")
    r = c.research
    for ch in r.checks if r else []:
        if ch.category in stale:
            was = old.value_text(ch.category) or "not set"
            ch.verdict, ch.finding = "unclear", f"Checked against the earlier brief ({was}): {ch.finding}"
            reasons.append(f"{label(ch.category)} check was set aside")
    if "volume" in stale and (c.scores.volume is not None or (r and r.volume_score is not None)):
        c.scores.volume = None
        if r:
            r.volume_score = None
        reasons.append("Volume was scored against the earlier target")
    return reasons


def _ranks(fits: dict[str, float | None], types: dict[str, str | None]) -> dict[str, int]:
    """1 for the best fit among companies of the same type: ready sources and untapped holders
    are never ranked against each other. Unknown fit last."""
    out = {}
    for kind in dict.fromkeys(types.values()):
        group = [d for d in fits if types.get(d) == kind]
        order = sorted(group, key=lambda d: (fits[d] is None, -(fits[d] or 0)))
        out |= {d: i + 1 for i, d in enumerate(order)}
    return out


@dataclass
class Preview:
    """A session's researched companies under a working brief and kills. Nothing is saved."""

    brief: Brief
    kills: Kills
    candidates: list[Candidate]  # copies, re-scored and ranked; killed ones last
    provisional: dict[str, list[str]]  # domain -> why its fit rests on judgements of an older brief
    fit_before: dict[str, float | None]  # domain -> fit in the original search
    rank_before: dict[str, int]  # among the companies of its type not killed then
    rank_now: dict[str, int]  # among the companies of its type not killed now
    changes: list[BriefChange]  # from the original search's brief
    unsaved: bool  # brief or kills differ from the session's

    def get(self, domain: str) -> Candidate:
        return next(c for c in self.candidates if c.domain == domain)


def saved_kills(session: Session) -> Kills:
    return {c.domain: c.kill_reason for c in session.discovery.candidates if c.research and c.killed}


def preview(session: Session, brief: Brief, kills: Kills | None = None) -> Preview:
    """Re-score session's researched companies under brief, with kills (the session's own when
    None). Exclusions in brief add kills, and removing one lifts them. Python only: no Gemini
    call, nothing written."""
    excluded = brief.active("exclusions")
    kills = {d: why for d, why in (saved_kills(session) if kills is None else kills).items()
             if why != EXCLUDED or is_excluded(d, excluded)}  # exclusion kills follow the brief
    researched = [c for c in session.discovery.candidates if c.research]
    out, provisional = [], {}
    for original in researched:
        c = original.model_copy(deep=True)
        reasons = session.meta.provisional.get(c.domain, []) + _set_aside(c, session.brief, brief)
        if c.domain not in kills and is_excluded(c.domain, excluded):
            kills[c.domain] = EXCLUDED
        c.killed, c.kill_reason = c.domain in kills, kills.get(c.domain, "")
        rescore(c, brief)
        out.append(c)
        if reasons:
            provisional[c.domain] = list(dict.fromkeys(reasons))
    compared = session.meta.parent is not None
    fit_before = dict(session.meta.parent_fit) if compared else {c.domain: c.fit for c in researched}
    types = {c.domain: c.holder_type for c in out}
    return Preview(
        brief=brief, kills=kills, candidates=rank(out), provisional=provisional, fit_before=fit_before,
        rank_before=_ranks({c.domain: fit_before.get(c.domain) for c in researched if not c.killed}, types),
        rank_now=_ranks({c.domain: c.fit for c in out if not c.killed}, types),
        changes=_chain(session.meta.changes, changes(session.brief, brief)),
        unsaved=brief != session.brief or kills != saved_kills(session),
    )


def save(session: Session, p: Preview, name: str) -> Session:
    """A new session holding the preview's companies under its brief; session is untouched."""
    s = sessions.create(name, p.brief, session.meta.brief_text)
    s.meta.stage = "done"
    s.meta.parent, s.meta.parent_name = session.path.name, session.meta.name
    s.meta.parent_fit, s.meta.changes, s.meta.provisional = p.fit_before, p.changes, p.provisional
    s.meta.selected = [c.domain for c in p.candidates]
    s.hypotheses = session.hypotheses
    s.discovery = Discovery(hits=[], candidates=p.candidates, exclusions=p.brief.active("exclusions"))
    sessions.save(s)
    return s


def search_text(c: Candidate) -> str:
    """What the search box matches: name, domain, type, languages, regions, reason and data held."""
    parts = [c.name, c.domain, c.holder_type or "", "collector" if c.collector else "", *c.languages, *c.regions,
             c.reason or "", c.research.data_held if c.research else ""]
    return " ".join(parts).lower()


def matches(c: Candidate, query: str) -> bool:
    """Every word of the query appears somewhere in search_text."""
    text = search_text(c)
    return all(word in text for word in query.lower().split())
