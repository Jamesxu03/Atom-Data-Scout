"""Brief -> 5–10 HolderHypotheses (what kinds of organisation hold this data), searched from the
holder's side: the organisation that records and keeps the data, not the vendor whose software
records it. Each hypothesis is one of three sides:

- organisation: records and keeps the data in its own work, so it can license it
- regulated: a big brand or regulated institution, searched through what would make it sell
  (insolvency, a past data deal, a law change), since it rarely sells otherwise
- vendor: makes the recording tool; only a way to its customers

Organisation hypotheses come first, so product search spends its calls on them. Two more calls
use the same rules: local_plan() writes queries natively for one market (WS2), and
fill_archetypes() covers reasons to hold data that a search found nothing for yet (WS3).
"""

from pydantic import BaseModel, Field

from worker import archetypes, llm
from worker.brief_template import Brief
from worker.schemas import HolderHypothesis, LocalHooks, Market

SIDE_ORDER = {"organisation": 0, "regulated": 1, "vendor": 2}


class HypothesisList(BaseModel):
    hypotheses: list[HolderHypothesis] = Field(min_length=5, max_length=10)


class ArchetypeHypotheses(BaseModel):
    hypotheses: list[HolderHypothesis]


SIDES = """- side:
  - "organisation": it records and keeps this data in its own work and owns it, e.g. a
    contact-centre outsourcer, a training provider, a council, a conference organiser, a
    focus-group facility, a court-reporting firm.
  - "regulated": a big brand or regulated institution (bank, insurer, airline, telco, hospital
    group). These rarely sell data in normal times, so write its search_queries for what would
    make one sell: insolvency, administration, a unit closed or for sale, a past data deal or
    dataset release, or a new law that allows reuse.
  - "vendor": it makes the software or hardware that records the data, so its customers hold
    it. Tools rarely own their customers' data; they matter only as a way to those customers.
- owner: who owns the data: the organisation itself, or its customers.
- licence_route: the likely route to a licence, in one line, e.g. "its own staff recordings",
  "consent already in its terms", "kept for a regulator, needs a reuse clause", "a closed
  product", "administrators selling the assets"."""

QUERY_RULES = """How to write search_queries — this matters most:
- Search from the holder's side: the organisation that records and stores the data, not the
  vendor whose software records it.
- Describe the organisation's own work, in the words these organisations use about themselves
  on their own sites.
- Never use dataset language. Organisations holding data as a by-product don't describe
  themselves as having "datasets", "corpora", "training data", "hours of audio" or
  "annotated data", so those words won't find them.
- Good (for meeting audio): "contact centre outsourcing for insurers", "corporate training
  provider recorded workshops library", "council meeting webcast archive", "focus group
  facility with recording suites", "Verint customer story contact centre outsourcer".
- Good for a regulated institution: "insurer enters administration 2025", "telco sells
  customer service subsidiary", "bank AI data partnership call recordings".
- Bad: "AI meeting assistant that records and transcribes calls" (finds tools, and tools
  rarely own their customers' recordings); "meeting audio dataset", "multi-speaker speech
  corpus", "buy transcribed audio" (dataset language).
- Vary the queries: different services, customer types and regions, so they surface different
  organisations rather than the same market leaders."""

PROMPT = """You are a data-acquisition scout. A buyer wants the dataset described below.
Most datasets like this are not sold by "data vendors": they sit inside organisations that
recorded them while running their business, and own them.

Your job: list 5 to 10 distinct kinds of organisation likely to hold this data. At least 4
must be "organisation" and at most 2 "vendor".

Think broadly. Cover different reasons an organisation ends up holding data like this, not only
the obvious one; use these as a checklist:
{archetypes}

Also:
- Organisations anywhere in the world, not only in the buyer's country; where the buyer names
  languages or regions, include organisations based in or serving them.
- Niche or regional players, not just the famous ones.

For each one:
- category: short name for the kind of organisation
{sides}
- rationale: why it would have the data, how much, whether it comes with labels, and whether
  the rights would transfer
- search_queries: 3–5 web search queries that would surface real organisations of this kind.

{queries}

Order from most to least promising.

The buyer wants:
{brief}
"""


