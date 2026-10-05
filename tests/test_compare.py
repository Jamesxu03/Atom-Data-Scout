from tests.test_hypotheses import BRIEF, brief_with
from worker import compare, sessions
from worker.brief_template import COMMERCIAL, READY, RESTRICTED, Pick
from worker.config import MUST_HAVE_CAP
from worker.discovery import Discovery
from worker.schemas import Candidate, Check, Evidence, Research, Scores
from worker.scoring import is_relevant, rescore

STRICT = brief_with(licence=Pick(values=[COMMERCIAL], importance="must"),
                    recency=Pick(values=["Last 2 years"], importance="must"))
UNTAPPED = "Untapped holders (have the data, not selling it)"


def _research(licence=RESTRICTED, **kw) -> Research:
    return Research(data_held="meeting audio", licence=licence, licence_evidence=[], volume_estimate="2,000 hours",
                    volume_score=4, volume_evidence=[], **kw)


def _cand(name, domain, **kw) -> Candidate:
    return Candidate(name=name, website=f"https://{domain}", domain=domain, channels=["marketplace"],
                     source_urls=[], reason="sells audio", holder_type="ready", data_owner="company",
                     languages=["English"], scores=Scores(data_match=5), **kw)


def _source(brief=STRICT, cands=None) -> sessions.Session:
    s = sessions.create("Meeting audio", brief)
    s.discovery = Discovery(hits=[], candidates=cands or [
        _cand("Corpus Co", "corpus.example", research=_research()),
        _cand("Unresearched", "unresearched.example"),
    ])
    for c in s.discovery.candidates:
        rescore(c, brief)
    s.meta.stage = "done"
    sessions.save(s)
    return sessions.load(s.path)


def _files(s: sessions.Session) -> dict[str, str]:
    return {p.name: p.read_text() for p in s.path.iterdir()}


def test_relaxing_a_strict_licence_is_exact_and_nothing_is_written_until_saved():
    source = _source()
    on_disk = _files(source)
    assert source.discovery.candidates[1].fit == MUST_HAVE_CAP  # research-only licence; D1 commercial, must

    relaxed = brief_with(licence=Pick(values=[COMMERCIAL, RESTRICTED], importance="must"),
                         recency=Pick(values=["Last 2 years"], importance="must"))
    p = compare.preview(source, relaxed)
    assert [c.domain for c in p.candidates] == ["corpus.example"]  # researched only
    assert p.candidates[0].fit > 4 and p.provisional == {}  # exact: D1 is judged in Python
    assert p.fit_before == {"corpus.example": MUST_HAVE_CAP} and p.unsaved
    (change,) = p.changes
    assert change.key == "licence" and change.exact and change.after.endswith("(must)")
    assert _files(source) == on_disk and len(sessions.list_all()) == 1  # a preview writes nothing

    assert not compare.preview(source, source.brief).unsaved
    s = sessions.load(compare.save(source, p, "Meeting audio · compare").path)
    assert [c.domain for c in s.discovery.candidates] == ["corpus.example"] and s.discovery.candidates[0].fit > 4
    assert s.meta.parent == source.path.name and s.meta.parent_name == "Meeting audio"
    assert s.meta.parent_fit == {"corpus.example": MUST_HAVE_CAP} and s.meta.changes == p.changes
    assert _files(source) == on_disk


def test_a_changed_value_makes_the_fit_provisional_but_a_must_switch_does_not():
    failed = Check(category="recency", verdict="fails", finding="recorded in 2015",
                   evidence=[Evidence(quote="recorded in 2015", url="https://corpus.example")])
    source = _source(cands=[_cand("Corpus Co", "corpus.example", research=_research(COMMERCIAL, checks=[failed])),
                            _cand("Other", "other.example", research=_research(COMMERCIAL))])
    corpus = next(c for c in source.discovery.candidates if c.domain == "corpus.example")
    assert corpus.fit == MUST_HAVE_CAP

    switched = compare.preview(source, brief_with(licence=STRICT.picks["licence"],
                                                  recency=Pick(values=["Last 2 years"])))
    c = switched.get("corpus.example")
    assert c.research.checks[0].verdict == "fails" and c.fit > MUST_HAVE_CAP  # no longer a must: no cap
    assert [ch.exact for ch in switched.changes] == [True] and switched.provisional == {}

    widened = compare.preview(source, brief_with(licence=STRICT.picks["licence"],
                                                 recency=Pick(values=["Last 5 years"], importance="must")))
    c = widened.get("corpus.example")
    assert c.research.checks[0].verdict == "unclear"  # judged against "Last 2 years": unknown now
    assert c.research.checks[0].finding == "Checked against the earlier brief (Last 2 years): recorded in 2015"
    assert c.fit > MUST_HAVE_CAP
    assert [(ch.key, ch.exact) for ch in widened.changes] == [("recency", False)]
    assert widened.provisional == {"corpus.example": ["Recency check was set aside"]}  # per company
    assert widened.rank_before == {"other.example": 1, "corpus.example": 2}
    assert widened.rank_now == {"other.example": 1, "corpus.example": 2}  # a tie: the stored order


