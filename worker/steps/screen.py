"""Screen: homepage + the page search found the company on, one Flash call per batch -> the
holder type (ready source / untapped holder, or unrelated), who owns the data, how well it
matches the brief (1 to 5), evidence for the buyer's languages and regions, signs of scale, any
price, founding year or data period the pages state (kept only with a quote found on the page),
and for untapped holders initial answers to the three saleability questions (does the data
exist, is selling it allowed, are they willing), the main obstacle and how to buy it anyway.
Scores and fit are then computed in Python (see scoring.py); research later checks the answers
against news and the company's own policies.

Rules the model doesn't apply reliably are enforced afterwards (see _apply): only the
buyer's languages and regions count, only must-have categories can be contradicted, a ready
source whose data belongs to its customers is a service, so an untapped holder, and saleability
applies to untapped holders only. Unrelated companies, dead websites and companies a must-have
filter rules out (E1 source types, D3 customers' data) are excluded.
"""

import re
import socket
from collections.abc import Callable
from typing import Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from worker import llm
from worker.brief_template import CATEGORIES, CUSTOMER_EXCLUDE, Brief
from worker.config import (
    MODEL,
    PARALLEL_FETCHES,
    PARALLEL_LLM_CALLS,
    SCREEN_BATCH_SIZE,
    SCREEN_TEXT_CHARS,
)
from worker.crawl import extract_text, fetch_homepage_text, fetch_html_quick, same_site
from worker.discovery.channels import GROUNDING_REDIRECT, mention_at
from worker.llm import QuotaExceeded
from worker.parallel import run_parallel
from worker.schemas import FACTS, Answer, Candidate, DataOwner, Fact, Saleability, Scores
from worker.scoring import rescore
from worker.verify import MIN_FACT_QUOTE_WORDS, quote_in_text, value_in_quote


class Stated(BaseModel):
    value: str
    quote: str = Field(description="The sentence on these pages that states it, word for word")


class ScreenItem(BaseModel):
    domain: str
    holder_type: Literal["ready", "untapped", "unrelated"]
    data_owner: DataOwner = Field(description="Who controls the data this company holds")
    collector: bool = Field(default=False, description="Ready source that only records to order")
    vendor: bool = Field(default=False, description="Untapped only: makes the tool; its customers hold the data")
    exists: int | None = Field(default=None, description="Untapped only: 1 to 5, null if the pages don't say")
    allowed: int | None = Field(default=None, description="Untapped only: 1 to 5, null if the pages don't say")
    willing: int | None = Field(default=None, description="Untapped only: 1 to 5, null if the pages don't say")
    obstacle: str | None = Field(default=None, description="Untapped only: the main obstacle, one line")
    path: str | None = Field(default=None, description="Untapped only: how it could still be bought")
    data_match: int = Field(description="1 to 5: how closely its data matches the buyer's")
    languages: list[str] = Field(description="The buyer's languages it shows evidence of working in")
    regions: list[str] = Field(description="The buyer's regions it shows evidence of working in")
    volume_signal: int | None = Field(description="1 to 5 from signs of scale; null if none")
    contradicts: list[str] = Field(default=[], description="Keys of must-have requirements these pages clearly contradict")
    price: Stated | None = None
    founded: Stated | None = None
    data_period: Stated | None = None
    reason: str = Field(description="One line: what data it holds and why it matches or not")


class ScreenBatch(BaseModel):
    results: list[ScreenItem]


