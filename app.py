"""Streamlit UI. Run: uv run streamlit run app.py

Brief -> Refine (form) -> Save and search -> two lists, untapped holders and ready sources, each
ranked by verdict and fit (shown while the best are confirmed) -> tick rows -> Research selected
-> Compare tab: search the researched companies of one type, side by side, under a working brief
and kills, saved as a new session -> draft an email per company. Each search is a session folder (worker/sessions.py),
and every page reads it from disk, so any past session reopens without rerunning and a browser
refresh loses nothing.

Searches and research run in a background thread, so touching the page doesn't restart or
interrupt them; a fragment polls for progress once a second, and reruns the page when a search
that is confirming saves new verdicts.
"""

import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from worker import compare, pipeline, sessions
from worker.brief_template import (
    CATEGORIES,
    CODE_LABELS,
    SECTIONS,
    Brief,
    BriefDraft,
    Category,
    Pick,
    suggested_languages,
    transfer_flags,
)
from worker.config import RELEVANT_FIT
from worker.schemas import Candidate, CheckedCategory, Confirmation, Contact, Evidence
from worker.scoring import answers, is_verified, must_failures, saleability_label, split, verdict
from worker.steps import brief as brief_step
from worker.steps import email as email_step

EXAMPLE_BRIEF = (
    "An AI meeting-transcription company wants to buy multi-speaker English meeting audio "
    "with transcripts and speaker labels, at least 1,000 hours, licensed for model training."
)
LOGO = "assets/atom_logo.png"
ICON = "assets/atom_icon.png"  # square: a wide logo gets squeezed into the browser tab's favicon
HOLDER_LABELS = {"ready": "Ready source", "untapped": "Untapped", None: "Not screened"}
LIST_TITLES = {"untapped": "Untapped holders", "ready": "Ready sources", None: "Excluded and not screened"}
VERDICT_LABELS = {"confirmed": "✅ Confirmed", "possible": "🟡 Possible", "unconfirmed": "❔ Unconfirmed",
                  "rejected": "❌ Rejected", None: ""}
TRIGGER_LABELS = {"distress": "Distress", "precedent": "Past data deal", "law": "Law"}
QUESTION_LABELS = {"exists": "Exists?", "allowed": "Allowed?", "willing": "Willing?"}
VERDICT_ICONS = {"meets": "✅", "fails": "❌", "unclear": "❔"}
LEGAL_NOTE = "Licence status is a screening flag from public pages, not legal advice."
FACT_LABELS = {"price": "Price", "data_period": "Data years", "founded": "Founded"}
FACT_HELP = "Only when a page states it; the quote is checked on the page. Research looks again with Google Search."


# --- background jobs --------------------------------------------------------------------------

@dataclass
class Job:
    kind: str  # "search" or "research"
    session_path: str
    messages: list[str] = field(default_factory=list)
    error: str | None = None
    done: bool = False
    started: float = field(default_factory=time.time)
    finished: float | None = None
    version: int = 0  # bumped each time the job saves the session

    def bump(self) -> None:
        self.version += 1

    @property
    def elapsed(self) -> int:
        return int((self.finished or time.time()) - self.started)


def current_job() -> Job | None:
    return st.session_state.get("job")


def busy() -> bool:
    job = current_job()
    return job is not None and not job.done


def start_job(kind: str, path: str, work: Callable[[Job], None]) -> None:
    """Run work(job) in a background thread; it reports through job.messages and job.bump."""
    if busy():
        return
    job = Job(kind=kind, session_path=path)

    def run():
        try:
            work(job)
        except Exception as e:
            job.error = f"{type(e).__name__}: {e}"
            s = sessions.load(path)  # keep what was saved, and say why it stopped
            if kind == "search":
                s.meta.stage = "failed"
            s.meta.errors.append(f"{kind} failed: {job.error[:300]}")
            sessions.save(s)
        finally:
            job.finished = time.time()
            job.done = True

    threading.Thread(target=run, daemon=True).start()
    st.session_state.job = job


def start_search(path: str) -> None:
    start_job("search", path,
              lambda job: pipeline.search(sessions.load(path), progress=job.messages.append, on_update=job.bump))


def start_research(path: str, domains: list[str]) -> None:
    start_job("research", path,
              lambda job: pipeline.research(sessions.load(path), domains, progress=job.messages.append))


# --- navigation -------------------------------------------------------------------------------

def open_session(path: str) -> None:
    st.session_state.session_path = path
    st.session_state.draft = None


def new_session() -> None:
    st.session_state.session_path = None
    st.session_state.draft = None


def new_from_brief(path: str) -> None:
    """Open the form prefilled with this session's brief; saving creates a new session."""
    s = sessions.load(path)
    set_draft(BriefDraft(brief=s.brief), s.meta.brief_text)
    st.session_state.session_path = None
    st.session_state.session_name = s.meta.name


def set_draft(draft: BriefDraft, text: str) -> None:
    st.session_state.draft = draft
    st.session_state.brief_copy = text
    st.session_state.form_id = st.session_state.get("form_id", 0) + 1  # fresh form widgets


def sidebar() -> None:
    with st.sidebar:
        st.button("New session", on_click=new_session, type="primary", width="stretch")
        path = st.session_state.get("session_path")
        if path:
            st.button("New session from this brief", on_click=new_from_brief, args=(path,), width="stretch",
                      help="Opens the form with this brief filled in. Old results are never overwritten.")
        if busy():
            job = current_job()
            st.caption(f"Running {job.kind} for {Path(job.session_path).name} ({job.elapsed}s)")
        all_sessions = sessions.list_all()
        if all_sessions:
            st.subheader("Past sessions")
        for p, meta in all_sessions:
            st.button(
                f"**{meta.name}**  \n{meta.created:%d %b %Y, %H:%M} · {meta.relevant} relevant"
                + (" · compare" if meta.parent else ""),
                key=f"open-{p.name}", on_click=open_session, args=(str(p),), width="stretch",
                type="secondary" if str(p) != path else "primary",
            )


# --- brief and form ---------------------------------------------------------------------------

def brief_page() -> None:
    text = st.text_area("Data brief", st.session_state.get("brief_copy", EXAMPLE_BRIEF), height=180,
                        key="brief_input")
    if st.button("Refine brief", type="primary", disabled=not text.strip()):
        with st.spinner("Filling in the brief…"):
            try:
                draft = brief_step.draft_brief(text)
            except Exception as e:
                st.error(f"Couldn't refine the brief — {type(e).__name__}: {e}")
                return
        set_draft(draft, text)
        st.session_state.session_name = draft.brief.description
        st.rerun()


