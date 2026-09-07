from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.models.accommodation import (
    AccommodationOffer,
    AccommodationSearchResult,
    AccommodationSearchStatus,
)
from app.models.common import DataStatus, ProviderStatus, RegenerationStrategy, ValidationSeverity
from app.models.flight import (
    FlightOffer,
    FlightSearchResult,
    FlightSearchStatus,
    FlightSegment,
)
from app.models.planning_state import (
    DestinationContext,
    FeedbackEvent,
    PendingFeedbackSummary,
    PlanDiffPreview,
    PlanningStage,
    PlanningState,
    RegenerationAttempt,
    RegenerationReadiness,
    TravelGroupType,
    TripRequest,
    UserLock,
    VersionHistoryItem,
)
from app.models.scraping import ScrapedDataConfidence, ScrapedDataProvenance
from app.models.routing import (
    BufferSufficiencyStatus,
    RouteAwareSequenceSuggestion,
    RouteAwareSequencingReport,
    RouteFeasibilityReport,
    RouteFeasibilityStatus,
    RouteLegFeasibility,
    RoutePathPoint,
    TravelTimeBuffer,
    TravelTimeBufferReport,
    TravelTimeBufferStatus,
)
from app.services.candidate_quality_service import CandidateQualityService
from app.services.experience_planner_service import ExperiencePlannerService
from app.services.plan_validator_service import PlanValidatorService

_FORBIDDEN_FACTUAL_FIELD_NAMES = {
    "price",
    "rating",
    "opening_hours",
    "route_time",
    "booking_url",
    "review_count",
    "ticket_price",
    "availability",
    "safety_score",
}


def _place(
    place_id: str,
    name: str,
    category: str | None,
    *,
    lat: float | None = 0.0,
    lng: float | None = 0.0,
    confidence: float = 0.6,
    address: str | None = None,
) -> dict[str, Any]:
    return {
        "place_id": place_id,
        "name": name,
        "category": category,
        "coordinates": {"lat": lat, "lng": lng} if lat is not None and lng is not None else None,
        "address": address,
        "source": "openstreetmap_places",
        "data_status": "live",
        "confidence": confidence,
    }


def _planning_state(
    candidate_pois: list[dict[str, Any]] | None = None,
    *,
    must_visit: list[str] | None = None,
    destination_name: str = "Lisbon, Portugal",
    start_date: str = "2026-08-10",
    end_date: str = "2026-08-12",
) -> PlanningState:
    trip_request = TripRequest(
        primary_destination=destination_name,
        start_date=start_date,
        end_date=end_date,
        travelers_count=2,
        travel_group_type=TravelGroupType.COUPLE,
        must_visit=must_visit or [],
    )
    planning_state = PlanningState(trip_request=trip_request)
    planning_state.destination_context = DestinationContext(
        destination_name=destination_name,
        candidate_pois=candidate_pois or [],
    )
    planning_state.candidate_quality_report = CandidateQualityService().build_report(
        planning_state
    )
    return planning_state


def _run_planner_then_validator(planning_state: PlanningState) -> PlanningState:
    ExperiencePlannerService().run(planning_state)
    PlanValidatorService().run(planning_state)
    return planning_state


def _scheduled_names(planning_state: PlanningState) -> list[str]:
    return [
        experience.name
        for day_plan in planning_state.experience_plan.daily_plans
        for experience in day_plan.experiences
    ]


def _accommodation_inventory_warnings(planning_state: PlanningState) -> list[Any]:
    return [
        warning
        for warning in planning_state.validation_report.warnings
        if warning.category == "accommodation_inventory"
    ]


def _flight_inventory_warnings(planning_state: PlanningState) -> list[Any]:
    return [
        warning
        for warning in planning_state.validation_report.warnings
        if warning.category == "flight_inventory"
    ]


def _must_visit_warnings(planning_state: PlanningState) -> list[Any]:
    return [
        warning
        for warning in planning_state.validation_report.warnings
        if warning.category == "must_visit"
    ]


# ---------------------------------------------------------------------------
# Lisbon-style mismatched must-visits: destination_context has Lisbon
# candidates only, but must_visit names Paris landmarks that were never
# grounded to this destination.
# ---------------------------------------------------------------------------


def test_lisbon_plan_surfaces_ungrounded_must_visits_without_substitution() -> None:
    candidates = [
        _place("p1", "Belem Tower", "attraction", lat=38.6916, lng=-9.2160),
        _place("p2", "Lisbon Cathedral", "attraction", lat=38.7095, lng=-9.1332),
    ]
    planning_state = _planning_state(
        candidate_pois=candidates,
        must_visit=["Eiffel Tower", "Louvre Museum"],
        destination_name="Lisbon, Portugal",
    )

    _run_planner_then_validator(planning_state)
    scheduled_names = _scheduled_names(planning_state)

    # Neither ungrounded must-visit is scheduled, and no unrelated
    # attraction is substituted in their place -- only the real Lisbon
    # candidates ever appear.
    assert "Eiffel Tower" not in scheduled_names
    assert "Louvre Museum" not in scheduled_names
    assert set(scheduled_names) == {"Belem Tower", "Lisbon Cathedral"}

    must_visit_warnings = _must_visit_warnings(planning_state)
    assert len(must_visit_warnings) == 1
    message = must_visit_warnings[0].message
    assert "Eiffel Tower" in message
    assert "Louvre Museum" in message
    assert "not grounded/scheduled" in message
    assert "not replaced with unrelated attractions" in message

    # A failed must-visit never claims the plan is ready.
    assert planning_state.validation_report.readiness_status.value in {
        "needs_review",
        "blocked",
    }
    # Real candidates were scheduled here, so this specific plan is
    # needs_review, not blocked -- the failed must-visit is a warning only.
    assert planning_state.validation_report.readiness_status.value == "needs_review"


# ---------------------------------------------------------------------------
# Matched must-visit: no failed-must-visit warning is produced.
# ---------------------------------------------------------------------------


def test_matched_must_visit_produces_no_warning() -> None:
    candidates = [
        _place("p1", "Empire State Building", "attraction", lat=40.7484, lng=-73.9857),
        _place("p2", "Central Park", "park", lat=40.7851, lng=-73.9683),
    ]
    planning_state = _planning_state(
        candidate_pois=candidates,
        must_visit=["Empire State Building"],
        destination_name="New York",
    )

    _run_planner_then_validator(planning_state)
    scheduled_names = _scheduled_names(planning_state)

    assert "Empire State Building" in scheduled_names
    assert _must_visit_warnings(planning_state) == []


# ---------------------------------------------------------------------------
# Severe rejected must-visit: candidate exists but is excluded from
# scheduling by candidate quality (missing coordinates), so it still needs
# to surface a warning even though it *is* a real, grounded candidate.
# ---------------------------------------------------------------------------


def test_severe_rejected_must_visit_still_surfaces_warning() -> None:
    candidates = [
        _place("p1", "Ungrounded Fort", "attraction", lat=None, lng=None),
        _place("p2", "City Museum", "museum", lat=38.7, lng=-9.15),
    ]
    planning_state = _planning_state(
        candidate_pois=candidates,
        must_visit=["Ungrounded Fort"],
    )

    _run_planner_then_validator(planning_state)
    scheduled_names = _scheduled_names(planning_state)

    # The candidate exists in candidate_pois (it was "found" in the older
    # sense) but is rejected by CandidateQualityService for missing
    # coordinates, so ExperiencePlannerService never schedules it.
    reject_score = next(
        score
        for score in planning_state.candidate_quality_report.attraction_scores
        if score.candidate_name == "Ungrounded Fort"
    )
    assert reject_score.quality_tier.value == "rejected"
    assert "Ungrounded Fort" not in scheduled_names
    assert "City Museum" in scheduled_names

    must_visit_warnings = _must_visit_warnings(planning_state)
    assert len(must_visit_warnings) == 1
    assert "Ungrounded Fort" in must_visit_warnings[0].message
    assert planning_state.validation_report.readiness_status.value != "ready"


# ---------------------------------------------------------------------------
# No forbidden factual fields appear anywhere in the validation report.
# ---------------------------------------------------------------------------


def _assert_no_forbidden_fields(node: Any) -> None:
    if isinstance(node, dict):
        overlap = set(node.keys()) & _FORBIDDEN_FACTUAL_FIELD_NAMES
        assert not overlap, f"Forbidden field(s) found: {overlap}"
        for value in node.values():
            _assert_no_forbidden_fields(value)
    elif isinstance(node, list):
        for item in node:
            _assert_no_forbidden_fields(item)


def test_validation_report_has_no_forbidden_factual_fields() -> None:
    candidates = [
        _place("p1", "Belem Tower", "attraction", lat=38.6916, lng=-9.2160),
    ]
    planning_state = _planning_state(
        candidate_pois=candidates,
        must_visit=["Eiffel Tower"],
    )

    _run_planner_then_validator(planning_state)
    report_dump = planning_state.validation_report.model_dump(mode="json")

    _assert_no_forbidden_fields(report_dump)


# ---------------------------------------------------------------------------
# Step 165E: PlanValidatorService consumes planning_state.route_feasibility_report.
# Route lookups already happened elsewhere (RouteFeasibilityService, run by
# PlanningOrchestrator before validation) -- these tests set the report
# directly and never call a provider or network from the validator itself.
# ---------------------------------------------------------------------------


