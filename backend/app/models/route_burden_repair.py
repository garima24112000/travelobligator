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
    # A fixed code: accepted, no_replaceable_stop, no_suitable_candidate,
    # route_budget_exhausted, replacement_route_unavailable,
    # no_material_improvement.
    reason: str
    replaced_place: str | None = None
    replacement_place: str | None = None
    before_duration_seconds: float | None = None
    before_distance_meters: float | None = None
    after_duration_seconds: float | None = None
    after_distance_meters: float | None = None


class RouteBurdenRepairReport(BaseModel):
    attempts: list[RouteBurdenRepairAttempt] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=_utc_now)
