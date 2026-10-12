"""Data models that flow through the pipeline. Gemini outputs are forced into these shapes."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from worker.archetypes import BY_KEY as ARCHETYPE_KEYS
from worker.brief_template import (
    CATEGORIES,
    COMMERCIAL,
    INTERNAL_ONLY,
    OLD_CHECKS,
    OPEN,
    RESTRICTED,
    UNCLEAR,
)


class Evidence(BaseModel):
    quote: str = Field(description="Verbatim excerpt from the source text supporting the claim")
    url: str = Field(description="URL the quote was taken from")


class Fact(Evidence):
    """A value a page states, kept only with the words that state it."""

    value: str = Field(description="Short value, e.g. '$30 per audio hour', 'On request', '2014', '2018 to 2024'")


class Facts(BaseModel):
    """Price and age, when a page states them. The screen fills these on the candidate from the
    pages it reads; research fills them again with Google Search, and its values win."""

    price: Fact | None = Field(default=None, description="Published price of this data, or 'On request'")
    founded: Fact | None = Field(default=None, description="Year the company was founded")
    data_period: Fact | None = Field(default=None, description="When the data was recorded, e.g. '2018 to 2024'")


FACTS = tuple(Facts.model_fields)


# ready: the data is already offered (sold or licensed, listed on a marketplace, published as
# a dataset, or recorded to order by a collector); judged by its licence. untapped: holds the
# data but doesn't sell it; judged by saleability (Saleability below). Unrelated companies are
# excluded rather than typed.
HolderType = Literal["ready", "untapped"]
DataOwner = Literal["company", "customers", "unclear"]
Channel = Literal["product_search", "snowball", "directory", "marketplace", "public_source", "distressed",
                  "signals", "suppliers", "records"]
# Earlier holder types -> (type, collector flag); None: unrelated, now excluded
_OLD_HOLDER_TYPES = {"vendor": ("ready", False), "seller": ("ready", False), "public": ("ready", False),
                     "collector": ("ready", True), "by_product": ("untapped", False), "technology": ("untapped", False),
                     "unrelated": (None, False)}


class Answer(BaseModel):
    """One of the three saleability questions."""

    # Gemini skips optional lists, so the schema it sees requires the quotes (and the reason);
    # the defaults are for answers the screen builds, which have no quotes
    model_config = ConfigDict(json_schema_extra={"required": ["score", "reason", "evidence"]})

    score: int | None = Field(default=None, description="1 to 5; null if unknown")
    reason: str = Field(default="", description="One line: why this score")
    evidence: list[Evidence] = Field(default=[], description="Quotes that show it (research only)")
    verified: SkipJsonSchema[bool] = False  # set by us: research found a quote on the cited page


SALEABILITY_QUESTIONS = ("exists", "allowed", "willing")


class Saleability(BaseModel):
    """Can an untapped holder's data be bought? Three questions; the grade is the lowest score,
    because all three must hold: the data still exists, selling it is allowed, the holder is
    willing."""

    exists: Answer = Field(description="Is the data still there and kept: archives, retention")
    allowed: Answer = Field(description="May it be sold: ownership, terms, sensitivity")
    willing: Answer = Field(description="Would they sell: distress, data deals, or a big or regulated brand")
    obstacle: str | None = Field(default=None, description="The main thing in the way, in one line")
    path: str | None = Field(default=None, description="How it could still be bought, if there is a way")


# organisation: records and keeps its own data. regulated: a big brand or regulated institution
# (bank, insurer, airline, telco), searched through what would make it sell (insolvency, a past
# data deal, a law change). vendor: captures data for its customers, who hold it.
Side = Literal["organisation", "regulated", "vendor"]


class HolderHypothesis(BaseModel):
    """A category of business likely to hold this data, e.g. as a by-product."""

    # Gemini skips fields with defaults; the defaults are for hypotheses saved before they existed
    model_config = ConfigDict(json_schema_extra={
        "required": ["category", "rationale", "search_queries", "side", "owner", "licence_route"]})

    category: str
    rationale: str = Field(description="Why this kind of business would accumulate the data")
    search_queries: list[str] = Field(
        description="Product/service language the companies use about themselves, not dataset terms"
    )
    side: Side = Field(default="organisation", description="organisation, regulated or vendor")
    owner: str = Field(default="", description="Who owns the data: the organisation itself, or its customers")
    licence_route: str = Field(default="", description="The likely route to a licence, one line")
    market: SkipJsonSchema[str | None] = None  # set by us: the local market these queries are written for


class Market(BaseModel):
    """A country and language to search natively (WS2)."""

    country: str
    language: str  # the language local companies write in, e.g. "Arabic"
    tld: str = ""  # the country's domain, e.g. ".sa"
    brief_language: str = ""  # the brief's language it serves, e.g. "Gulf Arabic"

    @property
    def label(self) -> str:
        return f"{self.country} ({self.language})"


class LocalHooks(BaseModel):
    """What to search in one market, in its own language (Gemini output)."""

    hypotheses: list[HolderHypothesis] = Field(description="Organisation holders, queries in the local language")
    site_filter: str = Field(description="The country's domain filter, e.g. 'site:.de'")
    tender_portal: str = Field(description="The national public tender portal, e.g. 'TED', 'Etimad'")
    retention_law: str = Field(description="The local name of the law that makes companies keep this data, or ''")
    associations: list[str] = Field(description="Local trade associations of these organisations")
    tools: list[str] = Field(description="The two tools these organisations most use to record this data there")


TriggerKind = Literal["distress", "precedent", "law"]


def year_of(text: str) -> int | None:
    """The first year in a date like '2025', '2025-03' or 'March 2025'."""
    m = re.search(r"\b(19|20)\d{2}\b", text)
    return int(m.group()) if m else None


class Trigger(BaseModel):
    """Why a big brand or regulated institution might sell after all (WS6), kept only with a
    verified, dated quote."""

    model_config = ConfigDict(json_schema_extra={"required": ["kind", "date", "ongoing", "summary", "evidence"]})

    kind: TriggerKind = Field(description="distress: insolvency, administration, a unit closed or for sale; "
                                          "precedent: it licensed, sold or shared this data before; "
                                          "law: a law or regulator decision allows or requires reuse")
    date: str = Field(description="When it happened: the year, or year and month, e.g. '2025' or '2025-03'")
    ongoing: bool = Field(default=False, description="Still going on, e.g. administration not yet finished")
    summary: str = Field(description="One line: what happened")
    evidence: list[Evidence] = Field(default=[], description="Quotes stating it, ideally including the year")

    @property
    def year(self) -> int | None:
        return year_of(self.date)


class LawSignal(BaseModel):
    """A recent law or regulator decision in a market on sharing or reusing this kind of data."""

    title: str = Field(description="The law or decision, e.g. 'Data (Use and Access) Act 2025'")
    date: str = Field(description="When it was passed or took effect, e.g. '2025'")
    summary: str = Field(description="One line: what it allows or requires")
    sectors: list[str] = Field(description="The sectors it covers, e.g. banks, telcos")
    evidence: list[Evidence] = Field(description="Quotes from the official text or reputable news")


class MarketPlan(BaseModel):
    """One market's local search plan and law signals, saved with the session."""

    market: Market
    hooks: LocalHooks | None = None
    laws: list[LawSignal] = []


