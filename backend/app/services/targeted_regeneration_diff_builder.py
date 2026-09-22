from __future__ import annotations

from app.models.planning_state import PlanningState
from app.models.targeted_regeneration_diff import (
    MovedExperienceDiff,
    ReorderedDayDiff,
    TargetedRegenerationDiff,
    TravelerProfileDiff,
)

# Section 197C: pure, deterministic before/after diff construction.
# Never calls a provider/LLM, never mutates either state, never derives
# a fact that isn't already present in `before`/`after` themselves.


def _experience_locations(planning_state: PlanningState) -> dict[str, int]:
    if planning_state.experience_plan is None:
        return {}
    return {
        experience.experience_id: day.day_number
        for day in planning_state.experience_plan.daily_plans
        for experience in day.experiences
    }


def _day_order(planning_state: PlanningState, day_number: int) -> list[str]:
    if planning_state.experience_plan is None:
        return []
    for day in planning_state.experience_plan.daily_plans:
        if day.day_number == day_number:
            return [experience.experience_id for experience in day.experiences]
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
    before_locations = _experience_locations(before)
    after_locations = _experience_locations(after)
    before_ids = set(before_locations)
    after_ids = set(after_locations)

    added = sorted(after_ids - before_ids)
    removed = sorted(before_ids - after_ids)

    # Task 19: stable experience_id identity only -- a shared id whose
    # day differs is a move; never inferred from name/coordinate
    # similarity.
    common_ids = before_ids & after_ids
    moved = sorted(
        (
            MovedExperienceDiff(
                experience_id=experience_id,
                from_day=before_locations[experience_id],
                to_day=after_locations[experience_id],
            )
            for experience_id in common_ids
            if before_locations[experience_id] != after_locations[experience_id]
        ),
        key=lambda item: item.experience_id,
    )
    moved_ids = {item.experience_id for item in moved}

    reordered: list[ReorderedDayDiff] = []
    day_numbers = {day.day_number for day in before.experience_plan.daily_plans} if before.experience_plan else set()
    for day_number in sorted(day_numbers):
        before_order = _day_order(before, day_number)
        after_order = _day_order(after, day_number)
        before_set = set(before_order)
        after_set = set(after_order)
        # Only meaningful when the day's own experience_id SET is
        # unchanged -- an add/remove/move on this day is already
        # reported above and must never be double-counted here.
        if before_set != after_set:
            continue
        if before_set & moved_ids:
            continue
        if before_order != after_order:
            reordered.append(ReorderedDayDiff(day_index=day_number, before_order=before_order, after_order=after_order))

    before_validation = before.validation_report
    after_validation = after.validation_report

    return TargetedRegenerationDiff(
        trip_id=trip_id,
        source_version=source_version,
        new_version=new_version,
        affected_day_indices=sorted(affected_day_indices),
        preserved_day_indices=sorted(preserved_day_indices),
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
