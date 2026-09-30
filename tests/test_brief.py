import json
from typing import get_args

from tests.test_hypotheses import BRIEF
from worker.brief_template import (
    CATEGORIES,
    COMMERCIAL,
    FILL_PARTS,
    OPEN_CONDITIONS,
    SECTIONS,
    BriefFillData,
    BriefFillDeal,
    Pick,
    from_legacy,
    suggested_languages,
    transfer_flags,
)
from worker.steps.brief import draft_brief


def _fill(**picks) -> dict:
    """Both halves of the fill, every category not_set except those given."""
    extra = {"description": "multi-speaker meeting audio", "notes": "", "questions": ["Which dialects?"]}
    out = {}
    for schema in FILL_PARTS:
        data = {k: v for k, v in extra.items() if k in schema.model_fields}
        for key in schema.model_fields:
            if key in CATEGORIES:
                data[key] = {"status": "not_set", **picks.get(key, {})}
        out[schema] = schema.model_validate(data)
    return out


def test_stated_values_kept_inferred_flagged_and_never_required(fake_ask):
    fake_ask.responses |= _fill(
        modality={"status": "must", "values": ["Audio/speech"]},
        languages={"status": "nice", "values": ["Gulf Arabic", "English"]},
        region={"status": "inferred", "values": ["Middle East (GCC)"], "other": ["Oman"]},
        volume={"status": "must", "amount": 2_000_000, "unit": "data points"},
        speakers={"status": "inferred", "values": ["3 to 5"]},
        budget={"status": "not_set", "value": "Under £10k"},
    )
    text = "Arturo wants 2 million data points of Gulf Arabic meeting audio."
    draft = draft_brief(text)
    b = draft.brief

    assert b.description == "multi-speaker meeting audio"
    assert b.pick("modality") == Pick(values=["Audio/speech"], importance="must", source="stated")
    assert b.pick("languages").values == ["Gulf Arabic", "English"] and b.pick("languages").importance == "nice"
    # inferred: nice to have, never must
    assert b.pick("region") == Pick(values=["Middle East (GCC)", "Oman"], importance="nice", source="inferred")
    assert b.pick("speakers").source == "inferred"
    assert (b.pick("volume").amount, b.pick("volume").unit, b.must("volume")) == (2_000_000, "data points", True)
    assert "budget" not in b.picks  # not_set is dropped even if a value came back
    assert draft.questions == ["Which dialects?"]
    assert {c.schema for c in fake_ask.calls} == set(FILL_PARTS)  # two calls, one per half
    assert all(text in c.prompt and '"inferred"' in c.prompt and "- speakers: AU1" in c.prompt
               for c in fake_ask.calls)


def test_schema_offers_only_the_template_choices():
    data, deal = BriefFillData.model_json_schema(), BriefFillDeal.model_json_schema()
    speakers = data["$defs"]["Pick_speakers"]["properties"]["values"]["items"]["enum"]
    assert speakers == list(CATEGORIES["speakers"].choices)
    assert set(data["properties"]) | set(deal["properties"]) >= set(CATEGORIES)  # every category, once
    assert not set(data["properties"]) & set(deal["properties"])
    statuses = get_args(BriefFillData.model_fields["modality"].annotation.model_fields["status"].annotation)
    assert statuses == ("must", "nice", "inferred", "not_set")


def test_every_category_is_well_formed():
    codes = [c.code for s in SECTIONS for c in s.categories]
    assert len(codes) == len(set(codes)) and len(CATEGORIES) == len(codes)
    for cat in CATEGORIES.values():
        assert set(cat.used_for) <= set("SFRVE"), cat.key
        assert set(cat.neutral) <= set(cat.choices), cat.key
        assert cat.choices or cat.pick in ("text", "years"), cat.key


def test_summary_groups_by_importance_and_use():
    s = BRIEF.summary()
    assert s.startswith("Multi-speaker meeting audio. Must have — Modality: Audio/speech. Nice to have — ")
    assert "Volume target: 1,000 hours" in s and "Speakers per recording: 3 to 5" in s
    assert "Volume target" not in BRIEF.summary("S")  # C1 isn't used for search
    email = BRIEF.model_copy(update={"picks": {**BRIEF.picks, "budget": Pick(values=["Under £10k"], importance="any")}})
    assert "Also — Budget: Under £10k" in email.summary("E")  # the email uses values whatever the importance
    assert "Budget" not in email.summary("SFR")


def test_add_ons_only_for_the_modality_picked():
    assert [s.code for s in BRIEF.modality_sections()] == ["AUDIO"]
    video = BRIEF.model_copy(update={"picks": {**BRIEF.picks, "modality": Pick(values=["Video"])}})
    assert [s.code for s in video.modality_sections()] == ["VISUAL"]
    assert "Speakers per recording" not in video.summary()


def test_language_suggestions_and_transfer_flags():
    assert "Gulf Arabic" in suggested_languages(["Middle East (GCC)"])
    (saudi,) = transfer_flags(["Middle East (GCC)"], ["UK"])
    assert "PDPL" in saudi
    assert any("China" in f for f in transfer_flags(["china"], []))
    assert transfer_flags(["EU/EEA"], ["EU/EEA"]) == [] and transfer_flags(["EU/EEA"], ["US"])
    assert transfer_flags(["UK"], ["US"]) == []


def test_legacy_brief_is_converted():
    old = json.loads('{"data_type": "meeting audio", "features": ["multi-speaker"], "scenarios": [], '
                     '"languages": ["English"], "regions": ["UK"], "label_types": ["transcripts"], '
                     '"volume": {"amount": 1000.0, "unit": "hours"}, "formats": [], "licence": "training_only", '
                     '"holder_types": ["vendor", "public", "by_product", "technology"], "exclusions": [], '
                     '"must_have": ["languages"], "notes": ""}')
    b = from_legacy(old)
    assert b.description == "meeting audio" and b.must("languages") and not b.must("region")
    assert b.pick("volume").amount == 1000 and b.active("licence") == [COMMERCIAL, OPEN_CONDITIONS, "Public domain / Open"]
    assert "sources" not in b.picks  # all four accepted = no preference
    assert "features: multi-speaker" in b.notes
