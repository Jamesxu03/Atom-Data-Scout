from worker.schemas import DataSpec, HolderHypothesis
from worker.steps.hypotheses import HypothesisList, hypotheses

SPEC = DataSpec(
    data_type="multi-speaker meeting audio",
    languages=["English"],
    scenarios=["meetings"],
    needs_labels=True,
    label_types=["transcript", "speaker labels"],
    min_hours=1000,
    licence_needed="training_only",
)


def test_prompt_asks_for_product_language(fake_ask):
    fake_ask.responses[HypothesisList] = HypothesisList(
        hypotheses=[
            HolderHypothesis(category=f"cat {i}", rationale="r", search_queries=["q"])
            for i in range(5)
        ]
    )
    result = hypotheses(SPEC)

    assert len(result) == 5
    (call,) = fake_ask.calls
    assert "multi-speaker meeting audio" in call.prompt
    assert "Never use dataset language" in call.prompt
    assert "in the words these companies use" in call.prompt
