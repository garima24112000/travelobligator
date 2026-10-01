from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pytest

from app.core.config import get_settings
from app.models.common import GeoPoint
from app.models.itinerary_narrative import (
    ItineraryNarrativeDayInput,
    ItineraryNarrativeExperienceInput,
    ItineraryNarrativeRequest,
    ItineraryNarrativeStatus,
)
from app.models.planning_state import (
    DailyPlan,
    ExperienceItem,
    ExperiencePlan,
    PlanningState,
    TravelGroupType,
    TripRequest,
)
from app.providers.itinerary_narrator.anthropic_adapter import _TOOL_NAME, AnthropicItineraryNarratorProvider
from app.providers.itinerary_narrator.groq_adapter import GroqItineraryNarratorProvider
from app.providers.itinerary_narrator.structural_retry import MAX_NARRATOR_ATTEMPTS, RETRY_FORMAT_REMINDER
from app.services.itinerary_narrative_service import ItineraryNarrativeService

# Section 202C.1D: one bounded retry for a STRUCTURAL narrator output failure.
# Live evidence (202C.1C): the Groq narrator failed three pipeline runs in a row
# with HTTP 400 `json_validate_failed` and each went straight to the fallback.
# No network here: scripted fake clients only.


class _ProviderError(Exception):
    def __init__(self, status_code: int, code: str | None = None) -> None:
        super().__init__("provider error")
        self.status_code = status_code
        self.body = {"error": {"code": code}} if code else {}


class RateLimitError(_ProviderError):
    """Named like the SDK class the failure classifier recognises."""


def _structural() -> Exception:
    return _ProviderError(400, "json_validate_failed")


_VALID = {
    "summary": "A short trip to Lisbon.",
    "daily_narratives": [
        {
            "day_number": 1,
            "title": "Historic Belem",
            "narrative": "Start the day at Belem Tower.",
            "caveats": [],
            "referenced_experience_ids": ["exp_belem_tower"],
        }
    ],
    "assumptions": [],
    "warnings": [],
}
_FOREIGN_ID = {
    **_VALID,
    "daily_narratives": [{**_VALID["daily_narratives"][0], "referenced_experience_ids": ["exp_not_in_this_plan"]}],
}


def _request() -> ItineraryNarrativeRequest:
    return ItineraryNarrativeRequest(
        destination="Lisbon, Portugal",
        start_date="2026-10-10",
        end_date="2026-10-10",
        travelers_count=2,
        days=[
            ItineraryNarrativeDayInput(
                day_number=1,
                date="2026-10-10",
                experiences=[
                    ItineraryNarrativeExperienceInput(
                        experience_id="exp_belem_tower", name="Belem Tower", category="landmark", reason="must-visit"
                    )
                ],
            )
        ],
    )


class _GroqScript:
    """`client.invoke(prompt)`; each step is a value to return or an exception to raise."""

    def __init__(self, *steps: Any) -> None:
        self._steps = list(steps)
        self.prompts: list[str] = []

    def invoke(self, prompt: str) -> Any:
        self.prompts.append(prompt)
        step = self._steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


def _groq(*steps: Any) -> tuple[GroqItineraryNarratorProvider, _GroqScript]:
    client = _GroqScript(*steps)
    return GroqItineraryNarratorProvider(client=client), client


# -- Groq ------------------------------------------------------------------------


def test_first_attempt_success_makes_exactly_one_call() -> None:
    provider, client = _groq(_VALID)
    result = provider.narrate(_request())
    assert result.status == ItineraryNarrativeStatus.SUCCESS
    assert len(client.prompts) == 1 and RETRY_FORMAT_REMINDER not in client.prompts[0]


@pytest.mark.parametrize("first", [_structural(), None, "not structured", 42])
def test_structural_failure_is_retried_exactly_once_and_the_retry_result_is_accepted(first: Any) -> None:
    provider, client = _groq(first, _VALID)
    result = provider.narrate(_request())

    assert result.status == ItineraryNarrativeStatus.SUCCESS
    assert result.summary == "A short trip to Lisbon."
    assert len(client.prompts) == 2
    # same factual input, plus only the format reminder
    assert client.prompts[1] == f"{client.prompts[0]}\n\n{RETRY_FORMAT_REMINDER}"


