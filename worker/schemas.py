"""Data models that flow through the pipeline. Gemini outputs are forced into these shapes."""

from typing import Literal

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    quote: str = Field(description="Verbatim excerpt from the source text supporting the claim")
    url: str = Field(description="URL the quote was taken from")


class DataSpec(BaseModel):
    """What data the buyer wants."""

    data_type: str = Field(description="e.g. multi-speaker meeting audio")
    languages: list[str]
    scenarios: list[str] = Field(description="e.g. remote meetings, noisy offices")
    needs_labels: bool
    label_types: list[str] = Field(description="e.g. transcript, speaker diarization")
    min_hours: int | None = Field(description="Minimum volume in hours, if stated")
    licence_needed: Literal["training_only", "commercial", "exclusive", "unknown"]


class HolderHypothesis(BaseModel):
    """A category of business likely to hold this data, e.g. as a by-product."""

    category: str
    rationale: str = Field(description="Why this kind of business would accumulate the data")
    search_queries: list[str] = Field(
        description="Product/service language the companies use about themselves, not dataset terms"
    )


Channel = Literal["product_search", "snowball", "directory", "marketplace"]
Relevance = Literal["yes", "maybe", "no"]


class Found(BaseModel):
    """One company as reported by a discovery search (LLM output)."""

    name: str
    website: str = Field(description="The company's own homepage URL, not the page it was found on")
    source_url: str = Field(description="URL of the page where this company was found")


class FoundList(BaseModel):
    companies: list[Found]


class Candidate(BaseModel):
    """A company found by discovery, merged across channels by domain."""

    name: str
    website: str
    domain: str
    channels: list[Channel]
    source_urls: list[str] = Field(description="Where this company was found")
    hypotheses: list[str] = Field(default=[], description="HolderHypothesis categories that led here")
    # Filled by the relevance screen
    relevance: Relevance | None = None
    relevance_reason: str | None = None


class Funnel(BaseModel):
    found: int = 0  # raw hits across all channels
    deduped: int = 0  # unique domains
    relevant: int = 0  # screened yes or maybe
    profiled: int = 0  # deep-crawled


class CompanyProfile(BaseModel):
    """Deep-dive on one candidate. Every judgement carries evidence."""

    name: str
    website: str
    records_data: bool | None = Field(description="Does the company record/collect the data?")
    records_data_evidence: list[Evidence]
    retention: str | None = Field(description="How long data is kept, e.g. '12 months'")
    retention_evidence: list[Evidence]
    policy_allows_transfer: bool | None = Field(
        description="Does the privacy policy allow transfer to third parties / on sale of business?"
    )
    policy_allows_transfer_evidence: list[Evidence]
    policy_allows_training: bool | None = Field(
        description="Does the policy permit use for AI/model training?"
    )
    policy_allows_training_evidence: list[Evidence]
    # Filled from Companies House, not by Gemini
    company_number: str | None = None
    company_status: str | None = Field(
        default=None, description="active / administration / liquidation / dissolved ..."
    )
    incorporated_on: str | None = None
    registered_address: str | None = None


class Score(BaseModel):
    """1–5 per dimension from Gemini; `total` is computed in Python (see config.SCORE_WEIGHTS)."""

    data_match: int = Field(ge=1, le=5)
    estimated_volume: int = Field(ge=1, le=5)
    rights_clarity: int = Field(ge=1, le=5)
    seller_motivation: int = Field(ge=1, le=5)
    contactability: int = Field(ge=1, le=5)
    reasons: dict[str, str] = Field(description="Dimension name -> one-sentence justification")
    total: float | None = None


class Result(BaseModel):
    """One row of pipeline output."""

    profile: CompanyProfile
    score: Score
    email_draft: str
