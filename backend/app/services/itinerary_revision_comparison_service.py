from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from app.models.itinerary_revision_comparison import (
    ComparedExperience,
    ComparedMovedExperience,
    ComparedReorderedDay,
    ComparedRevisionSide,
    ItineraryRevisionComparison,
)
from app.models.planning_state import PlanningState
from app.repositories.factory import get_lineage_repository
from app.services.revision_snapshot_service import deserialize_planning_state_snapshot
from app.services.targeted_regeneration_diff_builder import compute_itinerary_content_diff

logger = logging.getLogger(__name__)

# Section 199C (Task 19-22): read-only, deterministic comparison of two
# real revisions. No LLM, no provider call, no fuzzy matching, no
# mutation of either revision or any live state -- it only ever reads
# already-stored snapshots and reuses the exact same stable-ID
# comparison (`compute_itinerary_content_diff`) the targeted-regeneration
# diff already uses.


class RevisionComparisonStatus(str, Enum):
    COMPARED = "compared"
    REVISION_NOT_FOUND = "revision_not_found"
    SNAPSHOT_UNAVAILABLE = "snapshot_unavailable"


@dataclass
class RevisionComparisonResult:
    status: RevisionComparisonStatus
    message: str = ""
    missing_revision_id: str | None = None
    comparison: ItineraryRevisionComparison | None = None


def _experience_names(planning_state: PlanningState) -> dict[str, str]:
    if planning_state.experience_plan is None:
        return {}
    return {
        experience.experience_id: experience.name
        for day in planning_state.experience_plan.daily_plans
        for experience in day.experiences
    }


class ItineraryRevisionComparisonService:
    def compare(
        self, trip_id: str, left_revision_id: str, right_revision_id: str
    ) -> RevisionComparisonResult:
        repository = get_lineage_repository()

        loaded = []
        for revision_id in (left_revision_id, right_revision_id):
            revision = repository.get_revision(revision_id)
            if revision is None or revision.trip_id != trip_id:
                # Never reveal whether a revision id belonging to a
                # different trip exists at all.
                return RevisionComparisonResult(
                    status=RevisionComparisonStatus.REVISION_NOT_FOUND,
                    message=f"Revision '{revision_id}' was not found for trip '{trip_id}'.",
                    missing_revision_id=revision_id,
                )
            if not revision.snapshot_available or revision.snapshot is None:
                return RevisionComparisonResult(
                    status=RevisionComparisonStatus.SNAPSHOT_UNAVAILABLE,
                    message=(
                        f"Revision '{revision_id}' has no stored snapshot, so it cannot "
                        "be compared."
                    ),
                    missing_revision_id=revision_id,
                )
            loaded.append((revision, deserialize_planning_state_snapshot(revision.snapshot)))

        (left_revision, left_state), (right_revision, right_state) = loaded
        content = compute_itinerary_content_diff(left_state, right_state)

        left_names = _experience_names(left_state)
        right_names = _experience_names(right_state)

        def resolve(experience_id: str, *preferred: dict[str, str]) -> ComparedExperience:
            for names in preferred:
                if experience_id in names:
                    return ComparedExperience(experience_id=experience_id, name=names[experience_id])
            # Cannot happen for an id taken from one of the two snapshots
            # (compute_itinerary_content_diff only ever emits such ids) --
            # kept as an honest stable-id fallback rather than a guess.
            return ComparedExperience(experience_id=experience_id, name=experience_id)

        def side(revision) -> ComparedRevisionSide:
            branch = repository.get_branch(revision.branch_id)
            return ComparedRevisionSide(
                revision_id=revision.revision_id,
                branch_id=revision.branch_id,
                branch_display_name=branch.display_name if branch else None,
                version_label=revision.version_label,
            )

        moved = [
            ComparedMovedExperience(
                experience_id=item.experience_id,
                name=resolve(item.experience_id, right_names, left_names).name,
                from_day=item.from_day,
                to_day=item.to_day,
            )
            for item in content.moved_experiences
        ]
        reordered = [
            ComparedReorderedDay(
                day_index=day.day_index,
                before_order=[resolve(i, left_names, right_names) for i in day.before_order],
                after_order=[resolve(i, right_names, left_names) for i in day.after_order],
            )
            for day in content.reordered_days
        ]

        no_differences = (
            not content.added_experience_ids
            and not content.removed_experience_ids
            and not moved
            and not reordered
            and content.traveler_profile_diff is None
            and content.validation_status_before == content.validation_status_after
            and content.warning_count_before == content.warning_count_after
            and content.critical_issue_count_before == content.critical_issue_count_after
        )

        comparison = ItineraryRevisionComparison(
            trip_id=trip_id,
            left=side(left_revision),
            right=side(right_revision),
            added_experiences=[resolve(i, right_names) for i in content.added_experience_ids],
            removed_experiences=[resolve(i, left_names) for i in content.removed_experience_ids],
            moved_experiences=moved,
            reordered_days=reordered,
            traveler_profile_diff=content.traveler_profile_diff,
            validation_status_left=content.validation_status_before,
            validation_status_right=content.validation_status_after,
            warning_count_left=content.warning_count_before,
            warning_count_right=content.warning_count_after,
            critical_issue_count_left=content.critical_issue_count_before,
            critical_issue_count_right=content.critical_issue_count_after,
            no_compared_differences=no_differences,
        )
        logger.info(
            "Revision comparison computed for trip %s.",
            trip_id,
            extra={
                "stage": "itinerary_branch",
                "operation": "compare_revisions",
                "trip_id": trip_id,
                "status": "compared",
            },
        )
        return RevisionComparisonResult(
            status=RevisionComparisonStatus.COMPARED, comparison=comparison
        )


itinerary_revision_comparison_service = ItineraryRevisionComparisonService()
