from __future__ import annotations

from app.models.accommodation import AccommodationSearchResult, AccommodationSearchStatus
from app.models.common import DataStatus, ProviderStatus, ReadinessStatus, ValidationSeverity
from app.models.flight import FlightSearchResult, FlightSearchStatus
from app.models.planning_state import (
    DailyPlan,
    PlanningStage,
    PlanningState,
    ValidationIssue,
    ValidationReport,
)
from app.models.routing import (
    BufferSufficiencyStatus,
    RouteFeasibilityReport,
    RouteFeasibilityStatus,
    TravelTimeBufferReport,
)
from app.services.base import PlanningStageService
from app.utils.geo import haversine_distance_km

_GEOGRAPHIC_SPREAD_WARNING_THRESHOLD_KM = 8.0


class PlanValidatorService(PlanningStageService):
    """Owns `validation_report` and `validation_cards`
    (docs/14_backend_architecture.md section 14).

    Runs deterministic checks first and does not modify the itinerary.

    * If the experience plan has no scheduled experiences at all, the report
      is `blocked`. The message is distinguished honestly: either no
      provider-backed attraction candidates were available, or candidates
      exist but this plan has not scheduled them into days yet.
    * If experiences have been scheduled (ExperiencePlannerService's
      conservative day-level scheduling step), the plan is not blocked
      anymore. However, route ordering, timing, opening-hours, and
      feasibility checks are not implemented yet, so the report is
      `needs_review`, never `ready`.
    * If the trip request or traveler profile captured any constraints, a
      warning names them explicitly so the report never implies they were
      checked. This never blocks the plan by itself; constraints only add a
      warning, not a critical issue.
    * If a must-visit request does not match any *scheduled* experience by
      name (case-insensitive containment, Step 156G), a warning names it
      explicitly instead of silently dropping it or inventing/substituting
      an unrelated place to satisfy it. Matching against scheduled
      experiences (not just `destination_context.candidate_pois`) catches
      both a must-visit that was never grounded to a real provider-backed
      candidate at all, and a grounded must-visit candidate that
      `CandidateQualityService`/`ExperiencePlannerService` excluded from
      scheduling for a severe reason (missing coordinates, insufficient
      provider confidence). This never blocks the plan by itself; it only
      adds a warning.
    * If the trip request or traveler profile captured a budget (`budget_min`
      and/or `budget_max`), a warning names the captured range and says
      budget/cost validation is not implemented yet, so the plan never
      claims to fit the budget. If `planning_state.currency_context` also
      has a live provider-backed exchange rate, the same warning notes that
      exchange-rate context is available, but still never claims the
      budget is met -- total trip cost is not calculated. This never
      blocks the plan by itself; it only adds a warning.
    * If a day's coordinate-backed scheduled experiences sum to more than
      `_GEOGRAPHIC_SPREAD_WARNING_THRESHOLD_KM` of straight-line
      (haversine) distance between consecutive experiences, a warning flags
      that day. This is a straight-line-only signal -- never called walking
      or route distance -- and does not change scheduling/order or block
      the plan; it only adds a warning.
    * If `planning_state.weather_context` has usable provider-backed
      `daily_weather`, a `category="weather"` warning says provider-backed
      weather data is available, that the itinerary has not been adjusted
      around weather yet, and that outdoor/long-walk days should be
      reviewed manually. It restates only existing
      `precipitation_probability_max`/`temperature_max_c`/
      `temperature_min_c` threshold breaches already present on
      `daily_weather` (no rain/heat/cold/comfort-risk/weather-description/
      UV/humidity/alert/severe-weather value is ever invented). If weather
      is missing/unavailable, no warning is added. This never blocks the
      plan or marks it ready by itself -- it only adds a warning, and it
      does not modify `daily_plans`.
    * If `planning_state.holiday_context` has a successful provider
      response (`data_status == live`), a `category="holidays"` warning
      says provider-backed public holiday data is available, that the
      itinerary has not been checked against venue closures, opening
      hours, or crowd context yet, and that affected dates should be
      reviewed manually. If real holidays fall inside the trip's date
      range, it restates only their existing dates/names from
      `holiday_context.holidays`; if the provider succeeded but none fall
      in range, it adds a softer version of the same warning instead. It
      never claims a venue is closed or a place is crowded, and never
      invents an event, festival, strike, opening hour, or crowd/closure/
      safety risk. If holidays are missing/unavailable/failed/not
      connected, no warning is added. This never blocks the plan or marks
      it ready by itself -- it only adds a warning, and it does not modify
      `daily_plans`.
    * If `planning_state.travel_time_buffer_report` (Step 166C) has a leg
      whose `buffer_status == insufficient` -- a real, successful,
      provider-backed travel duration between two consecutive scheduled
      experiences that exceeds a real, known schedule gap between them --
      a `category="travel_time_buffer"` `WARNING` names both experiences
      and the real duration/gap figures. A leg whose buffer is
      `sufficient` never gets a warning (nothing to review). A leg whose
      buffer is `not_computable` (no schedule gap known, e.g. no schedule
      timestamps exist yet) or `unavailable` (no real route duration at
      all: routing not connected/unavailable/failed, or missing
      coordinates) also never gets a warning here -- sufficiency is never
      guessed either way, and the existing feasibility warning above
      already covers "route timing is not implemented/checked" honestly.
      This is always a `WARNING`, never a critical issue, so it never
      blocks the plan by itself, and it does not modify `daily_plans`.
    """

    def run(self, planning_state: PlanningState) -> PlanningState:
        planning_state.set_active_stage(PlanningStage.VALIDATION)

        critical_issues: list[ValidationIssue] = []
        warnings: list[ValidationIssue] = []
        provider_coverage_notes: list[str] = []
        unavailable_data_notes = [item.field for item in planning_state.unavailable_data]

        has_scheduled_experiences = bool(
            planning_state.experience_plan
            and any(day.experiences for day in planning_state.experience_plan.daily_plans)
        )
        candidate_pois_count = (
            len(planning_state.destination_context.candidate_pois)
            if planning_state.destination_context
            else 0
        )

        if has_scheduled_experiences:
            warnings.append(
                _build_feasibility_warning(planning_state.route_feasibility_report)
            )
            warnings.extend(
                _build_travel_time_buffer_warnings(planning_state.travel_time_buffer_report)
            )
        elif candidate_pois_count > 0:
            critical_issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.CRITICAL,
                    category="scheduling",
                    message=(
                        f"{candidate_pois_count} provider-backed attraction candidate(s) "
                        "are available, but no experiences have been scheduled for this "
                        "plan yet."
                    ),
                    affected_section="experience_plan",
                    suggested_fix="Run the experience planner stage to schedule candidate attractions.",
                )
            )
            provider_coverage_notes.append(
                "Places are available via OpenStreetMap; this plan has not scheduled "
                "them into days yet."
            )
        else:
            critical_issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.CRITICAL,
                    category="provider_coverage",
                    message=(
                        "No experiences have been scheduled because no provider-backed "
                        "attraction candidates are available."
                    ),
                    affected_section="experience_plan",
                    suggested_fix="Connect a places provider and regenerate the plan.",
                )
            )
            provider_coverage_notes.append(
                "No provider-backed attraction candidates are available for this "
                "destination."
            )

        if planning_state.experience_plan:
            for day in planning_state.experience_plan.daily_plans:
                spread_km = _day_geographic_spread_km(day)
                if (
                    spread_km is not None
                    and spread_km > _GEOGRAPHIC_SPREAD_WARNING_THRESHOLD_KM
                ):
                    warnings.append(
                        ValidationIssue(
                            severity=ValidationSeverity.WARNING,
                            category="geographic_spread",
                            message=(
                                f"Day {day.day_number}'s scheduled experiences are "
                                f"geographically spread out: consecutive coordinate-backed "
                                f"experiences sum to about {spread_km:.1f} km of "
                                "straight-line distance. This is straight-line distance "
                                "only, not walking or route distance, and actual "
                                "walking/transit/route feasibility is not implemented yet, "
                                "so this day needs review."
                            ),
                            affected_section=f"experience_plan.daily_plans[{day.day_number}]",
                            suggested_fix=(
                                "Implement walking/transit route feasibility validation, "
                                "or reconsider which attractions are grouped into this day."
                            ),
                        )
                    )

        captured_constraints: list[str] = []
        for constraint in planning_state.trip_request.constraints:
            if constraint not in captured_constraints:
                captured_constraints.append(constraint)
        if planning_state.traveler_profile:
            for constraint in planning_state.traveler_profile.constraints:
                if constraint not in captured_constraints:
                    captured_constraints.append(constraint)

        if captured_constraints:
            constraint_list = ", ".join(captured_constraints)
            warnings.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    category="constraints",
                    message=(
                        f"The following constraint(s) were captured but are not fully "
                        f"validated yet: {constraint_list}. This plan does not confirm "
                        "that they are satisfied."
                    ),
                    affected_section="traveler_profile",
                    suggested_fix=(
                        "Implement constraint/feasibility validation against these "
                        "constraints before claiming they are satisfied."
                    ),
                )
            )

        must_visit_terms = (
            planning_state.traveler_profile.must_visit
            if planning_state.traveler_profile
            else planning_state.trip_request.must_visit
        )
        # Step 156G: match against what was actually *scheduled*, not just
        # what exists somewhere in destination_context.candidate_pois. A
        # must-visit can be a real, grounded provider-backed candidate and
        # still end up excluded from scheduling for a severe candidate-
        # quality reason (missing coordinates, insufficient provider
        # confidence, Step 156C/156E) -- that case must still surface an
        # honest warning, not be silently treated as "found."
        scheduled_names_lower = (
            [
                experience.name.lower()
                for day_plan in planning_state.experience_plan.daily_plans
                for experience in day_plan.experiences
            ]
            if planning_state.experience_plan
            else []
        )
        unmatched_must_visit = [
            term
            for term in must_visit_terms
            if not any(term.lower() in name for name in scheduled_names_lower)
        ]

        if unmatched_must_visit:
            unmatched_list = ", ".join(unmatched_must_visit)
            warnings.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    category="must_visit",
                    message=(
                        f"The following must-visit place(s) were requested but were not "
                        f"grounded/scheduled for this destination: {unmatched_list}. "
                        f"They were not replaced with unrelated attractions."
                    ),
                    affected_section="experience_plan",
                    suggested_fix=(
                        "Search for these must-visit places directly, or connect a places "
                        "provider with broader coverage."
                    ),
                )
            )

        trip_request = planning_state.trip_request
        budget_min = trip_request.budget_min
        budget_max = trip_request.budget_max
        budget_currency = trip_request.budget_currency

        if (
            budget_min is None
            and budget_max is None
            and planning_state.traveler_profile
        ):
            profile_budget = planning_state.traveler_profile.budget_profile
            budget_min = profile_budget.get("budget_min")
            budget_max = profile_budget.get("budget_max")
            budget_currency = profile_budget.get("currency", budget_currency)

        if budget_min is not None or budget_max is not None:
            if budget_min is not None and budget_max is not None:
                budget_range = (
                    f"{_format_budget_amount(budget_min)}-{_format_budget_amount(budget_max)} "
                    f"{budget_currency}"
                )
            elif budget_min is not None:
                budget_range = f"{_format_budget_amount(budget_min)}+ {budget_currency}"
            else:
                budget_range = f"up to {_format_budget_amount(budget_max)} {budget_currency}"

            budget_message = (
                f"A budget of {budget_range} was captured, but budget/cost "
                "validation is not implemented yet, so this plan does not "
                "confirm it fits within that budget."
            )
            currency_context = planning_state.currency_context
            if currency_context is not None and currency_context.data_status == DataStatus.LIVE:
                budget_message += (
                    " Provider-backed exchange-rate context is available "
                    f"({currency_context.base_currency} -> "
                    f"{currency_context.destination_currency}), but total trip cost is "
                    "still not calculated, so this still does not confirm the budget is met."
                )

            warnings.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    category="budget",
                    message=budget_message,
                    affected_section="experience_plan",
                    suggested_fix=(
                        "Implement cost estimation and budget validation before "
                        "claiming the plan fits the traveler's budget."
                    ),
                )
            )

        weather_warning = _build_weather_warning(planning_state)
        if weather_warning is not None:
            warnings.append(weather_warning)

        holiday_warning = _build_holiday_warning(planning_state)
        if holiday_warning is not None:
            warnings.append(holiday_warning)

        warnings.append(
            _build_accommodation_inventory_warning(planning_state.accommodation_inventory_report)
        )

        warnings.append(
            _build_flight_inventory_warning(planning_state.flight_inventory_report)
        )

        readiness_status = (
            ReadinessStatus.BLOCKED if critical_issues else ReadinessStatus.NEEDS_REVIEW
        )

        validation_report = ValidationReport(
            readiness_status=readiness_status,
            critical_issues=critical_issues,
            warnings=warnings,
            provider_coverage_notes=provider_coverage_notes,
            unavailable_data_notes=unavailable_data_notes,
        )

        planning_state.validation_report = validation_report
        planning_state.touch()
        return planning_state


