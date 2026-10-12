"""Streamlit UI. Run: uv run streamlit run app.py

Two pages in the top bar:

Search     Brief -> Refine (form) -> Save and search -> two lists, untapped holders and ready
           sources, each ranked by verdict and fit (shown while the best are confirmed) -> tick
           rows -> Research selected -> Outreach tab: a board of the researched companies (To
           contact, Emailed, Replied, ...) with what was sent and who replied -> Compare tab:
           the researched companies side by side under a working brief, saved as a new session.
Companies  The clients searches are run for (e.g. a meeting-transcription company buying audio):
           each client's page has a board of its searches, dragged between stages.

Each search is a session folder (worker/sessions.py) and every page reads it from disk, so any
past search reopens without rerunning; which one is open lives in the URL (?s=). Clients, the
boards and outreach are in data/crm.sqlite3 (worker/crm.py), apart from the session files a
running search rewrites.

Searches and research run in a background thread (ui/jobs.py), so touching the page doesn't
restart or interrupt them; a fragment polls for progress once a second, and reruns the page when
a search that is confirming saves new verdicts.
"""

import streamlit as st

from ui.nav import COMPANIES_PAGE, SEARCH_PAGE, sidebar

LOGO = "assets/atom_logo.png"
ICON = "assets/atom_icon.png"  # square: a wide logo gets squeezed into the browser tab's favicon

st.set_page_config(page_title="Atom Data Scout", page_icon=ICON, layout="wide")
st.logo(LOGO, size="large", icon_image=ICON)
page = st.navigation([
    st.Page(SEARCH_PAGE, title="Search", icon=":material/search:", default=True),
    st.Page(COMPANIES_PAGE, title="Companies", icon=":material/apartment:"),
], position="top")
st.title("Atom Data Scout")
sidebar()
page.run()
