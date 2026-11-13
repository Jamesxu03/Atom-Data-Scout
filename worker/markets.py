"""Target markets (WS2): the countries and languages a brief names, each searched natively.

Local companies describe themselves in their own language, so English queries mostly miss them.
markets() turns the brief's regions (B1) and languages (B2), both partly free text, into at most
LOCAL_MARKETS country-language pairs, in the brief's order: from the languages first when they
are a must-have, otherwise from the regions. A language with no region named gives the countries
where it is mainly spoken. English is what every channel already searches in, so it becomes a
market only when a region names an English-speaking country (for its domain and tender portal).
"""

from worker.brief_template import Brief
from worker.config import LOCAL_MARKETS
from worker.schemas import Market

ENGLISH = "English"

# country -> (main language, domain)
COUNTRIES = {
    "United Kingdom": (ENGLISH, ".uk"), "United States": (ENGLISH, ".us"), "Canada": (ENGLISH, ".ca"),
    "Australia": (ENGLISH, ".au"), "New Zealand": (ENGLISH, ".nz"), "Ireland": (ENGLISH, ".ie"),
    "Germany": ("German", ".de"), "Austria": ("German", ".at"), "Switzerland": ("German", ".ch"),
    "France": ("French", ".fr"), "Belgium": ("French", ".be"), "Spain": ("Spanish", ".es"),
    "Italy": ("Italian", ".it"), "Netherlands": ("Dutch", ".nl"), "Poland": ("Polish", ".pl"),
    "Portugal": ("Portuguese", ".pt"), "Sweden": ("Swedish", ".se"), "Denmark": ("Danish", ".dk"),
    "Norway": ("Norwegian", ".no"), "Finland": ("Finnish", ".fi"), "Greece": ("Greek", ".gr"),
    "Czechia": ("Czech", ".cz"), "Romania": ("Romanian", ".ro"), "Hungary": ("Hungarian", ".hu"),
    "Mexico": ("Spanish", ".mx"), "Colombia": ("Spanish", ".co"), "Argentina": ("Spanish", ".ar"),
    "Chile": ("Spanish", ".cl"), "Brazil": ("Portuguese", ".br"),
    "Saudi Arabia": ("Arabic", ".sa"), "United Arab Emirates": ("Arabic", ".ae"), "Qatar": ("Arabic", ".qa"),
    "Kuwait": ("Arabic", ".kw"), "Egypt": ("Arabic", ".eg"), "Jordan": ("Arabic", ".jo"),
    "Lebanon": ("Arabic", ".lb"), "Morocco": ("Arabic", ".ma"), "Algeria": ("Arabic", ".dz"),
    "Turkey": ("Turkish", ".tr"), "Iran": ("Persian", ".ir"), "Israel": ("Hebrew", ".il"),
    "Afghanistan": ("Pashto", ".af"), "Pakistan": ("Urdu", ".pk"), "India": ("Hindi", ".in"),
    "Bangladesh": ("Bengali", ".bd"), "Sri Lanka": ("Sinhala", ".lk"), "Nepal": ("Nepali", ".np"),
    "China": ("Chinese", ".cn"), "Taiwan": ("Chinese", ".tw"), "Hong Kong": ("Cantonese", ".hk"),
    "Japan": ("Japanese", ".jp"), "South Korea": ("Korean", ".kr"), "Singapore": (ENGLISH, ".sg"),
    "Indonesia": ("Indonesian", ".id"), "Vietnam": ("Vietnamese", ".vn"), "Thailand": ("Thai", ".th"),
    "Philippines": ("Filipino", ".ph"), "Malaysia": ("Malay", ".my"), "Myanmar": ("Burmese", ".mm"),
    "Cambodia": ("Khmer", ".kh"), "Nigeria": (ENGLISH, ".ng"), "Kenya": ("Swahili", ".ke"),
    "Tanzania": ("Swahili", ".tz"), "Ethiopia": ("Amharic", ".et"), "South Africa": (ENGLISH, ".za"),
}
# Other ways briefs write a country
COUNTRY_NAMES = {"uk": "United Kingdom", "britain": "United Kingdom", "england": "United Kingdom",
                 "usa": "United States", "us": "United States", "america": "United States",
                 "uae": "United Arab Emirates", "emirates": "United Arab Emirates", "ksa": "Saudi Arabia",
                 "korea": "South Korea", "czech republic": "Czechia"}

