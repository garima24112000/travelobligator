from __future__ import annotations

from app.core.errors import REGENERATION_NOT_AVAILABLE_MESSAGE
from app.models.planning_state import PlanningState, RegenerationAttempt
from app.schemas.errors import ErrorCode


class RegenerationAttemptService:
    """Owns `PlanningState.regeneration_attempts`: a minimal audit trail of
    every `POST /trips/{trip_id}/regenerate` call (Step 142: blocked
    attempts only; Step 174C: also the one successful "applied" outcome).

    Purely additive bookkeeping -- appending a `RegenerationAttempt` never
    touches any other section of `PlanningState` itself (no plan content,
    no version, no feedback, no locks, no readiness). It never regenerates
    the plan or creates a new plan version on its own -- the caller (the
    route) is responsible for doing that first when recording an applied
    attempt, so `current_version`/`would_create_version` below reflect
    whatever state already exists at call time, honestly.
    """

    def record_blocked_attempt(
        self,
        planning_state: PlanningState,
        reason_code: str = ErrorCode.REGENERATION_NOT_AVAILABLE.value,
        message: str = REGENERATION_NOT_AVAILABLE_MESSAGE,
        status: str = "blocked",
    ) -> PlanningState:
        """Appends one `RegenerationAttempt`. `reason_code`/`message`/`status`
        default to today's blanket "not implemented" refusal so every
        existing call site (and test) is unaffected; Step 174B's route-level
        guardrails pass the specific reason for a given refusal (e.g. an
        active lock, or no pending feedback) instead of the default.
        """
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
                status=status,
                current_version=current_version,
                # Reuses the already-recomputed readiness gate's hint
                # rather than re-deriving it, so the two never disagree.
                would_create_version=(
                    planning_state.regeneration_readiness.would_create_version
                ),
                pending_feedback_count=len(planning_state.feedback_history),
                active_lock_count=active_lock_count,
                reason_code=reason_code,
                message=message,
            )
        )
        planning_state.touch()
        return planning_state

    def record_applied_attempt(self, planning_state: PlanningState) -> PlanningState:
        """Records the one successful regeneration outcome (Step 174C).

        Must be called *after* the caller has already created the new
        `VersionHistoryItem` and recomputed `plan_diff_preview`/
        `regeneration_readiness` -- this method never does any of that
        itself, it only appends the audit record reusing
        `record_blocked_attempt`'s exact same shape (no new
        `RegenerationAttempt` fields) with `status="applied"`, so
        `current_version`/`would_create_version` in the recorded attempt
        reflect the real post-regeneration state.
        """
        return self.record_blocked_attempt(
            planning_state,
            reason_code="REGENERATION_APPLIED",
            message="Regeneration was applied from pending feedback.",
            status="applied",
        )


regeneration_attempt_service = RegenerationAttemptService()
