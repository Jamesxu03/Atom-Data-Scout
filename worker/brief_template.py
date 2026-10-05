"""The brief template, defined once as data: sections, categories, choices, single or multi
pick, the modality that shows an add-on, and where the tool uses each category. The form, the
Gemini schema, the prompts, the scoring and the email step all read from here, so a choice
exists in one place only.

Where the tool uses a category ("used for" codes):
  S  search queries        F  screening filter      R  ranking score
  V  checked during deep research                   E  outreach email

A category that is filled in counts (nice to have); its Must switch makes it a hard cap when
evidence contradicts it. An empty category is ignored. Unknown evidence stays neutral.

The template only asks what changes who is found or how they rank. Deal terms that only research
and the email use go in one Deal terms text (F1); details too fine for a category go in the
notes, which the screen also reads. Briefs saved with the older, longer template are upgraded on
load (see _upgrade_picks).
"""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, create_model, model_validator

PickType = Literal["single", "multi", "amount", "years", "text", "paragraph"]
Importance = Literal["must", "nice", "any"]  # any: the category is empty, so it counts for nothing
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
READY = "Ready sources (sellers, marketplace listings, public datasets, collectors)"
UNTAPPED = "Untapped holders (have the data, not selling it)"
OPEN, OPEN_CONDITIONS, RESTRICTED = "Public domain / Open", "Open with conditions", "Restricted / Conditional"
COMMERCIAL, EXCLUSIVE, UNCLEAR = "Commercial, non-exclusive", "Commercial, exclusive", "Unclear"
INTERNAL_ONLY = "Internal use only"  # found by research only; never acceptable
COLLECTION_OK, EXISTING_ONLY = "Welcome", "Existing data only"
# E1 labels from earlier versions of the template -> today's
_OLD_SOURCE_LABELS = {
    **dict.fromkeys(["Data vendors and collection companies", "Data sellers (existing datasets)",
                     "Data collectors (record to order)", "Marketplace listings", "Open repositories and public bodies",
                     "Research institutes and universities"], (READY,)),
    **dict.fromkeys(["By-product holders (their service generates the data)", "Untapped holders (own data, not selling it)",
                     "Distressed or insolvent companies", "Distressed, closed or for-sale companies",
                     "Technology providers with internal data"], (UNTAPPED,)),
}
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
        Category("annotation", "A3", "Minimum annotation", "single", "FR",
                 ("Raw, no labels", "Basic labels", "Rich labels", "Expert-verified labels")),
    )),
    Section("B", "Where", (
        Category("region", "B1", "Region of origin", "multi", "SFR",
                 ("Global", "UK", "EU/EEA", "North America", "Latin America", "Middle East (GCC)",
                  "Middle East and North Africa", "Sub-Saharan Africa", "South Asia", "East Asia",
                  "Southeast Asia", "Oceania"), free_text=True, neutral=("Global",),
                 help="Type specific countries if you need them"),
        Category("languages", "B2", "Languages and dialects", "text", "SFR", free_text=True,
                 help="Suggestions follow the regions picked; type your own, e.g. Gulf Arabic or "
                      "Hinglish (code-switching)"),
    )),
    Section("C", "Volume and timing", (
        Category("volume", "C1", "Volume target", "amount", "RVE",
                 ("hours", "recordings", "records/rows", "images", "video hours", "documents", "tokens", "GB",
                  "data points"),
                 help="The total: several sellers can be combined to reach it"),
        Category("recency", "C2", "Recency", "single", "FV",
                 ("Any", "Last 5 years", "Last 2 years", "Last 12 months", "Live/ongoing"), neutral=("Any",)),
        Category("collection", "C3", "Data recorded to order", "single", "SFE", (COLLECTION_OK, EXISTING_ONLY),
                 help="Welcome: also search data collectors. Existing data only, as a must have, caps collectors"),
    )),
    Section("D", "Rights", (
        Category("licence", "D1", "Acceptable licence", "multi", "FRV",
                 (OPEN, OPEN_CONDITIONS, RESTRICTED, COMMERCIAL, EXCLUSIVE, UNCLEAR),
                 help="Public domain / Open: CC0, public domain, open government data. Open with conditions: "
                      "CC BY, CC BY-SA, ODbL. Restricted: non-commercial, research only, no derivatives. "
                      "Unclear: keep and verify later."),
        Category("permitted_use", "D2", "Permitted use needed", "multi", "VE",
                 ("Evaluation only", "Internal model training", "Fine-tuning", "Training for a commercial product",
                  "Retrieval/RAG", "Resale or redistribution")),
        Category("customer_data", "D3", "Data the seller holds for its customers", "single", "FR",
                 (CUSTOMER_EXCLUDE, CUSTOMER_CONSENT, CUSTOMER_LATER),
                 help="e.g. a company recording its clients' customer-support calls"),
    )),
    Section("E", "Where to look", (
        Category("sources", "E1", "Source types", "multi", "SF",
                 (READY, UNTAPPED),
                 help="Empty searches for both. Must have also drops companies of the other type."),
        Category("exclusions", "E2", "Exclusions", "text", "F", free_text=True,
                 help="Companies or domains to skip (already contacted, the buyer's competitors)"),
    )),
    Section("F", "Deal terms", (
        Category("deal_terms", "F1", "Deal terms", "paragraph", "VE",
                 help="Checked in research and asked about in the email, never used to screen: personal "
                      "data and consent, people in frame, exclusivity, term, documents, quality evidence, "
                      "demographics, where the buyer will process the data, pricing, timeline, sample "
                      "(not the budget: the email would see it). Must have caps the fit when research finds the terms can't be met."),
    )),
    Section("AUDIO", "Audio/speech details", (
        Category("speakers", "AU1", "Speakers per recording", "multi", "FR",
                 ("1", "2", "3 to 5", "6+", "Overlapping speech")),
        Category("speech_style", "AU2", "Speech style", "multi", "SFR",
                 ("Spontaneous conversation", "Call centre", "Read/scripted", "Broadcast")),
        Category("audio_quality", "AU3", "Audio quality", "multi", "FR",
                 ("Telephone (8 kHz)", "Wideband (16 kHz+)", "Studio (44.1/48 kHz)", "Any"), neutral=("Any",)),
        Category("transcript", "AU4", "Transcript", "multi", "FR",
                 ("Clean transcript", "Verbatim transcript", "Utterance timestamps", "Word timestamps",
                  "Speaker labels")),
    ), modalities=(AUDIO,)),
    Section("VISUAL", "Video/image details", (
        Category("viewpoint", "VI1", "Viewpoint", "multi", "SFR",
                 ("First-person", "Handheld third-person", "Fixed camera", "Dashcam", "Aerial")),
        Category("visual_labels", "VI2", "Labels", "multi", "FR",
                 ("Captions", "Tags", "Bounding boxes", "Segmentation", "Pose", "Actions/events")),
    ), modalities=(VIDEO, IMAGE)),
    Section("TEXT", "Text details", (
        Category("genre", "TX1", "Genre", "multi", "SFR",
                 ("Chat/dialogue", "Documents/reports", "Web content", "Transcripts", "Code")),
    ), modalities=(TEXT,)),
    Section("RECORDS", "Tabular, sensor or geospatial details", (
        Category("entity_level", "TB1", "Entity level", "multi", "SFR",
                 ("Individual", "Household", "Facility/site", "Company", "City/region", "Country")),
        Category("coverage_period", "TB2", "Coverage period", "years", "FR", help="From and to years"),
    ), modalities=(TABULAR, SENSOR, GEO)),
)

