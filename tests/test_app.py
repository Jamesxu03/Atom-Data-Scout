"""Streamlit page with the Gemini-backed steps stubbed out (no Gemini, no network)."""

import time
from datetime import date

from streamlit.testing.v1 import AppTest

from tests.test_hypotheses import BRIEF, brief_with
from worker import pipeline, sessions
from worker.brief_template import UNCLEAR, BriefDraft, Pick
from worker.discovery import Discovery, Hit
from worker.schemas import (
    Answer,
    Candidate,
    Contact,
    EmailDraft,
    Evidence,
    Fact,
    Research,
    Saleability,
    Scores,
)
from worker.steps import brief as brief_step
from worker.steps import email as email_step

CUSTOMERS_OWN = Evidence(quote="Customers own the recordings they make", url="https://otter.ai/terms")
OTTER_RESEARCH = Research(
    data_held="customer meeting recordings", licence=UNCLEAR, licence_evidence=[],
    saleability=Saleability(
        exists=Answer(score=5, reason="kept until the customer deletes it", verified=True, evidence=[
            Evidence(quote="Recordings are kept until you delete them", url="https://otter.ai/privacy")]),
        allowed=Answer(score=2, reason="its customers own the recordings", verified=True, evidence=[CUSTOMERS_OWN]),
        willing=Answer(score=None, reason="nothing found"),
        obstacle="customers own the recordings", path="needs its customers' consent"),
    volume_estimate="millions of meetings", volume_score=5,
    volume_evidence=[Evidence(quote="Over a billion meetings transcribed", url="https://otter.ai")],
)


def _candidates() -> list[Candidate]:
    def cand(name, domain, **kw):
        return Candidate(name=name, website=f"https://{domain}", domain=domain, channels=["product_search"],
                         source_urls=[], **kw)

    return [
        cand("EgoData", "egodata.example", reason="sells data", holder_type="ready", data_owner="company",
             scores=Scores(data_match=5, coverage=5, licence=4), fit=4.8),
        cand("Otter", "otter.ai", reason="AI notetaker", holder_type="untapped", data_owner="customers",
             saleability=Saleability(exists=Answer(score=4), allowed=Answer(score=4), willing=Answer(score=3)),
             languages=["English"], scores=Scores(data_match=5, coverage=5, licence=2, volume=5), fit=4.0,
             research=OTTER_RESEARCH,
             contacts=[Contact(email="privacy@otter.ai", role="Privacy / DPO inbox",
                               source_url="https://otter.ai/privacy", origin="website", verified=True)]),
        cand("Lowfit", "lowfit.example", reason="adjacent", holder_type="untapped", data_owner="company",
             scores=Scores(data_match=2, coverage=1, licence=3), fit=1.9),
        cand("Down", "down.example", reason="homepage unreachable (ConnectError)"),
        cand("Payroll Co", "payroll.example", reason="HR", holder_type=None, data_owner="unclear",
             scores=Scores(data_match=1), fit=1.0, excluded=True),
    ]


def _fake_search(session, progress=print, **_):
    progress("Hypotheses")
    time.sleep(0.2)
    session.discovery = Discovery(
        hits=[Hit(channel="product_search", name="x", website="https://x.com", source_url="s")] * 6,
        candidates=_candidates())
    session.meta.stage = "done"
    session.meta.timings = {"Hypotheses": 0.2, "total": 0.2}
    sessions.save(session)
    return session


def _button(at, label):
    return next(b for b in at.button if b.label.startswith(label))


def test_brief_form_search_and_one_ranked_table(monkeypatch):
    draft = BriefDraft(brief=brief_with(region=Pick(values=["Middle East (GCC)"], source="inferred"),
                                        processing=Pick(values=["UK"])),
                       questions=["Do accented speakers count?"])
    monkeypatch.setattr(brief_step, "draft_brief", lambda text: draft)
    monkeypatch.setattr(pipeline, "search", _fake_search)
    at = AppTest.from_file("../app.py", default_timeout=30).run()

    assert at.title[0].value == "Atom Data Scout"
    _button(at, "Refine brief").click().run()
    assert not at.exception
    assert "Do accented speakers count?" in at.info[0].value  # open questions above the form
    languages = next(m for m in at.multiselect if m.label == "Languages and dialects")
    assert languages.value == ["English"] and "Gulf Arabic" in languages.options  # suggested from the region
    assert [c.value for c in at.caption].count("inferred — not stated in the brief") == 1
    assert any("PDPL" in w.value for w in at.warning)  # Gulf origin, processed in the UK
    titles = [e.label for e in at.expander]
    assert "Audio/speech details" in titles and "Video/image details" not in titles  # add-ons follow A1

    modality = at.button_group(key=f"{at.session_state.form_id}-modality")
    modality.set_value(["Audio/speech", "Video"]).run()
    assert "Video/image details" in [e.label for e in at.expander]
    modality.set_value(["Audio/speech"]).run()

    _button(at, "Save and search").click().run()
    for _ in range(40):
        time.sleep(0.25)
        at.run()
        if at.metric:
            break

    assert not at.exception
    assert [(m.label, m.value) for m in at.metric] == [
        ("Found", "6"), ("Unique", "5"), ("Screened", "4"), ("Relevant", "2"), ("Researched", "1"),
        ("With contacts", "1"),
    ]
    assert "Multi-speaker meeting audio" in at.info[0].value  # the saved brief's summary
    (table,) = [df for df in at.dataframe if "Fit" in df.value.columns]
    # one table by fit: below-threshold and excluded hidden, unknown fit last
    assert list(table.value["Company"]) == ["EgoData", "Otter", "Down"]
    assert list(table.value["Fit"]) == ["4.8", "4.0", "?"]
    # Otter: exists 5 and allowed 2 verified by research, willing 3 still the screen's
    assert list(table.value["Saleability"])[:2] == ["prior: offered, licence not checked", "Low (initial)"]
    assert list(table.value["Type"])[:2] == ["Ready source", "Untapped"]
    assert list(table.value["Obstacle → how to buy"])[:2] == [
        "", "customers own the recordings → needs its customers' consent"]
    research = _button(at, "Research selected")
    assert research.label == "Research selected (0)" and research.disabled
    assert any("not legal advice" in c.value for c in at.caption)

    # the session is listed in the sidebar and reopens from disk
    ((path, meta),) = sessions.list_all()
    assert meta.relevant == 2 and meta.stage == "done"
    saved = sessions.load(path).brief
    assert saved.pick("region").source == "inferred" and saved.pick("modality").importance == "must"
    assert any(b.label.startswith(f"**{meta.name}**") for b in at.sidebar.button)


