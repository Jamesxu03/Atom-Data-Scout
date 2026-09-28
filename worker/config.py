"""Central settings: models, limits, scoring weights."""

# Cheap bulk work (profile extraction) vs. reasoning work (hypotheses, scoring)
MODEL_FLASH = "gemini-3.8-flash"
MODEL_PRO = MODEL_FLASH  # Flash for everything for now; Pro option: "gemini-3.1-pro-preview"

# Discovery budget. Each unit below is one Gemini call with Google Search, so it costs money
# (and the free tier allows ~20 calls/day in total). Raise once the flow works.
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