def _feasibility_warning(planning_state: PlanningState) -> Any:
    matches = [
        warning
        for warning in planning_state.validation_report.warnings
        if warning.category == "feasibility"
    ]
    assert len(matches) == 1
    return matches[0]


def _two_candidate_planning_state() -> PlanningState:
    candidates = [
        _place("p1", "Belem Tower", "attraction", lat=38.6916, lng=-9.2160),
        _place("p2", "Lisbon Cathedral", "attraction", lat=38.7095, lng=-9.1332),
    ]
    return _planning_state(candidate_pois=candidates)


def test_no_route_feasibility_report_falls_back_to_original_not_implemented_warning() -> None:
    planning_state = _two_candidate_planning_state()
    ExperiencePlannerService().run(planning_state)
    assert planning_state.route_feasibility_report is None

    PlanValidatorService().run(planning_state)

    warning = _feasibility_warning(planning_state)
    assert "not implemented" in warning.message
    assert planning_state.validation_report.readiness_status.value == "needs_review"


def test_successful_route_data_replaces_blanket_warning_with_provider_backed_message() -> None:
    planning_state = _two_candidate_planning_state()
    ExperiencePlannerService().run(planning_state)
    scheduled = planning_state.experience_plan.daily_plans[0].experiences
    assert len(scheduled) == 2

    planning_state.route_feasibility_report = RouteFeasibilityReport(
        status=ProviderStatus.SUCCESS,
        provider="osrm",
        route_data_source="osrm",
        legs=[
            RouteLegFeasibility(
                from_experience_id=scheduled[0].experience_id,
                from_experience_name=scheduled[0].name,
                to_experience_id=scheduled[1].experience_id,
                to_experience_name=scheduled[1].name,
                provider="osrm",
                status=ProviderStatus.SUCCESS,
                distance_meters=1200.0,
                duration_seconds=600.0,
                feasibility_status=RouteFeasibilityStatus.FEASIBLE,
                message="A provider-backed route was found for this leg.",
            )
        ],
    )

    PlanValidatorService().run(planning_state)

    warning = _feasibility_warning(planning_state)
    # The blanket fallback wording ("...checks are not implemented yet, so
    # this plan needs review...") is gone -- replaced by a message that
    # actually describes the provider-backed route data found.
    assert "checks are not implemented yet" not in warning.message
    assert "provider-backed" in warning.message
    assert "osrm" in warning.message
    # Still never claims the plan is ready -- full route-aware scheduling
    # (Section 166) is still not implemented.
    assert planning_state.validation_report.readiness_status.value == "needs_review"


def test_not_connected_routing_keeps_needs_review_and_names_no_provider_connected() -> None:
    planning_state = _two_candidate_planning_state()
    ExperiencePlannerService().run(planning_state)
    scheduled = planning_state.experience_plan.daily_plans[0].experiences

    planning_state.route_feasibility_report = RouteFeasibilityReport(
        status=ProviderStatus.NOT_CONNECTED,
        provider="not_connected",
        route_data_source="not_connected",
        legs=[
            RouteLegFeasibility(
                from_experience_id=scheduled[0].experience_id,
                from_experience_name=scheduled[0].name,
                to_experience_id=scheduled[1].experience_id,
                to_experience_name=scheduled[1].name,
                provider="not_connected",
                status=ProviderStatus.NOT_CONNECTED,
                distance_meters=None,
                duration_seconds=None,
                feasibility_status=RouteFeasibilityStatus.NEEDS_REVIEW,
                message="No routing provider is connected, so this leg's route feasibility could not be checked.",
            )
        ],
    )

    PlanValidatorService().run(planning_state)

    warning = _feasibility_warning(planning_state)
    assert "No routing provider is connected" in warning.message
    assert "provider-backed" not in warning.message
    assert planning_state.validation_report.readiness_status.value == "needs_review"


def test_route_feasibility_warning_never_fabricates_distance_or_duration() -> None:
    planning_state = _two_candidate_planning_state()
    ExperiencePlannerService().run(planning_state)
    scheduled = planning_state.experience_plan.daily_plans[0].experiences

    planning_state.route_feasibility_report = RouteFeasibilityReport(
        status=ProviderStatus.SUCCESS,
        provider="osrm",
        route_data_source="osrm",
        legs=[
            RouteLegFeasibility(
                from_experience_id=scheduled[0].experience_id,
                from_experience_name=scheduled[0].name,
                to_experience_id=scheduled[1].experience_id,
                to_experience_name=scheduled[1].name,
                provider="osrm",
                status=ProviderStatus.SUCCESS,
                distance_meters=1200.0,
                duration_seconds=600.0,
                feasibility_status=RouteFeasibilityStatus.FEASIBLE,
                message="A provider-backed route was found for this leg.",
            )
        ],
    )

    PlanValidatorService().run(planning_state)

    warning = _feasibility_warning(planning_state)
    # The warning message summarizes leg counts, never a specific
    # distance/duration figure.
    assert "1200" not in warning.message
    assert "600" not in warning.message


# ---------------------------------------------------------------------------
# Step 166C: PlanValidatorService consumes planning_state.travel_time_buffer_report.
# Route lookups already happened elsewhere (TravelTimeBufferService, run by
# PlanningOrchestrator before validation) -- these tests set the report
# directly and never call a provider or network from the validator itself.
# ---------------------------------------------------------------------------


def _travel_time_buffer_warnings(planning_state: PlanningState) -> list[Any]:
    return [
        warning
        for warning in planning_state.validation_report.warnings
        if warning.category == "travel_time_buffer"
    ]


def test_insufficient_buffer_produces_a_needs_review_warning() -> None:
    planning_state = _two_candidate_planning_state()
    ExperiencePlannerService().run(planning_state)
    scheduled = planning_state.experience_plan.daily_plans[0].experiences

    planning_state.travel_time_buffer_report = TravelTimeBufferReport(
        status=ProviderStatus.SUCCESS,
        buffers=[
            TravelTimeBuffer(
                from_experience_id=scheduled[0].experience_id,
                from_experience_name=scheduled[0].name,
                to_experience_id=scheduled[1].experience_id,
                to_experience_name=scheduled[1].name,
                provider="osrm",
                status=TravelTimeBufferStatus.SUCCESS,
                route_duration_seconds=900.0,
                route_distance_meters=1500.0,
                recommended_buffer_seconds=900.0,
                available_gap_seconds=300.0,
                buffer_status=BufferSufficiencyStatus.INSUFFICIENT,
                message="A provider-backed travel duration exceeds the scheduled gap.",
            )
        ],
    )

    PlanValidatorService().run(planning_state)

    buffer_warnings = _travel_time_buffer_warnings(planning_state)
    assert len(buffer_warnings) == 1
    message = buffer_warnings[0].message
    assert scheduled[0].name in message
    assert scheduled[1].name in message
    assert "900" in message
    assert "300" in message
    # Never a critical issue, never claims the plan is ready.
    assert planning_state.validation_report.readiness_status.value == "needs_review"
    assert not any(
        issue.category == "travel_time_buffer"
        for issue in planning_state.validation_report.critical_issues
    )


def test_sufficient_buffer_produces_no_warning_for_that_leg() -> None:
    planning_state = _two_candidate_planning_state()
    ExperiencePlannerService().run(planning_state)
    scheduled = planning_state.experience_plan.daily_plans[0].experiences

    planning_state.travel_time_buffer_report = TravelTimeBufferReport(
        status=ProviderStatus.SUCCESS,
        buffers=[
            TravelTimeBuffer(
                from_experience_id=scheduled[0].experience_id,
                from_experience_name=scheduled[0].name,
                to_experience_id=scheduled[1].experience_id,
                to_experience_name=scheduled[1].name,
                provider="osrm",
                status=TravelTimeBufferStatus.SUCCESS,
                route_duration_seconds=300.0,
                route_distance_meters=500.0,
                recommended_buffer_seconds=300.0,
                available_gap_seconds=1200.0,
                buffer_status=BufferSufficiencyStatus.SUFFICIENT,
                message="A provider-backed travel duration fits within the scheduled gap.",
            )
        ],
    )

    PlanValidatorService().run(planning_state)

    assert _travel_time_buffer_warnings(planning_state) == []
    assert planning_state.validation_report.readiness_status.value == "needs_review"


def test_not_computable_buffer_never_invents_sufficiency() -> None:
    """No schedule timestamps exist (the case for every plan this app
    currently generates) -- the validator must never guess sufficiency
    either way."""
    planning_state = _two_candidate_planning_state()
    ExperiencePlannerService().run(planning_state)
    scheduled = planning_state.experience_plan.daily_plans[0].experiences

    planning_state.travel_time_buffer_report = TravelTimeBufferReport(
        status=ProviderStatus.SUCCESS,
        buffers=[
            TravelTimeBuffer(
                from_experience_id=scheduled[0].experience_id,
                from_experience_name=scheduled[0].name,
                to_experience_id=scheduled[1].experience_id,
                to_experience_name=scheduled[1].name,
                provider="osrm",
                status=TravelTimeBufferStatus.SUCCESS,
                route_duration_seconds=900.0,
                route_distance_meters=1500.0,
                recommended_buffer_seconds=900.0,
                available_gap_seconds=None,
                buffer_status=BufferSufficiencyStatus.NOT_COMPUTABLE,
                message="No schedule timestamps exist for these experiences.",
            )
        ],
    )

    PlanValidatorService().run(planning_state)

    assert _travel_time_buffer_warnings(planning_state) == []
    assert planning_state.validation_report.readiness_status.value == "needs_review"


