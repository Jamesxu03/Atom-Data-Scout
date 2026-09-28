"""Central settings: model, speed, limits, scoring weights."""

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

# Discovery budget. Each unit below is one Gemini call with Google Search, so it costs money.
DISCOVERY = {
    "product_search_hypotheses": 5,  # (a) one call per hypothesis
    "snowball_seeds": 3,  # (b) one call per seed company
    "directory_hypotheses": 3,  # (c) one call per hypothesis
    "marketplace_calls": 1,  # (d)
    "companies_per_call": 10,
}

# Relevance screen: homepages per Flash call
SCREEN_BATCH_SIZE = 10
SCREEN_TEXT_CHARS = 1500  # homepage text sent per company

# Only this many relevant candidates go to the deep crawl — keep low while testing
MAX_PROFILED = 5

# Contacts step: one Gemini search call per profiled company (website contacts are free)
FIND_CONTACTS = True
CONTACTS_PER_COMPANY = 5

# Weights must sum to 1.0
SCORE_WEIGHTS = {
    "data_match": 0.25,
    "estimated_volume": 0.15,
    "rights_clarity": 0.30,
    "seller_motivation": 0.15,
    "contactability": 0.15,
}