CATEGORIES: dict[str, Category] = {c.key: c for s in SECTIONS for c in s.categories}
SECTION_OF: dict[str, Section] = {c.key: s for s in SECTIONS for c in s.categories}

# E1 choice -> the holder type (from the screen) it covers
SOURCE_HOLDERS = {READY: "ready", UNTAPPED: "untapped"}

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

# (origin regions or countries, the rule). Where the buyer processes the data, if stated, is in
# the deal terms, so research judges whether a rule applies.
TRANSFER_RULES = [
    ({"china", "east asia"}, "China: exporting personal data needs a security assessment or regulator approval."),
    ({"saudi arabia", "middle east (gcc)", "middle east and north africa"},
     "Saudi Arabia (PDPL): sensitive personal data must stay in the country unless an exemption applies."),
    ({"eu/eea"}, ("EU/EEA: personal data leaving the EEA (other than to the UK) needs an adequacy decision or "
                  "another transfer mechanism (e.g. standard contractual clauses).")),
]

# --- briefs saved with the older, longer template ---------------------------------------------

# Removed categories: key -> (label, choices that meant "no preference")
_OLD_NOTES = {  # details of the data: into the notes, which the screen reads
    "production": ("How it was produced", ("Any",)),
    "code_switching": ("Code-switching", ()),
    "seller_share": ("Minimum from one seller", ()),
    "overlap": ("Overlapping speech", ()),
    "channels": ("Channels", ()),
    "environment": ("Environment", ()),
    "resolution": ("Minimum resolution", ("Any",)),
    "text_format": ("Text format", ()),
    "granularity": ("Granularity", ()),
    "geocoding": ("Geo-coding", ("Not needed",)),
}
_OLD_TERMS = {  # into F1 Deal terms. The old F1 Budget is dropped: it was never passed on
    "personal_data": ("Personal data", ()),
    "people_in_frame": ("People in frame", ()),
    "quality": ("Quality evidence", ("None needed",)),
    "demographics": ("Demographic coverage", ("Not needed",)),
    "documents": ("Documents required", ()),
    "processing": ("Where the buyer will process it", ()),
    "exclusivity": ("Exclusivity", ()),
    "term": ("Term", ("Any",)),
    "pricing": ("Pricing model", ("Any",)),
    "timeline": ("Timeline", ("Flexible",)),
    "sample": ("Sample before purchase", ("Not needed",)),
}
# The removed categories research used to check (a must have capped the fit): now F1's check
OLD_CHECKS = {"personal_data", "people_in_frame", "quality", "demographics", "documents", "processing"}
# AU7 to AU9 -> AU4 Transcript choices
_OLD_TRANSCRIPT = {"transcript": {"Clean": "Clean transcript", "Verbatim": "Verbatim transcript"},
                   "timestamps": {"Utterance": "Utterance timestamps", "Word": "Word timestamps"},
                   "speaker_labels": {"Needed": "Speaker labels"}}
