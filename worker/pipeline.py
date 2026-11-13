"""The two jobs a session runs, each saving to the session folder as it goes:

  search()    brief -> hypotheses -> local market plans and law signals -> discovery rounds,
              each followed by source checks + screen, while rounds keep finding qualifying
              companies -> snowball and vendors' customers -> source checks + screen ->
              trigger checks for big or regulated institutions -> two lists (ready sources,
              untapped holders), shown while the best of each is confirmed (stage "confirming")
  research()  for the companies the user selects: deep crawl, research (licence for ready
              sources, saleability and triggers from news and policies for untapped holders,
              volume, data held) and contacts, then re-score and re-rank

Independent calls run in parallel. If the Gemini quota runs out mid-job, everything gathered
so far is saved and the session is marked partial instead of being lost. A search makes at
most MAX_GROUNDED_CALLS grounded calls (Budget).
"""

import csv
import io
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

from worker import sessions
from worker.brief_template import Brief
from worker.config import (
    CONFIRM_TOP,
    DISCOVERY,
    DISCOVERY_ROUNDS,
    LOCAL_CALLS,
    MAX_GROUNDED_CALLS,
    PARALLEL_CRAWLS,
    PARALLEL_LLM_CALLS,
    PARALLEL_RESEARCH,
    RELEVANT_FIT,
    ROUND_CALLS,
    ROUND_MIN_NEW,
    TRIGGER_CHECKS,
)
from worker.crawl import Browser, CrawlResult, crawl, fetch_html
from worker.discovery import (
    Discovery,
    check_sources,
    discover,
    final_tasks,
    finish_round,
    next_calls,
    pick_seeds,
    round_one_calls,
    run_round,
    run_tasks,
    seed_types,
    uncovered_archetypes,
)
from worker.llm import QuotaExceeded
from worker.markets import markets as find_markets
from worker.parallel import Batch, run_parallel
from worker.schemas import FACTS, SALEABILITY_QUESTIONS, Candidate, Funnel, Market, MarketPlan
from worker.scoring import (
    answers,
    is_relevant,
    must_failures,
    rank,
    rescore,
    saleability_label,
    verdict,
)
from worker.sessions import Session
from worker.steps import confirm, triggers
from worker.steps.contacts import find_contacts
from worker.steps.hypotheses import fill_archetypes, hypotheses, local_plan
from worker.steps.research import research as research_company
from worker.steps.screen import screen

Progress = Callable[[str], None]


class Timer:
    """Times each step and reports it through progress, so slow steps are visible."""

    def __init__(self, progress: Progress, timings: dict[str, float] | None = None):
        self.progress = progress
        self.timings = dict(timings or {})
        self.timings.pop("total", None)
        self.started = time.perf_counter()
        self.before = sum(self.timings.values())

    @contextmanager
    def step(self, name: str, message: str | None = None) -> Iterator[None]:
        self.progress(message or name)
        t = time.perf_counter()
        try:
            yield
        finally:
            secs = round(time.perf_counter() - t, 1)
            self.timings[name] = round(self.timings.get(name, 0) + secs, 1)
            self.progress(f"✓ {name}: {secs:.1f}s")

    def total(self) -> dict[str, float]:
        return {**self.timings, "total": round(self.before + time.perf_counter() - self.started, 1)}


# Kept back for the end of a search, whatever the rounds spend
RESERVED_CALLS = (DISCOVERY["snowball_seeds"] + DISCOVERY["vendor_customer_calls"] + TRIGGER_CHECKS
                  + sum(CONFIRM_TOP.values()))


class Budget:
    """Grounded calls for one search, at most MAX_GROUNDED_CALLS. Planned up front: if every
    round at full size wouldn't fit, round 3 goes first, then the local markets, then round 2;
    trigger checks and confirmation are always kept (RESERVED_CALLS)."""

    def __init__(self, brief: Brief, markets: int):
        first, local, later = sum(round_one_calls(brief).values()), markets * (sum(LOCAL_CALLS.values()) + 1), \
            sum(ROUND_CALLS.values())
        self.rounds, self.local = DISCOVERY_ROUNDS, True

        def planned() -> int:
            return first + (local if self.local else 0) + later * (self.rounds - 1) + RESERVED_CALLS

        for cut in ("round 3", "local", "round 2"):
            if planned() <= MAX_GROUNDED_CALLS:
                break
            if cut == "local":
                self.local = False
            else:
                self.rounds = min(self.rounds, int(cut[-1]) - 1)

    @staticmethod
    def allows(spent: int, more: int) -> bool:
        return spent + more + RESERVED_CALLS <= MAX_GROUNDED_CALLS


