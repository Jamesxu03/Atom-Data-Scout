"""Companies: the clients searches are run for. Each client's page has a board of its searches,
dragged between stages, with what outreach has come to and the latest activity."""

from collections import defaultdict

import pandas as pd
import streamlit as st

from ui.board import Card, board
from ui.labels import ago
from ui.nav import SEARCH_PAGE
from worker import crm, sessions

ACTIVITY_LABELS = {"sent": "✉ Emailed", "reply": "↩ Reply from", "call": "☎ Call with", "note": "✎ Note",
                   "stage": "→ Moved"}
SEARCHING = ("searching", "confirming")


def plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}es" if word.endswith("h") else f"{n} {word}s"


def open_client(client_id: str | None) -> None:
    if client_id:
        st.query_params["client"] = client_id
    else:
        st.query_params.pop("client", None)


def _new_client() -> None:
    name = st.session_state.get("new-client-name", "").strip()
    if not name:
        return
    client = crm.create_client(name, st.session_state.get("new-client-note", ""))
    st.session_state["new-client-name"] = st.session_state["new-client-note"] = ""
    open_client(client.id)


def _assign(session: str, widget: str) -> None:
    if client_id := st.session_state.get(widget):
        crm.assign_search(session, client_id)


def companies_page() -> None:
    title, new = st.columns([4, 1], vertical_alignment="bottom")
    title.subheader("Companies")
    title.caption("The clients searches are run for. Open one to see its searches and outreach.")
    with new.popover("New client", icon=":material/add:", width="stretch"):
        st.text_input("Client name", key="new-client-name", placeholder="e.g. Otter AI")
        st.text_area("Note", key="new-client-note", height=80, placeholder="What they buy, who to talk to")
        st.button("Add client", type="primary", on_click=_new_client)

    all_sessions = {p.name: meta for p, meta in sessions.list_all()}
    placed = defaultdict(list)
    for pl in crm.placements():
        if pl.session in all_sessions:
            placed[pl.client_id].append(pl.session)
    found = crm.clients()
    if not found:
        st.info("No clients yet. Add one, then run searches for it from Search.")
    for row in range(0, len(found), 3):
        for col, client in zip(st.columns(3), found[row:row + 3], strict=False):
            stats = crm.stats(crm.deals(client.id))
            with col.container(border=True):
                st.markdown(f"#### {client.name}")
                if client.note:
                    st.caption(client.note)
                st.markdown(f"{plural(len(placed[client.id]), 'search')} · {stats.contacted} contacted · "
                            f"{stats.replied} replied")
                st.caption(f"Last activity {ago(stats.last_activity)}" if stats.last_activity else "No outreach yet")
                st.button("Open", key=f"client-{client.id}", on_click=open_client, args=(client.id,), width="stretch")

    unassigned = [(name, meta) for name, meta in all_sessions.items()
                  if name not in {s for names in placed.values() for s in names}]
    if unassigned and found:
        with st.expander(f"Searches without a client ({len(unassigned)})"):
            st.caption("Put each on its client's board to track its outreach.")
            names = {c.id: c.name for c in found}
            for name, meta in unassigned:
                text, pick = st.columns([3, 2], vertical_alignment="center")
                text.markdown(f"**{meta.name}**  \n{meta.created:%d %b %Y, %H:%M} · {meta.relevant} relevant")
                widget = f"assign-{name}"
                pick.selectbox("Client", list(names), index=None, key=widget, format_func=names.get,
                               placeholder="Assign to a client", label_visibility="collapsed",
                               on_change=_assign, args=(name, widget))


def _search_card(name: str, meta: sessions.SessionMeta, stage: str, found: list[crm.Deal]) -> Card:
    stats = crm.stats(found)
    lines = [f"Created {meta.created:%d %b %Y}", f"{meta.relevant} relevant · {len(meta.selected)} researched"]
    if meta.parent:
        lines.append(f"Compare of {meta.parent_name}")
    if found:
        lines.append(f"{stats.contacted} emailed · {stats.replied} replied")
    last_sent = max((d.last_sent for d in found if d.last_sent), default=None)
    chip, tone = "", ""
    if meta.stage in SEARCHING:
        chip, tone = "Searching…", "muted"
    elif last_sent:
        chip = f"✉ {ago(last_sent)}"
        tone = "good" if stats.replied else "warn" if any(crm.follow_up_due(d) for d in found) else ""
    return Card(id=name, column=stage, title=meta.name, lines=lines,
                badge=str(stats.replied) if stats.contacted else "", badge_help="Companies that replied",
                chip=chip, tone=tone)


def client_page(client_id: str) -> None:
    client = crm.get_client(client_id)
    st.button("← All companies", on_click=open_client, args=(None,), type="tertiary")
    if client is None:
        st.warning("This client doesn't exist (any more).")
        return
    title, new = st.columns([4, 1], vertical_alignment="bottom")
    title.subheader(client.name)
    if new.button(f"New search for {client.name}", type="primary", width="stretch"):
        st.session_state.draft = None
        st.switch_page(SEARCH_PAGE, query_params={"client": client.id})
    with st.expander("Note", expanded=not client.note):
        st.text_area("Note", client.note, key=f"note-{client.id}", height=80, label_visibility="collapsed",
                     placeholder="What they buy, who to talk to",
                     on_change=lambda: crm.set_note(client.id, st.session_state[f"note-{client.id}"]))

    metas = {p.name: meta for p, meta in sessions.list_all()}
    placed = [p for p in crm.placements(client.id) if p.session in metas]
    found = crm.deals(client.id)
    by_search = defaultdict(list)
    for d in found:
        by_search[d.session].append(d)
    stats = crm.stats(found)
    cols = st.columns(5)
    cols[0].metric("Searches", len(placed))
    cols[1].metric("Contacted", stats.contacted, help="Companies emailed")
    cols[2].metric("Replied", stats.replied)
    cols[3].metric("Reply rate", f"{stats.replied / stats.contacted:.0%}" if stats.contacted else "—")
    cols[4].metric("Calls", stats.calls)

    st.markdown("**Searches**")
    if not placed:
        st.caption(f"No searches for {client.name} yet: start one with *New search for {client.name}*, or assign "
                   "an existing one under Companies.")
        return
    cards = [_search_card(p.session, metas[p.session], crm.search_stage(p, by_search[p.session]),
                          by_search[p.session]) for p in placed]
    move, opened = board(crm.SEARCH_STAGES, cards, key=f"searches-{client.id}",
                         footers={s: plural(n, "search") for s in crm.SEARCH_STAGES
                                  if (n := sum(c.column == s for c in cards))})
    st.caption("Drag a search to another stage; click one to open it. A search follows its outreach until you "
               "move it: Outreach once an email is sent, In talks once someone replies.")
    if move:
        crm.move_search(move.id, move.column, move.order)
        st.rerun()
    if opened in metas:
        st.switch_page(SEARCH_PAGE, query_params={"s": opened})

    activity = crm.recent(client.id)
    if activity:
        st.markdown("**Latest activity**")
        st.dataframe(pd.DataFrame([
            {"When": f"{a.at:%d %b %Y} ({ago(a.at)})", "What": ACTIVITY_LABELS[a.kind],
             "Who": a.contact or "", "Company": company,
             "Detail": a.body if a.kind == "stage" else (a.subject or a.body)[:120],
             "Search": metas[search].name if search in metas else search}
            for a, company, search in activity
        ]), hide_index=True, width="stretch")