def _ordered(hyps: list[HolderHypothesis]) -> list[HolderHypothesis]:
    """Organisations first, then regulated institutions, then vendors; the model's order within each."""
    return sorted(hyps, key=lambda h: SIDE_ORDER[h.side])


def hypotheses(brief: Brief) -> list[HolderHypothesis]:
    prompt = PROMPT.format(brief=brief.summary("S"), archetypes=archetypes.guide(brief.chosen("modality")),
                           sides=SIDES, queries=QUERY_RULES)
    return _ordered(llm.ask(prompt, HypothesisList, **llm.step("hypotheses")).hypotheses)


LOCAL_PROMPT = """You are a data-acquisition scout searching {country} in {language}. A buyer
wants the dataset described below. Organisations in {country} describe themselves in
{language} on their own sites, so English searches miss them.

List 3 to 5 kinds of organisation in {country} likely to hold this data. No tool vendors: only
"organisation" or "regulated" sides.
{sides}
- rationale: why it would have the data, in English
- search_queries: 3–5 queries written in {language} as a native marketer in {country} would
  write them, in the words local organisations use on their own sites. Do not translate English
  queries word for word. Examples of the style, for call audio: German "Gesprächsaufzeichnung
  Callcenter Dienstleister Versicherung"; Arabic "تسجيل المكالمات مركز اتصال"; Japanese "通話録音
  コールセンター 導入事例"; Chinese "通话录音 客服中心 案例".

Then the local search hooks you know for {country}:
- site_filter: the country's domain filter, e.g. "site:{tld}"
- tender_portal: the national public tender portal (e.g. Contracts Finder in the UK, TED in
  the EU, Etimad in Saudi Arabia)
- retention_law: the local name of the law that makes organisations record and keep this
  data, or "" if none
- associations: local trade associations of these organisations
- tools: the two tools these organisations most use in {country} to record this data

Checklist of reasons an organisation holds such data:
{archetypes}

The buyer wants:
{brief}
"""


def local_plan(brief: Brief, market: Market) -> LocalHooks:
    """Native-language hypotheses and search hooks for one market. Vendor hypotheses are dropped."""
    prompt = LOCAL_PROMPT.format(country=market.country, language=market.language, tld=market.tld or ".com",
                                 sides=SIDES, archetypes=archetypes.guide(brief.chosen("modality")),
                                 brief=brief.summary("S"))
    hooks = llm.ask(prompt, LocalHooks, **llm.step("hypotheses"))
    hyps = [h.model_copy(update={"market": market.label}) for h in hooks.hypotheses if h.side != "vendor"]
    return hooks.model_copy(update={"hypotheses": _ordered(hyps)})


FILL_PROMPT = """You are a data-acquisition scout. A buyer wants the dataset described below.
A search for organisations holding it found no good match for these reasons an organisation
ends up with such data:
{missing}

For each reason, write one hypothesis: a kind of organisation that holds this data for that
reason.
- category: short name for the kind of organisation
{sides}
- rationale: why it would have the data
- search_queries: 3–5 web search queries for real organisations of this kind.

{queries}

Organisations already found, so look elsewhere:
{found}

The buyer wants:
{brief}
"""


def fill_archetypes(brief: Brief, missing: list[archetypes.Archetype], found: list[str]) -> list[HolderHypothesis]:
    """One hypothesis per archetype nothing qualifying was found for yet (one call)."""
    groups = ["any", *archetypes.groups(brief.chosen("modality"))]
    lines = [f"- {a.label}: {a.why}; e.g. {', '.join(e for g in groups for e in a.examples.get(g, ()))}"
             for a in missing]
    prompt = FILL_PROMPT.format(missing="\n".join(lines), sides=SIDES, queries=QUERY_RULES,
                                found="\n".join(f"- {f}" for f in found) or "(none yet)", brief=brief.summary("S"))
    return _ordered(llm.ask(prompt, ArchetypeHypotheses, **llm.step("hypotheses")).hypotheses)
