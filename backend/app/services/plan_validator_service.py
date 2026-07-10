from __future__ import annotations

from app.models.common import ReadinessStatus, ValidationSeverity
from app.models.planning_state import PlanningStage, PlanningState, ValidationIssue, ValidationReport
from app.services.base import PlanningStageService


class PlanValidatorService(PlanningStageService):
    """Owns `validation_report` and `validation_cards`
    (docs/14_backend_architecture.md section 14).

    Runs deterministic checks first and does not modify the itinerary.

    * If the experience plan has no scheduled experiences at all, the report
      is `blocked`. The message is distinguished honestly: either no
      provider-backed attraction candidates were available, or candidates
      exist but this plan has not scheduled them into days yet.
    * If experiences have been scheduled (ExperiencePlannerService's
      conservative day-level scheduling step), the plan is not blocked
      anymore. However, route ordering, timing, opening-hours, and
      feasibility checks are not implemented yet, so the report is
      `needs_review`, never `ready`.
    """

    def run(self, planning_state: PlanningState) -> PlanningState:
        planning_state.set_active_stage(PlanningStage.VALIDATION)

        critical_issues: list[ValidationIssue] = []
        warnings: list[ValidationIssue] = []
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

        if has_scheduled_experiences:
            warnings.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    category="feasibility",
                    message=(
                        "Attractions have been scheduled into days, but route ordering, "
                        "timing, opening-hours, and feasibility checks are not implemented "
                        "yet, so this plan needs review before it can be considered ready."
                    ),
                    affected_section="experience_plan",
                    suggested_fix=(
                        "Implement route/timing feasibility validation before marking "
                        "plans ready."
                    ),
                )
            )
        elif candidate_pois_count > 0:
            critical_issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.CRITICAL,
                    category="scheduling",
                    message=(
                        f"{candidate_pois_count} provider-backed attraction candidate(s) "
                        "are available, but no experiences have been scheduled for this "
                        "plan yet."
                    ),
                    affected_section="experience_plan",
                    suggested_fix="Run the experience planner stage to schedule candidate attractions.",
                )
            )
            provider_coverage_notes.append(
                "Places are available via OpenStreetMap; this plan has not scheduled "
                "them into days yet."
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
            warnings=warnings,
            provider_coverage_notes=provider_coverage_notes,
            unavailable_data_notes=unavailable_data_notes,
        )

        planning_state.validation_report = validation_report
        planning_state.touch()
        return planning_state
