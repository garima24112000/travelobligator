from __future__ import annotations

from typing import Any

from app.models.itinerary_narrative import (
    ItineraryNarrativeDayInput,
    ItineraryNarrativeExperienceInput,
    ItineraryNarrativeRequest,
    ItineraryNarrativeStatus,
)
from app.providers.itinerary_narrator.anthropic_adapter import AnthropicItineraryNarratorProvider

# Step 182F: Anthropic itinerary narrator adapter tests. Mirrors
# test_anthropic_ai_candidate_proposal_provider.py's structure. Every
# test uses a fake/injected client -- never a real network call.


def _request(**overrides: Any) -> ItineraryNarrativeRequest:
    fields: dict[str, Any] = {
        "destination": "Lisbon, Portugal",
        "start_date": "2026-10-10",
        "end_date": "2026-10-11",
        "travelers_count": 2,
        "days": [
            ItineraryNarrativeDayInput(
                day_number=1,
                date="2026-10-10",
                experiences=[
                    ItineraryNarrativeExperienceInput(
                        name="Belem Tower", category="landmark", reason="must-visit"
                    )
                ],
            )
        ],
    }
    fields.update(overrides)
    return ItineraryNarrativeRequest(**fields)


class _FakeToolUseBlock:
    def __init__(self, name: str, tool_input: dict[str, Any]) -> None:
        self.type = "tool_use"
        self.name = name
        self.input = tool_input


class _FakeResponse:
    def __init__(self, content: list[Any]) -> None:
        self.content = content


class _FakeMessages:
    def __init__(self, response: Any = None, exception: Exception | None = None) -> None:
        self._response = response
        self._exception = exception

    def create(self, **kwargs: Any) -> Any:
        if self._exception is not None:
            raise self._exception
        return self._response


class _FakeClient:
    def __init__(self, response: Any = None, exception: Exception | None = None) -> None:
        self.messages = _FakeMessages(response=response, exception=exception)


def test_returns_not_connected_when_no_key_configured() -> None:
    """The conftest.py autouse fixture forces ANTHROPIC_API_KEY="" for
    every test (never a real developer-local key), so an explicit
    api_key=None with no injected client always exercises the real
    no-key path."""
    provider = AnthropicItineraryNarratorProvider(api_key=None)

    result = provider.narrate(_request())

    assert result.status == ItineraryNarrativeStatus.NOT_CONNECTED
    assert "ANTHROPIC_API_KEY" in (result.message or "")


def test_returns_success_for_well_formed_tool_use_response() -> None:
    tool_input = {
        "summary": "A short trip to Lisbon.",
        "daily_narratives": [
            {
                "day_number": 1,
                "title": "Historic Belem",
                "narrative": "Start the day at Belem Tower.",
                "caveats": [],
            }
        ],
        "assumptions": [],
        "warnings": [],
    }
    client = _FakeClient(
        response=_FakeResponse([_FakeToolUseBlock("submit_itinerary_narrative", tool_input)])
    )
    provider = AnthropicItineraryNarratorProvider(client=client)

    result = provider.narrate(_request())

    assert result.status == ItineraryNarrativeStatus.SUCCESS
    assert result.summary == "A short trip to Lisbon."
    assert len(result.daily_narratives) == 1
    assert result.daily_narratives[0].date.isoformat() == "2026-10-10"
    assert result.provider == "anthropic_itinerary_narrator_provider"


def test_drops_a_day_number_not_present_in_the_request() -> None:
    tool_input = {
        "summary": "Trip summary.",
        "daily_narratives": [
            {"day_number": 1, "title": "Real day", "narrative": "Real narrative."},
            {"day_number": 99, "title": "Fake day", "narrative": "Should be dropped."},
        ],
    }
    client = _FakeClient(
        response=_FakeResponse([_FakeToolUseBlock("submit_itinerary_narrative", tool_input)])
    )
    provider = AnthropicItineraryNarratorProvider(client=client)

    result = provider.narrate(_request())

    assert result.status == ItineraryNarrativeStatus.SUCCESS
    assert len(result.daily_narratives) == 1
    assert result.daily_narratives[0].day_number == 1


def test_returns_failed_when_no_tool_use_block_present() -> None:
    client = _FakeClient(response=_FakeResponse([]))
    provider = AnthropicItineraryNarratorProvider(client=client)

    result = provider.narrate(_request())

    assert result.status == ItineraryNarrativeStatus.FAILED
    assert "structured tool_use" in (result.message or "")


def test_returns_failed_on_missing_summary() -> None:
    tool_input = {"daily_narratives": []}
    client = _FakeClient(
        response=_FakeResponse([_FakeToolUseBlock("submit_itinerary_narrative", tool_input)])
    )
    provider = AnthropicItineraryNarratorProvider(client=client)

    result = provider.narrate(_request())

    assert result.status == ItineraryNarrativeStatus.FAILED


def test_returns_failed_on_client_exception() -> None:
    client = _FakeClient(exception=RuntimeError("simulated network/timeout failure"))
    provider = AnthropicItineraryNarratorProvider(client=client)

    result = provider.narrate(_request())

    assert result.status == ItineraryNarrativeStatus.FAILED
    assert "Anthropic API call failed" in (result.message or "")


def test_never_fabricates_price_rating_route_or_time_fields() -> None:
    tool_input = {
        "summary": "Trip summary.",
        "daily_narratives": [
            {"day_number": 1, "title": "Day one", "narrative": "Narrative text."}
        ],
    }
    client = _FakeClient(
        response=_FakeResponse([_FakeToolUseBlock("submit_itinerary_narrative", tool_input)])
    )
    provider = AnthropicItineraryNarratorProvider(client=client)

    result = provider.narrate(_request())

    dumped_report = result.model_dump()
    dumped_day = result.daily_narratives[0].model_dump()
    forbidden_field_names = {
        "price",
        "rating",
        "review_count",
        "route_duration",
        "travel_time",
        "booking_url",
        "booking_confirmation",
        "opening_hours",
        "flight_number",
        "time",
    }
    assert forbidden_field_names.isdisjoint(dumped_report.keys())
    assert forbidden_field_names.isdisjoint(dumped_day.keys())
