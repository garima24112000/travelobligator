from __future__ import annotations

import logging
from dataclasses import dataclass

from app.models.ai_feedback_interpretation import (
    AdjustInterestAction,
    AIFeedbackInterpretationResult,
    AIFeedbackInterpretationStatus,
    ChangePaceAction,
    GeneralInstructionAction,
    MoveExperienceAction,
    RegenerateDayAction,
    RemoveExperienceAction,
)
from app.models.ai_itinerary_reasoning import build_candidate_id
from app.models.planning_state import PlanningStage, PlanningState, TripPace
from app.models.targeted_regeneration_plan import (
    TargetedDayInstruction,
    TargetedExperienceMove,
    TargetedExperienceRemoval,
    TargetedNewPlaceLookup,
    TargetedRegenerationPlan,
    TargetedRegenerationPlanStatus,
    TravelerProfileMutation,
)

logger = logging.getLogger(__name__)

# Section 197A (docs/14_backend_architecture.md, following section 147):
# the deterministic compiler that turns an already-validated Section 196
# `AIFeedbackInterpretationResult` into a `TargetedRegenerationPlan`.
#
# Core invariant (Task 2): Section 197 TRUSTS the structured intent
# Section 196 already produced. This builder never re-reads
# `feedback_text`, never re-infers scope from free text, and never
# re-runs an LLM (Task 25) -- it is pure policy/compilation over
# `planning_state`'s CURRENT, real structure and the interpretation's
# already-typed fields.
#
# Every experience-id/day-index reference is independently re-checked
# against the CURRENT `planning_state` here (Task 16/34) -- never
# trusted just because Section 196's own validator already passed it
# against whatever state existed when the interpretation was built.
# This is what gives Section 197A its own version-staleness defense: if
# an id genuinely no longer exists in the current plan, that is detected
# here directly by lookup, without needing a separately tracked "version
# the interpretation was built against" (Task 35).

_STAGE_ORDER = (
    PlanningStage.TRAVELER_PROFILE,
    PlanningStage.DESTINATION_CONTEXT,
    PlanningStage.TRIP_STRATEGY,
    PlanningStage.STAY_TRANSPORT,
    PlanningStage.EXPERIENCE_PLAN,
    PlanningStage.VALIDATION,
)


def _ordered_stages(stages: set[PlanningStage]) -> list[PlanningStage]:
    return [stage for stage in _STAGE_ORDER if stage in stages]


@dataclass
class _CurrentItem:
    experience_id: str
    day_index: int
    candidate_id: str | None
    coordinates: object | None


def _index_current_items(planning_state: PlanningState) -> dict[str, _CurrentItem]:
    index: dict[str, _CurrentItem] = {}
    experience_plan = planning_state.experience_plan
    if experience_plan is None:
        return index
    for daily_plan in experience_plan.daily_plans:
        for experience in daily_plan.experiences:
            candidate_id = None
            if experience.provider_source and experience.provider_place_id:
                candidate_id = build_candidate_id(experience.provider_source, experience.provider_place_id)
            index[experience.experience_id] = _CurrentItem(
                experience_id=experience.experience_id,
                day_index=daily_plan.day_number,
                candidate_id=candidate_id,
                coordinates=experience.coordinates,
            )
    return index


def _blocked(trip_id: str, reasons: list[str], *, scope: object | None = None) -> TargetedRegenerationPlan:
    return TargetedRegenerationPlan(
        trip_id=trip_id,
        interpretation_scope=scope,
        status=TargetedRegenerationPlanStatus.BLOCKED,
        block_reasons=reasons,
    )


def _unsupported(trip_id: str, reasons: list[str], *, scope: object | None = None) -> TargetedRegenerationPlan:
    return TargetedRegenerationPlan(
        trip_id=trip_id,
        interpretation_scope=scope,
        status=TargetedRegenerationPlanStatus.UNSUPPORTED,
        unsupported_actions=reasons,
    )