def _format_budget_amount(value: float) -> str:
    """Format a captured `budget_min`/`budget_max` figure cleanly for
    display -- e.g. 1500.0 -> "1500", 2500.0 -> "2500", 1500.5 -> "1500.5"
    -- never an unnecessary trailing ".0". This only reformats the exact
    captured value; it never rounds to a different amount, estimates a
    cost, or invents a price/fee/tax.
    """
    if value == int(value):
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


_NO_ROUTE_FEASIBILITY_DATA_MESSAGE = (
    "Attractions have been scheduled into days, but route ordering, timing, "
    "opening-hours, and feasibility checks are not implemented yet, so this plan "
    "needs review before it can be considered ready."
)
_NO_ROUTE_FEASIBILITY_DATA_SUGGESTED_FIX = (
    "Implement route/timing feasibility validation before marking plans ready."
)
_ROUTE_AWARE_SCHEDULING_SUGGESTED_FIX = (
    "Route ordering, timing, opening-hours, and full route-aware scheduling "
    "(Section 166) are still not implemented -- implement those before marking "
    "plans ready."
)


def _build_feasibility_warning(route_feasibility_report: RouteFeasibilityReport | None) -> ValidationIssue:
    """Deterministic review warning about scheduled-experience feasibility
    (Step 165E), built purely from `planning_state.route_feasibility_report`
    -- no provider call of its own (route lookups already happened in
    `RouteFeasibilityService`, before validation runs). Always a `WARNING`,
    never a critical issue, and this never marks the plan ready by itself --
    full route-aware scheduling/timing/opening-hours validation is Section
    166's job, not this step's.

    When no report is available, or it has no legs (e.g. every day has at
    most one scheduled experience), this falls back to the original,
    unconditional "not implemented yet" wording -- exactly the message this
    plan showed before Step 165E, never claiming route data exists when it
    doesn't.

    When the report has legs, the message honestly reflects what was
    actually found: legs with a real provider-backed route
    (`feasibility_status=feasible`) are named as such, using
    `route_data_source` (never inventing which provider was used); legs
    that could not be route-checked (routing not connected/unavailable/
    failed, or missing coordinates) are named separately, without claiming
    a route-unavailable state for the feasible legs. Readiness never
    escalates to `ready` from this warning alone, whether or not any leg
    succeeded -- consuming route data here is feasibility *reporting*, not
    proof the whole itinerary is route-validated.
    """
    if route_feasibility_report is None or not route_feasibility_report.legs:
        return ValidationIssue(
            severity=ValidationSeverity.WARNING,
            category="feasibility",
            message=_NO_ROUTE_FEASIBILITY_DATA_MESSAGE,
            affected_section="experience_plan",
            suggested_fix=_NO_ROUTE_FEASIBILITY_DATA_SUGGESTED_FIX,
        )

    legs = route_feasibility_report.legs
    feasible_legs = [leg for leg in legs if leg.feasibility_status == RouteFeasibilityStatus.FEASIBLE]
    other_legs = [leg for leg in legs if leg.feasibility_status != RouteFeasibilityStatus.FEASIBLE]

    if not feasible_legs:
        message = _NO_ROUTE_FEASIBILITY_DATA_MESSAGE
        if any(leg.status == ProviderStatus.NOT_CONNECTED for leg in legs):
            message += " No routing provider is connected."
        return ValidationIssue(
            severity=ValidationSeverity.WARNING,
            category="feasibility",
            message=message,
            affected_section="experience_plan",
            suggested_fix=_NO_ROUTE_FEASIBILITY_DATA_SUGGESTED_FIX,
        )

    message = (
        f"{len(feasible_legs)} of {len(legs)} scheduled leg(s) have a provider-backed "
        f"route (via {route_feasibility_report.route_data_source}) with a real distance "
        "and duration. Route timing, opening-hours, and full route-aware scheduling are "
        "still not implemented, so this plan still needs review before it can be "
        "considered ready."
    )
    if other_legs:
        message += (
            f" {len(other_legs)} leg(s) could not be route-checked (routing "
            "unavailable/not connected, or missing coordinates)."
        )

    return ValidationIssue(
        severity=ValidationSeverity.WARNING,
        category="feasibility",
        message=message,
        affected_section="experience_plan",
        suggested_fix=_ROUTE_AWARE_SCHEDULING_SUGGESTED_FIX,
    )


