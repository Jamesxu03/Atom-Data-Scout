import json
from typing import get_args

from tests.test_hypotheses import BRIEF
from worker.brief_template import (
    CATEGORIES,
    COLLECTION_OK,
    COMMERCIAL,
    EXISTING_ONLY,
    FILL_PARTS,
    OPEN_CONDITIONS,
    SECTIONS,
    Brief,
    BriefFill,
    Pick,
    from_legacy,
    suggested_languages,
    transfer_flags,
)
from worker.steps.brief import draft_brief


def _fill(**picks) -> dict:
    """The fill, every category not_set except those given."""
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
        deal_terms={"status": "not_set", "value": "Budget under £10k"},
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
    assert "deal_terms" not in b.picks  # not_set is dropped even if a value came back
    assert draft.questions == ["Which dialects?"]
    assert [c.schema for c in fake_ask.calls] == [BriefFill]  # the whole template in one call
    assert all(text in c.prompt and '"inferred"' in c.prompt and "- speakers: AU1" in c.prompt
               for c in fake_ask.calls)


def test_schema_offers_only_the_template_choices():
    data = BriefFill.model_json_schema()
    speakers = data["$defs"]["Pick_speakers"]["properties"]["values"]["items"]["enum"]
    assert speakers == list(CATEGORIES["speakers"].choices)
    assert set(data["properties"]) >= set(CATEGORIES)  # every category
    statuses = get_args(BriefFill.model_fields["modality"].annotation.model_fields["status"].annotation)
    assert statuses == ("must", "nice", "inferred", "not_set")


def test_every_category_is_well_formed():
    codes = [c.code for s in SECTIONS for c in s.categories]
    assert len(codes) == len(set(codes)) and len(CATEGORIES) == len(codes)
    for cat in CATEGORIES.values():
        assert set(cat.used_for) <= set("SFRVE"), cat.key
        assert set(cat.neutral) <= set(cat.choices), cat.key
        assert cat.choices or cat.pick in ("text", "years", "paragraph"), cat.key


def test_summary_groups_by_importance_and_use():
    s = BRIEF.summary()
    assert s.startswith("Multi-speaker meeting audio. Must have — Modality: Audio/speech. Nice to have — ")
    assert "Volume target: 1,000 hours" in s and "Speakers per recording: 3 to 5" in s
    assert "Volume target" not in BRIEF.summary("S")  # C1 isn't used for search
    terms = BRIEF.model_copy(update={"picks": {**BRIEF.picks, "deal_terms": Pick(values=["Sample first"])}})
    assert "Deal terms: Sample first" in terms.summary("E") and "Deal terms" in terms.summary("FRV")
    assert "Deal terms" not in terms.summary("SFR")  # never used to screen


def test_add_ons_only_for_the_modality_picked():
    assert [s.code for s in BRIEF.modality_sections()] == ["AUDIO"]
    video = BRIEF.model_copy(update={"picks": {**BRIEF.picks, "modality": Pick(values=["Video"])}})
    assert [s.code for s in video.modality_sections()] == ["VISUAL"]
    assert "Speakers per recording" not in video.summary()


def test_language_suggestions_and_transfer_flags():
    assert "Gulf Arabic" in suggested_languages(["Middle East (GCC)"])
    (saudi,) = transfer_flags(["Middle East (GCC)"])
    assert "PDPL" in saudi
    assert any("China" in f for f in transfer_flags(["china"]))
    assert transfer_flags(["EU/EEA"]) and transfer_flags(["UK"]) == []


def test_brief_saved_with_the_older_template_is_upgraded():
    b = Brief.model_validate({"description": "meeting audio", "notes": "Meetings only.", "picks": {
        "languages": {"values": ["English"], "importance": "must"},
        "licence": {"values": [COMMERCIAL], "importance": "any"},  # counted for nothing: dropped
        "transcript": {"values": ["Verbatim"], "importance": "nice"},
        "speaker_labels": {"values": ["Needed"], "importance": "must"},
        "overlap": {"values": ["Required"], "importance": "nice"},
        "environment": {"values": ["Office/meeting room"]},
        "production": {"values": ["Any"]},  # no preference: nothing to keep
        "delivery": {"values": ["One-off dataset"], "importance": "must"},
        "personal_data": {"values": ["Anonymised only"], "importance": "must"},
        "budget": {"values": ["Under £10k"]},
        "made_up": {"values": ["x"]},
    }})
    assert set(b.picks) == {"languages", "transcript", "speakers", "collection", "deal_terms"}
    assert b.pick("transcript") == Pick(values=["Verbatim transcript", "Speaker labels"], importance="must")
    assert b.pick("speakers").values == ["Overlapping speech"]
    assert b.pick("collection") == Pick(values=[EXISTING_ONLY], importance="must")
    # a must-have research check (personal data) makes the deal terms a must have
    assert b.pick("deal_terms") == Pick(values=["Personal data: Anonymised only"], importance="must")  # no budget
    assert b.notes == "Meetings only; Environment: Office/meeting room"
    assert Brief.model_validate(b.model_dump()) == b  # today's briefs pass through unchanged
    custom = Brief.model_validate({"description": "x", "picks": {
        "delivery": {"values": ["Recurring feed", "Custom collection to order"]}}})
    assert custom.pick("collection") == Pick(values=[COLLECTION_OK])


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
