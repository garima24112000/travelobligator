from __future__ import annotations

from app.models.planning_state import PlanningState, RegenerationReadiness

# Fixed, honest description of what a real regeneration would need. This
# never changes across states -- only `available_inputs` and
# `missing_capabilities` change as more of it becomes available.
_REQUIRED_INPUTS: tuple[str, ...] = (
    "generated_plan",
    "pending_feedback",
    "regeneration_engine",
)


class RegenerationReadinessService:
    """Owns `PlanningState.regeneration_readiness` (Step 135).

    Recomputes the gate from scratch from `version_history`,
    `feedback_history`, and `user_locks` -- never incrementally patched, so
    it can never drift from the state it describes. `status` stays
    "blocked" and `can_regenerate` stays False in every branch below,
    because no real regeneration engine is connected yet. This never
    regenerates the plan, never creates a new plan version, never calls an
    AI/provider, and never claims a change was applied or that a locked
    item will definitely be preserved.
    """

    def recompute(self, planning_state: PlanningState) -> PlanningState:
        version_history = planning_state.version_history
        feedback_history = planning_state.feedback_history
        active_lock_count = sum(
            1 for lock in planning_state.user_locks if lock.is_active
        )

        if not version_history:
            readiness = RegenerationReadiness(
                current_version=None,
                would_create_version=None,
                pending_feedback_count=0,
                active_lock_count=active_lock_count,
                required_inputs=list(_REQUIRED_INPUTS),
                available_inputs=[],
                missing_capabilities=["generated_plan", "regeneration_engine"],
                blocked_by=[
                    "No plan has been generated for this trip yet.",
                    "Regeneration engine is not implemented yet.",
                ],
                next_step="Generate the initial plan first.",
            )
        elif not feedback_history:
            available_inputs = ["generated_plan", "version_history"]
            if active_lock_count > 0:
                available_inputs.append("user_locks")

            readiness = RegenerationReadiness(
                current_version=planning_state.metadata.current_version,
                would_create_version=None,
                pending_feedback_count=0,
                active_lock_count=active_lock_count,
                required_inputs=list(_REQUIRED_INPUTS),
                available_inputs=available_inputs,
                missing_capabilities=["pending_feedback", "regeneration_engine"],
                blocked_by=[
                    "No pending feedback exists yet to preview a regeneration.",
                    "Regeneration engine is not implemented yet.",
                ],
                next_step="Capture feedback before regeneration can be previewed.",
            )
        else:
            available_inputs = [
                "generated_plan",
                "version_history",
                "pending_feedback",
                "plan_diff_preview",
            ]
            if active_lock_count > 0:
                available_inputs.append("user_locks")

            readiness = RegenerationReadiness(
                current_version=planning_state.metadata.current_version,
                would_create_version=f"v{len(version_history) + 1}",
                pending_feedback_count=len(feedback_history),
                active_lock_count=active_lock_count,
                required_inputs=list(_REQUIRED_INPUTS),
                available_inputs=available_inputs,
                missing_capabilities=["regeneration_engine"],
                blocked_by=[
                    "Feedback exists, but the regeneration engine is not "
                    "implemented yet.",
                ],
                next_step="Implement the regeneration engine before applying feedback.",
            )

        planning_state.regeneration_readiness = readiness
        planning_state.touch()
        return planning_state


regeneration_readiness_service = RegenerationReadinessService()
