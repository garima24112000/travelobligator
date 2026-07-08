from __future__ import annotations

from app.models.common import ReadinessStatus, ValidationSeverity
from app.models.planning_state import PlanningStage, PlanningState, ValidationIssue, ValidationReport
from app.services.base import PlanningStageService


class PlanValidatorService(PlanningStageService):
    """Owns `validation_report` and `validation_cards`
    (docs/14_backend_architecture.md section 14).

    Runs deterministic checks first and does not modify the itinerary. For
    now the only deterministic fact available is whether the experience plan
    actually contains scheduled experiences; if it does not (because
    provider data was unavailable upstream), the report is honestly marked
    `blocked` rather than `ready`.
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

        if not has_scheduled_experiences:
            critical_issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.CRITICAL,
                    category="provider_coverage",
                    message=(
                        "No experiences have been scheduled because provider data "
                        "for attractions is not available."
                    ),
                    affected_section="experience_plan",
                    suggested_fix="Connect a places provider and regenerate the plan.",
                )
            )
            provider_coverage_notes.append(
                "Places, routes, and accommodation providers are not connected."
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
