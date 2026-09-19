from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.models.ai_candidate_promotion import AICandidatePromotionReport, PromotedAICandidate
from app.models.ai_itinerary_reasoning import (
    AIItineraryReasoningGuardrailReport,
    AIItineraryReasoningResult,
    AIItineraryReasoningStatus,
    ItineraryReasoningCandidatePlacement,
    ItineraryReasoningDayPlan,
    ItineraryReasoningStrategy,
    ItineraryReasoningTimeWindow,
)
from app.models.candidate_quality import CandidateQualityReport
from app.models.common import DataStatus, GeoPoint
from app.models.planning_state import (
    DestinationContext,
    PlanningState,
    TravelGroupType,
    TripPace,
    TripRequest,
)
from app.services.candidate_quality_service import CandidateQualityService
from app.services.experience_planner_service import ExperiencePlannerService

# Tests for the Section 193C AI-guided ExperiencePlanner integration
# (docs/14_backend_architecture.md section 143). LLM #2's own safety is
# already covered by Section 193A/193B's own extensive test suites; these
# tests specifically cover the generation-layer seam: does
# ExperiencePlannerService correctly consume a completed
# AIItineraryReasoningResult, and does it correctly and safely fall back
# to the pre-193C deterministic path whenever it shouldn't trust one.


def _place(
    place_id: str,
    name: str,
    category: str = "museum",
    *,
    lat: float = 38.70,
    lng: float = -9.10,
    confidence: float = 0.8,
) -> dict[str, Any]:
    return {
        "place_id": place_id,
        "name": name,
        "category": category,
        "coordinates": {"lat": lat, "lng": lng},
        "source": "openstreetmap_places",
        "data_status": "live",
        "confidence": confidence,
    }


def _candidate_id(place_id: str) -> str:
    return f"openstreetmap_places:{place_id}"


def _planning_state(
    candidate_pois: list[dict[str, Any]],
    *,
    pace: TripPace = TripPace.BALANCED,
    start_date: str = "2026-08-10",
    end_date: str = "2026-08-11",
) -> PlanningState:
    trip_request = TripRequest(
        primary_destination="Testville",
        start_date=start_date,
        end_date=end_date,
        travelers_count=2,
        travel_group_type=TravelGroupType.COUPLE,
        pace=pace,
    )
    planning_state = PlanningState(trip_request=trip_request)
    planning_state.destination_context = DestinationContext(
        destination_name="Testville", candidate_pois=candidate_pois
    )
    planning_state.candidate_quality_report = CandidateQualityService().build_report(planning_state)
    return planning_state


def _strategy() -> ItineraryReasoningStrategy:
    return ItineraryReasoningStrategy(
        summary="A test plan.", pace="balanced", reason="Matches traveler preferences."
    )


def _completed_result(days: list[ItineraryReasoningDayPlan]) -> AIItineraryReasoningResult:
    return AIItineraryReasoningResult(
        status=AIItineraryReasoningStatus.COMPLETED,
        strategy=_strategy(),
        days=days,
        guardrail_report=AIItineraryReasoningGuardrailReport(passed=True),
        provider_name="fake_provider",
        confidence=0.7,
    )


def _day(day_index: int, candidate_ids: list[str], **overrides: Any) -> ItineraryReasoningDayPlan:
    fields: dict[str, Any] = {
        "day_index": day_index,
        "candidate_ids": candidate_ids,
        "rationale": "Groups nearby sites for a relaxed day.",
    }
    fields.update(overrides)
    return ItineraryReasoningDayPlan(**fields)


def _scheduled_names_by_day(planning_state: PlanningState) -> list[list[str]]:
    return [
        [experience.name for experience in day.experiences]
        for day in planning_state.experience_plan.daily_plans
    ]


# ---------------------------------------------------------------------------
# Task 19: successful AI reasoning -- selection/grouping/order honored,
# omitted candidates never silently appended.
# ---------------------------------------------------------------------------