_HIGH_PRECIPITATION_PROBABILITY_THRESHOLD = 50
_HIGH_TEMPERATURE_C_THRESHOLD = 30
_LOW_TEMPERATURE_C_THRESHOLD = 5


def _build_weather_warning(planning_state: PlanningState) -> ValidationIssue | None:
    """Deterministic review warning built purely from
    `planning_state.weather_context` -- no provider call, no AI/LLM, no
    invented fact, and `daily_plans` are never modified.

    Only added when `weather_context` has usable provider-backed
    `daily_weather`; if weather is missing/unavailable (or
    `weather_context` itself is unset), returns None instead of guessing.
    The message restates only existing
    `precipitation_probability_max`/`temperature_max_c`/`temperature_min_c`
    threshold breaches already present on `daily_weather` -- never a
    rain/heat/cold/comfort-risk/weather-description/UV/humidity/alert/
    severe-weather value that isn't already there. Always a `WARNING`, never
    a critical issue, so it never blocks the plan or marks it ready by
    itself.
    """
    weather_context = planning_state.weather_context
    if weather_context is None or not weather_context.daily_weather:
        return None

    high_precipitation_dates = [
        day.date.isoformat()
        for day in weather_context.daily_weather
        if day.precipitation_probability_max is not None
        and day.precipitation_probability_max >= _HIGH_PRECIPITATION_PROBABILITY_THRESHOLD
    ]
    high_temperature_dates = [
        day.date.isoformat()
        for day in weather_context.daily_weather
        if day.temperature_max_c is not None
        and day.temperature_max_c >= _HIGH_TEMPERATURE_C_THRESHOLD
    ]
    low_temperature_dates = [
        day.date.isoformat()
        for day in weather_context.daily_weather
        if day.temperature_min_c is not None
        and day.temperature_min_c <= _LOW_TEMPERATURE_C_THRESHOLD
    ]

    message_parts = [
        "Provider-backed weather data is available for this trip, but the "
        "itinerary has not been adjusted around weather yet -- review "
        "outdoor/long-walk days manually."
    ]
    if high_precipitation_dates:
        message_parts.append(
            f"High precipitation probability (>={_HIGH_PRECIPITATION_PROBABILITY_THRESHOLD}%) "
            f"forecast on: {', '.join(high_precipitation_dates)}."
        )
    if high_temperature_dates:
        message_parts.append(
            f"High temperature (>={_HIGH_TEMPERATURE_C_THRESHOLD}°C) forecast on: "
            f"{', '.join(high_temperature_dates)}."
        )
    if low_temperature_dates:
        message_parts.append(
            f"Low temperature (<={_LOW_TEMPERATURE_C_THRESHOLD}°C) forecast on: "
            f"{', '.join(low_temperature_dates)}."
        )

    return ValidationIssue(
        severity=ValidationSeverity.WARNING,
        category="weather",
        message=" ".join(message_parts),
        affected_section="experience_plan",
        suggested_fix=(
            "Implement weather-aware itinerary adjustment (e.g. rescheduling or "
            "rerouting outdoor/long-walk activities around high precipitation "
            "probability, high temperature, or low temperature) before treating "
            "this plan as weather-checked."
        ),
    )


