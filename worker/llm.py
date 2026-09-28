"""One place to call Gemini: structured output, retries, optional Google Search."""

import os
import time
from functools import cache
from typing import TypeVar

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel

from worker.config import MODEL_FLASH

load_dotenv()

T = TypeVar("T", bound=BaseModel)

RETRIES = 2  # extra attempts after the first


@cache
def _client() -> genai.Client:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is empty — fill it in .env")
    # attempts=1: no hidden SDK retries — ask() owns retrying, so each failure costs one request
    return genai.Client(
        api_key=key,
        http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1)),
    )


class QuotaExceeded(RuntimeError):
    """Rate/quota limit hit (HTTP 429). Not retried — on the free tier it's usually a daily cap."""


def ask(
    prompt: str,
    schema: type[T],
    model: str = MODEL_FLASH,
    use_search: bool = False,
    system: str | None = None,
) -> T:
    """Send prompt to Gemini and return its answer parsed into `schema`."""
    body = {
        "model": model,
        "input": prompt,
        "response_format": {
            "type": "text",
            "mime_type": "application/json",
            "schema": schema.model_json_schema(),
        },
    }
    if use_search:
        body["tools"] = [{"type": "google_search"}]
    if system:
        body["system_instruction"] = system

    last_error: Exception | None = None
    for attempt in range(RETRIES + 1):
        try:
            interaction = _client().interactions.create(**body)
            return schema.model_validate_json(interaction.output_text)
        except Exception as e:  # API error or JSON not matching schema
            if "429" in str(e) or "too_many_requests" in str(e):
                raise QuotaExceeded(f"Gemini quota hit for {model}: {str(e)[:300]}") from e
            last_error = e
            if attempt < RETRIES:
                print(f"  [llm] attempt {attempt + 1} failed ({type(e).__name__}: {str(e)[:200]}), retrying")
                time.sleep(2**attempt)
    raise RuntimeError(f"Gemini call failed after {RETRIES + 1} attempts: {last_error}")
