"""Email step: a first outreach email to one contact at a researched company. One Gemini call,
no search. The draft is only shown and saved for the user to edit; nothing is sent."""

from worker import llm
from worker.archetypes import BY_KEY as ARCHETYPES
from worker.brief_template import CATEGORIES, Brief
from worker.config import MODEL
from worker.schemas import Candidate, Contact, EmailDraft

# What to ask for depends on what kind of holder the company is
ASKS = {
    "seller": "whether the data is available, how much there is, its format, the price, and "
              "whether they can share a sample",
    "collector": "whether they could record data to this specification (languages, speakers, volume), "
                 "how long it would take, how they price it per unit, and who would own the result",
    "untapped": "whether they would consider licensing data they already hold, and on what terms",
    "public": "the licence terms for using the data to train commercial models",
    "technology": "whether they hold recordings of their own (e.g. R&D or test data) that they "
                  "would consider licensing",
}
DEFAULT_ASK = "whether they hold this kind of data and would consider licensing it"

PROMPT = """Write a short first outreach email from Atom, a data acquisition company, to
{recipient} at {company} ({website}).

Atom is looking for: {brief}

What the client needs from a deal:
{terms}

What we know about {company}, from public pages:
{facts}

Ask: {ask}.

Rules:
- British English. Plain, polite, under 150 words. No hype.
- Only mention facts listed above; don't guess anything else about the company.
- Never state or suggest a price or the client's budget, and never commit Atom to anything
  (a purchase, volume, exclusivity or deadline). Suggest a short call as the next step.
- From the client's needs, ask only about the two or three that matter most for this company
  (e.g. permitted use, documents, a sample), as questions, not demands.
- Don't name the buyer; say "a client".
- Sign off as "[Your name], Atom".
- subject: short and specific, no clickbait.
"""


def _facts(c: Candidate) -> str:
    lines = [f"- {c.reason}"] if c.reason else []
    if r := c.research:
        lines.append(f"- Data held: {r.data_held}")
        lines += [f'- "{e.quote}" ({e.url})' for e in r.licence_evidence]
        if r.volume_estimate:
            lines.append(f"- Volume: {r.volume_estimate}")
            lines += [f'- "{e.quote}" ({e.url})' for e in r.volume_evidence]
        lines += [f"- {CATEGORIES[ch.category].label}: {ch.finding}" for ch in r.checks if ch.verdict != "unclear"]
    return "\n".join(lines) or "- Nothing beyond its name and website."


# Deal terms the email may ask about: every E category except the budget, which is never stated
EMAIL_TERMS = [k for k, c in CATEGORIES.items() if "E" in c.used_for and k != "budget"]


def _terms(brief: Brief) -> str:
    lines = [f"- {CATEGORIES[k].label}: {brief.value_text(k)}" for k in EMAIL_TERMS if brief.is_set(k)]
    return "\n".join(lines) or "- Nothing specific yet."


def _ask(c: Candidate) -> str:
    """Untapped holders may not see their data as an asset: lead with the pitch for why they hold
    it. Ones already open to licensing get a direct question about terms."""
    ask = ASKS.get(c.holder_type, DEFAULT_ASK)
    if c.holder_type != "untapped":
        return ask
    if c.selling_status == "open_to_it":
        ask = "on what terms they would license data they hold (they appear open to it)"
    elif c.archetype:
        ask += f". Open with this angle, in your own words: {ARCHETYPES[c.archetype].pitch}"
    if c.data_owner == "customers":
        ask += ". Their customers own the data, so mention licensing with their customers' consent"
    return ask


def build_prompt(brief: Brief, c: Candidate, contact: Contact | None) -> str:
    if contact and contact.name:
        recipient = contact.name + (f" ({contact.role})" if contact.role else "")
    else:
        recipient = (contact.role if contact and contact.role else "the team")
    return PROMPT.format(
        recipient=recipient, company=c.name, website=c.website, brief=brief.summary("SR"), terms=_terms(brief),
        facts=_facts(c), ask=_ask(c),
    )


def draft_email(brief: Brief, c: Candidate, contact: Contact | None) -> EmailDraft:
    draft = llm.ask(build_prompt(brief, c, contact), EmailDraft, model=MODEL)
    return draft.model_copy(update={"to": contact.email if contact else None})
