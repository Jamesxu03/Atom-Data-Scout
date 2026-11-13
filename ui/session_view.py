"""One search: its funnel, the two candidate lists, the researched companies, and the
Compare tab."""

from pathlib import Path

import pandas as pd
import streamlit as st

from ui.clients import client_picker
from ui.company_view import contacts_section, email_section, research_details
from ui.compare_view import compare_tab
from ui.jobs import busy, current_job, start_research
from ui.labels import (
    FACT_HELP,
    FACT_LABELS,
    LEGAL_NOTE,
    LIST_TITLES,
    confirmation_text,
    fact_text,
    fmt,
    kill_text,
    obstacle_text,
    trigger_text,
    type_label,
)
from ui.nav import open_session
from ui.outreach import outreach_labels, outreach_tab
from worker import crm, pipeline, sessions
from worker.brief_template import CATEGORIES, transfer_flags
from worker.config import RELEVANT_FIT
from worker.schemas import Candidate, Confirmation
from worker.scoring import must_failures, saleability_label, split, verdict


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
    client_row(path)
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

    labels = ["Compare", "Candidates", "Outreach"] if s.meta.parent else ["Candidates", "Outreach", "Compare"]
    tabs = dict(zip(labels, st.tabs(labels, key=f"tab-{Path(path).name}", on_change="rerun"), strict=True))
    with tabs["Candidates"]:  # only the open tab runs
        if tabs["Candidates"].open:
            candidates_table(s, path)
            researched(s, path, locked)
    with tabs["Outreach"]:
        if tabs["Outreach"].open:
            outreach_tab(s, path, locked)
    with tabs["Compare"]:
        if tabs["Compare"].open:
            compare_tab(s, path)


def client_row(path: str) -> None:
    """Who the search is for, and where it sits on their board (dragging it there does the same)."""
    name = Path(path).name
    cid = crm.client_of(name)
    left, right = st.columns([3, 2], vertical_alignment="bottom")
    with left:
        chosen = client_picker(f"client-{name}", cid, tip="Who this search is for: it goes on their board under "
                                                          "Companies, with its outreach")
    if chosen != cid:
        if chosen:
            crm.assign_search(name, chosen)
        else:
            crm.unassign_search(name)
        cid = chosen
    if not cid:
        return
    placement = next(p for p in crm.placements(cid) if p.session == name)
    stage = crm.search_stage(placement, crm.deals(cid, session=name))
    new = right.selectbox("Stage on the board", crm.SEARCH_STAGES, index=crm.SEARCH_STAGES.index(stage),
                          key=f"search-stage-{name}-{stage}")
    if new != stage:
        crm.move_search(name, new)
        st.rerun()


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

    selected, outreach = [], outreach_labels(s, path)
    for side, cands in split(s.discovery.candidates).items():
        rows = [c for c in cands if shown(c)]
        if side is None and not rows:
            continue
        st.markdown(f"**{LIST_TITLES[side]}** ({len(rows)})")
        if not rows:
            st.caption("None match these filters." if cands else "None found.")
            continue
        selected += candidate_rows(s, rows, side, f"table-{key}-{side}", outreach)
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


def candidate_rows(s: sessions.Session, rows: list[Candidate], side: str | None, key: str,
                   outreach: dict[str, str] | None = None) -> list[str]:
    """One list's table; returns the domains ticked in it. outreach: where outreach to each stands."""
    untapped = side == "untapped"
    event = st.dataframe(
        pd.DataFrame([
            {
                "Fit": fmt(c.fit),
                "Verdict": confirmation_text(c),
                "Company": c.name,
                **({"Outreach": outreach.get(c.domain, "")} if outreach else {}),
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
