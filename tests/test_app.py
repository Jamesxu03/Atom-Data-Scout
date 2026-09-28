"""Streamlit page with pipeline.run stubbed out (no Gemini, no network)."""

import time

from streamlit.testing.v1 import AppTest

from worker import pipeline
from worker.schemas import Candidate, Contact, Funnel


def _fake_run(brief, progress=print, **_):
    progress("Analyse brief")
    time.sleep(0.2)
    cands = [
        Candidate(name="Otter", website="https://otter.ai", domain="otter.ai",
                  channels=["product_search", "directory"], source_urls=["https://g2.com/x"],
                  source_verified=True, relevance="yes", relevance_reason="AI notetaker",
                  contacts=[Contact(email="privacy@otter.ai", role="Privacy / DPO inbox",
                                    source_url="https://otter.ai/privacy", origin="website", verified=True)]),
        Candidate(name="Payroll Co", website="https://payroll.example", domain="payroll.example",
                  channels=["product_search"], source_urls=[], relevance="no", relevance_reason="HR"),
    ]
    return pipeline.RunOutput(
        funnel=Funnel(found=3, deduped=2, relevant=1, profiled=1, with_contacts=1),
        candidates=cands, timings={"Analyse brief": 0.2, "total": 0.2},
    )


def test_run_from_page(monkeypatch):
    monkeypatch.setattr(pipeline, "run", _fake_run)
    at = AppTest.from_file("../app.py", default_timeout=30).run()

    assert at.title[0].value == "Atom Data Scout"
    assert not at.toggle  # no demo switch
    assert at.button[0].label == "Start search"

    at.button[0].click().run()
    assert at.button[0].label == "Running…" and at.button[0].disabled
    for _ in range(40):
        time.sleep(0.25)
        at.run()
        if at.metric:
            break

    assert not at.exception
    assert [(m.label, m.value) for m in at.metric] == [
        ("Found", "3"), ("After dedupe", "2"), ("Relevant", "1"), ("Profiled", "1"), ("With contacts", "1"),
    ]
    assert len(at.dataframe[1].value) == 1  # candidates table hides "no" by default
    assert [h.value for h in at.subheader] == ["Candidates", "Contacts"]
    assert at.button[0].label == "Start search"
