from __future__ import annotations

from app.models.common import RegenerationStrategy
from app.models.planning_state import FeedbackEvent, PlanningStage, PlanningState

# Stage order used to build `affected_stages`, matching PlanningStage's
# declaration order (docs/09_planning_state.md). PlanningStage.FEEDBACK and
# PlanningStage.CREATE_TRIP are never produced by classification below.
_STAGE_ORDER: tuple[PlanningStage, ...] = (
    PlanningStage.TRAVELER_PROFILE,
    PlanningStage.DESTINATION_CONTEXT,
    PlanningStage.TRIP_STRATEGY,
    PlanningStage.STAY_TRANSPORT,
    PlanningStage.EXPERIENCE_PLAN,
    PlanningStage.VALIDATION,
)

# Deterministic keyword -> (feedback_type, affected_stages) rules. Purely
# substring matching on lowercased feedback text -- no AI, no guessing.
_FEEDBACK_TYPE_RULES: dict[str, dict[str, tuple]] = {
    "pace_change": {
        "keywords": ("less packed", "slower", "relaxed", "too much", "too many"),
        "affected_stages": (PlanningStage.EXPERIENCE_PLAN, PlanningStage.VALIDATION),
    },
    "interest_change": {
        "keywords": (
            "more museums",
            "museum",
            "art",
            "history",
            "food",
            "beach",
            "shopping",
            "nightlife",
        ),
        "affected_stages": (
            PlanningStage.TRAVELER_PROFILE,
            PlanningStage.DESTINATION_CONTEXT,
            PlanningStage.EXPERIENCE_PLAN,
            PlanningStage.VALIDATION,
        ),
    },
    "remove_or_avoid": {
        "keywords": ("remove", "skip", "avoid"),
        "affected_stages": (
            PlanningStage.TRAVELER_PROFILE,
            PlanningStage.EXPERIENCE_PLAN,
            PlanningStage.VALIDATION,
        ),
    },
    "restaurant_preference": {
        "keywords": ("restaurant", "food", "eat", "lunch", "dinner", "cafe"),
        "affected_stages": (
            PlanningStage.DESTINATION_CONTEXT,
            PlanningStage.EXPERIENCE_PLAN,
            PlanningStage.VALIDATION,
        ),
    },
    "stay_preference": {
        "keywords": ("hotel", "stay", "area", "neighborhood", "accommodation"),
        "affected_stages": (
            PlanningStage.STAY_TRANSPORT,
            PlanningStage.EXPERIENCE_PLAN,
            PlanningStage.VALIDATION,
        ),
    },
    "transport_preference": {
        "keywords": (
            "walk",
            "walking",
            "transit",
            "train",
            "bus",
            "uber",
            "taxi",
            "drive",
        ),
        "affected_stages": (
            PlanningStage.STAY_TRANSPORT,
            PlanningStage.EXPERIENCE_PLAN,
            PlanningStage.VALIDATION,
        ),
    },
}

# Stable priority order used to pick a single primary feedback_type when
# more than one category's keywords match the same feedback text.
_PRIMARY_TYPE_PRIORITY: tuple[str, ...] = (
    "remove_or_avoid",
    "pace_change",
    "stay_preference",
    "transport_preference",
    "restaurant_preference",
    "interest_change",
    "general_feedback",
)

_GENERAL_FEEDBACK_SUMMARY = (
    "This feedback is captured but not classified into a specific planning "
    "stage yet."
)

_INTERPRETATION_NOTE = (
    "This is a preliminary label only. No plan sections were regenerated."
)

# Deterministic, honest "what would likely need to change" preview per
# feedback_type -- describes future regeneration work, never something this
# endpoint performs itself. No plan section is touched by building this.
_LIKELY_CHANGES_BY_TYPE: dict[str, tuple[str, ...]] = {
    "pace_change": (
        "Adjust daily pacing or number of scheduled experiences.",
        "Re-run experience planning before changing the itinerary.",
        "Re-run validation after any future itinerary change.",
    ),
    "interest_change": (
        "Update traveler interests.",
        "Re-check destination candidate places against the updated interests.",
        "Re-run experience planning before changing the itinerary.",
        "Re-run validation after any future itinerary change.",
    ),
    "remove_or_avoid": (
        "Record the requested removal or avoidance preference.",
        "Re-run experience planning before removing or replacing scheduled places.",
        "Re-run validation after any future itinerary change.",
    ),
    "restaurant_preference": (
        "Update restaurant preference handling.",
        "Re-check restaurant candidates if provider data is available.",
        "Re-run experience planning before changing restaurant suggestions.",
    ),
    "stay_preference": (
        "Update stay-area or accommodation preference handling.",
        "Re-check stay-area guidance and accommodation POI candidates.",
        "Re-run stay/transport reasoning before changing stay guidance.",
    ),
    "transport_preference": (
        "Update transport preference handling.",
        "Re-check stay/transport reasoning.",
        "Connect a real route provider before validating route time or walking feasibility.",
    ),
    "general_feedback": (
        "Review this feedback manually before deciding which planning stages to rerun.",
    ),
}