def _build_holiday_warning(planning_state: PlanningState) -> ValidationIssue | None:
    """Deterministic review warning built purely from
    `planning_state.holiday_context` -- no provider call, no AI/LLM, no
    invented fact, and `daily_plans` are never modified.

    Only added when `holiday_context` reflects a successful provider
    response (`data_status == DataStatus.LIVE`); if holidays are missing/
    unavailable/failed/not connected (or `holiday_context` itself is
    unset), returns None instead of guessing. If real holidays fall inside
    the trip's date range, the message restates only their existing
    dates/names from `holiday_context.holidays`; if the provider succeeded
    but none fall in range, a softer version of the same warning is
    returned instead. Never claims a venue is closed or a place is
    crowded, and never invents an event, festival, strike, opening hour,
    or crowd/closure/safety risk -- venue closures, opening hours, and
    crowd context are named only as dimensions that have not been checked
    yet. Always a `WARNING`, never a critical issue, so it never blocks the
    plan or marks it ready by itself.
    """
    holiday_context = planning_state.holiday_context
    if holiday_context is None or holiday_context.data_status != DataStatus.LIVE:
        return None

    if holiday_context.holidays:
        holiday_list = ", ".join(
            f"{holiday.date.isoformat()} ({holiday.name})" for holiday in holiday_context.holidays
        )
        message = (
            "Provider-backed public holiday data is available for this trip, but the "
            "itinerary has not been checked against venue closures, opening hours, or "
            "crowd context yet -- review these dates manually: "
            f"{holiday_list}."
        )
    else:
        message = (
            "Provider-backed public holiday data was checked for this trip's date "
            "range, but no public holidays fall within it. The itinerary still has "
            "not been checked against venue closures, opening hours, or crowd "
            "context for any date, since that interpretation is not implemented yet."
        )

    return ValidationIssue(
        severity=ValidationSeverity.WARNING,
        category="holidays",
        message=message,
        affected_section="experience_plan",
        suggested_fix=(
            "Implement holiday-aware itinerary checks (e.g. venue closures, opening "
            "hours, or crowd context around public holidays) before treating this "
            "plan as holiday-checked."
        ),
    )