def test_unavailable_buffer_keeps_needs_review_without_new_warning() -> None:
    planning_state = _two_candidate_planning_state()
    ExperiencePlannerService().run(planning_state)
    scheduled = planning_state.experience_plan.daily_plans[0].experiences

    planning_state.travel_time_buffer_report = TravelTimeBufferReport(
        status=ProviderStatus.NOT_CONNECTED,
        buffers=[
            TravelTimeBuffer(
                from_experience_id=scheduled[0].experience_id,
                from_experience_name=scheduled[0].name,
                to_experience_id=scheduled[1].experience_id,
                to_experience_name=scheduled[1].name,
                provider="not_connected",
                status=TravelTimeBufferStatus.NOT_CONNECTED,
                route_duration_seconds=None,
                route_distance_meters=None,
                recommended_buffer_seconds=None,
                available_gap_seconds=None,
                buffer_status=BufferSufficiencyStatus.UNAVAILABLE,
                message="No routing provider is connected.",
            )
        ],
    )

    PlanValidatorService().run(planning_state)

    assert _travel_time_buffer_warnings(planning_state) == []
    # No critical issue is ever created just because routing is
    # unavailable/not connected.
    assert planning_state.validation_report.readiness_status.value == "needs_review"
    assert planning_state.validation_report.critical_issues == []


def test_no_travel_time_buffer_report_produces_no_warning() -> None:
    planning_state = _two_candidate_planning_state()
    ExperiencePlannerService().run(planning_state)
    assert planning_state.travel_time_buffer_report is None

    PlanValidatorService().run(planning_state)

    assert _travel_time_buffer_warnings(planning_state) == []
    assert planning_state.validation_report.readiness_status.value == "needs_review"


# ---------------------------------------------------------------------------
# Step 166D hardening: no duplicate/conflicting warnings for the same leg,
# and the validator never claims route timing was checked when it wasn't.
# ---------------------------------------------------------------------------


def test_duplicate_insufficient_buffer_entries_for_same_leg_produce_one_warning() -> None:
    """Defensive: TravelTimeBufferService itself never produces two buffer
    entries for the same leg, but the validator must never emit more than
    one warning about the same leg even if a report somehow did."""
    planning_state = _two_candidate_planning_state()
    ExperiencePlannerService().run(planning_state)
    scheduled = planning_state.experience_plan.daily_plans[0].experiences

    duplicate_buffer = TravelTimeBuffer(
        from_experience_id=scheduled[0].experience_id,
        from_experience_name=scheduled[0].name,
        to_experience_id=scheduled[1].experience_id,
        to_experience_name=scheduled[1].name,
        provider="osrm",
        status=TravelTimeBufferStatus.SUCCESS,
        route_duration_seconds=900.0,
        route_distance_meters=1500.0,
        recommended_buffer_seconds=900.0,
        available_gap_seconds=300.0,
        buffer_status=BufferSufficiencyStatus.INSUFFICIENT,
        message="A provider-backed travel duration exceeds the scheduled gap.",
    )
    planning_state.travel_time_buffer_report = TravelTimeBufferReport(
        status=ProviderStatus.SUCCESS,
        buffers=[duplicate_buffer, duplicate_buffer.model_copy()],
    )

    PlanValidatorService().run(planning_state)

    assert len(_travel_time_buffer_warnings(planning_state)) == 1


def test_multiple_non_feasible_legs_produce_exactly_one_feasibility_warning() -> None:
    """The blanket feasibility warning is a single, aggregate WARNING --
    never one per leg -- so multiple non-feasible legs never produce
    duplicate/conflicting feasibility warnings."""
    planning_state = _two_candidate_planning_state()
    ExperiencePlannerService().run(planning_state)
    scheduled = planning_state.experience_plan.daily_plans[0].experiences

    planning_state.route_feasibility_report = RouteFeasibilityReport(
        status=ProviderStatus.NOT_CONNECTED,
        provider="not_connected",
        route_data_source="not_connected",
        legs=[
            RouteLegFeasibility(
                from_experience_id=scheduled[0].experience_id,
                from_experience_name=scheduled[0].name,
                to_experience_id=scheduled[1].experience_id,
                to_experience_name=scheduled[1].name,
                provider="not_connected",
                status=ProviderStatus.NOT_CONNECTED,
                feasibility_status=RouteFeasibilityStatus.NEEDS_REVIEW,
                message="No routing provider is connected.",
            )
        ],
    )

    PlanValidatorService().run(planning_state)

    feasibility_warnings = [
        warning
        for warning in planning_state.validation_report.warnings
        if warning.category == "feasibility"
    ]
    assert len(feasibility_warnings) == 1


def test_validator_never_claims_route_timing_was_checked_when_unavailable() -> None:
    planning_state = _two_candidate_planning_state()
    ExperiencePlannerService().run(planning_state)
    assert planning_state.route_feasibility_report is None
    assert planning_state.travel_time_buffer_report is None

    PlanValidatorService().run(planning_state)

    feasibility_warning = _feasibility_warning(planning_state)
    # Must explicitly deny that timing/feasibility checks happened -- never
    # an affirmative "checked" claim standing alone.
    assert "not implemented yet" in feasibility_warning.message
    assert "needs review" in feasibility_warning.message
    assert planning_state.validation_report.readiness_status.value != "ready"
    # No blocking failure is ever created just because routing/timing data
    # is unavailable.
    assert planning_state.validation_report.critical_issues == []


# ---------------------------------------------------------------------------
# Step 167D: accommodation_inventory_report clarity in PlanValidatorService.
# Every case here is a non-blocking WARNING -- missing/unconnected bookable
# lodging inventory never produces a critical_issue and never blocks
# generation by itself.
# ---------------------------------------------------------------------------


def test_accommodation_inventory_warning_when_report_missing() -> None:
    """When accommodation_inventory_report hasn't been computed at all
    (None), the validator still surfaces exactly one honest warning --
    never silence, and never a claim that lodging was checked."""
    planning_state = _planning_state()
    assert planning_state.accommodation_inventory_report is None

    PlanValidatorService().run(planning_state)

    warnings = _accommodation_inventory_warnings(planning_state)
    assert len(warnings) == 1
    assert "no accommodation inventory provider is connected" in warnings[0].message.lower()
    assert "could not be checked" in warnings[0].message.lower()
    assert not any(
        issue.category == "accommodation_inventory"
        for issue in planning_state.validation_report.critical_issues
    )


def test_accommodation_inventory_warning_when_not_connected() -> None:
    planning_state = _planning_state()
    planning_state.accommodation_inventory_report = AccommodationSearchResult(
        provider="accommodation_inventory_provider",
        status=AccommodationSearchStatus.NOT_CONNECTED,
        offers=[],
        message="Accommodation inventory provider is not connected.",
    )

    PlanValidatorService().run(planning_state)

    warnings = _accommodation_inventory_warnings(planning_state)
    assert len(warnings) == 1
    assert "no accommodation inventory provider is connected" in warnings[0].message.lower()
    assert not any(
        issue.category == "accommodation_inventory"
        for issue in planning_state.validation_report.critical_issues
    )


def test_accommodation_inventory_warning_when_failed() -> None:
    planning_state = _planning_state()
    planning_state.accommodation_inventory_report = AccommodationSearchResult(
        provider="accommodation_inventory_provider",
        status=AccommodationSearchStatus.FAILED,
        offers=[],
        message="The accommodation inventory provider request failed unexpectedly.",
    )

    PlanValidatorService().run(planning_state)

    warnings = _accommodation_inventory_warnings(planning_state)
    assert len(warnings) == 1
    assert "failed" in warnings[0].message.lower()
    assert not any(
        issue.category == "accommodation_inventory"
        for issue in planning_state.validation_report.critical_issues
    )


def test_accommodation_inventory_warning_when_success_with_no_offers() -> None:
    """A `success` status with zero offers is still reported as
    unavailable -- never claims a hotel is bookable when none was found."""
    planning_state = _planning_state()
    planning_state.accommodation_inventory_report = AccommodationSearchResult(
        provider="fake_accommodation_inventory_provider",
        status=AccommodationSearchStatus.SUCCESS,
        offers=[],
        message="No properties matched this search.",
    )

    PlanValidatorService().run(planning_state)

    warnings = _accommodation_inventory_warnings(planning_state)
    assert len(warnings) == 1
    assert "no bookable lodging offers" in warnings[0].message.lower()
    assert not any(
        issue.category == "accommodation_inventory"
        for issue in planning_state.validation_report.critical_issues
    )


def test_accommodation_inventory_warning_when_success_with_offers() -> None:
    """A real, provider-backed offer is named honestly, but never claimed
    to have been reviewed for accuracy or scheduled into the itinerary."""
    planning_state = _planning_state()
    offer = AccommodationOffer(
        provider="fake_accommodation_inventory_provider",
        provider_property_id="prop_1",
        property_name="Fake Property",
        data_status=DataStatus.LIVE,
    )
    planning_state.accommodation_inventory_report = AccommodationSearchResult(
        provider="fake_accommodation_inventory_provider",
        status=AccommodationSearchStatus.SUCCESS,
        offers=[offer],
    )

    PlanValidatorService().run(planning_state)

    warnings = _accommodation_inventory_warnings(planning_state)
    assert len(warnings) == 1
    message_lower = warnings[0].message.lower()
    assert "1 provider-backed bookable accommodation offer" in message_lower
    assert "not scheduled into the itinerary" in message_lower
    assert not any(
        issue.category == "accommodation_inventory"
        for issue in planning_state.validation_report.critical_issues
    )


