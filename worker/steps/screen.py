"""Relevance screen: homepage + the page search found the company on, one Flash call per batch
-> yes / maybe / no, plus the holder type (vendor / public / by-product / technology /
unrelated), who owns the data, and which of the buyer's languages it shows evidence for.

Rules the model doesn't apply reliably are enforced afterwards (see _apply): no evidence
for the buyer's languages or a technology company caps "yes" at "maybe", and a "vendor"
whose data belongs to its customers is a service, so a by-product holder.

Only yes and maybe go on to the deep crawl.
Run: uv run python -m worker.steps.screen [run_dir]   (needs spec.json + discovery.json)
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
from worker.schemas import Candidate, DataOwner, DataSpec, HolderType, Relevance


class ScreenItem(BaseModel):
    domain: str
    holder_type: HolderType
    data_owner: DataOwner = Field(description="Who controls the data this company holds")
    languages: list[str] = Field(description="The buyer's languages it shows evidence of working in")
    label: Relevance
    reason: str = Field(description="One line explaining the label")


class ScreenBatch(BaseModel):
    results: list[ScreenItem]


PROMPT = """You are screening companies for a data buyer. The buyer wants:
- data type: {data_type}
- languages: {languages}
- scenarios: {scenarios}
- labels: {labels}

Below are excerpts from several organisations' homepages and, when it's another page, from
the page a web search found them on. Read both: the source page often shows the line of
business that matters when the homepage leads with something else (e.g. a translation
company's speech-data collection service). Claims on another site's page are weaker than
the organisation's own pages.

For each one, decide what kind of holder it is with respect to this data, who owns the data
it holds, which of the buyer's languages it works in, and whether the buyer should contact it.

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

languages: which of the buyer's languages the organisation shows evidence of working in: its
pages are written in the language, it names the language as supported or offered, it is
based in or serves a country where the language is spoken, or it holds data in it. A
generic claim like "100+ languages" is not evidence. Use the buyer's spelling; an empty
list if there's no evidence or the buyer named no languages.

label:
- "yes": a vendor, public or by-product holder whose core product, service or collection
  clearly involves this kind of data and, if the buyer named languages, with evidence for
  at least one of them.
- "maybe": plausible but unclear from these pages (e.g. an adjacent product, or too little
  text), a technology company that may hold internal data, or a match with no evidence for
  the buyer's languages; say which data in the reason.
- "no": unrelated.

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


def _label_batch(spec: DataSpec, batch: list[Fetched]) -> dict[str, ScreenItem]:
    companies = "\n\n".join(
        f"### {c.domain} ({c.name})\nHomepage:\n{home[:SCREEN_TEXT_CHARS]}" + (f"\n\n{src}" if src else "")
        for c, home, src in batch
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


def _apply(spec: DataSpec, c: Candidate, item: ScreenItem) -> None:
    """Copy the model's labels onto the candidate, then enforce the rules it applies unevenly."""
    said = {lang.strip().lower() for lang in item.languages}
    c.relevance, c.relevance_reason = item.label, item.reason
    c.holder_type, c.data_owner = item.holder_type, item.data_owner
    c.languages = [lang for lang in spec.languages if lang.lower() in said]  # buyer's spelling
    if c.holder_type == "vendor" and c.data_owner == "customers":
        c.holder_type = "by_product"  # works on its clients' data: a service, not a data seller
    if c.relevance == "yes" and c.holder_type == "technology":
        c.relevance = "maybe"  # sells tools, not footage: worth a question at most
    if c.relevance == "yes" and spec.languages and not c.languages:
        c.relevance = "maybe"
        c.relevance_reason = f"{c.relevance_reason} (no evidence of the buyer's languages)"


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
    source: Callable[[Candidate], str | None] = source_excerpt,
) -> list[Candidate]:
    """Label every candidate that has no relevance yet (in place) and return all of them.

    Raises QuotaExceeded after saving the labels it did get.
    """
    fetch = fetch or fetch_homepage_text
    todo = [c for c in candidates if c.relevance is None]
    if not todo:
        return candidates

    def try_fetch(c: Candidate) -> Fetched | None:
        """Homepage text and source excerpt, or None with the failure recorded on the candidate."""
        try:
            home = fetch(c.website)
        except Exception as e:
            if is_dead_site(e):
                c.relevance, c.relevance_reason = "no", "dead website: domain doesn't exist"
            else:  # can't judge without a homepage; keep it rather than lose a real company
                c.relevance, c.relevance_reason = "maybe", f"homepage unreachable ({type(e).__name__})"
            return None
        return c, home, source(c)

    progress(f"Fetching {len(todo)} homepages and source pages")
    fetched = dict(run_parallel(
        [(c.domain, lambda c=c: try_fetch(c)) for c in todo], PARALLEL_FETCHES, lambda _: None
    ).results)
    reachable = [f for c in todo if (f := fetched.get(c.domain)) and f[1]]

    batches = [reachable[i : i + SCREEN_BATCH_SIZE] for i in range(0, len(reachable), SCREEN_BATCH_SIZE)]
    labelled = run_parallel(
        [(f"Screening {i * SCREEN_BATCH_SIZE + 1}–{i * SCREEN_BATCH_SIZE + len(b)} of {len(reachable)}",
          lambda b=b: (b, _label_batch(spec, b))) for i, b in enumerate(batches)],
        PARALLEL_LLM_CALLS,
        progress,
    )
    for _, (batch, labels) in labelled.results:
        for c, _, _ in batch:
            item = labels.get(c.domain)
            if item:
                _apply(spec, c, item)
            else:
                c.relevance, c.relevance_reason = "maybe", "not labelled by the model"
    if labelled.quota_error:
        raise QuotaExceeded(labelled.quota_error)
    return candidates


RELEVANCE_ORDER = {"yes": 0, "maybe": 1, "no": 2, None: 3}
HOLDER_ORDER = {"vendor": 0, "public": 1, "by_product": 2, "technology": 3, "unrelated": 4, None: 5}


def rank(candidates: list[Candidate]) -> list[Candidate]:
    """yes, maybe, no, unscreened; within each, vendors, public sources, by-product holders,
    then technology.
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