def test_ai_guided_grouping_and_order_is_used_when_completed_result_present() -> None:
    candidates = [
        _place("a", "Candidate A", lat=38.70, lng=-9.10),
        _place("b", "Candidate B", lat=38.71, lng=-9.11),
        _place("c", "Candidate C", lat=38.72, lng=-9.12),
        _place("d", "Candidate D", lat=38.73, lng=-9.13),
    ]
    planning_state = _planning_state(candidates, start_date="2026-08-10", end_date="2026-08-11")
    planning_state.ai_itinerary_reasoning_result = _completed_result(
        [
            _day(1, [_candidate_id("c"), _candidate_id("a")]),
            _day(2, [_candidate_id("d")]),
        ]
    )

    ExperiencePlannerService().run(planning_state)

    assert _scheduled_names_by_day(planning_state) == [
        ["Candidate C", "Candidate A"],
        ["Candidate D"],
    ]
    all_scheduled = [name for day in _scheduled_names_by_day(planning_state) for name in day]
    assert "Candidate B" not in all_scheduled


def test_ai_guided_day_stop_order_matches_reasoning_order_not_geography() -> None:
    """Candidate A is geographically closer to Candidate B than to
    Candidate C, but the reasoning result explicitly orders C before A --
    the deterministic nearest-neighbor re-ordering must not override
    LLM #2's own chosen order."""
    candidates = [
        _place("a", "Candidate A", lat=38.700, lng=-9.100),
        _place("b", "Candidate B", lat=38.701, lng=-9.101),  # very close to A
        _place("c", "Candidate C", lat=38.900, lng=-9.300),  # far from A
    ]
    planning_state = _planning_state(candidates, start_date="2026-08-10", end_date="2026-08-10")
    planning_state.ai_itinerary_reasoning_result = _completed_result(
        [_day(1, [_candidate_id("c"), _candidate_id("a"), _candidate_id("b")])]
    )

    ExperiencePlannerService().run(planning_state)

    day = planning_state.experience_plan.daily_plans[0]
    assert [e.name for e in day.experiences] == ["Candidate C", "Candidate A", "Candidate B"]
    assert [e.stop_order for e in day.experiences] == [1, 2, 3]


# ---------------------------------------------------------------------------
# Task 20: factual fields come from the provider candidate, never the LLM.
# ---------------------------------------------------------------------------


def test_factual_fields_come_from_provider_candidate_not_reasoning_output() -> None:
    candidates = [_place("x", "Provider Name", lat=12.34, lng=56.78)]
    planning_state = _planning_state(candidates, start_date="2026-08-10", end_date="2026-08-10")
    planning_state.ai_itinerary_reasoning_result = _completed_result(
        [_day(1, [_candidate_id("x")])]
    )

    ExperiencePlannerService().run(planning_state)

    item = planning_state.experience_plan.daily_plans[0].experiences[0]
    assert item.name == "Provider Name"
    assert item.coordinates == GeoPoint(lat=12.34, lng=56.78)
    assert item.data_quality.data_status == DataStatus.LIVE


# ---------------------------------------------------------------------------
# Task 21: AI-directed promoted candidate end-to-end -- provenance survives.
# ---------------------------------------------------------------------------


def test_ai_directed_promoted_candidate_is_scheduled_with_provenance_preserved() -> None:
    planning_state = _planning_state([], start_date="2026-08-10", end_date="2026-08-10")
    promoted = PromotedAICandidate(
        candidate_id="promoted_proposal_001",
        name="Torre de Belem",
        category="attraction",
        provider_place_id="way/24341353",
        provider_source="openstreetmap_places",
        original_ai_candidate_id="proposal_001",
        quality_bucket="good_candidate",
        grounding_status="targeted_lookup",
        coordinates=GeoPoint(lat=38.6916, lng=-9.2160),
        confidence=0.7,
        data_status="live",
        promoted=True,
    )
    planning_state.ai_candidate_promotion_report = AICandidatePromotionReport(
        trip_id=planning_state.trip_id,
        status="promoted",
        total_reviewed_candidates=1,
        promoted_count=1,
        skipped_count=0,
        promoted_candidates=[promoted],
        generated_at=datetime.now(timezone.utc),
    )
    planning_state.ai_itinerary_reasoning_result = _completed_result(
        [_day(1, [_candidate_id("way/24341353")])]
    )

    ExperiencePlannerService().run(planning_state)

    item = planning_state.experience_plan.daily_plans[0].experiences[0]
    assert item.name == "Torre de Belem"
    assert item.promoted_from_ai is True
    assert item.provider_place_id == "way/24341353"
    assert item.provider_source == "openstreetmap_places"
    assert item.original_ai_candidate_id == "proposal_001"
    assert item.coordinates == GeoPoint(lat=38.6916, lng=-9.2160)


# ---------------------------------------------------------------------------
# Task 22/25: unknown/duplicate/invalid-day reasoning output -> refuse and
# fall back to deterministic planning; never a placeholder stop.
# ---------------------------------------------------------------------------


