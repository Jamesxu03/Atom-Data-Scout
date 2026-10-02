"""Brief -> 5–10 HolderHypotheses (what kinds of business hold this data)."""

from pydantic import BaseModel, Field

from worker import archetypes, llm
from worker.brief_template import Brief
from worker.config import HYPOTHESES_THINKING_LEVEL, MODEL
from worker.schemas import HolderHypothesis


class HypothesisList(BaseModel):
    hypotheses: list[HolderHypothesis] = Field(min_length=5, max_length=10)


PROMPT = """You are a data-acquisition scout. A buyer wants the dataset described below.
Most datasets like this are not sold by "data vendors" — they sit inside businesses that
accumulated them as a by-product of running their service.

Your job: list 5 to 10 distinct categories of business likely to hold this data.

Think broadly. Cover different reasons a business ends up holding data like this, not only the
obvious one; use these as a checklist:
{archetypes}

Also:
- Companies anywhere in the world, not only in the buyer's country; where the buyer names
  languages or regions, include businesses based in or serving them.
- Niche or regional players, not just the famous ones.

For each category:
- category: short name for the type of business
- rationale: why this business would have the data, how much, and whether it is likely
  to come with labels and transferable rights
- search_queries: 3–5 web search queries that would surface real companies of this type.

How to write search_queries — this matters most:
- Describe the PRODUCT OR SERVICE that generates the data, in the words these companies use
  about themselves on their own homepages and in their marketing.
- Never use dataset language. Companies holding data as a by-product don't describe
  themselves as having "datasets", "corpora", "training data", "hours of audio" or
  "annotated data" — so those words won't find them.
- Good (for meeting audio): "AI meeting assistant that records and transcribes calls",
  "conference call service for businesses", "sales call recording and coaching software".
- Bad: "meeting audio dataset", "multi-speaker speech corpus", "buy transcribed audio".
- Vary the queries: different product names, customer types and regions, so they surface
  different companies rather than the same market leaders.

Order categories from most to least promising.

The buyer wants:
{brief}
"""


def hypotheses(brief: Brief) -> list[HolderHypothesis]:
    prompt = PROMPT.format(brief=brief.summary("S"), archetypes=archetypes.guide(brief.chosen("modality")))
    result = llm.ask(prompt, HypothesisList, model=MODEL, thinking=HYPOTHESES_THINKING_LEVEL)
    return result.hypotheses

