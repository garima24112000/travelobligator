from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from app.core.config import get_settings
from app.models.ai_candidate_promotion import AICandidatePromotionReport, PromotedAICandidate
from app.models.ai_itinerary_reasoning import (
    AIItineraryReasoningRequest,
    AIItineraryReasoningResult,
    AIItineraryReasoningStatus,
    CandidateOrigin,
    ItineraryCandidateReference,
    ItineraryReasoningCategory,
    build_candidate_id,
    validate_result_against_request,
)
from app.models.ai_itinerary_repair import AIItineraryRepairStatus, merge_repair_into_reasoning_result
from app.models.candidate_quality import CandidateQualityTier
from app.models.common import ClaimSource, ClaimSourceType, DataQuality, ProviderStatus
from app.models.planning_state import DailyPlan, ExperienceItem, PlanningState
from app.models.targeted_regeneration_execution import (
    TargetedRegenerationExecutionResult,
    TargetedRegenerationExecutionStatus,
)
from app.models.targeted_regeneration_plan import TargetedRegenerationPlan, TargetedRegenerationPlanStatus
from app.providers.ai_itinerary_reasoning import AIItineraryReasoningProvider, get_ai_itinerary_reasoning_provider
from app.providers.gateway import ProviderGateway, provider_gateway
from app.services.ai_itinerary_repair_request_builder import AIItineraryRepairRequestBuilder
from app.services.ai_itinerary_repair_service import AIItineraryRepairService
from app.services.ai_itinerary_reasoning_request_builder import AIItineraryReasoningRequestBuilder
from app.services.candidate_quality_service import CandidateQualityService
from app.services.experience_planner_service import ExperiencePlannerService
from app.services.itinerary_narrative_service import ItineraryNarrativeService, itinerary_narrative_service
from app.services.plan_validator_service import PlanValidatorService
from app.services.route_aware_sequencing_service import RouteAwareSequencingService, route_aware_sequencing_service
from app.services.route_feasibility_service import RouteFeasibilityService, route_feasibility_service
from app.services.travel_time_buffer_service import TravelTimeBufferService, travel_time_buffer_service

logger = logging.getLogger(__name__)

# Section 197B (docs/14_backend_architecture.md, following section 148):
# the execution engine for a `ready` Section 197A `TargetedRegenerationPlan`.
#
# Core invariant, restated from the task spec and honored structurally
# throughout this module: the SOURCE `PlanningState` passed into
# `execute()` is NEVER mutated, under any outcome. Every edit happens on
# a `model_copy(deep=True)` working state; a non-`completed` result
# always carries `resulting_planning_state=None`. This module composes
# EXISTING stage services (`ExperiencePlannerService`,
# `PlanValidatorService`, `RouteFeasibilityService`,
# `RouteAwareSequencingService`, `TravelTimeBufferService`,
# `AIItineraryReasoningService`'s own request builder + provider,
# `AIItineraryRepairRequestBuilder`/`AIItineraryRepairService`,
# `ItineraryNarrativeService`) -- it never builds a parallel planning
# stack. None of those services accepts a day-scoping parameter (Task 1's
# audit); day-scoping is achieved here by (a) restricting the candidate
# universe/instructions sent to reasoning, (b) structurally rejecting a
# reasoning result that names any day outside the plan's own
# `affected_day_indices`, and (c) splicing every hard-preserved day back
# from a pre-execution snapshot immediately after any whole-trip
# materialization step and before any downstream report is computed --
# so the final itinerary and its routing/validation reports always agree
# (Task 8).


class _DayScopedRepairRequestBuilder:
    """Adapts `AIItineraryRepairRequestBuilder.build_request`'s new
    `allowed_day_indices` parameter (Section 197B, Task 28) to the plain
    `build_request(planning_state, attempt_number=1)` shape
    `AIItineraryRepairService` calls -- lets the executor inject a
    day-scoped builder via `AIItineraryRepairService`'s existing
    constructor injection point without changing that service itself.
    """

    def __init__(self, inner: AIItineraryRepairRequestBuilder, allowed_day_indices: set[int]) -> None:
        self._inner = inner
        self._allowed_day_indices = allowed_day_indices

    def build_request(self, planning_state: PlanningState, attempt_number: int = 1) -> Any:
        return self._inner.build_request(
            planning_state, attempt_number=attempt_number, allowed_day_indices=self._allowed_day_indices
        )


def _restamp_stop_order(day: DailyPlan) -> None:
    for index, experience in enumerate(day.experiences, start=1):
        experience.stop_order = index


def _is_pure_additive_new_place_plan(plan: TargetedRegenerationPlan) -> bool:
    """Section 197B.1 (Task 2): a request is treated as additive-only
    when `new_place_lookups` is the plan's ONLY content -- no other
    structured action authorized touching the existing itinerary.
    "Redo day 2 and include Sintra" (day_instructions present) or
    "Remove the museum and add Sintra" (experience_removals present)
    both fall through to the general pipeline unchanged (Task 18/23) --
    only a bare "I also want to visit Sintra" is additive-only.
    """
    return bool(plan.new_place_lookups) and not (
        plan.experience_removals
        or plan.experience_moves
        or plan.day_instructions
        or plan.traveler_profile_mutation
    )


