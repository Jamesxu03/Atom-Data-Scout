"""Refine step: free-text buyer brief -> BriefDraft (a filled-in Brief plus options and open
questions for the form). The user edits the draft; the saved Brief is what the search uses."""

from worker import llm
from worker.config import MODEL
from worker.schemas import MUST_HAVE_FIELDS, Brief, BriefDraft

PROMPT = """You are helping a data broker pin down what a buyer wants to acquire. Turn the
buyer's text below into a complete data brief that a person will review in a form.

Rules:
- Fill each field from the text.
- Where the text says nothing about a field, suggest a sensible value for this kind of data
  and list that field name in `suggested`. Use exactly the field names of the brief.
- `options`: for any field the buyer might want to vary, give 2 to 5 alternative values (e.g.
  other languages or dialects, nearby regions, other formats, other volume units). One entry
  per field; each option is a short value, not a sentence.
- `questions`: things to check with the buyer before searching, e.g. an unclear volume or
  licence, or whether dialects count.
- `must_have`: only fields the text states as hard requirements ("must", "only",
  "at least"). Never a field you suggested.
- data_type: a short noun phrase, e.g. "multi-speaker meeting audio".
- languages: include dialects where the text names them, e.g. "Gulf Arabic".
- volume: the minimum amount and its unit if stated, e.g. 1000 and "hours".
- licence: "training_only" if the data is for training models only, "commercial" for broader
  commercial use, "exclusive" if exclusivity is asked for, "unknown" if unclear.
- holder_types: the kinds of source the buyer accepts. All four ("vendor", "public",
  "by_product", "technology") unless the text rules some out.
- exclusions: company domains the text says to skip; otherwise empty.
- notes: anything else in the text that matters and fits no field.

Buyer text:
{text}
"""

_BRIEF_FIELDS = set(Brief.model_fields)


def draft_brief(text: str) -> BriefDraft:
    draft = llm.ask(PROMPT.format(text=text), BriefDraft, model=MODEL)
    return clean(draft)


def clean(draft: BriefDraft) -> BriefDraft:
    """Keep only real field names, and never let a suggested field become a requirement."""
    suggested = [f for f in dict.fromkeys(draft.suggested) if f in _BRIEF_FIELDS]
    must_have = [f for f in dict.fromkeys(draft.brief.must_have)
                 if f in MUST_HAVE_FIELDS and f not in suggested]
    options = [o for o in draft.options if o.field in _BRIEF_FIELDS and o.options]
    brief = draft.brief.model_copy(update={"must_have": must_have})
    return draft.model_copy(update={"brief": brief, "suggested": suggested, "options": options})
