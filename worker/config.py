"""Central settings: model, speed, limits, fit weights."""

# Default Gemini model
MODEL = "gemini-3.8-flash"
# For experiments only (eval/settings.py). Check its quota on the API key before using it:
# llm.QuotaExceeded stops a run cleanly, but a run cut short measures nothing.
PRO_MODEL = "gemini-pro-latest"

# Reasoning effort: "minimal" | "low" | "medium" | "high". "low" made a search call ~2.5x
# faster than the default (24s -> 10s) with the same answer. Hypotheses get a bit more.
THINKING_LEVEL = "low"

# Model and thinking per step. Every Gemini call names its step (llm.step), and eval/settings.py
# swaps these to compare settings on the same briefs.
STEPS = ("brief", "hypotheses", "discovery", "screen", "triggers", "confirm", "research", "contacts", "email")
STEP_MODELS = dict.fromkeys(STEPS, MODEL)
STEP_THINKING = {**dict.fromkeys(STEPS, THINKING_LEVEL), "hypotheses": "medium"}

# A Gemini call that hasn't answered by then is abandoned and retried (llm.RETRIES): without a
# limit one hung grounded call stalled a whole search
LLM_TIMEOUT_SECONDS = 180

# Concurrency: Gemini calls and page fetches run in parallel threads
PARALLEL_LLM_CALLS = 8
PARALLEL_FETCHES = 16
PARALLEL_CRAWLS = 3  # deep crawls may each start a headless browser
# Companies researched (or confirmed) at once. Each runs its research and contacts calls side by
# side, so this keeps Gemini calls within PARALLEL_LLM_CALLS; crawls are still capped at
# PARALLEL_CRAWLS.
PARALLEL_RESEARCH = PARALLEL_LLM_CALLS // 2

# Discovery budget for round 1. Each unit below is one Gemini call with Google Search, so it
# costs money.
DISCOVERY = {
    "product_search_hypotheses": 5,  # (a) one call per hypothesis, organisation holders first
    "snowball_seeds": 3,  # (b) one call per seed company, once after the last round
    "directory_hypotheses": 1,  # (c) one call per vendor hypothesis; broad categories mostly add noise
    "marketplace_calls": 3,  # (d) one call per angle in channels.MARKETPLACE_ANGLES (max 3)
    "public_source_calls": 2,  # (e) one call per angle in channels.PUBLIC_ANGLES (max 2)
    "distressed_calls": 3,  # (f) one per angle in channels.DISTRESSED_ANGLES: distress, precedent, regulated
    "signals_calls": 1,  # (g) untapped holders: privacy policies, data-safety labels, job ads, scale claims
    "suppliers_calls": 4,  # (h) one call per tool: its customer stories name the holders
    "records_calls": 1,  # (i) untapped holders: tenders (buyer and supplier), research projects, registers
    "vendor_customer_calls": 2,  # (h) once after the last round: customers of vendors the screen found
    "companies_per_call": 10,
}

# Local markets (WS2): each country and language the brief names gets its own searches, written
# natively in its language: product search on its top local hypotheses, one suppliers call on
# the tools most used there, and one call on its national tender portal.
LOCAL_MARKETS = 3  # at most this many per run, in the brief's order
LOCAL_CALLS = {"product_search": 2, "suppliers": 1, "records": 1}

# Rounds (WS3): after round 1 is screened, up to two more rounds rerun only the channels that
# found new qualifying companies (see discovery/rounds.py), told what was already found. A round
# that adds fewer than ROUND_MIN_NEW stops the search.
DISCOVERY_ROUNDS = 3
ROUND_MIN_NEW = 3
ROUND_CALLS = {"ready": 2, "untapped": 10}  # most calls per later round, per side
EXCLUDED_LIST_MAX = 150  # "already found" entries in a later round's prompts, best fit first
LIKE_THESE = 5  # good organisation holders shown as examples in later rounds
FILL_ARCHETYPES = 4  # archetypes with no qualifying holder get one new hypothesis each in round 2
UNCOVERED_MARKET_CALLS = 2  # round 2: extra calls for a must-have language with no qualifying company