_ACCOMMODATION_NOT_CONNECTED_MESSAGE = (
    "No accommodation inventory provider is connected, so bookable lodging price, "
    "availability, rating, and booking link data could not be checked. This is "
    "separate from any OpenStreetMap accommodation-location candidates suggested "
    "elsewhere in this plan, which are not bookable inventory."
)
_ACCOMMODATION_FAILED_MESSAGE = (
    "The accommodation inventory provider request failed, so bookable lodging "
    "price, availability, rating, and booking link data could not be checked."
)
_ACCOMMODATION_UNAVAILABLE_MESSAGE_TEMPLATE = (
    "The accommodation inventory provider was checked, but no bookable lodging "
    "offers were available ({detail}). No price, availability, rating, or booking "
    "link data exists to review."
)
_ACCOMMODATION_SUCCESS_MESSAGE_TEMPLATE = (
    "{count} provider-backed bookable accommodation offer(s) were found via "
    "{provider}, but these have not been reviewed for price, availability, rating, "
    "or booking-link accuracy, and they are not scheduled into the itinerary."
)
_ACCOMMODATION_SCRAPED_SUCCESS_MESSAGE_TEMPLATE = (
    "{count} accommodation offer(s) were found via {provider}, but this data was "
    "scraped from a public page (scraped_public_page/experimental/fragile) -- it "
    "is not official-provider data and has not been verified for price, "
    "availability, rating, or booking-link accuracy. It is not scheduled into the "
    "itinerary and needs manual review before being trusted."
)
_ACCOMMODATION_SUGGESTED_FIX = (
    "Connect a real accommodation inventory provider, or manually review bookable "
    "lodging options for these dates, before treating lodging as checked."
)


