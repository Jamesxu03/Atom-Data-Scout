"""The brief template, defined once as data: sections, categories, choices, single or multi
pick, the modality that shows an add-on, and where the tool uses each category. The form, the
Gemini schema, the prompts, the scoring and the email step all read from here, so a choice
exists in one place only.

Where the tool uses a category ("used for" codes):
  S  search queries        F  screening filter      R  ranking score
  V  checked during deep research                   E  outreach email

Every category also has an importance: must (hard cap when evidence contradicts it), nice
(weighted into the score) or any (ignored). Unknown evidence stays neutral.
"""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, create_model

PickType = Literal["single", "multi", "amount", "years", "text"]
Importance = Literal["must", "nice", "any"]
IMPORTANCE_LABELS = {"must": "Must have", "nice": "Nice to have", "any": "Any"}
CODE_LABELS = {"S": "search", "F": "filter", "R": "ranking", "V": "research", "E": "email"}


@dataclass(frozen=True)
class Category:
    key: str
    code: str  # e.g. "A1"
    label: str
    pick: PickType
    used_for: str  # e.g. "SFR"
    choices: tuple[str, ...] = ()
    free_text: bool = False  # the user can type values of their own
    neutral: tuple[str, ...] = ()  # choices that mean "no preference"
    help: str = ""


@dataclass(frozen=True)
class Section:
    code: str
    title: str
    categories: tuple[Category, ...]
    modalities: tuple[str, ...] = ()  # add-on: shown only when one of these A1 choices is picked


# Choices other code refers to by name
AUDIO, VIDEO, IMAGE, TEXT = "Audio/speech", "Video", "Image", "Text"
TABULAR, SENSOR, GEO = "Tabular records", "Time series/sensor", "Geospatial"
VENDORS = "Data vendors and collection companies"
MARKETPLACES = "Marketplace listings"
PUBLIC_BODIES = "Open repositories and public bodies"
RESEARCH_INSTITUTES = "Research institutes and universities"
BY_PRODUCT = "By-product holders (their service generates the data)"
DISTRESSED = "Distressed or insolvent companies"
TECHNOLOGY = "Technology providers with internal data"
OPEN, OPEN_CONDITIONS, RESTRICTED = "Public domain / Open", "Open with conditions", "Restricted / Conditional"
COMMERCIAL, EXCLUSIVE, UNCLEAR = "Commercial, non-exclusive", "Commercial, exclusive", "Unclear"
INTERNAL_ONLY = "Internal use only"  # found by research only; never acceptable
CUSTOMER_EXCLUDE, CUSTOMER_CONSENT, CUSTOMER_LATER = (
    "Exclude", "Accept if customer consent can be obtained", "Accept, check later")

