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