PROMPT = """You are screening companies for a data buyer. The buyer wants:
{brief}

Languages: {languages}
Regions: {regions}
Volume: {volume}

Must-have requirements, by key:
{musts}

Below are excerpts from several organisations' homepages and, when it's another page, from
the page a web search found them on. Read both: the source page often shows the line of
business that matters when the homepage leads with something else (e.g. a translation
company's speech-data collection service). Claims on another site's page are weaker than
the organisation's own pages.

For each one, decide what kind of holder it is with respect to this data, who owns the data
it holds, how well its data matches, which of the buyer's languages and regions it works in,
and how much data it is likely to hold.

holder_type:
- "ready": the data is already offered: it sells or licenses this kind of data (a dataset
  catalogue, off-the-shelf datasets, a marketplace listing), publishes it (a public body,
  research institute or repository: corpora, parliament or broadcast archives), or records
  it to a client's specification as a service (a data collector). Its data is its own.
- "untapped": holds this kind of data, or could, but doesn't sell it: its product or service
  records it, the law makes it keep it, it has production leftovers, it closed or was
  acquired, its devices record it, and so on. Includes agencies that transcribe, translate,
  interpret or record their clients' material, and makers of tools or hardware for this kind
  of data, who at most hold internal R&D data.
- "unrelated": neither, including companies that capture a different kind of data from what
  the buyer wants, and sites that only list or write about others (a directory, news site,
  consultancy, marketplace).

data_owner:
- "company": the organisation recorded or collected the data itself (its own staff,
  contractors or devices), so it could license it. Always the case for a ready source.
- "customers": the data is recorded by or for its customers, who own it; licensing it would
  need their permission.
- "unclear": can't tell from these pages.

collector: true for a ready source that records data to order and shows no existing data.

vendor (untapped only): true if its product is the software, platform or hardware that
records this kind of data (e.g. a meeting recorder or notetaker app, call-recording or
conversation-intelligence software, a research or deposition platform, a dashcam maker), so
its customers do the recording. False for organisations that record as part of their own
work, even for clients: a focus-group facility, court-reporting firm, conference or
earnings-call host, webcasting crew, broadcaster or transcription agency.

For untapped holders only (null for others), answer three questions from these pages, each 1
to 5, or null when the pages don't say:
- exists: is the data still there? 5: kept long term, an archive; 1: deleted within 30 days,
  or it doesn't record at all. How much there is goes in volume_signal, not here.
- allowed: may it be sold? 5: the company recorded it itself and its terms allow other uses;
  1: copyright belongs to someone else, terms say recordings are only for quality monitoring
  and never shared, or it is health data or recordings kept for financial regulation.
- willing: would they sell? 5: insolvent, shutting down, selling assets, or has done data
  deals with AI companies; 1: a big brand, or a strictly regulated institution.
obstacle: the main thing in the way, in one line (e.g. "terms say recordings are only used for
quality monitoring"). path: how it could still be bought, if there is a way (e.g. "needs its
customers' consent", "contact the administrators"); null if none.

data_match (1 to 5):
- 5: its core business clearly involves this kind of data, with the buyer's must-have and
  nice-to-have details (e.g. multi-speaker meetings, not single-speaker dictation).
- 4: clearly this kind of data, but only some of those details.
- 3: adjacent data (e.g. phone calls when the buyer wants meetings), or plausible but
  unclear from these pages.
- 2: a small side line, or a tool or hardware maker that may hold some internal data.
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
counts, hours or calls processed, or a stated dataset size, judged against the volume
wanted from one seller. For a collector, judge its capacity instead: contributor network
size, languages covered, past project sizes. 5: clearly more than that; 3: substantial; 1:
tiny. null if the pages give no sign of scale.

contradicts: keys from the must-have list that these pages clearly contradict, e.g.
"modality" for a company that only handles text when audio is a must-have. Leave it empty
when the pages just don't say.

price, founded, data_period: only when these pages state them; otherwise null.
- price: the price of this data, with currency and unit (e.g. "$30 per audio hour"), or
  "On request" if the pages say pricing is on request.
- founded: the year the organisation was founded.
- data_period: when its data was recorded or collected, e.g. "2018 to 2024" or "since 2015".
value is the short answer; quote is the sentence that states it, copied word for word from
these pages. Answers whose quote is not on the pages are thrown away.

Return one result per organisation, using the domain exactly as given, with a one-line reason.

{companies}
"""

Fetched = tuple[Candidate, str, str | None]  # candidate, homepage text ("" if blocked), source-page excerpt
NO_HOMEPAGE = "(couldn't be read: judge from the source page below only)"


def unreachable_reason(e: Exception) -> str:
    """Why the homepage couldn't be read, in words."""
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        return (f"homepage blocks automated access (HTTP {code})" if code in (401, 403, 405, 429, 503)
                else f"homepage returned HTTP {code}")
    if isinstance(e, httpx.TimeoutException):
        return "homepage timed out"
    return f"homepage couldn't be read ({type(e).__name__})"


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
            text = extract_text(fetch(url))
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
        f"### {c.domain} ({c.name})\nHomepage:\n{home[:SCREEN_TEXT_CHARS] or NO_HOMEPAGE}"
        + (f"\n\n{src}" if src else "")
        for c, home, src in batch
    )
    prompt = PROMPT.format(
        brief=brief.summary("FR"),
        languages=", ".join(brief.languages) or "any",
        regions=", ".join(brief.regions) or "any",
        volume=brief.volume_wanted(),
        musts=_musts(brief),
        companies=companies,
    )
    result = llm.ask(prompt, ScreenBatch, model=MODEL)
    return {item.domain.lower(): item for item in result.results}


def _musts(brief: Brief) -> str:
    lines = [f"- {k}: {CATEGORIES[k].label}: {brief.value_text(k)}"
             for k in CATEGORIES if "F" in CATEGORIES[k].used_for and brief.must(k)]
    return "\n".join(lines) or "(none)"


def _buyers(wanted: list[str], said: list[str]) -> list[str]:
    """The buyer's values the model reported, in the buyer's spelling."""
    said = {x.strip().lower() for x in said}
    return [w for w in wanted if w.lower() in said]