SECTIONS: tuple[Section, ...] = (
    Section("A", "The data", (
        Category("modality", "A1", "Modality", "multi", "SFR",
                 (AUDIO, VIDEO, IMAGE, TEXT, TABULAR, SENSOR, GEO, "Multimodal")),
        Category("domain", "A2", "Domain", "multi", "SFR",
                 ("Meetings/conversation", "Call centre/support", "Broadcast/media", "Healthcare",
                  "Legal/public sector", "Retail/e-commerce", "Automotive/mobility", "Industrial/IoT",
                  "Environment/waste", "Finance"), free_text=True, help="Type your own if none fits"),
        Category("production", "A3", "How it was produced", "multi", "SF",
                 ("Natural (captured in normal operations)", "Prompted/scripted collection", "Synthetic", "Any"),
                 neutral=("Any",)),
        Category("annotation", "A4", "Minimum annotation", "single", "FR",
                 ("Raw, no labels", "Basic labels", "Rich labels", "Expert-verified labels")),
        Category("quality", "A5", "Quality evidence", "single", "VE",
                 ("None needed", "Seller's QA description", "Measured metrics (e.g. WER, inter-annotator agreement)",
                  "Sample we test ourselves"), neutral=("None needed",)),
    )),
    Section("B", "Where and who", (
        Category("region", "B1", "Region of origin", "multi", "SFR",
                 ("Global", "UK", "EU/EEA", "North America", "Latin America", "Middle East (GCC)",
                  "Middle East and North Africa", "Sub-Saharan Africa", "South Asia", "East Asia",
                  "Southeast Asia", "Oceania"), free_text=True, neutral=("Global",),
                 help="Type specific countries if you need them"),
        Category("languages", "B2", "Languages and dialects", "text", "SFR", free_text=True,
                 help="Suggestions follow the regions picked; type your own, e.g. Gulf Arabic"),
        Category("code_switching", "B3", "Code-switching", "single", "F", ("Not allowed", "Acceptable", "Wanted")),
        Category("demographics", "B4", "Demographic coverage", "single", "VE",
                 ("Not needed", "Balanced (gender, age bands)", "Set quotas", "Metadata per sample"),
                 neutral=("Not needed",)),
    )),
    Section("C", "Volume and delivery", (
        Category("volume", "C1", "Volume target", "amount", "RVE",
                 ("hours", "recordings", "records/rows", "images", "video hours", "documents", "tokens", "GB",
                  "data points")),
        Category("seller_share", "C2", "Minimum from one seller", "single", "R",
                 ("100% of target", "50%", "10%", "Any amount (we combine sellers)"),
                 help="A seller holding 10% of the target is still useful if you combine several sellers"),
        Category("recency", "C3", "Recency", "single", "FV",
                 ("Any", "Last 5 years", "Last 2 years", "Last 12 months", "Live/ongoing"), neutral=("Any",)),
        Category("delivery", "C4", "Delivery type", "multi", "SE",
                 ("One-off dataset", "Recurring feed", "API access", "Custom collection to order")),
    )),
    Section("D", "Rights and compliance", (
        Category("licence", "D1", "Acceptable licence", "multi", "FRV",
                 (OPEN, OPEN_CONDITIONS, RESTRICTED, COMMERCIAL, EXCLUSIVE, UNCLEAR),
                 help="Public domain / Open: CC0, public domain, open government data. Open with conditions: "
                      "CC BY, CC BY-SA, ODbL. Restricted: non-commercial, research only, no derivatives. "
                      "Unclear: keep and verify later."),
        Category("permitted_use", "D2", "Permitted use needed", "multi", "VE",
                 ("Evaluation only", "Internal model training", "Fine-tuning", "Training for a commercial product",
                  "Retrieval/RAG", "Resale or redistribution")),
        Category("exclusivity", "D3", "Exclusivity", "single", "E",
                 ("Not needed", "Preferred", "Field-of-use exclusive required", "Fully exclusive required")),
        Category("term", "D4", "Term", "single", "E",
                 ("Any", "Perpetual", "Fixed term, trained models may be kept",
                  "Fixed term, data and models deleted after")),
        Category("personal_data", "D5", "Personal data", "single", "FV",
                 ("None allowed", "Anonymised only", "Pseudonymised acceptable",
                  "Identifiable with documented consent"),
                 help="Voice or face data used to identify people is biometric: only "
                      "'Identifiable with documented consent' fits"),
        Category("customer_data", "D6", "Data the seller holds for its customers", "single", "FR",
                 (CUSTOMER_EXCLUDE, CUSTOMER_CONSENT, CUSTOMER_LATER),
                 help="e.g. a company recording its clients' customer-support calls"),
        Category("documents", "D7", "Documents required", "multi", "VE",
                 ("Licence agreement", "Per-person consent records", "Data processing agreement",
                  "Datasheet/metadata", "Source details for EU AI Act summary", "Warranties and indemnity")),
        Category("processing", "D8", "Where the buyer will process it", "single", "V",
                 ("UK", "EU/EEA", "US", "Middle East", "Asia", "Other"),
                 help="Flags origin regions that restrict transfers, e.g. China, Saudi Arabia"),
    )),
    Section("E", "Where to look", (
        Category("sources", "E1", "Source types", "multi", "SF",
                 (VENDORS, MARKETPLACES, PUBLIC_BODIES, RESEARCH_INSTITUTES, BY_PRODUCT, DISTRESSED, TECHNOLOGY),
                 help="Empty searches everywhere. Must have also drops companies of other types."),
        Category("exclusions", "E2", "Exclusions", "text", "F", free_text=True,
                 help="Companies or domains to skip (already contacted, the buyer's competitors)"),
    )),
    Section("F", "Commercial", (
        Category("budget", "F1", "Budget", "single", "E",
                 ("Unknown", "Under £10k", "£10k to £50k", "£50k to £250k", "Over £250k"), neutral=("Unknown",)),
        Category("pricing", "F2", "Pricing model", "multi", "E",
                 ("Any", "Flat fee", "Per unit", "Recurring", "Revenue share"), neutral=("Any",)),
        Category("timeline", "F3", "Timeline", "single", "E",
                 ("Under 1 month", "1 to 3 months", "3 to 6 months", "Flexible"), neutral=("Flexible",)),
        Category("sample", "F4", "Sample before purchase", "single", "E", ("Required", "Preferred", "Not needed"),
                 neutral=("Not needed",)),
    )),
    Section("AUDIO", "Audio/speech details", (
        Category("speakers", "AU1", "Speakers per recording", "multi", "FR", ("1", "2", "3 to 5", "6+")),
        Category("overlap", "AU2", "Overlapping speech", "single", "FR", ("None", "Some", "Required")),
        Category("speech_style", "AU3", "Speech style", "multi", "SFR",
                 ("Spontaneous conversation", "Call centre", "Read/scripted", "Broadcast")),
        Category("audio_quality", "AU4", "Audio quality", "multi", "FR",
                 ("Telephone (8 kHz)", "Wideband (16 kHz+)", "Studio (44.1/48 kHz)", "Any"), neutral=("Any",)),
        Category("channels", "AU5", "Channels", "multi", "FR",
                 ("Mono", "One channel per speaker", "Multi-mic/far-field")),
        Category("environment", "AU6", "Environment", "multi", "FR",
                 ("Quiet", "Office/meeting room", "Noisy/public", "In-car", "Mixed")),
        Category("transcript", "AU7", "Transcript", "single", "FR", ("None", "Clean", "Verbatim")),
        Category("timestamps", "AU8", "Timestamps", "single", "FR", ("None", "Utterance", "Word")),
        Category("speaker_labels", "AU9", "Speaker labels", "single", "FR", ("Not needed", "Needed"),
                 neutral=("Not needed",)),
    ), modalities=(AUDIO,)),
    Section("VISUAL", "Video/image details", (
        Category("viewpoint", "VI1", "Viewpoint", "multi", "SFR",
                 ("First-person", "Handheld third-person", "Fixed camera", "Dashcam", "Aerial")),
        Category("resolution", "VI2", "Minimum resolution", "single", "FR", ("Any", "720p", "1080p", "4K"),
                 neutral=("Any",)),
        Category("visual_labels", "VI3", "Labels", "multi", "FR",
                 ("Captions", "Tags", "Bounding boxes", "Segmentation", "Pose", "Actions/events")),
        Category("people_in_frame", "VI4", "People in frame", "single", "FV",
                 ("None", "Blurred", "Identifiable with consent")),
    ), modalities=(VIDEO, IMAGE)),
    Section("TEXT", "Text details", (
        Category("genre", "TX1", "Genre", "multi", "SFR",
                 ("Chat/dialogue", "Documents/reports", "Web content", "Transcripts", "Code")),
        Category("text_format", "TX2", "Format", "multi", "FR",
                 ("Raw", "Cleaned", "Instruction/response pairs", "Q&A")),
    ), modalities=(TEXT,)),
    Section("RECORDS", "Tabular, sensor or geospatial details", (
        Category("entity_level", "TB1", "Entity level", "multi", "SFR",
                 ("Individual", "Household", "Facility/site", "Company", "City/region", "Country")),
        Category("granularity", "TB2", "Granularity", "multi", "FR", ("Event", "Hourly", "Daily", "Monthly", "Annual")),
        Category("coverage_period", "TB3", "Coverage period", "years", "FR", help="From and to years"),
        Category("geocoding", "TB4", "Geo-coding", "single", "FR", ("Not needed", "Region", "Precise coordinates"),
                 neutral=("Not needed",)),
    ), modalities=(TABULAR, SENSOR, GEO)),
)

