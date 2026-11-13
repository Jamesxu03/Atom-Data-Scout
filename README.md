# Atom Data Scout

Atom Data Scout finds companies that hold the data a buyer is looking for. Paste a buyer's brief
(for example "1,000 hours of multi-speaker English meeting audio with transcripts") and it
searches the web for likely holders, ranks them by fit, checks the best ones against their own
pages, and drafts an outreach email for each.

It is a local Streamlit app powered by Gemini with Google Search grounding. Everything it stores
stays on your machine, and it never sends an email for you.

## What it does

- **Understands the brief.** Gemini turns free text into a structured brief (data type, languages,
  regions, volume, licence, deal terms). You review it in a form and mark the must-haves.
- **Searches widely.** It looks beyond the obvious sellers: marketplaces and open corpora, but
  also companies that hold the data as a by-product, closed or distressed firms, customers of
  recording tools, tenders and public records, and local markets searched in their own language.
- **Separates two kinds of company.** *Ready sources* already sell or license the data.
  *Untapped holders* have it but do not sell it, so they are graded on saleability: does the data
  still exist, is selling it allowed, and is the holder willing.
- **Ranks by fit.** Each company gets a 1 to 5 fit score from data match, coverage, licence or
  saleability, and volume. A contradicted must-have caps the score.
- **Verifies its claims.** Every quote must appear on the page it cites, or it is dropped. The
  top candidates get a verdict: Confirmed, Possible, Unconfirmed or Rejected.
- **Researches and compares.** Deep research covers licence, volume, price and contacts. A
  Compare tab puts up to four companies side by side and re-scores them as you edit the brief.
- **Supports outreach.** It drafts an email tailored to each company and contact, then tracks
  each client's outreach on a drag and drop board (To contact, Emailed, Replied, and so on).

## How a search runs

```
Buyer's brief → Refine (form) → Final brief
  → Search: hypotheses → local markets → discovery rounds → screening → trigger checks
  → Two ranked lists (ready sources, untapped holders) → confirmation with quotes
  → Research the rows you tick → Compare → Draft emails → Outreach board
```

Searches run in the background, so the page stays usable while results fill in. Each search is
saved as a session folder and can be reopened from its URL without running again.

The full reference, including the scoring rules and what every step checks, is in
[docs/how-it-works.md](docs/how-it-works.md).

## Quick start

You need [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 for you) and a
[Gemini API key](https://aistudio.google.com/apikey) from a project with billing turned on.

```bash
git clone https://github.com/Jamesxu03/Atom-Data-Scout.git
cd Atom-Data-Scout
uv sync                               # create .venv and install dependencies
uv run playwright install chromium    # headless browser for JavaScript heavy sites
cp .env.example .env                  # then add your GEMINI_API_KEY
uv run streamlit run app.py           # open the app
```

Your key lives only in `.env`, which is ignored by git. Never commit it.

**Cost.** Every search makes real Gemini calls: at most 110 calls with Google Search, plus
screening and confirmation. Research and email drafts add a few calls per company. The limits
are in `worker/config.py`.

## Configuration

All settings are in `worker/config.py`:

| Setting | What it controls |
|---|---|
| `MODEL`, `STEP_MODELS`, `STEP_THINKING` | The Gemini model and thinking level for each step |
| `DISCOVERY`, `DISCOVERY_ROUNDS`, `MAX_GROUNDED_CALLS` | How many search calls each channel and round may make |
| `CONFIRM_TOP` | How many candidates per list are confirmed |
| `FIT_WEIGHTS` and the `*_CAP` values | How the fit score is weighted and capped |
| `PARALLEL_*` | How many Gemini calls, page fetches and crawls run at once |

## Project layout

```
app.py        Streamlit entry point: top bar, sidebar, page routing
views/        the two pages: Search and Companies
ui/           the brief form, results tables, Compare tab, outreach board, background jobs
worker/       the pipeline, with no Streamlit code
  pipeline.py     search() and research()
  discovery/      search channels, rounds, merging results by domain
  steps/          brief, hypotheses, screen, triggers, confirm, research, contacts, email
  scoring.py      fit, saleability grade, caps and ranking
  verify.py       checks that quotes, emails and names are really on the cited page
  crawl.py        page fetching with a cache and a headless browser fallback
  llm.py          one place to call Gemini: structured output, retries, quota handling
  crm.py          clients, boards and outreach activity (SQLite)
  sessions.py     saved searches on disk
eval/         recall and quality checks against known good companies and fixed briefs
tests/        pytest suite; Gemini is faked and the network is blocked
data/         sessions, crm.sqlite3 and the page cache (created at run time, ignored by git)
```

## Development

```bash
uv run pytest                 # about 235 tests, no API calls, a few seconds
uv run ruff check .           # lint
RUN_LIVE=1 uv run pytest      # also run the live Gemini test (uses your key)
```

Evaluation scripts measure how well discovery performs on fixed briefs:

```bash
uv run python -m eval.discovery_recall [session_dir]                 # recall of eval/seed_companies.csv
uv run python -m eval.settings A meeting_audio_en 2                  # live: two runs under setting A
uv run python -m eval.discovery_quality meeting_audio_en [--labels]  # report on those runs
```

Add a dependency with `uv add <package>`.

## Good to know

- **Results are leads, not facts.** Licence and saleability are screening flags read from public
  pages. They are not legal advice. Check them before you rely on them.
- **Nothing is sent.** Email drafts are downloaded and sent from your own mailbox. The buyer is
  never named in a draft, and no price or budget is stated.
- **Data stays local.** Sessions, the client database and cached pages are written to `data/`.
  Only prompts and page text go to the Gemini API.
- **Polite crawling.** Pages are fetched once and cached for a week. Only public web addresses
  are fetched.

## Tech stack

Python 3.12, Streamlit, Google Gemini (`google-genai`) with Google Search grounding, Pydantic,
httpx, trafilatura, Playwright, SQLite, pytest and ruff.

## Author

Built by [James Xu](https://github.com/Jamesxu03) at Atom Group.
