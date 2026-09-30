"""Scores and fit, computed in Python. The model only supplies data_match and a volume signal
(screen) and a licence status and volume score (research); everything else is a rule here."""

from worker.config import (
    FIT_WEIGHTS,
    INTERNAL_ONLY_CAP,
    MUST_HAVE_CAP,
    RELEVANT_FIT,
    TECHNOLOGY_CAP,
)
from worker.schemas import Brief, Candidate, DataOwner, HolderType, LicenceStatus


def clamp(score: int | None) -> int | None:
    return None if score is None else max(1, min(5, int(score)))


def coverage(brief: Brief, languages: list[str], regions: list[str]) -> int | None:
    """5 if there is evidence for every dimension the brief names (languages, regions), 3 if for
    only one of two, 1 if none; None if the brief names neither."""
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


def licence_prior(holder_type: HolderType | None, data_owner: DataOwner | None) -> int | None:
    return _prior(holder_type, data_owner)[0]


def prior_label(holder_type: HolderType | None, data_owner: DataOwner | None) -> str:
    return _prior(holder_type, data_owner)[1]


def licence_from_status(status: LicenceStatus, brief: Brief) -> int | None:
    """After research. open_restricted is fine when the buyer only trains models."""
    if status == "open_restricted":
        return 4 if brief.licence == "training_only" else 2
    return {"sellable": 5, "open": 5, "needs_consent": 2, "internal_only": 1}.get(status)


def fit(c: Candidate, brief: Brief) -> float | None:
    """Weighted mean over the known scores, then caps. None until data_match is known."""
    known = {k: v for k, v in c.scores.model_dump().items() if v is not None}
    if "data_match" not in known:
        return None
    value = sum(FIT_WEIGHTS[k] * v for k, v in known.items()) / sum(FIT_WEIGHTS[k] for k in known)
    missing = [
        field for field, wanted, found in
        (("languages", brief.languages, c.languages), ("regions", brief.regions, c.regions))
        if field in brief.must_have and wanted and not found
    ]
    if missing:
        value = min(value, MUST_HAVE_CAP)
    if c.holder_type == "technology":
        value = min(value, TECHNOLOGY_CAP)
    if c.research and c.research.licence_status == "internal_only":
        value = min(value, INTERNAL_ONLY_CAP)
    return round(value, 2)


def rescore(c: Candidate, brief: Brief) -> None:
    """Recompute coverage, licence and fit (in place). Research, where done, replaces the
    licence prior and the screen's volume signal; a research result with no verified evidence
    leaves them as they were."""
    c.scores.coverage = coverage(brief, c.languages, c.regions)
    c.scores.licence = licence_prior(c.holder_type, c.data_owner)
    if c.research:
        checked = licence_from_status(c.research.licence_status, brief)
        if checked is not None:
            c.scores.licence = checked
        if c.research.volume_score is not None:
            c.scores.volume = clamp(c.research.volume_score)
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
