# Atom Data Scout

Internal tool: given a buyer's data brief, find companies likely to hold that data, rank them
by fit, check the promising ones in depth and draft outreach emails.

It tells apart two kinds of company:

| Type | What it means | Judged by |
|---|---|---|
| **Ready source** | The data is already offered: sold or licensed, listed on a marketplace, published by a public body or repository, or recorded to order by a collector (shown as *Ready source · records to order*). | Its licence, against D1 |
| **Untapped** | Holds the data but doesn't sell it: a service by-product, records kept by law, production leftovers, a closed company's archive, a device fleet, and so on. Includes services that record for clients (focus-group facilities, court reporters, earnings-call hosts). | Saleability |

**Saleability** answers three questions, each 1 to 5: does the data still **exist**, is selling
it **allowed**, and is the holder **willing**. The grade is the lowest answer, because all three
must hold: *High* (4 to 5), *Medium* (3), *Low* (2) or *Not sellable* (1). The screen gives
*initial* answers from the homepage; research checks them against the news, public records and
the company's own policies, and an answer becomes *verified* once a quote is found on the page
it cites. Each untapped holder also gets the main obstacle and how it could still be bought
(e.g. "needs its customers' consent", "contact the administrators").

**Tool vendors** are untapped companies whose product is the recording tool (meeting recorders,
call-recording software, research platforms), so their customers do the recording and hold
the data. They are shown as *Untapped · tool vendor*, hidden unless *Tool vendors* is on, capped
at fit 2.0 and never used as snowball seeds: they lead to holders but aren't one.

Companies unrelated to the brief are excluded rather than typed.

## Workflow

```
Initial brief → Refine (form with options) → Final brief
  → Search: hypotheses → discovery (9 channels) → source checks → screen → one list ranked by fit
  → You tick promising rows → Research selected: licence or saleability, volume, contacts
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
   brief's languages and regions, signs of scale, any must-have it clearly contradicts, and for
   untapped holders the initial saleability answers.
   It also reads any price, founding year and data period the pages state, kept only when the
   quote is on the page and contains the value's numbers (*Price*, *Data years* and *Founded*
   columns; *?* when not published).
   Only the channels for the source type picked (E1) run; must-have filters (E1, D6) drop
   companies of the other type, and exclusions (E2) are skipped. The channels are:
   - ready sources: marketplaces, collection services and published datasets; open corpora
     and public bodies
   - untapped holders: product search and software directories; distressed, closed or
     for-sale companies; what their own pages give away (privacy policies, data-safety labels,
     job ads); the customer lists of the tools they run on; and public records (tenders,
     finished research projects, registers)

   Hypotheses and the signals channel use ten archetypes of why a company holds data it
   doesn't sell (`worker/archetypes.py`) as an internal checklist, so searches go beyond the
   obvious by-product holders. Archetypes are not shown or stored per company.
3. **Ranking.** Fit is a weighted mean of four 1-to-5 scores (see `worker/scoring.py`):
   data match 40%, coverage (languages and regions) 25%, licence 20%, volume 15%. Unknown scores
   are left out and the weights rescaled; categories set to Any count for nothing. The licence
   slot depends on the type:
   - ready source: 4 until research finds its licence, then 5 if D1 accepts it, otherwise 1
   - untapped holder: its saleability grade. Data its customers own scores at most 2 on
     *allowed* (it needs their consent), 1 if D6 excludes it, and as found if D6 is *check
     later*. With only one answer known
     the grade is at most 3.

   Fit is capped at 2.0 when the evidence contradicts a must-have (the *Must-haves not met*
   column says which), at 2.0 for an untapped holder graded *Not sellable* and 3.0 for one
   graded *Low*, at 2.0 for tool vendors, and at 2.0 when research finds a ready source's data is
   for internal use only.
   Collectors are capped when C4 is a must-have without custom collection. The table shows
   the *Saleability* column and *Obstacle → how to buy*, and filters by minimum fit, type,
   languages (evidence for at least one picked) and minimum volume score; companies with
   unknown volume stay unless *Known volume* is on. Tool vendors appear only with *Tool vendors* on.
4. **Research.** Tick rows in the table and click *Research selected*. For each company this
   deep-crawls the homepage, privacy policy and terms, then makes one Gemini + Google Search call
   for what data it holds, how much, its price, founding year and data period, and a check of
   each research (V) requirement: quality evidence, demographics, recency, permitted use,
   personal data, documents, processing location. Then, by type:
   - ready sources: the licence, in the same labels as D1 plus *Internal use only*; for
     collectors, who owns commissioned data
   - untapped holders: the three saleability questions, with the main obstacle and how to buy
     it anyway. Besides the company's own policies, it searches the news and public records by
     name ("<company> lawsuit", "... insolvency", "... acquired", "... AI training data
     partnership" and so on) for insolvency and shutdown notices, layoffs, acquisitions, data
     deals with AI companies, fines and lawsuits, and retention and ownership terms. It prefers
     recent sources (the last three years) and two or more independent ones, and cites at least
     one news article for *willing* when there is one. Search results are cited by their
     grounding link, which is resolved to the article, so a made-up URL loses its quote. News
     pages that need a browser are fetched with one.

   One more call finds contacts.
   Every quote must appear on the page it cites, or it is dropped: a licence or check left with
   no quote becomes *Unclear*, and a saleability answer with no quote falls back to the screen's
   initial one. The scores are then recomputed and the list is ranked again. Licence and
   saleability are screening flags from public pages, not legal advice.
5. **Email.** In each researched company's panel, pick a contact (verified ones first) and click
   *Draft email*. The email asks about the deal terms that matter most (D2 to D4, D7, F2 to F4)
   and never states a price or the budget. The question depends on the type:
   - ready sources are asked about availability and price, or the licence terms if the data
     is published
   - collectors about recording to spec and who owns the result
   - untapped holders are asked whether they would license data they hold, with a brief word
     on why it has value; a direct question about terms if they score 4 or more on *willing*;
     and the way to buy it, if one was found

   Edit the subject and body (edits are saved), then download the draft. Nothing is sent.

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
  archetypes.py         why untapped holders have data: an internal checklist for search
  llm.py                ask(): structured output, retries, optional Google Search
  sessions.py           create, list, save and load session folders
  pipeline.py           search() and research(); funnel and CSV exports
  scoring.py            coverage, licence and saleability grade, caps, fit, ranking
  verify.py             is an email / phone / name / quote really on the page it cites?
  discovery/            the nine search channels and snowball; dedupe by domain, skip exclusions
  steps/                brief, hypotheses, screen, research, contacts, email
  crawl.py              homepage / privacy / terms / contact pages (httpx, playwright fallback)
  parallel.py           thread pool that keeps finished results if the quota runs out
eval/
  seed_companies.csv    known-good companies and their expected type: ready, untapped (a holder)
                        or vendor (from past meeting-audio searches)
  discovery_recall.py   what share of them discovery found, via which channel, their rank,
                        whether the screen gave them the expected type, the share of tool vendors
                        among untapped companies, and how many holders reach the top 25
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
