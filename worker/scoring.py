"""Scores and fit, computed in Python. The model only supplies data_match, a volume signal,
contradicted must-haves and initial saleability answers (screen), and the licence found, a volume
score, checks and verified saleability answers (research); everything else is a rule here.

The licence score depends on the holder type:
- ready source: 4 until research finds its licence, then 5 if D1 accepts it, otherwise 1
- untapped holder: its saleability grade, the lowest of three answers (does the data exist, is
  selling it allowed, are they willing), because all three must hold for a purchase. A grade of
  1 or 2 also caps the fit.

Tool vendors (untapped companies whose customers record and own the data) are capped below
RELEVANT_FIT: they lead to holders but aren't one.

How importance works (see worker/brief_template.py):
- must: counted in the score, and the fit is capped when evidence contradicts it
- nice: counted in the score
- any: ignored
Unknown evidence stays neutral: an unknown score is left out and the weights rescaled.
"""

from worker.brief_template import (
    COMMERCIAL,
    CUSTOM_COLLECTION,
    CUSTOMER_CONSENT,
    CUSTOMER_EXCLUDE,
    EXCLUSIVE,
    INTERNAL_ONLY,
    UNCLEAR,
    Brief,
)
from worker.config import (
    FIT_WEIGHTS,
    INTERNAL_ONLY_CAP,
    LOW_SALEABILITY_CAP,
    MUST_HAVE_CAP,
    NOT_SELLABLE_CAP,
    RELEVANT_FIT,
    VENDOR_CAP,
)
from worker.schemas import SALEABILITY_QUESTIONS, Candidate


def clamp(score: int | None) -> int | None:
    return None if score is None else max(1, min(5, int(score)))


def coverage(brief: Brief, languages: list[str], regions: list[str]) -> int | None:
    """5 if there is evidence for every dimension the brief counts (languages, regions), 3 if for
    only one of two, 1 if none; None if it counts neither."""
    named = [bool(found) for wanted, found in ((brief.languages, languages), (brief.regions, regions)) if wanted]
    if not named:
        return None
    hits = sum(named)
    return 5 if hits == len(named) else 3 if hits else 1


GRADES = {5: "High", 4: "High", 3: "Medium", 2: "Low", 1: "Not sellable"}
READY_PRIOR = 4  # a ready source offers the data; research then checks its licence against D1


def answers(c: Candidate, brief: Brief) -> dict[str, int | None]:
    """The three saleability scores (research's where verified, otherwise the screen's). Data its
    customers own is at most 2 on "allowed" (it needs their consent), or 1 if D6 excludes it;
    D6 "accept, check later" leaves the answer as it is."""
    s = c.current_saleability()
    scores = {q: clamp(getattr(s, q).score) if s else None for q in SALEABILITY_QUESTIONS}
    if c.data_owner == "customers":
        d6 = brief.active("customer_data")
        if CUSTOMER_EXCLUDE in d6:
            scores["allowed"] = 1
        elif not d6 or CUSTOMER_CONSENT in d6:
            scores["allowed"] = min(scores["allowed"] or 2, 2)
    return scores


def grade(c: Candidate, brief: Brief) -> int | None:
    """The lowest known answer; None if all three are unknown. With only one answer known the
    grade is at most 3 (medium): one good answer says little about the other two."""
    known = [v for v in answers(c, brief).values() if v is not None]
    if not known:
        return None
    return min(known) if len(known) >= 2 else min(min(known), 3)


def is_verified(c: Candidate) -> bool:
    """Every known answer has a quote research found on the cited page."""
    s = c.current_saleability()
    known = [a for q in SALEABILITY_QUESTIONS if s and (a := getattr(s, q)).score is not None]
    return bool(known) and all(a.verified for a in known)


def saleability_label(c: Candidate, brief: Brief) -> str:
    """What the Saleability column shows."""
    if c.holder_type == "ready":
        if c.research:
            return f"checked: {c.research.licence}"
        return "prior: records to order" if c.collector else "prior: offered, licence not checked"
    if c.holder_type == "untapped":
        g = grade(c, brief)
        return "Unknown" if g is None else f"{GRADES[g]} ({'verified' if is_verified(c) else 'initial'})"
    return "?"


def licence_acceptable(found: str, brief: Brief) -> bool | None:
    """Does the licence research found fit D1? None when it can't say (unclear, or D1 not set).
    A seller offering exclusive licences can also license non-exclusively."""
    accepted = brief.active("licence")
    if not accepted or found == UNCLEAR:
        return None
    if found == INTERNAL_ONLY:
        return False
    return found in accepted or (found == EXCLUSIVE and COMMERCIAL in accepted)