def test_accommodation_inventory_warning_for_scraped_offer_calls_out_non_official_status() -> None:
    """Step 168E: when an offer carries `scraped_provenance`, the warning
    must explicitly say this is scraped_public_page/experimental/fragile
    data, not official-provider data, and must never claim official
    price/availability/rating/booking-link verification for it."""
    planning_state = _planning_state()
    offer = AccommodationOffer(
        provider="scraped:example_test_only_travel_blog",
        provider_property_id="prop_1",
        property_name="TEST_ONLY_SCRAPED_PROPERTY_ALPHA",
        data_status=DataStatus.SCRAPED_PUBLIC_PAGE,
        scraped_provenance=ScrapedDataProvenance(
            source_id="example_test_only_travel_blog",
            source_name="Example Test-Only Travel Blog",
            confidence=ScrapedDataConfidence.EXPERIMENTAL,
        ),
    )
    planning_state.accommodation_inventory_report = AccommodationSearchResult(
        provider="scraped:example_test_only_travel_blog",
        status=AccommodationSearchStatus.SUCCESS,
        offers=[offer],
    )

    PlanValidatorService().run(planning_state)

    warnings = _accommodation_inventory_warnings(planning_state)
    assert len(warnings) == 1
    message_lower = warnings[0].message.lower()
    assert "scraped_public_page" in message_lower
    assert "not official-provider data" in message_lower
    assert "has not been verified" in message_lower
    assert not any(
        issue.category == "accommodation_inventory"
        for issue in planning_state.validation_report.critical_issues
    )


def test_accommodation_inventory_warning_never_blocks_generation() -> None:
    """Missing/unconnected accommodation inventory never contributes a
    critical_issue on its own, regardless of scheduling outcome."""
    planning_state = _two_candidate_planning_state()
    _run_planner_then_validator(planning_state)

    assert planning_state.accommodation_inventory_report is None
    warnings = _accommodation_inventory_warnings(planning_state)
    assert len(warnings) == 1
    assert not any(
        issue.category == "accommodation_inventory"
        for issue in planning_state.validation_report.critical_issues
    )


# ---------------------------------------------------------------------------
# Step 169E: flight_inventory_report clarity in PlanValidatorService.
# Every case here is a non-blocking WARNING -- missing/unconnected flight
# inventory never produces a critical_issue and never blocks generation by
# itself. Mirrors the accommodation_inventory test block above exactly.
# ---------------------------------------------------------------------------


def test_flight_inventory_warning_when_report_missing() -> None:
    """When flight_inventory_report hasn't been computed at all (None),
    the validator still surfaces exactly one honest warning -- never
    silence, and never a claim that flights were checked."""
    planning_state = _planning_state()
    assert planning_state.flight_inventory_report is None

    PlanValidatorService().run(planning_state)

    warnings = _flight_inventory_warnings(planning_state)
    assert len(warnings) == 1
    assert "no flight inventory provider is connected" in warnings[0].message.lower()
    assert "could not be checked" in warnings[0].message.lower()
    assert not any(
        issue.category == "flight_inventory"
        for issue in planning_state.validation_report.critical_issues
    )


def test_flight_inventory_warning_when_not_connected() -> None:
    planning_state = _planning_state()
    planning_state.flight_inventory_report = FlightSearchResult(
        provider="flight_inventory_provider",
        status=FlightSearchStatus.NOT_CONNECTED,
        offers=[],
        message="Flight inventory provider is not connected.",
        destination="Lisbon, Portugal",
        departure_date="2026-08-10",
    )

    PlanValidatorService().run(planning_state)

    warnings = _flight_inventory_warnings(planning_state)
    assert len(warnings) == 1
    assert "no flight inventory provider is connected" in warnings[0].message.lower()
    assert not any(
        issue.category == "flight_inventory"
        for issue in planning_state.validation_report.critical_issues
    )


def test_flight_inventory_warning_when_failed() -> None:
    planning_state = _planning_state()
    planning_state.flight_inventory_report = FlightSearchResult(
        provider="flight_inventory_provider",
        status=FlightSearchStatus.FAILED,
        offers=[],
        message="The flight inventory provider request failed unexpectedly.",
        destination="Lisbon, Portugal",
        departure_date="2026-08-10",
    )

    PlanValidatorService().run(planning_state)

    warnings = _flight_inventory_warnings(planning_state)
    assert len(warnings) == 1
    assert "failed" in warnings[0].message.lower()
    assert not any(
        issue.category == "flight_inventory"
        for issue in planning_state.validation_report.critical_issues
    )


def test_flight_inventory_warning_when_success_with_no_offers() -> None:
    """A `success` status with zero offers is still reported as
    unavailable -- never claims a flight is bookable when none was
    found."""
    planning_state = _planning_state()
    planning_state.flight_inventory_report = FlightSearchResult(
        provider="fake_flight_inventory_provider",
        status=FlightSearchStatus.SUCCESS,
        offers=[],
        message="No flights matched this search.",
        destination="Lisbon, Portugal",
        departure_date="2026-08-10",
    )

    PlanValidatorService().run(planning_state)

    warnings = _flight_inventory_warnings(planning_state)
    assert len(warnings) == 1
    assert "no flight offers" in warnings[0].message.lower()
    assert not any(
        issue.category == "flight_inventory"
        for issue in planning_state.validation_report.critical_issues
    )


def test_flight_inventory_warning_when_success_with_offers() -> None:
    """A real, provider-backed offer is named honestly, but never claimed
    to have been reviewed for accuracy or scheduled into the itinerary."""
    planning_state = _planning_state()
    segment = FlightSegment(
        origin_airport="TST",
        destination_airport="DMO",
        carrier_name="TEST_ONLY_AIRLINE_ALPHA",
        flight_number="TEST_ONLY_FLIGHT_123",
        data_status=DataStatus.LIVE,
    )
    offer = FlightOffer(
        offer_id="TEST_ONLY_FLIGHT_OFFER_ALPHA",
        provider="fake_flight_inventory_provider",
        data_status=DataStatus.LIVE,
        outbound_segments=[segment],
    )
    planning_state.flight_inventory_report = FlightSearchResult(
        provider="fake_flight_inventory_provider",
        status=FlightSearchStatus.SUCCESS,
        offers=[offer],
        destination="Lisbon, Portugal",
        departure_date="2026-08-10",
    )

    PlanValidatorService().run(planning_state)

    warnings = _flight_inventory_warnings(planning_state)
    assert len(warnings) == 1
    message_lower = warnings[0].message.lower()
    assert "1 provider-backed flight offer" in message_lower
    assert "not scheduled into the itinerary" in message_lower
    assert not any(
        issue.category == "flight_inventory"
        for issue in planning_state.validation_report.critical_issues
    )


def test_flight_inventory_warning_for_scraped_offer_calls_out_non_official_status() -> None:
    """When an offer carries `scraped_provenance`, the warning must
    explicitly say this is scraped_public_page/experimental/fragile data,
    not official-provider data, and must never claim official schedule/
    price/availability/baggage/booking-link verification for it."""
    planning_state = _planning_state()
    segment = FlightSegment(
        origin_airport="TST",
        destination_airport="DMO",
        data_status=DataStatus.SCRAPED_PUBLIC_PAGE,
    )
    offer = FlightOffer(
        offer_id="TEST_ONLY_FLIGHT_OFFER_ALPHA",
        provider="scraped:example_test_only_flight_search_page",
        data_status=DataStatus.SCRAPED_PUBLIC_PAGE,
        outbound_segments=[segment],
        scraped_provenance=ScrapedDataProvenance(
            source_id="example_test_only_flight_search_page",
            source_name="Example Test-Only Flight Search Page",
            confidence=ScrapedDataConfidence.EXPERIMENTAL,
        ),
    )
    planning_state.flight_inventory_report = FlightSearchResult(
        provider="scraped:example_test_only_flight_search_page",
        status=FlightSearchStatus.SUCCESS,
        offers=[offer],
        destination="Lisbon, Portugal",
        departure_date="2026-08-10",
    )

    PlanValidatorService().run(planning_state)

    warnings = _flight_inventory_warnings(planning_state)
    assert len(warnings) == 1
    message_lower = warnings[0].message.lower()
    assert "scraped_public_page" in message_lower
    assert "not official-provider data" in message_lower
    assert "has not been verified" in message_lower
    assert not any(
        issue.category == "flight_inventory"
        for issue in planning_state.validation_report.critical_issues
    )


def test_flight_inventory_warning_never_blocks_generation() -> None:
    """Missing/unconnected flight inventory never contributes a
    critical_issue on its own, regardless of scheduling outcome."""
    planning_state = _two_candidate_planning_state()
    _run_planner_then_validator(planning_state)

    assert planning_state.flight_inventory_report is None
    warnings = _flight_inventory_warnings(planning_state)
    assert len(warnings) == 1
    assert not any(
        issue.category == "flight_inventory"
        for issue in planning_state.validation_report.critical_issues
    )


