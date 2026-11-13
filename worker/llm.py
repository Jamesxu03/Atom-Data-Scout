"""One place to call Gemini: structured output, retries, optional Google Search."""

import os
import time
from functools import cache
from typing import TypeVar

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, ValidationError

from worker import config
from worker.config import MODEL, THINKING_LEVEL

load_dotenv()

T = TypeVar("T", bound=BaseModel)

RETRIES = 2  # extra attempts after the first
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


def has_key() -> bool:
    """Whether GEMINI_API_KEY is set (in the environment or .env)."""
    return bool(os.getenv("GEMINI_API_KEY", "").strip())


@cache
def _client() -> genai.Client:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is empty: copy .env.example to .env and add your key")
    # attempts=1: no hidden SDK retries — ask() owns retrying, so each failure costs one request.
    # A timeout raises like a dropped connection, so ask() retries it.
    return genai.Client(
        api_key=key,
        http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1),
                                       timeout=config.LLM_TIMEOUT_SECONDS * 1000),
    )


class QuotaExceeded(RuntimeError):
    """Quota / rate limit (HTTP 429) that retrying won't fix. Callers stop making calls."""


def http_status(e: Exception) -> int | None:
    """HTTP status of an SDK error: `status_code` (interactions API) or `code` (genai.errors)."""
    for attr in ("status_code", "code"):
        value = getattr(e, attr, None)
        if isinstance(value, int):
            return value
    return None


def _is_recitation(e: Exception) -> bool:
    """Output blocked for resembling copyrighted text: sampled, so another attempt often passes."""
    return "recitation" in str(e).lower()


def _is_hard_quota(e: Exception) -> bool:
    """Daily cap or zero quota (free tier): waiting a few seconds won't help."""
    msg = str(e).lower()
    return "per day" in msg or "limit: 0" in msg


def step(name: str) -> dict[str, str]:
    """The model and thinking level configured for a step (config.STEP_MODELS / STEP_THINKING),
    read at call time so an experiment can swap them: llm.ask(prompt, Schema, **llm.step("screen"))."""
    return {"model": config.STEP_MODELS[name], "thinking": config.STEP_THINKING[name]}


def search_queries(interaction) -> list[str]:
    """The Google Search queries Gemini ran for a grounded answer; empty when not reported."""
    out = []
    for s in getattr(interaction, "steps", None) or []:
        if getattr(s, "type", None) == "google_search_call":
            out += list(getattr(getattr(s, "arguments", None), "queries", None) or [])
    return out


def ask(
    prompt: str,
    schema: type[T],
    model: str = MODEL,
    use_search: bool = False,
    system: str | None = None,
    thinking: str | None = THINKING_LEVEL,
    queries: list[str] | None = None,
) -> T:
    """Send prompt to Gemini and return its answer parsed into `schema`. With `queries`, the
    search queries Gemini ran are appended to it.

    Retries bad JSON, timeouts, connection errors, 5xx, per-minute 429s and output blocked for
    recitation (long word-for-word quotes). Raises at once on
    other 4xx (bad request, auth, not found) and raises QuotaExceeded on quota exhaustion.
    """
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
    if thinking:
        body["generation_config"] = {"thinking_level": thinking}

    last_error: Exception | None = None
    for attempt in range(RETRIES + 1):
        try:
            interaction = _client().interactions.create(**body)
            result = schema.model_validate_json(interaction.output_text or "")
            if queries is not None:
                queries.extend(search_queries(interaction))
            return result
        except ValidationError as e:  # model output didn't match the schema — worth another go
            last_error = e
            delay = 1
        except Exception as e:
            status = http_status(e)
            if status == 429 and _is_hard_quota(e):
                raise QuotaExceeded(f"Gemini quota exhausted for {model}: {str(e)[:300]}") from e
            if status is not None and status not in RETRYABLE_STATUS and not _is_recitation(e):
                raise  # 400/401/403/404: the same request will fail the same way
            last_error = e
            delay = 5 * (attempt + 1) if status == 429 else 2**attempt
        if attempt < RETRIES:
            print(f"  [llm] attempt {attempt + 1} failed ({type(last_error).__name__}: {str(last_error)[:120]}), "
                  "retrying", flush=True)
            time.sleep(delay)

    if http_status(last_error) == 429:
        raise QuotaExceeded(f"Gemini rate limit for {model} after retries: {last_error}")
    raise RuntimeError(f"Gemini call failed after {RETRIES + 1} attempts: {last_error}")
