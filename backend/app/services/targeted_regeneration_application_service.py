from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from app.models.ai_feedback_interpretation import AIFeedbackInterpretationStatus
from app.models.planning_state import PlanningState
from app.models.targeted_regeneration_execution import TargetedRegenerationExecutionStatus
from app.models.targeted_regeneration_plan import TargetedRegenerationPlanStatus
from app.models.targeted_regeneration_runtime import (
    TargetedRegenerationRuntimeResult,
    TargetedRegenerationRuntimeStatus,
)
from app.repositories.planning_state_repository import planning_state_repository as default_planning_state_repository
from app.schemas.errors import ErrorCode
from app.services.ai_feedback_interpretation_request_builder import (
    ai_feedback_interpretation_request_builder as default_request_builder,
)
from app.services.ai_feedback_interpreter_service import ai_feedback_interpreter_service as default_interpreter_service
from app.services.feedback_service import feedback_service as default_feedback_service
from app.services.feedback_service import pending_feedback_events
from app.services.plan_diff_preview_service import plan_diff_preview_service as default_plan_diff_preview_service
from app.services.regeneration_attempt_service import (
    regeneration_attempt_service as default_regeneration_attempt_service,
)
from app.services.regeneration_readiness_service import (
    regeneration_readiness_service as default_regeneration_readiness_service,
)
from app.services.targeted_regeneration_diff_builder import build_targeted_regeneration_diff
from app.services.targeted_regeneration_executor import targeted_regeneration_executor as default_executor
from app.services.targeted_regeneration_plan_builder import (
    targeted_regeneration_plan_builder as default_plan_builder,
)
from app.services.versioning_service import versioning_service as default_versioning_service

logger = logging.getLogger(__name__)

# Section 197C (docs/14_backend_architecture.md, following section
# 149.1): the ONE application-level orchestration boundary that wires
# Sections 196 -> 197A -> 197B into real runtime persistence. Both the
# sync `/regenerate` route and the async job worker call this same
# `regenerate(trip_id)` method (Task 28: semantic parity by
# construction, not by duplicated logic).
#
# This service owns exactly what `TargetedRegenerationExecutor`
# deliberately does NOT: loading state, calling the AI interpreter,
# persisting the structured interpretation, deciding whether to commit,
# version creation, marking feedback applied, and building the content
# diff. It never reimplements deterministic remove/move, provider
# grounding, scoped reasoning, preservation, routing, validation, repair,
# or narration itself -- all of that stays inside 197A/197B, called
# through their own single entry points (Task 8/9).
#
# Multiple pending feedback events (Task 16): this service processes
# exactly ONE pending event per call -- the oldest by `created_at` --
# and leaves every other pending event untouched. This is a deliberate,
# explicit "one intent at a time" strategy (Task 16's option B) chosen
# over combining/concatenating multiple structured interpretations,
# which would risk silently merging two conflicting user intents into
# one plan. A caller with several pending events simply calls
# `regenerate` again for the next one.
#
# Section 197C.1: audits every outcome via the EXISTING, deliberately
# coarse `RegenerationAttemptService`/`RegenerationAttempt` -- the exact
# same model/service the legacy path already uses, never a second
# targeted-only audit model (Task 1/2). Audited legacy behavior: the
# legacy route appends a `RegenerationAttempt` for EVERY refusal it
# produces, not only for a successful/failed execution -- confirm
# missing, an active lock, no pending feedback, and "no derivable
# affected stage" (its own closest analogue to "plan not executable")
# all call `record_blocked_attempt` before refusing. This service
# mirrors that comprehensiveness rather than the narrower "only audit
# once the executor is about to run" framing one early draft of this
# task considered -- real legacy behavior wins (Task 3).
#
# Status vocabulary chosen to stay honest about what actually happened
# (Task 5/10/11): `status="blocked"` for every outcome where the
# executor was NEVER invoked (no pending feedback, an active lock, no
# experience plan, needs_clarification, interpreter rejected/
# not_connected, plan not ready) -- mirrors legacy's own "blocked" for
# its pre-execution refusals exactly. `status="failed"` is reserved for
# outcomes where the executor WAS actually invoked (a
# `TargetedRegenerationExecutionResult` exists) and still didn't
# complete, or where it completed in memory but the pre-commit
# version/lock recheck refused to persist it -- real work happened, even
# though nothing was saved. `status="applied"` is the one success
# outcome, recorded only via `record_applied_attempt`, only after the
# new version/feedback-applied marking/recomputes already happened,
# exactly mirroring `RegenerationAttemptService.record_applied_attempt`'s
# own documented ordering requirement.
#
# One call to `regenerate()` returns exactly once, through exactly one
# of the branches below, so exactly one `RegenerationAttempt` is ever
# appended per call (Task 12) -- Section 194's own bounded AI repair
# loop remains entirely internal to `TargetedRegenerationExecutor` and
# never surfaces here as a separate attempt.