CATEGORIES: dict[str, Category] = {c.key: c for s in SECTIONS for c in s.categories}
SECTION_OF: dict[str, Section] = {c.key: s for s in SECTIONS for c in s.categories}

# E1 choice -> the holder types (from the screen) it covers
SOURCE_HOLDERS = {
    VENDORS: {"vendor"}, MARKETPLACES: {"vendor"}, PUBLIC_BODIES: {"public"}, RESEARCH_INSTITUTES: {"public"},
    BY_PRODUCT: {"by_product"}, DISTRESSED: {"by_product", "vendor"}, TECHNOLOGY: {"technology"},
}

# B1 region -> languages suggested for B2
REGION_LANGUAGES = {
    "Global": ["English"],
    "UK": ["British English", "Welsh", "Scottish Gaelic"],
    "EU/EEA": ["German", "French", "Spanish", "Italian", "Polish", "Dutch"],
    "North America": ["American English", "Canadian French", "Mexican Spanish"],
    "Latin America": ["Latin American Spanish", "Brazilian Portuguese"],
    "Middle East (GCC)": ["Gulf Arabic", "Modern Standard Arabic", "English", "Hindi", "Urdu"],
    "Middle East and North Africa": ["Modern Standard Arabic", "Egyptian Arabic", "Levantine Arabic",
                                     "Maghrebi Arabic", "Persian", "Turkish", "Hebrew"],
    "Sub-Saharan Africa": ["Swahili", "Hausa", "Yoruba", "Amharic", "Zulu", "English", "French"],
    "South Asia": ["Hindi", "Bengali", "Urdu", "Tamil", "Telugu", "Marathi", "Indian English"],
    "East Asia": ["Mandarin Chinese", "Cantonese", "Japanese", "Korean"],
    "Southeast Asia": ["Indonesian", "Vietnamese", "Thai", "Filipino", "Malay"],
    "Oceania": ["Australian English", "New Zealand English", "Māori"],
}

