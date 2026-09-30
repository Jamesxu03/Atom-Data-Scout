from worker.schemas import Brief, HolderHypothesis, Volume
from worker.steps.hypotheses import HypothesisList, hypotheses

BRIEF = Brief(
    data_type="multi-speaker meeting audio",
    features=["multi-speaker"],
    scenarios=["meetings"],
    languages=["English"],
    regions=[],
    label_types=["transcript", "speaker labels"],
    volume=Volume(amount=1000, unit="hours"),
    formats=[],
    licence="training_only",
    holder_types=["vendor", "public", "by_product", "technology"],
    exclusions=[],
    must_have=[],
)


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
    assert "Multi-speaker meeting audio" in call.prompt
    assert "Volume: at least 1,000 hours" in call.prompt  # the brief's summary
    assert "Never use dataset language" in call.prompt
    assert "in the words these companies use" in call.prompt
