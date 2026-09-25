from __future__ import annotations

from dataclasses import dataclass, field

from app.models.planning_state import PlanningState
from app.services.experience_identity import experience_stable_key
from app.models.targeted_regeneration_diff import (
    MovedExperienceDiff,
    ReorderedDayDiff,
    TargetedRegenerationDiff,
    TravelerProfileDiff,
)

# Section 197C: pure, deterministic before/after diff construction.
# Never calls a provider/LLM, never mutates either state, never derives
# a fact that isn't already present in `before`/`after` themselves.


def _identity(experience) -> str:
    """Section 202B.1 (Task 6): comparison identity. The stable provider
    place key when the item has one (so the same grounded place is never a
    fake Removed + Added pair even when an older persisted revision gave
    it a different random `experience_id`), else the `experience_id`
    itself. Never a display name or coordinates."""
    key = experience_stable_key(experience)
    return f"place:{key}" if key is not None else f"id:{experience.experience_id}"


def _experience_locations(planning_state: PlanningState) -> dict[str, tuple[str, int]]:
    """identity -> (experience_id, day_number)."""
    if planning_state.experience_plan is None:
        return {}
    return {
        _identity(experience): (experience.experience_id, day.day_number)
        for day in planning_state.experience_plan.daily_plans
        for experience in day.experiences
    }


def _day_order(planning_state: PlanningState, day_number: int) -> list[tuple[str, str]]:
    """[(identity, experience_id)] in this day's order."""
    if planning_state.experience_plan is None:
        return []
    for day in planning_state.experience_plan.daily_plans:
        if day.day_number == day_number:
            return [(_identity(experience), experience.experience_id) for experience in day.experiences]
    return []


def _traveler_profile_diff(before: PlanningState, after: PlanningState) -> TravelerProfileDiff | None:
    before_profile = before.traveler_profile
    after_profile = after.traveler_profile
    before_pace = before_profile.pace.value if before_profile else before.trip_request.pace.value
    after_pace = after_profile.pace.value if after_profile else after.trip_request.pace.value
    before_interests = set(before_profile.interests if before_profile else before.trip_request.interests)
    after_interests = set(after_profile.interests if after_profile else after.trip_request.interests)

    interests_added = sorted(after_interests - before_interests)
    interests_removed = sorted(before_interests - after_interests)

    if before_pace == after_pace and not interests_added and not interests_removed:
        return None

    return TravelerProfileDiff(
        pace_before=before_pace,
        pace_after=after_pace,
        interests_added=interests_added,
        interests_removed=interests_removed,
    )


def traveler_profile_unchanged(before: PlanningState, after: PlanningState) -> bool:
    return _traveler_profile_diff(before, after) is None


@dataclass
class ItineraryContentDiff:
    """The lowest-level, plan-agnostic stable-ID comparison of two
    `PlanningState`s (Section 199C, Task 19/20). Extracted unchanged from
    what `build_targeted_regeneration_diff` always computed inline so the
    generic revision-comparison service and the targeted-regeneration
    diff share ONE implementation -- never a second, divergent copy."""

    added_experience_ids: list[str] = field(default_factory=list)
    removed_experience_ids: list[str] = field(default_factory=list)
    moved_experiences: list[MovedExperienceDiff] = field(default_factory=list)
    reordered_days: list[ReorderedDayDiff] = field(default_factory=list)
    traveler_profile_diff: TravelerProfileDiff | None = None
    validation_status_before: str | None = None
    validation_status_after: str | None = None
    warning_count_before: int = 0
    warning_count_after: int = 0
    critical_issue_count_before: int = 0
    critical_issue_count_after: int = 0


def partition_days_by_final_equality(
    before: PlanningState, after: PlanningState
) -> tuple[list[int], list[int]]:
    """Section 202B.1 (Tasks 8/9): the TRUTHFUL affected/preserved day
    partition, derived only from the final states themselves.

    A day is `preserved` iff it exists on both sides and its complete
    `DailyPlan` content is byte-for-byte equal before vs after; every
    other day (changed, added, or removed) is `affected`. Planned
    preservation metadata (`TargetedRegenerationPlan.preserved_day_
    indices`) is intentionally NOT consulted -- the 202A baseline showed
    an additive request reporting `affected=[]`/`preserved=[1,2,3]` while
    day 2 had actually received a new place, because the reported values
    were the plan's, not the outcome's. Returns `(affected, preserved)`,
    each sorted.
    """
    before_days = {d.day_number: d for d in (before.experience_plan.daily_plans if before.experience_plan else [])}
    after_days = {d.day_number: d for d in (after.experience_plan.daily_plans if after.experience_plan else [])}
    affected: list[int] = []
    preserved: list[int] = []
    for day_number in sorted(set(before_days) | set(after_days)):
        before_day = before_days.get(day_number)
        after_day = after_days.get(day_number)
        if before_day is not None and after_day is not None and before_day.model_dump() == after_day.model_dump():
            preserved.append(day_number)
        else:
            affected.append(day_number)
    return affected, preserved


