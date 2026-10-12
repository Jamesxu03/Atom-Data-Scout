"""Confirmation (WS4): before a company reaches the default shortlist, check with quotes from its
own pages that its data fits the client's must-haves and that it can plausibly sell or license
it. This is where a longer search spends its extra time first.

The best of each list (CONFIRM_TOP by fit; tool vendors, killed companies and big or regulated
institutions with no trigger are left out) get the crawl research uses (homepage, privacy
policy, terms) and one Gemini call with Google Search, with no contacts call. Every quote must
be on the page it cites, as in research; claims left without one are dropped. The verdict is
then worked out here, in Python:

  untapped holders
    rejected     a must-have contradicted; data its customers own when D3 excludes it; or a
                 blocker: "we never share or sell recordings", or kept only for a regulator
                 with no reuse clause (only the obstacle when a trigger was verified)
    confirmed    fit confirmed (below), it owns the data (or its customers do and D3 accepts
                 that), and a selling signal or a distress or precedent trigger
    possible     fit confirmed and nothing in the way, but no sign yet that it would sell
  ready sources
    rejected     a must-have contradicted, internal use only, or a licence D1 refuses
    confirmed    fit confirmed and a licence D1 accepts (any stated licence when D1 is empty)
    possible     fit confirmed, licence not stated
  either
    unconfirmed  nothing contradicted, but the fit isn't shown on its pages

Fit is confirmed by a quote showing it holds this kind of data, and each must-have among
CONFIRM_CATEGORIES (modality, domain, languages, region) shown: by a quote, or by what the
screen already found (a clear data match; evidence for the language or region, since an
English site rarely says "English"). Other must-haves only reject when contradicted, and never
the volume target (several sellers can make it up) or, for untapped holders, permitted use and
deal terms (they only cover their clients today; the outreach email asks). A screening flag from public pages, not legal advice.
"""

from collections.abc import Callable

from worker import llm
from worker.brief_template import CATEGORIES, CUSTOMER_EXCLUDE, INTERNAL_ONLY, UNCLEAR, Brief
from worker.config import (
    CONFIRM_CATEGORIES,
    CONFIRM_SCREEN_MATCH,
    CONFIRM_TOP,
    NEVER_REJECTS,
    RELEVANT_FIT,
    UNTAPPED_NEGOTIABLE,
)
from worker.crawl import Browser, CrawlResult, fetch_html
from worker.schemas import Candidate, Confirmation, ConfirmFound
from worker.scoring import licence_acceptable, split
from worker.steps.research import LICENCE_MEANINGS, holder_text, page_excerpts, verify_all
from worker.verify import MIN_QUOTE_WORDS

Fetch = Callable[[str], str]
# Judged in Python from labels and flags, or by the licence and saleability questions
NOT_CHECKED = {"exclusions", "sources", "collection", "customer_data", "licence"}

PROMPT = """You are confirming whether a company is a good source for a data buyer, from the
page excerpts below and Google Search. Every claim needs a quote.

The buyer wants: {brief}

Company: {name} ({website})
What the first screen found: {reason}
Kind of holder: {holder}

1. data_held and data_evidence: what data it holds that matches the buyer's, and quotes that
   show it holds this kind of data. "none" and no quotes if it holds none.
2. checks: one per must-have below, with its key. "meets" or "fails" only with a quote showing
   it; otherwise "unclear". Pages that don't mention it are "unclear", never "meets".
{musts}
3. data_owner and owner_evidence: "company" if it recorded or collected the data itself (its
   own staff, contractors or devices), "customers" if it was recorded by or for its customers,
   who own it, "unclear" if the pages don't say; with quotes.
{question}

Evidence rules (quotes that break them are thrown away):
- quote: copied word for word from the page, in the page's own language, one or two full
  sentences, no longer. No paraphrase, no translation, no joining of separate passages.
- url: the page the quote is on: one of the excerpt URLs below, or for a page found in search,
  the link exactly as the search result gave it. Never write or guess a URL yourself.

{pages}
"""