# B1 region -> its main markets, as (country, language)
REGION_MARKETS = {
    "UK": [("United Kingdom", ENGLISH)],
    "EU/EEA": [("Germany", "German"), ("France", "French"), ("Spain", "Spanish")],
    "North America": [("United States", ENGLISH), ("Canada", "French"), ("Mexico", "Spanish")],
    "Latin America": [("Mexico", "Spanish"), ("Brazil", "Portuguese"), ("Colombia", "Spanish")],
    "Middle East (GCC)": [("Saudi Arabia", "Arabic"), ("United Arab Emirates", "Arabic")],
    "Middle East and North Africa": [("Egypt", "Arabic"), ("Morocco", "Arabic"), ("Turkey", "Turkish")],
    "Sub-Saharan Africa": [("Nigeria", ENGLISH), ("Kenya", "Swahili"), ("South Africa", ENGLISH)],
    "South Asia": [("India", "Hindi"), ("Pakistan", "Urdu"), ("Bangladesh", "Bengali")],
    "East Asia": [("Japan", "Japanese"), ("South Korea", "Korean"), ("China", "Chinese")],
    "Southeast Asia": [("Indonesia", "Indonesian"), ("Vietnam", "Vietnamese"), ("Thailand", "Thai")],
    "Oceania": [("Australia", ENGLISH)],
}

# Words in a B2 language -> (language local sites use, countries where it is mainly spoken).
# The longest match wins, so "Gulf Arabic" reads as the Gulf, not as any Arabic.
LANGUAGES = {
    "gulf arabic": ("Arabic", ["Saudi Arabia", "United Arab Emirates", "Kuwait"]),
    "egyptian arabic": ("Arabic", ["Egypt"]),
    "levantine arabic": ("Arabic", ["Jordan", "Lebanon"]),
    "maghrebi arabic": ("Arabic", ["Morocco", "Algeria"]),
    "arabic": ("Arabic", ["Saudi Arabia", "United Arab Emirates", "Egypt"]),
    "canadian french": ("French", ["Canada"]),
    "french": ("French", ["France", "Belgium", "Canada"]),
    "german": ("German", ["Germany", "Austria", "Switzerland"]),
    "latin american spanish": ("Spanish", ["Mexico", "Colombia", "Argentina"]),
    "mexican spanish": ("Spanish", ["Mexico"]),
    "spanish": ("Spanish", ["Spain", "Mexico", "Colombia"]),
    "brazilian portuguese": ("Portuguese", ["Brazil"]),
    "portuguese": ("Portuguese", ["Portugal", "Brazil"]),
    "italian": ("Italian", ["Italy"]), "dutch": ("Dutch", ["Netherlands", "Belgium"]),
    "polish": ("Polish", ["Poland"]), "swedish": ("Swedish", ["Sweden"]), "danish": ("Danish", ["Denmark"]),
    "norwegian": ("Norwegian", ["Norway"]), "finnish": ("Finnish", ["Finland"]), "greek": ("Greek", ["Greece"]),
    "czech": ("Czech", ["Czechia"]), "romanian": ("Romanian", ["Romania"]), "hungarian": ("Hungarian", ["Hungary"]),
    "turkish": ("Turkish", ["Turkey"]), "persian": ("Persian", ["Iran"]), "farsi": ("Persian", ["Iran"]),
    "hebrew": ("Hebrew", ["Israel"]), "pashto": ("Pashto", ["Afghanistan"]), "urdu": ("Urdu", ["Pakistan"]),
    "hinglish": ("Hindi", ["India"]), "hindi": ("Hindi", ["India"]), "bengali": ("Bengali", ["Bangladesh", "India"]),
    "marathi": ("Marathi", ["India"]), "telugu": ("Telugu", ["India"]), "tanglish": ("Tamil", ["India"]),
    "tamil": ("Tamil", ["India", "Sri Lanka"]), "gujarati": ("Gujarati", ["India"]),
    "kannada": ("Kannada", ["India"]), "malayalam": ("Malayalam", ["India"]), "odia": ("Odia", ["India"]),
    "punjabi": ("Punjabi", ["India", "Pakistan"]), "sinhala": ("Sinhala", ["Sri Lanka"]),
    "nepali": ("Nepali", ["Nepal"]),
    "cantonese": ("Cantonese", ["Hong Kong"]), "mandarin": ("Chinese", ["China", "Taiwan"]),
    "chinese": ("Chinese", ["China", "Taiwan"]), "japanese": ("Japanese", ["Japan"]),
    "korean": ("Korean", ["South Korea"]), "indonesian": ("Indonesian", ["Indonesia"]),
    "vietnamese": ("Vietnamese", ["Vietnam"]), "thai": ("Thai", ["Thailand"]),
    "filipino": ("Filipino", ["Philippines"]), "tagalog": ("Filipino", ["Philippines"]),
    "malay": ("Malay", ["Malaysia"]), "burmese": ("Burmese", ["Myanmar"]), "khmer": ("Khmer", ["Cambodia"]),
    "swahili": ("Swahili", ["Kenya", "Tanzania"]), "amharic": ("Amharic", ["Ethiopia"]),
    "english": (ENGLISH, ["United States", "United Kingdom", "Australia"]),
}


