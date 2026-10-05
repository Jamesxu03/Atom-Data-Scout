"""Data models that flow through the pipeline. Gemini outputs are forced into these shapes."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

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


class HolderHypothesis(BaseModel):
    """A category of business likely to hold this data, e.g. as a by-product."""

    category: str
    rationale: str = Field(description="Why this kind of business would accumulate the data")
    search_queries: list[str] = Field(
        description="Product/service language the companies use about themselves, not dataset terms"
    )


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
    # Filled by the screen; reason is set once a candidate has been looked at
    reason: str | None = None
    holder_type: HolderType | None = None
    data_owner: DataOwner | None = None
    collector: bool = False  # ready source that records to order, with no existing data
    vendor: bool = False  # untapped: makes the tool or hardware; its customers record and hold the data
    saleability: Saleability | None = None  # untapped holders: the screen's initial answers
    languages: list[str] = Field(default=[], description="Buyer's languages it shows evidence of working in")
    regions: list[str] = Field(default=[], description="Buyer's regions it shows evidence of working in")
    scores: Scores = Scores()
    fit: float | None = None
    excluded: bool = False  # dead site, unrelated, or ruled out by a must-have filter
    contradicts: list[str] = Field(default=[], description="Must-have categories the screen saw contradicted")
    # Filled on demand, for the companies the user selects
    research: Research | None = None
    contacts: list[Contact] = []
    email: EmailDraft | None = None

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
        return data

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


class Funnel(BaseModel):
    found: int = 0  # raw hits across all channels
    unique: int = 0  # unique domains
    screened: int = 0  # scored by the screen
    relevant: int = 0  # fit at least RELEVANT_FIT
    researched: int = 0
    with_contacts: int = 0  # researched companies with at least one verified contact