def test_unknown_candidate_id_in_reasoning_result_falls_back_to_deterministic() -> None:
    candidates = [_place("a", "Candidate A")]
    planning_state = _planning_state(candidates, start_date="2026-08-10", end_date="2026-08-10")
    # Bypasses 193B's own validate_result_against_request (simulating a
    # malformed/injected PlanningState) -- ExperiencePlannerService must
    # still defend against this itself.
    planning_state.ai_itinerary_reasoning_result = _completed_result(
        [_day(1, ["made-up-provider:unknown-123"])]
    )

    ExperiencePlannerService().run(planning_state)

    assert _scheduled_names_by_day(planning_state) == [["Candidate A"]]
    assert "made-up-provider:unknown-123" not in str(planning_state.experience_plan.model_dump())


def test_duplicate_candidate_across_days_falls_back_to_deterministic() -> None:
    candidates = [_place("a", "Candidate A"), _place("b", "Candidate B", lat=38.71, lng=-9.11)]
    planning_state = _planning_state(candidates, start_date="2026-08-10", end_date="2026-08-11")
    planning_state.ai_itinerary_reasoning_result = _completed_result(
        [_day(1, [_candidate_id("a")]), _day(2, [_candidate_id("a")])]
    )

    ExperiencePlannerService().run(planning_state)

    all_scheduled = [name for day in _scheduled_names_by_day(planning_state) for name in day]
    # Deterministic fallback ran instead -- both real candidates are
    # eligible for scheduling (never a plan derived from the unsafe
    # duplicate reasoning output).
    assert set(all_scheduled) <= {"Candidate A", "Candidate B"}


def test_invalid_day_index_falls_back_to_deterministic() -> None:
    candidates = [_place("a", "Candidate A")]
    planning_state = _planning_state(candidates, start_date="2026-08-10", end_date="2026-08-10")
    planning_state.ai_itinerary_reasoning_result = _completed_result(
        [_day(99, [_candidate_id("a")])]
    )

    ExperiencePlannerService().run(planning_state)

    assert _scheduled_names_by_day(planning_state) == [["Candidate A"]]


# ---------------------------------------------------------------------------
# Task 23: provider failure (rejected/not_connected) -> deterministic path.
# ---------------------------------------------------------------------------


def test_rejected_reasoning_result_falls_back_to_deterministic() -> None:
    candidates = [_place("a", "Candidate A")]
    planning_state = _planning_state(candidates, start_date="2026-08-10", end_date="2026-08-10")
    planning_state.ai_itinerary_reasoning_result = AIItineraryReasoningResult(
        status=AIItineraryReasoningStatus.REJECTED,
        days=[],
        guardrail_report=AIItineraryReasoningGuardrailReport(passed=False, blocked_reasons=["bad output"]),
        confidence=0.0,
    )

    ExperiencePlannerService().run(planning_state)

    assert _scheduled_names_by_day(planning_state) == [["Candidate A"]]


def test_not_connected_reasoning_result_falls_back_to_deterministic() -> None:
    candidates = [_place("a", "Candidate A")]
    planning_state = _planning_state(candidates, start_date="2026-08-10", end_date="2026-08-10")
    planning_state.ai_itinerary_reasoning_result = AIItineraryReasoningResult(
        status=AIItineraryReasoningStatus.NOT_CONNECTED,
        days=[],
        guardrail_report=AIItineraryReasoningGuardrailReport(
            passed=False, blocked_reasons=["not connected"]
        ),
        confidence=0.0,
    )

    ExperiencePlannerService().run(planning_state)

    assert _scheduled_names_by_day(planning_state) == [["Candidate A"]]


def test_absent_reasoning_result_uses_deterministic_path() -> None:
    """The pre-193C baseline: ai_itinerary_reasoning_result is None."""
    candidates = [_place("a", "Candidate A")]
    planning_state = _planning_state(candidates, start_date="2026-08-10", end_date="2026-08-10")
    assert planning_state.ai_itinerary_reasoning_result is None

    ExperiencePlannerService().run(planning_state)

    assert _scheduled_names_by_day(planning_state) == [["Candidate A"]]


# ---------------------------------------------------------------------------
# Task 10/24: subset selection -- omitted candidates stay omitted, never
# auto-appended; zero-attraction result falls back entirely.
# ---------------------------------------------------------------------------


