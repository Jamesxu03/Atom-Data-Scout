from worker.brief_template import COMMERCIAL, OPEN, OPEN_CONDITIONS, Brief, Pick
from worker.schemas import HolderHypothesis
from worker.steps.hypotheses import HypothesisList, hypotheses

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
    assert "in the words these companies use" in call.prompt
