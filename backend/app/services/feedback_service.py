from __future__ import annotations

from app.models.common import RegenerationStrategy
from app.models.planning_state import FeedbackEvent, PlanningState


class FeedbackService:
    """Owns `feedback_history`, the affected-stages decision, the
    regeneration-strategy decision, and `change_summary`
    (docs/14_backend_architecture.md section 15).

    Classifying feedback text into a feedback type and affected stages
    requires an AIReasoningProvider. Until one is connected, feedback is
    recorded honestly as `explanation_only` (no section is regenerated)
    rather than guessing which stages to rerun.
    """

    def apply_feedback(self, planning_state: PlanningState, feedback_text: str) -> PlanningState:
        feedback_event = FeedbackEvent(
            feedback_text=feedback_text,
            affected_stages=[],
            regeneration_strategy=RegenerationStrategy.EXPLANATION_ONLY,
            change_summary={},
            follow_up_question=(
                "Feedback interpretation is not yet connected to an AI reasoning "
                "provider, so no plan sections were regenerated."
            ),
        )

        planning_state.feedback_history.append(feedback_event)
        planning_state.touch()
        return planning_state


feedback_service = FeedbackService()
