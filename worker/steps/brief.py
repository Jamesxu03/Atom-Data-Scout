"""Refine step: free-text buyer brief -> BriefDraft, filled in against the template in
worker/brief_template.py. The Gemini schemas are built from the template with each category's
choices as an enum, so the model can only return valid choices. The template is too big for one
schema, so it is filled in two parallel calls (see FILL_PARTS). The user then edits the draft
in the form; the saved Brief is what the search uses."""

from concurrent.futures import ThreadPoolExecutor

from worker import llm
from worker.brief_template import FILL_PARTS, BriefDraft, category_guide, from_fill
from worker.config import MODEL

PROMPT = """You are helping a data broker pin down what a buyer wants to acquire. Fill in the
brief template from the buyer's text below. A person will review every field in a form.

Fill in only the categories in your answer's schema; the full list is below for context.

For each category, set status:
- "must": the text states it as a hard requirement ("must", "only", "at least", "required").
- "nice": the text states it as a preference, or states it without saying how strict it is.
- "inferred": the text doesn't state it, but a sensible default clearly follows (e.g. speaker
  labels for multi-speaker meeting audio, or a region from the languages named).
- "not_set": the text doesn't say and nothing clearly follows. Most commercial categories
  (budget, pricing, timeline) are not_set unless stated.

Rules:
- Only use the listed choices. Where a category takes values of its own (languages, domain
  "other", specific countries, exclusions), use short values, e.g. "Gulf Arabic".
- description: what data, in a few words, e.g. "multi-speaker meeting audio".
- Fill the modality add-ons (audio, video/image, text, tabular) only for the modality chosen.
- volume: the target amount and its unit, e.g. 2000000 and "data points".
- licence (D1): the licence types the buyer would accept. Training for a commercial product
  usually rules out "Restricted / Conditional".
- questions: things to check with the buyer before searching, e.g. an unclear volume or
  licence, whether dialects count, or where the data will be processed.

The categories, by key:
{guide}

Buyer text:
{text}
"""


def draft_brief(text: str) -> BriefDraft:
    prompt = PROMPT.format(guide=category_guide(), text=text)
    with ThreadPoolExecutor(len(FILL_PARTS)) as pool:
        parts = list(pool.map(lambda schema: llm.ask(prompt, schema, model=MODEL), FILL_PARTS))
    return from_fill(parts)
