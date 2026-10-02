"""Inventory sufficiency + provider usage reports (Section 203C.2B).

`InventorySufficiencyReport` records, BEFORE itinerary reasoning, whether
the grounded candidate pool can support a useful itinerary:

    T  target_stops     = trip_days * pace target per day
    R  minimum_useful   = ceil(0.80 * T)
    H  healthy_buffer   = ceil(2.25 * T)

    healthy          viable >= H
    sufficient       T <= viable < H
    thin_but_usable  R <= viable < T
    insufficient     viable < R

Only `insufficient` may end as `readiness=blocked` with the blocking code
`INSUFFICIENT_VERIFIED_INVENTORY`; that is a completed computation with an
honest result, not a failed generation. Whenever `viable >= R` the plan is
held to the usefulness contract (`app.services.usefulness_contract`).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

INSUFFICIENT_VERIFIED_INVENTORY = "INSUFFICIENT_VERIFIED_INVENTORY"
UNDERFILLED_PLAN = "UNDERFILLED_PLAN"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class InventorySufficiencyStatus(str, Enum):
    HEALTHY = "healthy"
    SUFFICIENT = "sufficient"
    THIN_BUT_USABLE = "thin_but_usable"
    INSUFFICIENT = "insufficient"


class InventorySufficiencyReport(BaseModel):
    status: InventorySufficiencyStatus
    trip_days: int = Field(ge=1)
    pace: str
    target_stops: int = Field(ge=0)
    minimum_useful: int = Field(ge=0)
    healthy_buffer: int = Field(ge=0)
    # Unique, quality-approved, non-low-value grounded candidates.
    viable_candidates: int = Field(ge=0)
    # One bounded expansion round (a further provider page) was tried.
    expansion_attempted: bool = False
    viable_before_expansion: int | None = None
    message: str
    generated_at: datetime = Field(default_factory=_utc_now)


class ProviderUsageReport(BaseModel):
    """Counts only: calls and credits one generation spent per provider
    API. Never a query, a place id, a URL or a key."""

    generation_id: str
    budget: int | None = None
    credits_used: int = 0
    credits_by_api: dict[str, int] = Field(default_factory=dict)
    calls_by_api: dict[str, int] = Field(default_factory=dict)
    refused_calls: int = 0
    # Duplicate candidates merged during this generation, by the rule that
    # matched: `place_id`, `source_identity`, `name_proximity`.
    entity_merges: dict[str, int] = Field(default_factory=dict)
