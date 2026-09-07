from __future__ import annotations

from app.models.planning_state import PlanningState, RegenerationReadiness
from app.services.feedback_service import derive_pending_affected_stages, pending_feedback_events

# Fixed, honest description of what a real regeneration would need. This
# never changes across states -- only `available_inputs` and
# `missing_capabilities` change as more of it becomes available.
_REQUIRED_INPUTS: tuple[str, ...] = (
    "generated_plan",
    "pending_feedback",
    "regeneration_engine",
)


class RegenerationReadinessService:
    """Owns `PlanningState.regeneration_readiness` (Step 135; Step 174D
    rewires this to the real, deterministic regeneration engine Step 174C
    connected).

    Recomputes the gate from scratch from `version_history`, pending
    (unapplied) feedback (`feedback_service.pending_feedback_events`), and
    `user_locks` -- never incrementally patched, so it can never drift
    from the state it describes. `can_regenerate`/`status` are honest
    about the exact MVP scope `POST /trips/{trip_id}/regenerate` actually
    supports: `true`/"ready" only when a generated plan exists, at least
    one pending feedback event exists, that feedback's own deterministic
    classification names at least one real affected stage, and there are
    zero active locks. Every other combination stays "blocked" -- this
    never regenerates the plan itself, never creates a new plan version,
    never calls an AI/provider, and never claims a locked item will be
    preserved (locks are disallowed entirely for this MVP scope).
    """

    def recompute(self, planning_state: PlanningState) -> PlanningState:
        version_history = planning_state.version_history
        pending_events = pending_feedback_events(planning_state.feedback_history)
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
        elif not pending_events:
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
                    "No pending feedback exists yet to regenerate from.",
                ],
                next_step="Capture feedback before regeneration can run.",
            )
        elif active_lock_count > 0:
            readiness = RegenerationReadiness(
                current_version=planning_state.metadata.current_version,
                would_create_version=f"v{len(version_history) + 1}",
                pending_feedback_count=len(pending_events),
                active_lock_count=active_lock_count,
                required_inputs=list(_REQUIRED_INPUTS),
                available_inputs=[
                    "generated_plan",
                    "version_history",
                    "pending_feedback",
                    "plan_diff_preview",
                    "user_locks",
                ],
                missing_capabilities=["regeneration_engine"],
                blocked_by=[
                    "Regeneration is blocked because one or more active "
                    "locks exist. Remove all active locks before "
                    "requesting regeneration.",
                ],
                next_step="Remove active locks before requesting regeneration.",
            )
        elif not derive_pending_affected_stages(planning_state.feedback_history):
            readiness = RegenerationReadiness(
                current_version=planning_state.metadata.current_version,
                would_create_version=f"v{len(version_history) + 1}",
                pending_feedback_count=len(pending_events),
                active_lock_count=active_lock_count,
                required_inputs=list(_REQUIRED_INPUTS),
                available_inputs=[
                    "generated_plan",
                    "version_history",
                    "pending_feedback",
                    "plan_diff_preview",
                ],
                missing_capabilities=["regeneration_engine"],
                blocked_by=[
                    "Pending feedback exists, but it did not classify to "
                    "any plan section a regeneration could rerun.",
                ],
                next_step=(
                    "Submit feedback that names a specific change (pace, "
                    "stay area, restaurants, etc.)."
                ),
            )
        else:
            readiness = RegenerationReadiness(
                status="ready",
                can_regenerate=True,
                current_version=planning_state.metadata.current_version,
                would_create_version=f"v{len(version_history) + 1}",
                pending_feedback_count=len(pending_events),
                active_lock_count=active_lock_count,
                required_inputs=list(_REQUIRED_INPUTS),
                available_inputs=[
                    "generated_plan",
                    "version_history",
                    "pending_feedback",
                    "plan_diff_preview",
                    "regeneration_engine",
                ],
                missing_capabilities=[],
                blocked_by=[],
                next_step="Call POST /trips/{trip_id}/regenerate with confirm=true.",
            )

        planning_state.regeneration_readiness = readiness
        planning_state.touch()
        return planning_state


regeneration_readiness_service = RegenerationReadinessService()