def test_retry_that_also_fails_structurally_returns_failed_after_two_calls() -> None:
    provider, client = _groq(_structural(), _structural(), _VALID)
    result = provider.narrate(_request())

    assert result.status == ItineraryNarrativeStatus.FAILED
    assert result.summary is None and result.daily_narratives == []
    assert "did not match the required structure" in (result.message or "")
    assert len(client.prompts) == MAX_NARRATOR_ATTEMPTS == 2  # the third scripted answer is never requested


@pytest.mark.parametrize(
    "failure",
    [
        RateLimitError(429),  # rate limit / quota
        _ProviderError(429, "rate_limit_exceeded"),
        _ProviderError(401),  # authentication
        _ProviderError(403),
        _ProviderError(500),  # provider outage
        _ProviderError(503),
        _ProviderError(400, "invalid_request_error"),  # a 400 that is NOT a structural failure
        TimeoutError("timed out"),
        ConnectionError("network"),
        RuntimeError("anything else"),
    ],
)
def test_non_structural_failures_are_never_retried(failure: Exception) -> None:
    provider, client = _groq(failure, _VALID)
    result = provider.narrate(_request())
    assert result.status == ItineraryNarrativeStatus.FAILED
    assert len(client.prompts) == 1


def test_a_parsed_but_ungrounded_first_answer_is_rejected_without_a_retry() -> None:
    provider, client = _groq(_FOREIGN_ID, _VALID)
    result = provider.narrate(_request())
    assert result.status == ItineraryNarrativeStatus.FAILED
    assert "outside that day's real scheduled items" in (result.message or "")
    assert len(client.prompts) == 1  # a guardrail rejection is not a structural failure


@pytest.mark.parametrize("retry_answer", [_FOREIGN_ID, {"daily_narratives": []}, {**_VALID, "summary": "  "}])
def test_the_retry_answer_gets_no_leniency(retry_answer: dict[str, Any]) -> None:
    provider, client = _groq(_structural(), retry_answer, _VALID)
    result = provider.narrate(_request())
    assert result.status == ItineraryNarrativeStatus.FAILED  # never "almost valid"
    assert len(client.prompts) == 2


def test_retry_logging_carries_no_prompt_or_output(caplog: pytest.LogCaptureFixture) -> None:
    provider, _ = _groq(_structural(), _VALID)
    with caplog.at_level(logging.INFO, logger="app.providers.itinerary_narrator.structural_retry"):
        provider.narrate(_request())

    statuses = [getattr(r, "status", None) for r in caplog.records]
    assert statuses == ["retrying", "retry_succeeded"]
    for record in caplog.records:
        assert record.stage == "itinerary_narrator" and record.max_attempts == 2
        text = record.getMessage() + repr({k: v for k, v in vars(record).items() if k not in ("msg", "args")})
        for secret in ("Belem Tower", "Lisbon", "A short trip", "exp_belem_tower"):
            assert secret not in text


def test_failed_retry_is_logged_as_such(caplog: pytest.LogCaptureFixture) -> None:
    provider, _ = _groq(_structural(), _structural())
    with caplog.at_level(logging.INFO, logger="app.providers.itinerary_narrator.structural_retry"):
        provider.narrate(_request())
    assert [getattr(r, "status", None) for r in caplog.records] == ["retrying", "retry_failed"]


# -- Anthropic (same contract) ---------------------------------------------------------


class _Block:
    def __init__(self, tool_input: dict[str, Any]) -> None:
        self.type, self.name, self.input = "tool_use", _TOOL_NAME, tool_input


class _Response:
    def __init__(self, content: list[Any]) -> None:
        self.content = content


class _AnthropicScript:
    def __init__(self, *steps: Any) -> None:
        self._steps = list(steps)
        self.calls: list[dict[str, Any]] = []
        self.messages = self

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        step = self._steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


def test_anthropic_first_attempt_success_makes_one_call() -> None:
    client = _AnthropicScript(_Response([_Block(_VALID)]))
    result = AnthropicItineraryNarratorProvider(client=client).narrate(_request())
    assert result.status == ItineraryNarrativeStatus.SUCCESS and len(client.calls) == 1


