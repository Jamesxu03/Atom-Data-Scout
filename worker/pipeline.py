"""End-to-end: brief -> spec -> hypotheses -> candidates -> crawl -> profile -> score -> ranked results."""

import csv
import io
import time
from collections.abc import Callable

from worker.demo import demo_results
from worker.schemas import Result
from worker.scoring import compute_total

Progress = Callable[[str], None]

STEPS = [
    "Analysing brief",
    "Guessing who holds this data",
    "Searching for candidate companies",
    "Crawling websites and privacy policies",
    "Extracting company profiles",
    "Scoring and drafting emails",
]


def run(brief: str, demo: bool = False, progress: Progress = print) -> list[Result]:
    if not demo:
        # Steps 3–8 need Gemini / Companies House keys; wire them in here as they're built.
        raise NotImplementedError("Live pipeline not built yet — run with demo=True.")

    for step in STEPS:
        progress(step)
        time.sleep(0.4)
    results = demo_results()

    for r in results:
        r.score.total = compute_total(r.score)
    return sorted(results, key=lambda r: r.score.total, reverse=True)


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


if __name__ == "__main__":
    for r in run("demo brief", demo=True):
        print(f"{r.score.total:>5}  {r.profile.name}  ({r.profile.company_status})")