class Found(BaseModel):
    """One company as reported by a discovery search (LLM output)."""

    name: str
    website: str = Field(description="The company's own homepage URL, not the page it was found on")
    source_url: str = Field(description="URL of the page where this company was found")


class FoundList(BaseModel):
    companies: list[Found]


class Contact(BaseModel):
    """A way to reach someone at the company. Business details that are published publicly only."""

    name: str | None = Field(default=None, description="Person's name; null for a generic inbox")
    role: str | None = Field(default=None, description="e.g. CEO, Head of Data, DPO, Administrator")
    email: str | None = None
    phone: str | None = None
    linkedin_url: str | None = None
    source_url: str = Field(description="Page where these details are published")
    origin: Literal["website", "search"] = "search"
    verified: bool = Field(
        default=False, description="Email/phone (or name) found verbatim on source_url when we fetched it"
    )


class Scores(BaseModel):
    """1 to 5 each; None means no evidence yet."""

    data_match: int | None = None
    coverage: int | None = None  # languages and regions, computed in Python
    volume: int | None = None
    licence: int | None = None  # computed in Python


# What research found, in the same labels as D1 "Acceptable licence", plus internal use only
LicenceFound = Literal.__getitem__(CATEGORIES["licence"].choices + (INTERNAL_ONLY,))
# The categories research checks, besides licence and volume which have their own fields
CheckedCategory = Literal.__getitem__(tuple(
    k for k, c in CATEGORIES.items() if "V" in c.used_for and k not in ("licence", "volume")))


class Check(BaseModel):
    """One requirement from the brief, checked against public pages."""

    category: CheckedCategory
    verdict: Literal["meets", "fails", "unclear"]
    finding: str = Field(description="One line: what the pages say")
    evidence: list[Evidence]

    @model_validator(mode="before")
    @classmethod
    def _upgrade(cls, data):
        """Checks of categories the older template had are now the deal terms check."""
        if isinstance(data, dict) and data.get("category") in OLD_CHECKS:
            data = {**data, "category": "deal_terms"}
        return data


# Research saved before the D1 labels existed
_OLD_STATUS = {"sellable": COMMERCIAL, "open": OPEN, "open_restricted": RESTRICTED,
               "needs_consent": UNCLEAR, "internal_only": INTERNAL_ONLY, "unknown": UNCLEAR}