# (origin regions or countries, processing locations it doesn't apply to, the rule)
TRANSFER_RULES = [
    ({"china", "east asia"}, set(),
     "China: exporting personal data needs a security assessment or regulator approval."),
    ({"saudi arabia", "middle east (gcc)", "middle east and north africa"}, set(),
     "Saudi Arabia (PDPL): sensitive personal data must stay in the country unless an exemption applies."),
    ({"eu/eea"}, {"EU/EEA", "UK"},
     ("EU/EEA: personal data leaving the EEA needs an adequacy decision or another transfer mechanism "
      "(e.g. standard contractual clauses).")),
]


# --- the saved brief --------------------------------------------------------------------------

class Pick(BaseModel):
    """One category's choices. amount/unit for the volume target; amount/amount_to for years."""

    values: list[str] = []
    amount: float | None = None
    amount_to: float | None = None
    unit: str | None = None
    importance: Importance = "nice"
    source: Literal["stated", "inferred"] = "stated"


def _number(x: float) -> str:
    return f"{x:,.0f}" if x == int(x) else f"{x:,}"


class Brief(BaseModel):
    description: str = Field(description="What data, in a few words, e.g. multi-speaker meeting audio")
    picks: dict[str, Pick] = {}
    notes: str = ""

    def pick(self, key: str) -> Pick:
        return self.picks.get(key) or Pick()

    def chosen(self, key: str) -> list[str]:
        """The values picked that express a preference, whatever the importance."""
        return [v for v in self.pick(key).values if v not in CATEGORIES[key].neutral]

    def is_set(self, key: str) -> bool:
        cat, p = CATEGORIES[key], self.pick(key)
        if cat.pick == "amount":
            return p.amount is not None
        if cat.pick == "years":
            return p.amount is not None or p.amount_to is not None
        return bool(self.chosen(key))

    def importance(self, key: str) -> Importance:
        """any when nothing (or only 'no preference') is picked."""
        return self.pick(key).importance if self.is_set(key) else "any"

    def active(self, key: str) -> list[str]:
        """Values that count for matching (S F R V): importance must or nice."""
        return self.chosen(key) if self.importance(key) != "any" else []

    def must(self, key: str) -> bool:
        return self.importance(key) == "must"

    def modality_sections(self) -> list[Section]:
        picked = set(self.pick("modality").values)
        return [s for s in SECTIONS if s.modalities and picked & set(s.modalities)]

    @property
    def languages(self) -> list[str]:
        return self.active("languages")

    @property
    def regions(self) -> list[str]:
        return self.active("region")

    def value_text(self, key: str) -> str:
        cat, p = CATEGORIES[key], self.pick(key)
        if cat.pick == "amount":
            return f"{_number(p.amount)} {p.unit or ''}".strip() if p.amount is not None else ""
        if cat.pick == "years":
            return "–".join(str(int(x)) for x in (p.amount, p.amount_to) if x is not None)
        return ", ".join(self.chosen(key))

    def volume_wanted(self) -> str:
        """The target, and how much of it one seller needs to hold (C2)."""
        if self.importance("volume") == "any":
            return "any"
        share = self.active("seller_share")
        text = self.value_text("volume")
        return f"{text} in total; one seller should hold {share[0]} of it" if share else text

    def summary(self, codes: str | None = None) -> str:
        """A readable paragraph. With `codes` (e.g. "SFR"), only categories used for those, and
        only must/nice ones, except for E (the email uses every stated value)."""
        must, nice, other = [], [], []
        for s in SECTIONS:
            if s.modalities and s not in self.modality_sections():
                continue
            for cat in s.categories:
                if not self.is_set(cat.key) or cat.key == "exclusions":
                    continue
                if codes and not set(codes) & set(cat.used_for):
                    continue
                item = f"{cat.label}: {self.value_text(cat.key)}"
                importance = self.importance(cat.key)
                if importance == "must":
                    must.append(item)
                elif importance == "nice":
                    nice.append(item)
                elif not codes or "E" in codes:
                    other.append(item)
        parts = [self.description[:1].upper() + self.description[1:]]
        if must:
            parts.append("Must have — " + "; ".join(must))
        if nice:
            parts.append("Nice to have — " + "; ".join(nice))
        if other:
            parts.append("Also — " + "; ".join(other))
        if self.notes.strip():
            parts.append(f"Notes: {self.notes.strip()}")
        return ". ".join(p.rstrip(". ") for p in parts) + "."

    def accepts_holder(self, holder_type: str | None) -> bool:
        """E1 as a must-have filter: companies of other types are dropped."""
        if not self.must("sources") or holder_type is None:
            return True
        return any(holder_type in SOURCE_HOLDERS[s] for s in self.active("sources"))


