from __future__ import annotations

from app.core.config import get_settings
from app.models.planning_state import FeedbackEvent, PlanningState, RegenerationReadiness
from app.providers.ai_feedback_interpreter.factory import feedback_interpreter_is_configured
from app.services.feedback_service import derive_pending_affected_stages, pending_feedback_events

# Fixed, honest description of what a real regeneration would need. This
# never changes across states -- only `available_inputs` and
# `missing_capabilities` change as more of it becomes available.
_REQUIRED_INPUTS: tuple[str, ...] = (
    "generated_plan",
    "pending_feedback",
    "regeneration_engine",
)

# M2 Phase 1 (D3): why this deployment cannot apply a change request.
# Targeted regeneration is on, but no connected interpreter is configured.
CHANGE_INTERPRETATION_UNAVAILABLE = "change_interpretation_unavailable"
# Targeted regeneration is off and no deterministic operation can apply the
# request(s) (with no registered operation at all, none ever could).
CHANGE_APPLICATION_NOT_SUPPORTED = "change_application_not_supported"
# M2 Phase 2: change application would otherwise work, but the owner's
# `GENERATION_ENABLED=false` switch refuses every new planning run (HTTP 503).
CHANGE_GENERATION_PAUSED = "generation_paused"

_CHANGE_UNAVAILABLE_BLOCKED_BY = (
    "Change requests are saved, but this version of the app cannot apply them to the itinerary."
)
_CHANGE_UNAVAILABLE_NEXT_STEP = (
    "Your request stays saved with the trip. Applying change requests is not available yet."
)
_CHANGE_PAUSED_BLOCKED_BY = "Applying changes is paused for now: new planning runs are switched off."
_CHANGE_PAUSED_NEXT_STEP = "Your request stays saved with the trip. Try again later."


def change_application_unavailable_reason(pending_events: list[FeedbackEvent]) -> str | None:
    """None when `POST /trips/{id}/regenerate` could apply `pending_events`
    as far as the deployment's capabilities go; otherwise the reason it
    would be refused whatever the request says. Mirrors the route's own
    branching: `TARGETED_REGENERATION_ENABLED` picks the targeted path
    (which needs the feedback interpreter), otherwise only the registered
    deterministic legacy operations can apply anything. Configuration and
    registry only -- no provider is called. A missing capability is reported
    before the temporary `GENERATION_ENABLED` switch (the more lasting fact)."""
    capability = _missing_capability(pending_events)
    if capability is not None:
        return capability
    return None if get_settings().generation_enabled else CHANGE_GENERATION_PAUSED


def _missing_capability(pending_events: list[FeedbackEvent]) -> str | None:
    if get_settings().targeted_regeneration_enabled:
        return None if feedback_interpreter_is_configured() else CHANGE_INTERPRETATION_UNAVAILABLE
    # Imported here: the mutation service imports this module.
    from app.services.regeneration_mutation_service import (
        LEGACY_SUPPORTED_OPERATIONS,
        legacy_regeneration_can_apply,
    )

    if not LEGACY_SUPPORTED_OPERATIONS:
        return CHANGE_APPLICATION_NOT_SUPPORTED
    if pending_events and not legacy_regeneration_can_apply(pending_events):
        return CHANGE_APPLICATION_NOT_SUPPORTED
    return None


def apply_change_capability(
    readiness: RegenerationReadiness, pending_events: list[FeedbackEvent]
) -> RegenerationReadiness:
    """A copy of `readiness` stating the deployment's CURRENT change-request
    capability. A readiness that said "ready" while the capability is
    missing is turned into a refusal: an action that would certainly be
    refused is never offered. Nothing else about the readiness changes."""
    reason = change_application_unavailable_reason(pending_events)
    update: dict[str, object] = {
        "change_requests_applicable": reason is None,
        "change_requests_unavailable_reason": reason,
    }
    if reason is not None and readiness.can_regenerate:
        paused = reason == CHANGE_GENERATION_PAUSED
        update.update(
            status="blocked",
            can_regenerate=False,
            available_inputs=[item for item in readiness.available_inputs if item != "regeneration_engine"],
            missing_capabilities=["regeneration_engine"],
            blocked_by=[_CHANGE_PAUSED_BLOCKED_BY if paused else _CHANGE_UNAVAILABLE_BLOCKED_BY],
            next_step=_CHANGE_PAUSED_NEXT_STEP if paused else _CHANGE_UNAVAILABLE_NEXT_STEP,
        )
    return readiness.model_copy(update=update)


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
                    "Regeneration is unavailable for the current planning state.",
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
        elif not get_settings().targeted_regeneration_enabled and not derive_pending_affected_stages(
            planning_state.feedback_history
        ):
            # The targeted path does not need a deterministic classification
            # (the route sends it there before this check), so this refusal
            # applies to the legacy path only.
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

        planning_state.regeneration_readiness = apply_change_capability(readiness, pending_events)
        planning_state.touch()
        return planning_state


regeneration_readiness_service = RegenerationReadinessService()