def plan_markets(brief: Brief, found: list[Market], progress: Progress = print) -> tuple[list[MarketPlan], Batch]:
    """Each market's native-language hypotheses and search hooks, and its recent law signals,
    all in parallel. A market whose plan failed keeps no hooks, so it isn't searched."""
    plans = [MarketPlan(market=m) for m in found]
    with Browser() as browser:  # law pages are often news sites that need one

        def hooks(p: MarketPlan) -> None:
            p.hooks = local_plan(brief, p.market)

        def laws(p: MarketPlan) -> None:
            p.laws = triggers.law_signals(brief, p.market, lambda url: fetch_html(url, browser))

        batch = run_parallel(
            [(f"Local searches in {p.market.label}", lambda p=p: hooks(p)) for p in plans]
            + [(f"Laws on reusing this data in {p.market.country}", lambda p=p: laws(p)) for p in plans],
            PARALLEL_LLM_CALLS, progress)
    return plans, batch


def search(session: Session, progress: Progress = print, seeds: list[tuple[str, str]] = (),
           crawler: Callable[[str], CrawlResult] = crawl, on_update: Callable[[], None] = lambda: None) -> Session:
    """Find, screen, check and confirm candidates for the session's brief. Stops before research.
    on_update is called after every save, so a page showing the session can refresh."""
    brief = session.brief
    timer = Timer(progress)
    session.meta.stage = "searching"
    sessions.save(session)
    lock = threading.Lock()

    def save() -> None:
        with lock:
            session.meta.timings = timer.total()
            sessions.save(session)
        on_update()

    def check_and_screen(label: str) -> None:
        """Confirm sources, then screen unscreened candidates. Sources come first because the
        screen reads them too (resolved and cached by then), and so every row says whether its
        source was confirmed. Excluded companies are skipped."""
        d = session.discovery
        with timer.step("Source checks"):
            session.discovery = check_sources(d, {c.domain for c in d.candidates if not c.excluded}, progress)
        save()
        todo = sum(not c.screened for c in session.discovery.candidates)
        with timer.step(label, f"{label}: {todo} companies"):
            screen(brief, session.discovery.candidates, progress=progress)
        save()

    def stop_if_partial(where: str) -> None:
        if session.discovery.partial:
            raise QuotaExceeded(f"quota ran out during {where}")

    try:
        with timer.step("Hypotheses", "Guessing who holds this data"):
            session.hypotheses = hypotheses(brief)
        save()

        found = find_markets(brief)
        budget = Budget(brief, len(found))
        spent = 0
        if found and budget.local:
            labels = ", ".join(m.label for m in found)
            with timer.step("Local markets", f"Planning native searches and checking laws: {labels}"):
                session.markets, batch = plan_markets(brief, found, progress)
            session.meta.errors.extend(batch.errors)
            spent += len(found)  # one grounded law call each
            save()
            if batch.quota_error:
                raise QuotaExceeded(batch.quota_error)

        r = 1
        with timer.step("Discovery searches", "Discovering candidate companies: round 1"):
            session.discovery = discover(brief, session.hypotheses, session.markets, progress)
        while True:
            save()
            stop_if_partial(f"discovery round {r}")
            check_and_screen("Screen")
            finish_round(session.discovery, brief, r)
            save()
            stats = session.discovery.round(r)
            spent += stats.calls
            progress(f"Round {r}: {stats.new} new companies, {stats.qualifying} qualifying")
            if r >= budget.rounds or stats.qualifying < ROUND_MIN_NEW:
                break
            calls = next_calls(session.discovery, brief, r, session.markets)
            if not calls or not budget.allows(spent, sum(calls.values())):
                break
            if r == 1 and (missing := uncovered_archetypes(session.discovery, brief)):
                with timer.step("Hypotheses", f"New hypotheses for {len(missing)} reasons nothing was found for"):
                    good = [f"{c.name}: {c.reason}" for c in session.discovery.candidates
                            if c.holder_type == "untapped" and (c.fit or 0) >= RELEVANT_FIT][:20]
                    try:
                        session.hypotheses = fill_archetypes(brief, missing, good) + session.hypotheses
                    except QuotaExceeded:
                        raise
                    except Exception as e:  # round 2 still runs, on the hypotheses it has
                        session.meta.errors.append(f"new hypotheses: {type(e).__name__}: {str(e)[:200]}")
            r += 1
            with timer.step("Discovery searches", f"Discovering candidate companies: round {r}"):
                session.discovery = run_round(brief, session.hypotheses, session.discovery, r, calls,
                                              session.markets, progress)

        seed_list = pick_seeds(session.discovery.candidates, explicit=list(seeds), types=seed_types(brief))
        tasks = final_tasks(session.discovery, brief, session.hypotheses, seed_list, session.markets)
        if tasks:
            with timer.step("Snowball searches", f"Snowballing from {len(seed_list)} holders, and searching "
                                                 "the customers of tool vendors found"):
                session.discovery = run_tasks(session.discovery, tasks, r + 1, progress)
            save()
            check_and_screen("Screen new")  # only the new companies
            finish_round(session.discovery, brief, r + 1)
            stop_if_partial("snowballing")

        if todo := triggers.to_check(session.discovery.candidates):
            with timer.step("Trigger checks", f"Checking {len(todo)} big or regulated organisations for "
                                              "insolvency, past data deals and laws"):
                errors, quota = triggers.check_all(brief, session.discovery.candidates, session.markets, progress)
            session.meta.errors.extend(errors)
            save()
            if quota:
                raise QuotaExceeded(quota)

        session.discovery.candidates = rank(session.discovery.candidates)
        session.meta.stage = "confirming"
        save()
        confirm_all(session, crawler, progress, timer, save)
        session.meta.stage = "done"
    except QuotaExceeded as e:
        session.meta.stage = "partial"
        session.meta.errors.append(f"Stopped early — Gemini quota: {str(e)[:300]}")
        progress("Quota ran out — saving what was found so far")

    session.discovery.candidates = rank(session.discovery.candidates)
    save()
    progress(f"Done in {session.meta.timings['total']:.0f}s")
    return session