class TargetedRegenerationExecutor:
    """Pure orchestration boundary (Task 34). `execute` never persists
    anything (Task 35) -- the caller (a future Section 197C) owns
    `planning_state_repository.save`, version creation, feedback
    consumption, and diff-preview generation.
    """

    def __init__(
        self,
        experience_planner_service: ExperiencePlannerService | None = None,
        reasoning_request_builder: AIItineraryReasoningRequestBuilder | None = None,
        reasoning_provider: AIItineraryReasoningProvider | None = None,
        route_feasibility_svc: RouteFeasibilityService | None = None,
        route_aware_sequencing_svc: RouteAwareSequencingService | None = None,
        travel_time_buffer_svc: TravelTimeBufferService | None = None,
        plan_validator_service: PlanValidatorService | None = None,
        repair_request_builder: AIItineraryRepairRequestBuilder | None = None,
        repair_provider: AIItineraryReasoningProvider | None = None,
        narrator_service: ItineraryNarrativeService | None = None,
        gateway: ProviderGateway | None = None,
        quality_service: CandidateQualityService | None = None,
    ) -> None:
        self.experience_planner_service = experience_planner_service or ExperiencePlannerService()
        self.reasoning_request_builder = reasoning_request_builder or AIItineraryReasoningRequestBuilder()
        self._reasoning_provider_override = reasoning_provider
        self.route_feasibility_service = route_feasibility_svc or route_feasibility_service
        self.route_aware_sequencing_service = route_aware_sequencing_svc or route_aware_sequencing_service
        self.travel_time_buffer_service = travel_time_buffer_svc or travel_time_buffer_service
        self.plan_validator_service = plan_validator_service or PlanValidatorService()
        self.repair_request_builder = repair_request_builder or AIItineraryRepairRequestBuilder()
        self._repair_provider_override = repair_provider
        self.narrator_service = narrator_service or itinerary_narrative_service
        self.gateway = gateway or provider_gateway
        self.quality_service = quality_service or CandidateQualityService()

    def _resolve_reasoning_provider(self) -> AIItineraryReasoningProvider:
        return self._reasoning_provider_override or get_ai_itinerary_reasoning_provider()

    # -- public entry point --------------------------------------------

    def execute(
        self, planning_state: PlanningState, plan: TargetedRegenerationPlan
    ) -> TargetedRegenerationExecutionResult:
        started_at = time.monotonic()
        result = self._execute(planning_state, plan)
        duration_ms = (time.monotonic() - started_at) * 1000
        # Task 55: safe fields only -- plain counts/booleans/status
        # strings, never a raw instruction, feedback text, or credential.
        logger.info(
            "TargetedRegenerationExecutor.execute finished.",
            extra={
                "stage": "targeted_regeneration_execution",
                "status": result.status.value,
                "source_version": result.source_version,
                "affected_day_count": len(result.affected_day_indices),
                "preserved_day_count": len(result.preserved_day_indices),
                "deterministic_edit_count": len(result.deterministic_edits_applied),
                "provider_lookup_count": 1 if result.provider_lookup_status == "grounded" else 0,
                "reasoning_required": result.reasoning_status not in (None, "not_required"),
                "repair_attempt_count": 1 if result.repair_attempted else 0,
                "duration_ms": round(duration_ms, 3),
            },
        )
        return result

    def _execute(
        self, planning_state: PlanningState, plan: TargetedRegenerationPlan
    ) -> TargetedRegenerationExecutionResult:
        base_kwargs: dict[str, Any] = dict(
            trip_id=planning_state.trip_id,
            source_version=plan.source_version,
            affected_day_indices=list(plan.affected_day_indices),
            preserved_day_indices=list(plan.preserved_day_indices),
            affected_experience_ids=list(plan.affected_experience_ids),
        )

        if plan.status == TargetedRegenerationPlanStatus.NEEDS_CLARIFICATION:
            return TargetedRegenerationExecutionResult(
                status=TargetedRegenerationExecutionStatus.NEEDS_CLARIFICATION,
                clarification_reason=plan.clarification_reason,
                **base_kwargs,
            )

        if plan.status in (TargetedRegenerationPlanStatus.BLOCKED, TargetedRegenerationPlanStatus.UNSUPPORTED):
            return TargetedRegenerationExecutionResult(
                status=TargetedRegenerationExecutionStatus.BLOCKED,
                block_reasons=list(plan.block_reasons) or list(plan.unsupported_actions),
                **base_kwargs,
            )

        # Task 5: recheck source-version binding immediately before doing
        # any work -- never execute a plan compiled against a since-changed
        # PlanningState.
        if plan.source_version != planning_state.metadata.current_version:
            return TargetedRegenerationExecutionResult(
                status=TargetedRegenerationExecutionStatus.BLOCKED,
                block_reasons=[
                    f"Plan was compiled against version {plan.source_version!r}, but the current "
                    f"planning state is now {planning_state.metadata.current_version!r} -- refusing "
                    "to execute a stale plan."
                ],
                **base_kwargs,
            )

        # Task 6: TOCTOU lock recheck -- never weaken existing global-lock
        # semantics.
        active_lock_count = sum(1 for lock in planning_state.user_locks if lock.is_active)
        if active_lock_count > 0:
            return TargetedRegenerationExecutionResult(
                status=TargetedRegenerationExecutionStatus.BLOCKED,
                block_reasons=[f"{active_lock_count} active lock(s) block execution."],
                **base_kwargs,
            )

        if planning_state.experience_plan is None:
            return TargetedRegenerationExecutionResult(
                status=TargetedRegenerationExecutionStatus.BLOCKED,
                block_reasons=["No experience plan exists to execute against."],
                **base_kwargs,
            )

        try:
            if _is_pure_additive_new_place_plan(plan):
                # Section 197B.1: a pure additive new-place request (no
                # other action authorizes touching the existing
                # itinerary) gets its own narrow execution path -- see
                # `_execute_pure_additive_new_place`'s own docstring for
                # why this is not the same pipeline as every other plan
                # shape.
                return self._execute_pure_additive_new_place(planning_state, plan, base_kwargs)
            return self._execute_ready_plan(planning_state, plan, base_kwargs)
        except Exception as exc:  # never let an unexpected failure fabricate a result
            logger.warning(
                "TargetedRegenerationExecutor.execute failed unexpectedly.",
                exc_info=True,
                extra={"stage": "targeted_regeneration_execution", "status": "failed"},
            )
            return TargetedRegenerationExecutionResult(
                status=TargetedRegenerationExecutionStatus.FAILED,
                failure_reason=f"Execution failed unexpectedly: {exc}",
                **base_kwargs,
            )

    # -- main pipeline ----------------------------------------------------

    def _execute_ready_plan(
        self, planning_state: PlanningState, plan: TargetedRegenerationPlan, base_kwargs: dict[str, Any]
    ) -> TargetedRegenerationExecutionResult:
        original_experience_plan = planning_state.experience_plan
        assert original_experience_plan is not None  # guarded by the caller

        # Task 4: isolated working state -- every edit below happens here.
        working_state = planning_state.model_copy(deep=True)

        # Task 31: a new targeted-regeneration attempt gets its own bounded
        # repair budget -- never inherits a stale count from initial
        # generation or an earlier attempt. The source keeps its own count
        # untouched (we only ever write to `working_state`).
        working_state.ai_itinerary_repair_attempt_count = 0

        # Task 7: hard preservation snapshot, taken from the ORIGINAL
        # (pre-copy) state, before any edit happens anywhere. A pure
        # additive new-place plan never reaches this method at all (see
        # `_is_pure_additive_new_place_plan`/`_execute_pure_additive_new_place`)
        # -- every plan that DOES reach here has at least one action that
        # explicitly authorizes touching the existing itinerary, so
        # `plan.preserved_day_indices` (197A's own computation) is used
        # unconditionally.
        hard_preserved_days: set[int] = set(plan.preserved_day_indices)

        preserved_day_snapshot: dict[int, DailyPlan] = {
            day.day_number: day.model_copy(deep=True)
            for day in original_experience_plan.daily_plans
            if day.day_number in hard_preserved_days
        }
        preserved_experience_snapshot: dict[str, ExperienceItem] = {
            experience.experience_id: experience.model_copy(deep=True)
            for day in original_experience_plan.daily_plans
            for experience in day.experiences
            if experience.experience_id in plan.preserved_experience_ids
        }

        deterministic_edits_applied: list[str] = []
        provider_lookup_status = "not_required"
        reasoning_status = "not_required"

        # Task 9/10: deterministic remove/move -- no provider call, no LLM
        # call, no refill of an emptied slot.
        if plan.experience_removals or plan.experience_moves:
            self._apply_deterministic_edits(working_state, plan, deterministic_edits_applied)
            self._update_reasoning_result_after_deterministic_edit(working_state, plan)

        # Task 15/16: global profile mutation -- only real, existing fields.
        if plan.traveler_profile_mutation is not None:
            self._apply_profile_mutation(working_state, plan.traveler_profile_mutation)

        # Task 17-20: user-requested new-place grounding. Never a fake
        # success -- an ungroundable/low-quality result stops execution
        # here, before any further mutation is considered "applied."
        grounded_candidates: list[ItineraryCandidateReference] = []
        if plan.new_place_lookups:
            provider_lookup_status = "grounded"
            for lookup in plan.new_place_lookups:
                candidate = self._ground_new_place(working_state, lookup.query)
                if candidate is None:
                    return TargetedRegenerationExecutionResult(
                        status=TargetedRegenerationExecutionStatus.PROVIDER_UNAVAILABLE,
                        provider_lookup_status="unavailable",
                        block_reasons=[
                            f"Could not ground requested place {lookup.query!r} to a real, "
                            "sufficiently high-quality provider result."
                        ],
                        **base_kwargs,
                    )
                grounded_candidates.append(candidate)
                # Task 21/22: `ExperiencePlannerService`'s AI-guided
                # materialization resolves a `candidate_id` only against
                # `destination_context`'s own pool -- it never reads the
                # reasoning request's `allowed_candidates` directly. The
                # existing, already-provider-backed "promoted AI
                # candidate" pathway (Step 170D) is the one mechanism that
                # already lets a candidate outside that broad pool
                # materialize into a real `ExperienceItem`, so a grounded
                # new-place candidate is registered there too -- reusing
                # existing materialization machinery rather than adding a
                # second one.
                self._register_grounded_candidate_for_materialization(working_state, candidate)

        # Task 12-14: scoped AI reasoning, only when the plan requires it.
        if plan.requires_ai_itinerary_reasoning:
            reasoning_result = self._run_scoped_reasoning(working_state, plan, hard_preserved_days, grounded_candidates)
            if reasoning_result is None:
                return TargetedRegenerationExecutionResult(
                    status=TargetedRegenerationExecutionStatus.REASONING_FAILED,
                    provider_lookup_status=provider_lookup_status,
                    reasoning_status="failed_or_rejected",
                    block_reasons=[
                        "Scoped AI itinerary reasoning did not produce a usable, "
                        "preservation-safe result."
                    ],
                    **base_kwargs,
                )
            working_state.ai_itinerary_reasoning_result = reasoning_result
            reasoning_status = reasoning_result.status.value

            # Task 22: materialize only through provider-backed objects --
            # never an ExperienceItem built from LLM prose.
            self.experience_planner_service.run(working_state)

        # Task 8/23: splice every hard-preserved day back from the
        # snapshot BEFORE any downstream report is computed, so routing/
        # validation always agree with the final, actually-returned
        # content.
        self._splice_preserved_days(working_state, preserved_day_snapshot)

        # Task 24/25/26: routing / sequencing / buffers, recomputed fresh.
        self._rerun_routing_reports(working_state, hard_preserved_days)

        # Task 27: validator is authoritative.
        self.plan_validator_service.run(working_state)
        validation_status = (
            working_state.validation_report.readiness_status.value
            if working_state.validation_report is not None
            else None
        )

        # Task 28/29: bounded repair, constrained to affected, non-preserved
        # days only.
        repair_attempted = False
        repair_status: str | None = None
        settings = get_settings()
        if settings.ai_itinerary_repair_enabled:
            repair_attempted, repair_status = self._attempt_bounded_repair(working_state, plan, hard_preserved_days)
            if repair_attempted and repair_status == AIItineraryRepairStatus.COMPLETED.value:
                self._splice_preserved_days(working_state, preserved_day_snapshot)
                self._rerun_routing_reports(working_state, hard_preserved_days)
                self.plan_validator_service.run(working_state)
                validation_status = (
                    working_state.validation_report.readiness_status.value
                    if working_state.validation_report is not None
                    else None
                )

        # Task 30/32: narrator on the FINAL working state only.
        self.narrator_service.generate(working_state)
        narrator_status = (
            working_state.itinerary_narrative_report.status.value
            if working_state.itinerary_narrative_report is not None
            else None
        )

        # Task 33: final preservation audit -- a violation here means no
        # `completed` result is ever returned.
        audit_passed, audit_reasons = self._final_preservation_audit(
            working_state, preserved_day_snapshot, preserved_experience_snapshot, plan
        )
        if not audit_passed:
            return TargetedRegenerationExecutionResult(
                status=TargetedRegenerationExecutionStatus.FAILED,
                failure_reason="Preservation audit failed: " + "; ".join(audit_reasons),
                preservation_audit_passed=False,
                provider_lookup_status=provider_lookup_status,
                reasoning_status=reasoning_status,
                **base_kwargs,
            )

        return TargetedRegenerationExecutionResult(
            status=TargetedRegenerationExecutionStatus.COMPLETED,
            resulting_planning_state=working_state,
            deterministic_edits_applied=deterministic_edits_applied,
            provider_lookup_status=provider_lookup_status,
            reasoning_status=reasoning_status,
            routing_rerun=True,
            validation_status=validation_status,
            repair_attempted=repair_attempted,
            repair_status=repair_status,
            narrator_status=narrator_status,
            preservation_audit_passed=True,
            **base_kwargs,
        )

    # -- pure additive new-place execution (Section 197B.1) ----------------

    def _execute_pure_additive_new_place(
        self, planning_state: PlanningState, plan: TargetedRegenerationPlan, base_kwargs: dict[str, Any]
    ) -> TargetedRegenerationExecutionResult:
        """A bare "I also want to visit X" request never carries permission
        to touch anything already scheduled -- Task 2's core invariant.
        Deliberately NOT the general pipeline: existing items are never
        exposed to AI reasoning as selectable/regroupable candidates at
        all (most of them structurally cannot be -- only AI-promoted
        items carry real `provider_place_id`/`provider_source` on
        `ExperienceItem`, per the existing 170D convention), so there is
        nothing for a reasoning result to "drop," "move," or "reorder" in
        the first place -- existing content is immune by construction,
        not merely validated-and-rejected after the fact. Reasoning's
        only job here is choosing WHICH existing day a newly grounded
        candidate should join; the executor then deterministically
        appends that one real, provider-backed item to that day's
        existing (untouched, unreordered) `experiences` list.
        """
        working_state = planning_state.model_copy(deep=True)
        working_state.ai_itinerary_repair_attempt_count = 0
        assert working_state.experience_plan is not None

        # Task 3: snapshot every existing experience -- identity, day
        # assignment, and relative order -- before any edit.
        original_snapshot: list[tuple[int, list[ExperienceItem]]] = [
            (day.day_number, [experience.model_copy(deep=True) for experience in day.experiences])
            for day in working_state.experience_plan.daily_plans
        ]
        original_experience_ids = {
            experience.experience_id for _, experiences in original_snapshot for experience in experiences
        }
        day_count = len(working_state.experience_plan.daily_plans)

        # Task 10/11: ground every requested place FIRST -- if any fails,
        # the whole request fails honestly, before a single item is
        # inserted (never a silent partial success).
        grounded_candidates: list[ItineraryCandidateReference] = []
        for lookup in plan.new_place_lookups:
            candidate = self._ground_new_place(working_state, lookup.query)
            if candidate is None:
                return TargetedRegenerationExecutionResult(
                    status=TargetedRegenerationExecutionStatus.PROVIDER_UNAVAILABLE,
                    provider_lookup_status="unavailable",
                    block_reasons=[
                        f"Could not ground requested place {lookup.query!r} to a real, "
                        "sufficiently high-quality provider result."
                    ],
                    **base_kwargs,
                )
            grounded_candidates.append(candidate)

        if not get_settings().ai_itinerary_reasoning_enabled:
            return TargetedRegenerationExecutionResult(
                status=TargetedRegenerationExecutionStatus.REASONING_FAILED,
                provider_lookup_status="grounded",
                reasoning_status="not_connected",
                block_reasons=["AI reasoning is required to choose an insertion day but is disabled."],
                **base_kwargs,
            )

        # Task 4: exactly one insertion decision per grounded candidate --
        # never a whole-trip regroup.
        insertions: list[tuple[int, ItineraryCandidateReference]] = []
        for candidate in grounded_candidates:
            target_day_index = self._choose_additive_insertion_day(working_state, candidate, day_count)
            if target_day_index is None:
                return TargetedRegenerationExecutionResult(
                    status=TargetedRegenerationExecutionStatus.REASONING_FAILED,
                    provider_lookup_status="grounded",
                    reasoning_status="failed_or_rejected",
                    block_reasons=[f"Could not determine a valid insertion day for {candidate.name!r}."],
                    **base_kwargs,
                )
            insertions.append((target_day_index, candidate))

        # Task 6: deterministic append-only insertion -- the target day's
        # existing items are never rebuilt, reordered, or removed; the
        # new item is simply appended after them.
        daily_plans_by_number = {day.day_number: day for day in working_state.experience_plan.daily_plans}
        deterministic_edits_applied: list[str] = []
        affected_days: set[int] = set()
        for target_day_index, candidate in insertions:
            day = daily_plans_by_number[target_day_index]
            new_item = self._build_experience_item_from_candidate(candidate)
            # Deliberately does NOT call `_restamp_stop_order` here --
            # that would rewrite every pre-existing item's `stop_order`
            # bookkeeping field too, and Task 21's preservation audit
            # requires a pre-existing item to stay byte-for-byte
            # untouched. An append changes no existing item's relative
            # position, so only the new item needs a `stop_order` of its
            # own (its final 1-indexed position in the list).
            day.experiences.append(new_item)
            new_item.stop_order = len(day.experiences)
            affected_days.add(target_day_index)
            deterministic_edits_applied.append(
                f"added {candidate.name!r} ({new_item.experience_id}) to day {target_day_index}"
            )

        # Task 7/19: route-aware sequencing must never reorder ANY day
        # here -- not even the insertion day -- since a generic reorder
        # suggestion has no concept of "preserve the pre-existing
        # sub-order." All days are passed as excluded from `apply_report`;
        # `build_report`'s feasibility data is still computed and attached
        # honestly either way.
        self._rerun_routing_reports(working_state, set(range(1, day_count + 1)))

        # Task 8: validator remains authoritative; a resulting issue is
        # reported honestly, never used as license to remove/move an
        # existing item.
        self.plan_validator_service.run(working_state)
        validation_status = (
            working_state.validation_report.readiness_status.value
            if working_state.validation_report is not None
            else None
        )

        # Task 9: repair is skipped entirely for a pure additive
        # execution -- the current repair contract has no mechanism to
        # guarantee it would never touch an existing, non-inserted item,
        # so the safer choice is to keep the validator's findings honest
        # rather than risk violating preservation.
        repair_attempted = False
        repair_status: str | None = None

        # Task 32: narrator on the final state only.
        self.narrator_service.generate(working_state)
        narrator_status = (
            working_state.itinerary_narrative_report.status.value
            if working_state.itinerary_narrative_report is not None
            else None
        )

        # Task 21: additive-specific preservation audit.
        audit_passed, audit_reasons = self._additive_preservation_audit(
            working_state, original_snapshot, original_experience_ids, grounded_candidates
        )
        if not audit_passed:
            return TargetedRegenerationExecutionResult(
                status=TargetedRegenerationExecutionStatus.FAILED,
                failure_reason="Additive preservation audit failed: " + "; ".join(audit_reasons),
                preservation_audit_passed=False,
                provider_lookup_status="grounded",
                reasoning_status="completed",
                **base_kwargs,
            )

        result_kwargs = dict(base_kwargs)
        result_kwargs["affected_day_indices"] = sorted(affected_days)
        result_kwargs["preserved_day_indices"] = sorted(set(range(1, day_count + 1)) - affected_days)

        return TargetedRegenerationExecutionResult(
            status=TargetedRegenerationExecutionStatus.COMPLETED,
            resulting_planning_state=working_state,
            deterministic_edits_applied=deterministic_edits_applied,
            provider_lookup_status="grounded",
            reasoning_status="completed",
            routing_rerun=True,
            validation_status=validation_status,
            repair_attempted=repair_attempted,
            repair_status=repair_status,
            narrator_status=narrator_status,
            preservation_audit_passed=True,
            **result_kwargs,
        )

    def _choose_additive_insertion_day(
        self, working_state: PlanningState, candidate: ItineraryCandidateReference, day_count: int
    ) -> int | None:
        request = self._build_additive_insertion_request(working_state, candidate, day_count)
        provider = self._resolve_reasoning_provider()
        try:
            result = provider.reason(request)
        except Exception:
            return None

        if result.status != AIItineraryReasoningStatus.COMPLETED:
            return None
        # Exactly one day, naming exactly the one candidate available --
        # anything else (extra days, extra/missing/duplicated candidate
        # ids) is rejected outright, never partially trusted.
        if len(result.days) != 1:
            return None
        day_plan = result.days[0]
        if day_plan.candidate_ids != [candidate.candidate_id]:
            return None
        if not (1 <= day_plan.day_index <= day_count):
            return None

        violations = validate_result_against_request(request, result)
        if violations:
            return None

        return day_plan.day_index

    @staticmethod
    def _build_additive_insertion_request(
        working_state: PlanningState, candidate: ItineraryCandidateReference, day_count: int
    ) -> AIItineraryReasoningRequest:
        trip_request = working_state.trip_request
        traveler_context = AIItineraryReasoningRequestBuilder._traveler_context(working_state)

        existing_summary_lines: list[str] = []
        assert working_state.experience_plan is not None
        for day in working_state.experience_plan.daily_plans:
            names = ", ".join(experience.name for experience in day.experiences) or "(nothing scheduled yet)"
            existing_summary_lines.append(f"Day {day.day_number}: {names}")

        instructions = [
            "This is an ADDITIVE insertion only -- exactly one new candidate is available: "
            f"{candidate.candidate_id!r} ({candidate.name!r}).",
            "Choose exactly one day for it. Return exactly one entry in `days`, for that day, "
            f"whose candidate_ids contains only {candidate.candidate_id!r} -- never any other id, "
            "never more than one entry, never more than one day.",
            "You are NOT selecting, regrouping, or reordering the existing itinerary. The "
            "existing items below are shown only as context to help you choose a sensible day -- "
            "you cannot reference, remove, move, or replace any of them, and none of them appear "
            "in your allowed candidates.",
            "Existing itinerary (context only, not selectable):",
            *existing_summary_lines,
        ]

        return AIItineraryReasoningRequest(
            trip_id=working_state.trip_id,
            destination_name=trip_request.primary_destination,
            start_date=trip_request.start_date.isoformat(),
            end_date=trip_request.end_date.isoformat(),
            trip_duration_days=day_count,
            traveler_context=traveler_context,
            allowed_candidates=[candidate],
            reasoning_instructions=instructions,
        )

    @staticmethod
    def _build_experience_item_from_candidate(candidate: ItineraryCandidateReference) -> ExperienceItem:
        return ExperienceItem(
            name=candidate.name,
            category=candidate.category.value,
            coordinates=candidate.coordinates,
            why_included="You explicitly asked for this place to be added.",
            confidence=candidate.quality_score,
            data_quality=DataQuality(data_status=candidate.data_status, confidence=candidate.quality_score),
            claim_sources=[
                ClaimSource(
                    claim=f"{candidate.name} is a real place found via a direct provider lookup for your request.",
                    source_type=ClaimSourceType.PROVIDER_FACT,
                    source=candidate.provider_name,
                    based_on=["user_requested_new_place_lookup"],
                )
            ],
            promoted_from_ai=False,
            provider_place_id=candidate.provider_place_id,
            provider_source=candidate.provider_name,
        )

    @staticmethod
    def _additive_preservation_audit(
        working_state: PlanningState,
        original_snapshot: list[tuple[int, list[ExperienceItem]]],
        original_experience_ids: set[str],
        grounded_candidates: list[ItineraryCandidateReference],
    ) -> tuple[bool, list[str]]:
        """Task 21: every original item must still exist, on its original
        day, in its original relative order, byte-for-byte unchanged.
        The only new experience_ids allowed anywhere in the final plan
        are ones that resolve to a grounded, requested candidate.
        """
        reasons: list[str] = []
        experience_plan = working_state.experience_plan
        if experience_plan is None:
            return False, ["No experience plan exists on the final working state."]

        daily_plans_by_number = {day.day_number: day for day in experience_plan.daily_plans}

        for day_number, original_experiences in original_snapshot:
            current_day = daily_plans_by_number.get(day_number)
            if current_day is None:
                reasons.append(f"day {day_number} is missing from the final plan.")
                continue
            current_originals = [e for e in current_day.experiences if e.experience_id in original_experience_ids]
            if len(current_originals) != len(original_experiences):
                reasons.append(f"day {day_number} lost or duplicated an original experience.")
                continue
            for original_item, current_item in zip(original_experiences, current_originals):
                if original_item.experience_id != current_item.experience_id:
                    reasons.append(f"day {day_number} changed the relative order of its original experiences.")
                    break
                if current_item.model_dump() != original_item.model_dump():
                    reasons.append(
                        f"original experience {original_item.experience_id!r} was altered "
                        "(identity/provider identity/coordinates must stay byte-for-byte identical)."
                    )

        grounded_ids = {candidate.candidate_id for candidate in grounded_candidates}
        all_current_ids = {
            experience.experience_id for day in experience_plan.daily_plans for experience in day.experiences
        }
        new_experience_ids = all_current_ids - original_experience_ids
        if len(new_experience_ids) != len(grounded_candidates):
            reasons.append(
                f"expected exactly {len(grounded_candidates)} new experience(s), found "
                f"{len(new_experience_ids)}."
            )
        for day in experience_plan.daily_plans:
            for experience in day.experiences:
                if experience.experience_id not in new_experience_ids:
                    continue
                candidate_id = (
                    build_candidate_id(experience.provider_source, experience.provider_place_id)
                    if experience.provider_source and experience.provider_place_id
                    else None
                )
                if candidate_id not in grounded_ids:
                    reasons.append(
                        f"new experience {experience.experience_id!r} does not match a grounded "
                        "requested candidate."
                    )

        return (len(reasons) == 0, reasons)

    # -- deterministic edits (Task 9/10/11) --------------------------------

    @staticmethod
    def _apply_deterministic_edits(
        working_state: PlanningState, plan: TargetedRegenerationPlan, applied_log: list[str]
    ) -> None:
        assert working_state.experience_plan is not None
        daily_plans_by_number = {day.day_number: day for day in working_state.experience_plan.daily_plans}

        for removal in plan.experience_removals:
            day = daily_plans_by_number.get(removal.day_index)
            if day is None:
                continue
            day.experiences = [e for e in day.experiences if e.experience_id != removal.experience_id]
            _restamp_stop_order(day)
            applied_log.append(f"removed {removal.experience_id} from day {removal.day_index}")

        for move in plan.experience_moves:
            source_day = daily_plans_by_number.get(move.source_day_index)
            target_day = daily_plans_by_number.get(move.target_day_index)
            if source_day is None or target_day is None:
                continue
            item = next((e for e in source_day.experiences if e.experience_id == move.experience_id), None)
            if item is None:
                continue
            source_day.experiences = [e for e in source_day.experiences if e.experience_id != move.experience_id]
            _restamp_stop_order(source_day)
            item.day_number = target_day.day_number
            target_day.experiences.append(item)
            _restamp_stop_order(target_day)
            applied_log.append(
                f"moved {move.experience_id} from day {move.source_day_index} to day {move.target_day_index}"
            )

    @staticmethod
    def _update_reasoning_result_after_deterministic_edit(
        working_state: PlanningState, plan: TargetedRegenerationPlan
    ) -> None:
        """Task 11: keep `ai_itinerary_reasoning_result` consistent with a
        deterministic remove/move rather than leaving it claim a removed
        candidate is still selected, or a moved one still on its old day.
        Never asks an LLM to re-decide a user-explicit remove/move.
        """
        result = working_state.ai_itinerary_reasoning_result
        if result is None or result.status != AIItineraryReasoningStatus.COMPLETED:
            return

        removed_candidate_ids = {r.candidate_id for r in plan.experience_removals if r.candidate_id}
        moved_candidate_targets = {m.candidate_id: m.target_day_index for m in plan.experience_moves if m.candidate_id}
        if not removed_candidate_ids and not moved_candidate_targets:
            return

        new_days = []
        for day in result.days:
            remaining = [
                candidate_id
                for candidate_id in day.candidate_ids
                if candidate_id not in removed_candidate_ids and candidate_id not in moved_candidate_targets
            ]
            if remaining:
                new_days.append(day.model_copy(update={"candidate_ids": remaining}))

        for candidate_id, target_day_index in moved_candidate_targets.items():
            target = next((day for day in new_days if day.day_index == target_day_index), None)
            if target is not None and candidate_id not in target.candidate_ids:
                target.candidate_ids.append(candidate_id)
            # If no day-plan entry exists yet for the target day, this is
            # left as-is -- `ExperiencePlannerService` reads the real,
            # already-edited `experience_plan` (not just the reasoning
            # result) for the deterministic-edit-only path, so the true
            # schedule is never inconsistent even when the reasoning
            # result's own bookkeeping can't represent a brand-new day
            # entry.

        if new_days != result.days:
            working_state.ai_itinerary_reasoning_result = result.model_copy(update={"days": new_days})

    # -- profile mutation (Task 15/16) -------------------------------------

    @staticmethod
    def _apply_profile_mutation(working_state: PlanningState, mutation: Any) -> None:
        profile = working_state.traveler_profile
        if profile is not None:
            if mutation.pace is not None:
                profile.pace = mutation.pace
            for interest in mutation.interests_to_add:
                if interest not in profile.interests:
                    profile.interests.append(interest)
            if mutation.interests_to_remove:
                profile.interests = [i for i in profile.interests if i not in mutation.interests_to_remove]
        # `trip_request` is the fallback traveler-context source
        # (`AIItineraryReasoningRequestBuilder._traveler_context`) whenever
        # no `traveler_profile` has been computed yet -- mutate it too so
        # scoped reasoning reflects the requested change either way.
        trip_request = working_state.trip_request
        if mutation.pace is not None:
            trip_request.pace = mutation.pace
        for interest in mutation.interests_to_add:
            if interest not in trip_request.interests:
                trip_request.interests.append(interest)
        if mutation.interests_to_remove:
            trip_request.interests = [i for i in trip_request.interests if i not in mutation.interests_to_remove]

    # -- new-place grounding (Task 17-20) ----------------------------------

    def _ground_new_place(self, working_state: PlanningState, query: str) -> ItineraryCandidateReference | None:
        destination_name = working_state.trip_request.primary_destination
        try:
            response = self.gateway.places.search_must_visit_place(query, destination_name)
        except Exception:
            return None

        if response.status in (ProviderStatus.NOT_CONNECTED, ProviderStatus.FAILED):
            return None

        place = response.data[0] if response.data else None
        if place is None or place.coordinates is None:
            return None

        traveler_profile = working_state.traveler_profile
        if traveler_profile is not None:
            user_interests = list(traveler_profile.interests)
            must_visit_names = list(traveler_profile.must_visit)
        else:
            user_interests = list(working_state.trip_request.interests)
            must_visit_names = list(working_state.trip_request.must_visit)

        score = self.quality_service.score_provider_backed_candidate(
            place, user_interests=user_interests, must_visit_names=must_visit_names
        )
        # Task 19: a low-quality/unresolved result never becomes a trusted
        # candidate just because the user named it.
        if score.quality_tier == CandidateQualityTier.REJECTED:
            return None

        category = (
            ItineraryReasoningCategory.RESTAURANT
            if getattr(score.use_case, "value", "") == "restaurant"
            else ItineraryReasoningCategory.ATTRACTION
        )

        return ItineraryCandidateReference(
            candidate_id=build_candidate_id(place.source, place.place_id),
            name=place.name,
            category=category,
            provider_name=place.source,
            provider_place_id=place.place_id,
            coordinates=place.coordinates,
            data_status=place.data_status,
            quality_score=score.total_score,
            quality_tier=score.quality_tier.value,
            # Task 17: never mislabeled as an AI proposal/AI-directed
            # discovery -- this is a real, user-typed request.
            origin=CandidateOrigin.USER_REQUESTED,
        )

    @staticmethod
    def _register_grounded_candidate_for_materialization(
        working_state: PlanningState, candidate: ItineraryCandidateReference
    ) -> None:
        promoted = PromotedAICandidate(
            candidate_id=candidate.candidate_id,
            name=candidate.name,
            category=candidate.category.value,
            source="user_requested_new_place",
            provider_place_id=candidate.provider_place_id,
            provider_source=candidate.provider_name,
            quality_bucket=candidate.quality_tier,
            coordinates=candidate.coordinates,
            confidence=candidate.quality_score,
            data_status=candidate.data_status.value,
            promotion_reasons=["User explicitly requested this place in feedback."],
        )
        report = working_state.ai_candidate_promotion_report
        if report is None:
            working_state.ai_candidate_promotion_report = AICandidatePromotionReport(
                trip_id=working_state.trip_id,
                status="user_requested_candidate_registered",
                total_reviewed_candidates=1,
                promoted_count=1,
                promoted_candidates=[promoted],
                generated_at=datetime.now(timezone.utc),
            )
        else:
            report.promoted_candidates.append(promoted)
            report.promoted_count += 1
            report.total_reviewed_candidates += 1

    # -- scoped reasoning (Task 12-14) -------------------------------------

    @staticmethod
    def _reserved_candidate_ids(working_state: PlanningState, hard_preserved_days: set[int]) -> set[str]:
        reserved: set[str] = set()
        if working_state.experience_plan is None:
            return reserved
        for day in working_state.experience_plan.daily_plans:
            if day.day_number not in hard_preserved_days:
                continue
            for experience in day.experiences:
                if experience.provider_source and experience.provider_place_id:
                    reserved.add(build_candidate_id(experience.provider_source, experience.provider_place_id))
        return reserved

    @staticmethod
    def _scoped_instructions(plan: TargetedRegenerationPlan) -> list[str]:
        instructions: list[str] = []
        if plan.day_instructions:
            day_list = ", ".join(str(day.day_index) for day in plan.day_instructions)
            instructions.append(
                f"Only day(s) {day_list} may appear in your response `days` list. Every other "
                "day is already finalized -- do not mention or change it."
            )
            for day_instruction in plan.day_instructions:
                instructions.append(f"For day {day_instruction.day_index}: {day_instruction.instruction}")
        return instructions

    def _run_scoped_reasoning(
        self,
        working_state: PlanningState,
        plan: TargetedRegenerationPlan,
        hard_preserved_days: set[int],
        grounded_candidates: list[ItineraryCandidateReference],
    ) -> AIItineraryReasoningResult | None:
        if not get_settings().ai_itinerary_reasoning_enabled:
            return None

        request = self.reasoning_request_builder.build_request(working_state)

        # Task 13: candidates already reserved by a hard-preserved day are
        # removed from the universe entirely -- the model cannot "steal"
        # what it never sees.
        reserved_ids = self._reserved_candidate_ids(working_state, hard_preserved_days)
        filtered_candidates = [c for c in request.allowed_candidates if c.candidate_id not in reserved_ids]
        filtered_candidates.extend(grounded_candidates)

        scoped_request = request.model_copy(
            update={
                "allowed_candidates": filtered_candidates,
                "reasoning_instructions": list(request.reasoning_instructions) + self._scoped_instructions(plan),
            }
        )

        provider = self._resolve_reasoning_provider()
        try:
            result = provider.reason(scoped_request)
        except Exception:
            return None

        if result.status != AIItineraryReasoningStatus.COMPLETED:
            return None

        # Task 39: reasoning may only ever return the day(s) the plan
        # actually marked affected -- never a preserved or unrelated day.
        for day in result.days:
            if day.day_index not in plan.affected_day_indices:
                return None

        # Task 14/40: independent structural re-validation against the
        # RESTRICTED request -- defense against a misbehaving/fake
        # provider that ignores the candidate universe it was given.
        violations = validate_result_against_request(scoped_request, result)
        if violations:
            return None

        return result

    # -- preservation splice (Task 8/23) -----------------------------------

    @staticmethod
    def _splice_preserved_days(working_state: PlanningState, preserved_day_snapshot: dict[int, DailyPlan]) -> None:
        if not preserved_day_snapshot or working_state.experience_plan is None:
            return
        working_state.experience_plan.daily_plans = [
            preserved_day_snapshot.get(day.day_number, day) for day in working_state.experience_plan.daily_plans
        ]

    # -- routing / sequencing / buffers (Task 24/25/26) --------------------

    def _rerun_routing_reports(self, working_state: PlanningState, hard_preserved_days: set[int]) -> None:
        working_state.route_feasibility_report = self.route_feasibility_service.build_report(working_state)

        sequencing_report = self.route_aware_sequencing_service.build_report(working_state)
        working_state.route_aware_sequencing_report = sequencing_report

        settings = get_settings()
        if settings.route_aware_scheduling_enabled:
            # Task 25: never reorder a hard-preserved day, even though
            # `build_report` computes suggestions for every day with 2+
            # experiences.
            scoped_suggestions = [s for s in sequencing_report.suggestions if s.day_index not in hard_preserved_days]
            if scoped_suggestions:
                scoped_report = sequencing_report.model_copy(update={"suggestions": scoped_suggestions})
                applied = self.route_aware_sequencing_service.apply_report(
                    working_state,
                    scoped_report,
                    min_improvement_seconds=settings.route_aware_scheduling_min_improvement_seconds,
                )
                if applied:
                    working_state.route_feasibility_report = self.route_feasibility_service.build_report(working_state)

        working_state.travel_time_buffer_report = self.travel_time_buffer_service.build_report(working_state)

    # -- bounded repair (Task 28/29/31) ------------------------------------

    def _attempt_bounded_repair(
        self, working_state: PlanningState, plan: TargetedRegenerationPlan, hard_preserved_days: set[int]
    ) -> tuple[bool, str | None]:
        settings = get_settings()
        allowed_days = set(plan.affected_day_indices) - hard_preserved_days
        if not allowed_days:
            return False, None

        max_attempts = max(1, min(2, settings.ai_itinerary_repair_max_attempts))
        attempt_number = working_state.ai_itinerary_repair_attempt_count + 1
        if attempt_number > max_attempts:
            return False, None

        scoped_builder = _DayScopedRepairRequestBuilder(self.repair_request_builder, allowed_days)
        repair_service = AIItineraryRepairService(
            request_builder=scoped_builder, provider=self._repair_provider_override
        )

        try:
            repair_result = repair_service.repair(working_state, attempt_number=attempt_number)
        except Exception:
            return True, "failed"

        if repair_result.status == AIItineraryRepairStatus.SKIPPED:
            # Task 29: no repairable finding on an allowed day -- never
            # invoke the LLM merely because an edit happened.
            return False, repair_result.status.value

        working_state.ai_itinerary_repair_result = repair_result
        working_state.ai_itinerary_repair_attempt_count = attempt_number

        if repair_result.status != AIItineraryRepairStatus.COMPLETED:
            return True, repair_result.status.value

        repair_request = scoped_builder.build_request(working_state, attempt_number=attempt_number)
        if repair_request is None or working_state.ai_itinerary_reasoning_result is None:
            return True, repair_result.status.value

        try:
            merged = merge_repair_into_reasoning_result(
                working_state.ai_itinerary_reasoning_result, repair_request, repair_result
            )
        except ValueError:
            return True, "rejected"

        working_state.ai_itinerary_reasoning_result = merged
        self.experience_planner_service.run(working_state)
        return True, repair_result.status.value

    # -- final preservation audit (Task 33) --------------------------------

    @staticmethod
    def _final_preservation_audit(
        working_state: PlanningState,
        preserved_day_snapshot: dict[int, DailyPlan],
        preserved_experience_snapshot: dict[str, ExperienceItem],
        plan: TargetedRegenerationPlan,
    ) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        experience_plan = working_state.experience_plan
        if experience_plan is None:
            return False, ["No experience plan exists on the final working state."]

        daily_plans_by_number = {day.day_number: day for day in experience_plan.daily_plans}

        for day_number, snapshot_day in preserved_day_snapshot.items():
            current_day = daily_plans_by_number.get(day_number)
            if current_day is None or current_day.model_dump() != snapshot_day.model_dump():
                reasons.append(f"day {day_number} was not preserved exactly.")

        all_experiences = [experience for day in experience_plan.daily_plans for experience in day.experiences]
        experiences_by_id = {experience.experience_id: experience for experience in all_experiences}

        for experience_id, snapshot_experience in preserved_experience_snapshot.items():
            current = experiences_by_id.get(experience_id)
            if current is None or current.model_dump() != snapshot_experience.model_dump():
                reasons.append(f"experience {experience_id!r} was not preserved exactly.")

        seen_experience_ids: set[str] = set()
        seen_candidate_ids: set[str] = set()
        for experience in all_experiences:
            if experience.experience_id in seen_experience_ids:
                reasons.append(f"duplicate experience_id {experience.experience_id!r} in final plan.")
            seen_experience_ids.add(experience.experience_id)
            if experience.provider_source and experience.provider_place_id:
                candidate_id = build_candidate_id(experience.provider_source, experience.provider_place_id)
                if candidate_id in seen_candidate_ids:
                    reasons.append(f"duplicate candidate_id {candidate_id!r} in final plan.")
                seen_candidate_ids.add(candidate_id)

        for removal in plan.experience_removals:
            if removal.experience_id in seen_experience_ids:
                reasons.append(f"removed experience_id {removal.experience_id!r} is still present in the final plan.")

        for move in plan.experience_moves:
            count = sum(1 for experience in all_experiences if experience.experience_id == move.experience_id)
            if count != 1:
                reasons.append(
                    f"moved experience_id {move.experience_id!r} appears {count} time(s); expected exactly once."
                )

        return (len(reasons) == 0, reasons)


targeted_regeneration_executor = TargetedRegenerationExecutor()