def licence_from_found(found: str, brief: Brief) -> int | None:
    if found == INTERNAL_ONLY:
        return 1
    ok = licence_acceptable(found, brief)
    return None if ok is None else 5 if ok else 1


def must_failures(c: Candidate, brief: Brief) -> list[str]:
    """Must-have categories the evidence contradicts. No evidence is not a contradiction, with two
    exceptions the buyer asks for explicitly: a must-have language or region with no evidence
    at all, and a researched licence left unclear when D1 is a must-have without "Unclear"."""
    fails = [key for key, wanted, found in (("languages", brief.languages, c.languages),
                                             ("region", brief.regions, c.regions))
             if brief.must(key) and wanted and not found]
    fails += [k for k in c.contradicts if brief.must(k)]
    if r := c.research:
        fails += [ch.category for ch in r.checks if ch.verdict == "fails" and brief.must(ch.category)]
        if brief.must("licence") and c.holder_type == "ready":  # untapped holders: saleability instead
            unclear_refused = r.licence == UNCLEAR and UNCLEAR not in brief.active("licence")
            if licence_acceptable(r.licence, brief) is False or unclear_refused:
                fails.append("licence")
    if brief.must("volume") and c.scores.volume == 1:
        fails.append("volume")
    if (brief.must("customer_data") and c.data_owner == "customers" and c.holder_type == "untapped"
            and CUSTOMER_EXCLUDE in brief.active("customer_data")):
        fails.append("customer_data")
    if c.collector and brief.must("delivery") and CUSTOM_COLLECTION not in brief.active("delivery"):
        fails.append("delivery")  # the buyer wants existing data, not data recorded to order
    return list(dict.fromkeys(fails))


def fit(c: Candidate, brief: Brief) -> float | None:
    """Weighted mean over the known scores, then caps. None until data_match is known."""
    known = {k: v for k, v in c.scores.model_dump().items() if v is not None}
    if "data_match" not in known:
        return None
    value = sum(FIT_WEIGHTS[k] * v for k, v in known.items()) / sum(FIT_WEIGHTS[k] for k in known)
    if must_failures(c, brief):
        value = min(value, MUST_HAVE_CAP)
    if c.vendor:
        value = min(value, VENDOR_CAP)
    if c.holder_type == "untapped" and (g := grade(c, brief)) is not None and g <= 2:
        value = min(value, NOT_SELLABLE_CAP if g == 1 else LOW_SALEABILITY_CAP)
    if c.holder_type == "ready" and c.research and c.research.licence == INTERNAL_ONLY:
        value = min(value, INTERNAL_ONLY_CAP)
    return round(value, 2)


def licence_score(c: Candidate, brief: Brief) -> int | None:
    """The licence slot of fit: the saleability grade for untapped holders, the licence for ready
    sources (ignored when D1 is set to Any, unless research found it is for internal use only)."""
    if c.holder_type == "untapped":
        return grade(c, brief)
    if c.holder_type != "ready":
        return None
    found = c.research.licence if c.research else None
    if found == INTERNAL_ONLY:
        return 1
    if brief.importance("licence") == "any":
        return None
    checked = licence_from_found(found, brief) if found else None
    return checked if checked is not None else READY_PRIOR


def rescore(c: Candidate, brief: Brief, volume_signal: int | None = None) -> None:
    """Recompute coverage, licence, volume and fit (in place). Research, where done, replaces the
    ready-source prior, the screen's saleability answers and its volume signal; anything research
    couldn't verify leaves them as they were. Categories set to Any count for nothing."""
    c.scores.coverage = coverage(brief, c.languages, c.regions)
    c.scores.licence = licence_score(c, brief)
    if volume_signal is not None:
        c.scores.volume = clamp(volume_signal)
    if c.research and c.research.volume_score is not None:
        c.scores.volume = clamp(c.research.volume_score)
    if brief.importance("volume") == "any":
        c.scores.volume = None
    c.fit = fit(c, brief)


def is_relevant(c: Candidate) -> bool:
    return not c.excluded and c.fit is not None and c.fit >= RELEVANT_FIT


def rank(candidates: list[Candidate]) -> list[Candidate]:
    """By fit; unknown fit (e.g. unreachable homepage) and excluded companies last.
    Corroboration (more channels, hypotheses and sources) only breaks ties: it favours famous
    companies, which are the least likely to sell their customers' data."""
    return sorted(candidates, key=lambda c: (
        c.excluded, c.fit is None, -(c.fit or 0), tuple(-int(x) for x in c.corroboration()),
    ))