def test_flight_offers_never_appear_in_scheduled_daily_experiences() -> None:
    """A scraped flight offer must never be scheduled as a daily itinerary
    experience -- flights are inventory reporting only."""
    planning_state = _two_candidate_planning_state()
    segment = FlightSegment(
        origin_airport="TST",
        destination_airport="DMO",
        data_status=DataStatus.SCRAPED_PUBLIC_PAGE,
    )
    offer = FlightOffer(
        offer_id="TEST_ONLY_FLIGHT_OFFER_ALPHA",
        provider="scraped:example_test_only_flight_search_page",
        data_status=DataStatus.SCRAPED_PUBLIC_PAGE,
        outbound_segments=[segment],
    )
    planning_state.flight_inventory_report = FlightSearchResult(
        provider="scraped:example_test_only_flight_search_page",
        status=FlightSearchStatus.SUCCESS,
        offers=[offer],
        destination="Lisbon, Portugal",
        departure_date="2026-08-10",
    )
    _run_planner_then_validator(planning_state)

    assert "TEST_ONLY_FLIGHT_OFFER_ALPHA" not in _scheduled_names(planning_state)


# ---------------------------------------------------------------------------
# Step 175B: provider-coverage consistency hardening. PlanValidatorService
# cross-checks what it already observed on accommodation/flight/route
# reports against the separately-maintained planning_state.provider_coverage
# -- purely additive, read-only, WARNING-only.
# ---------------------------------------------------------------------------


def _provider_coverage_consistency_warnings(planning_state: PlanningState) -> list[Any]:
    return [
        warning
        for warning in planning_state.validation_report.warnings
        if warning.category == "provider_coverage_consistency"
    ]


def _accommodation_success_report() -> AccommodationSearchResult:
    offer = AccommodationOffer(
        provider="fake_accommodation_inventory_provider",
        provider_property_id="prop_1",
        property_name="Fake Property",
        data_status=DataStatus.LIVE,
    )
    return AccommodationSearchResult(
        provider="fake_accommodation_inventory_provider",
        status=AccommodationSearchStatus.SUCCESS,
        offers=[offer],
    )


def _flight_success_report() -> FlightSearchResult:
    segment = FlightSegment(
        origin_airport="TST",
        destination_airport="DMO",
        data_status=DataStatus.LIVE,
    )
    offer = FlightOffer(
        offer_id="TEST_ONLY_FLIGHT_OFFER_ALPHA",
        provider="fake_flight_inventory_provider",
        data_status=DataStatus.LIVE,
        outbound_segments=[segment],
    )
    return FlightSearchResult(
        provider="fake_flight_inventory_provider",
        status=FlightSearchStatus.SUCCESS,
        offers=[offer],
        destination="Lisbon, Portugal",
        departure_date="2026-08-10",
    )


def test_missing_provider_coverage_does_not_crash_validation() -> None:
    """Defensive: `PlanningState` always constructs a `ProviderCoverage`,
    but this check must never crash even if that field is ever missing on
    an older/malformed persisted record."""
    planning_state = _planning_state()
    planning_state.provider_coverage = None  # type: ignore[assignment]

    PlanValidatorService().run(planning_state)

    assert _provider_coverage_consistency_warnings(planning_state) == []
    assert planning_state.validation_report is not None


def test_consistent_provider_coverage_produces_no_consistency_warning() -> None:
    planning_state = _planning_state()
    planning_state.accommodation_inventory_report = _accommodation_success_report()
    planning_state.provider_coverage.hotel_prices = "success"

    PlanValidatorService().run(planning_state)

    assert _provider_coverage_consistency_warnings(planning_state) == []


def test_accommodation_provider_coverage_contradiction_produces_warning_not_critical() -> None:
    planning_state = _planning_state()
    planning_state.accommodation_inventory_report = _accommodation_success_report()
    planning_state.provider_coverage.hotel_prices = "not_connected"

    PlanValidatorService().run(planning_state)

    warnings = _provider_coverage_consistency_warnings(planning_state)
    assert len(warnings) == 1
    assert warnings[0].severity == ValidationSeverity.WARNING
    assert "hotel_prices" in warnings[0].message
    assert not any(
        issue.category == "provider_coverage_consistency"
        for issue in planning_state.validation_report.critical_issues
    )


def test_flight_provider_coverage_contradiction_produces_warning_not_critical() -> None:
    planning_state = _planning_state()
    planning_state.flight_inventory_report = _flight_success_report()
    planning_state.provider_coverage.flights = "failed"

    PlanValidatorService().run(planning_state)

    warnings = _provider_coverage_consistency_warnings(planning_state)
    assert len(warnings) == 1
    assert warnings[0].severity == ValidationSeverity.WARNING
    assert "flights" in warnings[0].message
    assert not any(
        issue.category == "provider_coverage_consistency"
        for issue in planning_state.validation_report.critical_issues
    )


def test_route_provider_coverage_contradiction_produces_warning_not_critical() -> None:
    planning_state = _two_candidate_planning_state()
    ExperiencePlannerService().run(planning_state)
    scheduled = planning_state.experience_plan.daily_plans[0].experiences

    planning_state.route_feasibility_report = RouteFeasibilityReport(
        status=ProviderStatus.SUCCESS,
        provider="osrm",
        route_data_source="osrm",
        legs=[
            RouteLegFeasibility(
                from_experience_id=scheduled[0].experience_id,
                from_experience_name=scheduled[0].name,
                to_experience_id=scheduled[1].experience_id,
                to_experience_name=scheduled[1].name,
                provider="osrm",
                status=ProviderStatus.SUCCESS,
                distance_meters=1200.0,
                duration_seconds=600.0,
                feasibility_status=RouteFeasibilityStatus.FEASIBLE,
                message="A provider-backed route was found for this leg.",
            )
        ],
    )
    planning_state.provider_coverage.routes = "unavailable"

    PlanValidatorService().run(planning_state)

    warnings = _provider_coverage_consistency_warnings(planning_state)
    assert len(warnings) == 1
    assert warnings[0].severity == ValidationSeverity.WARNING
    assert "routes" in warnings[0].message
    assert not any(
        issue.category == "provider_coverage_consistency"
        for issue in planning_state.validation_report.critical_issues
    )
    assert planning_state.validation_report.readiness_status.value == "needs_review"


def test_provider_coverage_consistency_warning_never_flips_readiness_status() -> None:
    """A contradiction warning is additive only -- it must never turn an
    otherwise-`blocked` plan into `needs_review`, and (per the other
    tests in this block) never turns an otherwise-`needs_review` plan
    into `blocked` either."""
    planning_state = _planning_state()
    assert planning_state.destination_context.candidate_pois == []
    planning_state.accommodation_inventory_report = _accommodation_success_report()
    planning_state.provider_coverage.hotel_prices = "failed"

    PlanValidatorService().run(planning_state)

    assert _provider_coverage_consistency_warnings(planning_state)
    assert planning_state.validation_report.readiness_status.value == "blocked"
    assert not any(
        issue.category == "provider_coverage_consistency"
        for issue in planning_state.validation_report.critical_issues
    )


def test_provider_coverage_consistency_check_never_fabricates_data() -> None:
    """The consistency warning message only ever restates already-known
    provider/status strings -- no forbidden factual field (price, rating,
    etc.) is ever introduced."""
    planning_state = _planning_state()
    planning_state.accommodation_inventory_report = _accommodation_success_report()
    planning_state.provider_coverage.hotel_prices = "not_connected"

    PlanValidatorService().run(planning_state)

    report_dump = planning_state.validation_report.model_dump(mode="json")
    _assert_no_forbidden_fields(report_dump)


# ---------------------------------------------------------------------------
# Step 175C: route-aware sequencing, movement-data, and route-geometry
# validation hardening. Every check here is additive and read-only over
# planning_state.route_aware_sequencing_report/travel_time_buffer_report/
# route_feasibility_report -- WARNING/SUGGESTION severity only, never a
# critical issue, and never a claim that a resulting order is optimal,
# safest, or verified.
# ---------------------------------------------------------------------------

_OVERCLAIM_WORDS = ("optimal", "safest", "verified", "guaranteed")


def _route_aware_sequencing_issues(planning_state: PlanningState) -> list[Any]:
    return [
        warning
        for warning in planning_state.validation_report.warnings
        if warning.category == "route_aware_sequencing"
    ]


def _movement_data_issues(planning_state: PlanningState) -> list[Any]:
    return [
        warning
        for warning in planning_state.validation_report.warnings
        if warning.category == "movement_data"
    ]


def _route_geometry_issues(planning_state: PlanningState) -> list[Any]:
    return [
        warning
        for warning in planning_state.validation_report.warnings
        if warning.category == "route_geometry"
    ]


def _leg_with_geometry(scheduled: list[Any], *, has_geometry: bool) -> RouteLegFeasibility:
    geometry = (
        [RoutePathPoint(lat=38.70, lon=-9.13), RoutePathPoint(lat=38.71, lon=-9.14)]
        if has_geometry
        else None
    )
    return RouteLegFeasibility(
        from_experience_id=scheduled[0].experience_id,
        from_experience_name=scheduled[0].name,
        to_experience_id=scheduled[1].experience_id,
        to_experience_name=scheduled[1].name,
        provider="osrm",
        status=ProviderStatus.SUCCESS,
        distance_meters=1200.0,
        duration_seconds=600.0,
        feasibility_status=RouteFeasibilityStatus.FEASIBLE,
        route_geometry=geometry,
    )


def test_missing_route_aware_sequencing_report_does_not_crash() -> None:
    planning_state = _two_candidate_planning_state()
    ExperiencePlannerService().run(planning_state)
    assert planning_state.route_aware_sequencing_report is None

    PlanValidatorService().run(planning_state)

    issues = _route_aware_sequencing_issues(planning_state)
    assert len(issues) == 1
    assert issues[0].severity == ValidationSeverity.SUGGESTION
    assert not any(
        issue.category == "route_aware_sequencing"
        for issue in planning_state.validation_report.critical_issues
    )
    assert planning_state.validation_report.readiness_status.value == "needs_review"


