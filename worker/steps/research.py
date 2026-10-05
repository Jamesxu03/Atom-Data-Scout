"""Research step, for companies the user selects: what data they hold, how much, price and age
(founding year, when the data was recorded), a check of each requirement the brief marks for
research (V): recency, permitted use and the deal terms (personal data, documents, where the
data can be processed, ...), and then by holder type:
- ready source: the licence, in the same labels as D1 "Acceptable licence"
- untapped holder: the three saleability questions (does the data still exist, is selling it
  allowed, are they willing), answered from the news and public records as well as the
  company's own policies, with the main obstacle and how it could still be bought

One Gemini call with Google Search per company, given its crawled homepage, privacy policy and
terms. Every quote the model returns must appear on the page it cites: crawled pages are matched
against the crawl, other pages are fetched. Unverified quotes are dropped; a licence, volume or
check left with no verified quote becomes unclear, and a saleability answer with no verified
quote falls back to the screen's initial one. News pages are fetched with a browser when plain
HTTP gets no text. This is the main guard against made-up legal claims. The result is a
screening flag from public pages, not legal advice.
"""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from worker import llm
from worker.brief_template import (
    CATEGORIES,
    COMMERCIAL,
    EXCLUSIVE,
    INTERNAL_ONLY,
    OPEN,
    OPEN_CONDITIONS,
    RESTRICTED,
    UNCLEAR,
    Brief,
    transfer_flags,
)
from worker.config import MODEL, PARALLEL_FETCHES, RESEARCH_TEXT_CHARS
from worker.crawl import Browser, CrawlResult, fetch_html, fetch_html_quick
from worker.discovery.channels import resolve_source
from worker.schemas import (
    FACTS,
    SALEABILITY_QUESTIONS,
    Candidate,
    Check,
    CheckedCategory,
    Evidence,
    Research,
)
from worker.verify import (
    MIN_FACT_QUOTE_WORDS,
    MIN_QUOTE_WORDS,
    quote_in_text,
    quote_on_page,
    value_in_quote,
)

LICENCE_MEANINGS = {
    OPEN: "published as public domain or under an open licence with no conditions (CC0, open government data)",
    OPEN_CONDITIONS: "published under an open licence with attribution or share-alike conditions "
                     "(CC BY, CC BY-SA, ODbL)",
    RESTRICTED: "published or offered only for non-commercial use, research only, or with no derivatives "
                "(e.g. CC BY-NC)",
    COMMERCIAL: "the company sells or licenses the data commercially (a catalogue, a data licensing offer)",
    EXCLUSIVE: "the company offers exclusive licences to the data",
    INTERNAL_ONLY: "its policy says the data is used only internally (e.g. support calls recorded for "
                   "quality and staff training) and never shared or sold",
    UNCLEAR: "you found nothing that settles it, or the data belongs to its customers and the terms "
             "don't say whether it could be licensed",
}

PROMPT = """You are checking whether a company could supply data to a buyer. Use the page
excerpts below and Google Search.

The buyer wants: {brief}
Volume: {volume}

Company: {name} ({website})
What the first screen found: {reason}
Kind of holder: {holder}

Find out:
1. data_held: what data the company holds that matches the buyer's needs: kind, formats,
   labels, languages. Say plainly if it holds none.
2. {type_question}
3. volume_estimate and volume_score: how much of this data it likely holds, and a score from
   1 to 5 against the volume wanted (5: clearly more than needed; 3: substantial but probably
   less; 1: tiny). null for both if you find no sign of scale.
4. checks: one per requirement below. verdict "meets" or "fails" only with a quote showing it,
   otherwise "unclear".
{checks}
5. price, founded, data_period, each with the quote and url that state it, or null:
   - price: the published price of this data, with currency and unit (e.g. "$30 per audio
     hour", a marketplace listing price), or "On request" if the company says so.
   - founded: the year the company was founded.
   - data_period: when the matching data was recorded or collected, e.g. "2018 to 2024".

Evidence rules (quotes that break them are thrown away):
- Every licence other than "{unclear}", every volume_estimate, and every "meets" or "fails"
  verdict needs quotes. Every price, founded and data_period needs a quote containing its
  numbers. Every saleability score needs quotes.
- quote: copied word for word from the page, one or two full sentences, no longer. No
  paraphrase, no joining of separate passages.
- url: the page the quote is on: one of the excerpt URLs below, or for a page found in search,
  the link exactly as the search result gave it (a grounding redirect link is fine). Never
  write or guess a URL yourself: a made-up URL loses the quote.

{pages}
"""


