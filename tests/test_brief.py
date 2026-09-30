from tests.test_hypotheses import BRIEF
from worker.schemas import BriefDraft, FieldOptions
from worker.steps.brief import draft_brief


def test_stated_values_kept_suggested_flagged_and_never_required(fake_ask):
    brief = BRIEF.model_copy(update={
        "regions": ["North America"],  # suggested by the model
        "must_have": ["languages", "regions", "volume", "not_a_field"],
    })
    fake_ask.responses[BriefDraft] = BriefDraft(
        brief=brief,
        suggested=["regions", "formats", "bogus"],
        options=[FieldOptions(field="languages", options=["British English", "American English"]),
                 FieldOptions(field="nope", options=["x"]), FieldOptions(field="formats", options=[])],
        questions=["Do accented speakers count?"],
    )
    text = "Multi-speaker English meeting audio, at least 1,000 hours, for training."
    draft = draft_brief(text)

    assert draft.brief.data_type == "multi-speaker meeting audio"
    assert draft.brief.languages == ["English"]
    assert draft.suggested == ["regions", "formats"]  # unknown names dropped
    assert draft.brief.must_have == ["languages", "volume"]  # suggested and unknown fields never required
    assert [o.field for o in draft.options] == ["languages"]
    assert draft.questions == ["Do accented speakers count?"]
    (call,) = fake_ask.calls
    assert text in call.prompt
    assert "Never a field you suggested" in call.prompt


def test_summary_reads_as_a_paragraph():
    s = BRIEF.model_copy(update={"regions": ["UK"], "exclusions": ["otter.ai"], "must_have": ["label_types"],
                                 "holder_types": ["vendor", "public"]}).summary()
    assert s.startswith("Multi-speaker meeting audio (multi-speaker). Languages: English. Regions: UK.")
    assert "Volume: at least 1,000 hours" in s and "Licence: model training only" in s
    assert "Sources accepted: data vendors, public sources" in s
    assert "Must have: label types" in s and "Skip: otter.ai" in s
