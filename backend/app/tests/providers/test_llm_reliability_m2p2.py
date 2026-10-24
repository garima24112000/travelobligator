"""M2 Phase 2 (Workstream C): LLM rate-limit waits, failure classification.

Evidence (development E2E run, Groq only): the reasoning stage got HTTP 429,
retried after Groq's short Retry-After and was refused again (88 ms later in
request time) -- the token budget, whose own reset was ~34 s away, had not
refilled. The anchor stage's HTTP 400 was Groq's `json_validate_failed`
(model output failing the JSON schema), recovered by the structural retry.
No provider is called here."""

from __future__ import annotations

from typing import Any

import pytest

from app.core import performance
from app.providers import ai_stage_budget
from app.providers.ai_failure import (
    WAIT_SOURCE_QUOTA_RESET,
    WAIT_SOURCE_RETRY_AFTER,
    AIProviderFailureKind,
    classify_ai_provider_exception,
    is_transient_failure,
    rate_limit_wait_seconds,
)
from app.providers.ai_stage_budget import StageRun


class _Response:
    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


class _HTTPError(Exception):
    def __init__(self, status_code: int, headers: dict[str, str] | None = None, code: str | None = None) -> None:
        super().__init__("RAW PROVIDER BODY must never surface")
        self.status_code = status_code
        self.code = code
        self.response = _Response(headers or {})


def _groq_429(retry_after: str, remaining_requests: str, reset_tokens: str, reset_requests: str = "2s") -> _HTTPError:
    return _HTTPError(
        429,
        {
            "retry-after": retry_after,
            "x-ratelimit-limit-requests": "1000",
            "x-ratelimit-remaining-requests": remaining_requests,
            "x-ratelimit-reset-requests": reset_requests,
            "x-ratelimit-limit-tokens": "8000",
            "x-ratelimit-remaining-tokens": "3452",
            "x-ratelimit-reset-tokens": reset_tokens,
        },
    )


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture(autouse=True)
def _production_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai_stage_budget, "TRANSPORT_RETRY_BACKOFF_SECONDS", 1.0)


def _stage(budget: float) -> tuple[StageRun, _Clock]:
    clock = _Clock()
    run = StageRun("groq_reasoning", total_budget_seconds=budget, request_timeout_seconds=20, clock=clock, sleep=clock.sleep)
    run.next_attempt_timeout()
    return run, clock


# -- the wait a 429 asks for -------------------------------------------------------------


def test_a_token_refusal_waits_for_the_token_reset_not_the_short_retry_after() -> None:
    # requests still remain, so the refusal was on TOKENS
    assert rate_limit_wait_seconds(_groq_429("2", "998", "34.11s")) == (34.11, WAIT_SOURCE_QUOTA_RESET)


def test_a_request_count_refusal_waits_for_the_request_reset() -> None:
    assert rate_limit_wait_seconds(_groq_429("2", "0", "34s", reset_requests="7s")) == (7.0, WAIT_SOURCE_QUOTA_RESET)


def test_a_longer_retry_after_still_wins() -> None:
    assert rate_limit_wait_seconds(_groq_429("40", "998", "34s")) == (40.0, WAIT_SOURCE_RETRY_AFTER)


def test_without_quota_headers_only_retry_after_is_used() -> None:
    assert rate_limit_wait_seconds(_HTTPError(429, {"retry-after": "3"})) == (3.0, WAIT_SOURCE_RETRY_AFTER)
    assert rate_limit_wait_seconds(_HTTPError(429)) == (None, None)


def test_unreadable_quota_headers_are_ignored() -> None:
    bad = _HTTPError(429, {"retry-after": "3", "x-ratelimit-remaining-requests": "lots", "x-ratelimit-reset-tokens": "soon"})
    assert rate_limit_wait_seconds(bad) == (3.0, WAIT_SOURCE_RETRY_AFTER)


# -- the same-provider retry -------------------------------------------------------------


def test_the_observed_refusal_is_not_retried_when_the_token_reset_does_not_fit() -> None:
    # Before: waited 2 s (Retry-After), retried, refused again -- a wasted request.
    run, clock = _stage(budget=20.0)
    assert run.allow_transport_retry(_groq_429("2", "998", "34.11s")) is False
    assert clock.slept == []  # no wait, no second request
    assert run.retry_after == "quota_reset_skipped"
    assert run.attempts == 1


def test_a_token_reset_that_fits_is_waited_in_full() -> None:
    run, clock = _stage(budget=25.0)
    assert run.allow_transport_retry(_groq_429("1", "998", "6s")) is True
    assert clock.slept == [6.0] and run.retry_after == "quota_reset_obeyed"


def test_plain_retry_after_behaviour_is_unchanged() -> None:
    run, clock = _stage(budget=25.0)
    assert run.allow_transport_retry(_HTTPError(429, {"retry-after": "5"})) is True
    assert clock.slept == [5.0] and run.retry_after == "obeyed"


def test_the_new_labels_survive_the_performance_sanitizer() -> None:
    recorder = performance.started_recorder()
    with performance.activate(recorder):
        for index, label in enumerate(("quota_reset_obeyed", "quota_reset_skipped", "made_up_label")):
            performance.note_llm_stage(
                f"groq_stage_{index}",
                attempts=1,
                structural_retries=0,
                transport_retries=0,
                deadline_exceeded=False,
                result="failed",
                transport_failure="rate_limit",
                retry_after=label,
            )
    stages = recorder.snapshot()["llm_stages"]
    labels = {stage.get("retry_after") for stage in stages.values()}
    assert {"quota_reset_obeyed", "quota_reset_skipped"} <= labels
    assert "made_up_label" not in labels


# -- classification of the observed and neighbouring failures ---------------------------


@pytest.mark.parametrize(
    ("exc", "kind", "transient"),
    [
        (_HTTPError(400, code="json_validate_failed"), AIProviderFailureKind.MALFORMED_OUTPUT, False),
        (_HTTPError(400), AIProviderFailureKind.PROVIDER_ERROR, False),  # e.g. a bad parameter: retry cannot help
        (_HTTPError(429), AIProviderFailureKind.RATE_LIMITED, True),
        (TimeoutError("t"), AIProviderFailureKind.TIMEOUT_OR_NETWORK, True),
        (_HTTPError(503), AIProviderFailureKind.PROVIDER_ERROR, True),
    ],
    ids=["400-json-validate-failed", "400-other", "429", "timeout", "503"],
)
def test_failure_classification(exc: Any, kind: AIProviderFailureKind, transient: bool) -> None:
    assert classify_ai_provider_exception(exc) == kind
    assert is_transient_failure(exc) is transient