def _checks(brief: Brief) -> str:
    keys = [k for k in CheckedCategory.__args__ if brief.importance(k) != "any"]
    lines = [f"   - {k}: {CATEGORIES[k].label}: {brief.value_text(k)}" for k in keys]
    flags = transfer_flags(brief.regions)
    if flags and "deal_terms" in keys:
        lines.append("     Transfer rules that may apply, depending on where the buyer processes the data: "
                     + " ".join(flags))
    return "\n".join(lines) or "   (none)"


def _pages(crawl: CrawlResult) -> str:
    parts = [
        f"### {kind} ({page.url})\n{page.text[:RESEARCH_TEXT_CHARS]}"
        for kind in ("homepage", "privacy", "terms")
        if (page := getattr(crawl, kind))
    ]
    return "\n\n".join(parts) or "(No pages could be crawled; rely on Google Search.)"


READY_QUESTION = """licence: which of these describes how the data could be licensed. For public
   sources, look up the dataset's own licence. For a collector that records to order: rights
   assigned to the client is "Commercial, exclusive"; the company keeping rights and licensing
   them is "Commercial, non-exclusive". Leave saleability null.
{licences}"""

UNTAPPED_QUESTION = """saleability: the company holds the data without selling it, so judge whether
   it could be bought. Set licence to "{unclear}" unless a policy settles it. Answer three
   questions, each with a score from 1 to 5 (null if you find nothing), a one-line reason and
   quotes:
   - exists: is the data still there? 5: kept long term, an archive; 1: deleted within 30
     days, or it doesn't record at all. Not how much: that is volume.
   - allowed: may it be sold? 5: the company recorded it itself and its terms allow other
     uses; 1: copyright belongs to someone else, terms say recordings are only for quality
     monitoring and never shared, or it is health data or recordings kept for financial
     regulation. Data its customers own needs their consent.
   - willing: would they sell? 5: insolvent, shutting down, selling assets, or has done data
     deals with AI companies; 1: a big brand, or a strictly regulated institution.
   Then obstacle (the main thing in the way, one line) and path (how it could still be bought,
   e.g. "needs its customers' consent", "contact the administrators"; null if there is no way).

   Search beyond its own site, in the news and public records. Run searches such as:
   "{name} lawsuit", "{name} fined", "{name} administration OR insolvency OR liquidation",
   "{name} shuts down", "{name} acquired", "{name} layoffs", "{name} data licensing",
   "{name} AI training data partnership", "{name} privacy". Look for:
   - insolvency, administration or liquidation notices, shutdowns, layoffs, asset sales, and
     acquisitions after which the product was retired (willing)
   - announced data partnerships or licensing deals with AI companies, or statements about
     using customer data for AI (willing, allowed)
   - regulator actions, fines or lawsuits over recording, privacy or data use (allowed)
   - how long recordings are kept, and archive size or history, e.g. "ten years of recordings"
     (exists)
   - who owns recordings under its terms, and copyright notices (allowed)
   Prefer recent sources (the last three years) and two or more independent ones. When news or
   public records exist, cite at least one of them for willing, quoting the article itself,
   not only the company's own pages."""


def _type_question(c: Candidate) -> str:
    if c.holder_type == "untapped":
        return UNTAPPED_QUESTION.format(unclear=UNCLEAR, name=c.name)
    return READY_QUESTION.format(licences="\n".join(f'   - "{k}": {v}' for k, v in LICENCE_MEANINGS.items()))


def _holder(c: Candidate) -> str:
    if c.holder_type == "untapped":
        if c.vendor:
            return "tool vendor: makes the tool its customers record with, so its customers hold the data"
        return "untapped holder: has the data but doesn't sell it"
    if c.holder_type == "ready":
        return "ready source that records to order" if c.collector else "ready source: already offers the data"
    return "unknown"


def build_prompt(brief: Brief, c: Candidate, crawl: CrawlResult) -> str:
    return PROMPT.format(
        brief=brief.summary("FRV"), volume=brief.volume_wanted(), name=c.name, website=c.website,
        reason=c.reason or "nothing", holder=_holder(c), type_question=_type_question(c),
        checks=_checks(brief), unclear=UNCLEAR, pages=_pages(crawl),
    )


def _same_url(a: str, b: str) -> bool:
    return a.rstrip("/").split("#")[0] == b.rstrip("/").split("#")[0]


