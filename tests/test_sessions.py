import time

from tests.test_hypotheses import BRIEF
from worker import sessions
from worker.discovery import Discovery, Hit, merge, snowball
from worker.schemas import EmailDraft, FoundList, HolderHypothesis, Research


def test_create_list_and_reload(tmp_sessions):
    first = sessions.create("Gulf meeting audio", BRIEF, "brief text")
    time.sleep(0.01)
    second = sessions.create("Gulf meeting audio", BRIEF)  # same minute, same name: own folder
    assert first.path != second.path
    assert first.path.name.endswith("-gulf-meeting-audio") and first.path.parent == tmp_sessions

    hits = [Hit(channel="product_search", name="Otter", website="https://otter.ai", source_url="s")]
    first.discovery = Discovery(hits=hits, candidates=merge(hits))
    first.hypotheses = [HolderHypothesis(category=f"c{i}", rationale="r", search_queries=["q"]) for i in range(5)]
    first.meta.stage = "done"
    sessions.save(first)

    assert [p for p, _ in sessions.list_all()] == [second.path, first.path]  # newest first
    loaded = sessions.load(first.path)
    assert loaded.brief == BRIEF and loaded.meta.brief_text == "brief text" and loaded.meta.stage == "done"
    assert loaded.hypotheses == first.hypotheses
    assert loaded.discovery.candidates[0].domain == "otter.ai"
    assert {p.name for p in first.path.iterdir()} == {"session.json", "brief.json", "hypotheses.json",
                                                       "discovery.json"}


def test_merge_keeps_research_and_email_through_snowball(fake_ask):
    s = sessions.create("x", BRIEF)
    hits = [Hit(channel="product_search", name="Otter", website="https://otter.ai", source_url="s")]
    s.discovery = Discovery(hits=hits, candidates=merge(hits))
    otter = s.discovery.candidates[0]
    otter.research = Research(data_held="d", licence="Unclear", licence_evidence=[], volume_estimate=None,
                              volume_score=None, volume_evidence=[])
    otter.email = EmailDraft(to="a@otter.ai", subject="s", body="b")
    sessions.save(s)

    fake_ask.responses[FoundList] = FoundList(companies=[])
    reloaded = sessions.load(s.path)
    d = snowball(reloaded.discovery, [("Otter", "https://otter.ai")], progress=lambda _: None)
    (again,) = d.candidates
    assert again.research == otter.research and again.email == otter.email


def test_session_saved_before_the_template_still_opens(tmp_sessions):
    import json

    s = sessions.create("old", BRIEF)
    (s.path / "brief.json").write_text(json.dumps({
        "data_type": "meeting audio", "features": [], "scenarios": [], "languages": ["English"], "regions": [],
        "label_types": [], "volume": {"amount": None, "unit": None}, "formats": [], "licence": "unknown",
        "holder_types": [], "exclusions": [], "must_have": [], "notes": ""}))
    hits = [Hit(channel="product_search", name="Otter", website="https://otter.ai", source_url="s")]
    d = Discovery(hits=hits, candidates=merge(hits)).model_dump(mode="json")
    d["candidates"][0]["research"] = {"data_held": "d", "licence_status": "sellable", "licence_evidence": [],
                                      "volume_estimate": None, "volume_score": None, "volume_evidence": []}
    (s.path / "discovery.json").write_text(json.dumps(d))
    loaded = sessions.load(s.path)
    assert loaded.brief.description == "meeting audio" and loaded.brief.languages == ["English"]
    assert loaded.discovery.candidates[0].research.licence == "Commercial, non-exclusive"