def compute_itinerary_content_diff(before: PlanningState, after: PlanningState) -> ItineraryContentDiff:
    before_locations = _experience_locations(before)
    after_locations = _experience_locations(after)
    before_identities = set(before_locations)
    after_identities = set(after_locations)

    # Ids reported on each side are that side's own real `experience_id`.
    added = sorted(after_locations[i][0] for i in after_identities - before_identities)
    removed = sorted(before_locations[i][0] for i in before_identities - after_identities)

    # Stable identity only -- a shared identity whose day differs is a
    # move; never inferred from name/coordinate similarity. The moved
    # item is reported under its AFTER id (consumers resolve moved names
    # against the resulting plan).
    common = before_identities & after_identities
    moved = sorted(
        (
            MovedExperienceDiff(
                experience_id=after_locations[identity][0],
                from_day=before_locations[identity][1],
                to_day=after_locations[identity][1],
            )
            for identity in common
            if before_locations[identity][1] != after_locations[identity][1]
        ),
        key=lambda item: item.experience_id,
    )
    moved_identities = {
        identity for identity in common if before_locations[identity][1] != after_locations[identity][1]
    }

    reordered: list[ReorderedDayDiff] = []
    day_numbers = {day.day_number for day in before.experience_plan.daily_plans} if before.experience_plan else set()
    for day_number in sorted(day_numbers):
        before_order = _day_order(before, day_number)
        after_order = _day_order(after, day_number)
        before_seq = [identity for identity, _ in before_order]
        after_seq = [identity for identity, _ in after_order]
        # Only meaningful when the day's own identity SET is unchanged --
        # an add/remove/move on this day is already reported above and
        # must never be double-counted here.
        if set(before_seq) != set(after_seq):
            continue
        if set(before_seq) & moved_identities:
            continue
        if before_seq != after_seq:
            reordered.append(
                ReorderedDayDiff(
                    day_index=day_number,
                    before_order=[experience_id for _, experience_id in before_order],
                    after_order=[experience_id for _, experience_id in after_order],
                )
            )

    before_validation = before.validation_report
    after_validation = after.validation_report

    return ItineraryContentDiff(
        added_experience_ids=added,
        removed_experience_ids=removed,
        moved_experiences=moved,
        reordered_days=reordered,
        traveler_profile_diff=_traveler_profile_diff(before, after),
        validation_status_before=before_validation.readiness_status.value if before_validation else None,
        validation_status_after=after_validation.readiness_status.value if after_validation else None,
        warning_count_before=len(before_validation.warnings) if before_validation else 0,
        warning_count_after=len(after_validation.warnings) if after_validation else 0,
        critical_issue_count_before=len(before_validation.critical_issues) if before_validation else 0,
        critical_issue_count_after=len(after_validation.critical_issues) if after_validation else 0,
    )


def build_targeted_regeneration_diff(
    before: PlanningState,
    after: PlanningState,
    *,
    trip_id: str,
    source_version: str,
    new_version: str,
    affected_day_indices: list[int],
    preserved_day_indices: list[int],
) -> TargetedRegenerationDiff:
    content = compute_itinerary_content_diff(before, after)
    return TargetedRegenerationDiff(
        trip_id=trip_id,
        source_version=source_version,
        new_version=new_version,
        affected_day_indices=sorted(affected_day_indices),
        preserved_day_indices=sorted(preserved_day_indices),
        added_experience_ids=content.added_experience_ids,
        removed_experience_ids=content.removed_experience_ids,
        moved_experiences=content.moved_experiences,
        reordered_days=content.reordered_days,
        traveler_profile_diff=content.traveler_profile_diff,
        validation_status_before=content.validation_status_before,
        validation_status_after=content.validation_status_after,
        warning_count_before=content.warning_count_before,
        warning_count_after=content.warning_count_after,
        critical_issue_count_before=content.critical_issue_count_before,
        critical_issue_count_after=content.critical_issue_count_after,
    )
