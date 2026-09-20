from __future__ import annotations

from app.models.ai_feedback_interpretation import (
    AIFeedbackInterpretationRequest,
    AIFeedbackItineraryItemContext,
    AIFeedbackLockContext,
)
from app.models.ai_itinerary_reasoning import TravelerContextSummary, build_candidate_id
from app.models.planning_state import PlanningState

# Section 196 (docs/14_backend_architecture.md, following section 146):
# strict, read-only input builder for the AI feedback interpreter.
# `build_request` only ever reads `planning_state` -- it never calls a
# provider/LLM/network service, never mutates `planning_state`, and never
# performs new provider discovery. Mirrors
# `ItineraryNarrativeRequestBuilder`/`AIItineraryRepairRequestBuilder`'s
# own "pure read of already-computed PlanningState fields" convention
# exactly.
#
# `current_items` is built from `planning_state.experience_plan` --
# whatever is CURRENT at call time, which (for the live LangGraph
# `/generate` path) is already the final, post-repair plan, exactly like
# the itinerary narrator's own request builder. `candidate_id` is
# included only when the real `ExperienceItem` actually carries provider
# identity (`provider_place_id`/`provider_source` both set -- only ever
# true for an AI-directed promoted candidate today; a plain broad-pool
# item's `candidate_id` stays `None`, never guessed).


class AIFeedbackInterpretationRequestBuilder:
    def build_request(self, planning_state: PlanningState, feedback_text: str) -> AIFeedbackInterpretationRequest:
        trip_request = planning_state.trip_request
        traveler_profile = planning_state.traveler_profile

        current_items: list[AIFeedbackItineraryItemContext] = []
        if planning_state.experience_plan is not None:
            for day_plan in planning_state.experience_plan.daily_plans:
                for experience in day_plan.experiences:
                    candidate_id: str | None = None
                    if experience.provider_place_id and experience.provider_source:
                        candidate_id = build_candidate_id(
                            experience.provider_source, experience.provider_place_id
                        )
                    current_items.append(
                        AIFeedbackItineraryItemContext(
                            experience_id=experience.experience_id,
                            candidate_id=candidate_id,
                            name=experience.name,
                            category=experience.category,
                            day_index=day_plan.day_number,
                        )
                    )

        active_locks = [
            AIFeedbackLockContext(locked_item_type=lock.locked_item_type, locked_item_id=lock.locked_item_id)
            for lock in planning_state.user_locks
            if lock.is_active
        ]

        validation_status: str | None = None
        warning_count = 0
        critical_issue_count = 0
        if planning_state.validation_report is not None:
            validation_status = planning_state.validation_report.readiness_status.value
            warning_count = len(planning_state.validation_report.warnings)
            critical_issue_count = len(planning_state.validation_report.critical_issues)

        pace = traveler_profile.pace.value if traveler_profile else trip_request.pace.value
        interests = traveler_profile.interests if traveler_profile else trip_request.interests
        must_visit = traveler_profile.must_visit if traveler_profile else trip_request.must_visit
        must_avoid = traveler_profile.must_avoid if traveler_profile else trip_request.must_avoid
        constraints = traveler_profile.constraints if traveler_profile else trip_request.constraints

        traveler_context = TravelerContextSummary(
            travelers_count=trip_request.travelers_count,
            travel_group_type=trip_request.travel_group_type.value,
            pace=pace,
            interests=list(interests),
            must_visit=list(must_visit),
            must_avoid=list(must_avoid),
            constraints=list(constraints),
            budget_min=trip_request.budget_min,
            budget_max=trip_request.budget_max,
            budget_currency=trip_request.budget_currency,
        )

        trip_duration_days = max(1, (trip_request.end_date - trip_request.start_date).days + 1)

        return AIFeedbackInterpretationRequest(
            trip_id=planning_state.trip_id,
            current_version=planning_state.metadata.current_version,
            destination_name=trip_request.primary_destination,
            start_date=trip_request.start_date.isoformat(),
            end_date=trip_request.end_date.isoformat(),
            trip_duration_days=trip_duration_days,
            traveler_context=traveler_context,
            feedback_text=feedback_text,
            current_items=current_items,
            active_locks=active_locks,
            validation_status=validation_status,
            warning_count=warning_count,
            critical_issue_count=critical_issue_count,
        )


ai_feedback_interpretation_request_builder = AIFeedbackInterpretationRequestBuilder()
