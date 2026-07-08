from __future__ import annotations

from app.models.planning_state import PlanningState, VersionHistoryItem


class VersioningService:
    """Owns `version_history` and `metadata.current_version`
    (docs/14_backend_architecture.md section 17).

    A new version should be created when user-visible planning output
    changes: once after the first full generation, and again after any
    feedback-driven regeneration.
    """

    def create_initial_version(
        self,
        planning_state: PlanningState,
        changed_sections: list[str],
        summary: str = "Initial plan generated.",
    ) -> PlanningState:
        version_label = "v1"
        planning_state.version_history.append(
            VersionHistoryItem(
                version_label=version_label,
                created_by="initial_generation",
                summary=summary,
                changed_sections=changed_sections,
            )
        )
        planning_state.metadata.current_version = version_label
        planning_state.touch()
        return planning_state

    def create_version_after_feedback(
        self,
        planning_state: PlanningState,
        feedback_event_id: str,
        changed_sections: list[str],
        preserved_sections: list[str] | None = None,
        summary: str | None = None,
    ) -> PlanningState:
        next_index = len(planning_state.version_history) + 1
        version_label = f"v{next_index}"

        planning_state.version_history.append(
            VersionHistoryItem(
                version_label=version_label,
                created_by="user_feedback",
                summary=summary,
                changed_sections=changed_sections,
                preserved_sections=preserved_sections or [],
                feedback_event_id=feedback_event_id,
            )
        )
        planning_state.metadata.current_version = version_label
        planning_state.touch()
        return planning_state


versioning_service = VersioningService()
