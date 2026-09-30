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

1. **Brief.** Paste the buyer's text and click *Refine brief*. Gemini fills in the brief
   template (`worker/brief_template.py`): the data (A), where and who (B), volume and delivery (C),
   rights and compliance (D), where to look (E), commercial (F), plus add-ons for the modality
   picked (audio, video/image, text, tabular). Choices are enums, so the model can only pick
   valid ones. Each category has an importance: **Must have** (the fit is capped when evidence
   contradicts it), **Nice to have** (weighted into the score) or **Any** (ignored). Picks the
   model inferred rather than read are marked *inferred* and are never Must have. Open questions
   for the buyer sit above the form, and transfer rules (e.g. Saudi PDPL, China export) show
   when the origin and processing location call for them. Edit anything, then click
   *Save and search*.
2. **Search.** Runs in the background. Each company's homepage (and the page it was found on)
   is screened for holder type, data owner, how well the data matches (1 to 5), evidence for the
   brief's languages and regions, signs of scale, and any must-have it clearly contradicts.
   Only the channels for the source types picked (E1) run; must-have filters (E1, D6) drop
   companies of other types, and exclusions (E2) are skipped.
3. **Ranking.** Fit is a weighted mean of four 1-to-5 scores (see `worker/scoring.py`):
   data match 40%, coverage (languages and regions) 25%, licence 20%, volume 15%. Unknown scores
   are left out and the weights rescaled; categories set to Any count for nothing. Fit is capped
   at 2.0 when the evidence contradicts a must-have (the *Must-haves not met* column says which),
   at 3.0 for technology companies, and at 2.0 when research finds the data is for internal use
   only. Before research, the licence score is a prior based on holder type and D6.
4. **Research.** Tick rows in the table and click *Research selected*. For each company this
   deep-crawls the homepage, privacy policy and terms, then makes one Gemini + Google Search call
   for what data it holds, how much, its licence (in the same labels as D1, plus *Internal use
   only*), and a check of each research (V) requirement: quality evidence, demographics, recency,
   permitted use, personal data, documents, processing location. One more call finds contacts.
   Every quote must appear on the page it cites, or it is dropped (and a licence or check left
   with no quote becomes *Unclear*). The licence and volume scores are then replaced and the list is ranked
   again. The licence status is a screening flag from public pages, not legal advice.
5. **Email.** In each researched company's panel, pick a contact (verified ones first) and click
   *Draft email*. The email asks about the deal terms that matter most (D2 to D4, D7, F2 to F4)
   and never states a price or the budget. Edit the subject and body (edits are saved), then download the draft. Nothing is
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
  brief_template.py     the brief template as data; the Brief model and Gemini fill schemas
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