def _help(cat: Category) -> str:
    used = ", ".join(CODE_LABELS[c] for c in cat.used_for)
    return f"{cat.code} · used for {used}" + (f". {cat.help}" if cat.help else "")


def category_input(cat: Category, b: Brief, fid: int, picks: dict[str, Pick]) -> Pick:
    """One category: its choices (pills, a multiselect, a number and unit, years or a text box)
    and a Must switch. Picks the model inferred carry an "inferred" caption until changed."""
    old, key = b.pick(cat.key), f"{fid}-{cat.key}"
    value_col, importance_col = st.columns([4, 1], vertical_alignment="bottom")
    amount = amount_to = unit = None
    values: list[str] = []
    with value_col:
        if cat.pick == "amount":
            a, u = st.columns([2, 1])
            amount = a.number_input(cat.label, min_value=0.0, value=old.amount, step=1000.0, format="%.0f",
                                    key=f"{key}-amount", help=_help(cat))
            units = list(cat.choices)
            unit = u.selectbox("Unit", units, index=units.index(old.unit) if old.unit in units else 0,
                               key=f"{key}-unit")
        elif cat.pick == "years":
            a, t = st.columns(2)
            amount = a.number_input(f"{cat.label}: from", 1900, 2100, value=int(old.amount) if old.amount else None,
                                    key=f"{key}-from", help=_help(cat))
            amount_to = t.number_input("to", 1900, 2100, value=int(old.amount_to) if old.amount_to else None,
                                       key=f"{key}-to")
        elif cat.pick == "paragraph":
            text = st.text_area(cat.label, old.values[0] if old.values else "", key=key, help=_help(cat),
                                height=100, placeholder="e.g. identifiable voices only with documented consent; "
                                                        "sample before purchase; processed in the UK")
            values = [text.strip()] if text.strip() else []
        elif cat.free_text or cat.pick == "text":
            options = list(cat.choices)
            if cat.key == "languages":  # suggestions follow the regions picked above
                options += suggested_languages(picks["region"].values if "region" in picks else [])
            values = st.multiselect(cat.label, list(dict.fromkeys(options + old.values)), default=old.values,
                                    accept_new_options=True, key=key, help=_help(cat),
                                    placeholder="Pick or type your own")
        else:
            mode = "single" if cat.pick == "single" else "multi"
            default = [v for v in old.values if v in cat.choices]
            chosen = st.pills(cat.label, list(cat.choices), selection_mode=mode,
                              default=(default[0] if default else None) if mode == "single" else default,
                              key=key, help=_help(cat))
            values = [chosen] if isinstance(chosen, str) else list(chosen or [])
    with importance_col:
        must = st.toggle("Must", value=old.importance == "must", key=f"{key}-must",
                         help="Caps the fit when evidence contradicts it")
    importance = "must" if must else "nice"
    new = Pick(values=values, amount=amount, amount_to=amount_to, unit=unit if amount is not None else None,
               importance=importance)
    same = (new.values, new.amount, new.amount_to, new.importance) == (old.values, old.amount, old.amount_to,
                                                                      old.importance)
    if cat.key in b.picks and old.source == "inferred" and same:
        new.source = "inferred"
        st.caption("inferred — not stated in the brief")
    return new


def form_page(draft: BriefDraft) -> None:
    b, fid = draft.brief, st.session_state.get("form_id", 0)
    if st.button("← Back to the brief text"):
        st.session_state.draft = None
        st.rerun()
    if draft.questions:
        st.info("**Check with the buyer**\n" + "\n".join(f"- {q}" for q in draft.questions))
    st.caption("Whatever is filled in is weighted into the score; leave a category empty to ignore it. "
               "**Must** also caps the fit when evidence contradicts it. Unknown evidence stays neutral.")

    name = st.text_input("Session name", st.session_state.get("session_name") or b.description, key=f"{fid}-name")
    description = st.text_input("What data, in a few words", b.description, key=f"{fid}-description",
                                help="Used in every search, e.g. multi-speaker meeting audio")
    picks: dict[str, Pick] = {}
    for section in SECTIONS:
        if section.modalities and not set(picks["modality"].values) & set(section.modalities):
            continue  # add-ons appear once their modality is ticked
        title = section.title if section.modalities else f"{section.code}. {section.title}"
        with st.expander(title, expanded=True):
            for cat in section.categories:
                picks[cat.key] = category_input(cat, b, fid, picks)
            if section.code == "B":
                for flag in transfer_flags(picks["region"].values):
                    st.warning(flag)
    notes = st.text_area("Notes", b.notes, key=f"{fid}-notes")

    if not st.button("Save and search", type="primary"):
        return
    if not description.strip():
        st.error("Say what data, in a few words.")
        return
    brief = Brief(description=description.strip(), notes=notes,
                  picks={k: p for k, p in picks.items() if p.values or p.amount is not None or p.amount_to is not None})
    s = sessions.create(name.strip() or brief.description, brief, st.session_state.get("brief_copy", ""))
    open_session(str(s.path))
    start_search(str(s.path))
    st.rerun()


# --- results ----------------------------------------------------------------------------------

def fmt(x: float | None) -> str:
    return "?" if x is None else f"{x:.1f}" if isinstance(x, float) else str(x)


def fact_text(c: Candidate, key: str) -> str:
    """The stated value, with the company's age added to a founding year."""
    if not (f := c.fact(key)):
        return "?"
    if key == "founded" and (year := re.search(r"\b(1[89]\d\d|20\d\d)\b", f.value)):
        return f"{year[1]} ({date.today().year - int(year[1])} years)"
    return f.value


def type_label(c: Candidate) -> str:
    if c.excluded and c.holder_type is None:
        return "Excluded"
    return HOLDER_LABELS[c.holder_type] + (" · records to order" if c.collector else " · tool vendor" if c.vendor else "")


def obstacle_text(c: Candidate) -> str:
    """The main obstacle, and how to buy it anyway, for untapped holders."""
    s = c.current_saleability()
    if not s or not (s.obstacle or s.path):
        return ""
    return " → ".join(x for x in (s.obstacle, s.path) if x)