def transfer_flags(regions: list[str], processing: list[str]) -> list[str]:
    """Rules that restrict moving data from these origin regions to where it will be processed."""
    origin = {r.strip().lower() for r in regions}
    return [rule for places, exempt, rule in TRANSFER_RULES
            if origin & places and not (processing and set(processing) <= exempt)]


def suggested_languages(regions: list[str]) -> list[str]:
    return list(dict.fromkeys(lang for r in regions for lang in REGION_LANGUAGES.get(r, [])))


# --- the Gemini schema, built from the template -----------------------------------------------

# must / nice: the text states it (with that importance); inferred: a sensible default the text
# doesn't state (saved as nice to have, never must); not_set: leave empty
Status = Literal["must", "nice", "inferred", "not_set"]


def _literal(choices: tuple[str, ...]):
    return Literal.__getitem__(choices)


def _pick_model(cat: Category) -> type[BaseModel]:
    fields: dict = {"status": (Status, ...)}  # explained once, in the prompt
    if cat.pick == "single":
        fields["value"] = (_literal(cat.choices) | None, None)
    elif cat.pick == "multi":
        fields["values"] = (list[_literal(cat.choices)], [])
        if cat.free_text:
            fields["other"] = (list[str], Field([], description="Values that fit none of the choices"))
    elif cat.pick == "amount":
        fields["amount"] = (float | None, None)
        fields["unit"] = (_literal(cat.choices) | None, None)
    elif cat.pick == "years":
        fields["from_year"] = (int | None, None)
        fields["to_year"] = (int | None, None)
    else:
        fields["values"] = (list[str], [])
    return create_model(f"Pick_{cat.key}", **fields)


def _fill_model(name: str, sections: list[Section], extra: dict) -> type[BaseModel]:
    # No per-category descriptions: the prompt explains each category (see category_guide).
    fields = dict(extra)
    for s in sections:
        for cat in s.categories:
            fields[cat.key] = (_pick_model(cat), ...)
    return create_model(name, **fields)


# Gemini rejects the whole template as one schema ("too complex"), so the fill is two calls
# made in parallel: the data (A to C and the modality add-ons) and the deal (D to F).
_DEAL = ("D", "E", "F")
BriefFillData = _fill_model(
    "BriefFillData", [s for s in SECTIONS if s.code not in _DEAL],
    {"description": (str, Field(description="What data, in a few words"))})