def test_route_aware_sequencing_not_connected_produces_honest_issues() -> None:
    planning_state = _two_candidate_planning_state()
    ExperiencePlannerService().run(planning_state)
    scheduled = planning_state.experience_plan.daily_plans[0].experiences

    planning_state.route_aware_sequencing_report = RouteAwareSequencingReport(
        status=ProviderStatus.NOT_CONNECTED,
        suggestions=[
            RouteAwareSequenceSuggestion(
                day_index=1,
                status=ProviderStatus.NOT_CONNECTED,
                original_order=[scheduled[0].experience_id, scheduled[1].experience_id],
                suggested_order=[scheduled[0].experience_id, scheduled[1].experience_id],
                provider="not_connected",
                message="No routing provider is connected.",
            )
        ],
    )

    PlanValidatorService().run(planning_state)

    sequencing_issues = _route_aware_sequencing_issues(planning_state)
    sequencing_movement_issues = [
        issue
        for issue in _movement_data_issues(planning_state)
        if "travel-time buffer" not in issue.message.lower()
    ]
    assert len(sequencing_issues) == 1
    assert "not applied" in sequencing_issues[0].message.lower()
    assert len(sequencing_movement_issues) == 1
    assert sequencing_movement_issues[0].severity == ValidationSeverity.SUGGESTION
    assert not any(
        issue.category in {"route_aware_sequencing", "movement_data"}
        for issue in planning_state.validation_report.critical_issues
    )


def test_applied_route_aware_sequencing_never_overclaims() -> None:
    planning_state = _two_candidate_planning_state()
    ExperiencePlannerService().run(planning_state)
    scheduled = planning_state.experience_plan.daily_plans[0].experiences

    suggestion = RouteAwareSequenceSuggestion(
        day_index=1,
        status=ProviderStatus.SUCCESS,
        original_order=[scheduled[0].experience_id, scheduled[1].experience_id],
        suggested_order=[scheduled[1].experience_id, scheduled[0].experience_id],
        route_duration_seconds=300.0,
        route_distance_meters=500.0,
        improvement_seconds=120.0,
        provider="osrm",
        message="A provider-backed nearest-next sequencing suggestion was applied.",
        applied=True,
    )
    planning_state.route_aware_sequencing_report = RouteAwareSequencingReport(
        status=ProviderStatus.SUCCESS,
        suggestions=[suggestion],
        is_shadow_only=False,
        applied_to_itinerary=True,
    )

    PlanValidatorService().run(planning_state)

    sequencing_issues = _route_aware_sequencing_issues(planning_state)
    assert len(sequencing_issues) == 1
    message_lower = sequencing_issues[0].message.lower()
    assert "applied" in message_lower
    for banned_word in _OVERCLAIM_WORDS:
        assert banned_word not in message_lower
    assert not any(
        issue.category == "route_aware_sequencing"
        for issue in planning_state.validation_report.critical_issues
    )


def test_default_order_without_movement_data_is_surfaced_honestly() -> None:
    planning_state = _two_candidate_planning_state()
    ExperiencePlannerService().run(planning_state)
    scheduled = planning_state.experience_plan.daily_plans[0].experiences

    planning_state.route_aware_sequencing_report = RouteAwareSequencingReport(
        status=ProviderStatus.UNAVAILABLE,
        suggestions=[
            RouteAwareSequenceSuggestion(
                day_index=1,
                status=ProviderStatus.UNAVAILABLE,
                original_order=[scheduled[0].experience_id, scheduled[1].experience_id],
                suggested_order=[scheduled[0].experience_id, scheduled[1].experience_id],
                provider="osrm",
                message="Fewer than two scheduled experiences in this day have coordinates.",
            )
        ],
    )

    PlanValidatorService().run(planning_state)

    sequencing_issues = _route_aware_sequencing_issues(planning_state)
    sequencing_movement_issues = [
        issue
        for issue in _movement_data_issues(planning_state)
        if "travel-time buffer" not in issue.message.lower()
    ]
    assert "suggested order" in sequencing_issues[0].message.lower()
    assert len(sequencing_movement_issues) == 1
    assert sequencing_movement_issues[0].severity == ValidationSeverity.SUGGESTION
    assert planning_state.validation_report.readiness_status.value == "needs_review"


def test_missing_travel_time_buffer_report_adds_movement_suggestion_not_crash() -> None:
    planning_state = _two_candidate_planning_state()
    ExperiencePlannerService().run(planning_state)
    assert planning_state.travel_time_buffer_report is None

    PlanValidatorService().run(planning_state)

    movement_issues = [
        issue
        for issue in _movement_data_issues(planning_state)
        if "travel-time buffer" in issue.message.lower()
    ]
    assert len(movement_issues) == 1
    assert movement_issues[0].severity == ValidationSeverity.SUGGESTION
    assert not any(
        issue.category == "movement_data"
        for issue in planning_state.validation_report.critical_issues
    )


def test_travel_time_buffer_not_connected_is_suggestion_not_critical() -> None:
    planning_state = _two_candidate_planning_state()
    ExperiencePlannerService().run(planning_state)
    scheduled = planning_state.experience_plan.daily_plans[0].experiences

    planning_state.travel_time_buffer_report = TravelTimeBufferReport(
        status=ProviderStatus.NOT_CONNECTED,
        buffers=[
            TravelTimeBuffer(
                from_experience_id=scheduled[0].experience_id,
                from_experience_name=scheduled[0].name,
                to_experience_id=scheduled[1].experience_id,
                to_experience_name=scheduled[1].name,
                provider="not_connected",
                status=TravelTimeBufferStatus.NOT_CONNECTED,
                buffer_status=BufferSufficiencyStatus.UNAVAILABLE,
                message="No routing provider is connected.",
            )
        ],
    )

    PlanValidatorService().run(planning_state)

    movement_issues = [
        issue
        for issue in _movement_data_issues(planning_state)
        if "travel-time buffer" in issue.message.lower()
    ]
    assert len(movement_issues) == 1
    assert movement_issues[0].severity == ValidationSeverity.SUGGESTION
    assert not any(
        issue.category == "movement_data"
        for issue in planning_state.validation_report.critical_issues
    )


def test_travel_time_buffer_partial_movement_is_warning_severity() -> None:
    planning_state = _two_candidate_planning_state()
    ExperiencePlannerService().run(planning_state)
    scheduled = planning_state.experience_plan.daily_plans[0].experiences

    planning_state.travel_time_buffer_report = TravelTimeBufferReport(
        status=ProviderStatus.PARTIAL,
        buffers=[
            TravelTimeBuffer(
                from_experience_id=scheduled[0].experience_id,
                from_experience_name=scheduled[0].name,
                to_experience_id=scheduled[1].experience_id,
                to_experience_name=scheduled[1].name,
                provider="osrm",
                status=TravelTimeBufferStatus.SUCCESS,
                route_duration_seconds=300.0,
                route_distance_meters=500.0,
                recommended_buffer_seconds=300.0,
                available_gap_seconds=1200.0,
                buffer_status=BufferSufficiencyStatus.SUFFICIENT,
                message="A provider-backed travel duration fits within the scheduled gap.",
            )
        ],
    )

    PlanValidatorService().run(planning_state)

    movement_issues = [
        issue
        for issue in _movement_data_issues(planning_state)
        if "travel-time buffer" in issue.message.lower()
    ]
    assert len(movement_issues) == 1
    assert movement_issues[0].severity == ValidationSeverity.WARNING
    assert not any(
        issue.category == "movement_data"
        for issue in planning_state.validation_report.critical_issues
    )


def test_insufficient_buffer_warning_unchanged_and_no_extra_summary_on_success() -> None:
    """Existing per-leg insufficient-buffer behavior (category
    "travel_time_buffer") is untouched by Step 175C, and a `status=success`
    report gets no additional movement_data summary -- nothing new to add
    beyond the existing insufficient-leg warning."""
    planning_state = _two_candidate_planning_state()
    ExperiencePlannerService().run(planning_state)
    scheduled = planning_state.experience_plan.daily_plans[0].experiences

    planning_state.travel_time_buffer_report = TravelTimeBufferReport(
        status=ProviderStatus.SUCCESS,
        buffers=[
            TravelTimeBuffer(
                from_experience_id=scheduled[0].experience_id,
                from_experience_name=scheduled[0].name,
                to_experience_id=scheduled[1].experience_id,
                to_experience_name=scheduled[1].name,
                provider="osrm",
                status=TravelTimeBufferStatus.SUCCESS,
                route_duration_seconds=900.0,
                route_distance_meters=1500.0,
                recommended_buffer_seconds=900.0,
                available_gap_seconds=300.0,
                buffer_status=BufferSufficiencyStatus.INSUFFICIENT,
                message="A provider-backed travel duration exceeds the scheduled gap.",
            )
        ],
    )

    PlanValidatorService().run(planning_state)

    insufficient_warnings = [
        warning
        for warning in planning_state.validation_report.warnings
        if warning.category == "travel_time_buffer"
    ]
    assert len(insufficient_warnings) == 1
    assert not any(
        "travel-time buffer" in issue.message.lower()
        for issue in _movement_data_issues(planning_state)
    )


