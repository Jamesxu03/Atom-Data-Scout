"""One researched company: what research found, with quotes, its contacts and the outreach email."""

from collections.abc import Callable
from pathlib import Path

import pandas as pd
import streamlit as st

from ui.labels import (
    FACT_LABELS,
    LEGAL_NOTE,
    QUESTION_LABELS,
    TRIGGER_LABELS,
    VERDICT_ICONS,
    fact_text,
)
from worker import crm, sessions
from worker.brief_template import CATEGORIES, Brief
from worker.schemas import Candidate, Contact
from worker.scoring import answers, is_verified, saleability_label
from worker.steps import email as email_step


def _update_candidate(path: str, domain: str, change: Callable[[Candidate], None]) -> None:
    s = sessions.load(path)
    change(next(c for c in s.discovery.candidates if c.domain == domain))
    sessions.save(s)


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
        if client_id := crm.client_of(Path(path).name):  # on the client's outreach board from now on
            crm.ensure_deal(client_id, Path(path).name, c.domain, c.name)
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
    download, sent = st.columns(2)
    download.download_button("Download email", f"To: {c.email.to or ''}\nSubject: {subject}\n\n{body}",
                             f"{c.domain}-email.txt", "text/plain", key=f"download-{key}", width="stretch")
    client_id = crm.client_of(Path(path).name)
    if sent.button("Mark as sent", key=f"sent-{key}", icon=":material/send:", disabled=client_id is None,
                   width="stretch", help="Once you've sent it from your mailbox: logs it on the client's outreach "
                                         "board and moves the company to Emailed" if client_id else
                   "Put this search on a client's board first"):
        crm.log(client_id, Path(path).name, c.domain, c.name, "sent", contact=c.email.to or "", subject=subject,
                body=body)
        st.rerun()
    st.caption("Nothing is sent automatically: send it from your mailbox, then mark it sent.")
