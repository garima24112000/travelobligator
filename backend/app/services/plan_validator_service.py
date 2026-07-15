from __future__ import annotations

from app.models.common import ReadinessStatus, ValidationSeverity
from app.models.planning_state import PlanningStage, PlanningState, ValidationIssue, ValidationReport
from app.services.base import PlanningStageService
from app.services.experience_planner_service import _matches_must_visit


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
    * If the trip request or traveler profile captured any constraints, a
      warning names them explicitly so the report never implies they were
      checked. This never blocks the plan by itself; constraints only add a
      warning, not a critical issue.
    * If a must-visit request does not match any provider-backed
      `candidate_pois` by name, a warning names it explicitly instead of
      silently dropping it or inventing a place to satisfy it. This never
      blocks the plan by itself; it only adds a warning.
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

        captured_constraints: list[str] = []
        for constraint in planning_state.trip_request.constraints:
            if constraint not in captured_constraints:
                captured_constraints.append(constraint)
        if planning_state.traveler_profile:
            for constraint in planning_state.traveler_profile.constraints:
                if constraint not in captured_constraints:
                    captured_constraints.append(constraint)

        if captured_constraints:
            constraint_list = ", ".join(captured_constraints)
            warnings.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    category="constraints",
                    message=(
                        f"The following constraint(s) were captured but are not fully "
                        f"validated yet: {constraint_list}. This plan does not confirm "
                        "that they are satisfied."
                    ),
                    affected_section="traveler_profile",
                    suggested_fix=(
                        "Implement constraint/feasibility validation against these "
                        "constraints before claiming they are satisfied."
                    ),
                )
            )

        must_visit_terms = (
            planning_state.traveler_profile.must_visit
            if planning_state.traveler_profile
            else planning_state.trip_request.must_visit
        )
        candidate_pois = (
            planning_state.destination_context.candidate_pois
            if planning_state.destination_context
            else []
        )
        unmatched_must_visit = [
            term
            for term in must_visit_terms
            if not any(_matches_must_visit(poi, [term.lower()]) for poi in candidate_pois)
        ]

        if unmatched_must_visit:
            unmatched_list = ", ".join(unmatched_must_visit)
            warnings.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    category="must_visit",
                    message=(
                        f"The following must-visit request(s) were not scheduled because "
                        f"they were not found in provider-backed attraction candidates: "
                        f"{unmatched_list}."
                    ),
                    affected_section="experience_plan",
                    suggested_fix=(
                        "Search for these must-visit places directly, or connect a places "
                        "provider with broader coverage."
                    ),
                )
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