def test_reopen_session_research_panel_and_email(monkeypatch):
    s = sessions.create("Meeting audio", BRIEF)
    s.discovery = Discovery(hits=[], candidates=_candidates())
    s.meta.stage = "done"
    sessions.save(s)
    monkeypatch.setattr(email_step, "draft_email", lambda brief, c, contact: EmailDraft(
        to=contact.email, subject="Meeting recordings", body="Dear team, ..."))

    at = AppTest.from_file("../app.py", default_timeout=30).run()
    next(b for b in at.sidebar.button if b.label.startswith("**Meeting audio**")).click().run()
    assert not at.exception
    assert [e.label for e in at.expander if e.label.startswith("Otter")] == [
        "Otter — fit 4.0 — Low (initial)"]
    assert any("Customers own the recordings they make" in m.value for m in at.markdown)
    assert any(m.value.startswith("- **Allowed?** 2/5 (verified)") for m in at.markdown)
    assert any(m.value.startswith("- **Willing?** 3/5 (initial)") for m in at.markdown)
    assert any(m.value == "**How to buy it:** needs its customers' consent" for m in at.markdown)

    _button(at, "Draft email").click().run()
    assert not at.exception
    assert at.text_input(key=f"subject-{s.path.name}-otter.ai").value == "Meeting recordings"
    saved = sessions.load(s.path).discovery.candidates[1]
    assert saved.email == EmailDraft(to="privacy@otter.ai", subject="Meeting recordings", body="Dear team, ...")

    at.text_input(key=f"subject-{s.path.name}-otter.ai").input("Licensing meeting recordings").run()
    assert sessions.load(s.path).discovery.candidates[1].email.subject == "Licensing meeting recordings"

    _button(at, "New session from this brief").click().run()
    assert next(m for m in at.multiselect if m.label == "Languages and dialects").value == ["English"]
    assert at.text_input[0].value == "Meeting audio"
    assert at.text_input[1].value == "multi-speaker meeting audio"


def test_language_and_volume_filters_and_price_and_age_columns():
    cands = _candidates()
    cands[0].price = Fact(value="$30 per audio hour", quote="Meeting audio costs $30 per audio hour",
                          url="https://egodata.example")
    cands[1].founded = Fact(value="2016", quote="Otter was founded in 2016 in California", url="https://otter.ai")
    s = sessions.create("Meeting audio", BRIEF)
    s.discovery = Discovery(hits=[], candidates=cands)
    s.meta.stage = "done"
    sessions.save(s)
    key = s.path.name

    at = AppTest.from_file("../app.py", default_timeout=30).run()
    next(b for b in at.sidebar.button if b.label.startswith("**Meeting audio**")).click().run()

    def table():
        return next(df for df in at.dataframe if "Fit" in df.value.columns).value

    assert list(table()["Company"]) == ["EgoData", "Otter", "Down"]
    assert list(table()["Price"]) == ["$30 per audio hour", "?", "?"]
    assert list(table()["Founded"])[1] == f"2016 ({date.today().year - 2016} years)"

    at.multiselect(key=f"languages-{key}").set_value(["English"]).run()
    assert list(table()["Company"]) == ["Otter"]  # the only one with evidence for English
    at.multiselect(key=f"languages-{key}").set_value([]).run()

    at.select_slider(key=f"volume-{key}").set_value(4).run()
    assert list(table()["Company"]) == ["EgoData", "Otter", "Down"]  # unknown volume still shown
    at.toggle(key=f"unknown-volume-{key}").set_value(True).run()
    assert list(table()["Company"]) == ["Otter"]
    assert not at.exception


def test_tool_vendors_hidden_until_toggled():
    cands = _candidates()
    cands.append(Candidate(name="Gong", website="https://gong.io", domain="gong.io", channels=["product_search"],
                           source_urls=[], reason="call recorder", holder_type="untapped", data_owner="customers",
                           vendor=True, scores=Scores(data_match=5), fit=2.0))
    s = sessions.create("Vendors", BRIEF)
    s.discovery = Discovery(hits=[], candidates=cands)
    s.meta.stage = "done"
    sessions.save(s)

    at = AppTest.from_file("../app.py", default_timeout=30).run()
    next(b for b in at.sidebar.button if b.label.startswith("**Vendors**")).click().run()

    def table():
        return next(df for df in at.dataframe if "Fit" in df.value.columns).value

    assert "Gong" not in list(table()["Company"])
    at.toggle(key=f"vendors-{s.path.name}").set_value(True).run()  # shown despite fit below the minimum
    assert table().set_index("Company").loc["Gong", "Type"] == "Untapped · tool vendor"
    assert not at.exception
