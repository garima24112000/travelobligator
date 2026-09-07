from __future__ import annotations

from app.models.planning_state import (
    PlanDiffPreview,
    PlanningState,
    PreservedLockedItem,
)
from app.services.feedback_service import derive_pending_affected_stages, pending_feedback_events

# Honest blockers preventing this preview from becoming a real
# regeneration/diff -- shown only while `regeneration_available` is
# `False`; the exact list depends on which precondition is missing (see
# `recompute`'s branches).
_NO_PLAN_BLOCKED_BY: tuple[str, ...] = (
    "No plan has been generated for this trip yet.",
    "Regeneration engine is not implemented for an ungenerated trip.",
)
_NO_PENDING_FEEDBACK_BLOCKED_BY: tuple[str, ...] = (
    "No pending feedback exists yet to preview a regeneration diff.",
)
_ACTIVE_LOCKS_BLOCKED_BY: tuple[str, ...] = (
    "Regeneration is blocked because one or more active locks exist. "
    "Remove all active locks before requesting regeneration.",
)
_UNCLASSIFIED_FEEDBACK_BLOCKED_BY: tuple[str, ...] = (
    "Pending feedback exists, but it did not classify to any plan "
    "section a regeneration could rerun.",
)


class PlanDiffPreviewService:
    """Owns `PlanningState.plan_diff_preview` (Step 132; Step 174D rewires
    this to the real, deterministic regeneration engine Step 174C
    connected).

    Recomputes the preview from scratch from `version_history`, pending
    (unapplied) feedback (`feedback_service.pending_feedback_events`), and
    `user_locks` -- never incrementally patched, so it can never drift
    from the state it describes. `regeneration_available` is `True` only
    for the exact MVP scope `POST /trips/{trip_id}/regenerate` actually
    supports: a generated plan exists, at least one pending feedback
    event exists, that feedback's own deterministic classification names
    at least one real affected stage, and there are zero active locks.
    This never regenerates the plan, never creates a new plan version,
    never calls an AI/diff provider, and never claims a change was
    applied or invents a content-level (before/after) diff -- only
    section names and preserved-lock identities, exactly as before.
    """

    def recompute(self, planning_state: PlanningState) -> PlanningState:
        version_history = planning_state.version_history
        pending_events = pending_feedback_events(planning_state.feedback_history)
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
                blocked_by=list(_NO_PLAN_BLOCKED_BY),
                note=(
                    "No plan diff is available yet because no plan has "
                    "been generated for this trip."
                ),
            )
        elif not pending_events:
            preview = PlanDiffPreview(
                preview_status="not_available",
                from_version=from_version,
                active_lock_count=active_lock_count,
                would_preserve_locked_items=would_preserve_locked_items,
                blocked_by=list(_NO_PENDING_FEEDBACK_BLOCKED_BY),
                note=(
                    "No plan diff is available yet because there is no "
                    "pending feedback to preview a regeneration diff."
                ),
            )
        elif active_lock_count > 0:
            would_consider_sections = derive_pending_affected_stages(
                planning_state.feedback_history
            )
            preview = PlanDiffPreview(
                preview_status="ready_for_future_regeneration_preview",
                from_version=from_version,
                would_create_version=f"v{len(version_history) + 1}",
                triggered_by_feedback_event_ids=[
                    event.feedback_event_id for event in pending_events
                ],
                pending_feedback_count=len(pending_events),
                active_lock_count=active_lock_count,
                would_consider_sections=would_consider_sections,
                would_preserve_locked_items=would_preserve_locked_items,
                blocked_by=list(_ACTIVE_LOCKS_BLOCKED_BY),
                note=(
                    "This is a preview only. Regeneration cannot run while "
                    "active locks exist; remove them first."
                ),
            )
        elif not derive_pending_affected_stages(planning_state.feedback_history):
            preview = PlanDiffPreview(
                preview_status="ready_for_future_regeneration_preview",
                from_version=from_version,
                would_create_version=f"v{len(version_history) + 1}",
                triggered_by_feedback_event_ids=[
                    event.feedback_event_id for event in pending_events
                ],
                pending_feedback_count=len(pending_events),
                active_lock_count=active_lock_count,
                would_consider_sections=[],
                would_preserve_locked_items=would_preserve_locked_items,
                blocked_by=list(_UNCLASSIFIED_FEEDBACK_BLOCKED_BY),
                note=(
                    "This is a preview only. The pending feedback did not "
                    "classify to any plan section a regeneration could rerun."
                ),
            )
        else:
            would_consider_sections = derive_pending_affected_stages(
                planning_state.feedback_history
            )
            preview = PlanDiffPreview(
                preview_status="regeneration_available",
                from_version=from_version,
                regeneration_available=True,
                would_create_version=f"v{len(version_history) + 1}",
                triggered_by_feedback_event_ids=[
                    event.feedback_event_id for event in pending_events
                ],
                pending_feedback_count=len(pending_events),
                active_lock_count=active_lock_count,
                would_consider_sections=would_consider_sections,
                would_preserve_locked_items=would_preserve_locked_items,
                blocked_by=[],
                note=(
                    "Regeneration is available for this pending feedback. "
                    "Call POST /trips/{trip_id}/regenerate with confirm=true "
                    "to apply it."
                ),
            )

        planning_state.plan_diff_preview = preview
        planning_state.touch()
        return planning_state


plan_diff_preview_service = PlanDiffPreviewService()
