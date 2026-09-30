"""Research step, for companies the user selects: what data they hold, how much, and whether
it can be licensed. One Gemini call with Google Search per company, given its crawled
homepage, privacy policy and terms.

Every quote the model returns must appear on the page it cites: crawled pages are matched
against the crawl, other pages are fetched. Unverified quotes are dropped, and a licence
status or volume left with no verified quote becomes unknown. This is the main guard against
made-up legal claims. The result is a screening flag from public pages, not legal advice.
"""

from collections.abc import Callable

from worker import llm
from worker.config import MODEL, RESEARCH_TEXT_CHARS
from worker.crawl import CrawlResult, fetch_html_quick
from worker.discovery.channels import resolve_source
from worker.schemas import Brief, Candidate, Evidence, Research
from worker.verify import quote_in_text, quote_on_page

PROMPT = """You are checking whether a company could supply data to a buyer. Use the page
excerpts below and Google Search.

The buyer wants: {brief}

Company: {name} ({website})
What the first screen found: {reason}

Find out:
1. data_held: what data the company holds that matches the buyer's needs: kind, formats,
   labels, languages. Say plainly if it holds none.
2. licence_status: whether that data could be licensed to the buyer.
   - "sellable": the company sells or licenses this data (a catalogue, a data licensing
     offer, "we license our datasets").
   - "open": published under a licence that allows commercial use (e.g. CC BY, CC0).
   - "open_restricted": published under a licence that restricts use, e.g. research only
     or non-commercial (CC BY-NC). For public sources, look up the dataset's licence.
   - "needs_consent": it holds the data but its customers or users own it, so licensing
     would need their consent.
   - "internal_only": its policy says recordings are used only internally, e.g. customer
     support calls recorded for quality and staff training, and never shared or sold.
   - "unknown": you found nothing that settles it.
3. volume_estimate and volume_score: how much of this data it likely holds, and a score from
   1 to 5 against the buyer's volume (5: clearly more than needed; 3: substantial but
   probably less; 1: tiny). null for both if you find no sign of scale.

Evidence rules (quotes that break them are thrown away):
- Every licence_status other than "unknown", and every volume_estimate, needs quotes.
- quote: copied word for word from the page, at least one full sentence. No paraphrase, no
  joining of separate passages.
- url: the page the quote is on: one of the excerpt URLs below, or a page you found in search.

{pages}
"""


def _pages(crawl: CrawlResult) -> str:
    parts = [
        f"### {kind} ({page.url})\n{page.text[:RESEARCH_TEXT_CHARS]}"
        for kind in ("homepage", "privacy", "terms")
        if (page := getattr(crawl, kind))
    ]
    return "\n\n".join(parts) or "(No pages could be crawled; rely on Google Search.)"


def _same_url(a: str, b: str) -> bool:
    return a.rstrip("/").split("#")[0] == b.rstrip("/").split("#")[0]


def verify_evidence(
    evidence: list[Evidence], crawl: CrawlResult, fetch: Callable[[str], str] = fetch_html_quick
) -> list[Evidence]:
    """Only quotes found on the page they cite, with search redirect links resolved."""
    out = []
    for ev in evidence:
        url = resolve_source(ev.url)
        crawled = next((p for p in crawl.pages() if _same_url(p.url, url)), None)
        if crawled and quote_in_text(ev.quote, crawled.text):
            ok = True
        else:  # not crawled, or the crawl's main-text extraction dropped the passage
            try:
                ok = quote_on_page(ev.quote, fetch(url))
            except Exception:  # unreachable = can't verify
                ok = False
        if ok:
            out.append(ev.model_copy(update={"url": url}))
    return out


def verify_research(r: Research, crawl: CrawlResult, fetch: Callable[[str], str] = fetch_html_quick) -> Research:
    licence_evidence = verify_evidence(r.licence_evidence, crawl, fetch)
    volume_evidence = verify_evidence(r.volume_evidence, crawl, fetch)
    update = {"licence_evidence": licence_evidence, "volume_evidence": volume_evidence}
    if not licence_evidence:
        update["licence_status"] = "unknown"
    if not volume_evidence:
        update |= {"volume_estimate": None, "volume_score": None}
    return r.model_copy(update=update)


def research(brief: Brief, c: Candidate, crawl: CrawlResult,
             fetch: Callable[[str], str] = fetch_html_quick) -> Research:
    prompt = PROMPT.format(
        brief=brief.summary(), name=c.name, website=c.website, reason=c.reason or "nothing",
        pages=_pages(crawl),
    )
    return verify_research(llm.ask(prompt, Research, model=MODEL, use_search=True), crawl, fetch)