def _verified(ev: Evidence, crawl: CrawlResult, fetch: Callable[[str], str], min_words: int) -> Evidence | None:
    """ev with its search redirect link resolved, if its quote is on the page it cites."""
    url = resolve_source(ev.url)
    crawled = next((p for p in crawl.pages() if _same_url(p.url, url)), None)
    if crawled and quote_in_text(ev.quote, crawled.text, min_words):
        ok = True
    else:  # not crawled, or the crawl's main-text extraction dropped the passage
        try:
            ok = quote_on_page(ev.quote, fetch(url), min_words)
        except Exception:  # unreachable = can't verify
            ok = False
    return ev.model_copy(update={"url": url}) if ok else None


def verify_evidence(
    evidence: list[Evidence], crawl: CrawlResult, fetch: Callable[[str], str] = fetch_html_quick,
    min_words: int = MIN_QUOTE_WORDS,
) -> list[Evidence]:
    """Only quotes found on the page they cite, with search redirect links resolved."""
    return _verify_all([(evidence, fetch, min_words)], crawl)[0]


def _verify_all(
    groups: list[tuple[list[Evidence], Callable[[str], str], int]], crawl: CrawlResult,
) -> list[list[Evidence]]:
    """verify_evidence for several lists at once: every quote is checked in parallel, since
    each may need a redirect lookup and a page fetch."""
    jobs = [(i, ev, fetch, min_words) for i, (evidence, fetch, min_words) in enumerate(groups) for ev in evidence]
    out: list[list[Evidence]] = [[] for _ in groups]
    if not jobs:
        return out
    with ThreadPoolExecutor(min(PARALLEL_FETCHES, len(jobs))) as pool:
        checked = pool.map(lambda job: _verified(job[1], crawl, job[2], job[3]), jobs)
        for (i, *_), ev in zip(jobs, checked):
            if ev:
                out[i].append(ev)
    return out


def verify_research(
    r: Research, crawl: CrawlResult, fetch: Callable[[str], str] = fetch_html_quick,
    news_fetch: Callable[[str], str] | None = None,
) -> Research:
    """Keep only what verified quotes support. news_fetch (default: fetch) reads the pages
    saleability quotes cite, which are often news sites that need a browser."""
    facts = {key: f for key in FACTS if (f := getattr(r, key)) and value_in_quote(f.value, f.quote)}
    s = r.saleability
    groups = [
        (r.licence_evidence, fetch, MIN_QUOTE_WORDS),
        (r.volume_evidence, fetch, MIN_QUOTE_WORDS),
        *((ch.evidence, fetch, MIN_QUOTE_WORDS) for ch in r.checks),
        *(([f], fetch, MIN_FACT_QUOTE_WORDS) for f in facts.values()),
        *((getattr(s, q).evidence, news_fetch or fetch, MIN_QUOTE_WORDS) for q in SALEABILITY_QUESTIONS if s),
    ]
    verified = iter(_verify_all(groups, crawl))

    licence_evidence, volume_evidence = next(verified), next(verified)
    update = {"licence_evidence": licence_evidence, "volume_evidence": volume_evidence}
    if not licence_evidence:
        update["licence"] = UNCLEAR
    if not volume_evidence:
        update |= {"volume_estimate": None, "volume_score": None}
    checks = []
    for ch in r.checks:
        evidence = next(verified)
        verdict = ch.verdict if evidence else "unclear"
        checks.append(Check(category=ch.category, verdict=verdict, finding=ch.finding, evidence=evidence))
    update["checks"] = list({ch.category: ch for ch in checks}.values())  # one per category
    for key in FACTS:
        kept = next(verified) if key in facts else []
        update[key] = kept[0] if kept else None
    if s:
        answers = {}
        for q in SALEABILITY_QUESTIONS:
            a = getattr(s, q)
            evidence = next(verified)
            answers[q] = a.model_copy(update={"evidence": evidence, "verified": bool(evidence),
                                              "score": a.score if evidence else None})
        update["saleability"] = s.model_copy(update=answers)
    return r.model_copy(update=update)


def research(brief: Brief, c: Candidate, crawl: CrawlResult, fetch: Callable[[str], str] = fetch_html_quick,
             news_fetch: Callable[[str], str] | None = None) -> Research:
    """news_fetch defaults to fetch_html with one browser for all the news pages, launched only
    if one needs rendering."""
    found = llm.ask(build_prompt(brief, c, crawl), Research, model=MODEL, use_search=True)
    if c.holder_type != "untapped":
        found = found.model_copy(update={"saleability": None})
    if news_fetch:
        return verify_research(found, crawl, fetch, news_fetch)
    with Browser() as browser:
        return verify_research(found, crawl, fetch, lambda url: fetch_html(url, browser))