# Honestly lists every plan section the feedback capture endpoint leaves
# untouched, regardless of feedback_type.
_UNCHANGED_SECTIONS: tuple[str, ...] = (
    "traveler_profile",
    "destination_context",
    "trip_strategy",
    "stay_transport",
    "experience_plan",
    "validation_report",
    "provider_coverage",
    "route_feasibility_context",
)

# Honest blockers preventing this preview from ever becoming a real
# regeneration today.
_BLOCKED_BY: tuple[str, ...] = (
    "Feedback regeneration is not implemented yet.",
    "No AI interpretation provider is connected.",
    "No plan sections are modified by the feedback capture endpoint.",
)


class FeedbackService:
    """Owns `feedback_history`, the affected-stages decision, the
    regeneration-strategy decision, and `change_summary`
    (docs/14_backend_architecture.md section 15).

    Classifying feedback text into a `feedback_type`/`affected_stages` is
    currently deterministic keyword matching only (see
    `_FEEDBACK_TYPE_RULES`) -- a preliminary label, not an AI interpretation
    and not something applied to the plan. Real interpretation (and any
    regeneration) requires an AIReasoningProvider; until one is connected,
    feedback is recorded honestly as `explanation_only` (no section is
    regenerated).
    """

    def apply_feedback(self, planning_state: PlanningState, feedback_text: str) -> PlanningState:
        feedback_type, affected_stages, matched_labels = self._classify(feedback_text)

        summary = (
            _GENERAL_FEEDBACK_SUMMARY
            if feedback_type == "general_feedback"
            else f"Feedback text matched deterministic rule-based keywords for '{feedback_type}'."
        )

        change_preview = {
            "preview_status": "not_applied",
            "would_require_regeneration": feedback_type != "general_feedback",
            "likely_changes": list(_LIKELY_CHANGES_BY_TYPE.get(feedback_type, ())),
            "unchanged_sections": list(_UNCHANGED_SECTIONS),
            "blocked_by": list(_BLOCKED_BY),
        }

        feedback_event = FeedbackEvent(
            feedback_text=feedback_text,
            feedback_type=feedback_type,
            affected_stages=affected_stages,
            regeneration_strategy=RegenerationStrategy.EXPLANATION_ONLY,
            interpretation={
                "method": "deterministic_rule_based",
                "applied_to_plan": False,
                "summary": summary,
                "matched_labels": matched_labels,
                "note": _INTERPRETATION_NOTE,
                "change_preview": change_preview,
            },
            handling_status="captured",
            change_summary={},
            follow_up_question=(
                "Feedback classification is rule-based only and not yet "
                "connected to an AI reasoning provider, so no plan sections "
                "were regenerated."
            ),
        )

        planning_state.feedback_history.append(feedback_event)
        planning_state.touch()
        return planning_state

    @staticmethod
    def _classify(feedback_text: str) -> tuple[str, list[PlanningStage], list[str]]:
        lowered = feedback_text.lower()

        matched_types: set[str] = set()
        stage_set: set[PlanningStage] = set()

        for candidate_type, rule in _FEEDBACK_TYPE_RULES.items():
            if any(keyword in lowered for keyword in rule["keywords"]):
                matched_types.add(candidate_type)
                stage_set.update(rule["affected_stages"])

        if not matched_types:
            return "general_feedback", [], []

        primary_type = next(
            candidate for candidate in _PRIMARY_TYPE_PRIORITY if candidate in matched_types
        )
        affected_stages = [stage for stage in _STAGE_ORDER if stage in stage_set]
        matched_labels = [
            candidate for candidate in _PRIMARY_TYPE_PRIORITY if candidate in matched_types
        ]
        return primary_type, affected_stages, matched_labels


feedback_service = FeedbackService()
