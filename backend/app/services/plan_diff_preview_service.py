from __future__ import annotations

from app.models.planning_state import (
    PlanDiffPreview,
    PlanningStage,
    PlanningState,
    PreservedLockedItem,
)

# Stage order used for `would_consider_sections`, matching PlanningStage's
# declaration order (docs/09_planning_state.md), consistent with
# FeedbackService._STAGE_ORDER.
_STAGE_ORDER: tuple[PlanningStage, ...] = (
    PlanningStage.TRAVELER_PROFILE,
    PlanningStage.DESTINATION_CONTEXT,
    PlanningStage.TRIP_STRATEGY,
    PlanningStage.STAY_TRANSPORT,
    PlanningStage.EXPERIENCE_PLAN,
    PlanningStage.VALIDATION,
)

# Honest blockers preventing this preview from ever becoming a real
# regeneration/diff today -- the same across every preview_status, since
# none of these are implemented regardless of how much feedback exists.
_BLOCKED_BY: tuple[str, ...] = (
    "Feedback-driven regeneration is not implemented yet.",
    "No AI planning/diff provider is connected.",
    "No new plan version has been generated.",
)


class PlanDiffPreviewService:
    """Owns `PlanningState.plan_diff_preview` (Step 132).

    Recomputes the preview from scratch from `version_history`,
    `feedback_history`, and `user_locks` -- never incrementally patched, so
    it can never drift from the state it describes. This never regenerates
    the plan, never creates a new plan version, never calls an AI/diff
    provider, and never claims a change was applied.
    """

    def recompute(self, planning_state: PlanningState) -> PlanningState:
        version_history = planning_state.version_history
        feedback_history = planning_state.feedback_history
        active_locks = [lock for lock in planning_state.user_locks if lock.is_active]

        active_lock_count = len(active_locks)
        would_preserve_locked_items = [
            PreservedLockedItem(
                locked_item_type=lock.locked_item_type,
                locked_item_id=lock.locked_item_id,
                reason=lock.reason,
            )
            for lock in active_locks
        ]
        from_version = planning_state.metadata.current_version if version_history else None

        if not version_history:
            preview = PlanDiffPreview(
                preview_status="not_available",
                from_version=None,
                active_lock_count=active_lock_count,
                would_preserve_locked_items=would_preserve_locked_items,
                blocked_by=list(_BLOCKED_BY),
                note=(
                    "No plan diff is available yet because no plan has "
                    "been generated for this trip."
                ),
            )
        elif not feedback_history:
            preview = PlanDiffPreview(
                preview_status="not_available",
                from_version=from_version,
                active_lock_count=active_lock_count,
                would_preserve_locked_items=would_preserve_locked_items,
                blocked_by=list(_BLOCKED_BY),
                note=(
                    "No plan diff is available yet because there is no "
                    "pending feedback to preview a regeneration diff."
                ),
            )
        else:
            stage_set = {
                stage for event in feedback_history for stage in event.affected_stages
            }
            would_consider_sections = [stage for stage in _STAGE_ORDER if stage in stage_set]

            preview = PlanDiffPreview(
                preview_status="ready_for_future_regeneration_preview",
                from_version=from_version,
                would_create_version=f"v{len(version_history) + 1}",
                triggered_by_feedback_event_ids=[
                    event.feedback_event_id for event in feedback_history
                ],
                pending_feedback_count=len(feedback_history),
                active_lock_count=active_lock_count,
                would_consider_sections=would_consider_sections,
                would_preserve_locked_items=would_preserve_locked_items,
                blocked_by=list(_BLOCKED_BY),
                note=(
                    "This is a preview only. No plan diff has been "
                    "generated or applied, and no new plan version exists yet."
                ),
            )

        planning_state.plan_diff_preview = preview
        planning_state.touch()
        return planning_state


plan_diff_preview_service = PlanDiffPreviewService()