_OLD_DELIVERY_CUSTOM = "Custom collection to order"


def _upgrade_picks(data: dict) -> dict:
    """A brief saved with the older template -> today's. Picks set to Any are dropped (they
    counted for nothing); removed categories become a line in the notes or the deal terms, which
    is a must have if a removed research check was. Briefs already in today's template pass
    through unchanged."""
    picks = {k: p.model_dump() if isinstance(p, BaseModel) else dict(p) for k, p in (data.get("picks") or {}).items()}
    picks = {k: p for k, p in picks.items() if p.get("importance") != "any"}
    notes, terms, terms_must = [], [], False

    def take(key: str, neutral: tuple[str, ...] = ()) -> tuple[list[str], bool]:
        p = picks.pop(key, None) or {}
        return [v for v in p.get("values", []) if v not in neutral], p.get("importance") == "must"

    if "delivery" in picks:  # C4 Delivery type -> C3 Data recorded to order
        values, must = take("delivery")
        if values:
            picks["collection"] = {"values": [COLLECTION_OK if _OLD_DELIVERY_CUSTOM in values else EXISTING_ONLY],
                                   "importance": "must" if must else "nice"}
    if "Required" in picks.get("overlap", {}).get("values", []):
        _, must = take("overlap")
        speakers = picks.setdefault("speakers", {"values": [], "importance": "nice"})
        speakers["values"] = [*speakers.get("values", []), "Overlapping speech"]
        if must:
            speakers["importance"] = "must"
    merged, merged_must = [], False
    for key, choices in _OLD_TRANSCRIPT.items():
        if key == "transcript" and not set(picks.get(key, {}).get("values", [])) & {"None", *choices}:
            continue  # already today's AU4
        values, must = take(key)
        merged += [choices.get(v, v) for v in values if v in choices or v in CATEGORIES["transcript"].choices]
        merged_must |= must and any(v in choices for v in values)
    if merged:
        picks["transcript"] = {"values": list(dict.fromkeys(merged)), "importance": "must" if merged_must else "nice"}
    for key, (label, neutral) in _OLD_NOTES.items():
        if (values := take(key, neutral)[0]):
            notes.append(f"{label}: {', '.join(values)}")
    for key, (label, neutral) in _OLD_TERMS.items():
        values, must = take(key, neutral)
        if values:
            terms.append(f"{label}: {', '.join(values)}")
            terms_must |= must and key in OLD_CHECKS
    if terms:
        picks["deal_terms"] = {"values": ["; ".join(terms)], "importance": "must" if terms_must else "nice"}
    if notes:
        data = {**data, "notes": "; ".join(filter(None, [(data.get("notes") or "").strip().rstrip(".;"), *notes]))}
    return {**data, "picks": {k: p for k, p in picks.items() if k in CATEGORIES}}


