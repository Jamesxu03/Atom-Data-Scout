"""Relevance screen: homepage only + one Flash call per batch -> yes / maybe / no, plus the
holder type (vendor / by-product / technology / unrelated) and who owns the data.

Only yes and maybe go on to the deep crawl.
Run: uv run python -m worker.steps.screen [run_dir]   (needs spec.json + discovery.json)
"""

import socket
from collections.abc import Callable

import httpx
from pydantic import BaseModel, Field

from worker import llm
from worker.config import (
    MODEL,
    PARALLEL_FETCHES,
    PARALLEL_LLM_CALLS,
    SCREEN_BATCH_SIZE,
    SCREEN_TEXT_CHARS,
)
from worker.crawl import fetch_homepage_text
from worker.llm import QuotaExceeded
from worker.parallel import run_parallel
from worker.schemas import Candidate, DataOwner, DataSpec, HolderType, Relevance


class ScreenItem(BaseModel):
    domain: str
    holder_type: HolderType
    data_owner: DataOwner = Field(description="Who controls the data this company holds")
    label: Relevance
    reason: str = Field(description="One line explaining the label")


class ScreenBatch(BaseModel):
    results: list[ScreenItem]


PROMPT = """You are screening companies for a data buyer. The buyer wants:
- data type: {data_type}
- languages: {languages}
- scenarios: {scenarios}
- labels: {labels}

Below are homepage excerpts from several companies. For each one, decide what kind of
company it is with respect to this data, who owns the data it holds, and whether the buyer
should contact it.

holder_type:
- "vendor": collects, produces or sells this kind of data as its business (dataset
  vendors, data collection or annotation services).
- "by_product": its product or service captures this kind of data in normal operation,
  even though it doesn't sell data.
- "technology": makes hardware, software or tools that work with this kind of data (e.g.
  sensors, devices, tracking or analysis software) but doesn't itself capture or keep it at
  scale. At most it holds internal R&D or training data.
- "unrelated": none of the above, including companies that capture a different kind of data
  from what the buyer wants, and anything that isn't a company with its own product (a
  directory, news site, consultancy, marketplace).

data_owner:
- "company": the company recorded or collected the data itself (its own staff, contractors
  or devices), so it could license it.
- "customers": the data is recorded by or for its customers, who own it; licensing it would
  need their permission.
- "unclear": can't tell from the homepage.

label:
- "yes": a vendor or by-product holder whose core product clearly captures this kind of data.
- "maybe": plausible but unclear from the homepage (e.g. an adjacent product, or too little
  text), or a technology company that may hold internal data; say which data in the reason.
- "no": unrelated.

Return one result per company, using the domain exactly as given, with a one-line reason.

{companies}
"""


def _label_batch(spec: DataSpec, batch: list[tuple[Candidate, str]]) -> dict[str, ScreenItem]:
    companies = "\n\n".join(
        f"### {c.domain} ({c.name})\n{text[:SCREEN_TEXT_CHARS]}" for c, text in batch
    )
    prompt = PROMPT.format(
        data_type=spec.data_type,
        languages=", ".join(spec.languages) or "any",
        scenarios=", ".join(spec.scenarios) or "any",
        labels=", ".join(spec.label_types) or "none",
        companies=companies,
    )
    result = llm.ask(prompt, ScreenBatch, model=MODEL)
    return {item.domain.lower(): item for item in result.results}


def is_dead_site(e: Exception) -> bool:
    """The domain doesn't exist (DNS lookup failed) or the URL is invalid: usually a website the
    model made up. Refused/reset connections, TLS errors, 403s and timeouts are different —
    real companies do those to crawlers — so they stay "maybe"."""
    if isinstance(e, (httpx.UnsupportedProtocol, httpx.InvalidURL)):
        return True
    while e is not None:
        if isinstance(e, socket.gaierror):
            return True
        e = e.__cause__ or e.__context__
    return False


def screen(
    spec: DataSpec,
    candidates: list[Candidate],
    fetch: Callable[[str], str] | None = None,
    progress: Callable[[str], None] = print,
) -> list[Candidate]:
    """Label every candidate that has no relevance yet (in place) and return all of them.

    Raises QuotaExceeded after saving the labels it did get.
    """
    fetch = fetch or fetch_homepage_text
    todo = [c for c in candidates if c.relevance is None]
    if not todo:
        return candidates

    def try_fetch(c: Candidate) -> str | None:
        """Homepage text, or None with the failure recorded on the candidate."""
        try:
            return fetch(c.website)
        except Exception as e:
            if is_dead_site(e):
                c.relevance, c.relevance_reason = "no", "dead website: domain doesn't exist"
            else:  # can't judge without a homepage; keep it rather than lose a real company
                c.relevance, c.relevance_reason = "maybe", f"homepage unreachable ({type(e).__name__})"
        return None

    progress(f"Fetching {len(todo)} homepages")
    fetched = dict(run_parallel(
        [(c.domain, lambda c=c: try_fetch(c)) for c in todo], PARALLEL_FETCHES, lambda _: None
    ).results)
    reachable = [(c, fetched[c.domain]) for c in todo if fetched.get(c.domain)]

    batches = [reachable[i : i + SCREEN_BATCH_SIZE] for i in range(0, len(reachable), SCREEN_BATCH_SIZE)]
    labelled = run_parallel(
        [(f"Screening {i * SCREEN_BATCH_SIZE + 1}–{i * SCREEN_BATCH_SIZE + len(b)} of {len(reachable)}",
          lambda b=b: (b, _label_batch(spec, b))) for i, b in enumerate(batches)],
        PARALLEL_LLM_CALLS,
        progress,
    )
    for _, (batch, labels) in labelled.results:
        for c, _ in batch:
            item = labels.get(c.domain)
            if item:
                c.relevance, c.relevance_reason = item.label, item.reason
                c.holder_type, c.data_owner = item.holder_type, item.data_owner
                if c.holder_type == "technology" and c.relevance == "yes":
                    c.relevance = "maybe"  # sells tools, not footage: worth a question at most
            else:
                c.relevance, c.relevance_reason = "maybe", "not labelled by the model"
    if labelled.quota_error:
        raise QuotaExceeded(labelled.quota_error)
    return candidates


RELEVANCE_ORDER = {"yes": 0, "maybe": 1, "no": 2, None: 3}
HOLDER_ORDER = {"vendor": 0, "by_product": 1, "technology": 2, "unrelated": 3, None: 4}


def rank(candidates: list[Candidate]) -> list[Candidate]:
    """yes, maybe, no, unscreened; within each, vendors, then by-product holders, then technology.
    Corroboration (more channels, hypotheses and sources) only breaks ties: it favours famous
    companies, which are the least likely to sell their customers' data."""
    return sorted(candidates, key=lambda c: (
        RELEVANCE_ORDER[c.relevance], HOLDER_ORDER[c.holder_type], tuple(-int(x) for x in c.corroboration()),
    ))


def relevant(candidates: list[Candidate]) -> list[Candidate]:
    """yes and maybe, ranked; no and unscreened dropped."""
    return [c for c in rank(candidates) if c.relevance in ("yes", "maybe")]


if __name__ == "__main__":
    import sys
    from pathlib import Path

    from worker import runs
    from worker.discovery import Discovery

    run_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else runs.latest_run()
    spec = runs.load(run_dir, "spec", DataSpec)
    d = runs.load(run_dir, "discovery", Discovery)
    screen(spec, d.candidates)
    for c in d.candidates:
        print(f"  {c.relevance:5} {c.holder_type or '-':10} {c.domain:30} {c.relevance_reason}")
    print("saved", runs.save(run_dir, "discovery", d))