def progress_panel(path: str) -> None:
    job = current_job()
    if job is None or job.session_path != path:
        return
    if not job.done:
        with st.status(f"Running {job.kind}… {job.elapsed}s", expanded=True):
            for m in job.messages[-12:]:
                st.write(m)
        # the list shows while the best companies are confirmed: rerun when a verdict is saved
        seen = st.session_state.get("seen_version")
        if job.kind == "search" and job.version != seen and (meta := sessions.load_meta(path)) \
                and meta.stage == "confirming":
            st.session_state.seen_version = job.version
            st.rerun()
        return
    if st.session_state.get("rendered_job") is not job:
        st.session_state.rendered_job = job
        st.rerun()  # full rerun so the results pick up what the job saved
    if job.error:
        st.error(f"{job.kind.capitalize()} failed after {job.elapsed}s — {job.error}")
    else:
        with st.status(f"{job.kind.capitalize()} done in {job.elapsed}s", state="complete", expanded=False):
            for m in job.messages:
                st.write(m)


def session_page(path: str) -> None:
    s = sessions.load(path)
    job = current_job()
    job_here = job is not None and not job.done and job.session_path == path
    locked = job_here  # a running job will save over edits made meanwhile

    st.subheader(s.meta.name)
    st.caption(f"Created {s.meta.created:%d %b %Y, %H:%M}")
    st.info(s.brief.summary())
    for flag in transfer_flags(s.brief.regions):
        st.warning(flag)
    st.fragment(progress_panel, run_every=1 if job_here else None)(path)

    if job_here and job.kind == "search":
        if s.meta.stage != "confirming":
            st.caption("Searching — results appear here when done.")
            return
        st.info("The lists are ready. The best companies of each are being confirmed: verdicts appear as they "
                "arrive, and the default view fills in with confirmed and possible ones.")
    if s.meta.stage == "searching" and not job_here:
        st.warning("This search didn't finish (the app stopped). Showing what was saved.")
    elif s.meta.stage == "partial":
        st.warning("The Gemini quota ran out part-way, so these results are incomplete.")
    elif s.meta.stage == "failed":
        st.error("The search failed part-way (see problems below). Showing what was saved.")
    errors = s.discovery.errors + s.meta.errors
    if errors:
        with st.expander(f"{len(errors)} problems"):
            for e in errors:
                st.text(e)
    if s.meta.timings:
        with st.expander("Timings"):
            st.dataframe(pd.DataFrame(s.meta.timings.items(), columns=["Step", "Seconds"]), hide_index=True)

    f = pipeline.funnel(s.discovery)
    if s.meta.parent:  # a compare session holds researched companies only
        lineage(s)
        cols = st.columns(4)
        cols[0].metric("Compared", f.researched)
        cols[1].metric("Relevant", f.relevant, help=f"Fit of at least {RELEVANT_FIT}, not killed")
        cols[2].metric("Killed", sum(c.killed for c in s.discovery.candidates))
        cols[3].metric("With contacts", f.with_contacts, help="Researched companies with a verified contact")
    else:
        cols = st.columns(8)
        cols[0].metric("Found", f.found, help="Raw hits across all discovery channels")
        cols[1].metric("Unique", f.unique, help="Unique domains")
        cols[2].metric("Screened", f.screened, help="Homepage read and scored")
        cols[3].metric("Untapped", f.relevant_untapped, help=f"Untapped holders with a fit of at least {RELEVANT_FIT}")
        cols[4].metric("Ready", f.relevant_ready, help=f"Ready sources with a fit of at least {RELEVANT_FIT}")
        cols[5].metric("Confirmed", f.confirmed, help=f"Confirmed fit and saleability, with quotes. "
                                                      f"{f.possible} more possible")
        cols[6].metric("Researched", f.researched)
        cols[7].metric("With contacts", f.with_contacts, help="Researched companies with a verified contact")
        if f.rounds:
            with st.expander(f"Discovery rounds ({len(f.rounds)})"):
                st.dataframe(pd.DataFrame([
                    {"Round": r.round, "Searches": r.calls, "Google queries": r.queries or "?", "Results": r.hits,
                     "New companies": r.new, "Qualifying": r.qualifying}
                    for r in f.rounds
                ]), hide_index=True)
                st.caption("Qualifying: new, a fit, meets every must-have and isn't a tool vendor. A round adding "
                           "too few of them stops the search; the last row is the snowball and vendors' customers.")

    labels = ["Compare", "Candidates"] if s.meta.parent else ["Candidates", "Compare"]
    tabs = dict(zip(labels, st.tabs(labels, key=f"tab-{Path(path).name}", on_change="rerun"), strict=True))
    with tabs["Candidates"]:  # only the open tab runs
        if tabs["Candidates"].open:
            candidates_table(s, path)
            researched(s, path, locked)
    with tabs["Compare"]:
        if tabs["Compare"].open:
            compare_tab(s, path)


def lineage(s: sessions.Session) -> None:
    """Where a compare session came from, and what its brief changed."""
    parent = sessions.SESSIONS_DIR / s.meta.parent
    left, right = st.columns([4, 1], vertical_alignment="center")
    left.caption(f"Researched companies of **{s.meta.parent_name}**, re-scored under this brief. "
                 "Nothing was searched again.")
    if parent.exists():
        right.button("Open the original", on_click=open_session, args=(str(parent),), width="stretch")
    if not s.meta.changes:
        st.caption("The brief is unchanged.")
        return
    with st.expander(f"What changed in the brief ({len(s.meta.changes)})", expanded=True):
        st.dataframe(pd.DataFrame([
            {"Category": ch.label, "Before": ch.before or "—", "After": ch.after or "—",
             "Re-scored": "exactly" if ch.exact else "judged against the old brief"}
            for ch in s.meta.changes
        ]), hide_index=True, width="stretch")
        if not all(ch.exact for ch in s.meta.changes):
            st.caption("Gemini judged these against the old brief, so fits that rest on them are provisional "
                       "(marked *). Tick companies under Candidates and click *Research selected* for exact "
                       "scores.")


def confirmation_text(c: Candidate) -> str:
    return VERDICT_LABELS[verdict(c)]


def trigger_text(c: Candidate) -> str:
    """Untapped holders: the verified triggers, or why a big or regulated one is capped."""
    if c.triggers:
        return "; ".join(f"{TRIGGER_LABELS[t.kind]} {t.date}: {t.summary}" for t in c.triggers)
    if c.needs_trigger:
        return "Big or regulated: no trigger found" if c.trigger_checked else "Big or regulated: not checked yet"
    return ""