# Snowball only from untapped holders (plus collectors when the buyer wants data recorded to
# order) with fit >= SEED_FIT and evidence for the brief's languages or regions. Never from tool
# vendors: their competitors are more vendors, and their customers hold the data.
SNOWBALL_SEED_TYPES = ["untapped"]

# Relevance screen: companies per Flash call
SCREEN_BATCH_SIZE = 10
SCREEN_TEXT_CHARS = 1500  # text sent per page (homepage, and the source page if it's another page)
# A homepage with less text than this is rendered in a browser and sent with more room, so a
# small or JavaScript-heavy site isn't ranked low just for being thin
THIN_PAGE_CHARS = 500
THIN_PAGE_TEXT_CHARS = 3000
THIN_PAGE_RENDERS = 20  # most browser renders per screen call; renders take turns

# Big brands and regulated institutions (WS6): in scope only with a dated trigger
TRIGGER_CHECKS = 10  # the best by data match get one grounded call each, during the search
TRIGGER_YEARS = 3  # a trigger counts if it happened this recently, or is still going on
TRIGGER_WILLING = {"distress": 5, "precedent": 4, "law": 3}  # the willing answer a verified trigger gives

# Confirmation (WS4): the best candidates of each list are checked for fit and saleability, with
# quotes, before the default view shows them
CONFIRM_TOP = {"ready": 10, "untapped": 20}
# Must-haves that must be shown to confirm fit; other must-haves only reject when contradicted,
# since untapped holders rarely publish volumes, labels or terms. The data ones need a quote, or
# a screen data match of at least CONFIRM_SCREEN_MATCH ("clearly this kind of data"), and at
# least one quote must show the data either way. Languages and regions also count the screen's
# evidence: an English site rarely says "English".
CONFIRM_CATEGORIES = ("modality", "domain", "languages", "region")
CONFIRM_SCREEN_MATCH = 4
# Never a reason to reject: the volume target is a total that several sellers can make up, and
# an untapped holder's terms only cover its clients' use today, so outreach asks about them
NEVER_REJECTS = {"volume"}
UNTAPPED_NEGOTIABLE = {"permitted_use", "deal_terms"}

# Grounded calls one search may make. Under pressure, round 3 goes first, then local markets,
# then round 2; trigger checks and confirmation are kept.
MAX_GROUNDED_CALLS = 110

# Research: text sent per crawled page (homepage, privacy, terms)
RESEARCH_TEXT_CHARS = 6000

# Contacts: at most this many from the search call (website contacts are free)
CONTACTS_PER_COMPANY = 5

# Fit: weighted mean of the known scores (1 to 5). Weights of unknown scores are left out and
# the rest rescaled, so an unknown neither helps nor hurts. Weights must sum to 1.0.
FIT_WEIGHTS = {"data_match": 0.40, "coverage": 0.25, "licence": 0.20, "volume": 0.15}
RELEVANT_FIT = 2.5  # shown by default and counted as relevant
SEED_FIT = 3.5  # snowball seeds
# Caps. The must-have cap sits below RELEVANT_FIT so such companies drop out of the default view.
MUST_HAVE_CAP = 2.0  # a must-have language or region with no evidence
INTERNAL_ONLY_CAP = 2.0  # research found a ready source's data is for internal use only
# Untapped holders: any answer of 1, the screen's too (or D3 excludes its customers' data) ...
NOT_SELLABLE_CAP = 2.0
LOW_SALEABILITY_CAP = 3.0  # ... or research verified a 2 on "exists" or "allowed"
VENDOR_CAP = 2.0  # tool vendor: a way to the holders (its customers), not a source itself
TRIGGER_CAP = 2.0  # big brand or regulated institution with no verified trigger
# Untapped holders with no saleability answer start at 3 (medium); these archetypes start at 4
# when the company owns the data: nobody uses it any more, so it is more likely for sale
ARCHETYPE_PRIOR = {"dormant": 4, "production_leftovers": 4, "research": 4, "assessment": 4}
