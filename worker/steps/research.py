"""Research step, for companies the user selects: what data they hold, how much, the licence
(in the same labels as D1 "Acceptable licence", plus internal use only), and a check of each
requirement the brief marks for research (V): quality evidence, demographics, recency,
permitted use, personal data, documents and where the data can be processed.

One Gemini call with Google Search per company, given its crawled homepage, privacy policy and
terms. Every quote the model returns must appear on the page it cites: crawled pages are matched
against the crawl, other pages are fetched. Unverified quotes are dropped; a licence, volume or
check left with no verified quote becomes unclear. This is the main guard against made-up legal
claims. The result is a screening flag from public pages, not legal advice.
"""

from collections.abc import Callable

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
from worker.config import MODEL, RESEARCH_TEXT_CHARS
from worker.crawl import CrawlResult, fetch_html_quick
from worker.discovery.channels import resolve_source
from worker.schemas import Candidate, Check, CheckedCategory, Evidence, Research
from worker.verify import quote_in_text, quote_on_page

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

Find out:
1. data_held: what data the company holds that matches the buyer's needs: kind, formats,
   labels, languages. Say plainly if it holds none.
2. licence: which of these describes how the data could be licensed. For public sources, look
   up the dataset's own licence.
{licences}
3. volume_estimate and volume_score: how much of this data it likely holds, and a score from
   1 to 5 against the volume wanted (5: clearly more than needed; 3: substantial but probably
   less; 1: tiny). null for both if you find no sign of scale.
4. checks: one per requirement below. verdict "meets" or "fails" only with a quote showing it,
   otherwise "unclear".
{checks}

Evidence rules (quotes that break them are thrown away):
- Every licence other than "{unclear}", every volume_estimate, and every "meets" or "fails"
  verdict needs quotes.
- quote: copied word for word from the page, at least one full sentence. No paraphrase, no
  joining of separate passages.
- url: the page the quote is on: one of the excerpt URLs below, or a page you found in search.

{pages}
"""


def _checks(brief: Brief) -> str:
    keys = [k for k in CheckedCategory.__args__ if brief.importance(k) != "any"]
    lines = [f"   - {k}: {CATEGORIES[k].label}: {brief.value_text(k)}" for k in keys]
    flags = transfer_flags(brief.regions, brief.chosen("processing"))
    if flags and "processing" in keys:
        lines.append("     Transfer rules that may apply: " + " ".join(flags))
    return "\n".join(lines) or "   (none)"


def _pages(crawl: CrawlResult) -> str:
    parts = [
        f"### {kind} ({page.url})\n{page.text[:RESEARCH_TEXT_CHARS]}"
        for kind in ("homepage", "privacy", "terms")
        if (page := getattr(crawl, kind))
    ]
    return "\n\n".join(parts) or "(No pages could be crawled; rely on Google Search.)"


def build_prompt(brief: Brief, c: Candidate, crawl: CrawlResult) -> str:
    return PROMPT.format(
        brief=brief.summary("FRV"), volume=brief.volume_wanted(), name=c.name, website=c.website,
        reason=c.reason or "nothing", licences="\n".join(f'   - "{k}": {v}' for k, v in LICENCE_MEANINGS.items()),
        checks=_checks(brief), unclear=UNCLEAR, pages=_pages(crawl),
    )


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
        update["licence"] = UNCLEAR
    if not volume_evidence:
        update |= {"volume_estimate": None, "volume_score": None}
    checks = []
    for ch in r.checks:
        evidence = verify_evidence(ch.evidence, crawl, fetch)
        verdict = ch.verdict if evidence else "unclear"
        checks.append(Check(category=ch.category, verdict=verdict, finding=ch.finding, evidence=evidence))
    update["checks"] = list({ch.category: ch for ch in checks}.values())  # one per category
    return r.model_copy(update=update)


def research(brief: Brief, c: Candidate, crawl: CrawlResult,
             fetch: Callable[[str], str] = fetch_html_quick) -> Research:
    found = llm.ask(build_prompt(brief, c, crawl), Research, model=MODEL, use_search=True)
    return verify_research(found, crawl, fetch)