def candidates_table(s: sessions.Session, path: str) -> None:
    """Untapped holders and ready sources as two tables, never ranked against each other, with
    shared filters. Once the search has confirmed some companies, each table shows only the
    confirmed and possible ones unless "Unchecked and rejected" is on."""
    st.subheader("Candidates")
    key = Path(path).name
    left, mid, right = st.columns([2, 3, 1])
    min_fit = left.slider("Minimum fit", 1.0, 5.0, RELEVANT_FIT, 0.5, key=f"fit-{key}",
                          help="Companies that couldn't be screened (no readable page) show with Show excluded")
    min_volume = left.select_slider(
        "Minimum volume", [1, 2, 3, 4, 5], 1, key=f"volume-{key}",
        format_func=lambda v: "Any" if v == 1 else f"{v} or more",
        help="Volume score, 1 to 5 against the volume wanted from one seller")
    languages = mid.multiselect(
        "Languages", s.brief.languages, key=f"languages-{key}", placeholder="Any",
        help="Only companies with evidence for at least one of these") if s.brief.languages else []
    confirmed_any = any(c.confirmation for c in s.discovery.candidates)
    show_all = mid.toggle("Unchecked and rejected", key=f"unchecked-{key}", disabled=not confirmed_any,
                          help="By default only companies confirmed (or possibly) to fit and to be able to sell "
                               "are shown, once the search has checked them. This shows the rest, with why.")
    show_excluded = right.toggle("Show excluded", key=f"excluded-{key}",
                                 help="Unrelated companies, dead websites, ones a must-have filter rules out, ones "
                                      "you killed, and "
                                      "ones not screened because their site blocks automated access and no other "
                                      "page about them was found (the Reason column says why)")
    hide_unknown = right.toggle("Known volume", key=f"unknown-volume-{key}",
                                help="Only companies whose volume is known. Most stay unknown until researched.")
    show_vendors = right.toggle("Tool vendors", key=f"vendors-{key}",
                                help="Makers of the tools holders record with (e.g. meeting recorders). Their "
                                     "customers hold the data, so they are hidden and their fit is capped at 2.0.")

    def shown(c: Candidate) -> bool:
        if c.excluded or c.killed or c.fit is None:  # ruled out, or nothing to judge it by
            return show_excluded
        if c.vendor:
            if not show_vendors:
                return False
        elif c.fit < min_fit:
            return False
        if confirmed_any and not show_all and verdict(c) not in ("confirmed", "possible"):
            return False
        volume = c.scores.volume
        return ((not languages or any(lang in c.languages for lang in languages))
                and (volume >= min_volume if volume is not None else not hide_unknown))

    selected = []
    for side, cands in split(s.discovery.candidates).items():
        rows = [c for c in cands if shown(c)]
        if side is None and not rows:
            continue
        st.markdown(f"**{LIST_TITLES[side]}** ({len(rows)})")
        if not rows:
            st.caption("None match these filters." if cands else "None found.")
            continue
        selected += candidate_rows(s, rows, side, f"table-{key}-{side}")
    if confirmed_any and not show_all:
        st.caption("Showing confirmed and possible companies. Turn on *Unchecked and rejected* for the rest.")
    st.button(
        f"Research selected ({len(selected)})", type="primary", disabled=busy() or not selected,
        on_click=start_research, args=(path, selected),
        help="Tick rows in the tables first. Two Gemini calls per company (research and contacts).",
    )
    st.caption(LEGAL_NOTE)
    st.download_button("Download candidates CSV", pipeline.candidates_csv(s.discovery.candidates, s.brief),
                       "candidates.csv", "text/csv")
    verdict_details(s)


def candidate_rows(s: sessions.Session, rows: list[Candidate], side: str | None, key: str) -> list[str]:
    """One list's table; returns the domains ticked in it."""
    untapped = side == "untapped"
    event = st.dataframe(
        pd.DataFrame([
            {
                "Fit": fmt(c.fit),
                "Verdict": confirmation_text(c),
                "Company": c.name,
                "Type": type_label(c),
                "Saleability": saleability_label(c, s.brief),
                **({"Trigger": trigger_text(c)} if untapped else {}),
                "Data match": fmt(c.scores.data_match),
                "Coverage": fmt(c.scores.coverage),
                "Volume": fmt(c.scores.volume),
                **{label: fact_text(c, k) for k, label in FACT_LABELS.items()},
                "Languages and regions": ", ".join(c.languages + c.regions),
                "Reason": kill_text(c) if c.killed else (c.confirmation.reason if c.confirmation else c.reason),
                "Obstacle → how to buy": obstacle_text(c),
                "Must-haves not met": ", ".join(CATEGORIES[k].label for k in must_failures(c, s.brief)),
                "Found via": ", ".join([f"customer of {t}" for t in c.tools] + c.markets),
                "Website": c.website,
                "Found on": c.source_urls[0] if c.source_urls else None,
                "Source checked": c.source_verified,
            }
            for c in rows
        ]),
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="multi-row",
        key=key,
        column_config={
            "Fit": st.column_config.TextColumn(help="1 to 5: weighted mean of the known scores, after caps"),
            "Verdict": st.column_config.TextColumn(
                help="Confirmed: its own pages show it fits the must-haves and it can sell (owns the data and shows "
                     "a sign it would, or a licence the brief accepts). Possible: fits, saleability unclear. "
                     "Unconfirmed: the fit isn't shown. Rejected: a must-have contradicted or a blocker. Empty: "
                     "not checked. " + LEGAL_NOTE),
            "Saleability": st.column_config.TextColumn(
                help="Ready sources: the licence (prior until research checks it). Untapped: the lowest of three "
                     "answers (exists, allowed, willing): High, Medium, Low or Not sellable; initial from the "
                     "homepage, verified once research finds quotes. Only verified answers on exists or allowed cap "
                     "the fit; initial ones count in the licence score. " + LEGAL_NOTE),
            "Trigger": st.column_config.TextColumn(
                help="Big brands and regulated institutions count only after something changed: distress, a past "
                     "data deal or a law allowing reuse, dated and quoted. Until then their fit is capped at 2.0."),
            "Obstacle → how to buy": st.column_config.TextColumn(
                help="Untapped holders: what stands in the way, and how it could still be bought"),
            "Coverage": st.column_config.TextColumn(help="Evidence for the brief's languages and regions"),
            "Price": st.column_config.TextColumn(help=f"Published price of the data. {FACT_HELP}"),
            "Data years": st.column_config.TextColumn(help=f"When the data was recorded. {FACT_HELP}"),
            "Founded": st.column_config.TextColumn(help=f"Year the company was founded, and its age. {FACT_HELP}"),
            "Must-haves not met": st.column_config.TextColumn(
                help="Must-have categories the evidence contradicts; each caps the fit"),
            "Found via": st.column_config.TextColumn(
                help="The tool whose customer stories named it, and the local markets whose searches found it"),
            "Website": st.column_config.LinkColumn(),
            "Found on": st.column_config.LinkColumn(),
            "Source checked": st.column_config.CheckboxColumn(
                help="The source page was confirmed to list this company"),
        },
    )
    return [rows[i].domain for i in event.selection.rows if i < len(rows)]


