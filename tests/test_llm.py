"""Live test — makes one real Gemini call. Run: RUN_LIVE=1 uv run pytest tests/test_llm.py -s"""

import pytest
from pydantic import BaseModel

from worker.llm import ask


class Capital(BaseModel):
    country: str
    capital: str


@pytest.mark.live
def test_ask_returns_schema():
    result = ask("What is the capital of France?", Capital)
    print(result)
    assert isinstance(result, Capital)
    assert result.capital.lower() == "paris"


@pytest.mark.live
def test_brief_template_schemas_are_accepted():
    """Gemini rejects schemas that are too complex with a bare 400. The template is split to stay
    under the limit; this catches a template change that pushes a half over it."""
    from worker.steps.brief import draft_brief

    draft = draft_brief("1,000 hours of English meeting audio with speaker labels, for commercial training.")
    assert draft.brief.pick("modality").values == ["Audio/speech"]
