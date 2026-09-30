"""Screen: homepage + the page search found the company on, one Flash call per batch -> the
holder type (vendor / public / by-product / technology / unrelated), who owns the data, how
well it matches the brief (1 to 5), evidence for the buyer's languages and regions, and signs
of scale. Scores and fit are then computed in Python (see scoring.py).

Rules the model doesn't apply reliably are enforced afterwards (see _apply): only the
buyer's languages and regions count, and a "vendor" whose data belongs to its customers is a
service, so a by-product holder. Unrelated companies and dead websites are excluded.
"""

import socket
from collections.abc import Callable
from urllib.parse import urlparse

import httpx
import trafilatura
from pydantic import BaseModel, Field

from worker import llm
from worker.config import (
    MODEL,
    PARALLEL_FETCHES,
    PARALLEL_LLM_CALLS,
    SCREEN_BATCH_SIZE,
    SCREEN_TEXT_CHARS,
)
from worker.crawl import fetch_homepage_text, fetch_html_quick, same_site
from worker.discovery.channels import GROUNDING_REDIRECT, mention_at
from worker.llm import QuotaExceeded
from worker.parallel import run_parallel
from worker.schemas import Brief, Candidate, DataOwner, HolderType, Scores
from worker.scoring import clamp, rescore


class ScreenItem(BaseModel):
    domain: str
    holder_type: HolderType
    data_owner: DataOwner = Field(description="Who controls the data this company holds")
    data_match: int = Field(description="1 to 5: how closely its data matches the buyer's")
    languages: list[str] = Field(description="The buyer's languages it shows evidence of working in")
    regions: list[str] = Field(description="The buyer's regions it shows evidence of working in")
    volume_signal: int | None = Field(description="1 to 5 from signs of scale; null if none")
    reason: str = Field(description="One line: what data it holds and why it matches or not")


class ScreenBatch(BaseModel):
    results: list[ScreenItem]


PROMPT = """You are screening companies for a data buyer. The buyer wants:
- data type: {data_type}
- features: {features}
- languages: {languages}
- regions: {regions}
- scenarios: {scenarios}
- labels: {labels}
- volume: {volume}

Below are excerpts from several organisations' homepages and, when it's another page, from
the page a web search found them on. Read both: the source page often shows the line of
business that matters when the homepage leads with something else (e.g. a translation
company's speech-data collection service). Claims on another site's page are weaker than
the organisation's own pages.

For each one, decide what kind of holder it is with respect to this data, who owns the data
it holds, how well its data matches, which of the buyer's languages and regions it works in,
and how much data it is likely to hold.

holder_type:
- "vendor": sells or licenses this kind of data that it owns, or collects it to order as its
  main business (dataset vendors, data collection companies). A vendor's data is its own:
  an agency that transcribes, translates, interprets or records its clients' material is
  "by_product", not "vendor".
- "public": a public body, research institute, university or non-profit repository that
  records, publishes or distributes this kind of data (e.g. corpora, parliament or council
  session recordings, broadcast archives).
- "by_product": its product or service captures this kind of data in normal operation,
  even though it doesn't sell data (including agencies working on their clients' data).
- "technology": makes hardware, software or tools that work with this kind of data (e.g.
  sensors, devices, tracking or analysis software) but doesn't itself capture or keep it at
  scale. At most it holds internal R&D or training data.
- "unrelated": none of the above, including companies that capture a different kind of data
  from what the buyer wants, and sites that only list or write about others (a directory,
  news site, consultancy, marketplace).

data_owner:
- "company": the organisation recorded or collected the data itself (its own staff,
  contractors or devices), so it could license it. Always the case for a vendor.
- "customers": the data is recorded by or for its customers, who own it; licensing it would
  need their permission.
- "unclear": can't tell from these pages.

data_match (1 to 5):
- 5: its core business clearly involves this kind of data, with the buyer's features and
  scenarios (e.g. multi-speaker meetings, not single-speaker dictation).
- 4: clearly this kind of data, but only some of the features or scenarios.
- 3: adjacent data (e.g. phone calls when the buyer wants meetings), or plausible but
  unclear from these pages.
- 2: a small side line, or a technology company that may hold some internal data.
- 1: a different kind of data, or none.

languages: which of the buyer's languages the organisation shows evidence of working in: its
pages are written in the language, it names the language as supported or offered, it is
based in or serves a country where the language is spoken, or it holds data in it. A
generic claim like "100+ languages" is not evidence. Use the buyer's spelling; an empty
list if there's no evidence or the buyer named no languages.

regions: which of the buyer's regions the organisation shows evidence of working in: based
there, offices or customers there, or data recorded there. Use the buyer's spelling; an
empty list if there's no evidence or the buyer named no regions.

volume_signal (1 to 5, or null): signs of scale on these pages, such as customer or user
counts, hours or calls processed, or a stated dataset size, judged against the buyer's
volume. 5: clearly far more than the buyer needs; 3: substantial; 1: tiny. null if the
pages give no sign of scale.

Return one result per organisation, using the domain exactly as given, with a one-line reason.

{companies}
"""

Fetched = tuple[Candidate, str, str | None]  # candidate, homepage text, source-page excerpt


def _is_homepage(url: str, website: str) -> bool:
    return same_site(url, website) and urlparse(url).path.strip("/") == ""