def test_anthropic_missing_tool_use_is_retried_once_and_accepted() -> None:
    client = _AnthropicScript(_Response([]), _Response([_Block(_VALID)]))
    result = AnthropicItineraryNarratorProvider(client=client).narrate(_request())
    assert result.status == ItineraryNarrativeStatus.SUCCESS and len(client.calls) == 2
    first, second = (c["messages"][0]["content"] for c in client.calls)
    assert second == f"{first}\n\n{RETRY_FORMAT_REMINDER}"


def test_anthropic_two_structural_failures_stop_at_two_calls() -> None:
    client = _AnthropicScript(_Response([]), _Response([]), _Response([_Block(_VALID)]))
    result = AnthropicItineraryNarratorProvider(client=client).narrate(_request())
    assert result.status == ItineraryNarrativeStatus.FAILED and len(client.calls) == 2


@pytest.mark.parametrize("failure", [RateLimitError(429), _ProviderError(401), _ProviderError(500), TimeoutError("t")])
def test_anthropic_non_structural_failures_are_not_retried(failure: Exception) -> None:
    client = _AnthropicScript(failure, _Response([_Block(_VALID)]))
    result = AnthropicItineraryNarratorProvider(client=client).narrate(_request())
    assert result.status == ItineraryNarrativeStatus.FAILED and len(client.calls) == 1


# -- through the service: recovery, and the grounded fallback stays in place -----------------


def _state() -> PlanningState:
    state = PlanningState(
        trip_request=TripRequest(
            primary_destination="Lisbon, Portugal", start_date="2026-10-10", end_date="2026-10-10",
            travelers_count=2, travel_group_type=TravelGroupType.COUPLE,
        )
    )
    state.experience_plan = ExperiencePlan(
        daily_plans=[
            DailyPlan(
                day_number=1,
                date=date(2026, 10, 10),
                experiences=[
                    ExperienceItem(
                        experience_id="exp_belem_tower", name="Belem Tower", category="landmark", day_number=1,
                        stop_order=1, provider_place_id="way/1", provider_source="openstreetmap_places",
                        coordinates=GeoPoint(lat=38.69, lng=-9.21),
                    )
                ],
            )
        ]
    )
    return state


@pytest.fixture()
def _narrator_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "itinerary_narrator_enabled", True, raising=False)


def test_service_uses_the_ai_narrative_when_the_retry_recovers(_narrator_enabled: None) -> None:
    provider, client = _groq(_structural(), _VALID)
    state = ItineraryNarrativeService(provider=provider).generate(_state())
    report = state.itinerary_narrative_report
    assert report.status == ItineraryNarrativeStatus.SUCCESS
    assert "Belem Tower" in report.daily_narratives[0].narrative
    assert len(client.prompts) == 2


def test_service_falls_back_to_the_deterministic_narrative_after_two_structural_failures(_narrator_enabled: None) -> None:
    provider, client = _groq(_structural(), _structural(), _VALID)
    state = ItineraryNarrativeService(provider=provider).generate(_state())
    report = state.itinerary_narrative_report
    assert len(client.prompts) == 2  # never a third call
    assert report.status == ItineraryNarrativeStatus.FAILED
    assert report.narrative_source == "deterministic_fallback"
    assert report.daily_narratives and "Belem Tower" in report.daily_narratives[0].narrative  # fact-only text


@pytest.mark.parametrize(
    "narrative",
    [
        "Belem Tower has a ticket price worth knowing and a high rating.",  # forbidden factual-claim wording
        "After Belem Tower, continue to the Eiffel Tower in Paris.",  # a place that is not in the plan
    ],
)
def test_service_rejects_an_ungrounded_retry_answer_and_falls_back(_narrator_enabled: None, narrative: str) -> None:
    invented = {**_VALID, "daily_narratives": [{**_VALID["daily_narratives"][0], "narrative": narrative}]}
    provider, client = _groq(_structural(), invented, _VALID)
    state = ItineraryNarrativeService(provider=provider).generate(_state())
    report = state.itinerary_narrative_report
    assert len(client.prompts) == 2  # the grounding rejection does not trigger another call
    assert report.status == ItineraryNarrativeStatus.FAILED
    assert report.narrative_source == "deterministic_fallback"
    text = " ".join([report.summary or ""] + [d.narrative for d in report.daily_narratives])
    for unsupported in ("ticket price", "rating", "Eiffel", "Paris"):
        assert unsupported not in text
