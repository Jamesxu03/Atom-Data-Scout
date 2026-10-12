"""The Outreach tab: a search's researched companies on a board (To contact, Emailed, Replied, ...),
each company's emails, replies and calls in a dialog, and the outreach so far in a table.

Outreach belongs to the search's client (worker/crm.py), so a company another search for the
same client found keeps its history here. Nothing is sent from the app: the user sends the draft
from their own mailbox and marks it sent, and logs replies and calls as they come.
"""

from datetime import datetime, time
from pathlib import Path

import pandas as pd
import streamlit as st

from ui.board import Card, board
from ui.company_view import contacts_section, email_section, research_details
from ui.labels import ago, fmt, type_label
from worker import crm, sessions
from worker.schemas import Candidate
from worker.scoring import saleability_label

LOG_KINDS = {"sent": "Email sent", "reply": "Reply received", "call": "Call", "note": "Note"}
OUTCOMES = {None: "—", "interested": "Interested", "not_now": "Not now", "declined": "Declined"}
ICONS = {"sent": "✉", "reply": "↩", "call": "☎", "note": "✎", "stage": "→"}
OPEN = "deal-open-"  # + session folder: the domain whose dialog is open


def stage_text(d: crm.Deal) -> str:
    return f"{d.stage} · {ago(d.last_activity)}" if d.last_activity else d.stage


def outreach_labels(s: sessions.Session, path: str) -> dict[str, str]:
    """domain -> where its outreach stands, e.g. "Emailed · 3 days ago"; empty without a client."""
    cid = crm.client_of(Path(path).name)
    if not cid:
        return {}
    return {d.domain: stage_text(d) for d in crm.deals(cid, [c.domain for c in s.discovery.candidates])}


def _close_dialogs() -> None:
    for k in [k for k in st.session_state if str(k).startswith(OPEN)]:
        del st.session_state[k]


def _open_picked(name: str, widget: str) -> None:
    st.session_state[OPEN + name] = st.session_state[widget]
    st.session_state[widget] = None


def best_contact(c: Candidate) -> str:
    contacts = sorted(c.contacts, key=lambda k: (not k.verified, not k.email, not k.name))
    if not contacts:
        return ""
    k = contacts[0]
    return " — ".join(x for x in (k.name, k.role) if x) or k.email or k.phone or ""


def _card(c: Candidate, d: crm.Deal | None, s: sessions.Session) -> Card:
    lines = [type_label(c), f"Saleability: {saleability_label(c, s.brief)}"]
    if d and d.last_contact:
        lines.append(f"To: {d.last_contact}")
    else:
        lines.append(f"Contact: {who}" if (who := best_contact(c)) else "No contact found")
    chip, tone = "", ""
    if d and d.last_reply and (d.last_sent is None or d.last_reply >= d.last_sent):
        chip, tone = f"↩ Replied {ago(d.last_reply)}", "good"
    elif d and d.last_sent:
        chip, tone = f"✉ {ago(d.last_sent)}", "warn" if crm.follow_up_due(d) else ""
    elif c.email:
        chip, tone = "Email drafted", "muted"
    return Card(id=c.domain, column=d.stage if d else crm.TO_CONTACT, title=c.name, lines=lines, badge=fmt(c.fit),
                badge_help="Fit, 1 to 5", chip=chip, tone=tone)


def outreach_tab(s: sessions.Session, path: str, locked: bool) -> None:
    name = Path(path).name
    cid = crm.client_of(name)
    if not cid:
        st.info("Put this search on a client's board (Client, above) to track who you email and who replies.")
        return
    deals = {d.domain: d for d in crm.deals(cid, [c.domain for c in s.discovery.candidates])}
    shown = [c for c in s.discovery.candidates if c.research or c.domain in deals]
    if not shown:
        st.caption("Research companies under Candidates first: researched ones show here, ready to contact.")
        return
    by_domain = {c.domain: c for c in shown}
    shown.sort(key=lambda c: (deals[c.domain].position if c.domain in deals else float("inf"), -(c.fit or 0)))
    cards = [_card(c, deals.get(c.domain), s) for c in shown]
    move, opened = board(crm.DEAL_STAGES, cards, key=f"deals-{name}",
                         footers={stage: f"{n} {'company' if n == 1 else 'companies'}" for stage in crm.DEAL_STAGES
                                  if (n := sum(c.column == stage for c in cards))})
    if move and move.id in by_domain:
        crm.move_deal(cid, name, move.id, by_domain[move.id].name, move.column, move.order)
        st.rerun()
    if opened in by_domain:
        st.session_state[OPEN + name] = opened
    st.caption("Drag a company to another stage, or click it to see what was sent and log a reply or a call. "
               "Marking an email sent moves it to Emailed, a reply to Replied. Amber: emailed "
               f"{crm.FOLLOW_UP_DAYS}+ days ago with no reply.")
    widget = f"deal-pick-{name}"
    st.selectbox("Open a company", list(by_domain), index=None, key=widget, format_func=lambda d: by_domain[d].name,
                 placeholder="Find a company on the board", on_change=_open_picked, args=(name, widget))
    outreach_table(shown, deals, name)
    if (domain := st.session_state.get(OPEN + name)) in by_domain:
        deal_dialog(s, by_domain[domain], path, cid, locked)


