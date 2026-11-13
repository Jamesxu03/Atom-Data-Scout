# How Atom Data Scout works

The full reference for what each step does, how companies are scored and why. For setup and a
short overview, see the [README](../README.md).

## Two kinds of company

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
(e.g. "needs its customers' consent", "contact the administrators"). Initial answers are
guesses from a homepage, so they only count in the licence score; only verified ones can cap the
fit.

**Tool vendors** are untapped companies whose product is the recording tool (meeting recorders,
call-recording software, research platforms), so their customers do the recording and hold
the data. They are shown as *Untapped · tool vendor*, hidden unless *Tool vendors* is on, capped
at fit 2.0 and never used as snowball seeds: they lead to holders but aren't one.

**Big brands and regulated institutions** (banks, insurers, airlines, telcos) rarely sell recordings
in normal times. The screen flags them instead of scoring them "not willing", and their fit is
capped at 2.0 until a dated trigger is verified: distress (insolvency, a unit or call centre
for sale), a past data deal, or a law in their market that allows reuse. The trigger sets how
willing they are (5, 4 or 3); a law alone never makes a company confirmed. The *Trigger* column
says which, or why one is still capped.

**Two lists.** Ready sources and untapped holders are shown and ranked as two separate tables,
and are never compared with each other (in the Compare tab either).

**Confirmation.** At the end of a search, the best 20 untapped holders and the best 10 ready
sources are checked against their own pages with Google Search: does the data fit the
must-haves (modality, domain, languages and region must be shown, by a quote or by what the
screen already found; a volume target or, for untapped holders, permitted use never rejects a
company, since several sellers make up a volume and terms are negotiated), and can it sell it
(it owns the data and shows a selling signal, or a licence the brief accepts)? Every quote must
be on the page it cites. Verdicts: *Confirmed*, *Possible* (fits, saleability unclear),
*Unconfirmed* (fit not shown) or *Rejected* (a must-have contradicted, or a blocker such as "we
never share recordings"; the usual "we do not sell your personal information" is not one). Once
a search has verdicts, each list shows confirmed and possible companies first and only;
*Unchecked and rejected* shows the rest. The lists appear while confirmation runs and fill in
as verdicts arrive.

Companies unrelated to the brief are excluded rather than typed.

## Workflow in detail

```
Initial brief → Refine (form with options) → Final brief
  → Search: hypotheses → local markets and laws → discovery rounds (each: source checks, screen)
    → snowball and vendors' customers → trigger checks → two lists → confirmation
  → You tick promising rows → Research selected: licence or saleability, volume, contacts
  → Compare: search the researched companies, side by side; edit the brief, kill, save
  → Draft an outreach email per company
```

1. **Brief.** Paste the buyer's text and click *Refine brief*. Gemini fills in the brief
   template (`worker/brief_template.py`) in one call: the data (A), where (B), volume and timing
   (C), rights (D), where to look (E), deal terms (F), plus a few add-ons for the modality picked
   (audio, video/image, text, tabular). The form only asks what changes who is found or how they
   rank. Choices are enums, so the model can only pick valid ones. A category that is filled in
   is weighted into the score and an empty one is ignored; its **Must** switch also caps the fit
   when evidence contradicts it. Picks the model inferred rather than read are marked *inferred*
   and are never Must. **Deal terms** (F1) is one text box for what only research and the email
   use (personal data and consent, exclusivity, term, documents, where the data is processed,
   sample, ...); it never reaches the screen. Details of the data too fine for a category go in
   the **Notes**, which the screen does read. Open questions for the buyer sit above the form, and
   transfer rules (e.g. Saudi PDPL, China export) show when the origin calls for them. Edit
   anything, then click *Save and search*. Briefs saved with the older, longer template are
   upgraded when loaded: removed categories become a line in the notes or the deal terms.
2. **Search.** Runs in the background. Each company's homepage (and the page it was found on)
   is screened for holder type, data owner, how well the data matches (1 to 5), evidence for the
   brief's languages and regions, signs of scale, any must-have it clearly contradicts, and for
   untapped holders the initial saleability answers.
   It also reads any price, founding year and data period the pages state, kept only when the
   quote is on the page and contains the value's numbers (*Price*, *Data years* and *Founded*
   columns; *?* when not published).
   Only the channels for the source type picked (E1) run; must-have filters (E1, D3) drop
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
   are left out and the weights rescaled; empty categories count for nothing. The licence
   slot depends on the type:
   - ready source: 4 until research finds its licence, then 5 if D1 accepts it, otherwise 1
   - untapped holder: its saleability grade, 3 (*Medium*) while it is *Unknown*. Data its customers own scores at most 2 on
     *allowed* (it needs their consent), 1 if D3 excludes it, and as found if D3 is *check
     later*. With only one answer known
     the grade is at most 3.

   Fit is capped at 2.0 when the evidence contradicts a must-have (the *Must-haves not met*
   column says which), at 2.0 for tool vendors, and at 2.0 when research finds a ready source's
   data is for internal use only. An untapped holder is capped at 2.0 (or 3.0) only on evidence:
   research verifies a 1 (or 2) on *exists* or *allowed*, the screen sees it keeps no data at
   all, or D3 excludes data its customers own. *Willing* and the consent customers' data needs
   never cap; they lower the licence score.
   Collectors are capped when C3 *Data recorded to order* is a must-have *Existing data only*;
   *Welcome* also searches for collectors. The table shows
   the *Saleability* column and *Obstacle → how to buy*, and filters by minimum fit, type,
   languages (evidence for at least one picked) and minimum volume score; companies with
   unknown volume stay unless *Known volume* is on. Tool vendors appear only with *Tool vendors* on.
