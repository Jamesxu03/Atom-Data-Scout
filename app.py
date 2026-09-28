"""Streamlit UI. Run: uv run streamlit run app.py"""

import pandas as pd
import streamlit as st

from worker import pipeline
from worker.config import SCORE_WEIGHTS

EXAMPLE_BRIEF = (
    "An AI meeting-transcription company wants to buy multi-speaker English meeting audio "
    "with transcripts and speaker labels, at least 1,000 hours, licensed for model training."
)

LOGO = "assets/atom_logo.png"

st.set_page_config(page_title="Atom Data Scout", page_icon=LOGO, layout="wide")
st.logo(LOGO, size="large")
st.image(LOGO, width=180)
st.title("Data Scout")

left, right = st.columns([1, 2])

with left:
    brief = st.text_area("Data brief", EXAMPLE_BRIEF, height=180)
    demo = st.toggle("Demo mode (fake data, no API calls)", value=True)
    go = st.button("Start search", type="primary", disabled=not brief.strip())

if go:
    with left, st.status("Running pipeline…", expanded=True) as status:
        try:
            st.session_state.results = pipeline.run(brief, demo=demo, progress=st.write)
            status.update(label="Done", state="complete")
        except Exception as e:
            status.update(label="Failed", state="error")
            st.error(f"{type(e).__name__}: {e}")

results = st.session_state.get("results")

with right:
    if not results:
        st.info("Enter a brief and click Start search.")
    else:
        table = pd.DataFrame(
            [
                {
                    "Company": r.profile.name,
                    "Total": r.score.total,
                    **{d.replace("_", " ").title(): getattr(r.score, d) for d in SCORE_WEIGHTS},
                    "Status": r.profile.company_status,
                    "Website": r.profile.website,
                }
                for r in results
            ]
        )
        st.dataframe(
            table,
            hide_index=True,
            width="stretch",
            column_config={"Website": st.column_config.LinkColumn()},
        )
        st.download_button(
            "Download CSV", pipeline.to_csv(results), "results.csv", "text/csv"
        )

        for r in results:
            p, s = r.profile, r.score
            with st.expander(f"{p.name} — {s.total}"):
                st.markdown(
                    f"**Companies House:** {p.company_number or '—'} · {p.company_status or '—'} · "
                    f"incorporated {p.incorporated_on or '—'} · {p.registered_address or '—'}"
                )
                claims = [
                    ("Records data", p.records_data, p.records_data_evidence),
                    ("Retention", p.retention, p.retention_evidence),
                    ("Policy allows transfer", p.policy_allows_transfer, p.policy_allows_transfer_evidence),
                    ("Policy allows training", p.policy_allows_training, p.policy_allows_training_evidence),
                ]
                st.markdown("**Evidence**")
                for label, value, evidence in claims:
                    st.markdown(f"- **{label}:** {'unknown' if value is None else value}")
                    for ev in evidence:
                        st.markdown(f"  > {ev.quote}  \n  [{ev.url}]({ev.url})")
                st.markdown("**Score reasons**")
                for dim, reason in s.reasons.items():
                    st.markdown(f"- {dim}: {getattr(s, dim)} — {reason}")
                st.markdown("**Email draft**")
                st.code(r.email_draft, language=None)
