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
            st.session_state.output = pipeline.run(brief, demo=demo, progress=st.write)
            status.update(label="Done", state="complete")
        except Exception as e:
            status.update(label="Failed", state="error")
            st.error(f"{type(e).__name__}: {e}")

output = st.session_state.get("output")

with right:
    if not output:
        st.info("Enter a brief and click Start search.")
    else:
        f = output.funnel
        cols = st.columns(5)
        cols[0].metric("Found", f.found, help="Raw hits across all discovery channels")
        cols[1].metric("After dedupe", f.deduped, help="Unique domains")
        cols[2].metric("Relevant", f.relevant, help="Homepage screen said yes or maybe")
        cols[3].metric("Profiled", f.profiled, help="Deep-crawled (homepage, privacy, terms, contact)")
        cols[4].metric("With contacts", f.with_contacts, help="Profiled companies with a verified contact")

        st.subheader("Candidates")
        labels = st.multiselect("Relevance", ["yes", "maybe", "no"], default=["yes", "maybe"])
        shown = [c for c in output.candidates if c.relevance in labels]
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Company": c.name,
                        "Relevance": c.relevance,
                        "Reason": c.relevance_reason,
                        "Channels": ", ".join(c.channels),
                        "Website": c.website,
                        "Found on": c.source_urls[0] if c.source_urls else None,
                    }
                    for c in shown
                ],
                columns=["Company", "Relevance", "Reason", "Channels", "Website", "Found on"],
            ),
            hide_index=True,
            width="stretch",
            column_config={
                "Website": st.column_config.LinkColumn(),
                "Found on": st.column_config.LinkColumn(),
            },
        )
        st.download_button(
            "Download candidates CSV",
            pipeline.candidates_csv(output.candidates),
            "candidates.csv",
            "text/csv",
        )

        with_contacts = [c for c in output.candidates if c.contacts]
        if with_contacts:
            st.subheader("Contacts")
            only_verified = st.toggle("Verified only", value=False,
                                      help="Email/phone/name found on the source page when we checked it")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Company": c.name,
                            "Name": k.name,
                            "Role": k.role,
                            "Email": k.email,
                            "Phone": k.phone,
                            "Verified": k.verified,
                            "From": k.origin,
                            "Source": k.source_url,
                        }
                        for c in with_contacts
                        for k in c.contacts
                        if k.verified or not only_verified
                    ]
                ),
                hide_index=True,
                width="stretch",
                column_config={
                    "Source": st.column_config.LinkColumn(),
                    "Verified": st.column_config.CheckboxColumn(),
                },
            )
            st.download_button(
                "Download contacts CSV",
                pipeline.contacts_csv(output.candidates),
                "contacts.csv",
                "text/csv",
            )

        results = output.results
        if not results:
            st.caption("Profile extraction and scoring aren't wired into live runs yet.")
            st.stop()

        st.subheader("Ranked profiles")
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
            "Download results CSV", pipeline.to_csv(results), "results.csv", "text/csv"
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
                if r.contacts:
                    st.markdown("**Contacts**")
                    for k in r.contacts:
                        who = " — ".join(x for x in (k.name, k.role) if x) or "Contact"
                        how = " · ".join(x for x in (k.email, k.phone) if x) or "no email/phone published"
                        check = "✅ verified" if k.verified else "⚠️ unverified"
                        st.markdown(f"- **{who}**: {how} ({check}, [source]({k.source_url}))")
                st.markdown("**Score reasons**")
                for dim, reason in s.reasons.items():
                    st.markdown(f"- {dim}: {getattr(s, dim)} — {reason}")
                st.markdown("**Email draft**")
                st.code(r.email_draft, language=None)