# --- the saved brief --------------------------------------------------------------------------

class Pick(BaseModel):
    """One category's choices. amount/unit for the volume target; amount/amount_to for years."""

    values: list[str] = []
    amount: float | None = None
    amount_to: float | None = None
    unit: str | None = None
    importance: Literal["must", "nice"] = "nice"
    source: Literal["stated", "inferred"] = "stated"


def _number(x: float) -> str:
    return f"{x:,.0f}" if x == int(x) else f"{x:,}"


class Brief(BaseModel):
    description: str = Field(description="What data, in a few words, e.g. multi-speaker meeting audio")
    picks: dict[str, Pick] = {}
    notes: str = ""

    @model_validator(mode="before")
    @classmethod
    def _upgrade_template(cls, data):
        return _upgrade_picks(data) if isinstance(data, dict) else data

    @model_validator(mode="after")
    def _upgrade_sources(self):
        """Briefs saved with the old E1 labels get the new ones."""
        if (p := self.picks.get("sources")) and any(v in _OLD_SOURCE_LABELS for v in p.values):
            p.values = list(dict.fromkeys(n for v in p.values for n in _OLD_SOURCE_LABELS.get(v, [v])))
        return self

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
        """any when nothing (or only 'no preference') is picked: it counts for nothing."""
        return self.pick(key).importance if self.is_set(key) else "any"

    def active(self, key: str) -> list[str]:
        """Values that count for matching (S F R V): those of a category that is set."""
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
        return self.value_text("volume") or "any"

    def summary(self, codes: str | None = None) -> str:
        """A readable paragraph. With `codes` (e.g. "SFR"), only categories used for those."""
        must, nice = [], []
        for s in SECTIONS:
            if s.modalities and s not in self.modality_sections():
                continue
            for cat in s.categories:
                if not self.is_set(cat.key) or cat.key == "exclusions":
                    continue
                if codes and not set(codes) & set(cat.used_for):
                    continue
                (must if self.must(cat.key) else nice).append(f"{cat.label}: {self.value_text(cat.key)}")
        parts = [self.description[:1].upper() + self.description[1:]]
        if must:
            parts.append("Must have — " + "; ".join(must))
        if nice:
            parts.append("Nice to have — " + "; ".join(nice))
        if self.notes.strip():
            parts.append(f"Notes: {self.notes.strip()}")
        return ". ".join(p.rstrip(". ") for p in parts) + "."

    def accepts_holder(self, holder_type: str | None) -> bool:
        """E1 as a must-have filter: companies of other types are dropped."""
        if not self.must("sources") or holder_type is None:
            return True
        return any(holder_type == SOURCE_HOLDERS[s] for s in self.active("sources"))


def transfer_flags(regions: list[str]) -> list[str]:
    """Rules that may restrict moving personal data out of these origin regions."""
    origin = {r.strip().lower() for r in regions}
    return [rule for places, rule in TRANSFER_RULES if origin & places]


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
    elif cat.pick == "paragraph":
        fields["value"] = (str | None, None)
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


BriefFill = _fill_model("BriefFill", list(SECTIONS), {
    "description": (str, Field(description="What data, in a few words")),
    "notes": (str, Field("", description="Details of the data that fit no category")),
    "questions": (list[str], Field(description="Things to check with the buyer")),
})
FILL_PARTS = (BriefFill,)  # one schema now; the step still accepts several parts


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
        if cat.pick in ("single", "paragraph"):
            pick.values = [raw.value.strip()] if raw.value and raw.value.strip() else []
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
_LEGACY_SOURCES = {"vendor": READY, "public": READY, "by_product": UNTAPPED, "technology": UNTAPPED}


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
    sources = list(dict.fromkeys(_LEGACY_SOURCES[h] for h in old.get("holder_types", []) if h in _LEGACY_SOURCES))
    if len(sources) == 1:  # both = no preference
        picks["sources"] = pick(sources, "holder_types")
    details = [f"{k.replace('_', ' ')}: {', '.join(old[k])}"
               for k in ("features", "scenarios", "label_types", "formats") if old.get(k)]
    notes = "; ".join(details + ([old["notes"]] if old.get("notes") else []))
    return Brief(description=old.get("data_type", ""), picks={k: p for k, p in picks.items()
                                                              if p.values or p.amount is not None}, notes=notes)
