"""Data models that flow through the pipeline. Gemini outputs are forced into these shapes."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from worker.archetypes import ArchetypeKey
from worker.brief_template import (
    CATEGORIES,
    COMMERCIAL,
    INTERNAL_ONLY,
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


# seller: already holds this kind of data and sells or licenses it; collector: records or
# annotates it to a client's spec as a service; untapped: holds it because of what it does (see
# worker/archetypes.py) but doesn't sell it; public: a public body, research institute or
# repository that publishes it; technology: makes tools or hardware for it but doesn't keep it
# at scale
HolderType = Literal["seller", "collector", "untapped", "public", "technology", "unrelated"]
DataOwner = Literal["company", "customers", "unclear"]
# Untapped holders only. open_to_it: pages mention data partnerships, licensing or AI training data
SellingStatus = Literal["not_selling", "open_to_it", "unclear"]
Channel = Literal["product_search", "snowball", "directory", "marketplace", "public_source", "distressed",
                  "signals", "suppliers", "records"]
_OLD_HOLDER_TYPES = {"vendor": "seller", "by_product": "untapped"}


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
    selling_status: SellingStatus | None = Field(default=None, description="Untapped holders only")
    selling_evidence: list[Evidence] = Field(default=[], description="Quotes showing the selling status")

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
    collects_to_order: bool = False  # records or collects this kind of data to a client's spec
    archetype: ArchetypeKey | None = None  # untapped holders: why they hold the data
    selling_status: SellingStatus | None = None  # untapped holders
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
        """Sessions saved before vendors were split into sellers and collectors."""
        if isinstance(data, dict) and data.get("holder_type") in _OLD_HOLDER_TYPES:
            data = {**data, "holder_type": _OLD_HOLDER_TYPES[data["holder_type"]]}
        return data

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