class TargetedRegenerationPlanBuilder:
    """Pure, read-only compiler (Task 24). `build_plan` never mutates
    `planning_state`, never calls a provider, never calls an LLM, never
    persists anything -- the only public method on this class.
    """

    def build_plan(
        self, planning_state: PlanningState, interpretation: AIFeedbackInterpretationResult
    ) -> TargetedRegenerationPlan:
        plan = self._compile(planning_state, interpretation)
        # Task 39: safe fields only -- plain counts/booleans/enum-value
        # strings, never the raw feedback text, an experience name, a day
        # instruction, or a credential.
        logger.info(
            "TargetedRegenerationPlanBuilder.build_plan compiled.",
            extra={
                "stage": "targeted_regeneration_plan",
                "status": plan.status.value,
                "scope": plan.interpretation_scope.value if plan.interpretation_scope else None,
                "affected_day_count": len(plan.affected_day_indices),
                "affected_experience_count": len(plan.affected_experience_ids),
                "preserved_day_count": len(plan.preserved_day_indices),
                "provider_lookup_required": plan.requires_provider_discovery,
                "reasoning_required": plan.requires_ai_itinerary_reasoning,
                "required_stage_count": len(plan.required_stages),
            },
        )
        return plan

    def _compile(
        self, planning_state: PlanningState, interpretation: AIFeedbackInterpretationResult
    ) -> TargetedRegenerationPlan:
        trip_id = planning_state.trip_id
        source_version = planning_state.metadata.current_version

        if interpretation.status == AIFeedbackInterpretationStatus.NOT_CONNECTED:
            return TargetedRegenerationPlan(
                trip_id=trip_id,
                source_version=source_version,
                status=TargetedRegenerationPlanStatus.BLOCKED,
                block_reasons=["No AI feedback interpretation is available (not_connected)."],
            )

        if interpretation.status == AIFeedbackInterpretationStatus.REJECTED:
            return TargetedRegenerationPlan(
                trip_id=trip_id,
                source_version=source_version,
                status=TargetedRegenerationPlanStatus.BLOCKED,
                block_reasons=list(interpretation.blocked_reasons)
                or ["The feedback interpretation was rejected."],
            )

        if interpretation.status == AIFeedbackInterpretationStatus.NEEDS_CLARIFICATION:
            clarification = interpretation.clarification
            return TargetedRegenerationPlan(
                trip_id=trip_id,
                source_version=source_version,
                interpretation_scope=interpretation.scope,
                status=TargetedRegenerationPlanStatus.NEEDS_CLARIFICATION,
                clarification_reason=clarification.reason if clarification else None,
                clarification_possible_experience_ids=(
                    list(clarification.possible_experience_ids) if clarification else []
                ),
            )

        # Task 12: existing trip locks currently block regeneration
        # GLOBALLY (bookkeeping-only locks, no stage service reads them
        # selectively) -- 197A must not weaken that. Any active lock
        # blocks a targeted plan outright, exactly like `/regenerate`
        # today, regardless of what the interpretation asked for.
        active_lock_count = sum(1 for lock in planning_state.user_locks if lock.is_active)
        if active_lock_count > 0:
            return TargetedRegenerationPlan(
                trip_id=trip_id,
                source_version=source_version,
                interpretation_scope=interpretation.scope,
                status=TargetedRegenerationPlanStatus.BLOCKED,
                block_reasons=[
                    f"{active_lock_count} active lock(s) block all regeneration -- "
                    "existing global lock behavior is preserved, not worked around."
                ],
            )

        if planning_state.experience_plan is None:
            return TargetedRegenerationPlan(
                trip_id=trip_id,
                source_version=source_version,
                interpretation_scope=interpretation.scope,
                status=TargetedRegenerationPlanStatus.BLOCKED,
                block_reasons=["No experience plan exists yet to compile a targeted plan against."],
            )

        current_items = _index_current_items(planning_state)
        current_day_count = len(planning_state.experience_plan.daily_plans)

        # Task 23: GeneralInstructionAction has no structural day/scope
        # field of its own -- there is nothing deterministic for this
        # compiler to target, regardless of the interpretation's
        # top-level `scope`. Marking the whole plan unsupported is the
        # honest choice over guessing a stage mapping from free text.
        if any(isinstance(action, GeneralInstructionAction) for action in interpretation.actions):
            return _unsupported(
                trip_id,
                ["general_instruction has no structural day/stage target -- cannot be compiled deterministically."],
                scope=interpretation.scope,
            )

        stale_reasons: list[str] = []
        for action in interpretation.actions:
            if isinstance(action, RemoveExperienceAction) and action.experience_id not in current_items:
                stale_reasons.append(
                    f"experience_id {action.experience_id!r} no longer exists in the current plan "
                    "(stale reference -- the interpretation may have been built against an older version)."
                )
            elif isinstance(action, MoveExperienceAction) and action.experience_id not in current_items:
                stale_reasons.append(
                    f"experience_id {action.experience_id!r} no longer exists in the current plan "
                    "(stale reference -- the interpretation may have been built against an older version)."
                )
        for experience_id in interpretation.preserve.experience_ids:
            if experience_id not in current_items:
                stale_reasons.append(
                    f"preserved experience_id {experience_id!r} no longer exists in the current plan."
                )
        if stale_reasons:
            return _blocked(trip_id, stale_reasons, scope=interpretation.scope)

        out_of_range_reasons: list[str] = []
        for action in interpretation.actions:
            if isinstance(action, MoveExperienceAction) and action.target_day_index > current_day_count:
                out_of_range_reasons.append(
                    f"move_experience target_day_index {action.target_day_index} exceeds the current "
                    f"trip length ({current_day_count} day(s))."
                )
            elif isinstance(action, RegenerateDayAction) and action.day_index > current_day_count:
                out_of_range_reasons.append(
                    f"regenerate_day day_index {action.day_index} exceeds the current trip length "
                    f"({current_day_count} day(s))."
                )
        for day_index in interpretation.preserve.day_indices:
            if day_index > current_day_count:
                out_of_range_reasons.append(
                    f"preserve.day_indices contains {day_index}, which exceeds the current trip length "
                    f"({current_day_count} day(s))."
                )
        if out_of_range_reasons:
            return _blocked(trip_id, out_of_range_reasons, scope=interpretation.scope)

        removals: list[TargetedExperienceRemoval] = []
        moves: list[TargetedExperienceMove] = []
        day_instructions: list[TargetedDayInstruction] = []
        profile_mutation = TravelerProfileMutation()
        has_profile_mutation = False
        new_place_lookups: list[TargetedNewPlaceLookup] = []

        affected_days: set[int] = set()
        affected_experience_ids: set[str] = set()
        removed_ids: set[str] = set()
        moved_ids: set[str] = set()
        regenerated_days: set[int] = set()

        required_stages: set[PlanningStage] = set()
        requires_ai_itinerary_reasoning = False

        for action in interpretation.actions:
            if isinstance(action, RemoveExperienceAction):
                item = current_items[action.experience_id]
                removals.append(
                    TargetedExperienceRemoval(
                        experience_id=item.experience_id,
                        day_index=item.day_index,
                        candidate_id=item.candidate_id,
                    )
                )
                affected_days.add(item.day_index)
                affected_experience_ids.add(item.experience_id)
                removed_ids.add(item.experience_id)
                required_stages.update({PlanningStage.EXPERIENCE_PLAN, PlanningStage.VALIDATION})

            elif isinstance(action, MoveExperienceAction):
                item = current_items[action.experience_id]
                moves.append(
                    TargetedExperienceMove(
                        experience_id=item.experience_id,
                        source_day_index=item.day_index,
                        target_day_index=action.target_day_index,
                        candidate_id=item.candidate_id,
                        coordinates=item.coordinates,
                    )
                )
                affected_days.update({item.day_index, action.target_day_index})
                affected_experience_ids.add(item.experience_id)
                moved_ids.add(item.experience_id)
                required_stages.update({PlanningStage.EXPERIENCE_PLAN, PlanningStage.VALIDATION})

            elif isinstance(action, RegenerateDayAction):
                day_instructions.append(
                    TargetedDayInstruction(day_index=action.day_index, instruction=action.instruction)
                )
                affected_days.add(action.day_index)
                regenerated_days.add(action.day_index)
                required_stages.update({PlanningStage.EXPERIENCE_PLAN, PlanningStage.VALIDATION})
                requires_ai_itinerary_reasoning = True

            elif isinstance(action, ChangePaceAction):
                profile_mutation.pace = TripPace(action.pace)
                has_profile_mutation = True
                affected_days.update(range(1, current_day_count + 1))
                required_stages.update(
                    {PlanningStage.TRAVELER_PROFILE, PlanningStage.EXPERIENCE_PLAN, PlanningStage.VALIDATION}
                )
                requires_ai_itinerary_reasoning = True

            elif isinstance(action, AdjustInterestAction):
                if action.direction == "more":
                    profile_mutation.interests_to_add.append(action.category)
                else:
                    profile_mutation.interests_to_remove.append(action.category)
                has_profile_mutation = True
                affected_days.update(range(1, current_day_count + 1))
                required_stages.update(
                    {
                        PlanningStage.TRAVELER_PROFILE,
                        PlanningStage.DESTINATION_CONTEXT,
                        PlanningStage.EXPERIENCE_PLAN,
                        PlanningStage.VALIDATION,
                    }
                )
                requires_ai_itinerary_reasoning = True

        for new_place_request in interpretation.new_place_requests:
            new_place_lookups.append(
                TargetedNewPlaceLookup(query=new_place_request.query, note=new_place_request.note)
            )
            required_stages.update({PlanningStage.EXPERIENCE_PLAN, PlanningStage.VALIDATION})
            requires_ai_itinerary_reasoning = True

        # Task 13: defend the execution boundary even though Section 196
        # already checks its own output for these exact contradictions --
        # 197A independently re-derives affected/removed/moved sets from
        # the CURRENT state, so it re-checks them here too rather than
        # trusting Section 196's internal consistency check was run
        # against the same state.
        contradictions: list[str] = []
        for experience_id in removed_ids & moved_ids:
            contradictions.append(f"experience_id {experience_id!r} is both removed and moved.")
        for experience_id in set(interpretation.preserve.experience_ids) & affected_experience_ids:
            contradictions.append(
                f"experience_id {experience_id!r} is both preserved and targeted by another action."
            )
        for day_index in set(interpretation.preserve.day_indices) & regenerated_days:
            contradictions.append(f"day_index {day_index} is both preserved and targeted for regeneration.")
        for move in moves:
            if move.target_day_index in set(interpretation.preserve.day_indices):
                contradictions.append(
                    f"day_index {move.target_day_index} is preserved but is the move target for "
                    f"experience_id {move.experience_id!r}."
                )
        if contradictions:
            return _blocked(trip_id, contradictions, scope=interpretation.scope)

        if not removals and not moves and not day_instructions and not has_profile_mutation and not new_place_lookups:
            return _unsupported(
                trip_id,
                ["The interpretation carries only a preserve constraint -- no executable action to compile."],
                scope=interpretation.scope,
            )

        preserved_days = sorted(
            (set(range(1, current_day_count + 1)) - affected_days) | set(interpretation.preserve.day_indices)
        )
        preserved_experience_ids = sorted(
            set(interpretation.preserve.experience_ids) - affected_experience_ids
        )

        deterministic_edit_possible = not requires_ai_itinerary_reasoning and bool(removals or moves)

        return TargetedRegenerationPlan(
            trip_id=trip_id,
            source_version=source_version,
            interpretation_scope=interpretation.scope,
            status=TargetedRegenerationPlanStatus.READY,
            affected_day_indices=sorted(affected_days),
            preserved_day_indices=preserved_days,
            affected_experience_ids=sorted(affected_experience_ids),
            preserved_experience_ids=preserved_experience_ids,
            experience_removals=removals,
            experience_moves=moves,
            day_instructions=day_instructions,
            traveler_profile_mutation=profile_mutation if has_profile_mutation else None,
            new_place_lookups=new_place_lookups,
            required_stages=_ordered_stages(required_stages),
            requires_ai_itinerary_reasoning=requires_ai_itinerary_reasoning,
            deterministic_edit_possible=deterministic_edit_possible,
            requires_provider_discovery=bool(new_place_lookups),
            requires_routing_rerun=True,
            requires_validation_rerun=True,
            requires_narrator_rerun=True,
            would_create_version=True,
        )


targeted_regeneration_plan_builder = TargetedRegenerationPlanBuilder()
