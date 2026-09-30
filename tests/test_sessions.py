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
    otter.research = Research(data_held="d", licence_status="unknown", licence_evidence=[], volume_estimate=None,
                              volume_score=None, volume_evidence=[])
    otter.email = EmailDraft(to="a@otter.ai", subject="s", body="b")
    sessions.save(s)

    fake_ask.responses[FoundList] = FoundList(companies=[])
    reloaded = sessions.load(s.path)
    d = snowball(reloaded.discovery, [("Otter", "https://otter.ai")], progress=lambda _: None)
    (again,) = d.candidates
    assert again.research == otter.research and again.email == otter.email