def verdict_details(s: sessions.Session) -> None:
    """The quotes behind each verdict."""
    checked = [c for c in s.discovery.candidates if c.confirmation]
    if not checked:
        return
    with st.expander(f"Why these verdicts ({len(checked)})"):
        for c in checked:
            st.markdown(f"**{c.name}** — {confirmation_text(c)}: {c.confirmation.reason}")
            confirmation_quotes(c.confirmation)


def confirmation_quotes(conf: Confirmation) -> None:
    found = [(CATEGORIES[ch.category].label if ch.category in CATEGORIES else ch.category, ev)
             for ch in conf.checks for ev in ch.evidence]
    found += [(f"Licence: {conf.licence}", ev) for ev in conf.licence_evidence]
    found += [(f"Sign it would sell: {sig.summary}", ev) for sig in conf.signals for ev in sig.evidence]
    found += [(f"Blocker: {b.summary}", ev) for b in conf.blockers for ev in b.evidence]
    for what, ev in found:
        st.markdown(f"- *{what}:* {ev.quote} ([source]({ev.url}))")


def kill_text(c: Candidate) -> str:
    return f"Killed: {c.kill_reason}" if c.kill_reason else "Killed"


def change_text(now: float | None, before: float | None) -> str:
    if now is None or before is None:
        return ""
    return "same" if abs(now - before) < 0.05 else f"{now - before:+.1f}"


def rank_text(before: int | None, now: int | None) -> str:
    if now is None:
        return "killed"
    return f"#{now}" if before in (None, now) else f"#{before} → #{now}"


# --- compare ----------------------------------------------------------------------------------
# The Compare tab works on a working brief and working kills, kept in st.session_state per
# session. Everything shown is a preview re-scored in Python (worker/compare.py); nothing is
# written until Save, which creates a new session and leaves this one as it is.

MAX_COMPARED = 4
NONE = "—"
DIFFERENT = "background-color: rgba(255, 196, 0, 0.18)"
PROVISIONAL = "color: #8a8a8a; font-style: italic"


def _cmp(path: str, name: str) -> str:
    return f"cmp-{Path(path).name}-{name}"


def reset_working(path: str) -> None:
    """Forget the working brief, kills and picks: back to what is saved."""
    for k in [k for k in st.session_state if str(k).startswith(_cmp(path, ""))]:
        del st.session_state[k]


def _rebuild_editor(path: str, brief: Brief, shown: list[str]) -> None:
    """New editor widgets, filled in from brief."""
    st.session_state[_cmp(path, "snapshot")] = brief
    st.session_state[_cmp(path, "shown")] = shown
    st.session_state[_cmp(path, "version")] += 1


def _remove_requirement(path: str, key: str) -> None:
    brief = st.session_state[_cmp(path, "brief")]
    picks = {k: p for k, p in brief.picks.items() if k != key}
    _rebuild_editor(path, brief.model_copy(update={"picks": picks}),
                    [k for k in st.session_state[_cmp(path, "shown")] if k != key])


def _add_requirement(path: str, widget: str) -> None:
    if key := st.session_state.get(widget):
        _rebuild_editor(path, st.session_state[_cmp(path, "brief")], [*st.session_state[_cmp(path, "shown")], key])


def _kill(path: str, domain: str) -> None:
    reason = st.session_state.get(_cmp(path, f"why-{domain}"), "").strip()
    st.session_state[_cmp(path, "kills")] = {**st.session_state[_cmp(path, "kills")], domain: reason}
    pick = _cmp(path, "pick")
    st.session_state[pick] = [d for d in st.session_state.get(pick, []) if d != domain]


def _restore(path: str, domain: str) -> None:
    st.session_state[_cmp(path, "kills")] = {d: why for d, why in st.session_state[_cmp(path, "kills")].items()
                                             if d != domain}


def _pick(path: str, domains: list[str]) -> None:
    st.session_state[_cmp(path, "pick")] = domains


def _save(path: str) -> None:
    s = sessions.load(path)
    p = compare.preview(s, st.session_state[_cmp(path, "brief")], st.session_state[_cmp(path, "kills")])
    saved = compare.save(s, p, st.session_state.get(_cmp(path, "name"), "").strip() or s.meta.name)
    reset_working(path)
    open_session(str(saved.path))


def compare_tab(s: sessions.Session, path: str) -> None:
    if not any(c.research for c in s.discovery.candidates):
        st.caption("Only researched companies are compared: research some under Candidates first.")
        return
    kills = st.session_state.setdefault(_cmp(path, "kills"), compare.saved_kills(s))
    left, right = st.columns([1, 3], gap="large")
    with left:
        brief = brief_editor(s, path)
    p = compare.preview(s, brief, kills)
    with right:
        save_bar(s, path, p)
        domains = picker(p, path)
        side_by_side(p, domains, path)
        killed_list(p, path)
        overview(p)
        st.caption(f"Fit change and rank are against the original search. {LEGAL_NOTE}")


def brief_editor(s: sessions.Session, path: str) -> Brief:
    """The working brief: edit, remove or add requirements. Returns it, and keeps it in state."""
    snapshot = st.session_state.setdefault(_cmp(path, "snapshot"), s.brief)
    shown = st.session_state.setdefault(_cmp(path, "shown"), [k for k in CATEGORIES if s.brief.is_set(k)])
    fid = f"{_cmp(path, 'form')}-{st.session_state.setdefault(_cmp(path, 'version'), 0)}"
    st.markdown("**Working brief**")
    st.caption("Edit, remove or add requirements: the comparison follows. Nothing is saved until you click Save.")
    with st.expander("What data and notes"):
        description = st.text_input("What data, in a few words", snapshot.description, key=f"{fid}-description")
        notes = st.text_area("Notes", snapshot.notes, key=f"{fid}-notes")
    picks: dict[str, Pick] = {}
    flags = {}
    for key in (k for k in CATEGORIES if k in shown):
        with st.container(border=True):
            picks[key] = category_input(CATEGORIES[key], snapshot, fid, picks)
            flags[key] = st.empty()
            st.button("Remove", key=f"{fid}-{key}-remove", icon=":material/close:", type="tertiary",
                      on_click=_remove_requirement, args=(path, key))
    st.selectbox("Add a requirement", [k for k in CATEGORIES if k not in shown], index=None, key=f"{fid}-add",
                 format_func=lambda k: f"{CATEGORIES[k].code} {CATEGORIES[k].label}", placeholder="Pick a category",
                 on_change=_add_requirement, args=(path, f"{fid}-add"))
    brief = Brief(description=description.strip() or snapshot.description, notes=notes,
                  picks={k: p for k, p in picks.items() if p.values or p.amount is not None or p.amount_to is not None})
    st.session_state[_cmp(path, "brief")] = brief
    for ch in compare.changes(s.brief, brief):
        if ch.key in flags and not ch.exact:
            flags[ch.key].caption("Judged against the old brief: fits that rest on it are provisional (*)")
    return brief


