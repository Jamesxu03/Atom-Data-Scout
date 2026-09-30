"""Scores and fit, computed in Python. The model only supplies data_match, a volume signal and
contradicted must-haves (screen), and the licence found, a volume score and checks (research);
everything else is a rule here.

How importance works (see worker/brief_template.py):
- must: counted in the score, and the fit is capped when evidence contradicts it
- nice: counted in the score
- any: ignored
Unknown evidence stays neutral: an unknown score is left out and the weights rescaled.
"""

from worker.brief_template import (
    COMMERCIAL,
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
    MUST_HAVE_CAP,
    RELEVANT_FIT,
    TECHNOLOGY_CAP,
)
from worker.schemas import Candidate, DataOwner, HolderType


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


# Before research: how likely the data can be licensed, from what the screen saw
_PRIORS: dict[tuple[str, str | None], tuple[int, str]] = {
    ("vendor", "company"): (4, "likely sellable"),
    ("by_product", "company"): (3, "may be licensable"),
    ("by_product", "customers"): (2, "likely needs consent"),
}


def _prior(holder_type: HolderType | None, data_owner: DataOwner | None) -> tuple[int | None, str]:
    if holder_type == "public":
        return 3, "likely open or restricted"
    if holder_type == "technology":
        return 2, "likely internal data only"
    return _PRIORS.get((holder_type, data_owner), (None, "owner unclear"))


def prior_label(holder_type: HolderType | None, data_owner: DataOwner | None) -> str:
    return _prior(holder_type, data_owner)[1]


def licence_prior(c: Candidate, brief: Brief) -> int | None:
    """D6 decides what data held for customers is worth: needing consent (2), nothing (1), or
    left to check later (neutral)."""
    score = _prior(c.holder_type, c.data_owner)[0]
    if c.data_owner == "customers":
        d6 = brief.active("customer_data")
        if CUSTOMER_EXCLUDE in d6:
            return 1
        if d6 and CUSTOMER_CONSENT not in d6:  # accept, check later
            return None
    return score


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
        if brief.must("licence"):
            unclear_refused = r.licence == UNCLEAR and UNCLEAR not in brief.active("licence")
            if licence_acceptable(r.licence, brief) is False or unclear_refused:
                fails.append("licence")
    if brief.must("volume") and c.scores.volume == 1:
        fails.append("volume")
    if brief.must("customer_data") and c.data_owner == "customers" and CUSTOMER_EXCLUDE in brief.active("customer_data"):
        fails.append("customer_data")
    return list(dict.fromkeys(fails))


def fit(c: Candidate, brief: Brief) -> float | None:
    """Weighted mean over the known scores, then caps. None until data_match is known."""
    known = {k: v for k, v in c.scores.model_dump().items() if v is not None}
    if "data_match" not in known:
        return None
    value = sum(FIT_WEIGHTS[k] * v for k, v in known.items()) / sum(FIT_WEIGHTS[k] for k in known)
    if must_failures(c, brief):
        value = min(value, MUST_HAVE_CAP)
    if c.holder_type == "technology":
        value = min(value, TECHNOLOGY_CAP)
    if c.research and c.research.licence == INTERNAL_ONLY:
        value = min(value, INTERNAL_ONLY_CAP)
    return round(value, 2)


def rescore(c: Candidate, brief: Brief, volume_signal: int | None = None) -> None:
    """Recompute coverage, licence, volume and fit (in place). Research, where done, replaces the
    licence prior and the screen's volume signal; a research result with no verified evidence
    leaves them as they were. Categories set to Any count for nothing."""
    c.scores.coverage = coverage(brief, c.languages, c.regions)
    c.scores.licence = licence_prior(c, brief)
    if volume_signal is not None:
        c.scores.volume = clamp(volume_signal)
    if c.research:
        checked = licence_from_found(c.research.licence, brief)
        if checked is not None:
            c.scores.licence = checked
        if c.research.volume_score is not None:
            c.scores.volume = clamp(c.research.volume_score)
    if brief.importance("licence") == "any" and not (c.research and c.research.licence == INTERNAL_ONLY):
        c.scores.licence = None
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
