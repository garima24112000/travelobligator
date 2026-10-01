from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from app.core.config import get_settings
from app.models.common import GeoPoint
from app.models.itinerary_narrative import (
    ItineraryNarrativeDayInput,
    ItineraryNarrativeDayOutput,
    ItineraryNarrativeExperienceInput,
    ItineraryNarrativeReport,
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
from app.providers.itinerary_narrator.groq_adapter import GroqItineraryNarratorProvider
from app.services.itinerary_narrative_grounding import find_unsupported_factual_claims
from app.services.itinerary_narrative_service import ItineraryNarrativeService

# Section 202C.1E: the narrator is given no price, rating, opening hour, route
# duration/distance, availability, booking or safety value, so any such claim
# in its prose is invented. 202C.1D found that "opens at 9am, costs $12 and is
# rated 4.8 stars" passed the previous keyword guard. No network, no LLM.

_PLACES = ["Belem Tower", "Pier 39", "Crime Museum", "5 Star Diner", "Route 66 Museum"]


def _request() -> ItineraryNarrativeRequest:
    return ItineraryNarrativeRequest(
        destination="Lisbon, Portugal",
        start_date="2026-10-10",
        end_date="2026-10-11",
        travelers_count=2,
        interests=["history"],
        days=[
            ItineraryNarrativeDayInput(
                day_number=1,
                date="2026-10-10",
                experiences=[
                    ItineraryNarrativeExperienceInput(experience_id=f"exp_{i}", name=name, category="landmark")
                    for i, name in enumerate(_PLACES)
                ],
            )
        ],
    )


def _report(narrative: str, **extra: Any) -> ItineraryNarrativeReport:
    return ItineraryNarrativeReport.model_construct(
        status=ItineraryNarrativeStatus.SUCCESS,
        summary=extra.get("summary", "A two-day trip to Lisbon, Portugal."),
        daily_narratives=[
            ItineraryNarrativeDayOutput.model_construct(
                day_number=1, date=date(2026, 10, 10), title="Day 1", narrative=narrative,
                caveats=extra.get("caveats", []), referenced_experience_ids=[],
            )
        ],
        warnings=extra.get("warnings", []),
        assumptions=extra.get("assumptions", []),
    )


def _claims(narrative: str, **extra: Any) -> list[str]:
    return find_unsupported_factual_claims(_request(), _report(narrative, **extra))


@pytest.mark.parametrize(
    "narrative, category",
    [
        # price
        ("Belem Tower costs $12.", "price"),
        ("Entry is about 12 dollars.", "price"),
        ("Belem Tower costs 12 to enter.", "price"),
        ("Admission is 10.", "price"),
        ("Tickets are around 8 euros.", "price"),
        ("It is €15 per person.", "price"),
        ("Belem Tower has free admission.", "price"),
        ("An affordable stop near the river.", "price"),
        # rating
        ("Belem Tower is rated 4.8 stars.", "rating"),
        ("It has a rating of 4.6.", "rating"),
        ("A 4.8-star landmark.", "rating"),
        ("Scores 9 out of 10 with visitors.", "rating"),
        ("A five-star stop.", "rating"),
        ("One of the top-rated places.", "rating"),
        ("It has 2,300 reviews.", "rating"),
        ("Visitor reviews mention the view.", "rating"),
        # hours
        ("Belem Tower opens at 9am.", "opening_hours"),
        ("It closes at 5pm.", "opening_hours"),
        ("Open from 10 to 6.", "opening_hours"),
        ("It opens at 9.", "opening_hours"),
        ("Arrive by 14:30.", "opening_hours"),
        ("Closed on Mondays.", "opening_hours"),
        ("Check the opening hours first.", "opening_hours"),
        # availability / booking
        ("Tickets are available at the door.", "availability_booking"),
        ("Reservation required.", "availability_booking"),
        ("Reservations are recommended.", "availability_booking"),
        ("Book now to secure a place.", "availability_booking"),
        ("A few spots available each morning.", "availability_booking"),
        ("You should book ahead.", "availability_booking"),
        ("Tours are often sold out.", "availability_booking"),
        ("Your booking is confirmed.", "availability_booking"),
        # safety
        ("A very safe area to walk.", "safety"),
        ("This is a safe area.", "safety"),
        ("The district has low crime.", "safety"),
        ("It can be dangerous at night.", "safety"),
        # route facts
        ("Belem Tower is a 15-minute walk away.", "route"),
        ("It is 2 km away.", "route"),
        ("The next stop is 15 minutes away.", "route"),
        ("About 2.3 km from the river.", "route"),
        ("Just a short walk from the museum.", "route"),
        ("Allow 2 hours here.", "route"),
        ("Within walking distance of the centre.", "route"),
        ("Only 3 blocks from the square.", "route"),
    ],
)
def test_unsupported_factual_claim_forms_are_detected(narrative: str, category: str) -> None:
    assert category in _claims(narrative), narrative


def test_the_202c1d_sentence_is_now_caught_in_three_categories() -> None:
    found = _claims("Belem Tower opens at 9am, costs $12 and is rated 4.8 stars.")
    assert {"opening_hours", "price", "rating"} <= set(found)


@pytest.mark.parametrize(
    "narrative",
    [
        "Visit Belem Tower, then Pier 39.",
        "Day 1 has 5 places: Belem Tower, Pier 39, Crime Museum, 5 Star Diner and Route 66 Museum.",
        "The trip is for 2 travelers over 2 days.",
        "Route data available.",
        "Route data is not available for this day.",
        "No approved place for nightlife was available.",
        "Belem Tower serves the requested interest history.",
        "The plan has 3 warnings and 0 critical issues and needs review.",
        "A day may involve substantial geographic spread; you may want to review it.",
        "The trip runs from 2026-10-10 to 2026-10-11.",
        "On October 10, visit Belem Tower first.",
        "Weather: around 18°C with wind of 10 km/h and 12 mm of rain.",
        "Hotel and flight data were unavailable.",
        "The plan was adjusted after feasibility checks.",
        "Stop 1 is Belem Tower and stop 2 is Pier 39.",
    ],
)
def test_ordinary_grounded_narration_with_numbers_still_passes(narrative: str) -> None:
    assert _claims(narrative) == [], narrative


def test_claims_in_summary_caveats_warnings_and_assumptions_are_checked_too() -> None:
    plain = "Visit Belem Tower."
    assert "price" in _claims(plain, summary="A trip where entry costs $12.")
    assert "route" in _claims(plain, caveats=["Stops are 10 minutes away from each other."])
    assert "safety" in _claims(plain, warnings=["The area is very safe."])
    assert "availability_booking" in _claims(plain, assumptions=["Tickets are available."])


def test_supplied_place_names_are_never_mistaken_for_claims() -> None:
    # "Pier 39", "Crime Museum", "5 Star Diner", "Route 66 Museum" are real names in the request
    assert _claims("Visit Pier 39, then Crime Museum, then 5 Star Diner, then Route 66 Museum.") == []
    # ...but the same words outside a supplied name are still claims
    assert "safety" in _claims("Visit Pier 39; crime is low there.")
    assert "rating" in _claims("Pier 39 is a 5 star stop.")


def test_only_category_names_are_returned_never_text() -> None:
    found = _claims("Belem Tower costs $12 and is 2 km away.")
    assert set(found) <= {"price", "rating", "opening_hours", "availability_booking", "safety", "route"}
    assert all("Belem" not in item and "$" not in item for item in found)


# -- through the adapter + service: rejected, not retried, deterministic fallback -------------


class _Script:
    def __init__(self, *steps: Any) -> None:
        self._steps = list(steps)
        self.calls = 0

    def invoke(self, prompt: str) -> Any:
        self.calls += 1
        return self._steps.pop(0)


def _output(narrative: str) -> dict[str, Any]:
    return {
        "summary": "A short trip to Lisbon.",
        "daily_narratives": [
            {"day_number": 1, "title": "Day 1", "narrative": narrative, "caveats": [], "referenced_experience_ids": ["exp_belem_tower"]}
        ],
        "assumptions": [],
        "warnings": [],
    }


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


@pytest.mark.parametrize(
    "narrative",
    [
        "Belem Tower opens at 9am, costs $12 and is rated 4.8 stars.",
        "Belem Tower costs 12 dollars.",
        "Belem Tower has a rating of 4.6.",
        "Belem Tower closes at 5pm.",
        "Tickets are available for Belem Tower.",
        "Reservation required for Belem Tower.",
        "Belem Tower is in a very safe area.",
        "Belem Tower is a 15-minute walk from the start.",
        "Belem Tower is 2 km away.",
    ],
)
def test_unsupported_claim_is_rejected_without_a_retry_and_the_fallback_is_used(
    _narrator_enabled: None, narrative: str
) -> None:
    client = _Script(_output(narrative), _output("Visit Belem Tower."))  # a second answer exists but must not be requested
    state = ItineraryNarrativeService(provider=GroqItineraryNarratorProvider(client=client)).generate(_state())
    report = state.itinerary_narrative_report

    assert client.calls == 1  # a grounding rejection never triggers the structural retry
    assert report.status == ItineraryNarrativeStatus.FAILED
    assert report.narrative_source == "deterministic_fallback"
    text = " ".join([report.summary or ""] + [d.narrative for d in report.daily_narratives])
    assert "Belem Tower" in text  # the fact-only narrative still names the place
    for invented in ("$12", "12 dollars", "4.8", "4.6", "9am", "5pm", "available for", "Reservation", "safe", "15-minute", "2 km"):
        assert invented not in text


def test_ordinary_narration_is_still_accepted_through_the_service(_narrator_enabled: None) -> None:
    client = _Script(_output("Visit Belem Tower. Route data available."))
    state = ItineraryNarrativeService(provider=GroqItineraryNarratorProvider(client=client)).generate(_state())
    report = state.itinerary_narrative_report
    assert client.calls == 1
    assert report.status == ItineraryNarrativeStatus.SUCCESS
    assert report.daily_narratives[0].narrative == "Visit Belem Tower. Route data available."