def save_bar(s: sessions.Session, path: str, p: compare.Preview) -> None:
    if not p.unsaved:
        st.caption("No unsaved changes. Edit the brief or kill companies to compare them another way.")
        return
    with st.container(border=True):
        st.warning("**Unsaved changes.** This is a preview: Save keeps it as a new session, and this session stays "
                   "as it is.")
        name, save, reset = st.columns([3, 1, 1], vertical_alignment="bottom")
        name.text_input("Save as", s.meta.name if s.meta.parent else f"{s.meta.name} · compare",
                        key=_cmp(path, "name"))
        save.button("Save", type="primary", on_click=_save, args=(path,), width="stretch")
        reset.button("Reset", on_click=reset_working, args=(path,), width="stretch",
                     help="Back to this session's brief and kills")


def pick_label(c: Candidate) -> str:
    return " · ".join(x for x in (c.name, type_label(c), ", ".join(c.regions)) if x)


def picker(p: compare.Preview, path: str) -> list[str]:
    """Search the researched companies of one type and pick up to MAX_COMPARED: ready sources and
    untapped holders are judged differently, so they are never compared with each other. Values
    are domains, so a pick survives re-scoring."""
    alive = {c.domain: c for c in p.candidates if not c.killed}
    kinds = list(dict.fromkeys(c.holder_type for c in alive.values()))
    if len(kinds) > 1:
        kind = st.radio("Compare", kinds, horizontal=True, key=_cmp(path, "type"), format_func=LIST_TITLES.get,
                        help="Ready sources and untapped holders are compared separately")
        alive = {d: c for d, c in alive.items() if c.holder_type == kind}
    key = _cmp(path, "pick")
    saved = st.session_state.get(key)
    chosen = [d for d in saved if d in alive] if saved is not None else list(alive)[:MAX_COMPARED]
    if saved and not chosen:  # the picks were of the other type: start this one from its best
        chosen = list(alive)[:MAX_COMPARED]
    st.session_state[key] = chosen
    search, top = st.columns([4, 1], vertical_alignment="bottom")
    query = search.text_input("Search researched companies", key=_cmp(path, "query"),
                              placeholder="Name, type, region, language or what data they hold")
    top.button(f"Top {MAX_COMPARED}", on_click=_pick, args=(path, list(alive)[:MAX_COMPARED]), width="stretch",
               help="The best fits of this type under the working brief")
    found = [d for d, c in alive.items() if d not in chosen and compare.matches(c, query)]
    picked = st.multiselect(f"Compare (up to {MAX_COMPARED})", chosen + found, key=key, max_selections=MAX_COMPARED,
                            format_func=lambda d: pick_label(alive[d]), placeholder="Pick companies to compare")
    if query and not found:
        st.caption("No other researched company matches.")
    return picked


def answer_text(c: Candidate, q: str, brief: Brief) -> str:
    if c.holder_type != "untapped":
        return NONE
    a, score = (s := c.current_saleability()) and getattr(s, q), answers(c, brief)[q]
    if score is None:
        return "?"
    return f"{score}/5 ({'verified' if a and a.verified else 'initial'})"


def verdict_text(c: Candidate, key: str) -> str:
    ch = next((ch for ch in c.research.checks if ch.category == key), None)
    return f"{VERDICT_ICONS[ch.verdict]} {ch.verdict}" if ch else NONE


def compared_rows(cands: list[Candidate], p: compare.Preview) -> dict[str, list[str]]:
    """Short values, one row per field: these are compared and highlighted."""
    checks = [k for k in CheckedCategory.__args__ if any(ch.category == k for c in cands for ch in c.research.checks)]
    rows = {
        "Fit": [fmt(c.fit) + ("*" if c.domain in p.provisional else "") for c in cands],
        "Provisional because": ["; ".join(p.provisional.get(c.domain, [])) or NONE for c in cands],
        "Fit change": [change_text(c.fit, p.fit_before.get(c.domain)) or NONE for c in cands],
        "Rank": [rank_text(p.rank_before.get(c.domain), p.rank_now.get(c.domain)) for c in cands],
        "Type": [type_label(c) for c in cands],
        "Saleability": [saleability_label(c, p.brief) for c in cands],
        "Licence found": [c.research.licence if c.holder_type == "ready" else NONE for c in cands],
        **{label: [answer_text(c, q, p.brief) for c in cands] for q, label in QUESTION_LABELS.items()},
        **{CATEGORIES[k].label: [verdict_text(c, k) for c in cands] for k in checks},
        "Must-haves not met": [", ".join(CATEGORIES[k].label for k in must_failures(c, p.brief)) or NONE
                               for c in cands],
        "Languages": [", ".join(c.languages) or NONE for c in cands],
        "Regions": [", ".join(c.regions) or NONE for c in cands],
        "Verified contacts": [str(sum(k.verified for k in c.contacts)) for c in cands],
        "Email": ["drafted" if c.email else NONE for c in cands],
    }
    return {k: v for k, v in rows.items() if any(x != NONE for x in v)}


def text_rows(cands: list[Candidate]) -> dict[str, list[str]]:
    """Free text: rarely the same twice, so always shown."""
    checks = [k for k in CheckedCategory.__args__ if any(ch.category == k for c in cands for ch in c.research.checks)]
    rows = {
        "Data held": [c.research.data_held for c in cands],
        "Volume found": [c.research.volume_estimate or "?" for c in cands],
        **{label: [fact_text(c, k) for c in cands] for k, label in FACT_LABELS.items()},
        **{f"{CATEGORIES[k].label}: finding": [
            next((ch.finding for ch in c.research.checks if ch.category == k), NONE) for c in cands] for k in checks},
        "Obstacle → how to buy": [obstacle_text(c) or NONE for c in cands],
        "Why it was found": [c.reason or NONE for c in cands],
    }
    return {k: v for k, v in rows.items() if any(x != NONE for x in v)}


