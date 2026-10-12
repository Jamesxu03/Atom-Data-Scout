"""Which search or client is open lives in the URL (?s=<session folder>, ?client=<id>), so a
browser refresh or a pasted link reopens it. The sidebar shows the running job and the most
recent searches; every search is also on its client's board under Companies."""

from pathlib import Path

import streamlit as st

from ui.jobs import busy, current_job
from worker import sessions
from worker.brief_template import BriefDraft

SEARCH_PAGE = "views/search.py"
COMPANIES_PAGE = "views/companies.py"
RECENT = 5  # searches listed in the sidebar


def current_path() -> str | None:
    """The open search's folder, from ?s=, if it is a session."""
    name = st.query_params.get("s")
    if not name or name != Path(name).name or name.startswith("."):  # a folder name, never a path
        return None
    path = sessions.SESSIONS_DIR / name
    return str(path) if (path / "session.json").exists() else None


def open_session(path: str) -> None:
    st.query_params["s"] = Path(path).name
    st.session_state.draft = None


def new_session() -> None:
    st.query_params.pop("s", None)
    st.session_state.draft = None


def new_from_brief(path: str) -> None:
    """Open the form prefilled with this session's brief; saving creates a new session."""
    s = sessions.load(path)
    set_draft(BriefDraft(brief=s.brief), s.meta.brief_text)
    st.query_params.pop("s", None)
    st.session_state.session_name = s.meta.name


def set_draft(draft: BriefDraft, text: str) -> None:
    st.session_state.draft = draft
    st.session_state.brief_copy = text
    st.session_state.form_id = st.session_state.get("form_id", 0) + 1  # fresh form widgets


def sidebar() -> None:
    with st.sidebar:
        if st.button("New search", type="primary", width="stretch"):
            new_session()
            st.switch_page(SEARCH_PAGE, query_params={k: v for k, v in st.query_params.items() if k == "client"})
        path = current_path()
        if path:
            st.button("New search from this brief", on_click=new_from_brief, args=(path,), width="stretch",
                      help="Opens the form with this brief filled in. Old results are never overwritten.")
        if busy():
            job = current_job()
            st.caption(f"Running {job.kind} for {Path(job.session_path).name} ({job.elapsed}s)")
        recent = sessions.list_all()[:RECENT]
        if recent:
            st.subheader("Recent searches")
            st.caption("Every search is on its client's board under Companies.")
        for p, meta in recent:
            if st.button(
                f"**{meta.name}**  \n{meta.created:%d %b %Y, %H:%M} · {meta.relevant} relevant"
                + (" · compare" if meta.parent else ""),
                key=f"open-{p.name}", width="stretch", type="secondary" if str(p) != path else "primary",
            ):
                st.switch_page(SEARCH_PAGE, query_params={"s": p.name})
