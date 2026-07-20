from __future__ import annotations

from app.models.common import DataStatus, ReadinessStatus, ValidationSeverity
from app.models.planning_state import (
    DailyPlan,
    PlanningStage,
    PlanningState,
    ValidationIssue,
    ValidationReport,
)
from app.services.base import PlanningStageService
from app.services.experience_planner_service import _matches_must_visit
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
    * If a must-visit request does not match any provider-backed
      `candidate_pois` by name, a warning names it explicitly instead of
      silently dropping it or inventing a place to satisfy it. This never
      blocks the plan by itself; it only adds a warning.
    * If the trip request or traveler profile captured a budget (`budget_min`
      and/or `budget_max`), a warning names the captured range and says
      budget/cost validation is not implemented yet, so the plan never
      claims to fit the budget. This never blocks the plan by itself; it
      only adds a warning.
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
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    category="feasibility",
                    message=(
                        "Attractions have been scheduled into days, but route ordering, "
                        "timing, opening-hours, and feasibility checks are not implemented "
                        "yet, so this plan needs review before it can be considered ready."
                    ),
                    affected_section="experience_plan",
                    suggested_fix=(
                        "Implement route/timing feasibility validation before marking "
                        "plans ready."
                    ),
                )
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
        candidate_pois = (
            planning_state.destination_context.candidate_pois
            if planning_state.destination_context
            else []
        )
        unmatched_must_visit = [
            term
            for term in must_visit_terms
            if not any(_matches_must_visit(poi, [term.lower()]) for poi in candidate_pois)
        ]

        if unmatched_must_visit:
            unmatched_list = ", ".join(unmatched_must_visit)
            warnings.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    category="must_visit",
                    message=(
                        f"The following must-visit request(s) were not scheduled because "
                        f"they were not found in provider-backed attraction candidates: "
                        f"{unmatched_list}."
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
                budget_range = f"{budget_min}-{budget_max} {budget_currency}"
            elif budget_min is not None:
                budget_range = f"{budget_min}+ {budget_currency}"
            else:
                budget_range = f"up to {budget_max} {budget_currency}"

            warnings.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    category="budget",
                    message=(
                        f"A budget of {budget_range} was captured, but budget/cost "
                        "validation is not implemented yet, so this plan does not "
                        "confirm it fits within that budget."
                    ),
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
