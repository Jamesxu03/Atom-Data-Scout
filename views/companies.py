"""Companies: every client, or one client's page with its board of searches (?client=)."""

import streamlit as st

from ui.client_view import client_page, companies_page

if client_id := st.query_params.get("client"):
    client_page(client_id)
else:
    companies_page()