class Research(Facts):
    """What checking one company in depth found. Every claim carries a verified quote."""

    data_held: str = Field(description="What data they hold: kind, formats, labels")
    licence: LicenceFound
    licence_evidence: list[Evidence]
    volume_estimate: str | None = Field(description="e.g. '40,000 hours of call recordings'")
    volume_score: int | None = Field(description="1 to 5 against the buyer's volume; null if unknown")
    volume_evidence: list[Evidence]
    checks: list[Check] = Field(default=[], description="One per requirement listed under Checks")
    saleability: Saleability | None = Field(default=None, description="Untapped holders only")
    triggers: list[Trigger] = Field(default=[], description="Untapped holders only: dated events that make a sale "
                                                            "likely (distress, precedent, law)")

    @model_validator(mode="before")
    @classmethod
    def _upgrade(cls, data):
        if isinstance(data, dict) and "licence_status" in data and "licence" not in data:
            data = {**data, "licence": _OLD_STATUS.get(data.pop("licence_status"), UNCLEAR)}
        return data


class EmailDraft(BaseModel):
    to: str | None = None
    subject: str
    body: str


Verdict = Literal["confirmed", "possible", "unconfirmed", "rejected"]
SignalKind = Literal["licensing", "dataset", "reuse_terms", "distress", "own_data"]
BlockerKind = Literal["never_share", "regulator_only"]


class MustCheck(BaseModel):
    """One must-have checked against the company's pages."""

    category: str = Field(description="The must-have's key, exactly as listed")
    verdict: Literal["meets", "fails", "unclear"]
    finding: str = Field(description="One line: what the pages say")
    evidence: list[Evidence]


class SellingSignal(BaseModel):
    kind: SignalKind = Field(description="licensing: a data licensing or AI training data offer or partnership; "
                                         "dataset: released a dataset or named as a data provider; reuse_terms: "
                                         "terms let it use the data to train models or share it with research "
                                         "partners; distress: closed, insolvent, product retired or assets for "
                                         "sale; own_data: the data comes from its own staff or devices")
    summary: str = Field(description="One line")
    evidence: list[Evidence]


class Blocker(BaseModel):
    kind: BlockerKind = Field(description="never_share: a statement that the recordings or content themselves are "
                                          "never shared or sold; regulator_only: kept only because a regulator "
                                          "requires it, with no clause allowing other uses")
    summary: str = Field(description="One line")
    evidence: list[Evidence]


class ConfirmFound(BaseModel):
    """What the confirmation call reports for one company (Gemini output)."""

    model_config = ConfigDict(json_schema_extra={"required": [
        "data_held", "data_evidence", "checks", "data_owner", "owner_evidence", "licence", "licence_evidence",
        "signals", "blockers"]})

    data_held: str = Field(description="What data it holds that matches the buyer's, or 'none'")
    data_evidence: list[Evidence] = Field(description="Quotes showing it holds this kind of data")
    checks: list[MustCheck] = Field(description="One per must-have listed")
    data_owner: DataOwner
    owner_evidence: list[Evidence] = Field(description="Quotes showing who owns the data")
    licence: LicenceFound | None = Field(default=None, description="Ready sources only: how the data is licensed")
    licence_evidence: list[Evidence] = Field(default=[], description="Ready sources only")
    signals: list[SellingSignal] = Field(default=[], description="Untapped holders only")
    blockers: list[Blocker] = Field(default=[], description="Untapped holders only")


class Confirmation(BaseModel):
    """Is this company a confirmed fit that can plausibly sell? Every claim below has a verified
    quote; the verdict is worked out in Python (worker/steps/confirm.py)."""

    verdict: Verdict
    reason: str
    data_held: str = ""
    data_evidence: list[Evidence] = []
    checks: list[MustCheck] = []
    data_owner: DataOwner = "unclear"
    owner_evidence: list[Evidence] = []
    licence: LicenceFound | None = None
    licence_evidence: list[Evidence] = []
    signals: list[SellingSignal] = []
    blockers: list[Blocker] = []


# Keys of the older template merged into today's
_MERGED_KEYS = {"delivery": "collection", "overlap": "speakers", "timestamps": "transcript",
                "speaker_labels": "transcript"}


