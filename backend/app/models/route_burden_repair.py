"""Bounded post-routing day repair report (Section 203C.2B, final correction).

Records what the single deterministic repair attempt did for each
long-route day: which stop was judged geographically isolated, which unused
grounded candidate was tried in its place, the routing provider's own
before/after walking totals, and whether the change was kept. Names and
provider-measured numbers only -- never an estimate.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RouteBurdenRepairAttempt(BaseModel):
    day_number: int
    # True only when a replacement was actually routed with the provider.
    route_burden_repair_attempted: bool = False
    accepted: bool = False
    # A fixed code: accepted, incomplete_route_data, no_replaceable_stop,
    # no_suitable_candidate, route_budget_exhausted,
    # replacement_route_unavailable, no_material_improvement.
    reason: str
    # Section 3B: why each stop of the day may or may not be replaced, in
    # stop order -- a fixed code per stop: replaceable, must_visit,
    # user_lock, grounded_anchor, no_coordinates. So "no replaceable stop"
    # always names its cause.
    stop_protections: list[str] = Field(default_factory=list)
    replaced_place: str | None = None
    replacement_place: str | None = None
    before_duration_seconds: float | None = None
    before_distance_meters: float | None = None
    after_duration_seconds: float | None = None
    after_distance_meters: float | None = None
    # Diagnostic only: an ACCEPTED replacement made the day materially
    # better, yet the routing provider's own legs still show excessive
    # walking (a walk leg or the day's walking beyond the unchanged limits).
    # Such a day is improved, not resolved: it keeps its long-travel finding.
    hard_walking_violation_remains: bool = False


class RoutabilityRepairAttempt(BaseModel):
    """What the bounded routability repair did for one day whose legs the
    routing provider could not route (Section 3C.2). Fixed codes, place
    names and counts only -- never provider text, and never a route."""

    day_number: int
    # A fixed code: accepted, localized (the legs routed once asked for one
    # at a time; no stop was replaced), transient_failure, malformed_request,
    # no_suspect_stop, suspect_protected, no_suitable_candidate,
    # route_budget_exhausted, replacement_unroutable, replacement_route_burden.
    reason: str
    accepted: bool = False
    # Legs of the day without a factual route, before and after the attempt.
    failed_legs_before: int = 0
    failed_legs_after: int = 0
    # Legs asked for again one at a time to find the stop that cannot be routed.
    relocalized_legs: int = 0
    # The stop every one of whose legs failed, and why it may not be replaced
    # (`replaceable`, `must_visit`, `user_lock`, `no_coordinates`).
    suspect_place: str | None = None
    suspect_protection: str | None = None
    # True when the replaced stop was a grounded anchor: allowed here only,
    # because a place the provider cannot route to outranks anchor preference.
    suspect_was_grounded_anchor: bool = False
    replaced_place: str | None = None
    replacement_place: str | None = None


class RoutabilityRepairReport(BaseModel):
    attempts: list[RoutabilityRepairAttempt] = Field(default_factory=list)
    # Plan-wide factual routing coverage (routed legs / required legs).
    coverage_before: float | None = None
    coverage_after: float | None = None
    generated_at: datetime = Field(default_factory=_utc_now)


class RouteBurdenRepairReport(BaseModel):
    attempts: list[RouteBurdenRepairAttempt] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=_utc_now)
