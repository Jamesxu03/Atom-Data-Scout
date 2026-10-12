"""Email step: a first outreach email to one contact at a researched company. One Gemini call,
no search. The draft is only shown and saved for the user to edit; nothing is sent."""

from worker import llm
from worker.brief_template import CATEGORIES, OPEN, OPEN_CONDITIONS, RESTRICTED, Brief
from worker.schemas import Candidate, Contact, EmailDraft

# What to ask for depends on the holder type and, for a ready source, the licence research found
ASK_SELLER = ("whether the data is available, how much there is, its format, the price, and whether they "
              "can share a sample")
ASK_OPEN = "the licence terms for using the data to train commercial models"
ASK_COLLECTOR = ("whether they could record data to this specification (languages, speakers, volume), how "
                 "long it would take, how they price it per unit, and who would own the result")
ASK_UNTAPPED = "whether they would consider licensing data they already hold, and on what terms"
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
    if s := c.current_saleability():
        lines += [f"- {a.reason}" for q in ("exists", "allowed", "willing") if (a := getattr(s, q)).verified and a.reason]
    lines += [f"- {t.summary} ({t.date})" for t in c.triggers]
    return "\n".join(lines) or "- Nothing beyond its name and website."


# What the email may ask about: every E category (the prompt keeps any budget in them unsaid)
EMAIL_TERMS = [k for k, c in CATEGORIES.items() if "E" in c.used_for]


def _terms(brief: Brief) -> str:
    lines = [f"- {CATEGORIES[k].label}: {brief.value_text(k)}" for k in EMAIL_TERMS if brief.is_set(k)]
    return "\n".join(lines) or "- Nothing specific yet."


def _ask(c: Candidate) -> str:
    if c.holder_type == "ready":
        if c.collector:
            return ASK_COLLECTOR
        published = c.research and c.research.licence in (OPEN, OPEN_CONDITIONS, RESTRICTED)
        return ASK_OPEN if published else ASK_SELLER
    if c.holder_type != "untapped":
        return DEFAULT_ASK
    s = c.current_saleability()
    if s and (s.willing.score or 0) >= 4:  # e.g. selling assets, or done AI data deals before
        ask = "on what terms they would license data they hold"
    else:
        ask = ASK_UNTAPPED + ". They may not think of it as something to sell, so say briefly why it has value"
    if s and s.path:
        ask += f". A possible way forward, if it fits: {s.path}"
    elif c.data_owner == "customers":
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
    draft = llm.ask(build_prompt(brief, c, contact), EmailDraft, **llm.step("email"))
    return draft.model_copy(update={"to": contact.email if contact else None})
