"""Pages in other scripts, per-step settings, grounding queries, the archetype prior and the eval report."""

from types import SimpleNamespace

from eval import settings
from eval.discovery_quality import GoldRow, report, run_metrics
from eval.discovery_recall import _norm_name
from tests.test_hypotheses import BRIEF
from worker import config, llm, sessions
from worker.discovery import Discovery
from worker.discovery.channels import mention_at, name_variants
from worker.schemas import Answer, Candidate, Confirmation, RoundStats, Saleability, Scores
from worker.scoring import licence_score, split
from worker.verify import quote_in_text, word_count


def test_quotes_in_scripts_without_spaces_are_counted_by_characters():
    ja = "当社はコールセンター向けの通話録音サービスを提供しています。"
    assert quote_in_text("当社はコールセンター向けの通話録音サービスを提供しています", ja)
    assert not quote_in_text("通話録音", ja)  # too short to prove anything, like a 2-word English quote
    assert quote_in_text("我们为客服中心提供通话录音服务", "我们为客服中心提供通话录音服务，已累计记录一千万通电话。")
    assert quote_in_text("نحن نقدم خدمة تسجيل المكالمات لمراكز الاتصال", "نحن نقدم خدمة تسجيل المكالمات لمراكز الاتصال في")
    assert word_count("Acme 通話録音サービス") == 1 + 8 // 2


def test_names_in_any_script_are_found_on_pages():
    assert name_variants("株式会社アドバンスト・メディア") == ["株式会社アドバンスト メディア", "アドバンスト メディア"]
    assert mention_at("導入事例：アドバンスト・メディア様", "株式会社アドバンスト・メディア", "https://advanced-media.co.jp") >= 0
    assert mention_at("مركز اتصال شركة الاتصالات السعودية", "شركة الاتصالات السعودية", "https://stc.com.sa") >= 0
    assert mention_at("別の会社の事例です", "株式会社アドバンスト・メディア", "https://advanced-media.co.jp") == -1
    assert _norm_name("株式会社アクメ") != _norm_name("株式会社ベータ")  # no longer both ""


def test_step_settings_and_grounding_queries(monkeypatch):
    assert llm.step("hypotheses") == {"model": config.MODEL, "thinking": "medium"}
    assert llm.step("screen")["thinking"] == "low"
    step = SimpleNamespace(type="google_search_call", arguments=SimpleNamespace(queries=["a", "b"]))
    other = SimpleNamespace(type="model_output")
    assert llm.search_queries(SimpleNamespace(steps=[step, other, step])) == ["a", "b", "a", "b"]
    assert llm.search_queries(SimpleNamespace()) == []
    monkeypatch.setattr(config, "STEP_MODELS", dict(config.STEP_MODELS))
    monkeypatch.setattr(config, "STEP_THINKING", dict(config.STEP_THINKING))
    settings.apply("D")
    assert llm.step("hypotheses")["model"] == config.PRO_MODEL and llm.step("discovery")["thinking"] == "medium"
    assert llm.step("confirm") == {"model": config.MODEL, "thinking": "low"}  # pages already fetched: Flash


def _cand(domain, **kw):
    return Candidate(name=domain, website=f"https://{domain}", domain=domain, channels=[], source_urls=[],
                     reason="r", **kw)


def test_dormant_archives_start_with_a_better_licence_prior():
    dormant = _cand("a.com", holder_type="untapped", data_owner="company", archetype="dormant")
    service = _cand("b.com", holder_type="untapped", data_owner="company", archetype="service")
    customers = _cand("c.com", holder_type="untapped", data_owner="customers", archetype="dormant")
    assert [licence_score(c, BRIEF) for c in (dormant, service)] == [4, 3]
    assert licence_score(customers, BRIEF) == 2  # its customers' data needs their consent
    known = dormant.model_copy(update={"saleability": Saleability(exists=Answer(score=2), allowed=Answer(score=5),
                                                                 willing=Answer(score=None))})
    assert licence_score(known, BRIEF) == 2  # answers, once there are any, decide
    assert Candidate.model_validate({**dormant.model_dump(), "archetype": "by_product"}).archetype is None


def test_two_lists_and_the_quality_report():
    s = sessions.create("eval meeting_audio_en A run 1", BRIEF)
    cands = [_cand(f"h{i}.com", holder_type="untapped", fit=4.0 - i / 10, languages=["English"],
                   scores=Scores(data_match=4)) for i in range(3)]
    cands += [_cand("v.com", holder_type="untapped", fit=2.0, vendor=True), _cand("r.com", holder_type="ready", fit=4.5),
              _cand("x.com", fit=None)]
    cands[1].confirmation = Confirmation(verdict="confirmed", reason="r")
    s.discovery = Discovery(hits=[], candidates=cands, rounds=[RoundStats(round=1, calls=20, queries=50)])
    s.meta.timings = {"total": 120}
    lists = split(cands)
    assert [c.domain for c in lists["untapped"]][:3] == ["h1.com", "h0.com", "h2.com"]  # confirmed first
    assert [c.domain for c in lists["ready"]] == ["r.com"] and [c.domain for c in lists[None]] == ["x.com"]

    gold = [GoldRow(brief="meeting_audio_en", name="H1", website="h1.com", fit="good", sellable="yes", found_by="manual"),
            GoldRow(brief="meeting_audio_en", name="Missed", website="missed.com", fit="good", sellable="maybe",
                    found_by="manual"),
            GoldRow(brief="meeting_audio_en", name="V", website="v.com", fit="poor", sellable="no", found_by="scout")]
    m = run_metrics(s, gold)
    assert (m["recall manual"], m["confirmed good finds"], m["known bad in a top 20"]) == (0.5, 1, 0)
    assert m["untapped: organisation share of top 20"] == 3 / 4  # the vendor is in the list, hidden by default
    assert (m["Google queries per discovery call"], m["minutes"]) == (2.5, 2)
    text = report("meeting_audio_en", [s], gold)
    assert "recall manual" in text and "50%" in text and "- Missed missed.com" in text
