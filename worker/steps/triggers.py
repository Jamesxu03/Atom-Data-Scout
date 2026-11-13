"""Triggers (WS6): big brands and regulated institutions (banks, insurers, airlines, telcos) rarely
sell recordings in normal times, so they are in scope only when something has changed:

  distress   insolvency, administration, run-off, a unit, brand or call centre closed or for sale
  precedent  it licensed, sold or shared this kind of data before, or released a dataset
  law        a new law or regulator decision in its market allows or requires reuse

A trigger counts only with a quote found on the page it cites that dates it (the year in the
quote or the page address), from the last TRIGGER_YEARS years or still going on. Until one is
verified the company's fit is capped (scoring.py); a verified one lifts the cap and sets how
willing it is (TRIGGER_WILLING). A law alone never makes a company confirmed, only possible.

  law_signals()  one grounded call per local market: recent laws and decisions on sharing or
                 reusing this data, with the sectors each covers
  check()        one grounded call per company: distress and precedent, and which of its
                 market's law signals cover it
  check_all()    the best TRIGGER_CHECKS unchecked companies by data match, during the search
"""

from collections.abc import Callable
from datetime import date

from pydantic import BaseModel, Field

from worker import llm
from worker.brief_template import Brief
from worker.config import PARALLEL_LLM_CALLS, TRIGGER_CHECKS, TRIGGER_YEARS
from worker.crawl import Browser, CrawlResult, fetch_html
from worker.parallel import run_parallel
from worker.schemas import Candidate, LawSignal, Market, MarketPlan, Trigger, year_of
from worker.scoring import rescore
from worker.steps.research import verify_all, verify_triggers
from worker.verify import MIN_QUOTE_WORDS

Fetch = Callable[[str], str]


class LawList(BaseModel):
    laws: list[LawSignal]


class TriggerFound(BaseModel):
    triggers: list[Trigger] = Field(description="Distress and precedent events, each dated and quoted")
    laws: list[int] = Field(description="Numbers of the listed laws that cover this company's sector and market")


LAW_PROMPT = """Find laws, regulations and regulator decisions in {country} from {since} onwards
that allow or require organisations to share, sell, license or reuse data like this, for
example for AI training or research: data-sharing schemes, exemptions for training AI or for
research, regulatory sandboxes, open data or data portability duties.

The data: {brief}

Search in {language} and English, e.g. the local name of the data protection law and its
amendments, "{country} AI training data law", "{country} data sharing regulation {since}".

For each one: title, date (the year it was passed or took effect), summary (one line: what it
allows or requires), sectors (which organisations it covers, e.g. banks, telcos, all
companies), and evidence: quotes copied word for word from the official text or reputable
news, ideally ones that include the year. url: the page each quote is on, as the search result
gave it; never write a URL yourself. Return none rather than guess.
"""

TRIGGER_PROMPT = """{name} ({website}) is a big brand or regulated institution that may hold
data like this: {brief}

What the first screen found: {reason}

Such organisations rarely sell data in normal times. Find dated events from {since} onwards
(or still going on) that could make it sell or share it:
- distress: insolvency, administration, liquidation, run-off, a unit, brand or call centre
  closed or put up for sale, a merger that retired the systems holding the data
- precedent: it licensed, sold or shared this kind of data before, released a dataset, or
  announced a data partnership with an AI company

Search the news and public records, e.g. "{name} administration", "{name} insolvency",
"{name} sells unit", "{name} closes call centre", "{name} data partnership", "{name} AI
training data", "{name} dataset".

For each event: kind, date (year, or year and month), ongoing, summary (one line), and
evidence: quotes copied word for word from the article or notice, ideally ones that include
the year. url: the page each quote is on, as the search result gave it; never write a URL
yourself. Return none rather than guess.

laws: the numbers of the laws below that cover this organisation's sector and market (none if
none do):
{laws}
"""


def _since() -> int:
    return date.today().year - TRIGGER_YEARS


def law_signals(brief: Brief, market: Market, fetch: Fetch) -> list[LawSignal]:
    """Recent laws for one market, kept only with verified quotes."""
    prompt = LAW_PROMPT.format(country=market.country, language=market.language, since=_since(),
                               brief=brief.summary("S"))
    found = llm.ask(prompt, LawList, use_search=True, **llm.step("triggers")).laws
    found = [law for law in found if (year := year_of(law.date)) and year >= _since()]
    checked = verify_all([(law.evidence, fetch, MIN_QUOTE_WORDS) for law in found], CrawlResult(website=""))
    return [law.model_copy(update={"evidence": ev}) for law, ev in zip(found, checked, strict=True) if ev]


def _law_trigger(law: LawSignal) -> Trigger:
    return Trigger(kind="law", date=law.date, ongoing=True, summary=f"{law.title}: {law.summary}",
                   evidence=law.evidence)


def check(brief: Brief, c: Candidate, laws: list[LawSignal], fetch: Fetch) -> list[Trigger]:
    """Verified triggers for one company: distress and precedent with dated quotes, and the
    market's law signals that cover it (already verified)."""
    listed = "\n".join(f"{i}. {law.title} ({law.date}): {law.summary}; covers {', '.join(law.sectors)}"
                       for i, law in enumerate(laws, 1)) or "(none)"
    prompt = TRIGGER_PROMPT.format(name=c.name, website=c.website, brief=brief.summary("S"),
                                   reason=c.reason or "nothing", since=_since(), laws=listed)
    found = llm.ask(prompt, TriggerFound, use_search=True, **llm.step("triggers"))
    events = verify_triggers([t for t in found.triggers if t.kind != "law"], CrawlResult(website=c.website), fetch)
    return events + [_law_trigger(laws[i - 1]) for i in dict.fromkeys(found.laws) if 1 <= i <= len(laws)]


def to_check(candidates: list[Candidate]) -> list[Candidate]:
    """Unchecked big brands and regulated institutions, best data match first (TRIGGER_CHECKS)."""
    todo = [c for c in candidates if c.needs_trigger and not c.trigger_checked and not c.excluded and not c.killed]
    return sorted(todo, key=lambda c: -(c.scores.data_match or 0))[:TRIGGER_CHECKS]


def laws_for(c: Candidate, plans: list[MarketPlan]) -> list[LawSignal]:
    """The law signals of the markets whose searches found it; every market's when none did."""
    mine = [p for p in plans if p.market.label in c.markets]
    return [law for p in (mine or plans) for law in p.laws]


def check_all(brief: Brief, candidates: list[Candidate], plans: list[MarketPlan],
              progress: Callable[[str], None] = print, fetch: Fetch | None = None) -> tuple[list[str], str | None]:
    """Check to_check(candidates) in parallel (in place) and re-score them. Returns the errors
    and the quota error, if the quota ran out. News pages are fetched with one browser, launched
    only if one needs rendering."""
    todo = to_check(candidates)
    if not todo:
        return [], None
    if fetch is None:
        with Browser() as browser:
            return check_all(brief, candidates, plans, progress, lambda url: fetch_html(url, browser))

    def one(c: Candidate) -> None:
        c.triggers = check(brief, c, laws_for(c, plans), fetch)
        c.trigger_checked = True
        rescore(c, brief)

    batch = run_parallel([(f"Checking {c.name} for insolvency, data deals and laws", lambda c=c: one(c))
                          for c in todo], PARALLEL_LLM_CALLS, progress)
    return batch.errors, batch.quota_error
