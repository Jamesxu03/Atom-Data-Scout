# Atom Data Scout

Internal tool: given a buyer's data brief, find companies likely to hold that data (as a
by-product of their business, or as data vendors and public sources such as corpora and
parliament recordings), rank them by fit, check the promising ones in depth and draft
outreach emails.

## Workflow

```
Initial brief → Refine (form with options) → Final brief
  → Search: hypotheses → discovery (5 channels) → source checks → screen → one list ranked by fit
  → You tick promising rows → Research selected: licence evidence, volume, contacts
  → Draft an outreach email per company
```

1. **Brief.** Paste the buyer's text and click *Refine brief*. Gemini fills in the standard
   template (data type, features, scenarios, languages, regions, labels, volume, formats,
   licence, accepted sources, exclusions, must-haves). Fields it had to guess are marked
   *suggested* and never become must-haves. Open questions to check with the buyer are
   shown above the form. Edit anything, then click *Save and search*.
2. **Search.** Runs in the background. Each company's homepage (and the page it was found on)
   is screened for holder type, data owner, how well the data matches (1 to 5), evidence for the
   brief's languages and regions, and signs of scale.
3. **Ranking.** Fit is a weighted mean of four 1-to-5 scores (see `worker/scoring.py`):
   data match 40%, coverage (languages and regions) 25%, licence 20%, volume 15%. Unknown scores
   are left out and the weights rescaled. Fit is capped at 2.0 when a must-have language or region
   has no evidence, at 3.0 for technology companies, and at 2.0 when research finds the data is
   for internal use only. Before research, the licence score is a prior based on holder type.
4. **Research.** Tick rows in the table and click *Research selected*. For each company this
   deep-crawls the homepage, privacy policy and terms, then makes one Gemini + Google Search call
   for what data it holds, how much, and its licence status, and one call for contacts. Every
   quote must appear on the page it cites, or it is dropped (and a status left with no quote
   becomes *unknown*). The licence and volume scores are then replaced and the list is ranked
   again. The licence status is a screening flag from public pages, not legal advice.
5. **Email.** In each researched company's panel, pick a contact (verified ones first) and click
   *Draft email*. Edit the subject and body (edits are saved), then download the draft. Nothing is
   sent.

Every search is a **session** saved under `data/sessions/<time>-<name>/`
(`session.json`, `brief.json`, `hypotheses.json`, `discovery.json`). The sidebar reopens any
past session from disk without rerunning. *New session from this brief* opens the form
prefilled with the brief, so briefs can be reused as templates. Rerunning always creates a new
session, so old results are never overwritten.

Every Gemini call uses the model set in `worker/config.py`. Independent calls and page fetches
run in parallel, and each step's time is saved with the session.

## Layout

```
app.py                  Streamlit UI (searches and research run in a background thread)
worker/
  config.py             model, thinking level, concurrency, budgets, fit weights and caps
  llm.py                ask(): structured output, retries, optional Google Search
  sessions.py           create, list, save and load session folders
  pipeline.py           search() and research(); funnel and CSV exports
  scoring.py            coverage, licence prior and checked status, fit, ranking
  verify.py             is an email / phone / name / quote really on the page it cites?
  discovery/            product search, snowball, directories, data vendors, public sources;
                        dedupe by domain, skip exclusions
  steps/                brief, hypotheses, screen, research, contacts, email
  crawl.py              homepage / privacy / terms / contact pages (httpx, playwright fallback)
  parallel.py           thread pool that keeps finished results if the quota runs out
eval/
  seed_companies.csv    known-good companies (from past meeting-audio searches)
  discovery_recall.py   what share of them discovery found, via which channel, and their rank
tests/                  pytest; Gemini is faked and network is blocked
data/                   sessions and page cache (gitignored)
```

## Setup

Requires [uv](https://docs.astral.sh/uv/) (installs Python 3.12 automatically).

```bash
uv sync                               # create .venv and install dependencies
uv run playwright install chromium    # headless browser for JS-heavy sites
cp .env.example .env                  # then add your GEMINI_API_KEY
```

## Use

```bash
uv run streamlit run app.py           # the UI (every search makes real Gemini calls)
uv run pytest                         # tests, no API calls
RUN_LIVE=1 uv run pytest              # also run the live Gemini test (checks the API key)
uv run python -m eval.discovery_recall [session_dir]   # recall and rank of eval/seed_companies.csv
```

Add a dependency with `uv add <package>`.