def _build_accommodation_inventory_warning(
    accommodation_inventory_report: AccommodationSearchResult | None,
) -> ValidationIssue:
    """Deterministic review warning built purely from
    `planning_state.accommodation_inventory_report` (Step 167D) -- no
    provider call of its own (the lookup already happened in
    `AccommodationInventoryService`, before validation runs).

    Always a `WARNING`, never a critical issue -- missing/unconnected
    bookable lodging inventory never blocks generation by itself, and
    neither does scraped (Step 168) lodging inventory. When the report is
    missing entirely (stage not yet run) or the provider is
    `not_connected`, the message says so explicitly rather than implying
    lodging was checked. A `success` result with real offers still never
    claims those offers were reviewed for accuracy or scheduled into the
    itinerary -- this app never schedules lodging into the itinerary and
    never adds hotel recommendation logic. When any offer carries
    `scraped_provenance` (Step 168C's disabled-by-default local/manual
    scraped provider), the message explicitly calls out that this is
    scraped_public_page/experimental/fragile data, not official-provider
    data, and needs manual review -- it never claims official price/
    availability/rating/booking-link verification for scraped data. This
    is explicitly distinguished from `DestinationContext.
    candidate_accommodation_pois` (open-data OSM location candidates),
    which are never bookable inventory.
    """
    if (
        accommodation_inventory_report is None
        or accommodation_inventory_report.status == AccommodationSearchStatus.NOT_CONNECTED
    ):
        message = _ACCOMMODATION_NOT_CONNECTED_MESSAGE
    elif accommodation_inventory_report.status == AccommodationSearchStatus.FAILED:
        message = _ACCOMMODATION_FAILED_MESSAGE
    elif (
        accommodation_inventory_report.status == AccommodationSearchStatus.UNAVAILABLE
        or not accommodation_inventory_report.offers
    ):
        message = _ACCOMMODATION_UNAVAILABLE_MESSAGE_TEMPLATE.format(
            detail=accommodation_inventory_report.message or "no offers were returned"
        )
    else:
        offers = accommodation_inventory_report.offers
        is_scraped = any(offer.scraped_provenance is not None for offer in offers)
        message_template = (
            _ACCOMMODATION_SCRAPED_SUCCESS_MESSAGE_TEMPLATE
            if is_scraped
            else _ACCOMMODATION_SUCCESS_MESSAGE_TEMPLATE
        )
        message = message_template.format(
            count=len(offers),
            provider=accommodation_inventory_report.provider,
        )

    return ValidationIssue(
        severity=ValidationSeverity.WARNING,
        category="accommodation_inventory",
        message=message,
        affected_section="stay_transport",
        suggested_fix=_ACCOMMODATION_SUGGESTED_FIX,
    )


