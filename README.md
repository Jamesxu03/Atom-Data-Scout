# Atom Data Scout

Internal tool: given a buyer's data brief, find companies likely to hold that data as a
by-product of their business, check they're relevant, and find people to contact.

```
brief → data spec → holder hypotheses → discovery (4 channels) → relevance screen
      → snowball from relevant companies → screen → deep crawl + contacts
```

Every Gemini call uses `gemini-3.8-flash` (see `worker/config.py`). Independent calls and
page fetches run in parallel.

## Layout

```
app.py                  Streamlit UI (runs the pipeline in a background thread)
worker/
  config.py             model, thinking level, concurrency, budgets, score weights
  llm.py                ask(): structured output, retries, optional Google Search
  pipeline.py           runs every step; saves each stage under data/runs/<time>/
  discovery/            product search, snowball, directories, marketplaces; dedupe by domain
  steps/                parse_brief, hypotheses, screen, contacts
  crawl.py              homepage / privacy / terms / contact pages (httpx, playwright fallback)
  parallel.py           thread pool that keeps finished results if the quota runs out
  demo.py               fictional data for demo mode
eval/
  seed_companies.csv    companies you know are relevant (fill this in)
  discovery_recall.py   what share of them discovery found, and via which channel
tests/                  pytest; Gemini is faked and network is blocked
data/                   run outputs and page cache (gitignored)
```

Not built yet: profile extraction and scoring in live runs (demo only), and company
registry lookups (the profile has country-neutral registry fields for when one is added).

**Phase 1:** Python + Streamlit, to validate result quality.
**Phase 2:** job queue + separate frontend, so long runs don't depend on a browser tab.

## Setup

Requires [uv](https://docs.astral.sh/uv/) (installs Python 3.12 automatically).

```bash
uv sync                               # create .venv and install dependencies
uv run playwright install chromium    # headless browser for JS-heavy sites
cp .env.example .env                  # then add your GEMINI_API_KEY
```

## Use

```bash
uv run streamlit run app.py           # the UI; switch off Demo mode for a real run
uv run python test_gemini.py          # check the API key works (1 call)
uv run pytest                         # tests, no API calls
RUN_LIVE=1 uv run pytest              # also run the live Gemini test
uv run python -m eval.discovery_recall   # recall against eval/seed_companies.csv
```

Each step can also run alone on the latest run folder, e.g.
`uv run python -m worker.steps.parse_brief "<brief>"`, then `worker.steps.hypotheses`,
`worker.discovery`, `worker.steps.screen`.

Add a dependency with `uv add <package>`.
