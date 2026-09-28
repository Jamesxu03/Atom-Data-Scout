"""Central settings: models, limits, scoring weights."""

# Cheap bulk work (profile extraction) vs. reasoning work (hypotheses, scoring)
MODEL_FLASH = "gemini-3.8-flash"
# Free tier has 0 quota for Pro — using Flash until billing is on. Then restore:
# MODEL_PRO = "gemini-3.1-pro-preview"
MODEL_PRO = MODEL_FLASH

# Each search costs money — keep low while testing, raise to 20 once the flow works
MAX_CANDIDATES = 5

# Weights must sum to 1.0
SCORE_WEIGHTS = {
    "data_match": 0.25,
    "estimated_volume": 0.15,
    "rights_clarity": 0.30,
    "seller_motivation": 0.15,
    "contactability": 0.15,
}