def confirm_all(session: Session, crawler: Callable[[str], CrawlResult] = crawl, progress: Progress = print,
                timer: Timer | None = None, save: Callable[[], None] | None = None) -> None:
    """Confirm the best of each list (confirm.select), saving after each verdict so the list
    on screen fills in as they arrive. Raises QuotaExceeded after keeping the verdicts it got."""
    brief, timer = session.brief, timer or Timer(progress, session.meta.timings)
    save = save or (lambda: sessions.save(session))
    targets = confirm.select(session.discovery.candidates)
    if not targets:
        return
    crawl_slots = threading.Semaphore(PARALLEL_CRAWLS)

    def one(c: Candidate) -> None:
        with crawl_slots:
            cr = crawler(c.website)
        c.confirmation = confirm.confirm(brief, c, cr)
        rescore(c, brief)
        progress(f"{c.name}: {c.confirmation.verdict} — {c.confirmation.reason}")
        save()

    with timer.step("Confirmation", f"Confirming fit and saleability for the best {len(targets)} companies"):
        batch = run_parallel([(f"Confirming {c.domain}", lambda c=c: one(c)) for c in targets],
                             PARALLEL_RESEARCH, progress)
    session.meta.errors.extend(batch.errors)
    if batch.quota_error:
        raise QuotaExceeded(batch.quota_error)


