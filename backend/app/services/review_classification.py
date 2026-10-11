"""The one classification of validation findings by what they ask of a traveller.

A review code is the upper-cased category of a validation finding
(`PlanValidatorService`). Each is one of two classes:

  * ``informational`` -- a standing data-coverage or bookkeeping note: data
    that was not applied or not connected (weather, holidays, budget, hotel /
    flight inventory, provider coverage) or an internal consistency report.
    Nothing about the scheduled days needs checking because of it.
  * ``material`` -- something the traveller should look at before following
    the plan: a day that is a lot of travel, a route that could not be
    checked, a ferry on the way, places spread over a large area, a requested
    interest or place that is not covered, a thin or underfilled plan.

This is PRESENTATION only. It never changes `readiness_status`, a review or
blocking code, or what the planner does: an informational-only plan is still
`needs_review`, exactly as before. It is also not the evaluation tooling's
acceptance list (`backend/scripts/canary_city.py`), which decides a benchmark
outcome and may accept a material finding under conditions of its own.

A code this module does not know is MATERIAL: an unclassified finding is
shown to the traveller, never hidden.
"""

from __future__ import annotations

from typing import Iterable

INFORMATIONAL = "informational"
MATERIAL = "material"

# Data that was not applied or not connected, and internal bookkeeping.
INFORMATIONAL_CODES: frozenset[str] = frozenset(
    {
        "ACCOMMODATION_INVENTORY",
        "BUDGET",
        "FLIGHT_INVENTORY",
        "HOLIDAYS",
        "HOTEL_RATINGS",
        "PROVIDER_COVERAGE",
        "PROVIDER_COVERAGE_CONSISTENCY",
        "REGENERATION",
        "REGENERATION_STATE_CONSISTENCY",
        "ROUTE_AWARE_SEQUENCING",
        "ROUTE_GEOMETRY",
        "WEATHER",
    }
)

# What a traveller should check before following the plan.
MATERIAL_CODES: frozenset[str] = frozenset(
    {
        "CATEGORY_CONCENTRATION",
        "CONSTRAINTS",
        "DESTINATION_UNRESOLVED",
        "DUPLICATE_EXPERIENCE",
        "EMPTY_DAY",
        "FEASIBILITY",
        "GEOGRAPHIC_DISPERSION",
        "GEOGRAPHIC_SPREAD",
        "INSUFFICIENT_VERIFIED_INVENTORY",
        "INTEREST_SUPPLY_LIMITED",
        "INTEREST_UNDERCOVERAGE",
        "LONG_TRAVEL_DAY",
        "LOW_VALUE_FILLER_SKIPPED",
        "MOVEMENT_DATA",
        "MUST_VISIT",
        "ROUTE_FEASIBILITY",
        "ROUTE_INCLUDES_FERRY",
        "SCHEDULING",
        "SUSPECTED_DUPLICATE_STOP",
        "THIN_DAY",
        "TRAVEL_TIME_BUFFER",
        "UNDERFILLED_PLAN",
    }
)


def code_of(category: str | None) -> str:
    """The review code of a finding's category."""
    return (category or "").strip().upper()


def classify(code: str | None) -> str:
    """``informational`` only for a code listed as such; everything else --
    a material code and any code this module has never seen -- is ``material``."""
    return INFORMATIONAL if code_of(code) in INFORMATIONAL_CODES else MATERIAL


def classification_for(codes: Iterable[str | None]) -> dict[str, str]:
    """`{code: class}` for the given codes / categories, in code order."""
    return {code: classify(code) for code in sorted({code_of(item) for item in codes} - {""})}
