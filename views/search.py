"""Search: a new brief (text, then the form), or a past search when the URL names one (?s=)."""

import streamlit as st

from ui.brief_form import brief_page, form_page
from ui.nav import current_path
from ui.session_view import session_page

if path := current_path():
    session_page(path)
elif draft := st.session_state.get("draft"):
    form_page(draft)
else:
    brief_page()
