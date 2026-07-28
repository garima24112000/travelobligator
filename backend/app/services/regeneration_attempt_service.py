from __future__ import annotations

from app.core.errors import REGENERATION_NOT_AVAILABLE_MESSAGE
from app.models.planning_state import PlanningState, RegenerationAttempt
from app.schemas.errors import ErrorCode


class RegenerationAttemptService:
    """Owns `PlanningState.regeneration_attempts` (Step 142): a minimal
    audit trail proving a `POST /trips/{trip_id}/regenerate` call was made
    and refused.

    Purely additive bookkeeping -- appending a `RegenerationAttempt` never
    touches any other section of `PlanningState` (no plan content, no
    version, no feedback, no locks, no readiness). It never regenerates the
    plan, never creates a new plan version, and never claims a change was
    applied, that a real diff exists, or that a locked item will
    definitely be preserved.
    """

    def record_blocked_attempt(self, planning_state: PlanningState) -> PlanningState:
        current_version = (
            planning_state.metadata.current_version
            if planning_state.version_history
            else None
        )
        active_lock_count = sum(
            1 for lock in planning_state.user_locks if lock.is_active
        )

        planning_state.regeneration_attempts.append(
            RegenerationAttempt(
                status="blocked",
                current_version=current_version,
                # Reuses the already-recomputed readiness gate's hint
                # rather than re-deriving it, so the two never disagree.
                would_create_version=(
                    planning_state.regeneration_readiness.would_create_version
                ),
                pending_feedback_count=len(planning_state.feedback_history),
                active_lock_count=active_lock_count,
                reason_code=ErrorCode.REGENERATION_NOT_AVAILABLE.value,
                message=REGENERATION_NOT_AVAILABLE_MESSAGE,
            )
        )
        planning_state.touch()
        return planning_state


regeneration_attempt_service = RegenerationAttemptService()