def test_data_match_languages_must_switches_volume_and_source_types():
    source = _source(BRIEF, [_cand("Corpus Co", "corpus.example", research=_research(COMMERCIAL)),
                             _cand("Rival", "rival.example", research=_research(COMMERCIAL))])
    edited = brief_with(volume=Pick(amount=50_000, unit="hours"), languages=Pick(values=["Gulf Arabic"]),
                        annotation=Pick(values=["Rich labels"], importance="must"),
                        sources=Pick(values=[UNTAPPED], importance="must"), exclusions=Pick(values=["rival.example"]))
    edited = edited.model_copy(update={"description": "meeting audio with transcripts"})
    p = compare.preview(source, edited)
    corpus = p.get("corpus.example")
    assert corpus.scores.volume is None and corpus.research.volume_score is None  # scored against 1,000 hours
    assert corpus.languages == []  # English was evidence for the old brief only
    assert corpus.contradicts == ["sources"] and corpus.fit == MUST_HAVE_CAP  # a ready source, untapped wanted
    assert p.provisional["corpus.example"] == [
        "Data match was screened against the earlier what data",
        "New languages and dialects weren't looked for",
        "Minimum annotation as a must-have wasn't checked by the screen",
        "Volume was scored against the earlier target",
    ]
    assert p.kills == {"rival.example": compare.EXCLUDED} and p.get("rival.example").killed
    assert p.candidates[-1].domain == "rival.example" and "rival.example" not in p.rank_now
    assert {ch.key: ch.exact for ch in p.changes} == {
        "description": False, "languages": False, "annotation": False, "volume": False, "sources": True,
        "exclusions": True}

    saved = compare.save(source, p, "x")
    back = compare.preview(saved, brief_with(sources=Pick(values=[READY], importance="must")))
    assert back.get("corpus.example").contradicts == []  # accepted again
    assert not back.get("rival.example").killed and back.unsaved  # no longer excluded
    assert back.fit_before == p.fit_before  # still from the original search
    assert "Volume was scored against the earlier target" in back.provisional["corpus.example"]  # still unknown
    assert [ch.key for ch in back.changes] == ["sources"]  # the rest is back to the original brief

    by_hand = compare.save(source, compare.preview(source, source.brief, {"rival.example": "too small"}), "y")
    assert compare.preview(by_hand, by_hand.brief).get("rival.example").killed  # saved kills carry over


def test_kills_are_working_state_until_saved():
    source = _source(BRIEF, [_cand("Corpus Co", "corpus.example", research=_research(COMMERCIAL)),
                             _cand("Second", "second.example", research=_research(COMMERCIAL))])
    p = compare.preview(source, source.brief, {"corpus.example": "too small"})
    assert p.unsaved and [c.domain for c in p.candidates] == ["second.example", "corpus.example"]  # ranked last
    killed = p.get("corpus.example")
    assert killed.killed and killed.kill_reason == "too small" and not is_relevant(killed)
    assert not any(c.killed for c in sessions.load(source.path).discovery.candidates)

    s = sessions.load(compare.save(source, p, "x").path)
    assert s.meta.relevant == 1 and compare.saved_kills(s) == {"corpus.example": "too small"}
    restored = compare.preview(s, s.brief, {})
    assert restored.unsaved and not restored.get("corpus.example").killed


def test_search_matches_every_word_anywhere():
    c = _cand("Corpus Co", "corpus.example", research=_research(), regions=["UK"])
    assert compare.matches(c, "corpus") and compare.matches(c, "uk meeting AUDIO") and compare.matches(c, "")
    assert compare.matches(c, "sells") and not compare.matches(c, "corpus video")