def quotes(c: Candidate) -> list[tuple[str, Evidence]]:
    r, s = c.research, c.current_saleability()
    out = [("Licence", ev) for ev in r.licence_evidence]
    out += [(label, ev) for q, label in QUESTION_LABELS.items() for ev in ((s and getattr(s, q).evidence) or [])]
    out += [("Volume", ev) for ev in r.volume_evidence]
    out += [(label, f) for k, label in FACT_LABELS.items() if (f := c.fact(k))]
    out += [(CATEGORIES[ch.category].label, ev) for ch in r.checks for ev in ch.evidence]
    return out


def _table(rows: dict[str, list[str]], names: list[str], provisional: list[bool] = ()) -> None:
    """Fields down, companies across. Rows where companies differ are highlighted; a
    provisional fit is greyed."""
    df = pd.DataFrame.from_dict(rows, orient="index", columns=names)

    def style(row: pd.Series) -> list[str]:
        differ = len(names) > 1 and row.nunique() > 1
        out = [DIFFERENT if differ else "" for _ in row]
        if row.name == "Fit":
            out = [f"{o};{PROVISIONAL}" if prov else o for o, prov in zip(out, provisional, strict=True)]
        return out

    st.dataframe(df.style.apply(style, axis=1), width="stretch")


def side_by_side(p: compare.Preview, domains: list[str], path: str) -> None:
    cands = [p.get(d) for d in domains]
    if not cands:
        st.caption("Pick companies above to compare them side by side.")
        return
    for col, c in zip(st.columns(len(cands)), cands, strict=True):
        with col:
            st.markdown(f"**{c.name}**  \n[{c.domain}]({c.website})")
            kill, details = st.columns(2)
            with kill.popover("Kill", icon=":material/block:", width="stretch", key=_cmp(path, f"kill-{c.domain}")):
                st.text_input("Why", key=_cmp(path, f"why-{c.domain}"), placeholder="e.g. licence too strict")
                st.button("Kill", key=_cmp(path, f"kill-ok-{c.domain}"), type="primary", on_click=_kill,
                          args=(path, c.domain), help="Until you save, only this preview changes")
            if details.button("Details", key=_cmp(path, f"details-{c.domain}"), width="stretch"):
                details_dialog(c, p.brief, path)
    names = [c.name if [x.name for x in cands].count(c.name) == 1 else f"{c.name} ({c.domain})" for c in cands]
    only_diff = st.toggle("Only differences", key=_cmp(path, "diff"), disabled=len(cands) < 2,
                          help="Hides rows where every company has the same value. Text rows always show.")
    rows = compared_rows(cands, p)
    if only_diff and len(cands) > 1:
        rows = {k: v for k, v in rows.items() if len(set(v)) > 1 or k == "Fit"}
    _table(rows, names, [c.domain in p.provisional for c in cands])
    if any(c.domain in p.provisional for c in cands):
        st.caption("\\* Provisional: this fit rests on something Gemini judged against an older brief (see "
                   "*Provisional because*). Research the company again for an exact score.")
    with st.expander("Text and sources"):
        _table(text_rows(cands), names)
        for col, c in zip(st.columns(len(cands)), cands, strict=True):
            with col:
                st.markdown(f"**{c.name}**")
                for what, ev in (found := quotes(c)):
                    st.markdown(f"*{what}:* {ev.quote}\n\n[{ev.url}]({ev.url})")
                if not found:
                    st.caption("No verified quotes.")


@st.dialog("Research details", width="large")
def details_dialog(c: Candidate, brief: Brief, path: str) -> None:
    st.subheader(c.name)
    st.caption("Scored under the working brief.")
    research_details(c, brief)
    contacts_section(c, path)


def killed_list(p: compare.Preview, path: str) -> None:
    dead = [c for c in p.candidates if c.killed]
    if not dead:
        return
    with st.expander(f"Killed ({len(dead)})"):
        for c in dead:
            text, button = st.columns([4, 1], vertical_alignment="center")
            text.markdown(f"**{c.name}** — {c.kill_reason or 'no reason given'}")
            excluded = c.kill_reason == compare.EXCLUDED
            button.button("Restore", key=_cmp(path, f"restore-{c.domain}"), on_click=_restore, args=(path, c.domain),
                          disabled=excluded, width="stretch",
                          help="Remove it from the brief's exclusions to restore it" if excluded else None)


def overview(p: compare.Preview) -> None:
    with st.expander(f"All researched ({len(p.candidates)})"):
        checks = [k for k in CheckedCategory.__args__
                  if any(ch.category == k for c in p.candidates for ch in c.research.checks)]
        st.dataframe(pd.DataFrame([
            {
                "Rank": rank_text(p.rank_before.get(c.domain), p.rank_now.get(c.domain)),
                "Fit": fmt(c.fit) + ("*" if c.domain in p.provisional else ""),
                "Fit change": change_text(c.fit, p.fit_before.get(c.domain)),
                "Company": c.name,
                "Type": type_label(c),
                "Saleability": saleability_label(c, p.brief),
                "Must-haves not met": ", ".join(CATEGORIES[k].label for k in must_failures(c, p.brief)),
                "Volume found": c.research.volume_estimate or "?",
                "Price": fact_text(c, "price"),
                "Data years": fact_text(c, "data_period"),
                **{CATEGORIES[k].label: verdict_text(c, k) for k in checks},
                "Obstacle → how to buy": obstacle_text(c),
                "Verified contacts": sum(k.verified for k in c.contacts),
                "Email": "drafted" if c.email else "",
                "Killed": kill_text(c) if c.killed else "",
            }
            for c in p.candidates
        ]), hide_index=True, width="stretch")


def _update_candidate(path: str, domain: str, change: Callable[[Candidate], None]) -> None:
    s = sessions.load(path)
    change(next(c for c in s.discovery.candidates if c.domain == domain))
    sessions.save(s)


def researched(s: sessions.Session, path: str, locked: bool) -> None:
    done = [c for c in s.discovery.candidates if c.research]
    if not done:
        return
    st.subheader("Researched")
    for c in done:
        killed = f" — {kill_text(c)}" if c.killed else ""
        with st.expander(f"{c.name} — fit {fmt(c.fit)} — {saleability_label(c, s.brief)}{killed}"):
            research_details(c, s.brief)
            contacts_section(c, path)
            email_section(s, c, path, locked)
    with_contacts = [c for c in done if c.contacts]
    if with_contacts:
        st.download_button("Download contacts CSV", pipeline.contacts_csv(with_contacts), "contacts.csv", "text/csv")