UNTAPPED_QUESTION = """4. signals: signs it would sell or license this data, each with quotes:
   - licensing: a data licensing, data partnership or "AI training data" page or announcement
   - dataset: it released a dataset, or a paper or competition names it as the data provider
     (search Hugging Face, arXiv, Kaggle and the press)
   - reuse_terms: its terms let it use the data to improve its services or train models, or
     share it with research partners
   - distress: closed, insolvent, in administration, a product retired, or assets for sale
   - own_data: the data comes from its own staff or devices, not its customers
5. blockers, each with quotes:
   - never_share: a statement that the recordings or content themselves are never shared or
     sold. The standard notice "we do not sell your personal information" is not a blocker.
   - regulator_only: the data is kept only because a regulator requires it, and no clause
     allows other uses
Leave licence null and licence_evidence empty."""

READY_QUESTION = """4. licence and licence_evidence: how the data could be licensed, one of:
{licences}
Leave signals and blockers empty."""


def _musts(brief: Brief) -> list[str]:
    return [k for k in CATEGORIES if brief.must(k) and k not in NOT_CHECKED]


def build_prompt(brief: Brief, c: Candidate, crawl: CrawlResult) -> str:
    musts = "\n".join(f"   - {k}: {CATEGORIES[k].label}: {brief.value_text(k)}" for k in _musts(brief))
    question = UNTAPPED_QUESTION if c.holder_type == "untapped" else READY_QUESTION.format(
        licences="\n".join(f'   - "{k}": {v}' for k, v in LICENCE_MEANINGS.items()))
    return PROMPT.format(brief=brief.summary("FRV"), name=c.name, website=c.website, reason=c.reason or "nothing",
                         holder=holder_text(c), musts=musts or "   (none)", question=question, pages=page_excerpts(crawl))


def verify(found: ConfirmFound, crawl: CrawlResult, fetch: Fetch) -> ConfirmFound:
    """Keep only what verified quotes support: a check left with none becomes unclear, a signal
    or blocker with none is dropped, an owner with none is unclear."""
    groups = [found.data_evidence, found.owner_evidence, found.licence_evidence,
              *(ch.evidence for ch in found.checks), *(s.evidence for s in found.signals),
              *(b.evidence for b in found.blockers)]
    checked = iter(verify_all([(g, fetch, MIN_QUOTE_WORDS) for g in groups], crawl))
    data, owner, licence = next(checked), next(checked), next(checked)
    checks = [ch.model_copy(update={"evidence": ev, "verdict": ch.verdict if ev else "unclear"})
              for ch, ev in zip(found.checks, checked)]
    signals = [s.model_copy(update={"evidence": ev}) for s, ev in zip(found.signals, checked) if ev]
    blockers = [b.model_copy(update={"evidence": ev}) for b, ev in zip(found.blockers, checked) if ev]
    return found.model_copy(update={
        "data_evidence": data, "owner_evidence": owner, "data_owner": found.data_owner if owner else "unclear",
        "licence_evidence": licence, "licence": found.licence if licence else UNCLEAR,
        "checks": list({ch.category: ch for ch in checks}.values()), "signals": signals, "blockers": blockers,
    })


def _labels(keys: list[str]) -> str:
    return ", ".join(CATEGORIES[k].label if k in CATEGORIES else k for k in keys)