def research(
    session: Session,
    domains: list[str],
    progress: Progress = print,
    crawler: Callable[[str], CrawlResult] = crawl,
) -> Session:
    """Deep crawl, research and contacts for the selected companies only, then re-rank."""
    brief = session.brief
    timer = Timer(progress, session.meta.timings)
    targets = [c for c in session.discovery.candidates if c.domain in domains]
    session.meta.selected = list(dict.fromkeys(session.meta.selected + [c.domain for c in targets]))

    crawl_slots = threading.Semaphore(PARALLEL_CRAWLS)

    def one(c: Candidate) -> None:
        with crawl_slots:
            cr = crawler(c.website)
        errors = [f"{c.domain}: crawl {e}" for e in cr.errors if e.startswith("homepage")]
        progress(f"Researching {c.domain}, finding contacts")
        with ThreadPoolExecutor(1) as pool:  # contacts need only the crawl, so run beside research
            contacts = pool.submit(find_contacts, c, cr)
            c.research = research_company(brief, c, cr)
            if c.holder_type == "untapped":  # research looks for the same triggers as the trigger check
                c.triggers = list({(t.kind, t.summary): t for t in c.triggers + c.research.triggers}.values())
                c.trigger_checked = True
            try:
                c.contacts = contacts.result()
            except QuotaExceeded:
                raise
            except Exception as e:  # keep the research, which is still useful
                errors.append(f"{c.domain}: contacts {type(e).__name__}: {str(e)[:200]}")
        rescore(c, brief)
        session.meta.errors.extend(errors)

    with timer.step("Research", f"Researching {len(targets)} companies"):
        batch = run_parallel(
            [(f"Deep crawl: {c.domain}", lambda c=c: one(c)) for c in targets], PARALLEL_RESEARCH, progress
        )
    session.meta.errors.extend(batch.errors)
    if batch.quota_error:
        session.meta.errors.append(f"Research stopped early — Gemini quota: {batch.quota_error[:300]}")
    session.discovery.candidates = rank(session.discovery.candidates)
    session.meta.timings = timer.total()
    sessions.save(session)
    progress(f"Researched {sum(c.research is not None for c in targets)} of {len(targets)}")
    return session


def funnel(d: Discovery) -> Funnel:
    cands = d.candidates
    return Funnel(
        found=len(d.hits),
        unique=len(cands),
        screened=sum(c.scores.data_match is not None for c in cands),
        relevant=sum(is_relevant(c) for c in cands),
        relevant_ready=sum(is_relevant(c) and c.holder_type == "ready" for c in cands),
        relevant_untapped=sum(is_relevant(c) and c.holder_type == "untapped" for c in cands),
        confirmed=sum(verdict(c) == "confirmed" and not c.killed for c in cands),
        possible=sum(verdict(c) == "possible" and not c.killed for c in cands),
        researched=sum(c.research is not None for c in cands),
        with_contacts=sum(any(k.verified for k in c.contacts) for c in cands),
        rounds=d.rounds,
    )


def contacts_csv(candidates: list[Candidate]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["company", "domain", "name", "role", "email", "phone", "linkedin_url",
                "source_url", "origin", "verified"])
    for c in candidates:
        for k in c.contacts:
            w.writerow([c.name, c.domain, k.name, k.role, k.email, k.phone, k.linkedin_url,
                        k.source_url, k.origin, k.verified])
    return buf.getvalue()


def candidates_csv(candidates: list[Candidate], brief: Brief) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["name", "domain", "website", "fit", "holder_type", "collector", "vendor", "data_owner", "data_match",
                "coverage", "volume", "licence", "licence_found", "saleability", *SALEABILITY_QUESTIONS, "obstacle",
                "path", *FACTS, "must_haves_not_met", "languages", "regions", "reason", "excluded", "channels",
                "source_verified", "source_urls", "killed", "kill_reason", "verdict", "verdict_reason",
                "archetype", "needs_trigger", "triggers", "markets", "customer_of", "first_round"])
    for c in candidates:
        s, sale = c.scores, c.current_saleability()
        untapped = c.holder_type == "untapped"
        scores = answers(c, brief) if untapped else dict.fromkeys(SALEABILITY_QUESTIONS)
        w.writerow([c.name, c.domain, c.website, c.fit, c.holder_type, c.collector, c.vendor, c.data_owner, s.data_match,
                    s.coverage, s.volume, s.licence, c.research.licence if c.research else None,
                    saleability_label(c, brief), *scores.values(), sale and sale.obstacle, sale and sale.path,
                    *((f.value if (f := c.fact(k)) else None) for k in FACTS),
                    ", ".join(must_failures(c, brief)),
                    ", ".join(c.languages), ", ".join(c.regions), c.reason, c.excluded,
                    " ".join(c.channels), c.source_verified, " ".join(c.source_urls), c.killed, c.kill_reason,
                    verdict(c), c.confirmation and c.confirmation.reason, c.archetype, c.needs_trigger,
                    "; ".join(f"{t.kind} {t.date}: {t.summary}" for t in c.triggers), ", ".join(c.markets),
                    ", ".join(c.tools), c.first_round])
    return buf.getvalue()
