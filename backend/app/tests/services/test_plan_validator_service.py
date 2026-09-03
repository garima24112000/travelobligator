from __future__ import annotations

from typing import Any

from app.models.accommodation import (
    AccommodationOffer,
    AccommodationSearchResult,
    AccommodationSearchStatus,
)
from app.models.common import DataStatus, ProviderStatus
from app.models.planning_state import (
    DestinationContext,
    PlanningState,
    TravelGroupType,
    TripRequest,
)
from app.models.routing import (
    BufferSufficiencyStatus,
    RouteFeasibilityReport,
    RouteFeasibilityStatus,
    RouteLegFeasibility,
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