def outreach_table(shown: list[Candidate], deals: dict[str, crm.Deal], name: str) -> None:
    rows = [(c, deals[c.domain]) for c in shown if c.domain in deals]
    if not rows:
        return
    st.markdown("**Outreach so far**")
    df = pd.DataFrame([
        {"Company": c.name, "Stage": d.stage, "Emailed": d.last_contact, "Emails sent": d.sent,
         "Last sent": f"{d.last_sent:%d %b %Y}" if d.last_sent else "",
         "Replied": f"{d.last_reply:%d %b %Y}" if d.last_reply else "", "Calls": d.calls,
         "Last activity": ago(d.last_activity), "Follow-up due": crm.follow_up_due(d), "Website": c.website}
        for c, d in rows
    ])
    st.dataframe(df, hide_index=True, width="stretch", column_config={
        "Follow-up due": st.column_config.CheckboxColumn(help=f"Emailed {crm.FOLLOW_UP_DAYS}+ days ago, no reply"),
        "Website": st.column_config.LinkColumn()})
    st.download_button("Download outreach CSV", df.to_csv(index=False), "outreach.csv", "text/csv",
                       key=f"outreach-csv-{name}")


@st.dialog("Outreach", width="large", on_dismiss=_close_dialogs)
def deal_dialog(s: sessions.Session, c: Candidate, path: str, cid: str, locked: bool) -> None:
    name = Path(path).name
    deal = next(iter(crm.deals(cid, [c.domain])), None)
    current = deal.stage if deal else crm.TO_CONTACT
    st.subheader(c.name)
    st.caption(f"[{c.domain}]({c.website}) · {type_label(c)} · fit {fmt(c.fit)} · {saleability_label(c, s.brief)}")
    stage = st.selectbox("Stage", crm.DEAL_STAGES, index=crm.DEAL_STAGES.index(current),
                         key=f"stage-{name}-{c.domain}-{current}")  # the stage in the key: a move elsewhere resets it
    if stage != current:
        crm.move_deal(cid, name, c.domain, c.name, stage)
        st.rerun()
    history = crm.timeline(deal.id) if deal else []
    email, log, timeline, research = st.tabs(["Email", "Log", f"Timeline ({len(history)})", "Research"])
    with email:
        contacts_section(c, path)
        email_section(s, c, path, locked)
    with log:
        log_form(c, name, cid, deal)
    with timeline:
        timeline_list(history, f"{name}-{c.domain}")
    with research:
        if c.research:
            research_details(c, s.brief)
        else:
            st.caption("Not researched yet.")


def _when(day) -> datetime:
    today = crm.now()
    return today if day == today.date() else datetime.combine(day, time(12)).astimezone()


def log_form(c: Candidate, name: str, cid: str, deal: crm.Deal | None) -> None:
    """Log an email sent outside the app, a reply, a call or a note."""
    emails = [k.email for k in c.contacts if k.email]
    default = (deal.last_contact if deal else "") or (c.email.to if c.email and c.email.to else "") or \
        (emails[0] if emails else "")
    with st.form(f"log-{name}-{c.domain}", clear_on_submit=True, border=False):
        kind = st.radio("What happened", list(LOG_KINDS), format_func=LOG_KINDS.get, horizontal=True)
        left, right = st.columns(2)
        day = left.date_input("When", crm.now().date(), max_value=crm.now().date(), format="DD/MM/YYYY")
        contact = right.text_input("With", default, help="Their email address or name")
        subject = st.text_input("Subject", placeholder="For emails")
        body = st.text_area("Text", height=140, placeholder="Paste the email or reply, or note what was said")
        outcome = st.selectbox("Outcome", list(OUTCOMES), format_func=OUTCOMES.get, help="For replies and calls")
        if st.form_submit_button("Log", type="primary"):
            crm.log(cid, name, c.domain, c.name, kind, _when(day), contact=contact, subject=subject.strip(),
                    body=body.strip(), outcome=outcome)
            st.rerun()


def timeline_list(history: list[crm.Activity], key: str) -> None:
    if not history:
        st.caption("Nothing yet. Mark the email sent once you've sent it, or log a reply or call.")
        return
    for a in history:
        with st.container(border=True):
            head, undo = st.columns([6, 1], vertical_alignment="center")
            what = {"stage": "Moved", **LOG_KINDS}[a.kind]
            outcome = f" · {OUTCOMES[a.outcome]}" if a.outcome else ""
            head.markdown(f"{ICONS[a.kind]} **{what}**{outcome} · {a.at:%d %b %Y, %H:%M} ({ago(a.at)})")
            undo.button("Delete", key=f"del-{key}-{a.id}", type="tertiary", on_click=crm.delete_activity, args=(a.id,),
                        help="Remove this entry. The stage stays where it is.")
            if a.contact:
                st.caption(f"{'To' if a.kind == 'sent' else 'With'}: {a.contact}")
            if a.subject:
                st.text(f"Subject: {a.subject}")
            if a.body and a.kind != "stage":
                st.text(a.body)
            elif a.kind == "stage":
                st.caption(a.body)