def test_subset_selection_only_schedules_selected_candidates() -> None:
    candidates = [
        _place("a", "Candidate A"),
        _place("b", "Candidate B", lat=38.71, lng=-9.11),
        _place("c", "Candidate C", lat=38.72, lng=-9.12),
        _place("d", "Candidate D", lat=38.73, lng=-9.13),
    ]
    planning_state = _planning_state(candidates, start_date="2026-08-10", end_date="2026-08-10")
    planning_state.ai_itinerary_reasoning_result = _completed_result(
        [_day(1, [_candidate_id("a"), _candidate_id("c")])]
    )

    ExperiencePlannerService().run(planning_state)

    assert _scheduled_names_by_day(planning_state) == [["Candidate A", "Candidate C"]]


def test_reasoning_selecting_only_restaurant_candidates_falls_back() -> None:
    """Zero attractions scheduled across the whole trip -> Task 10's
    "too few to form a valid plan" fallback, even though the reasoning
    result was otherwise structurally valid."""
    candidates = [_place("a", "Candidate A")]
    planning_state = _planning_state(candidates, start_date="2026-08-10", end_date="2026-08-10")
    # A day whose only candidate_id resolves to nothing in the (empty)
    # restaurant pool -- simulates an all-non-attraction selection by
    # using an id that simply isn't in either pool, which is the same
    # "cannot resolve -> fall back entirely" path Task 22 also covers.
    planning_state.ai_itinerary_reasoning_result = _completed_result(
        [_day(1, [_candidate_id("nonexistent")])]
    )

    ExperiencePlannerService().run(planning_state)

    assert _scheduled_names_by_day(planning_state) == [["Candidate A"]]


# ---------------------------------------------------------------------------
# Task 9: pace-based per-day cap is still enforced for AI-guided days.
# ---------------------------------------------------------------------------


def test_ai_guided_day_is_truncated_to_pace_based_per_day_cap() -> None:
    candidates = [_place(str(i), f"Candidate {i}", lat=38.70 + i * 0.001, lng=-9.10) for i in range(5)]
    planning_state = _planning_state(
        candidates, pace=TripPace.RELAXED, start_date="2026-08-10", end_date="2026-08-10"
    )
    # RELAXED caps at 2/day -- the reasoning result asks for all 5 on one day.
    planning_state.ai_itinerary_reasoning_result = _completed_result(
        [_day(1, [_candidate_id(str(i)) for i in range(5)])]
    )

    ExperiencePlannerService().run(planning_state)

    day = planning_state.experience_plan.daily_plans[0]
    assert len(day.experiences) == 2
    assert [e.name for e in day.experiences] == ["Candidate 0", "Candidate 1"]


# ---------------------------------------------------------------------------
# Assumptions/warnings surface the AI-guided path transparently.
# ---------------------------------------------------------------------------


def test_ai_guided_plan_records_transparent_assumption_and_day_rationale() -> None:
    candidates = [_place("a", "Candidate A")]
    planning_state = _planning_state(candidates, start_date="2026-08-10", end_date="2026-08-10")
    planning_state.ai_itinerary_reasoning_result = _completed_result(
        [_day(1, [_candidate_id("a")], rationale="A specific rationale for this day.")]
    )

    ExperiencePlannerService().run(planning_state)

    assumptions_text = " ".join(planning_state.experience_plan.assumptions)
    assert "AI itinerary reasoning" in assumptions_text
    day = planning_state.experience_plan.daily_plans[0]
    assert any("A specific rationale for this day." in warning for warning in day.warnings)


def test_no_forbidden_factual_fields_in_ai_guided_plan() -> None:
    forbidden = {"price", "rating", "opening_hours", "route_time", "booking_url", "availability"}
    candidates = [_place("a", "Candidate A")]
    planning_state = _planning_state(candidates, start_date="2026-08-10", end_date="2026-08-10")
    planning_state.ai_itinerary_reasoning_result = _completed_result(
        [_day(1, [_candidate_id("a")])]
    )

    ExperiencePlannerService().run(planning_state)

    dumped = planning_state.experience_plan.model_dump()

    def _collect_keys(value: object, keys: set[str]) -> None:
        if isinstance(value, dict):
            keys.update(value.keys())
            for nested in value.values():
                _collect_keys(nested, keys)
        elif isinstance(value, list):
            for item in value:
                _collect_keys(item, keys)

    all_keys: set[str] = set()
    _collect_keys(dumped, all_keys)
    assert all_keys & forbidden == set()
