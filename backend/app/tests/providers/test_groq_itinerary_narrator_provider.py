from __future__ import annotations

from typing import Any

from app.models.itinerary_narrative import (
    ItineraryNarrativeDayInput,
    ItineraryNarrativeExperienceInput,
    ItineraryNarrativeRequest,
    ItineraryNarrativeStatus,
)
from app.providers.itinerary_narrator.groq_adapter import GroqItineraryNarratorProvider

# Step 182F: Groq itinerary narrator adapter tests. Mirrors
# test_groq_ai_candidate_proposal_provider.py's structure. Every test
# uses a fake/injected client -- never a real network call.


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


class _FakeClient:
    def __init__(self, response: Any) -> None:
        self._response = response

    def invoke(self, prompt: str) -> Any:
        return self._response


class _RaisingClient:
    def invoke(self, prompt: str) -> Any:
        raise RuntimeError("simulated network/timeout failure")


def test_returns_not_connected_when_no_key_configured() -> None:
    """The conftest.py autouse fixture forces GROQ_API_KEY="" for every
    test (never a real developer-local key), so an explicit api_key=None
    with no injected client always exercises the real no-key path."""
    provider = GroqItineraryNarratorProvider(api_key=None)

    result = provider.narrate(_request())

    assert result.status == ItineraryNarrativeStatus.NOT_CONNECTED
    assert "GROQ_API_KEY" in (result.message or "")


def test_returns_success_for_well_formed_structured_output() -> None:
    response = {
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
    provider = GroqItineraryNarratorProvider(client=_FakeClient(response))

    result = provider.narrate(_request())

    assert result.status == ItineraryNarrativeStatus.SUCCESS
    assert result.summary == "A short trip to Lisbon."
    assert len(result.daily_narratives) == 1
    assert result.daily_narratives[0].date.isoformat() == "2026-10-10"
    assert result.provider == "groq_itinerary_narrator_provider"


def test_drops_a_day_number_not_present_in_the_request() -> None:
    """The model referencing a day outside the real request must never be
    turned into a fabricated day -- it is dropped instead."""
    response = {
        "summary": "Trip summary.",
        "daily_narratives": [
            {"day_number": 1, "title": "Real day", "narrative": "Real narrative."},
            {"day_number": 99, "title": "Fake day", "narrative": "Should be dropped."},
        ],
    }
    provider = GroqItineraryNarratorProvider(client=_FakeClient(response))

    result = provider.narrate(_request())

    assert result.status == ItineraryNarrativeStatus.SUCCESS
    assert len(result.daily_narratives) == 1
    assert result.daily_narratives[0].day_number == 1


def test_returns_failed_on_missing_summary() -> None:
    provider = GroqItineraryNarratorProvider(
        client=_FakeClient({"daily_narratives": []})
    )

    result = provider.narrate(_request())

    assert result.status == ItineraryNarrativeStatus.FAILED
    assert result.daily_narratives == []


def test_returns_failed_on_unstructured_output() -> None:
    provider = GroqItineraryNarratorProvider(client=_FakeClient("not a dict or pydantic model"))

    result = provider.narrate(_request())

    assert result.status == ItineraryNarrativeStatus.FAILED
    assert "structured response" in (result.message or "")


def test_returns_failed_on_client_exception() -> None:
    provider = GroqItineraryNarratorProvider(client=_RaisingClient())

    result = provider.narrate(_request())

    assert result.status == ItineraryNarrativeStatus.FAILED
    assert "Groq API call failed" in (result.message or "")


def test_never_fabricates_price_rating_route_or_time_fields() -> None:
    """The report model itself has no such field, so this asserts the
    absence structurally rather than by string-matching prose."""
    response = {
        "summary": "Trip summary.",
        "daily_narratives": [
            {"day_number": 1, "title": "Day one", "narrative": "Narrative text."}
        ],
    }
    provider = GroqItineraryNarratorProvider(client=_FakeClient(response))

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


def test_accepts_a_pydantic_model_response_via_model_dump() -> None:
    from pydantic import BaseModel

    class _FakeSchema(BaseModel):
        summary: str = "Trip summary."
        daily_narratives: list[dict[str, Any]] = []
        assumptions: list[str] = []
        warnings: list[str] = []

    provider = GroqItineraryNarratorProvider(client=_FakeClient(_FakeSchema()))

    result = provider.narrate(_request())

    assert result.status == ItineraryNarrativeStatus.SUCCESS
    assert result.summary == "Trip summary."