_EXCERPT_URL = re.compile(r"Source page on [^(]+\((\S+)\)")  # the header source_excerpt writes


def _stated_facts(c: Candidate, item: ScreenItem, home: str, src: str | None) -> None:
    """Keep the price and age the model read only when its quote is on one of the pages and
    contains the value's numbers."""
    pages = [(c.website, home)]
    if src:
        pages.append(((m := _EXCERPT_URL.match(src)) and m.group(1) or c.website, src))
    for key in FACTS:
        s = getattr(item, key)
        if not s or not value_in_quote(s.value, s.quote):
            continue
        if url := next((u for u, text in pages if quote_in_text(s.quote, text, MIN_FACT_QUOTE_WORDS)), None):
            setattr(c, key, Fact(value=s.value, quote=s.quote, url=url))


def _apply(brief: Brief, c: Candidate, item: ScreenItem, home: str = "", src: str | None = None) -> None:
    """Copy the model's answer onto the candidate, enforce the rules it applies unevenly, then
    score it."""
    c.reason = item.reason
    c.holder_type, c.data_owner = item.holder_type, item.data_owner
    c.languages = _buyers(brief.languages, item.languages)
    c.regions = _buyers(brief.regions, item.regions)
    _stated_facts(c, item, home, src)
    c.contradicts = [k for k in dict.fromkeys(item.contradicts) if k in CATEGORIES and brief.must(k)]
    c.collector = item.collector and item.holder_type == "ready"
    if item.holder_type == "unrelated":
        c.holder_type, c.excluded = None, True
    elif item.holder_type == "ready" and c.data_owner == "customers" and not c.collector:
        c.holder_type = "untapped"  # works on its clients' data: a service, not a data seller
    c.vendor = item.vendor and c.holder_type == "untapped"
    if c.holder_type == "untapped":
        c.saleability = Saleability(**{q: Answer(score=getattr(item, q)) for q in ("exists", "allowed", "willing")},
                                    obstacle=item.obstacle, path=item.path)
    if not c.excluded and not brief.accepts_holder(c.holder_type):
        c.excluded, c.reason = True, f"{c.reason} (not a source type the buyer accepts)"
    if (not c.excluded and c.data_owner == "customers" and c.holder_type == "untapped"
            and brief.must("customer_data") and CUSTOMER_EXCLUDE in brief.active("customer_data")):
        c.excluded, c.reason = True, f"{c.reason} (holds its customers' data, which the buyer excludes)"
    c.scores = Scores(data_match=max(1, min(5, item.data_match)))
    rescore(c, brief, volume_signal=item.volume_signal)


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

    blocked: dict[str, str] = {}  # domain -> why its homepage couldn't be read

    def try_fetch(c: Candidate) -> Fetched | None:
        """Homepage text and source excerpt. A blocked homepage (bot protection, timeouts) is
        judged from the page on another site search found it on, if there is one; with no page
        at all it is None, with the failure recorded on the candidate."""
        try:
            home = fetch(c.website)
        except Exception as e:
            if is_dead_site(e):
                c.reason, c.excluded = "dead website: domain doesn't exist", True
                return None
            why = unreachable_reason(e)
            if src := source(c):
                blocked[c.domain] = why
                return c, "", src
            c.reason = f"not screened: {why}, and no other page about it was found"  # kept, fit unknown
            return None
        return c, home, source(c)

    progress(f"Fetching {len(todo)} homepages and source pages")
    fetched = dict(run_parallel(
        [(c.domain, lambda c=c: try_fetch(c)) for c in todo], PARALLEL_FETCHES, lambda _: None
    ).results)
    reachable = [f for c in todo if (f := fetched.get(c.domain)) and (f[1] or f[2])]

    batches = [reachable[i : i + SCREEN_BATCH_SIZE] for i in range(0, len(reachable), SCREEN_BATCH_SIZE)]
    screened = run_parallel(
        [(f"Screening {i * SCREEN_BATCH_SIZE + 1}–{i * SCREEN_BATCH_SIZE + len(b)} of {len(reachable)}",
          lambda b=b: (b, _screen_batch(brief, b))) for i, b in enumerate(batches)],
        PARALLEL_LLM_CALLS,
        progress,
    )
    for _, (batch, items) in screened.results:
        for c, home, src in batch:
            item = items.get(c.domain)
            if item:
                _apply(brief, c, item, home, src)
                if c.domain in blocked:
                    c.reason = f"{c.reason} ({blocked[c.domain]}: judged from the page it was found on)"
            else:
                c.reason = "not screened by the model"
    if screened.quota_error:
        raise QuotaExceeded(screened.quota_error)
    return candidates