_FLIGHT_NOT_CONNECTED_MESSAGE = (
    "No flight inventory provider is connected, so airline, flight number, "
    "schedule, price, availability, baggage, and booking link data could not "
    "be checked."
)
_FLIGHT_FAILED_MESSAGE = (
    "The flight inventory provider request failed, so airline, flight "
    "number, schedule, price, availability, baggage, and booking link data "
    "could not be checked."
)
_FLIGHT_UNAVAILABLE_MESSAGE_TEMPLATE = (
    "The flight inventory provider was checked, but no flight offers were "
    "available ({detail}). No airline, flight number, schedule, price, "
    "availability, baggage, or booking link data exists to review."
)
_FLIGHT_SUCCESS_MESSAGE_TEMPLATE = (
    "{count} provider-backed flight offer(s) were found via {provider}, but "
    "these have not been reviewed for schedule, price, availability, "
    "baggage-policy, or booking-link accuracy, and they are not scheduled "
    "into the itinerary."
)
_FLIGHT_SCRAPED_SUCCESS_MESSAGE_TEMPLATE = (
    "{count} flight offer(s) were found via {provider}, but this data was "
    "scraped from a public page (scraped_public_page/experimental/fragile) "
    "-- it is not official-provider data and has not been verified for "
    "schedule, price, availability, baggage-policy, or booking-link "
    "accuracy. It is not scheduled into the itinerary and needs manual "
    "review before being trusted."
)
_FLIGHT_SUGGESTED_FIX = (
    "Connect a real flight inventory provider, or manually review flight "
    "options for these dates, before treating flights as checked."
)


def _build_flight_inventory_warning(
    flight_inventory_report: FlightSearchResult | None,
) -> ValidationIssue:
    """Deterministic review warning built purely from
    `planning_state.flight_inventory_report` (Step 169E) -- no provider
    call of its own (the lookup already happened in
    `FlightInventoryService`, before validation runs). Mirrors
    `_build_accommodation_inventory_warning` exactly.

    Always a `WARNING`, never a critical issue -- missing/unconnected
    flight inventory never blocks generation by itself, and neither does
    scraped (Step 169C/169D) flight inventory. When the report is missing
    entirely (stage not yet run) or the provider is `not_connected`, the
    message says so explicitly rather than implying flights were checked.
    A `success` result with real offers still never claims those offers
    were reviewed for accuracy or scheduled into the itinerary -- this app
    never schedules a flight into the itinerary as a daily experience and
    never adds flight recommendation logic. When any offer carries
    `scraped_provenance` (Step 169D's default local/manual scraped
    provider), the message explicitly calls out that this is
    scraped_public_page/experimental/fragile data, not official-provider
    data, and needs manual review -- it never claims official schedule/
    price/availability/baggage/booking-link verification for scraped
    data.
    """
    if (
        flight_inventory_report is None
        or flight_inventory_report.status == FlightSearchStatus.NOT_CONNECTED
    ):
        message = _FLIGHT_NOT_CONNECTED_MESSAGE
    elif flight_inventory_report.status == FlightSearchStatus.FAILED:
        message = _FLIGHT_FAILED_MESSAGE
    elif (
        flight_inventory_report.status == FlightSearchStatus.UNAVAILABLE
        or not flight_inventory_report.offers
    ):
        message = _FLIGHT_UNAVAILABLE_MESSAGE_TEMPLATE.format(
            detail=flight_inventory_report.message or "no offers were returned"
        )
    else:
        offers = flight_inventory_report.offers
        is_scraped = any(offer.scraped_provenance is not None for offer in offers)
        message_template = (
            _FLIGHT_SCRAPED_SUCCESS_MESSAGE_TEMPLATE
            if is_scraped
            else _FLIGHT_SUCCESS_MESSAGE_TEMPLATE
        )
        message = message_template.format(
            count=len(offers),
            provider=flight_inventory_report.provider,
        )

    return ValidationIssue(
        severity=ValidationSeverity.WARNING,
        category="flight_inventory",
        message=message,
        affected_section="stay_transport",
        suggested_fix=_FLIGHT_SUGGESTED_FIX,
    )


