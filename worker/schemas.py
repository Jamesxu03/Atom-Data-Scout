"""Data models that flow through the pipeline. Gemini outputs are forced into these shapes."""

from typing import Literal

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    quote: str = Field(description="Verbatim excerpt from the source text supporting the claim")
    url: str = Field(description="URL the quote was taken from")


# vendor: sells data it owns or collects it to order; public: a public body, research institute
# or repository that publishes it (corpora, parliament recordings); by_product: its product or
# service captures it in normal operation; technology: makes tools or hardware for it but
# doesn't keep it at scale
SourceType = Literal["vendor", "public", "by_product", "technology"]
HolderType = Literal[SourceType, "unrelated"]
DataOwner = Literal["company", "customers", "unclear"]
Channel = Literal["product_search", "snowball", "directory", "marketplace", "public_source"]
Licence = Literal["training_only", "commercial", "exclusive", "unknown"]

SOURCE_LABELS = {
    "vendor": "data vendors",
    "public": "public sources",
    "by_product": "by-product holders",
    "technology": "technology companies",
}
LICENCE_LABELS = {
    "training_only": "model training only",
    "commercial": "commercial use",
    "exclusive": "exclusive",
    "unknown": "not stated",
}


class Volume(BaseModel):
    amount: float | None = Field(description="e.g. 2000000; null if not stated")
    unit: str | None = Field(description='e.g. "hours", "utterances", "data points", "GB"')

    def __str__(self) -> str:
        if self.amount is None:
            return ""
        amount = f"{self.amount:,.0f}" if self.amount == int(self.amount) else f"{self.amount:,}"
        return f"{amount} {self.unit or ''}".strip()


class Brief(BaseModel):
    """What data the buyer wants: the standard template every search starts from."""

    data_type: str = Field(description="e.g. multi-speaker meeting audio")
    features: list[str] = Field(description="e.g. multi-speaker, overlapping speech, far-field")
    scenarios: list[str] = Field(description="e.g. remote meetings, noisy offices")
    languages: list[str] = Field(description="Languages or dialects, e.g. Gulf Arabic")
    regions: list[str] = Field(description="Countries or regions, e.g. Middle East")
    label_types: list[str] = Field(description="e.g. transcript, speaker diarization")
    volume: Volume
    formats: list[str] = Field(description="e.g. 16 kHz WAV")
    licence: Licence
    holder_types: list[SourceType] = Field(description="Kinds of source the buyer accepts")
    exclusions: list[str] = Field(description="Company domains to skip, e.g. otter.ai")
    must_have: list[str] = Field(description="Names of the fields above that are hard requirements")
    notes: str = ""

    def summary(self) -> str:
        """One readable paragraph, shown on the results page and used in prompts."""
        parts = [self.data_type[:1].upper() + self.data_type[1:]]
        if self.features:
            parts[0] += f" ({', '.join(self.features)})"
        if self.languages:
            parts.append(f"Languages: {', '.join(self.languages)}")
        if self.regions:
            parts.append(f"Regions: {', '.join(self.regions)}")
        if self.scenarios:
            parts.append(f"Scenarios: {', '.join(self.scenarios)}")
        if self.label_types:
            parts.append(f"Labels: {', '.join(self.label_types)}")
        if str(self.volume):
            parts.append(f"Volume: at least {self.volume}")
        if self.formats:
            parts.append(f"Formats: {', '.join(self.formats)}")
        parts.append(f"Licence: {LICENCE_LABELS[self.licence]}")
        if self.holder_types and set(self.holder_types) != set(SOURCE_LABELS):
            parts.append(f"Sources accepted: {', '.join(SOURCE_LABELS[h] for h in self.holder_types)}")
        if self.must_have:
            parts.append(f"Must have: {', '.join(f.replace('_', ' ') for f in self.must_have)}")
        if self.exclusions:
            parts.append(f"Skip: {', '.join(self.exclusions)}")
        if self.notes.strip():
            parts.append(f"Notes: {self.notes.strip()}")
        return ". ".join(parts) + "."


# Fields a buyer can mark as hard requirements
MUST_HAVE_FIELDS = [
    "data_type", "features", "scenarios", "languages", "regions", "label_types", "volume",
    "formats", "licence",
]


class FieldOptions(BaseModel):
    field: str = Field(description="A Brief field name, e.g. languages")
    options: list[str] = Field(description="2 to 5 alternative values the buyer might want")


class BriefDraft(BaseModel):
    """Output of the refine step: a filled-in brief plus what to check with the buyer."""

    brief: Brief
    suggested: list[str] = Field(description="Fields you filled in that the text did not state")
    options: list[FieldOptions] = Field(description="Alternatives to show in the form")
    questions: list[str] = Field(description="Open questions to check with the buyer")


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


LicenceStatus = Literal[
    "sellable", "open", "open_restricted", "needs_consent", "internal_only", "unknown"
]


class Research(BaseModel):
    """What checking one company in depth found. Every claim carries a verified quote."""

    data_held: str = Field(description="What data they hold: kind, formats, labels")
    licence_status: LicenceStatus
    licence_evidence: list[Evidence]
    volume_estimate: str | None = Field(description="e.g. '40,000 hours of call recordings'")
    volume_score: int | None = Field(description="1 to 5 against the buyer's volume; null if unknown")
    volume_evidence: list[Evidence]


class EmailDraft(BaseModel):
    to: str | None = None
    subject: str
    body: str


class Candidate(BaseModel):
    """A company found by discovery, merged across channels by domain. Holds everything known
    about it: screen, scores, research, contacts and email."""

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
    languages: list[str] = Field(default=[], description="Buyer's languages it shows evidence of working in")
    regions: list[str] = Field(default=[], description="Buyer's regions it shows evidence of working in")
    scores: Scores = Scores()
    fit: float | None = None
    excluded: bool = False  # dead site or unrelated
    # Filled on demand, for the companies the user selects
    research: Research | None = None
    contacts: list[Contact] = []
    email: EmailDraft | None = None

    @property
    def screened(self) -> bool:
        return self.reason is not None

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
