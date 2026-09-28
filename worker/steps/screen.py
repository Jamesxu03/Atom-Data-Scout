"""Relevance screen: homepage only + one Flash call per batch -> yes / maybe / no.

Only yes and maybe go on to the deep crawl.
Run: uv run python -m worker.steps.screen [run_dir]   (needs spec.json + discovery.json)
"""

from collections.abc import Callable

from pydantic import BaseModel, Field

from worker import llm
from worker.config import MODEL_FLASH, SCREEN_BATCH_SIZE, SCREEN_TEXT_CHARS
from worker.crawl import fetch_homepage_text
from worker.schemas import Candidate, DataSpec, Relevance


class ScreenItem(BaseModel):
    domain: str
    label: Relevance
    reason: str = Field(description="One line explaining the label")


class ScreenBatch(BaseModel):
    results: list[ScreenItem]


PROMPT = """You are screening companies for a data buyer. The buyer wants:
- data type: {data_type}
- languages: {languages}
- scenarios: {scenarios}
- labels: {labels}

Below are homepage excerpts from several companies. For each one, decide whether its
product or service would plausibly generate or hold this kind of data as part of running
the business (it does not need to sell data).

- "yes": the core product clearly captures this kind of data.
- "maybe": plausible but unclear from the homepage (e.g. an adjacent product, or too little text).
- "no": clearly unrelated, or not a company with its own product (e.g. a directory, news
  site, consultancy blog, marketplace).

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
    result = llm.ask(prompt, ScreenBatch, model=MODEL_FLASH)
    return {item.domain.lower(): item for item in result.results}


def screen(
    spec: DataSpec,
    candidates: list[Candidate],
    fetch: Callable[[str], str] | None = None,
    progress: Callable[[str], None] = print,
) -> list[Candidate]:
    """Set relevance + relevance_reason on each candidate (in place) and return them."""
    fetch = fetch or fetch_homepage_text
    reachable: list[tuple[Candidate, str]] = []
    for c in candidates:
        try:
            reachable.append((c, fetch(c.website)))
        except Exception as e:
            # Can't judge without a homepage; keep it rather than lose a real company to a bot block
            c.relevance, c.relevance_reason = "maybe", f"homepage unreachable ({type(e).__name__})"

    for i in range(0, len(reachable), SCREEN_BATCH_SIZE):
        batch = reachable[i : i + SCREEN_BATCH_SIZE]
        progress(f"Screening {i + 1}–{i + len(batch)} of {len(reachable)}")
        labels = _label_batch(spec, batch)
        for c, _ in batch:
            item = labels.get(c.domain)
            if item:
                c.relevance, c.relevance_reason = item.label, item.reason
            else:
                c.relevance, c.relevance_reason = "maybe", "not labelled by the model"
    return candidates


def relevant(candidates: list[Candidate]) -> list[Candidate]:
    """yes before maybe; no dropped."""
    order = {"yes": 0, "maybe": 1}
    return sorted((c for c in candidates if c.relevance in order), key=lambda c: order[c.relevance])


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
        print(f"  {c.relevance:5} {c.domain:30} {c.relevance_reason}")
    print("saved", runs.save(run_dir, "discovery", d))
