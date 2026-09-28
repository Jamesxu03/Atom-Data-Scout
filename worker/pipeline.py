"""End-to-end: brief -> spec -> hypotheses -> discovery -> source checks + screen -> snowball
-> source checks + screen -> deep crawl + contacts. Independent calls run in parallel.

If the Gemini quota runs out mid-run, everything gathered so far is saved and returned with
partial=True instead of being lost.

Profile extraction and scoring aren't built yet, so `results` stays empty.
"""

import csv
import io
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from pydantic import BaseModel

from worker import runs
from worker.config import FIND_CONTACTS, MAX_PROFILED, PARALLEL_CRAWLS
from worker.crawl import CrawlResult, crawl
from worker.discovery import Discovery, check_sources, discover, pick_seeds, snowball
from worker.llm import QuotaExceeded
from worker.parallel import run_parallel
from worker.schemas import Candidate, Funnel, Result
from worker.steps.contacts import find_contacts
from worker.steps.hypotheses import HypothesisList, hypotheses
from worker.steps.parse_brief import parse_brief
from worker.steps.screen import rank, relevant, screen

Progress = Callable[[str], None]


class RunOutput(BaseModel):
    funnel: Funnel
    candidates: list[Candidate]  # every deduped candidate, with relevance labels
    crawls: list[CrawlResult] = []
    results: list[Result] = []  # scored profiles (not built yet)
    run_dir: str | None = None
    partial: bool = False  # quota ran out: stages after that point are missing or incomplete
    errors: list[str] = []
    timings: dict[str, float] = {}  # seconds per step, in run order, plus "total"


class Timer:
    """Times each step and reports it through progress, so slow steps are visible."""

    def __init__(self, progress: Progress):
        self.progress = progress
        self.timings: dict[str, float] = {}
        self.started = time.perf_counter()

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
        return {**self.timings, "total": round(time.perf_counter() - self.started, 1)}


def run(
    brief: str,
    progress: Progress = print,
    seeds: list[tuple[str, str]] = (),
    crawler: Callable[[str], CrawlResult] = crawl,
) -> RunOutput:
    run_dir = runs.new_run()
    (run_dir / "brief.txt").write_text(brief, encoding="utf-8")
    timer = Timer(progress)

    with timer.step("Analyse brief"):
        spec = parse_brief(brief)
    runs.save(run_dir, "spec", spec)

    with timer.step("Hypotheses", "Guessing who holds this data"):
        hyps = HypothesisList(hypotheses=hypotheses(spec))
    runs.save(run_dir, "hypotheses", hyps)

    d = Discovery(hits=[], candidates=[])
    crawls: list[CrawlResult] = []
    errors: list[str] = []
    partial = False

    def check_and_screen(label: str) -> None:
        """Confirm sources, then screen unlabelled candidates. Sources come first because the
        screen reads them too (resolved and cached by then), and so every row in the export
        says whether its source was confirmed. Companies already screened "no" are skipped."""
        nonlocal d
        with timer.step("Source checks"):
            d = check_sources(d, {c.domain for c in d.candidates if c.relevance != "no"}, progress=progress)
        runs.save(run_dir, "discovery", d)
        with timer.step(label, f"{label}: {sum(c.relevance is None for c in d.candidates)} companies"):
            screen(spec, d.candidates, progress=progress)
        runs.save(run_dir, "discovery", d)

    try:
        with timer.step("Discovery searches", "Discovering candidate companies"):
            d = discover(spec, hyps.hypotheses, progress=progress)
        runs.save(run_dir, "discovery", d)
        if d.partial:
            raise QuotaExceeded("quota ran out during discovery")
        check_and_screen("Screen")

        seed_list = pick_seeds(d.candidates, explicit=list(seeds))
        if seed_list:
            with timer.step("Snowball searches", f"Snowballing from {len(seed_list)} relevant companies"):
                d = snowball(d, seed_list, languages=spec.languages, progress=progress)
            runs.save(run_dir, "discovery", d)
            check_and_screen("Screen new")  # only the snowball companies
            if d.partial:
                raise QuotaExceeded("quota ran out during snowballing")

        with timer.step("Deep crawl + contacts"):
            crawls = _profile(relevant(d.candidates)[:MAX_PROFILED], crawler, progress, errors)
    except QuotaExceeded as e:
        partial = True
        errors.append(f"Stopped early — Gemini quota: {str(e)[:300]}")
        progress("Quota ran out — saving what was found so far")

    d.candidates = rank(d.candidates)
    runs.save(run_dir, "discovery", d)
    keep = relevant(d.candidates)
    funnel = Funnel(
        found=len(d.hits),
        deduped=len(d.candidates),
        relevant=len(keep),
        profiled=sum(1 for cr in crawls if cr.homepage),
        with_contacts=sum(1 for c in d.candidates if any(k.verified for k in c.contacts)),
    )
    out = RunOutput(
        funnel=funnel, candidates=d.candidates, crawls=crawls, run_dir=str(run_dir),
        partial=partial, errors=d.errors + errors, timings=timer.total(),
    )
    runs.save(run_dir, "output", out)
    progress(f"Done in {out.timings['total']:.0f}s")
    return out


def _profile(
    candidates: list[Candidate],
    crawler: Callable[[str], CrawlResult],
    progress: Progress,
    errors: list[str],
) -> list[CrawlResult]:
    """Deep crawl + contacts for each candidate, a few companies at a time."""

    def one(c: Candidate) -> CrawlResult:
        cr = crawler(c.website)
        if cr.homepage:
            progress(f"Finding contacts: {c.domain}")
            try:
                c.contacts = find_contacts(c, cr, use_search=FIND_CONTACTS)
            except QuotaExceeded:
                raise
            except Exception as e:  # keep the run going; the crawl result is still useful
                cr.errors.append(f"contacts: {type(e).__name__}: {str(e)[:200]}")
        return cr

    batch = run_parallel(
        [(f"Deep crawl: {c.domain}", lambda c=c: one(c)) for c in candidates], PARALLEL_CRAWLS, progress
    )
    errors.extend(batch.errors)
    if batch.quota_error:
        raise QuotaExceeded(batch.quota_error)
    return [cr for _, cr in batch.results]


def to_csv(results: list[Result]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "rank", "name", "website", "total", "data_match", "estimated_volume", "rights_clarity",
        "seller_motivation", "contactability", "company_status", "country", "registration_number",
        "records_data", "retention", "policy_allows_transfer", "policy_allows_training",
    ])
    for i, r in enumerate(results, 1):
        p, s = r.profile, r.score
        w.writerow([
            i, p.name, p.website, s.total, s.data_match, s.estimated_volume, s.rights_clarity,
            s.seller_motivation, s.contactability, p.company_status, p.country, p.registration_number,
            p.records_data, p.retention, p.policy_allows_transfer, p.policy_allows_training,
        ])
    return buf.getvalue()


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


def candidates_csv(candidates: list[Candidate]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["name", "domain", "website", "relevance", "holder_type", "data_owner", "languages", "reason",
                "channels", "source_verified", "source_urls"])
    for c in candidates:
        w.writerow([c.name, c.domain, c.website, c.relevance, c.holder_type, c.data_owner, ", ".join(c.languages),
                    c.relevance_reason, " ".join(c.channels), c.source_verified, " ".join(c.source_urls)])
    return buf.getvalue()


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        sys.exit('Usage: python -m worker.pipeline "<brief>"')
    out = run(" ".join(sys.argv[1:]))
    print(out.funnel)
    for c in relevant(out.candidates):
        print(f"  {c.relevance:5} {c.domain:30} {','.join(c.channels)}")
