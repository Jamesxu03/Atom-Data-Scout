"""ask() error handling, with the SDK client replaced by a stub."""

import pytest
from pydantic import BaseModel

from worker import llm


class Answer(BaseModel):
    value: str


class StatusError(Exception):
    def __init__(self, status_code: int, message: str = ""):
        super().__init__(message)
        self.status_code = status_code


class Reply:
    def __init__(self, text):
        self.output_text = text


def _client_returning(*outcomes):
    calls = []

    class Interactions:
        def create(self, **body):
            calls.append(body)
            outcome = outcomes[min(len(calls), len(outcomes)) - 1]
            if isinstance(outcome, Exception):
                raise outcome
            return Reply(outcome)

    class Client:
        interactions = Interactions()

    return Client(), calls


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)


def _use(monkeypatch, *outcomes):
    client, calls = _client_returning(*outcomes)
    monkeypatch.setattr(llm, "_client", lambda: client)
    return calls


def test_bad_json_containing_429_is_retried_not_quota(monkeypatch):
    # model output mentions 429 (a phone number, company number...) and fails validation
    calls = _use(monkeypatch, '{"wrong": "call +44 429 1234"}', '{"value": "ok"}')
    assert llm.ask("q", Answer).value == "ok"
    assert len(calls) == 2


def test_daily_quota_raises_immediately(monkeypatch):
    calls = _use(monkeypatch, StatusError(429, "limit: 20 requests per day on Free Tier"))
    with pytest.raises(llm.QuotaExceeded):
        llm.ask("q", Answer)
    assert len(calls) == 1


def test_per_minute_rate_limit_is_retried(monkeypatch):
    calls = _use(monkeypatch, StatusError(429, "rate limit per minute"), '{"value": "ok"}')
    assert llm.ask("q", Answer).value == "ok"
    assert len(calls) == 2


def test_persistent_rate_limit_becomes_quota_exceeded(monkeypatch):
    calls = _use(monkeypatch, StatusError(429, "rate limit per minute"))
    with pytest.raises(llm.QuotaExceeded):
        llm.ask("q", Answer)
    assert len(calls) == llm.RETRIES + 1


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_client_errors_are_not_retried(monkeypatch, status):
    calls = _use(monkeypatch, StatusError(status, "bad"))
    with pytest.raises(StatusError):
        llm.ask("q", Answer)
    assert len(calls) == 1


def test_recitation_block_is_retried(monkeypatch):
    # a 400 for output resembling copyrighted text (long quotes): sampled, so a retry can pass
    calls = _use(monkeypatch, StatusError(400, "Request blocked due to copyright/recitation content."),
                 '{"value": "ok"}')
    assert llm.ask("q", Answer).value == "ok"
    assert len(calls) == 2


def test_server_error_is_retried(monkeypatch):
    calls = _use(monkeypatch, StatusError(503, "unavailable"), '{"value": "ok"}')
    assert llm.ask("q", Answer).value == "ok"
    assert len(calls) == 2


def test_request_body(monkeypatch):
    calls = _use(monkeypatch, '{"value": "ok"}')
    llm.ask("q", Answer, use_search=True, thinking="low")
    (body,) = calls
    assert body["tools"] == [{"type": "google_search"}]
    assert body["generation_config"] == {"thinking_level": "low"}
    assert body["response_format"]["schema"] == Answer.model_json_schema()


def test_a_hung_call_times_out_and_is_retried(monkeypatch):
    import httpx

    calls = _use(monkeypatch, httpx.ReadTimeout("no answer"), '{"value": "ok"}')
    assert llm.ask("q", Answer).value == "ok" and len(calls) == 2


def test_the_client_gives_up_on_a_call_after_the_timeout(monkeypatch):
    from worker.config import LLM_TIMEOUT_SECONDS

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    llm._client.cache_clear()
    try:
        assert llm._client()._api_client._http_options.timeout == LLM_TIMEOUT_SECONDS * 1000
    finally:
        llm._client.cache_clear()