def decide(brief: Brief, c: Candidate, f: ConfirmFound) -> tuple[str, str]:
    """(verdict, reason) from verified findings; see the module docstring."""
    checks = {ch.category: ch for ch in f.checks}
    ignored = NEVER_REJECTS | (UNTAPPED_NEGOTIABLE if c.holder_type == "untapped" else set())
    if fails := [k for k, ch in checks.items() if ch.verdict == "fails" and k not in ignored]:
        return "rejected", f"Contradicts a must-have: {_labels(fails)}"
    owner = f.data_owner if f.owner_evidence else c.data_owner or "unclear"
    customers_excluded = owner == "customers" and CUSTOMER_EXCLUDE in brief.active("customer_data")
    if c.holder_type == "ready":
        if f.licence == INTERNAL_ONLY:
            return "rejected", "Its data is for internal use only"
        if licence_acceptable(f.licence, brief) is False:
            return "rejected", f"Licence ({f.licence}) isn't one the brief accepts"
    else:
        if customers_excluded:
            return "rejected", "Its customers own the data, which the brief excludes"
        blockers = [b for b in f.blockers if not (b.kind == "regulator_only" and c.triggers)]
        if blockers:
            return "rejected", blockers[0].summary

    def meets(k: str) -> bool:
        return k in checks and checks[k].verdict == "meets"

    screen_evidence = {"languages": bool(c.languages), "region": bool(c.regions),
                       **dict.fromkeys(("modality", "domain"), (c.scores.data_match or 0) >= CONFIRM_SCREEN_MATCH)}
    if not (f.data_evidence or meets("modality") or meets("domain")):
        return "unconfirmed", "Its pages don't show it holds this kind of data"
    if missing := [k for k in CONFIRM_CATEGORIES if brief.must(k) and not (meets(k) or screen_evidence[k])]:
        return "unconfirmed", f"Its pages don't show: {_labels(missing)}"
    if c.holder_type == "ready":
        accepted = licence_acceptable(f.licence, brief)
        if accepted or (accepted is None and f.licence != UNCLEAR and not brief.active("licence")):
            return "confirmed", f"Fits; licence: {f.licence}"
        return "possible", "Fits; licence not stated"
    signals = [s.summary for s in f.signals] + [t.summary for t in c.triggers if t.kind != "law"]
    if owner == "unclear":
        return "possible", "Fits; who owns the data is unclear"
    if signals:
        return "confirmed", f"Fits; {signals[0]}"
    laws = [t.summary for t in c.triggers if t.kind == "law"]
    return "possible", f"Fits; {laws[0]}" if laws else "Fits; no sign yet that it would sell"


def redecide(brief: Brief, c: Candidate) -> None:
    """Today's verdict rules from the stored findings (in place), like rescore() for fit."""
    if conf := c.confirmation:
        found = ConfirmFound(**conf.model_dump(include=set(ConfirmFound.model_fields) - {"licence_evidence"}),
                             licence_evidence=conf.licence_evidence)
        conf.verdict, conf.reason = decide(brief, c, found)


def confirm(brief: Brief, c: Candidate, crawl: CrawlResult, fetch: Fetch | None = None) -> Confirmation:
    """One company's verdict. fetch reads the pages quotes cite (default: plain HTTP, then one
    browser launched only if a page needs rendering)."""
    found = llm.ask(build_prompt(brief, c, crawl), ConfirmFound, use_search=True, **llm.step("confirm"))
    if c.holder_type == "untapped":
        found = found.model_copy(update={"licence": None, "licence_evidence": []})
    else:
        found = found.model_copy(update={"signals": [], "blockers": []})
    if fetch is None:
        with Browser() as browser:
            return _confirmed(brief, c, verify(found, crawl, lambda url: fetch_html(url, browser)))
    return _confirmed(brief, c, verify(found, crawl, fetch))


def _confirmed(brief: Brief, c: Candidate, f: ConfirmFound) -> Confirmation:
    verdict, reason = decide(brief, c, f)
    return Confirmation(verdict=verdict, reason=reason, data_held=f.data_held, data_evidence=f.data_evidence,
                        owner_evidence=f.owner_evidence, checks=f.checks,
                        data_owner=f.data_owner, licence=f.licence if c.holder_type == "ready" else None,
                        licence_evidence=f.licence_evidence, signals=f.signals, blockers=f.blockers)


def select(candidates: list[Candidate]) -> list[Candidate]:
    """The best of each list by rank, CONFIRM_TOP each, not confirmed yet: fits only, and no
    tool vendors, killed companies, or big or regulated institutions with no trigger."""
    lists = split(candidates)
    out = []
    for side, n in CONFIRM_TOP.items():
        eligible = [c for c in lists[side] if c.confirmation is None and not c.excluded and not c.killed
                    and not c.vendor and not (c.needs_trigger and not c.triggers)
                    and c.fit is not None and c.fit >= RELEVANT_FIT]
        out += eligible[:n]
    return out
