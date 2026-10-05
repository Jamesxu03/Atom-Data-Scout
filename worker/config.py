"""Central settings: model, speed, limits, fit weights."""

# Every Gemini call uses this model
MODEL = "gemini-3.8-flash"

# Reasoning effort: "minimal" | "low" | "medium" | "high". "low" made a search call ~2.5x
# faster than the default (24s -> 10s) with the same answer. Hypotheses get a bit more.
THINKING_LEVEL = "low"
HYPOTHESES_THINKING_LEVEL = "medium"

# Concurrency: Gemini calls and page fetches run in parallel threads
PARALLEL_LLM_CALLS = 8
PARALLEL_FETCHES = 16
PARALLEL_CRAWLS = 3  # deep crawls may each start a headless browser
# Companies researched at once. Each runs its research and contacts calls side by side, so this
# keeps Gemini calls within PARALLEL_LLM_CALLS; crawls are still capped at PARALLEL_CRAWLS.
PARALLEL_RESEARCH = PARALLEL_LLM_CALLS // 2

# Discovery budget. Each unit below is one Gemini call with Google Search, so it costs money.
DISCOVERY = {
    "product_search_hypotheses": 5,  # (a) one call per hypothesis
    "snowball_seeds": 3,  # (b) one call per seed company
    "directory_hypotheses": 1,  # (c) one call per hypothesis; broad categories mostly add noise
    "marketplace_calls": 3,  # (d) one call per angle in channels.MARKETPLACE_ANGLES (max 3)
    "public_source_calls": 2,  # (e) one call per angle in channels.PUBLIC_ANGLES (max 2)
    "distressed_calls": 1,  # (f) insolvent, in administration, shut down or for sale
    "signals_calls": 1,  # (g) untapped holders: privacy policies, data-safety labels, job ads, scale claims
    "suppliers_calls": 1,  # (h) untapped holders: customers of the tools they run on, member directories
    "records_calls": 1,  # (i) untapped holders: tenders, finished research projects, registers
    "companies_per_call": 10,
}

# Snowball only from untapped holders (plus collectors when the buyer wants data recorded to
# order) with fit >= SEED_FIT and evidence for the brief's languages or regions. Never from tool
# vendors: their competitors are more vendors, and their customers hold the data.
SNOWBALL_SEED_TYPES = ["untapped"]

# Relevance screen: companies per Flash call
SCREEN_BATCH_SIZE = 10
SCREEN_TEXT_CHARS = 1500  # text sent per page (homepage, and the source page if it's another page)

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