def test_route_geometry_present_is_recognized_without_fake_path_details() -> None:
    planning_state = _two_candidate_planning_state()
    ExperiencePlannerService().run(planning_state)
    scheduled = planning_state.experience_plan.daily_plans[0].experiences

    planning_state.route_feasibility_report = RouteFeasibilityReport(
        status=ProviderStatus.SUCCESS,
        provider="osrm",
        route_data_source="osrm",
        legs=[_leg_with_geometry(scheduled, has_geometry=True)],
    )

    PlanValidatorService().run(planning_state)

    geometry_issues = _route_geometry_issues(planning_state)
    assert len(geometry_issues) == 1
    assert geometry_issues[0].severity == ValidationSeverity.SUGGESTION
    assert "all 1" in geometry_issues[0].message
    assert not any(
        issue.category == "route_geometry"
        for issue in planning_state.validation_report.critical_issues
    )
    # Never invents/echoes real coordinate detail as part of the message.
    assert "38.7" not in geometry_issues[0].message
    assert "-9.1" not in geometry_issues[0].message


def test_route_geometry_missing_for_all_legs_is_surfaced_honestly() -> None:
    planning_state = _two_candidate_planning_state()
    ExperiencePlannerService().run(planning_state)
    scheduled = planning_state.experience_plan.daily_plans[0].experiences

    planning_state.route_feasibility_report = RouteFeasibilityReport(
        status=ProviderStatus.SUCCESS,
        provider="osrm",
        route_data_source="osrm",
        legs=[_leg_with_geometry(scheduled, has_geometry=False)],
    )

    PlanValidatorService().run(planning_state)

    geometry_issues = _route_geometry_issues(planning_state)
    assert len(geometry_issues) == 1
    assert "no provider-backed route geometry" in geometry_issues[0].message.lower()
    assert geometry_issues[0].severity == ValidationSeverity.SUGGESTION
    assert not any(
        issue.category == "route_geometry"
        for issue in planning_state.validation_report.critical_issues
    )


def test_route_geometry_partial_coverage_is_surfaced_honestly() -> None:
    planning_state = _two_candidate_planning_state()
    ExperiencePlannerService().run(planning_state)
    scheduled = planning_state.experience_plan.daily_plans[0].experiences

    buffer_with_geometry = TravelTimeBuffer(
        from_experience_id=scheduled[0].experience_id,
        from_experience_name=scheduled[0].name,
        to_experience_id=scheduled[1].experience_id,
        to_experience_name=scheduled[1].name,
        provider="osrm",
        status=TravelTimeBufferStatus.SUCCESS,
        route_duration_seconds=300.0,
        route_distance_meters=500.0,
        recommended_buffer_seconds=300.0,
        buffer_status=BufferSufficiencyStatus.NOT_COMPUTABLE,
        route_geometry=[RoutePathPoint(lat=38.70, lon=-9.13), RoutePathPoint(lat=38.71, lon=-9.14)],
    )
    buffer_without_geometry = buffer_with_geometry.model_copy(
        update={"route_geometry": None, "to_experience_id": "another_experience_id"}
    )
    planning_state.travel_time_buffer_report = TravelTimeBufferReport(
        status=ProviderStatus.SUCCESS,
        buffers=[buffer_with_geometry, buffer_without_geometry],
    )

    PlanValidatorService().run(planning_state)

    geometry_issues = _route_geometry_issues(planning_state)
    assert len(geometry_issues) == 1
    assert "1 of 2" in geometry_issues[0].message
    assert geometry_issues[0].severity == ValidationSeverity.SUGGESTION


def test_route_geometry_validation_never_computes_distance_or_duration() -> None:
    """The route-geometry check only counts leg-level route_geometry
    presence -- it must never introduce a new distance/duration figure of
    its own; the real provider-backed figures already on
    RouteLegFeasibility stay untouched, and are never restated here."""
    planning_state = _two_candidate_planning_state()
    ExperiencePlannerService().run(planning_state)
    scheduled = planning_state.experience_plan.daily_plans[0].experiences

    planning_state.route_feasibility_report = RouteFeasibilityReport(
        status=ProviderStatus.SUCCESS,
        provider="osrm",
        route_data_source="osrm",
        legs=[_leg_with_geometry(scheduled, has_geometry=True)],
    )

    PlanValidatorService().run(planning_state)

    geometry_issues = _route_geometry_issues(planning_state)
    assert geometry_issues
    for issue in geometry_issues:
        assert "1200" not in issue.message
        assert "600" not in issue.message


def test_route_geometry_no_legs_produces_no_issue() -> None:
    """When neither report has any legs to inspect, no route_geometry
    issue is added at all -- never a claim about geometry that was never
    computable in the first place."""
    planning_state = _two_candidate_planning_state()
    ExperiencePlannerService().run(planning_state)
    assert planning_state.route_feasibility_report is None
    assert planning_state.travel_time_buffer_report is None

    PlanValidatorService().run(planning_state)

    assert _route_geometry_issues(planning_state) == []


def test_step_175c_checks_never_flip_readiness_to_blocked() -> None:
    planning_state = _two_candidate_planning_state()
    ExperiencePlannerService().run(planning_state)
    scheduled = planning_state.experience_plan.daily_plans[0].experiences

    planning_state.route_aware_sequencing_report = RouteAwareSequencingReport(
        status=ProviderStatus.FAILED,
        suggestions=[
            RouteAwareSequenceSuggestion(
                day_index=1,
                status=ProviderStatus.FAILED,
                original_order=[scheduled[0].experience_id, scheduled[1].experience_id],
                suggested_order=[scheduled[0].experience_id, scheduled[1].experience_id],
                provider="osrm",
                message="The routing provider request(s) failed.",
            )
        ],
    )

    PlanValidatorService().run(planning_state)

    assert planning_state.validation_report.readiness_status.value == "needs_review"
    assert planning_state.validation_report.critical_issues == []
    assert _route_aware_sequencing_issues(planning_state)
    assert _movement_data_issues(planning_state)


# ---------------------------------------------------------------------------
# Step 175D: regeneration lifecycle validation. Every check here is
# additive and read-only over pending_feedback_summary/
# regeneration_readiness/user_locks/regeneration_attempts/
# plan_diff_preview/version_history/metadata -- WARNING/SUGGESTION
# severity only, never a critical issue, and never a claim that feedback
# was fully satisfied, a locked item will be preserved, or regeneration
# will improve the trip.
# ---------------------------------------------------------------------------

_REGENERATION_OVERCLAIM_WORDS = ("satisfied", "improve", "improved", "preserved", "preserve")


def _regeneration_issues(planning_state: PlanningState) -> list[Any]:
    return [
        warning
        for warning in planning_state.validation_report.warnings
        if warning.category == "regeneration"
    ]


def _regeneration_consistency_issues(planning_state: PlanningState) -> list[Any]:
    return [
        warning
        for warning in planning_state.validation_report.warnings
        if warning.category == "regeneration_state_consistency"
    ]


def _pending_feedback_event() -> FeedbackEvent:
    return FeedbackEvent(
        feedback_text="Make this less packed",
        feedback_type="pace_change",
        affected_stages=[PlanningStage.EXPERIENCE_PLAN],
        regeneration_strategy=RegenerationStrategy.EXPLANATION_ONLY,
    )


def test_pending_feedback_with_can_regenerate_true_produces_ready_suggestion() -> None:
    planning_state = _planning_state()
    planning_state.feedback_history.append(_pending_feedback_event())
    planning_state.pending_feedback_summary = PendingFeedbackSummary(
        status="captured_not_applied", total_feedback_items=1
    )
    planning_state.regeneration_readiness = RegenerationReadiness(
        status="ready",
        can_regenerate=True,
        current_version="v1",
        would_create_version="v2",
        pending_feedback_count=1,
    )
    planning_state.plan_diff_preview = PlanDiffPreview(
        preview_status="regeneration_available", regeneration_available=True
    )

    PlanValidatorService().run(planning_state)

    issues = _regeneration_issues(planning_state)
    assert len(issues) == 1
    assert issues[0].severity == ValidationSeverity.SUGGESTION
    assert "available for deterministic regeneration" in issues[0].message.lower()
    assert not any(
        issue.category == "regeneration"
        for issue in planning_state.validation_report.critical_issues
    )


def test_pending_feedback_with_active_lock_produces_lock_blocked_warning() -> None:
    planning_state = _planning_state()
    planning_state.feedback_history.append(_pending_feedback_event())
    planning_state.pending_feedback_summary = PendingFeedbackSummary(
        status="captured_not_applied", total_feedback_items=1
    )
    planning_state.user_locks.append(
        UserLock(locked_item_type="experience", locked_item_id="experience_test_1")
    )
    planning_state.regeneration_readiness = RegenerationReadiness(
        status="blocked",
        can_regenerate=False,
        active_lock_count=1,
        blocked_by=[
            "Regeneration is blocked because one or more active locks exist. "
            "Remove all active locks before requesting regeneration."
        ],
    )

    PlanValidatorService().run(planning_state)

    issues = _regeneration_issues(planning_state)
    assert len(issues) == 1
    assert issues[0].severity == ValidationSeverity.WARNING
    assert "lock" in issues[0].message.lower()
    assert not any(
        issue.category == "regeneration"
        for issue in planning_state.validation_report.critical_issues
    )