def research_details(c: Candidate, brief: Brief) -> None:
    """Licence or saleability, volume, stated facts, data held and checks, each with its quotes."""
    r = c.research
    if c.holder_type == "untapped":
        saleability_section(c, brief)
    else:
        accepted = brief.active("licence")
        fits = f" (you accept: {', '.join(accepted)})" if accepted else ""
        st.markdown(f"**Licence:** {r.licence}{fits}")
        for ev in r.licence_evidence:
            st.markdown(f"> {ev.quote}\n\n[{ev.url}]({ev.url})")
        if not r.licence_evidence:
            st.caption("No verified quote.")
    st.markdown(f"**Volume:** {r.volume_estimate or 'unknown'}")
    for ev in r.volume_evidence:
        st.markdown(f"> {ev.quote}\n\n[{ev.url}]({ev.url})")
    for k, label in FACT_LABELS.items():
        if f := c.fact(k):
            st.markdown(f"**{label}:** {fact_text(c, k)}\n\n> {f.quote}\n\n[{f.url}]({f.url})")
    st.markdown(f"**Data held:** {r.data_held}")
    if r.checks:
        st.markdown("**Checks against the brief**")
    for ch in r.checks:
        st.markdown(f"{VERDICT_ICONS[ch.verdict]} **{CATEGORIES[ch.category].label}** "
                    f"({brief.value_text(ch.category)}): {ch.finding}")
        for ev in ch.evidence:
            st.markdown(f"> {ev.quote}\n\n[{ev.url}]({ev.url})")
    st.caption(LEGAL_NOTE)


def saleability_section(c: Candidate, brief: Brief) -> None:
    """The three answers with their reasons and quotes; the grade is the lowest of them."""
    st.markdown(f"**Saleability:** {saleability_label(c, brief)} — the lowest of the three answers")
    s, scores = c.current_saleability(), answers(c, brief)
    for q, label in QUESTION_LABELS.items():
        a = getattr(s, q) if s else None
        score = scores[q]
        state = "verified" if a and a.verified else "initial" if score is not None else "unknown"
        reason = f": {a.reason}" if a and a.reason else ""
        st.markdown(f"- **{label}** {score if score is not None else '?'}/5 ({state}){reason}")
        for ev in a.evidence if a else []:
            st.markdown(f"  > {ev.quote}\n\n  [{ev.url}]({ev.url})")
    for t in c.triggers:
        st.markdown(f"**{TRIGGER_LABELS[t.kind]} ({t.date}):** {t.summary}")
        for ev in t.evidence:
            st.markdown(f"  > {ev.quote}\n\n  [{ev.url}]({ev.url})")
    if c.needs_trigger and not c.triggers:
        st.caption("A big brand or regulated institution with no verified trigger (distress, a past data deal or a "
                   "law allowing reuse): its fit stays capped.")
    if s and s.obstacle:
        st.markdown(f"**Main obstacle:** {s.obstacle}")
    if s and s.path:
        st.markdown(f"**How to buy it:** {s.path}")
    if not is_verified(c):
        st.caption("Answers without a quote research could find on the cited page stay initial.")


def contacts_section(c: Candidate, path: str) -> None:
    st.markdown("**Contacts**")
    if not c.contacts:
        st.caption("None found.")
        return
    only_verified = st.toggle("Verified only", key=f"verified-{Path(path).name}-{c.domain}",
                              help="Email/phone/name found on the source page when we checked it")
    st.dataframe(
        pd.DataFrame([
            {"Name": k.name, "Role": k.role, "Email": k.email, "Phone": k.phone, "Verified": k.verified,
             "From": k.origin, "Source": k.source_url}
            for k in c.contacts if k.verified or not only_verified
        ]),
        hide_index=True, width="stretch",
        column_config={"Source": st.column_config.LinkColumn(), "Verified": st.column_config.CheckboxColumn()},
    )


def _describe(k: Contact) -> str:
    who = " — ".join(x for x in (k.name, k.role) if x) or "Contact"
    how = k.email or k.phone or "no email"
    return f"{who} ({how}){'' if k.verified else ' · unverified'}"


def email_section(s: sessions.Session, c: Candidate, path: str, locked: bool) -> None:
    st.markdown("**Email**")
    key = f"{Path(path).name}-{c.domain}"
    contacts = sorted(c.contacts, key=lambda k: (not k.verified, not k.email, not k.name))
    contact = None
    if contacts:
        i = st.selectbox("Contact", range(len(contacts)), format_func=lambda i: _describe(contacts[i]),
                         key=f"contact-{key}")
        contact = contacts[i]
    if st.button("Draft email", key=f"draft-{key}", disabled=locked):
        with st.spinner("Drafting…"):
            try:
                draft = email_step.draft_email(s.brief, c, contact)
            except Exception as e:
                st.error(f"Couldn't draft the email — {type(e).__name__}: {e}")
                return
        _update_candidate(path, c.domain, lambda cand: setattr(cand, "email", draft))
        st.session_state[f"subject-{key}"], st.session_state[f"body-{key}"] = draft.subject, draft.body
        st.rerun()
    if not c.email:
        return

    def save_edit() -> None:
        subject, body = st.session_state[f"subject-{key}"], st.session_state[f"body-{key}"]
        _update_candidate(path, c.domain,
                          lambda cand: setattr(cand, "email", cand.email.model_copy(
                              update={"subject": subject, "body": body})))

    st.session_state.setdefault(f"subject-{key}", c.email.subject)
    st.session_state.setdefault(f"body-{key}", c.email.body)
    st.caption(f"To: {c.email.to or '(no email address — find one before sending)'}")
    subject = st.text_input("Subject", key=f"subject-{key}", on_change=save_edit, disabled=locked)
    body = st.text_area("Body", key=f"body-{key}", height=260, on_change=save_edit, disabled=locked)
    st.download_button("Download email", f"To: {c.email.to or ''}\nSubject: {subject}\n\n{body}",
                       f"{c.domain}-email.txt", "text/plain", key=f"download-{key}")
    st.caption("Nothing is sent automatically.")


# --- page -------------------------------------------------------------------------------------

st.set_page_config(page_title="Atom Data Scout", page_icon=ICON, layout="wide")
st.logo(LOGO, size="large", icon_image=ICON)
st.title("Atom Data Scout")
sidebar()

if path := st.session_state.get("session_path"):
    session_page(path)
elif draft := st.session_state.get("draft"):
    form_page(draft)
else:
    brief_page()