4. **Research.** Tick rows in the table and click *Research selected*. For each company this
   deep-crawls the homepage, privacy policy and terms, then makes one Gemini + Google Search call
   for what data it holds, how much, its price, founding year and data period, and a check of
   each research (V) requirement: recency, permitted use and the deal terms (with the transfer
   rules that may apply to the origin). Then, by type:
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
5. **Compare.** A session has three tabs, *Candidates* (the list above and the research panels),
   *Outreach* (step 7) and *Compare*, which works on the researched companies only:
   - **Search and pick.** Search by name, type, region, language or what research found they
     hold, and pick up to four (*Top 4* picks the best fits).
   - **Side by side.** One column per company: fit, why it is provisional, fit change and rank
     change against the original search, type, saleability, licence found or the three
     saleability answers, each research check, must-haves not met, languages, regions,
     verified contacts and email. Rows where the companies differ are highlighted, and *Only
     differences* hides the rest. Free text (data held, volume found, price, data years,
     findings, obstacle) and the quotes with their links sit under *Text and sources*.
     *Details* opens a company's full research.
   - **Working brief.** On the left: edit, remove or add requirements, and the comparison
     re-scores as you go. **Kill** a company (with a reason) from its column; killed ones are
     listed below with *Restore*, and *All researched* shows every researched company.

   Everything in the tab is a preview: it is re-scored in Python only (`worker/compare.py`), with
   no Gemini call, and nothing is written. A banner shows unsaved changes; *Save* keeps the
   working brief and kills as a new session (opened on its *Compare* tab, with what changed from
   the original), *Reset* goes back. The session being compared is never changed. Killed
   companies are ranked last and not counted as relevant; in the candidates list they show
   with *Show excluded*.

   Which changes re-score exactly:

   | Change | Re-scored |
   |---|---|
   | D1 licence, D3 customers' data, C3 recorded to order, E1 source types (as a must-have it caps rather than hides) | exactly: research stored the label or flag, and the rule is in Python |
   | E2 exclusions | exactly: matching companies are killed, and lifted when the exclusion is removed |
   | Must turned off, a category removed | exactly: it no longer counts |
   | Languages or regions removed | exactly: the rest keep their evidence |
   | Must turned on for C1 volume, C2 recency, D2 permitted use or F1 deal terms | exactly: research checks them whatever their importance |
   | What data, notes, A1 to A3 and the modality add-ons | provisional: data match was screened against the old ones |
   | C1 volume target | provisional: the volume score becomes unknown |
   | C2, D2 or F1 values | provisional: their research checks become *unclear* |
   | Languages or regions added | provisional: they weren't looked for |
   | Must turned on for a category only the screen judges | provisional: the screen only checked the must-haves it had |

   A provisional fit is greyed and marked `*`, with the reason in the row under it, for the
   companies it affects only. In the saved session the reasons stay; tick the companies under
   *Candidates* and click *Research selected* for exact scores.
6. **Email.** In each researched company's panel, pick a contact (verified ones first) and click
   *Draft email*. The email asks about the deal terms that matter most (D2 permitted use, F1
   deal terms) and never states a price or the budget. The question depends on the type:
   - ready sources are asked about availability and price, or the licence terms if the data
     is published
   - collectors about recording to spec and who owns the result
   - untapped holders are asked whether they would license data they hold, with a brief word
     on why it has value; a direct question about terms if they score 4 or more on *willing*;
     and the way to buy it, if one was found

   Edit the subject and body (edits are saved), then download the draft. Nothing is sent: send
   it from your own mailbox and click *Mark as sent*.
7. **Outreach.** A search's *Outreach* tab is a board of its researched companies: *To contact,
   Emailed, Replied, Call scheduled, Negotiating, Won, Dropped*. Drag a company to another stage,
   or click it for its contacts, the email, a *Log* form (an email sent elsewhere, a reply, a
   call or a note, with its date and outcome) and the timeline. Marking an email sent moves the
   company to *Emailed*, a reply to *Replied*, a call to *Call scheduled*; never back. A card
   turns amber when it was emailed a week ago with no reply. *Outreach so far* lists who was
   emailed, when, and who replied, as a table and a CSV. The candidate tables gain an
   *Outreach* column once anything is logged.

## Clients, sessions and storage