def test_pending_feedback_without_derivable_stage_produces_not_ready_warning() -> None:
    planning_state = _planning_state()
    event = FeedbackEvent(
        feedback_text="Please make it wonderful",
        feedback_type="general_feedback",
        affected_stages=[],
        regeneration_strategy=RegenerationStrategy.EXPLANATION_ONLY,
    )
    planning_state.feedback_history.append(event)
    planning_state.pending_feedback_summary = PendingFeedbackSummary(
        status="captured_not_applied", total_feedback_items=1
    )
    planning_state.regeneration_readiness = RegenerationReadiness(
        status="blocked",
        can_regenerate=False,
        blocked_by=[
            "Pending feedback exists, but it did not classify to any plan "
            "section a regeneration could rerun."
        ],
    )

    PlanValidatorService().run(planning_state)

    issues = _regeneration_issues(planning_state)
    assert len(issues) == 1
    assert issues[0].severity == ValidationSeverity.WARNING
    assert "did not classify" in issues[0].message.lower()


def test_applied_feedback_only_does_not_imply_regeneration_available() -> None:
    planning_state = _planning_state()
    applied_event = _pending_feedback_event()
    applied_event.applied_at = datetime.now(timezone.utc)
    applied_event.applied_in_version = "v2"
    applied_event.handling_status = "applied"
    planning_state.feedback_history.append(applied_event)
    planning_state.pending_feedback_summary = PendingFeedbackSummary(
        status="none",
        note="All captured feedback has already been applied by a regeneration.",
    )
    planning_state.regeneration_readiness = RegenerationReadiness(
        status="blocked", can_regenerate=False
    )

    PlanValidatorService().run(planning_state)

    issues = _regeneration_issues(planning_state)
    assert len(issues) == 1
    assert issues[0].severity == ValidationSeverity.SUGGESTION
    assert "already been marked applied" in issues[0].message.lower()
    for banned_word in _REGENERATION_OVERCLAIM_WORDS:
        assert banned_word not in issues[0].message.lower()


def test_no_feedback_at_all_produces_no_regeneration_issue() -> None:
    """The common default state (no feedback ever captured) needs no
    review -- adding a warning here would just be noise."""
    planning_state = _planning_state()

    PlanValidatorService().run(planning_state)

    assert _regeneration_issues(planning_state) == []


def test_failed_latest_regeneration_attempt_is_surfaced_honestly() -> None:
    planning_state = _planning_state()
    planning_state.regeneration_attempts.append(
        RegenerationAttempt(
            status="failed",
            reason_code="REGENERATION_NOT_AVAILABLE",
            message="The regeneration rerun failed unexpectedly.",
        )
    )

    PlanValidatorService().run(planning_state)

    issues = _regeneration_issues(planning_state)
    assert len(issues) == 1
    assert issues[0].severity == ValidationSeverity.WARNING
    assert "failed" in issues[0].message.lower()
    assert not any(
        issue.category == "regeneration"
        for issue in planning_state.validation_report.critical_issues
    )


def test_failed_attempt_and_pending_feedback_both_surfaced() -> None:
    """A failed attempt is independent of the pending-feedback state --
    both issues can (and should) appear together without one
    suppressing the other."""
    planning_state = _planning_state()
    planning_state.feedback_history.append(_pending_feedback_event())
    planning_state.pending_feedback_summary = PendingFeedbackSummary(
        status="captured_not_applied", total_feedback_items=1
    )
    planning_state.regeneration_readiness = RegenerationReadiness(
        status="ready", can_regenerate=True
    )
    planning_state.regeneration_attempts.append(
        RegenerationAttempt(status="failed", message="The regeneration rerun failed.")
    )

    PlanValidatorService().run(planning_state)

    issues = _regeneration_issues(planning_state)
    assert len(issues) == 2
    assert any("ready" not in issue.message.lower() and "failed" in issue.message.lower() for issue in issues)
    assert any("available for deterministic regeneration" in issue.message.lower() for issue in issues)


def test_readiness_and_diff_preview_mismatch_produces_consistency_warning() -> None:
    planning_state = _planning_state()
    planning_state.regeneration_readiness = RegenerationReadiness(
        status="ready", can_regenerate=True
    )
    planning_state.plan_diff_preview = PlanDiffPreview(
        preview_status="not_available", regeneration_available=False
    )

    PlanValidatorService().run(planning_state)

    issues = _regeneration_consistency_issues(planning_state)
    assert len(issues) == 1
    assert issues[0].severity == ValidationSeverity.WARNING
    assert "disagree" in issues[0].message.lower()
    assert not any(
        issue.category == "regeneration_state_consistency"
        for issue in planning_state.validation_report.critical_issues
    )


def test_matching_readiness_and_diff_preview_produces_no_consistency_warning() -> None:
    planning_state = _planning_state()
    planning_state.regeneration_readiness = RegenerationReadiness(
        status="ready", can_regenerate=True
    )
    planning_state.plan_diff_preview = PlanDiffPreview(
        preview_status="regeneration_available", regeneration_available=True
    )

    PlanValidatorService().run(planning_state)

    assert _regeneration_consistency_issues(planning_state) == []


def test_version_history_current_version_mismatch_produces_consistency_warning() -> None:
    planning_state = _planning_state()
    planning_state.version_history.append(
        VersionHistoryItem(version_label="v1", created_by="system")
    )
    planning_state.metadata.current_version = "v99"

    PlanValidatorService().run(planning_state)

    issues = _regeneration_consistency_issues(planning_state)
    assert any("current_version" in issue.message for issue in issues)
    assert all(issue.severity == ValidationSeverity.WARNING for issue in issues)
    assert not any(
        issue.category == "regeneration_state_consistency"
        for issue in planning_state.validation_report.critical_issues
    )


def test_matching_version_history_and_current_version_produces_no_warning() -> None:
    planning_state = _planning_state()
    planning_state.version_history.append(
        VersionHistoryItem(version_label="v1", created_by="system")
    )
    planning_state.metadata.current_version = "v1"

    PlanValidatorService().run(planning_state)

    assert _regeneration_consistency_issues(planning_state) == []


def test_empty_version_history_never_triggers_version_mismatch_warning() -> None:
    """An empty version_history with the default current_version="v1" is
    normal, unpopulated state -- never a mismatch to flag."""
    planning_state = _planning_state()
    assert planning_state.version_history == []
    assert planning_state.metadata.current_version == "v1"

    PlanValidatorService().run(planning_state)

    assert _regeneration_consistency_issues(planning_state) == []


def test_regeneration_validation_never_creates_critical_issues() -> None:
    planning_state = _two_candidate_planning_state()
    ExperiencePlannerService().run(planning_state)
    planning_state.feedback_history.append(_pending_feedback_event())
    planning_state.pending_feedback_summary = PendingFeedbackSummary(
        status="captured_not_applied", total_feedback_items=1
    )
    planning_state.user_locks.append(
        UserLock(locked_item_type="experience", locked_item_id="experience_test_1")
    )
    planning_state.regeneration_attempts.append(RegenerationAttempt(status="failed"))
    planning_state.regeneration_readiness = RegenerationReadiness(
        status="ready", can_regenerate=True
    )
    planning_state.plan_diff_preview = PlanDiffPreview(
        preview_status="not_available", regeneration_available=False
    )
    planning_state.version_history.append(
        VersionHistoryItem(version_label="v1", created_by="system")
    )
    planning_state.metadata.current_version = "v2"

    PlanValidatorService().run(planning_state)

    assert planning_state.validation_report.critical_issues == []
    assert planning_state.validation_report.readiness_status.value == "needs_review"
    regeneration_related = [
        issue
        for issue in planning_state.validation_report.warnings
        if issue.category in {"regeneration", "regeneration_state_consistency"}
    ]
    # lock-blocked + failed-attempt + availability-mismatch + version-mismatch
    assert len(regeneration_related) >= 4


def test_regeneration_validation_never_overclaims() -> None:
    planning_state = _planning_state()
    planning_state.feedback_history.append(_pending_feedback_event())
    planning_state.pending_feedback_summary = PendingFeedbackSummary(
        status="captured_not_applied", total_feedback_items=1
    )
    planning_state.user_locks.append(
        UserLock(locked_item_type="experience", locked_item_id="experience_test_1")
    )
    planning_state.regeneration_readiness = RegenerationReadiness(
        status="ready", can_regenerate=True
    )
    planning_state.plan_diff_preview = PlanDiffPreview(
        preview_status="not_available", regeneration_available=False
    )
    planning_state.regeneration_attempts.append(
        RegenerationAttempt(status="failed", message="The regeneration rerun failed.")
    )
    planning_state.version_history.append(
        VersionHistoryItem(version_label="v1", created_by="system")
    )
    planning_state.metadata.current_version = "v2"

    PlanValidatorService().run(planning_state)

    all_regeneration_messages = " ".join(
        issue.message.lower()
        for issue in planning_state.validation_report.warnings
        if issue.category in {"regeneration", "regeneration_state_consistency"}
    )
    assert all_regeneration_messages  # sanity: something was actually produced
    for banned_word in _REGENERATION_OVERCLAIM_WORDS:
        assert banned_word not in all_regeneration_messages


def test_flight_inventory_affected_section_is_flight_inventory_not_stay_transport() -> None:
    """Step 175D refinement: flight inventory is distinct from
    stay_transport's local/intercity transport strategy."""
    planning_state = _planning_state()

    PlanValidatorService().run(planning_state)

    flight_warnings = _flight_inventory_warnings(planning_state)
    assert len(flight_warnings) == 1
    assert flight_warnings[0].affected_section == "flight_inventory"
