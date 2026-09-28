"""End-to-end: brief -> spec -> hypotheses -> discovery -> relevance screen -> deep crawl.

Profile extraction and scoring aren't wired into live runs yet; demo mode shows the full output.
"""

import csv
import io
import time
from collections.abc import Callable

from pydantic import BaseModel

from worker import runs
from worker.config import MAX_PROFILED
from worker.crawl import CrawlResult, crawl
from worker.demo import demo_candidates, demo_results
from worker.discovery import discover
from worker.schemas import Candidate, Funnel, Result
from worker.scoring import compute_total
from worker.steps.hypotheses import HypothesisList, hypotheses
from worker.steps.parse_brief import parse_brief
from worker.steps.screen import relevant, screen

Progress = Callable[[str], None]


class RunOutput(BaseModel):
    funnel: Funnel
    candidates: list[Candidate]  # every deduped candidate, with relevance labels
    crawls: list[CrawlResult] = []
    results: list[Result] = []  # scored profiles (demo only for now)
    run_dir: str | None = None


def run(
    brief: str,
    demo: bool = False,
    progress: Progress = print,
    seeds: list[tuple[str, str]] = (),
    crawler: Callable[[str], CrawlResult] = crawl,
) -> RunOutput:
    if demo:
        return _demo_run(progress)

    run_dir = runs.new_run()
    (run_dir / "brief.txt").write_text(brief, encoding="utf-8")

    progress("Analysing brief")
    spec = parse_brief(brief)
    runs.save(run_dir, "spec", spec)

    progress("Guessing who holds this data")
    hyps = HypothesisList(hypotheses=hypotheses(spec))
    runs.save(run_dir, "hypotheses", hyps)

    progress("Discovering candidate companies")
    d = discover(spec, hyps.hypotheses, seeds=seeds, progress=progress)
    runs.save(run_dir, "discovery", d)

    progress(f"Screening {len(d.candidates)} homepages for relevance")
    screen(spec, d.candidates, progress=progress)
    runs.save(run_dir, "discovery", d)
    keep = relevant(d.candidates)

    crawls = []
    for c in keep[:MAX_PROFILED]:
        progress(f"Deep crawl: {c.domain}")
        crawls.append(crawler(c.website))

    funnel = Funnel(
        found=len(d.hits),
        deduped=len(d.candidates),
        relevant=len(keep),
        profiled=sum(1 for cr in crawls if cr.homepage),
    )
    out = RunOutput(funnel=funnel, candidates=d.candidates, crawls=crawls, run_dir=str(run_dir))
    runs.save(run_dir, "output", out)
    return out


def _demo_run(progress: Progress) -> RunOutput:
    for step in ["Analysing brief", "Guessing who holds this data",
                 "Discovering candidate companies", "Screening homepages", "Deep crawl"]:
        progress(step)
        time.sleep(0.3)
    results = demo_results()
    for r in results:
        r.score.total = compute_total(r.score)
    results.sort(key=lambda r: r.score.total, reverse=True)
    candidates = demo_candidates()
    keep = relevant(candidates)
    return RunOutput(
        funnel=Funnel(found=len(candidates) + 4, deduped=len(candidates),
                      relevant=len(keep), profiled=len(results)),
        candidates=candidates,
        results=results,
    )


def to_csv(results: list[Result]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "rank", "name", "website", "total", "data_match", "estimated_volume", "rights_clarity",
        "seller_motivation", "contactability", "company_status", "company_number",
        "records_data", "retention", "policy_allows_transfer", "policy_allows_training",
    ])
    for i, r in enumerate(results, 1):
        p, s = r.profile, r.score
        w.writerow([
            i, p.name, p.website, s.total, s.data_match, s.estimated_volume, s.rights_clarity,
            s.seller_motivation, s.contactability, p.company_status, p.company_number,
            p.records_data, p.retention, p.policy_allows_transfer, p.policy_allows_training,
        ])
    return buf.getvalue()


def candidates_csv(candidates: list[Candidate]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["name", "domain", "website", "relevance", "reason", "channels", "source_urls"])
    for c in candidates:
        w.writerow([c.name, c.domain, c.website, c.relevance, c.relevance_reason,
                    " ".join(c.channels), " ".join(c.source_urls)])
    return buf.getvalue()


if __name__ == "__main__":
    import sys

    out = run(" ".join(sys.argv[1:]) or "demo", demo=len(sys.argv) < 2)
    print(out.funnel)
    for c in relevant(out.candidates):
        print(f"  {c.relevance:5} {c.domain:30} {','.join(c.channels)}")