class Candidate(Facts):
    """A company found by discovery, merged across channels by domain. Holds everything known
    about it: screen, scores, facts, research, contacts and email."""

    name: str
    website: str
    domain: str
    channels: list[Channel]
    source_urls: list[str] = Field(description="Where this company was found")
    hypotheses: list[str] = Field(default=[], description="HolderHypothesis categories that led here")
    source_verified: bool = Field(
        default=False, description="At least one source page was confirmed to list this company"
    )
    first_round: int | None = None  # the discovery round that first found it
    markets: list[str] = []  # local markets whose searches found it, e.g. "Germany (German)"
    tools: list[str] = []  # tools whose customer stories name it: "customer of Verint"
    # Filled by the screen; reason is set once a candidate has been looked at
    reason: str | None = None
    holder_type: HolderType | None = None
    data_owner: DataOwner | None = None
    collector: bool = False  # ready source that records to order, with no existing data
    vendor: bool = False  # untapped: makes the tool or hardware; its customers record and hold the data
    archetype: str | None = None  # untapped: why it holds the data (archetypes.BY_KEY)
    # untapped: a big brand or regulated institution, in scope only with a verified trigger
    needs_trigger: bool = False
    triggers: list[Trigger] = []  # verified and dated
    trigger_checked: bool = False
    saleability: Saleability | None = None  # untapped holders: the screen's initial answers
    languages: list[str] = Field(default=[], description="Buyer's languages it shows evidence of working in")
    regions: list[str] = Field(default=[], description="Buyer's regions it shows evidence of working in")
    scores: Scores = Scores()
    fit: float | None = None
    excluded: bool = False  # dead site, unrelated, or ruled out by a must-have filter
    contradicts: list[str] = Field(default=[], description="Must-have categories the screen saw contradicted")
    confirmation: Confirmation | None = None  # the best of each list, checked at the end of the search
    # Filled on demand, for the companies the user selects
    research: Research | None = None
    contacts: list[Contact] = []
    email: EmailDraft | None = None
    killed: bool = False  # ruled out by the user after research: hidden and ranked last
    kill_reason: str = ""

    @model_validator(mode="before")
    @classmethod
    def _upgrade(cls, data):
        """Sessions saved with earlier holder types or template keys."""
        if isinstance(data, dict) and data.get("holder_type") in _OLD_HOLDER_TYPES:
            holder, collector = _OLD_HOLDER_TYPES[data["holder_type"]]
            data = {**data, "holder_type": holder, "collector": collector or data.get("collector", False)}
            if holder is None:
                data["excluded"] = True
        if isinstance(data, dict) and data.get("contradicts"):  # keys of the older template
            renamed = [_MERGED_KEYS.get(k, k) for k in data["contradicts"]]
            data = {**data, "contradicts": [k for k in dict.fromkeys(renamed) if k in CATEGORIES]}
        if isinstance(data, dict) and data.get("archetype") not in ARCHETYPE_KEYS:  # unknown or older labels
            data = {**data, "archetype": None}
        return data

    def selling_signals(self) -> int:
        """Verified signs it would sell: the confirmation's selling signals and the triggers.
        Breaks ties after fit, where corroboration used to: being found by many channels mostly
        means being famous, and famous companies are the least likely to sell."""
        return len(self.confirmation.signals if self.confirmation else []) + len(self.triggers)

    def current_saleability(self) -> Saleability | None:
        """Research's answers where it verified them, otherwise the screen's."""
        screen, checked = self.saleability, self.research and self.research.saleability
        if not checked:
            return screen
        pick = {q: a if a.verified or not screen else getattr(screen, q)
                for q in SALEABILITY_QUESTIONS if (a := getattr(checked, q))}
        return checked.model_copy(update={**pick, "obstacle": checked.obstacle or (screen and screen.obstacle),
                                          "path": checked.path or (screen and screen.path)})

    @property
    def screened(self) -> bool:
        return self.reason is not None

    def fact(self, key: str) -> Fact | None:
        """Research's value when it found one, otherwise the screen's."""
        return (self.research and getattr(self.research, key)) or getattr(self, key)

    def corroboration(self) -> tuple[int, int, bool, int]:
        """How well-supported this candidate is; only a tiebreaker after fit."""
        return (len(self.channels), len(self.hypotheses), self.source_verified, len(self.source_urls))


class RoundStats(BaseModel):
    """What one discovery round cost and found, so the session shows whether later rounds paid
    for themselves."""

    round: int
    calls: int = 0  # grounded calls
    hits: int = 0
    new: int = 0  # companies first found in this round
    qualifying: int = 0  # new, a fit and no must-have failure (discovery/rounds.py)
    calls_by_slot: dict[str, int] = {}  # "channel" or "channel|market" -> calls
    yield_by_slot: dict[str, int] = {}  # slot -> qualifying new companies it found
    queries: int = 0  # Google searches Gemini ran, when it reports them


class Funnel(BaseModel):
    found: int = 0  # raw hits across all channels
    unique: int = 0  # unique domains
    screened: int = 0  # scored by the screen
    relevant: int = 0  # fit at least RELEVANT_FIT
    relevant_ready: int = 0
    relevant_untapped: int = 0
    confirmed: int = 0
    possible: int = 0
    researched: int = 0
    with_contacts: int = 0  # researched companies with at least one verified contact
    rounds: list[RoundStats] = []
