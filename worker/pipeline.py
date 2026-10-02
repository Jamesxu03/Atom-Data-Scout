"""The two jobs a session runs, each saving to the session folder as it goes:

  search()    brief -> hypotheses -> discovery -> source checks + screen -> snowball
              -> source checks + screen -> one list ranked by fit
  research()  for the companies the user selects: deep crawl, research (licence for ready
              sources, saleability from news and policies for untapped holders, volume, data
              held) and contacts, then re-score and re-rank

Independent calls run in parallel. If the Gemini quota runs out mid-job, everything gathered
so far is saved and the session is marked partial instead of being lost.
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
from worker.config import PARALLEL_CRAWLS, PARALLEL_RESEARCH
from worker.crawl import CrawlResult, crawl
from worker.discovery import Discovery, check_sources, discover, pick_seeds, seed_types, snowball
from worker.llm import QuotaExceeded
from worker.parallel import run_parallel
from worker.schemas import FACTS, SALEABILITY_QUESTIONS, Candidate, Funnel
from worker.scoring import answers, is_relevant, must_failures, rank, rescore, saleability_label
from worker.sessions import Session
from worker.steps.contacts import find_contacts
from worker.steps.hypotheses import hypotheses
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


def search(session: Session, progress: Progress = print, seeds: list[tuple[str, str]] = ()) -> Session:
    """Find, screen and score candidates for the session's brief. Stops after scoring."""
    brief = session.brief
    timer = Timer(progress)
    session.meta.stage = "searching"
    sessions.save(session)

    def save() -> None:
        session.meta.timings = timer.total()
        sessions.save(session)

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

    try:
        with timer.step("Hypotheses", "Guessing who holds this data"):
            session.hypotheses = hypotheses(brief)
        save()
        with timer.step("Discovery searches", "Discovering candidate companies"):
            session.discovery = discover(brief, session.hypotheses, progress=progress)
        save()
        if session.discovery.partial:
            raise QuotaExceeded("quota ran out during discovery")
        check_and_screen("Screen")

        seed_list = pick_seeds(session.discovery.candidates, explicit=list(seeds), types=seed_types(brief))
        if seed_list:
            with timer.step("Snowball searches", f"Snowballing from {len(seed_list)} relevant companies"):
                session.discovery = snowball(session.discovery, seed_list, brief.languages, brief.regions,
                                             progress=progress)
            save()
            check_and_screen("Screen new")  # only the snowball companies
            if session.discovery.partial:
                raise QuotaExceeded("quota ran out during snowballing")
        session.meta.stage = "done"
    except QuotaExceeded as e:
        session.meta.stage = "partial"
        session.meta.errors.append(f"Stopped early — Gemini quota: {str(e)[:300]}")
        progress("Quota ran out — saving what was found so far")

    session.discovery.candidates = rank(session.discovery.candidates)
    save()
    progress(f"Done in {session.meta.timings['total']:.0f}s")
    return session


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
        researched=sum(c.research is not None for c in cands),
        with_contacts=sum(any(k.verified for k in c.contacts) for c in cands),
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
                "source_verified", "source_urls"])
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
                    " ".join(c.channels), c.source_verified, " ".join(c.source_urls)])
    return buf.getvalue()
