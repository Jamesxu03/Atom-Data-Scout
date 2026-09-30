"""Streamlit UI. Run: uv run streamlit run app.py

Brief -> Refine (form) -> Save and search -> one list ranked by fit -> tick rows -> Research
selected -> draft an email per company. Each search is a session folder (worker/sessions.py),
and every page reads it from disk, so any past session reopens without rerunning and a browser
refresh loses nothing.

Searches and research run in a background thread, so touching the page doesn't restart or
interrupt them; a fragment polls for progress once a second.
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import streamlit as st

from worker import pipeline, sessions
from worker.config import RELEVANT_FIT
from worker.schemas import (
    LICENCE_LABELS,
    MUST_HAVE_FIELDS,
    SOURCE_LABELS,
    Brief,
    BriefDraft,
    Candidate,
    Contact,
    Volume,
)
from worker.scoring import prior_label
from worker.steps import brief as brief_step
from worker.steps import email as email_step

EXAMPLE_BRIEF = (
    "An AI meeting-transcription company wants to buy multi-speaker English meeting audio "
    "with transcripts and speaker labels, at least 1,000 hours, licensed for model training."
)
LOGO = "assets/atom_logo.png"
VOLUME_UNITS = ["hours", "utterances", "data points", "GB", "images", "videos"]
HOLDER_LABELS = {"vendor": "Vendor", "public": "Public", "by_product": "By-product",
                 "technology": "Technology", "unrelated": "Unrelated", None: "Unknown"}
STATUS_LABELS = {"sellable": "sellable", "open": "open licence", "open_restricted": "open, restricted use",
                 "needs_consent": "needs consent", "internal_only": "internal use only", "unknown": "unknown"}
LEGAL_NOTE = "Licence status is a screening flag from public pages, not legal advice."


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

    @property
    def elapsed(self) -> int:
        return int((self.finished or time.time()) - self.started)


def current_job() -> Job | None:
    return st.session_state.get("job")


def busy() -> bool:
    job = current_job()
    return job is not None and not job.done


def start_job(kind: str, path: str, work: Callable[[Callable[[str], None]], None]) -> None:
    if busy():
        return
    job = Job(kind=kind, session_path=path)

    def run():
        try:
            work(job.messages.append)
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
    start_job("search", path, lambda progress: pipeline.search(sessions.load(path), progress=progress))


def start_research(path: str, domains: list[str]) -> None:
    start_job("research", path,
              lambda progress: pipeline.research(sessions.load(path), domains, progress=progress))


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
    set_draft(BriefDraft(brief=s.brief, suggested=[], options=[], questions=[]), s.meta.brief_text)
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
                f"**{meta.name}**  \n{meta.created:%d %b %Y, %H:%M} · {meta.relevant} relevant",
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
        st.session_state.session_name = draft.brief.data_type
        st.rerun()


def _options(draft: BriefDraft, name: str) -> list[str]:
    return [o for fo in draft.options if fo.field == name for o in fo.options]


def form_page(draft: BriefDraft) -> None:
    b, fid = draft.brief, st.session_state.get("form_id", 0)
    if st.button("← Back to the brief text"):
        st.session_state.draft = None
        st.rerun()
    if draft.questions:
        st.info("**Check with the buyer**\n" + "\n".join(f"- {q}" for q in draft.questions))

    def suggested(name: str) -> None:
        if name in draft.suggested:
            st.caption("suggested")

    def multi(label: str, name: str, help: str | None = None) -> list[str]:
        values = getattr(b, name)
        out = st.multiselect(label, list(dict.fromkeys(values + _options(draft, name))), default=values,
                             accept_new_options=True, key=f"{fid}-{name}", help=help)
        suggested(name)
        return out

    with st.form(f"brief-form-{fid}"):
        name = st.text_input("Session name", st.session_state.get("session_name") or b.data_type,
                             key=f"{fid}-name")
        data_type = st.selectbox("Data type", list(dict.fromkeys([b.data_type] + _options(draft, "data_type"))),
                                 accept_new_options=True, key=f"{fid}-data_type")
        suggested("data_type")
        left, right = st.columns(2)
        with left:
            features = multi("Features", "features", "e.g. multi-speaker, overlapping speech, far-field")
            scenarios = multi("Scenarios", "scenarios")
            label_types = multi("Labels", "label_types")
            formats = multi("Formats", "formats", "e.g. 16 kHz WAV")
        with right:
            languages = multi("Languages", "languages", "Dialects allowed, e.g. Gulf Arabic")
            regions = multi("Regions", "regions", "e.g. Middle East")
            amount_col, unit_col = st.columns(2)
            amount = amount_col.number_input("Minimum volume", min_value=0.0, value=b.volume.amount,
                                             step=100.0, format="%.0f", key=f"{fid}-amount")
            unit = unit_col.selectbox("Unit", list(dict.fromkeys(([b.volume.unit] if b.volume.unit else [])
                                                                 + VOLUME_UNITS)),
                                      accept_new_options=True, key=f"{fid}-unit")
            suggested("volume")
            licence = st.selectbox("Licence", list(LICENCE_LABELS), index=list(LICENCE_LABELS).index(b.licence),
                                   format_func=LICENCE_LABELS.get, key=f"{fid}-licence")
            suggested("licence")
        holder_types = st.multiselect("Sources the buyer accepts", list(SOURCE_LABELS), default=b.holder_types,
                                      format_func=lambda h: SOURCE_LABELS[h].capitalize(), key=f"{fid}-holders",
                                      help="Vendors and public sources off also skips their search channels")
        exclusions = st.multiselect("Skip these domains", b.exclusions, default=b.exclusions,
                                    accept_new_options=True, key=f"{fid}-exclusions",
                                    help="Type a domain, e.g. otter.ai, and press Enter")
        must_have = st.multiselect("Must have", MUST_HAVE_FIELDS, default=b.must_have,
                                   format_func=lambda f: f.replace("_", " "), key=f"{fid}-must",
                                   help="Companies with no evidence of a must-have language or region rank low")
        notes = st.text_area("Notes", b.notes, key=f"{fid}-notes")
        submitted = st.form_submit_button("Save and search", type="primary")

    if not submitted:
        return
    if not (data_type or "").strip():
        st.error("Data type can't be empty.")
        return
    brief = Brief(
        data_type=data_type.strip(), features=features, scenarios=scenarios, languages=languages,
        regions=regions, label_types=label_types, volume=Volume(amount=amount, unit=unit if amount else None),
        formats=formats, licence=licence, holder_types=holder_types, exclusions=exclusions,
        must_have=must_have, notes=notes,
    )
    s = sessions.create(name.strip() or brief.data_type, brief, st.session_state.get("brief_copy", ""))
    open_session(str(s.path))
    start_search(str(s.path))
    st.rerun()


# --- results ----------------------------------------------------------------------------------

def fmt(x: float | None) -> str:
    return "?" if x is None else f"{x:.1f}" if isinstance(x, float) else str(x)


def licence_label(c: Candidate) -> str:
    if c.research:
        return f"checked: {STATUS_LABELS[c.research.licence_status]}"
    if c.holder_type in (None, "unrelated"):
        return "?"
    return f"prior: {prior_label(c.holder_type, c.data_owner)}"


def progress_panel(path: str) -> None:
    job = current_job()
    if job is None or job.session_path != path:
        return
    if not job.done:
        with st.status(f"Running {job.kind}… {job.elapsed}s", expanded=True):
            for m in job.messages[-12:]:
                st.write(m)
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
    st.fragment(progress_panel, run_every=1 if job_here else None)(path)

    if job_here and job.kind == "search":
        st.caption("Searching — results appear here when done.")
        return
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
    cols = st.columns(6)
    cols[0].metric("Found", f.found, help="Raw hits across all discovery channels")
    cols[1].metric("Unique", f.unique, help="Unique domains")
    cols[2].metric("Screened", f.screened, help="Homepage read and scored")
    cols[3].metric("Relevant", f.relevant, help=f"Fit of at least {RELEVANT_FIT}")
    cols[4].metric("Researched", f.researched)
    cols[5].metric("With contacts", f.with_contacts, help="Researched companies with a verified contact")

    candidates_table(s, path)
    researched(s, path, locked)


def candidates_table(s: sessions.Session, path: str) -> None:
    st.subheader("Candidates")
    key = Path(path).name
    left, mid, right = st.columns([2, 3, 1])
    min_fit = left.slider("Minimum fit", 1.0, 5.0, RELEVANT_FIT, 0.5, key=f"fit-{key}",
                          help="Companies whose fit is unknown (homepage unreachable) are always shown, last")
    present = list(dict.fromkeys(c.holder_type for c in s.discovery.candidates if not c.excluded))
    types = mid.multiselect("Type", present, default=present, format_func=HOLDER_LABELS.get, key=f"types-{key}")
    show_excluded = right.toggle("Show excluded", key=f"excluded-{key}", help="Unrelated companies and dead websites")

    rows = [
        c for c in s.discovery.candidates
        if (not c.excluded and c.holder_type in types and (c.fit is None or c.fit >= min_fit))
        or (c.excluded and show_excluded)
    ]
    if not rows:
        st.caption("No candidates match these filters.")
        return
    event = st.dataframe(
        pd.DataFrame([
            {
                "Fit": fmt(c.fit),
                "Company": c.name,
                "Type": HOLDER_LABELS[c.holder_type],
                "Licence": licence_label(c),
                "Data match": fmt(c.scores.data_match),
                "Coverage": fmt(c.scores.coverage),
                "Volume": fmt(c.scores.volume),
                "Languages and regions": ", ".join(c.languages + c.regions),
                "Reason": c.reason,
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
        key=f"table-{key}",
        column_config={
            "Fit": st.column_config.TextColumn(help="1 to 5: weighted mean of the known scores, after caps"),
            "Licence": st.column_config.TextColumn(
                help="prior: a guess from the holder type until research checks it. " + LEGAL_NOTE),
            "Coverage": st.column_config.TextColumn(help="Evidence for the brief's languages and regions"),
            "Website": st.column_config.LinkColumn(),
            "Found on": st.column_config.LinkColumn(),
            "Source checked": st.column_config.CheckboxColumn(
                help="The source page was confirmed to list this company"),
        },
    )
    selected = [rows[i].domain for i in event.selection.rows if i < len(rows)]
    st.button(
        f"Research selected ({len(selected)})", type="primary", disabled=busy() or not selected,
        on_click=start_research, args=(path, selected),
        help="Tick rows in the table first. Two Gemini calls per company (research and contacts).",
    )
    st.caption(LEGAL_NOTE)
    st.download_button("Download candidates CSV", pipeline.candidates_csv(s.discovery.candidates),
                       "candidates.csv", "text/csv")


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
        r = c.research
        with st.expander(f"{c.name} — fit {fmt(c.fit)} — licence {STATUS_LABELS[r.licence_status]}"):
            st.markdown(f"**Licence:** {STATUS_LABELS[r.licence_status]}")
            for ev in r.licence_evidence:
                st.markdown(f"> {ev.quote}\n\n[{ev.url}]({ev.url})")
            if not r.licence_evidence:
                st.caption("No verified quote.")
            st.markdown(f"**Volume:** {r.volume_estimate or 'unknown'}")
            for ev in r.volume_evidence:
                st.markdown(f"> {ev.quote}\n\n[{ev.url}]({ev.url})")
            st.markdown(f"**Data held:** {r.data_held}")
            st.caption(LEGAL_NOTE)
            contacts_section(c, path)
            email_section(s, c, path, locked)
    with_contacts = [c for c in done if c.contacts]
    if with_contacts:
        st.download_button("Download contacts CSV", pipeline.contacts_csv(with_contacts), "contacts.csv", "text/csv")


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

st.set_page_config(page_title="Atom Data Scout", page_icon=LOGO, layout="wide")
st.logo(LOGO, size="large")
st.title("Atom Data Scout")
sidebar()

if path := st.session_state.get("session_path"):
    session_page(path)
elif draft := st.session_state.get("draft"):
    form_page(draft)
else:
    brief_page()