_INSUFFICIENT_BUFFER_MESSAGE_TEMPLATE = (
    'The {gap:.0f}s gap scheduled between "{from_name}" and "{to_name}" is shorter '
    "than the provider-backed travel time between them ({duration:.0f}s via "
    "{provider}) -- this leg needs review before the schedule can be trusted."
)
_INSUFFICIENT_BUFFER_SUGGESTED_FIX = (
    "Adjust the schedule to allow at least the provider-backed travel time "
    "between these experiences, or re-run route-aware sequencing."
)


def _build_travel_time_buffer_warnings(
    travel_time_buffer_report: TravelTimeBufferReport | None,
) -> list[ValidationIssue]:
    """Deterministic review warnings built purely from
    `planning_state.travel_time_buffer_report` (Step 166C) -- no provider
    call of its own (route lookups already happened in
    `TravelTimeBufferService`, before validation runs). Only ever adds a
    `WARNING`, one per leg whose `buffer_status == insufficient` -- i.e.
    where a real, provider-backed schedule gap is known and a real,
    provider-backed travel duration exceeds it.

    A leg with no known gap (`buffer_status == not_computable`, e.g. no
    schedule timestamps exist yet -- the case for every plan this app
    currently generates) or no usable route data
    (`buffer_status == unavailable`) never produces a warning here, since
    sufficiency can't be honestly judged either way; that case stays
    covered by the existing blanket feasibility warning instead. A
    `sufficient` leg never produces a warning either, since there is
    nothing to review. This never invents a schedule timestamp, gap, or
    duration, and never blocks the plan (never a critical issue).

    Step 166D hardening: deduplicated by
    `(from_experience_id, to_experience_id)` -- if `travel_time_buffer_report`
    ever contained more than one buffer entry for the exact same leg (which
    `TravelTimeBufferService` itself never produces, but this guards
    against it defensively), only one warning is ever raised for it,
    never a duplicate/conflicting pair of warnings about the same leg.
    """
    if travel_time_buffer_report is None:
        return []

    warnings: list[ValidationIssue] = []
    seen_legs: set[tuple[str, str]] = set()
    for buffer in travel_time_buffer_report.buffers:
        if buffer.buffer_status != BufferSufficiencyStatus.INSUFFICIENT:
            continue
        leg_key = (buffer.from_experience_id, buffer.to_experience_id)
        if leg_key in seen_legs:
            continue
        seen_legs.add(leg_key)
        warnings.append(
            ValidationIssue(
                severity=ValidationSeverity.WARNING,
                category="travel_time_buffer",
                message=_INSUFFICIENT_BUFFER_MESSAGE_TEMPLATE.format(
                    from_name=buffer.from_experience_name,
                    to_name=buffer.to_experience_name,
                    gap=buffer.available_gap_seconds,
                    duration=buffer.route_duration_seconds,
                    provider=buffer.provider,
                ),
                affected_section="experience_plan",
                suggested_fix=_INSUFFICIENT_BUFFER_SUGGESTED_FIX,
            )
        )
    return warnings


def _day_geographic_spread_km(day: DailyPlan) -> float | None:
    """Sum of straight-line (haversine) distances between consecutive
    coordinate-backed experiences in a day, in the day's current scheduled
    order.

    Experiences with missing coordinates are skipped entirely rather than
    invented or estimated. Returns None if the day has fewer than two
    coordinate-backed experiences, since spread can't be measured.
    """
    points = [
        experience.coordinates
        for experience in day.experiences
        if experience.coordinates is not None
    ]
    if len(points) < 2:
        return None

    total_km = 0.0
    for previous_point, next_point in zip(points, points[1:]):
        distance_km = haversine_distance_km(previous_point, next_point)
        if distance_km is not None:
            total_km += distance_km
    return total_km
