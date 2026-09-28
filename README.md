# Atom Data Scout

Finds and ranks data-source companies: web search → crawl → Gemini extraction → scoring, plus a daily scan of insolvency notices.

## Layout

```
worker/            Python pipeline
  sources/         companies_house.py, gazette.py, search.py
db/schema.sql      Supabase table definitions (phase 2)
web/               Next.js frontend (phase 2, not created yet)
.env               API keys — gitignored, copy from .env.example
```

**Phase 1:** Python + Streamlit only, to validate result quality.
**Phase 2:** Next.js frontend + Supabase; the Python worker picks up queued jobs from the database.

## Setup

Requires [uv](https://docs.astral.sh/uv/) (installs Python 3.12 automatically).

```bash
uv sync                               # create .venv and install dependencies
uv run playwright install chromium    # headless browser for crawling
cp .env.example .env                  # then fill in your API keys
```

Run things with `uv run`, e.g. `uv run streamlit run worker/app.py`.
Add a dependency with `uv add <package>`.
