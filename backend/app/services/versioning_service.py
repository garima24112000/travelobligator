from __future__ import annotations

from app.models.planning_state import PlanningState, VersionHistoryItem

# Sections that may legitimately be absent (None) if a stage failed to
# populate them; checked defensively so an initial version never claims a
# section changed when the pipeline never actually produced it.
_INITIAL_VERSION_OPTIONAL_SECTIONS = (
    "traveler_profile",
    "destination_context",
    "trip_strategy",
    "stay_transport",
    "experience_plan",
    "validation_report",
)

_INITIAL_VERSION_SUMMARY = (
    "Initial generated plan version, recorded automatically after the "
    "first successful plan generation. This is a record of which pipeline "
    "sections were produced, not a snapshot of their content, and it does "
    "not guarantee anything will be preserved in a future regeneration."
)


class VersioningService:
    """Owns `version_history` and `metadata.current_version`
    (docs/14_backend_architecture.md section 17).

    A new version should be created when user-visible planning output
    changes: once after the first full generation, and again after any
    feedback-driven regeneration. No plan snapshot is stored yet -- each
    `VersionHistoryItem` only records which sections were produced/changed,
    not their content, since real diffing/regeneration is not implemented.
    """

    def create_initial_version(self, planning_state: PlanningState) -> PlanningState:
        """Records the "v1" version item for a trip's first successful
        generation. Idempotent: if an initial version already exists (e.g.
        `POST /trips/{trip_id}/generate` is called again for the same
        trip), this is a no-op rather than appending a duplicate v1 entry.
        """
        if planning_state.version_history:
            return planning_state

        changed_sections = [
            section
            for section in _INITIAL_VERSION_OPTIONAL_SECTIONS
            if getattr(planning_state, section) is not None
        ]
        # provider_coverage is never None (default_factory=ProviderCoverage),
        # so it is always recorded as produced rather than filtered above.
        changed_sections.append("provider_coverage")

        planning_state.version_history.append(
            VersionHistoryItem(
                version_label="v1",
                created_by="system_generation",
                summary=_INITIAL_VERSION_SUMMARY,
                changed_sections=changed_sections,
                preserved_sections=[],
                feedback_event_id=None,
            )
        )
        planning_state.metadata.current_version = "v1"
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