**Clients.** Pick the client a search is for, the buyer (*Client* on the form, or above a
search's results); a search without one has no outreach board. The client is never named in an
email. *Companies* in the top bar lists the
clients with what their outreach has come to. A client's page has its metrics (contacted,
replied, reply rate, calls), the latest activity and a **board of its searches**: *Sourcing,
Outreach, In talks, Won, Dropped*. A search moves to *Outreach* when an email is sent and to *In
talks* when someone replies, until you drag it; click one to open it. Outreach belongs to the
client, so a company another search for the same client finds again keeps its history.
Searches made before clients existed are under *Searches without a client* on the Companies page.

Every search (and every re-comparison) is a **session** saved under `data/sessions/<time>-<name>/`
(`session.json`, `brief.json`, `hypotheses.json`, `discovery.json`). Which one is open is in the
URL (`?s=<folder>`), so a refresh or a pasted link reopens it from disk without rerunning, as do
the client's board and the sidebar's recent searches. *New search from this brief* opens the
form prefilled with the brief, so briefs can be reused as templates. Rerunning always creates a
new session, so old results are never overwritten. Clients, the boards and outreach are in
`data/crm.sqlite3` (`worker/crm.py`), apart from the session files a running search rewrites.

## How discovery searches

The code is in `worker/discovery/`. Hypotheses are written from the holder's side:
at least four are organisations that record and keep the data, regulated institutions are
searched through what would make them sell (the distressed channel), and tool vendors come last,
as a way to their customers. The suppliers channel makes one call per named tool (Gong, Verint,
NICE...) on its customer stories, and later on the vendors the screen found, dropping the tool
itself. When the brief names languages or regions, up to three local markets (country and
language, `worker/markets.py`) get their own queries written natively, their tender portal,
and a check of recent laws on reusing the data. After round 1 is screened, up to two more
rounds rerun only the channels that found new qualifying companies (fit, every must-have met,
not a vendor), each side keeping its own budget, told every company already found and the
queries already tried, with the best holders as examples; a round adding fewer than three stops
the search. A search makes at most 110 grounded calls; under pressure round 3 goes first, then
local markets. Thin homepages are rendered in a browser before screening, and quotes and names
in Chinese, Japanese, Thai and other scripts without spaces are matched properly.

Every Gemini call names its step, and `worker/config.py` sets the model and thinking level per
step (`STEP_MODELS`, `STEP_THINKING`). Independent calls and page fetches
run in parallel, and each step's time is saved with the session.

## Project layout

```
app.py                  Streamlit entry point: the top bar (Search, Companies) and the sidebar
views/                  the two pages: search.py (a new brief, or a past search by ?s=) and
                        companies.py (the clients, or one client's page by ?client=)
ui/
  brief_form.py         the brief text and the form
  session_view.py       a search: funnel, candidate lists, researched companies, tabs
  company_view.py       one researched company: research, contacts, email
  outreach.py           the Outreach tab: the board, a company's dialog, the outreach table
  compare_view.py       the Compare tab
  client_view.py        the Companies page and a client's page with its board of searches
  board.py              the drag-and-drop board (an inline st.components.v2 component)
  clients.py            the client picker; nav.py: URL routing and the sidebar
  jobs.py               searches and research in a background thread
  labels.py             labels and short texts shared by every view
worker/
  config.py             model, thinking level, concurrency, budgets, fit weights and caps
  brief_template.py     the brief template as data; the Brief model and Gemini fill schemas
  archetypes.py         why untapped holders have data, and the tools whose customers hold it
  markets.py            the countries and languages a brief names, to search natively
  llm.py                ask(): structured output, retries, optional Google Search
  sessions.py           create, list, save and load session folders
  crm.py                clients, searches on their boards, deals and outreach activity (SQLite)
  pipeline.py           search() and research(); funnel and CSV exports
  compare.py            preview researched companies under a working brief and kills; save
  scoring.py            coverage, licence and saleability grade, caps, fit, ranking
  verify.py             is an email / phone / name / quote really on the page it cites?
  discovery/            channels.py: the search prompts; rounds.py: what each round searches and
                        whether another is worth it; core.py: hits, merge by domain, run calls
  steps/                brief, hypotheses, screen, triggers, confirm, research, contacts, email
  crawl.py              homepage / privacy / terms / contact pages (httpx, playwright fallback)
  parallel.py           thread pool that keeps finished results if the quota runs out
eval/
  seed_companies.csv    known-good companies and their expected type: ready, untapped (a holder)
                        or vendor (from past meeting-audio searches)
  discovery_recall.py   what share of them discovery found, via which channel, their rank,
                        whether the screen gave them the expected type, the share of tool vendors
                        among untapped companies, and how many holders reach the top 25
  gold_finds.csv        companies a person judged good (fit and sellable), mostly ones found by
                        hand that the tool missed: to fill in before tuning
  briefs/               three fixed eval briefs: English meeting audio, Gulf Arabic, meeting video
  discovery_quality.py  recall of the gold rows, confirmed good finds, precision (from labels),
                        organisation and local share of each list's top 20, calls and minutes
  settings.py           runs a brief under model and thinking settings A to D (live, uses quota)
tests/                  pytest; Gemini is faked and network is blocked
data/                   sessions, crm.sqlite3 and page cache (gitignored)
```
