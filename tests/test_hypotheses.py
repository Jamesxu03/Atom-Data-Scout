from worker import archetypes
from worker.brief_template import COMMERCIAL, OPEN, OPEN_CONDITIONS, Brief, Pick
from worker.schemas import HolderHypothesis, LocalHooks, Market
from worker.steps.hypotheses import (
    ArchetypeHypotheses,
    HypothesisList,
    fill_archetypes,
    hypotheses,
    local_plan,
)

BRIEF = Brief(description="multi-speaker meeting audio", picks={
    "modality": Pick(values=["Audio/speech"], importance="must"),
    "domain": Pick(values=["Meetings/conversation"]),
    "languages": Pick(values=["English"]),
    "annotation": Pick(values=["Rich labels"]),
    "volume": Pick(amount=1000, unit="hours"),
    "licence": Pick(values=[COMMERCIAL, OPEN_CONDITIONS, OPEN]),
    "speakers": Pick(values=["3 to 5"]),
})


def brief_with(**picks: Pick | None) -> Brief:
    """BRIEF with some picks replaced; None removes one."""
    merged = {**BRIEF.picks, **picks}
    return BRIEF.model_copy(update={"picks": {k: p for k, p in merged.items() if p is not None}})


def test_prompt_asks_for_product_language(fake_ask):
    fake_ask.responses[HypothesisList] = HypothesisList(
        hypotheses=[
            HolderHypothesis(category=f"cat {i}", rationale="r", search_queries=["q"])
            for i in range(5)
        ]
    )
    result = hypotheses(BRIEF)

    assert len(result) == 5
    (call,) = fake_ask.calls
    assert "Multi-speaker meeting audio. Must have — Modality: Audio/speech" in call.prompt
    assert "Languages and dialects: English" in call.prompt
    assert "Volume target" not in call.prompt  # not used for search
    assert "Never use dataset language" in call.prompt
    assert "in the words these organisations use" in call.prompt
    assert "Search from the holder's side" in call.prompt and "At least 4" in call.prompt
    # vendor examples are now the bad ones, with why
    assert 'Bad: "AI meeting assistant that records and transcribes calls" (finds tools' in call.prompt
    assert call.thinking == "medium"


def _hyp(category, side):
    return HolderHypothesis(category=category, rationale="r", search_queries=["q"], side=side)


def test_organisations_come_first_then_regulated_then_vendors(fake_ask):
    fake_ask.responses[HypothesisList] = HypothesisList(hypotheses=[
        _hyp("Notetakers", "vendor"), _hyp("Banks", "regulated"), _hyp("Outsourcers", "organisation"),
        _hyp("Councils", "organisation"), _hyp("Trainers", "organisation")])
    assert [h.category for h in hypotheses(BRIEF)] == ["Outsourcers", "Councils", "Trainers", "Banks", "Notetakers"]
    # the side is required in the schema Gemini sees, though saved hypotheses without it still load
    schema = HypothesisList.model_json_schema()["$defs"]["HolderHypothesis"]
    assert {"side", "owner", "licence_route"} <= set(schema["required"]) and "market" not in schema["properties"]
    assert HolderHypothesis.model_validate({"category": "c", "rationale": "r", "search_queries": []}).side == \
        "organisation"


def test_local_plan_writes_native_queries_and_drops_vendors(fake_ask):
    fake_ask.responses[LocalHooks] = LocalHooks(
        hypotheses=[_hyp("Callcenter-Dienstleister", "organisation"), _hyp("Software", "vendor")],
        site_filter="site:.de", tender_portal="TED", retention_law="", associations=[], tools=["Gong", "NICE"])
    hooks = local_plan(BRIEF, Market(country="Germany", language="German", tld=".de"))
    assert [(h.category, h.market) for h in hooks.hypotheses] == [("Callcenter-Dienstleister", "Germany (German)")]
    (call,) = fake_ask.calls
    assert "searching Germany in German" in call.prompt and "Do not translate English" in call.prompt
    assert "通話録音" in call.prompt and "site:.de" in call.prompt


def test_fill_archetypes_asks_one_hypothesis_per_missing_reason(fake_ask):
    fake_ask.responses[ArchetypeHypotheses] = ArchetypeHypotheses(hypotheses=[_hyp("Dubbing studios", "organisation")])
    missing = [archetypes.BY_KEY["production_leftovers"], archetypes.BY_KEY["dormant"]]
    assert [h.category for h in fill_archetypes(BRIEF, missing, ["Acme: call centre"])] == ["Dubbing studios"]
    (call,) = fake_ask.calls
    assert "- Production leftovers:" in call.prompt and "unused takes at dubbing" in call.prompt
    assert "- Acme: call centre" in call.prompt