BriefFillDeal = _fill_model(
    "BriefFillDeal", [s for s in SECTIONS if s.code in _DEAL],
    {"notes": (str, Field("", description="Anything else that matters and fits no category")),
     "questions": (list[str], Field(description="Things to check with the buyer"))})
FILL_PARTS = (BriefFillData, BriefFillDeal)


def category_guide() -> str:
    """One line per category for the refine prompt: key, code, label and help."""
    lines = []
    for s in SECTIONS:
        when = f" (only if modality includes {' or '.join(s.modalities)})" if s.modalities else ""
        lines.append(f"{s.title}{when}:")
        lines += [f"- {c.key}: {c.code} {c.label}" + (f". {c.help}" if c.help else "") for c in s.categories]
    return "\n".join(lines)


class BriefDraft(BaseModel):
    """The refine step's output: a filled-in brief (each pick marked stated or inferred) plus
    questions to check with the buyer."""

    brief: Brief
    questions: list[str] = []


def from_fill(parts: list[BaseModel]) -> BriefDraft:
    """Gemini's answers (one per FILL_PARTS schema) -> BriefDraft. Inferred picks become nice to
    have, never must have."""
    fill = {k: getattr(p, k) for p in parts for k in type(p).model_fields}
    picks = {}
    for cat in CATEGORIES.values():
        raw = fill[cat.key]
        if raw.status == "not_set":
            continue
        pick = Pick(importance="nice" if raw.status == "inferred" else raw.status,
                    source="inferred" if raw.status == "inferred" else "stated")
        if cat.pick == "single":
            pick.values = [raw.value] if raw.value else []
        elif cat.pick == "amount":
            pick.amount, pick.unit = raw.amount, raw.unit
        elif cat.pick == "years":
            pick.amount, pick.amount_to = raw.from_year, raw.to_year
        else:
            values = list(raw.values) + list(getattr(raw, "other", []))
            pick.values = list(dict.fromkeys(v.strip() for v in values if v and v.strip()))
        if pick.values or pick.amount is not None or pick.amount_to is not None:
            picks[cat.key] = pick
    brief = Brief(description=fill["description"].strip(), picks=picks, notes=fill["notes"])
    return BriefDraft(brief=brief, questions=list(fill["questions"]))


# --- sessions saved before the template existed -----------------------------------------------

_LEGACY_LICENCE = {"training_only": [COMMERCIAL, OPEN_CONDITIONS, OPEN], "commercial": [COMMERCIAL, OPEN],
                   "exclusive": [EXCLUSIVE]}
_LEGACY_SOURCES = {"vendor": [VENDORS, MARKETPLACES], "public": [PUBLIC_BODIES, RESEARCH_INSTITUTES],
                   "by_product": [BY_PRODUCT], "technology": [TECHNOLOGY]}


def from_legacy(old: dict) -> Brief:
    """The earlier fixed Brief (data_type, languages, regions, ...) -> the template."""
    must = set(old.get("must_have", []))

    def pick(values, key, **kw):
        return Pick(values=values, importance="must" if key in must else "nice", **kw)

    picks = {"languages": pick(old.get("languages", []), "languages"),
             "region": pick(old.get("regions", []), "regions"),
             "exclusions": pick(old.get("exclusions", []), "exclusions")}
    volume = old.get("volume") or {}
    if volume.get("amount") is not None:
        picks["volume"] = pick([], "volume", amount=volume["amount"], unit=volume.get("unit"))
    if old.get("licence") in _LEGACY_LICENCE:
        picks["licence"] = pick(_LEGACY_LICENCE[old["licence"]], "licence")
    sources = [s for h in old.get("holder_types", []) for s in _LEGACY_SOURCES.get(h, [])]
    if sources and len(sources) < sum(map(len, _LEGACY_SOURCES.values())):
        picks["sources"] = pick(sources, "holder_types")
    details = [f"{k.replace('_', ' ')}: {', '.join(old[k])}"
               for k in ("features", "scenarios", "label_types", "formats") if old.get(k)]
    notes = "; ".join(details + ([old["notes"]] if old.get("notes") else []))
    return Brief(description=old.get("data_type", ""), picks={k: p for k, p in picks.items()
                                                              if p.values or p.amount is not None}, notes=notes)