def _market(country: str, language: str, brief_language: str = "") -> Market:
    return Market(country=country, language=language, tld=COUNTRIES[country][1], brief_language=brief_language)


def country_of(text: str) -> str | None:
    """A country written in the brief, e.g. 'india' or 'UAE'; None if it isn't one we know."""
    t = text.strip().lower()
    return next((c for c in COUNTRIES if c.lower() == t), None) or COUNTRY_NAMES.get(t)


def _language(value: str) -> tuple[str, list[str]] | None:
    v = value.lower()
    keys = sorted((k for k in LANGUAGES if k in v), key=len, reverse=True)
    return LANGUAGES[keys[0]] if keys else None


def _from_region(region: str, languages: list[tuple[str, str]]) -> list[Market]:
    """A region's markets. With languages named, a country of the region gets the brief's
    language spoken there, so "South Asia" + "Tamil" searches India in Tamil."""
    if country := country_of(region):
        pairs = [(country, COUNTRIES[country][0])]
    else:
        pairs = REGION_MARKETS.get(region, [])
    out = []
    for country, default in pairs:
        spoken = [(lang, value) for lang, value in languages if country in _language(value)[1]]
        out += [_market(country, lang, value) for lang, value in spoken] or [_market(country, default)]
    return out


def markets(brief: Brief) -> list[Market]:
    """Up to LOCAL_MARKETS countries and languages to search natively, in the brief's order;
    none when it names neither languages nor regions."""
    languages = [(found[0], v) for v in brief.languages if (found := _language(v))]
    from_languages = [_market(country, lang, value) for lang, value in languages if lang != ENGLISH
                      for country in _language(value)[1]]
    from_regions = [m for r in brief.regions for m in _from_region(r, languages)]
    if from_regions and from_languages:  # where both are named, the regions say which countries
        countries = {m.country for m in from_regions}
        from_languages = [m for m in from_languages if m.country in countries] or from_languages
    first, then = ((from_languages, from_regions) if brief.must("languages") or not from_regions
                   else (from_regions, from_languages))
    out: dict[tuple[str, str], Market] = {}
    for m in [*first, *then]:
        out.setdefault((m.country, m.language), m)
    return list(out.values())[:LOCAL_MARKETS]
