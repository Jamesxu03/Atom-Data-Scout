"""Step 4: DataSpec -> 5–10 HolderHypotheses (what kinds of business hold this data).

Run: uv run python -m worker.steps.hypotheses [run_dir]   (defaults to latest run)
"""

import sys
from pathlib import Path

from pydantic import BaseModel, Field

from worker import runs
from worker.config import MODEL_PRO
from worker.llm import ask
from worker.schemas import DataSpec, HolderHypothesis


class HypothesisList(BaseModel):
    hypotheses: list[HolderHypothesis] = Field(min_length=5, max_length=10)


PROMPT = """You are a data-acquisition scout. A buyer wants the dataset described below.
Most datasets like this are not sold by "data vendors" — they sit inside businesses that
accumulated them as a by-product of running their service.

Your job: list 5 to 10 distinct categories of business likely to hold this data.

Think broadly:
- Businesses whose product captures this data in normal operation (e.g. recording, hosting,
  transcribing, moderating, customer support).
- Businesses whose staff or contractors produced it (e.g. labelling, research, outsourcing).
- Businesses that are shut down, insolvent, in administration or liquidation — their data
  assets are often sold off by administrators and are especially worth finding.
- Niche or regional players, not just the famous ones.

For each category:
- category: short name for the type of business
- rationale: why this business would have the data, how much, and whether it is likely
  to come with labels and transferable rights
- search_queries: 3–5 web search queries that would surface real companies of this type
  (include at least one aimed at insolvent / closed companies, e.g. "administration",
  "liquidation", "shut down", "acquired")

Order categories from most to least promising.

Data specification:
{spec}
"""


def hypotheses(spec: DataSpec) -> list[HolderHypothesis]:
    result = ask(PROMPT.format(spec=spec.model_dump_json(indent=2)), HypothesisList, model=MODEL_PRO)
    return result.hypotheses


if __name__ == "__main__":
    run = Path(sys.argv[1]) if len(sys.argv) > 1 else runs.latest_run()
    spec = runs.load(run, "spec", DataSpec)
    hyps = HypothesisList(hypotheses=hypotheses(spec))
    for i, h in enumerate(hyps.hypotheses, 1):
        print(f"\n{i}. {h.category}\n   {h.rationale}\n   queries: {h.search_queries}")
    print("\nsaved", runs.save(run, "hypotheses", hyps))