def source_excerpt(c: Candidate, fetch: Callable[[str], str] = fetch_html_quick) -> str | None:
    """Text from the page search found the company on, headed with where it came from; None if
    that's the homepage or no source page can be read.

    Prefers a page on the company's own site (its opening text). From another site it takes
    only the passage that mentions the company: a page that doesn't mention it says nothing
    about it (and suggests the model made the source up).
    """
    urls = [u for u in c.source_urls
            if u.startswith("http") and GROUNDING_REDIRECT not in u and not _is_homepage(u, c.website)]
    for url in sorted(urls, key=lambda u: not same_site(u, c.website)):
        try:
            text = trafilatura.extract(fetch(url)) or ""
        except Exception:  # dead, blocked or slow: try the next one
            continue
        if same_site(url, c.website):
            if text:
                return f"Source page on its own site ({url}):\n{text[:SCREEN_TEXT_CHARS]}"
        elif (i := mention_at(text, c.name, c.website)) >= 0:
            start = max(0, i - SCREEN_TEXT_CHARS // 5)
            return f"Source page on another site ({url}), passage mentioning it:\n" \
                   f"{text[start:start + SCREEN_TEXT_CHARS]}"
    return None


def _screen_batch(brief: Brief, batch: list[Fetched]) -> dict[str, ScreenItem]:
    companies = "\n\n".join(
        f"### {c.domain} ({c.name})\nHomepage:\n{home[:SCREEN_TEXT_CHARS]}" + (f"\n\n{src}" if src else "")
        for c, home, src in batch
    )
    prompt = PROMPT.format(
        data_type=brief.data_type,
        features=", ".join(brief.features) or "any",
        languages=", ".join(brief.languages) or "any",
        regions=", ".join(brief.regions) or "any",
        scenarios=", ".join(brief.scenarios) or "any",
        labels=", ".join(brief.label_types) or "none",
        volume=str(brief.volume) or "not stated",
        companies=companies,
    )
    result = llm.ask(prompt, ScreenBatch, model=MODEL)
    return {item.domain.lower(): item for item in result.results}


def _buyers(wanted: list[str], said: list[str]) -> list[str]:
    """The buyer's values the model reported, in the buyer's spelling."""
    said = {x.strip().lower() for x in said}
    return [w for w in wanted if w.lower() in said]


def _apply(brief: Brief, c: Candidate, item: ScreenItem) -> None:
    """Copy the model's answer onto the candidate, enforce the rules it applies unevenly, then
    score it."""
    c.reason = item.reason
    c.holder_type, c.data_owner = item.holder_type, item.data_owner
    c.languages = _buyers(brief.languages, item.languages)
    c.regions = _buyers(brief.regions, item.regions)
    if c.holder_type == "vendor" and c.data_owner == "customers":
        c.holder_type = "by_product"  # works on its clients' data: a service, not a data seller
    c.excluded = c.holder_type == "unrelated"
    c.scores = Scores(data_match=clamp(item.data_match), volume=clamp(item.volume_signal))
    rescore(c, brief)


def is_dead_site(e: Exception) -> bool:
    """The domain doesn't exist (DNS lookup failed) or the URL is invalid: usually a website the
    model made up. Refused/reset connections, TLS errors, 403s and timeouts are different —
    real companies do those to crawlers — so they are kept, unscored."""
    if isinstance(e, (httpx.UnsupportedProtocol, httpx.InvalidURL)):
        return True
    while e is not None:
        if isinstance(e, socket.gaierror):
            return True
        e = e.__cause__ or e.__context__
    return False


def screen(
    brief: Brief,
    candidates: list[Candidate],
    fetch: Callable[[str], str] | None = None,
    progress: Callable[[str], None] = print,
    source: Callable[[Candidate], str | None] = source_excerpt,
) -> list[Candidate]:
    """Screen and score every candidate not screened yet (in place) and return all of them.

    Raises QuotaExceeded after keeping the results it did get.
    """
    fetch = fetch or fetch_homepage_text
    todo = [c for c in candidates if not c.screened]
    if not todo:
        return candidates

    def try_fetch(c: Candidate) -> Fetched | None:
        """Homepage text and source excerpt, or None with the failure recorded on the candidate."""
        try:
            home = fetch(c.website)
        except Exception as e:
            if is_dead_site(e):
                c.reason, c.excluded = "dead website: domain doesn't exist", True
            else:  # can't judge without a homepage; keep it (fit unknown) rather than lose it
                c.reason = f"homepage unreachable ({type(e).__name__})"
            return None
        return c, home, source(c)

    progress(f"Fetching {len(todo)} homepages and source pages")
    fetched = dict(run_parallel(
        [(c.domain, lambda c=c: try_fetch(c)) for c in todo], PARALLEL_FETCHES, lambda _: None
    ).results)
    reachable = [f for c in todo if (f := fetched.get(c.domain)) and f[1]]

    batches = [reachable[i : i + SCREEN_BATCH_SIZE] for i in range(0, len(reachable), SCREEN_BATCH_SIZE)]
    screened = run_parallel(
        [(f"Screening {i * SCREEN_BATCH_SIZE + 1}–{i * SCREEN_BATCH_SIZE + len(b)} of {len(reachable)}",
          lambda b=b: (b, _screen_batch(brief, b))) for i, b in enumerate(batches)],
        PARALLEL_LLM_CALLS,
        progress,
    )
    for _, (batch, items) in screened.results:
        for c, _, _ in batch:
            item = items.get(c.domain)
            if item:
                _apply(brief, c, item)
            else:
                c.reason = "not screened by the model"
    if screened.quota_error:
        raise QuotaExceeded(screened.quota_error)
    return candidates

