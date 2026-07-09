from __future__ import annotations

from app.models.common import ReadinessStatus, ValidationSeverity
from app.models.planning_state import PlanningStage, PlanningState, ValidationIssue, ValidationReport
from app.services.base import PlanningStageService


class PlanValidatorService(PlanningStageService):
    """Owns `validation_report` and `validation_cards`
    (docs/14_backend_architecture.md section 14).

    Runs deterministic checks first and does not modify the itinerary. For
    now the only deterministic fact available is whether the experience plan
    actually contains scheduled experiences. If it does not, the reason is
    distinguished honestly:

    * no provider-backed attraction candidates were available at all, or
    * candidates exist (`destination_context.candidate_pois`), but
      ExperiencePlannerService does not implement day-level scheduling yet.

    Either way the report is marked `blocked` rather than `ready`, since no
    itinerary can be presented, but the message should not blame provider
    coverage when the real gap is unimplemented scheduling.
    """

    def run(self, planning_state: PlanningState) -> PlanningState:
        planning_state.set_active_stage(PlanningStage.VALIDATION)

        critical_issues: list[ValidationIssue] = []
        provider_coverage_notes: list[str] = []
        unavailable_data_notes = [item.field for item in planning_state.unavailable_data]

        has_scheduled_experiences = bool(
            planning_state.experience_plan
            and any(day.experiences for day in planning_state.experience_plan.daily_plans)
        )
        candidate_pois_count = (
            len(planning_state.destination_context.candidate_pois)
            if planning_state.destination_context
            else 0
        )

        if not has_scheduled_experiences:
            if candidate_pois_count > 0:
                critical_issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.CRITICAL,
                        category="scheduling",
                        message=(
                            f"{candidate_pois_count} provider-backed attraction candidate(s) "
                            "are available, but day-level scheduling is not implemented yet, "
                            "so no experiences have been scheduled."
                        ),
                        affected_section="experience_plan",
                        suggested_fix="Implement day-level scheduling in ExperiencePlannerService.",
                    )
                )
                provider_coverage_notes.append(
                    "Places are available via OpenStreetMap; day-level scheduling of "
                    "those candidates is not implemented yet."
                )
            else:
                critical_issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.CRITICAL,
                        category="provider_coverage",
                        message=(
                            "No experiences have been scheduled because no provider-backed "
                            "attraction candidates are available."
                        ),
                        affected_section="experience_plan",
                        suggested_fix="Connect a places provider and regenerate the plan.",
                    )
                )
                provider_coverage_notes.append(
                    "No provider-backed attraction candidates are available for this "
                    "destination."
                )

        readiness_status = (
            ReadinessStatus.BLOCKED if critical_issues else ReadinessStatus.NEEDS_REVIEW
        )

        validation_report = ValidationReport(
            readiness_status=readiness_status,
            critical_issues=critical_issues,
            provider_coverage_notes=provider_coverage_notes,
            unavailable_data_notes=unavailable_data_notes,
        )

        planning_state.validation_report = validation_report
        planning_state.touch()
        return planning_state
