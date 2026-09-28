"""Step 3: free-text buyer brief -> DataSpec.

Run: uv run python -m worker.steps.parse_brief "brief text"
"""

import sys

from worker import llm, runs
from worker.config import MODEL
from worker.schemas import DataSpec

PROMPT = """You are helping a data broker understand what a buyer wants to acquire.
Turn the buyer's brief below into a structured data specification.

Rules:
- Only include what the brief states or clearly implies. Do not invent requirements.
- min_hours: the minimum volume in hours if stated, otherwise null.
- licence_needed: "training_only" if the data is for training models only, "commercial"
  for broader commercial use, "exclusive" if exclusivity is asked for, "unknown" if unclear.
- label_types: annotations the buyer needs (e.g. transcript, speaker diarization); empty if none.

Buyer brief:
{brief}
"""


def parse_brief(brief: str) -> DataSpec:
    return llm.ask(PROMPT.format(brief=brief), DataSpec, model=MODEL)


if __name__ == "__main__":
    brief = " ".join(sys.argv[1:]) or sys.exit("Usage: parse_brief \"<brief text>\"")
    run = runs.new_run()
    (run / "brief.txt").write_text(brief, encoding="utf-8")
    spec = parse_brief(brief)
    print(spec.model_dump_json(indent=2))
    print("saved", runs.save(run, "spec", spec))
