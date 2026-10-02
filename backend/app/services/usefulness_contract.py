"""Inventory semantics and the usefulness contract (Section 203C.2B).

Pure reads of `PlanningState` -- no provider, no AI, nothing scheduled or
repaired here.

Inventory (grounded, quality-approved, non-low-value candidates = `viable`):

    healthy          viable >= H = ceil(2.25 * T)
    sufficient       T <= viable < H
    thin_but_usable  R <= viable < T
    insufficient     viable < R = ceil(0.80 * T)

Usefulness. Whenever `viable >= R` a useful itinerary must have BOTH:
  * scheduled meaningful stops >= R, and
  * no day without a meaningful stop.
A meaningful stop is a scheduled experience with a provider identity and
coordinates that is not a low-value single object. A plan that fails while
`viable >= R` is UNDERFILLED (repair, then deterministic fallback, then
`UNDERFILLED_PLAN`). Only `viable < R` is INSUFFICIENT_VERIFIED_INVENTORY.
Nothing is ever padded to pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.candidate_quality import CandidateQualityTier
from app.models.common import ValidationSeverity
from app.models.inventory_sufficiency import (
    INSUFFICIENT_VERIFIED_INVENTORY,
    UNDERFILLED_PLAN,
    InventorySufficiencyStatus,
)
from app.models.planning_state import ExperienceItem, PlanningState, ValidationIssue
from app.services.pace_targets import PaceTargets, pace_targets_for

_VIABLE_TIERS = frozenset(
    {
        CandidateQualityTier.PRIMARY_ANCHOR,
        CandidateQualityTier.GOOD_CANDIDATE,
        CandidateQualityTier.SECONDARY_CANDIDATE,
    }
)


def count_viable_candidates(planning_state: PlanningState) -> int:
    """Unique grounded candidates that could be a meaningful stop: a viable
    quality tier and not a low-value single object."""
    report = planning_state.candidate_quality_report
    if report is None:
        return 0
    return len(
        {
            score.candidate_id
            for score in (*report.attraction_scores, *report.ai_directed_scores)
            if score.quality_tier in _VIABLE_TIERS and not score.low_value_object
        }
    )


def inventory_status(viable: int, targets: PaceTargets) -> InventorySufficiencyStatus:
    if viable >= targets.healthy_buffer:
        return InventorySufficiencyStatus.HEALTHY
    if viable >= targets.target_stops:
        return InventorySufficiencyStatus.SUFFICIENT
    if viable >= targets.minimum_useful:
        return InventorySufficiencyStatus.THIN_BUT_USABLE
    return InventorySufficiencyStatus.INSUFFICIENT


def is_meaningful_stop(experience: ExperienceItem) -> bool:
    return bool(
        experience.provider_place_id
        and experience.provider_source
        and experience.coordinates is not None
        and not experience.low_value_object
    )


@dataclass(frozen=True)
class UsefulnessVerdict:
    targets: PaceTargets
    viable: int
    inventory: InventorySufficiencyStatus
    scheduled_meaningful_stops: int
    empty_days: tuple[int, ...]
    duplicate_place_ids: tuple[str, ...] = field(default_factory=tuple)

    @property
    def enforced(self) -> bool:
        """The contract applies whenever the inventory could satisfy it."""
        return self.inventory != InventorySufficiencyStatus.INSUFFICIENT

    @property
    def meets_total(self) -> bool:
        return self.scheduled_meaningful_stops >= self.targets.minimum_useful

    @property
    def passed(self) -> bool:
        return self.meets_total and not self.empty_days and not self.duplicate_place_ids

    @property
    def underfilled(self) -> bool:
        return self.enforced and not (self.meets_total and not self.empty_days)

    @property
    def target_attainment_ratio(self) -> float:
        target = self.targets.target_stops
        return round(self.scheduled_meaningful_stops / target, 3) if target else 0.0


def evaluate_usefulness(planning_state: PlanningState) -> UsefulnessVerdict:
    targets = pace_targets_for(planning_state)
    report = planning_state.inventory_sufficiency_report
    viable = report.viable_candidates if report is not None else count_viable_candidates(planning_state)

    plan = planning_state.experience_plan
    days = plan.daily_plans if plan is not None else []
    meaningful = 0
    empty_days: list[int] = []
    seen: set[str] = set()
    duplicates: list[str] = []
    for day in days:
        day_meaningful = [experience for experience in day.experiences if is_meaningful_stop(experience)]
        meaningful += len(day_meaningful)
        if not day_meaningful:
            empty_days.append(day.day_number)
        for experience in day.experiences:
            place_id = experience.provider_place_id
            if not place_id:
                continue
            if place_id in seen and place_id not in duplicates:
                duplicates.append(place_id)
            seen.add(place_id)
    if not days:
        empty_days = list(range(1, targets.trip_days + 1))

    return UsefulnessVerdict(
        targets=targets,
        viable=viable,
        inventory=inventory_status(viable, targets),
        scheduled_meaningful_stops=meaningful,
        empty_days=tuple(empty_days),
        duplicate_place_ids=tuple(duplicates),
    )


def usefulness_findings(
    verdict: UsefulnessVerdict,
) -> tuple[list[ValidationIssue], list[ValidationIssue], list[str], list[str]]:
    """`(critical_issues, warnings, blocking_codes, review_codes)`."""
    critical: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    blocking_codes: list[str] = []
    review_codes: list[str] = []
    targets = verdict.targets

    if not verdict.enforced:
        critical.append(
            ValidationIssue(
                severity=ValidationSeverity.CRITICAL,
                category="insufficient_verified_inventory",
                message=(
                    f"Only {verdict.viable} verified place(s) suitable for an itinerary were found for this "
                    f"destination; a {targets.trip_days}-day {targets.pace.value} trip needs at least "
                    f"{targets.minimum_useful}. The plan was not padded with unverified places."
                ),
                affected_section="experience_plan",
                suggested_fix="Try a shorter trip, a more relaxed pace, or a nearby larger destination.",
            )
        )
        blocking_codes.append(INSUFFICIENT_VERIFIED_INVENTORY)
        return critical, warnings, blocking_codes, review_codes

    if verdict.underfilled:
        reasons: list[str] = []
        if not verdict.meets_total:
            reasons.append(
                f"{verdict.scheduled_meaningful_stops} meaningful stop(s) are scheduled, below the "
                f"{targets.minimum_useful} expected for a {targets.trip_days}-day {targets.pace.value} trip "
                f"(target {targets.target_stops})"
            )
        if verdict.empty_days:
            reasons.append(
                "day(s) " + ", ".join(str(day) for day in verdict.empty_days) + " have no meaningful stop"
            )
        warnings.append(
            ValidationIssue(
                severity=ValidationSeverity.WARNING,
                category="underfilled_plan",
                message=(
                    "This itinerary is underfilled: "
                    + "; ".join(reasons)
                    + f", although {verdict.viable} verified candidate(s) were available."
                ),
                affected_section="experience_plan",
                suggested_fix="Regenerate the plan so the available verified places are scheduled.",
            )
        )
        review_codes.append(UNDERFILLED_PLAN)
    return critical, warnings, blocking_codes, review_codes
