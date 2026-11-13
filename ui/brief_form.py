"""The brief as free text, refined by Gemini into the form; Save and search creates a session."""

import streamlit as st

from ui.clients import client_picker
from ui.jobs import start_search
from ui.nav import open_session, set_draft
from worker import crm, sessions
from worker.brief_template import (
    CODE_LABELS,
    SECTIONS,
    Brief,
    BriefDraft,
    Category,
    Pick,
    suggested_languages,
    transfer_flags,
)
from worker.steps import brief as brief_step

EXAMPLE_BRIEF = (
    "An AI meeting-transcription company wants to buy multi-speaker English meeting audio "
    "with transcripts and speaker labels, at least 1,000 hours, licensed for model training."
)


def brief_page() -> None:
    if (cid := st.query_params.get("client")) and (client := crm.get_client(cid)):
        st.caption(f"New search for **{client.name}**")
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

    client_id = client_picker("brief-client", st.query_params.get("client"),
                              tip="Who the search is for: it goes on their board under Companies, with its outreach")
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
    if client_id:
        crm.assign_search(s.path.name, client_id)
    open_session(str(s.path))
    start_search(str(s.path))
    st.rerun()
