"""Streamlit UI. Run: uv run streamlit run app.py

The pipeline runs in a background thread, so touching the page while it runs doesn't
restart or interrupt it; a fragment polls for progress once a second.
"""

import threading
import time
from dataclasses import dataclass, field

import pandas as pd
import streamlit as st

from worker import pipeline
from worker.config import SCORE_WEIGHTS

EXAMPLE_BRIEF = (
    "An AI meeting-transcription company wants to buy multi-speaker English meeting audio "
    "with transcripts and speaker labels, at least 1,000 hours, licensed for model training."
)

LOGO = "assets/atom_logo.png"

# Candidate tables, in the order they're shown (pipeline ranks vendors first too)
HOLDER_GROUPS = [
    ("Data vendors", {"vendor"}),
    ("By-product holders", {"by_product"}),
    ("Technology and hardware", {"technology"}),
    ("Other", {"unrelated", None}),  # None: homepage unreachable, never screened by the model
]


@dataclass
class Job:
    messages: list[str] = field(default_factory=list)
    output: pipeline.RunOutput | None = None
    error: str | None = None
    done: bool = False
    started: float = field(default_factory=time.time)
    finished: float | None = None

    @property
    def elapsed(self) -> int:
        return int((self.finished or time.time()) - self.started)


def start_job() -> None:
    if (job := st.session_state.get("job")) and not job.done:
        return  # already running
    job = Job()
    brief = st.session_state.brief

    def work():
        try:
            job.output = pipeline.run(brief, progress=job.messages.append)
        except Exception as e:
            job.error = f"{type(e).__name__}: {e}"
        finally:
            job.finished = time.time()
            job.done = True

    threading.Thread(target=work, daemon=True).start()
    st.session_state.job = job


st.set_page_config(page_title="Atom Data Scout", page_icon=LOGO, layout="wide")
st.logo(LOGO, size="large")
st.title("Atom Data Scout")

job: Job | None = st.session_state.get("job")
running = job is not None and not job.done

left, right = st.columns([1, 2])

with left:
    st.text_area("Data brief", EXAMPLE_BRIEF, height=180, key="brief")
    st.button(
        "Running…" if running else "Start search",
        type="primary",
        on_click=start_job,
        disabled=running or not st.session_state.brief.strip(),
    )

    @st.fragment(run_every=1 if running else None)
    def progress_panel():
        job: Job | None = st.session_state.get("job")
        if job is None:
            return
        if not job.done:
            with st.status(f"Running… {job.elapsed}s", expanded=True):
                for m in job.messages[-12:]:
                    st.write(m)
            return
        if st.session_state.get("rendered_job") is not job:
            st.session_state.rendered_job = job
            st.rerun()  # full rerun so the results column picks up the new output
        if job.error:
            st.error(f"Failed after {job.elapsed}s — {job.error}")
        else:
            with st.status(f"Done in {job.elapsed}s", state="complete", expanded=False):
                if job.output and job.output.timings:
                    st.dataframe(
                        pd.DataFrame(job.output.timings.items(), columns=["Step", "Seconds"]),
                        hide_index=True,
                    )
                for m in job.messages:
                    st.write(m)

    progress_panel()

output = job.output if job and job.done else st.session_state.get("last_output")
if job and job.done and job.output:
    st.session_state.last_output = job.output
if output is not None and not isinstance(output, pipeline.RunOutput):
    # made before a code change reloaded worker/: rebuild it so new fields get their defaults
    output = pipeline.RunOutput.model_validate(output.model_dump())

with right:
    if not output:
        st.info("Running — results appear here when done." if running else "Enter a brief and click Start search.")
    else:
        if output.partial:
            st.warning("The Gemini quota ran out part-way, so these results are incomplete.")
        if output.errors:
            with st.expander(f"{len(output.errors)} problems during the run"):
                for e in output.errors:
                    st.text(e)
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
        if not shown:
            st.caption("No candidates with this relevance.")
        for title, types in HOLDER_GROUPS:
            group = [c for c in shown if c.holder_type in types]
            if not group:
                continue
            st.markdown(f"**{title}** ({len(group)})")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Company": c.name,
                            "Relevance": c.relevance,
                            "Data owner": c.data_owner,
                            "Reason": c.relevance_reason,
                            "Channels": ", ".join(c.channels),
                            "Website": c.website,
                            "Found on": c.source_urls[0] if c.source_urls else None,
                            "Source checked": c.source_verified,
                        }
                        for c in group
                    ],
                ),
                hide_index=True,
                width="stretch",
                column_config={
                    "Data owner": st.column_config.TextColumn(
                        help="company: its own recordings; customers: needs their permission"
                    ),
                    "Website": st.column_config.LinkColumn(),
                    "Found on": st.column_config.LinkColumn(),
                    "Source checked": st.column_config.CheckboxColumn(
                        help="The source page was confirmed to list this company"
                    ),
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
            st.caption("Profile extraction and scoring aren't built yet.")
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
                    f"**Registry:** {p.registry or '—'} ({p.country or '—'}) · "
                    f"{p.registration_number or '—'} · {p.company_status or '—'} · "
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
