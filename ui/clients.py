"""Picking the client a search is for, and adding one on the spot."""

import streamlit as st

from worker import crm


def _create(key: str) -> None:
    name = st.session_state.get(f"{key}-new", "").strip()
    if name:
        st.session_state[key] = crm.create_client(name).id
        st.session_state[f"{key}-new"] = ""


def client_picker(key: str, default: str | None = None, label: str = "Client", tip: str | None = None) -> str | None:
    """A client, or None for none yet; with a New client popover next to it."""
    found = {c.id: c.name for c in crm.clients()}
    if key not in st.session_state or st.session_state[key] not in (None, *found):  # first time, or deleted
        st.session_state[key] = default if default in found else None
    pick, new = st.columns([3, 1], vertical_alignment="bottom")
    chosen = pick.selectbox(label, [None, *found], key=key, help=tip,
                            format_func=lambda cid: "No client yet" if cid is None else found[cid])
    with new.popover("New", icon=":material/add:", width="stretch", help="Add a client"):
        st.text_input("Client name", key=f"{key}-new", placeholder="e.g. Otter AI")
        st.button("Add", key=f"{key}-add", type="primary", on_click=_create, args=(key,))
    return chosen
