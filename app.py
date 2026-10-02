"""Streamlit UI. Run: uv run streamlit run app.py

Brief -> Refine (form) -> Save and search -> one list ranked by fit -> tick rows -> Research
selected -> draft an email per company. Each search is a session folder (worker/sessions.py),
and every page reads it from disk, so any past session reopens without rerunning and a browser
refresh loses nothing.

Searches and research run in a background thread, so touching the page doesn't restart or
interrupt them; a fragment polls for progress once a second.
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

from worker import pipeline, sessions
from worker.archetypes import ARCHETYPES
from worker.archetypes import BY_KEY as ARCHETYPE_BY_KEY
from worker.brief_template import (
    CATEGORIES,
    CODE_LABELS,
    IMPORTANCE_LABELS,
    SECTIONS,
    Brief,
    BriefDraft,
    Category,
    Pick,
    suggested_languages,
    transfer_flags,
)
from worker.config import RELEVANT_FIT
from worker.schemas import Candidate, Contact
from worker.scoring import must_failures, prior_label
from worker.steps import brief as brief_step
from worker.steps import email as email_step

EXAMPLE_BRIEF = (
    "An AI meeting-transcription company wants to buy multi-speaker English meeting audio "
    "with transcripts and speaker labels, at least 1,000 hours, licensed for model training."
)
LOGO = "assets/atom_logo.png"
ICON = "assets/atom_icon.png"  # square: a wide logo gets squeezed into the browser tab's favicon
HOLDER_LABELS = {"seller": "Seller", "collector": "Collector", "untapped": "Untapped holder", "public": "Public",
                 "technology": "Technology", "unrelated": "Unrelated", None: "Unknown"}
SELLING_LABELS = {"open_to_it": "open to selling", "not_selling": "not selling", "unclear": "selling unclear"}
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
        st.session_state.session_name = draft.brief.description
        st.rerun()


def _help(cat: Category) -> str:
    used = ", ".join(CODE_LABELS[c] for c in cat.used_for)
    return f"{cat.code} · used for {used}" + (f". {cat.help}" if cat.help else "")


def category_input(cat: Category, b: Brief, fid: int, picks: dict[str, Pick]) -> Pick:
    """One category: its choices (pills, a multiselect, a number and unit, or years) and an
    importance selector. Picks the model inferred carry an "inferred" caption until changed."""
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
        importance = st.segmented_control(
            "Importance", list(IMPORTANCE_LABELS), default=old.importance, format_func=IMPORTANCE_LABELS.get,
            key=f"{key}-importance", label_visibility="collapsed") or "any"
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
    st.caption("Importance: **Must have** caps the fit when evidence contradicts it · **Nice to have** is "
               "weighted into the score · **Any** is ignored. Unknown evidence stays neutral.")

    name = st.text_input("Session name", st.session_state.get("session_name") or b.description, key=f"{fid}-name")
    description = st.text_input("What data, in a few words", b.description, key=f"{fid}-description",
                                help="Used in every search, e.g. multi-speaker meeting audio")
    picks: dict[str, Pick] = {}
    for section in SECTIONS:
        if section.modalities and not set(picks["modality"].values) & set(section.modalities):
            continue  # add-ons appear once their modality is ticked
        title = section.title if section.modalities else f"{section.code}. {section.title}"
        with st.expander(title, expanded=section.code in ("A", "B", "C") or bool(section.modalities)):
            for cat in section.categories:
                picks[cat.key] = category_input(cat, b, fid, picks)
            if section.code == "D":
                for flag in transfer_flags(picks["region"].values, picks["processing"].values):
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


def licence_label(c: Candidate) -> str:
    if c.research:
        return f"checked: {c.research.licence}"
    if c.holder_type in (None, "unrelated"):
        return "?"
    return f"prior: {prior_label(c)}"


def type_label(c: Candidate) -> str:
    """e.g. "Seller + collects", "Untapped holder · Kept by law · open to selling"."""
    label = HOLDER_LABELS[c.holder_type]
    if c.holder_type == "seller" and c.collects_to_order:
        label += " + collects"
    if c.holder_type == "untapped":
        label += "".join(f" · {x}" for x in (c.archetype and ARCHETYPE_BY_KEY[c.archetype].label,
                                             c.selling_status == "open_to_it" and SELLING_LABELS["open_to_it"]) if x)
    return label


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
    for flag in transfer_flags(s.brief.regions, s.brief.chosen("processing")):
        st.warning(flag)
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
    show_excluded = right.toggle("Show excluded", key=f"excluded-{key}",
                                 help="Unrelated companies, dead websites, and ones a must-have filter rules out")
    min_volume = left.select_slider(
        "Minimum volume", [1, 2, 3, 4, 5], 1, key=f"volume-{key}",
        format_func=lambda v: "Any" if v == 1 else f"{v} or more",
        help="Volume score, 1 to 5 against the volume wanted from one seller")
    languages = mid.multiselect(
        "Languages", s.brief.languages, key=f"languages-{key}", placeholder="Any",
        help="Only companies with evidence for at least one of these") if s.brief.languages else []
    hide_unknown = right.toggle("Known volume", key=f"unknown-volume-{key}",
                                help="Only companies whose volume is known. Most stay unknown until researched.")
    kinds_present = [a for a in ARCHETYPES if any(c.archetype == a.key for c in s.discovery.candidates)]
    kinds = mid.multiselect(
        "Why untapped holders have it", [a.key for a in kinds_present], key=f"archetypes-{key}", placeholder="Any",
        format_func=lambda k: ARCHETYPE_BY_KEY[k].label,
        help="Narrows untapped holders to these reasons; other types are unaffected") if kinds_present else []

    def shown(c: Candidate) -> bool:
        if c.excluded:
            return show_excluded
        volume = c.scores.volume
        return (c.holder_type in types
                and (c.fit is None or c.fit >= min_fit)
                and (not languages or any(lang in c.languages for lang in languages))
                and (not kinds or c.holder_type != "untapped" or c.archetype in kinds)
                and (volume >= min_volume if volume is not None else not hide_unknown))

    rows = [c for c in s.discovery.candidates if shown(c)]
    if not rows:
        st.caption("No candidates match these filters.")
        return
    event = st.dataframe(
        pd.DataFrame([
            {
                "Fit": fmt(c.fit),
                "Company": c.name,
                "Type": type_label(c),
                "Licence": licence_label(c),
                "Data match": fmt(c.scores.data_match),
                "Coverage": fmt(c.scores.coverage),
                "Volume": fmt(c.scores.volume),
                **{label: fact_text(c, k) for k, label in FACT_LABELS.items()},
                "Languages and regions": ", ".join(c.languages + c.regions),
                "Reason": c.reason,
                "Must-haves not met": ", ".join(CATEGORIES[k].label for k in must_failures(c, s.brief)),
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
            "Price": st.column_config.TextColumn(help=f"Published price of the data. {FACT_HELP}"),
            "Data years": st.column_config.TextColumn(help=f"When the data was recorded. {FACT_HELP}"),
            "Founded": st.column_config.TextColumn(help=f"Year the company was founded, and its age. {FACT_HELP}"),
            "Must-haves not met": st.column_config.TextColumn(
                help="Must-have categories the evidence contradicts; each caps the fit"),
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
    st.download_button("Download candidates CSV", pipeline.candidates_csv(s.discovery.candidates, s.brief),
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
        with st.expander(f"{c.name} — fit {fmt(c.fit)} — licence {r.licence}"):
            accepted = s.brief.active("licence")
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
            if c.holder_type == "untapped":
                st.markdown(f"**Selling:** {SELLING_LABELS.get(c.selling_status, 'unclear')}")
                for ev in r.selling_evidence:
                    st.markdown(f"> {ev.quote}\n\n[{ev.url}]({ev.url})")
            if r.checks:
                st.markdown("**Checks against the brief**")
            for ch in r.checks:
                st.markdown(f"{VERDICT_ICONS[ch.verdict]} **{CATEGORIES[ch.category].label}** "
                            f"({s.brief.value_text(ch.category)}): {ch.finding}")
                for ev in ch.evidence:
                    st.markdown(f"> {ev.quote}\n\n[{ev.url}]({ev.url})")
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