_STAGE = "targeted_regeneration_runtime"


class TargetedRegenerationApplicationService:
    def __init__(
        self,
        planning_state_repository: Any = None,
        interpreter_service: Any = None,
        request_builder: Any = None,
        plan_builder: Any = None,
        executor: Any = None,
        versioning_service: Any = None,
        feedback_service: Any = None,
        plan_diff_preview_service: Any = None,
        regeneration_readiness_service: Any = None,
        regeneration_attempt_service: Any = None,
    ) -> None:
        self.planning_state_repository = planning_state_repository or default_planning_state_repository
        self.interpreter_service = interpreter_service or default_interpreter_service
        self.request_builder = request_builder or default_request_builder
        self.plan_builder = plan_builder or default_plan_builder
        self.executor = executor or default_executor
        self.versioning_service = versioning_service or default_versioning_service
        self.feedback_service = feedback_service or default_feedback_service
        self.plan_diff_preview_service = plan_diff_preview_service or default_plan_diff_preview_service
        self.regeneration_readiness_service = (
            regeneration_readiness_service or default_regeneration_readiness_service
        )
        self.regeneration_attempt_service = regeneration_attempt_service or default_regeneration_attempt_service

    def regenerate(self, trip_id: str) -> TargetedRegenerationRuntimeResult:
        started_at = time.monotonic()
        result = self._regenerate(trip_id)
        duration_ms = (time.monotonic() - started_at) * 1000
        # Task 51: safe fields only -- plain counts/statuses/version
        # labels, never raw feedback text, a prompt, or a credential.
        diff = result.diff
        logger.info(
            "TargetedRegenerationApplicationService.regenerate finished.",
            extra={
                "stage": _STAGE,
                "trip_id": trip_id,
                "feedback_event_count": 1 if result.feedback_event_id else 0,
                "interpretation_status": result.interpretation_status,
                "plan_status": result.plan_status,
                "execution_status": result.execution_status,
                "status": result.status.value,
                "source_version": result.source_version,
                "new_version": result.new_version,
                "affected_day_count": len(diff.affected_day_indices) if diff else 0,
                "preserved_day_count": len(diff.preserved_day_indices) if diff else 0,
                "added_count": len(diff.added_experience_ids) if diff else 0,
                "removed_count": len(diff.removed_experience_ids) if diff else 0,
                "moved_count": len(diff.moved_experiences) if diff else 0,
                "persistence_status": "persisted" if result.is_completed() else "not_persisted",
                "duration_ms": round(duration_ms, 3),
            },
        )
        return result

    def _regenerate(self, trip_id: str) -> TargetedRegenerationRuntimeResult:
        planning_state = self.planning_state_repository.get_by_trip_id(trip_id)
        if planning_state is None:
            return TargetedRegenerationRuntimeResult(
                trip_id=trip_id,
                status=TargetedRegenerationRuntimeStatus.BLOCKED,
                message="Trip not found.",
                block_reasons=["Trip not found."],
            )

        source_version = planning_state.metadata.current_version

        pending = pending_feedback_events(planning_state.feedback_history)
        if not pending:
            self._record_blocked(
                planning_state,
                reason_code=ErrorCode.REGENERATION_NO_PENDING_FEEDBACK.value,
                message="No pending feedback to regenerate from.",
            )
            self.planning_state_repository.save(planning_state)
            return TargetedRegenerationRuntimeResult(
                trip_id=trip_id,
                status=TargetedRegenerationRuntimeStatus.BLOCKED,
                message="No pending feedback to regenerate from.",
                source_version=source_version,
                block_reasons=["No pending feedback."],
            )

        active_lock_count = sum(1 for lock in planning_state.user_locks if lock.is_active)
        if active_lock_count > 0:
            self._record_blocked(
                planning_state,
                reason_code=ErrorCode.REGENERATION_BLOCKED_BY_LOCKS.value,
                message=f"{active_lock_count} active lock(s) block regeneration.",
            )
            self.planning_state_repository.save(planning_state)
            return TargetedRegenerationRuntimeResult(
                trip_id=trip_id,
                status=TargetedRegenerationRuntimeStatus.BLOCKED,
                message=f"{active_lock_count} active lock(s) block regeneration.",
                source_version=source_version,
                block_reasons=[f"{active_lock_count} active lock(s) block regeneration."],
            )

        if planning_state.experience_plan is None:
            self._record_blocked(
                planning_state,
                reason_code=ErrorCode.REGENERATION_NOT_AVAILABLE.value,
                message="No experience plan exists yet.",
            )
            self.planning_state_repository.save(planning_state)
            return TargetedRegenerationRuntimeResult(
                trip_id=trip_id,
                status=TargetedRegenerationRuntimeStatus.BLOCKED,
                message="No experience plan exists yet.",
                source_version=source_version,
                block_reasons=["No experience plan exists yet."],
            )

        # Task 16: exactly one pending event per call, oldest first.
        target_event = min(pending, key=lambda event: event.created_at)

        # Task 3: interpret fresh, immediately before regeneration,
        # against the state we just loaded -- never reuse a previously
        # persisted interpretation to drive a NEW execution (Task 4: this
        # sidesteps stale-interpretation risk by construction, since
        # interpretation and plan-compile always share the same state
        # snapshot within one call).
        interpretation = self.interpreter_service.interpret(planning_state, target_event.feedback_text)
        self._persist_interpretation(target_event, interpretation, source_version)

        if interpretation.status == AIFeedbackInterpretationStatus.NEEDS_CLARIFICATION:
            self._record_blocked(
                planning_state,
                reason_code=ErrorCode.REGENERATION_NEEDS_CLARIFICATION.value,
                message="The feedback is ambiguous and needs clarification before regenerating.",
            )
            self.planning_state_repository.save(planning_state)
            clarification = interpretation.clarification
            return TargetedRegenerationRuntimeResult(
                trip_id=trip_id,
                status=TargetedRegenerationRuntimeStatus.NEEDS_CLARIFICATION,
                message="The feedback is ambiguous and needs clarification before regenerating.",
                feedback_event_id=target_event.feedback_event_id,
                interpretation_status=interpretation.status.value,
                source_version=source_version,
                clarification_reason=clarification.reason if clarification else None,
                clarification_possible_experience_ids=(
                    list(clarification.possible_experience_ids) if clarification else []
                ),
            )

        if interpretation.status in (
            AIFeedbackInterpretationStatus.NOT_CONNECTED,
            AIFeedbackInterpretationStatus.REJECTED,
        ):
            # Task 11: interpreter-unavailable/rejected happens strictly
            # BEFORE the plan compiler or executor ever run -- "blocked",
            # never "failed", the same distinction legacy draws between a
            # pre-execution refusal and an actual mutation attempt.
            self._record_blocked(
                planning_state,
                reason_code=(
                    ErrorCode.REGENERATION_PROVIDER_UNAVAILABLE.value
                    if interpretation.status == AIFeedbackInterpretationStatus.NOT_CONNECTED
                    else ErrorCode.REGENERATION_NOT_AVAILABLE.value
                ),
                message="AI feedback interpretation is not available; targeted regeneration was not attempted.",
            )
            self.planning_state_repository.save(planning_state)
            status = (
                TargetedRegenerationRuntimeStatus.PROVIDER_UNAVAILABLE
                if interpretation.status == AIFeedbackInterpretationStatus.NOT_CONNECTED
                else TargetedRegenerationRuntimeStatus.BLOCKED
            )
            return TargetedRegenerationRuntimeResult(
                trip_id=trip_id,
                status=status,
                message="AI feedback interpretation is not available; targeted regeneration was not attempted.",
                feedback_event_id=target_event.feedback_event_id,
                interpretation_status=interpretation.status.value,
                source_version=source_version,
                block_reasons=list(interpretation.blocked_reasons),
            )

        # Task 7: compile the plan; require READY before ever calling the
        # executor.
        plan = self.plan_builder.build_plan(planning_state, interpretation)
        if plan.status != TargetedRegenerationPlanStatus.READY:
            # Still pre-execution -- the executor is never invoked for a
            # plan that isn't `ready` -- so this is "blocked", mirroring
            # legacy's own "no derivable affected stage" refusal.
            self._record_blocked(
                planning_state,
                reason_code=(
                    ErrorCode.REGENERATION_NEEDS_CLARIFICATION.value
                    if plan.status == TargetedRegenerationPlanStatus.NEEDS_CLARIFICATION
                    else ErrorCode.REGENERATION_NOT_AVAILABLE.value
                ),
                message="The compiled regeneration plan is not executable.",
            )
            self.planning_state_repository.save(planning_state)
            if plan.status == TargetedRegenerationPlanStatus.NEEDS_CLARIFICATION:
                status = TargetedRegenerationRuntimeStatus.NEEDS_CLARIFICATION
            else:
                status = TargetedRegenerationRuntimeStatus.BLOCKED
            return TargetedRegenerationRuntimeResult(
                trip_id=trip_id,
                status=status,
                message="The compiled regeneration plan is not executable.",
                feedback_event_id=target_event.feedback_event_id,
                interpretation_status=interpretation.status.value,
                plan_status=plan.status.value,
                source_version=source_version,
                clarification_reason=plan.clarification_reason,
                clarification_possible_experience_ids=list(plan.clarification_possible_experience_ids),
                block_reasons=list(plan.block_reasons) or list(plan.unsupported_actions),
            )

        # Task 8: the one clean execution boundary -- no deterministic
        # edit/provider/reasoning/preservation/routing/validation/repair/
        # narration logic is reproduced here.
        execution_result = self.executor.execute(planning_state, plan)
        if execution_result.status != TargetedRegenerationExecutionStatus.COMPLETED:
            # Task 5/11: the executor WAS actually invoked here (unlike
            # every branch above) -- this is "failed", not "blocked",
            # even though nothing was ultimately persisted. Distinguishes
            # a new-place PROVIDER_UNAVAILABLE reached mid-execution from
            # the interpreter-level PROVIDER_UNAVAILABLE above, which
            # never got this far.
            self._record_blocked(
                planning_state,
                reason_code=ErrorCode.REGENERATION_NOT_AVAILABLE.value,
                message="Targeted regeneration execution did not complete successfully.",
                status="failed",
            )
            self.planning_state_repository.save(planning_state)
            status_map = {
                TargetedRegenerationExecutionStatus.NEEDS_CLARIFICATION: TargetedRegenerationRuntimeStatus.NEEDS_CLARIFICATION,
                TargetedRegenerationExecutionStatus.PROVIDER_UNAVAILABLE: TargetedRegenerationRuntimeStatus.PROVIDER_UNAVAILABLE,
            }
            status = status_map.get(execution_result.status, TargetedRegenerationRuntimeStatus.FAILED)
            return TargetedRegenerationRuntimeResult(
                trip_id=trip_id,
                status=status,
                message="Targeted regeneration execution did not complete successfully.",
                feedback_event_id=target_event.feedback_event_id,
                interpretation_status=interpretation.status.value,
                plan_status=plan.status.value,
                execution_status=execution_result.status.value,
                source_version=source_version,
                block_reasons=list(execution_result.block_reasons)
                or ([execution_result.failure_reason] if execution_result.failure_reason else []),
            )

        # Task 10: re-check the CURRENT persisted version immediately
        # before committing -- guards against a concurrent regeneration
        # that already advanced the version while this one was doing
        # expensive AI/provider work.
        current_on_disk = self.planning_state_repository.get_by_trip_id(trip_id)
        if current_on_disk is None or current_on_disk.metadata.current_version != plan.source_version:
            # The executor completed in memory, so this is "failed," not
            # "blocked" -- real work happened, it just couldn't be
            # committed. Recorded onto the freshest known state (never
            # the stale `planning_state`) so the audit trail's own
            # `current_version` reflects reality, not what this call
            # started from -- accepting the same small residual race the
            # rest of this commit boundary already discloses (docs/14_
            # backend_architecture.md section 151) rather than claiming
            # perfect cross-request atomicity.
            if current_on_disk is not None:
                self._record_blocked(
                    current_on_disk,
                    reason_code=ErrorCode.REGENERATION_CONFLICT.value,
                    message="The itinerary changed while this regeneration was running; nothing was overwritten.",
                    status="failed",
                )
                self.planning_state_repository.save(current_on_disk)
            return TargetedRegenerationRuntimeResult(
                trip_id=trip_id,
                status=TargetedRegenerationRuntimeStatus.CONFLICT,
                message="The itinerary changed while this regeneration was running; nothing was overwritten.",
                feedback_event_id=target_event.feedback_event_id,
                interpretation_status=interpretation.status.value,
                plan_status=plan.status.value,
                execution_status=execution_result.status.value,
                source_version=source_version,
                block_reasons=["Version conflict -- the itinerary changed during regeneration."],
            )

        # Task 17 (point 3): a lock introduced concurrently, after this
        # call's own earlier lock check but before commit, still blocks
        # -- never weaken lock semantics just because expensive work
        # already happened.
        current_active_lock_count = sum(1 for lock in current_on_disk.user_locks if lock.is_active)
        if current_active_lock_count > 0:
            # Same reasoning as the conflict branch above: the executor
            # already completed in memory, so this is "failed."
            self._record_blocked(
                current_on_disk,
                reason_code=ErrorCode.REGENERATION_BLOCKED_BY_LOCKS.value,
                message=f"{current_active_lock_count} active lock(s) block regeneration.",
                status="failed",
            )
            self.planning_state_repository.save(current_on_disk)
            return TargetedRegenerationRuntimeResult(
                trip_id=trip_id,
                status=TargetedRegenerationRuntimeStatus.BLOCKED,
                message=f"{current_active_lock_count} active lock(s) block regeneration.",
                feedback_event_id=target_event.feedback_event_id,
                interpretation_status=interpretation.status.value,
                plan_status=plan.status.value,
                execution_status=execution_result.status.value,
                source_version=source_version,
                block_reasons=[f"{current_active_lock_count} active lock(s) block regeneration."],
            )

        return self._commit(trip_id, planning_state, plan, execution_result, target_event, interpretation, source_version)

    # -- interpretation persistence (Task 3/14) -----------------------------

    @staticmethod
    def _persist_interpretation(target_event: Any, interpretation: Any, source_version: str | None) -> None:
        """Writes into the EXISTING `FeedbackEvent.interpretation` dict
        slot -- never a new field/migration. Backward compatible: an old
        event's `{"method": "deterministic_rule_based", ...}` shape is
        never read or overwritten by anything except a fresh AI
        interpretation actually running for THAT event. Never stores a
        raw prompt/response -- only the already-structured, already
        forbidden-pattern-checked interpretation result."""
        target_event.interpretation = {
            "method": "ai_interpreted",
            "status": interpretation.status.value,
            "provider": interpretation.provider_name,
            "model": interpretation.model_name,
            "source_version": source_version,
            "confidence": interpretation.confidence,
            "scope": interpretation.scope.value if interpretation.scope else None,
            "structured_result": interpretation.model_dump(mode="json"),
            "interpreted_at": datetime.now(timezone.utc).isoformat(),
        }

    # -- regeneration attempt audit (Section 197C.1) ------------------------

    def _record_blocked(
        self,
        planning_state: PlanningState,
        *,
        reason_code: str,
        message: str,
        status: str = "blocked",
    ) -> None:
        """Appends one `RegenerationAttempt` via the EXISTING, coarse
        `RegenerationAttemptService` -- the exact same model/method the
        legacy path already uses (Task 1/2: no second targeted-only audit
        model). Mutates `planning_state` in place (matching the service's
        own contract) -- the caller is still responsible for its own
        `planning_state_repository.save(...)` afterward, exactly like
        every other call site of this service already does.
        """
        self.regeneration_attempt_service.record_blocked_attempt(
            planning_state, reason_code=reason_code, message=message, status=status
        )

    # -- commit boundary (Task 10/11/12/13/15) -------------------------------

    def _commit(
        self,
        trip_id: str,
        source_planning_state: PlanningState,
        plan: Any,
        execution_result: Any,
        target_event: Any,
        interpretation: Any,
        source_version: str,
    ) -> TargetedRegenerationRuntimeResult:
        resulting_state = execution_result.resulting_planning_state
        assert resulting_state is not None

        changed_sections = self._changed_sections(plan)

        # Task 12: version increment via the existing canonical helper --
        # never a manually concatenated "v" + number.
        resulting_state = self.versioning_service.create_version_after_feedback(
            resulting_state,
            feedback_event_id=target_event.feedback_event_id,
            changed_sections=changed_sections,
            preserved_sections=[f"day_{day}" for day in plan.preserved_day_indices],
            summary=self._version_summary(plan),
        )
        new_version = resulting_state.metadata.current_version

        # Task 15: mark the SAME event applied on the resulting state's
        # own copy, using the exact same 3 fields the legacy mutation
        # path sets, in the same place in the lifecycle (only after a
        # version genuinely exists for it).
        for event in resulting_state.feedback_history:
            if event.feedback_event_id == target_event.feedback_event_id:
                event.applied_at = datetime.now(timezone.utc)
                event.applied_in_version = new_version
                event.handling_status = "applied"
                break

        # Same 3 recomputes the legacy mutation path performs, in the
        # same order, so the rest of the app (readiness/diff-preview/
        # pending-summary) stays internally consistent regardless of
        # which regeneration path produced the new state.
        resulting_state = self.feedback_service.recompute_pending_feedback_summary(resulting_state)
        resulting_state = self.plan_diff_preview_service.recompute(resulting_state)
        resulting_state = self.regeneration_readiness_service.recompute(resulting_state)

        # Task 4: the one successful outcome, recorded via the existing
        # service's own `record_applied_attempt` -- called only after
        # version creation/feedback-marking/recomputes above, exactly
        # matching that method's own documented ordering requirement, so
        # its `current_version` honestly reflects the just-created
        # version, not the one this call started from.
        resulting_state = self.regeneration_attempt_service.record_applied_attempt(resulting_state)

        try:
            self.planning_state_repository.save(resulting_state)
        except Exception:
            # Task 6: the executor succeeded and an attempt record was
            # appended in memory, but persistence itself failed -- never
            # report success, and never retry a second write (which
            # could cascade-fail the same way). Logged, not silently
            # swallowed; the caller gets an honest failure result with no
            # resulting_planning_state, so nothing downstream can mistake
            # this for a completed regeneration.
            logger.warning(
                "TargetedRegenerationApplicationService failed to persist a completed "
                "targeted regeneration; reporting failure rather than claiming success.",
                exc_info=True,
                extra={"stage": _STAGE, "trip_id": trip_id, "status": "failed"},
            )
            return TargetedRegenerationRuntimeResult(
                trip_id=trip_id,
                status=TargetedRegenerationRuntimeStatus.FAILED,
                message="Targeted regeneration completed but could not be persisted.",
                feedback_event_id=target_event.feedback_event_id,
                interpretation_status=interpretation.status.value,
                plan_status=plan.status.value,
                execution_status=execution_result.status.value,
                source_version=source_version,
                block_reasons=["Persistence failed after a completed regeneration."],
            )

        diff = build_targeted_regeneration_diff(
            source_planning_state,
            resulting_state,
            trip_id=trip_id,
            source_version=source_version,
            new_version=new_version,
            affected_day_indices=plan.affected_day_indices,
            preserved_day_indices=plan.preserved_day_indices,
        )

        return TargetedRegenerationRuntimeResult(
            trip_id=trip_id,
            status=TargetedRegenerationRuntimeStatus.COMPLETED,
            message=f"Targeted regeneration applied -- {source_version} -> {new_version}.",
            feedback_event_id=target_event.feedback_event_id,
            interpretation_status=interpretation.status.value,
            plan_status=plan.status.value,
            execution_status=execution_result.status.value,
            source_version=source_version,
            new_version=new_version,
            resulting_planning_state=resulting_state,
            diff=diff,
        )

    @staticmethod
    def _changed_sections(plan: Any) -> list[str]:
        sections: list[str] = [stage.value for stage in plan.required_stages]
        if "experience_plan" not in sections and (
            plan.experience_removals or plan.experience_moves or plan.day_instructions or plan.new_place_lookups
        ):
            sections.append("experience_plan")
        return sections

    @staticmethod
    def _version_summary(plan: Any) -> str:
        if plan.experience_removals:
            return "Targeted regeneration: removed a requested experience."
        if plan.experience_moves:
            return "Targeted regeneration: moved a requested experience."
        if plan.day_instructions:
            days = ", ".join(str(d.day_index) for d in plan.day_instructions)
            return f"Targeted regeneration: regenerated day(s) {days}."
        if plan.traveler_profile_mutation is not None:
            return "Targeted regeneration: updated traveler preferences."
        if plan.new_place_lookups:
            return "Targeted regeneration: added a requested place."
        return "Targeted regeneration applied."


targeted_regeneration_application_service = TargetedRegenerationApplicationService()
