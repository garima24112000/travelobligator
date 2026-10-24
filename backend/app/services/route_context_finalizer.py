"""M2 Phase 2 (status accuracy): the plan's `route_feasibility_context`,
rebuilt from the FINAL route report once routing has run.

The experience planner writes that context when the plan is drafted, before
any route is requested, so it can only say it holds no route data. After
routing, sequencing, mode adaptation and every repair, the route report holds
the real per-transfer results; this pure function restates them in the
context, so the plan no longer says `unavailable` next to a fully routed day.

Rules (no fact is added):
  * a segment is created only for a transfer with a successful provider route
    (the segment model's own contract) -- mode, distance and duration exactly
    as the route report holds them;
  * a day, and the plan, are `live` only when EVERY transfer has a verified
    route; otherwise `unavailable`, with a warning counting the transfers
    without one (never described as verified);
  * no report, or a routing provider that is not connected, leaves the draft
    context untouched.
No provider, routing or model call; nothing in planning reads the result.
"""

from __future__ import annotations

from typing import Any

from app.models.common import DataStatus, ProviderStatus
from app.models.planning_state import (
    DailyRouteFeasibility,
    PlanningState,
    RouteFeasibilityContext,
    RouteSegment,
)


def _status(value: Any) -> str:
    return getattr(value, "value", value)


def _verified(leg: Any) -> bool:
    return _status(leg.status) == ProviderStatus.SUCCESS.value and leg.duration_seconds is not None


def _unverified_warning(missing: int, total: int) -> str:
    return (
        f"{missing} of {total} transfer{'s' if total != 1 else ''} between stops "
        f"{'has' if missing == 1 else 'have'} no verified route; "
        f"{'it is' if missing == 1 else 'they are'} not verified."
    )


def route_context_from_report(planning_state: PlanningState) -> RouteFeasibilityContext | None:
    """The context restated from the final route report, or None when there is
    nothing to restate (no plan, no report, routing not connected)."""
    plan = planning_state.experience_plan
    report = planning_state.route_feasibility_report
    if plan is None or report is None or _status(report.status) == ProviderStatus.NOT_CONNECTED.value:
        return None

    day_of: dict[str, int] = {}
    for day in plan.daily_plans:
        for experience in day.experiences:
            day_of[experience.experience_id] = day.day_number

    by_day: dict[int, list[Any]] = {}
    for leg in report.legs:
        day_number = day_of.get(leg.from_experience_id)
        if day_number is not None and day_of.get(leg.to_experience_id) == day_number:
            by_day.setdefault(day_number, []).append(leg)

    days: list[DailyRouteFeasibility] = []
    total = verified_total = 0
    for day_number in sorted(by_day):
        legs = by_day[day_number]
        segments = [
            RouteSegment(
                from_place_id=leg.from_experience_id,
                from_name=leg.from_experience_name,
                to_place_id=leg.to_experience_id,
                to_name=leg.to_experience_name,
                travel_mode=leg.mode,
                distance_meters=leg.distance_meters,
                duration_minutes=round(leg.duration_seconds / 60.0, 1),
                source=leg.provider,
                data_status=DataStatus.LIVE,
            )
            for leg in legs
            if _verified(leg)
        ]
        missing = len(legs) - len(segments)
        total += len(legs)
        verified_total += len(segments)
        days.append(
            DailyRouteFeasibility(
                day_number=day_number,
                segments=segments,
                data_status=DataStatus.LIVE if missing == 0 else DataStatus.UNAVAILABLE,
                warnings=[_unverified_warning(missing, len(legs))] if missing else [],
            )
        )

    all_verified = total > 0 and verified_total == total
    warnings = [] if all_verified or total == 0 else [_unverified_warning(total - verified_total, total)]
    return RouteFeasibilityContext(
        source=report.route_data_source if verified_total else "routes_provider",
        data_status=DataStatus.LIVE if all_verified else DataStatus.UNAVAILABLE,
        confidence=round(verified_total / total, 3) if total else 0.0,
        daily_route_feasibility=days,
        assumptions=[
            "Restated from the final route report after the day order was settled: only transfers with a "
            "provider route are listed, with the provider's own mode, distance and duration."
        ],
        warnings=warnings,
    )


def finalize_route_feasibility_context(planning_state: PlanningState) -> PlanningState:
    """Replaces the draft context with the restated one when there is a final
    route report to restate; otherwise leaves the plan unchanged. Never raises:
    a failure keeps the draft context (presentation only)."""
    try:
        context = route_context_from_report(planning_state)
    except Exception:  # noqa: BLE001 - a diagnostic restatement never fails a generation
        return planning_state
    if context is not None and planning_state.experience_plan is not None:
        planning_state.experience_plan.route_feasibility_context = context
    return planning_state
