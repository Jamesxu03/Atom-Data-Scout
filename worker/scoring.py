"""Weighted total score — computed in Python, never by the LLM."""

from worker.config import SCORE_WEIGHTS
from worker.schemas import Score


def compute_total(score: Score) -> float:
    total = sum(getattr(score, dim) * w for dim, w in SCORE_WEIGHTS.items())
    return round(total, 2)
