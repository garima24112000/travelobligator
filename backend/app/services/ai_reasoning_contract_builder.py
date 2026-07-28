from __future__ import annotations

from app.models.ai_reasoning import AIReasoningRequest, AIReasoningTask, EvidenceRef, UnavailableConstraint
from app.models.planning_state import PlanningState


class AIReasoningContractBuilder:
    """Builds future `AIReasoningRequest` inputs from existing
    `PlanningState` data (Step 155B, docs/13_llm_reasoning_pipeline.md
    section 27, docs/14_backend_architecture.md section 25).

    This is a pure, read-only contract-preparation layer: it never calls an
    LLM, LangGraph, LangSmith, or any provider adapter, and it never
    mutates the `planning_state` passed in. It only ever produces typed
    section/field references (`EvidenceRef`) and typed missing-data
    constraints (`UnavailableConstraint`) -- never a free-form serialized
    dump of `PlanningState` and never a new travel fact (no place,
    restaurant, hotel, price, rating, opening hours, route time, review
    count, booking link, or safety score).
    """

    def unavailable_constraints_from_state(
        self, planning_state: PlanningState
    ) -> list[UnavailableConstraint]:
        """Combines `PlanningState.unavailable_data` and every
        `provider_status` entry's `unavailable_fields` into one
        deterministic, deduplicated list of `UnavailableConstraint`.

        Iterates `unavailable_data` first (already stable list order), then
        `provider_status` (a dict, so already insertion-ordered) -- this
        fixed iteration order plus the seen-key check below is what makes
        the result deterministic across repeated calls for the same state.
        """
        constraints: list[UnavailableConstraint] = []
        seen: set[tuple[str, str, str, str | None]] = set()

        for item in planning_state.unavailable_data:
            self._append_constraint(
                constraints,
                seen,
                field=item.field,
                reason=item.reason,
                data_status=item.data_status.value,
                source=item.source,
            )

        for entry in planning_state.provider_status.values():
            for field in entry.unavailable_fields:
                self._append_constraint(
                    constraints,
                    seen,
                    field=field,
                    reason=entry.error_message
                    or f"{entry.provider_name} data is unavailable.",
                    data_status=entry.data_status.value,
                    source=entry.provider_name,
                )

        return constraints

    @staticmethod
    def _append_constraint(
        constraints: list[UnavailableConstraint],
        seen: set[tuple[str, str, str, str | None]],
        *,
        field: str,
        reason: str,
        data_status: str,
        source: str | None,
    ) -> None:
        # Deduplication key exactly matches Step 155B's spec:
        # field + reason + data_status + source.
        key = (field, reason, data_status, source)
        if key in seen:
            return
        seen.add(key)
        constraints.append(
            UnavailableConstraint(
                field=field, reason=reason, data_status=data_status, source=source
            )
        )

    def evidence_refs_for_task(
        self, planning_state: PlanningState, task: AIReasoningTask
    ) -> list[EvidenceRef]:
        """Conservative, generic evidence references for `task`, built only
        from data that already exists on `planning_state`. Each ref is only
        added when its underlying section/field is actually populated --
        never claimed as "available" when it's missing, and never a
        fabricated place/price/rating/route/booking claim.
        """
        if task == AIReasoningTask.TRAVELER_PREFERENCE_INTERPRETATION:
            return self._evidence_for_traveler_preference_interpretation(planning_state)
        if task == AIReasoningTask.TRIP_STRATEGY_EXPLANATION:
            return self._evidence_for_trip_strategy_explanation(planning_state)
        if task == AIReasoningTask.STAY_AREA_EXPLANATION:
            return self._evidence_for_stay_area_explanation(planning_state)
        if task == AIReasoningTask.EXPERIENCE_PLAN_EXPLANATION:
            return self._evidence_for_experience_plan_explanation(planning_state)
        if task == AIReasoningTask.VALIDATION_EXPLANATION:
            return self._evidence_for_validation_explanation(planning_state)
        if task == AIReasoningTask.FEEDBACK_INTERPRETATION:
            return self._evidence_for_feedback_interpretation(planning_state)
        return []

    @staticmethod
    def _evidence_for_traveler_preference_interpretation(
        planning_state: PlanningState,
    ) -> list[EvidenceRef]:
        refs: list[EvidenceRef] = []
        trip_request = planning_state.trip_request

        if trip_request.free_text_preferences:
            refs.append(
                EvidenceRef(
                    source_section="trip_request",
                    source_field="free_text_preferences",
                    claim="Traveler free-text preferences are available.",
                )
            )
        if trip_request.interests:
            refs.append(
                EvidenceRef(
                    source_section="trip_request",
                    source_field="interests",
                    claim="Traveler interest inputs are available.",
                )
            )
        if trip_request.constraints:
            refs.append(
                EvidenceRef(
                    source_section="trip_request",
                    source_field="constraints",
                    claim="Traveler constraint inputs are available.",
                )
            )
        return refs

    @staticmethod
    def _evidence_for_trip_strategy_explanation(
        planning_state: PlanningState,
    ) -> list[EvidenceRef]:
        refs: list[EvidenceRef] = []

        if planning_state.traveler_profile is not None:
            refs.append(
                EvidenceRef(
                    source_section="traveler_profile",
                    source_field="pace",
                    claim="Traveler pace preference is available.",
                )
            )
        destination_context = planning_state.destination_context
        if destination_context is not None and destination_context.candidate_pois:
            refs.append(
                EvidenceRef(
                    source_section="destination_context",
                    source_field="candidate_pois",
                    claim="Provider-backed candidate attractions are available.",
                )
            )
        if planning_state.provider_coverage.places is not None:
            refs.append(
                EvidenceRef(
                    source_section="provider_coverage",
                    source_field="places",
                    claim="Provider coverage information for places is available.",
                )
            )
        return refs

    @staticmethod
    def _evidence_for_stay_area_explanation(
        planning_state: PlanningState,
    ) -> list[EvidenceRef]:
        refs: list[EvidenceRef] = []
        destination_context = planning_state.destination_context

        if destination_context is not None and destination_context.candidate_accommodation_pois:
            refs.append(
                EvidenceRef(
                    source_section="destination_context",
                    source_field="candidate_accommodation_pois",
                    claim="Provider-backed candidate accommodation POIs are available.",
                )
            )
        if planning_state.provider_coverage.accommodations is not None:
            refs.append(
                EvidenceRef(
                    source_section="provider_coverage",
                    source_field="accommodations",
                    claim="Provider coverage information for accommodations is available.",
                )
            )
        if any(item.field == "accommodation_options" for item in planning_state.unavailable_data):
            refs.append(
                EvidenceRef(
                    source_section="unavailable_data",
                    source_field="accommodation_options",
                    claim="Accommodation option data is marked unavailable.",
                )
            )
        return refs

    @staticmethod
    def _evidence_for_experience_plan_explanation(
        planning_state: PlanningState,
    ) -> list[EvidenceRef]:
        refs: list[EvidenceRef] = []
        experience_plan = planning_state.experience_plan

        if experience_plan is not None and experience_plan.daily_plans:
            refs.append(
                EvidenceRef(
                    source_section="experience_plan",
                    source_field="daily_plans",
                    claim="Scheduled daily plans are available.",
                )
            )
        destination_context = planning_state.destination_context
        if destination_context is not None and destination_context.candidate_pois:
            refs.append(
                EvidenceRef(
                    source_section="destination_context",
                    source_field="candidate_pois",
                    claim="Provider-backed candidate attractions are available.",
                )
            )
        if planning_state.provider_coverage.routes is not None:
            refs.append(
                EvidenceRef(
                    source_section="provider_coverage",
                    source_field="routes",
                    claim="Provider coverage information for routes is available.",
                )
            )
        return refs

    @staticmethod
    def _evidence_for_validation_explanation(
        planning_state: PlanningState,
    ) -> list[EvidenceRef]:
        refs: list[EvidenceRef] = []
        validation_report = planning_state.validation_report
        if validation_report is None:
            return refs

        refs.append(
            EvidenceRef(
                source_section="validation_report",
                source_field="readiness_status",
                claim="A validation readiness status is available.",
            )
        )
        if validation_report.critical_issues:
            refs.append(
                EvidenceRef(
                    source_section="validation_report",
                    source_field="critical_issues",
                    claim="Validation critical issues are available.",
                )
            )
        if validation_report.warnings:
            refs.append(
                EvidenceRef(
                    source_section="validation_report",
                    source_field="warnings",
                    claim="Validation warnings are available.",
                )
            )
        return refs

    @staticmethod
    def _evidence_for_feedback_interpretation(
        planning_state: PlanningState,
    ) -> list[EvidenceRef]:
        refs: list[EvidenceRef] = []
        feedback_history = planning_state.feedback_history

        if feedback_history:
            latest = max(feedback_history, key=lambda event: event.created_at)
            refs.append(
                EvidenceRef(
                    source_section="feedback_history",
                    source_field="feedback_text",
                    source_id=latest.feedback_event_id,
                    claim="The latest captured feedback text is available.",
                )
            )
            refs.append(
                EvidenceRef(
                    source_section="pending_feedback_summary",
                    source_field="total_feedback_items",
                    claim="A rollup of total captured feedback items is available.",
                )
            )
        if any(lock.is_active for lock in planning_state.user_locks):
            refs.append(
                EvidenceRef(
                    source_section="user_locks",
                    source_field="active_locks",
                    claim="Active user locks exist for this trip.",
                )
            )
        return refs

    def build_request(
        self,
        planning_state: PlanningState,
        task: AIReasoningTask,
        input_sections: list[str],
        user_prompt: str | None = None,
    ) -> AIReasoningRequest:
        """Builds a typed `AIReasoningRequest` for `task` from
        `planning_state`. `input_sections` is caller-provided and validated
        by `AIReasoningRequest` itself (must be non-empty) -- this method
        never invents or auto-expands the section list, and never
        serializes `planning_state` into prompt text. `planning_state` is
        only ever read here, never mutated.
        """
        return AIReasoningRequest(
            task=task,
            trip_id=planning_state.trip_id,
            input_sections=input_sections,
            user_prompt=user_prompt,
            unavailable_constraints=self.unavailable_constraints_from_state(planning_state),
            evidence_refs=self.evidence_refs_for_task(planning_state, task),
        )


ai_reasoning_contract_builder = AIReasoningContractBuilder()
