from __future__ import annotations

import re

from pydantic import ValidationError

from app.models.ai_itinerary_reasoning import AIItineraryReasoningStatus
from app.models.ai_itinerary_repair import AIItineraryRepairIssue, AIItineraryRepairRequest, RepairableIssueType
from app.models.common import ProviderStatus, ValidationSeverity
from app.models.planning_state import PlanningState
from app.models.routing import BufferSufficiencyStatus, RouteFeasibilityStatus
from app.services.ai_itinerary_reasoning_request_builder import AIItineraryReasoningRequestBuilder

# Section 194A (docs/14_backend_architecture.md, following section 143):
# builds the bounded `AIItineraryRepairRequest` contract from existing
# `PlanningState` data -- pure, read-only, never calls a provider/LLM,
# never mutates `planning_state`, never performs new provider discovery.
#
# Task 1/2 audit result (see this module's `classify_repairable_issues`
# docstring and `RepairableIssueType`'s own docstring for the full
# reasoning): only three real, structurally-sourced findings are ever
# classified as repairable in this codebase today. Every other
# `ValidationIssue.category` this app produces
# (accommodation_inventory/budget/constraints/flight_inventory/holidays/
# hotel_ratings/movement_data/must_visit/provider_coverage/
# provider_coverage_consistency/regeneration/
# regeneration_state_consistency/route_aware_sequencing/route_geometry/
# scheduling/weather) is either a global/aggregate status report (not an
# actionable "this day/pair needs a different arrangement" instruction),
# a critical empty-plan/no-candidates condition that repair (editing an
# already-scheduled day) has no mechanism to address, or genuinely
# external/factual data this reasoning stage must never be asked to
# "fix" (Task 2: "do not ask the LLM to repair missing provider truth").
# None of the "day too crowded"/"duplicate candidate"/"pacing violation"
# examples the 194A spec names as possibly-repairable have any
# corresponding validator/report finding in this codebase --
# `ExperiencePlannerService` already prevents them at scheduling time, so
# there is nothing here to classify; they are deliberately not invented.
#
# Section 194B correction: the original 194A classification treated
# every `RouteLegFeasibility.feasibility_status == NEEDS_REVIEW` leg as
# repairable. Auditing `RouteFeasibilityService._feasibility_from_result`
# (Section 194B Task 1) showed that status is *also* produced whenever
# `leg.status` is `not_connected`/`unavailable`/etc -- i.e. "no routing
# provider is configured," this app's own documented default (see
# CLAUDE.md: `ROUTING_PROVIDER` default `not_connected`) -- which is not
# a detected problem, just an absence of data, and would otherwise fire
# on nearly every multi-stop day regardless of whether anything is
# actually wrong. `classify_repairable_issues` below now additionally
# requires `leg.status == ProviderStatus.FAILED` -- a routing call that
# was genuinely attempted and genuinely failed -- before treating a
# `NEEDS_REVIEW` leg as repairable.

_DAILY_PLAN_AFFECTED_SECTION_PATTERN = re.compile(r"^experience_plan\.daily_plans\[(\d+)\]$")


def _experience_day_index(planning_state: PlanningState) -> dict[str, int]:
    """Maps a real, already-scheduled `ExperienceItem.experience_id` to
    its real `DailyPlan.day_number` -- read directly off the day's own
    `day_number` field, never off the (denormalized, restatement-only)
    `ExperienceItem.day_number`, so this never trusts a value that could
    theoretically be stale relative to which `DailyPlan` an item actually
    lives in.
    """
    experience_plan = planning_state.experience_plan
    if experience_plan is None:
        return {}
    index: dict[str, int] = {}
    for day in experience_plan.daily_plans:
        for experience in day.experiences:
            index[experience.experience_id] = day.day_number
    return index


def classify_repairable_issues(
    planning_state: PlanningState, allowed_day_indices: set[int] | None = None
) -> list[AIItineraryRepairIssue]:
    """Task 2's deterministic repairable/non-repairable classification,
    implemented directly from real structured fields (Task 8: never from
    loose parsing of `ValidationIssue.message` prose):

    - `category="geographic_spread"` `ValidationIssue`s on
      `planning_state.validation_report`, whose `affected_section` names
      a specific day (`experience_plan.daily_plans[N]`) in the exact
      format `PlanValidatorService` itself always writes it in.
    - `RouteLegFeasibility` entries on
      `planning_state.route_feasibility_report.legs` with
      `feasibility_status == RouteFeasibilityStatus.NEEDS_REVIEW` AND
      `status == ProviderStatus.FAILED` (Section 194B correction -- see
      the module comment above: `NEEDS_REVIEW` alone also fires whenever
      no routing provider is connected, which is not a real problem),
      resolved to a day via the leg's own `from_experience_id`/
      `to_experience_id` (a route leg only ever connects two experiences
      already scheduled on the same day).
    - `TravelTimeBuffer` entries on
      `planning_state.travel_time_buffer_report.buffers` with
      `buffer_status == BufferSufficiencyStatus.INSUFFICIENT`, resolved
      to a day the same way.

    An issue whose affected day can't be resolved to a real, currently
    scheduled day (e.g. a stale/mismatched `affected_section`, or an
    experience_id no longer present) is silently skipped rather than
    guessed. Returns an empty list -- never `None` -- when nothing
    repairable is found; the caller (`AIItineraryRepairRequestBuilder`)
    is what decides that an empty list means "do not call the LLM."

    `allowed_day_indices` (Section 197B, Task 28): when given, an issue
    whose `day_index` is outside this set is excluded -- the smallest
    addition needed so a targeted regeneration's bounded repair can be
    constrained to only the days that execution actually touched,
    without ever repairing an issue that happens to also exist on a
    hard-preserved day. `None` (the default) preserves the original,
    whole-trip behavior used by initial generation's repair loop.
    """
    issues: list[AIItineraryRepairIssue] = []

    validation_report = planning_state.validation_report
    if validation_report is not None:
        all_validation_issues = (
            list(validation_report.critical_issues)
            + list(validation_report.warnings)
            + list(validation_report.suggestions)
        )
        for issue in all_validation_issues:
            if issue.category != "geographic_spread" or issue.affected_section is None:
                continue
            match = _DAILY_PLAN_AFFECTED_SECTION_PATTERN.match(issue.affected_section)
            if match is None:
                continue
            issues.append(
                AIItineraryRepairIssue(
                    issue_type=RepairableIssueType.GEOGRAPHIC_SPREAD,
                    day_index=int(match.group(1)),
                    source_category=issue.category,
                    message=issue.message,
                    severity=issue.severity,
                )
            )

    day_index_by_experience_id = _experience_day_index(planning_state)

    route_feasibility_report = planning_state.route_feasibility_report
    if route_feasibility_report is not None:
        for leg in route_feasibility_report.legs:
            # Section 194B correction (Task 1's audit surfaced this):
            # `feasibility_status == NEEDS_REVIEW` alone is NOT a real
            # problem signal -- `RouteFeasibilityService` also reports it
            # for `leg.status == NOT_CONNECTED`/`UNAVAILABLE`/etc, i.e.
            # "no routing provider is configured" (this app's own
            # documented default), which fires on essentially every
            # multi-stop day regardless of whether anything is actually
            # wrong. Only `leg.status == ProviderStatus.FAILED` -- a real
            # routing call that was actually attempted and genuinely
            # failed -- is a real, structurally-sourced repairable
            # signal; "we have no data" is never treated as "there is a
            # problem."
            if (
                leg.feasibility_status != RouteFeasibilityStatus.NEEDS_REVIEW
                or leg.status != ProviderStatus.FAILED
            ):
                continue
            day_index = day_index_by_experience_id.get(leg.from_experience_id)
            if day_index is None:
                continue
            issues.append(
                AIItineraryRepairIssue(
                    issue_type=RepairableIssueType.ROUTE_NEEDS_REVIEW,
                    day_index=day_index,
                    source_category="route_feasibility",
                    message=leg.message
                    or (
                        f"The route between {leg.from_experience_name!r} and "
                        f"{leg.to_experience_name!r} needs review."
                    ),
                    severity=ValidationSeverity.WARNING,
                )
            )

    travel_time_buffer_report = planning_state.travel_time_buffer_report
    if travel_time_buffer_report is not None:
        for buffer in travel_time_buffer_report.buffers:
            if buffer.buffer_status != BufferSufficiencyStatus.INSUFFICIENT:
                continue
            day_index = day_index_by_experience_id.get(buffer.from_experience_id)
            if day_index is None:
                continue
            issues.append(
                AIItineraryRepairIssue(
                    issue_type=RepairableIssueType.INSUFFICIENT_TRAVEL_BUFFER,
                    day_index=day_index,
                    source_category="travel_time_buffer",
                    message=buffer.message
                    or (
                        f"The travel time from {buffer.from_experience_name!r} to "
                        f"{buffer.to_experience_name!r} exceeds the available schedule gap."
                    ),
                    severity=ValidationSeverity.WARNING,
                )
            )

    if allowed_day_indices is not None:
        issues = [issue for issue in issues if issue.day_index in allowed_day_indices]

    return issues


class AIItineraryRepairRequestBuilder:
    """Builds a bounded `AIItineraryRepairRequest` from existing
    `PlanningState` data (Task 13). Read-only: never mutates
    `planning_state`, never calls a provider/LLM, and never performs new
    provider discovery.

    Composes `AIItineraryReasoningRequestBuilder` (Section 193A) to
    reconstruct the exact same `allowed_candidates`/trip-context fields
    the original reasoning call used (Task 4/9: reuse, never duplicate,
    that deterministic assembly logic) -- safe because that builder reads
    only already-computed `PlanningState` fields
    (`destination_context`/`candidate_quality_report`/
    `ai_candidate_promotion_report`/`candidate_grounding_batch`) with no
    provider call of its own, and 194A never mutates any of those fields
    between the original reasoning call and a repair call.
    """

    def __init__(self, reasoning_request_builder: AIItineraryReasoningRequestBuilder | None = None) -> None:
        self._reasoning_request_builder = reasoning_request_builder or AIItineraryReasoningRequestBuilder()

    def build_request(
        self,
        planning_state: PlanningState,
        attempt_number: int = 1,
        allowed_day_indices: set[int] | None = None,
    ) -> AIItineraryRepairRequest | None:
        """Returns `None` -- meaning "do not call the LLM" (Task 13/18) --
        whenever any of the following holds:

        - there is no `completed` `ai_itinerary_reasoning_result` to
          repair in the first place.
        - `classify_repairable_issues` finds nothing repairable.
        - every classified issue's day_index doesn't actually match a
          real day in the current reasoning result (defensive: a stale
          report from a since-changed plan).
        - the freshly-rebuilt candidate universe is empty, or the
          original reasoning result somehow references a candidate_id
          outside it (defensive: repair must never be attempted against
          an inconsistent candidate universe).

        `allowed_day_indices` (Section 197B, Task 28): forwarded to
        `classify_repairable_issues` unchanged -- `None` preserves the
        original whole-trip behavior.
        """
        original_result = planning_state.ai_itinerary_reasoning_result
        if original_result is None or original_result.status != AIItineraryReasoningStatus.COMPLETED:
            return None

        issues = classify_repairable_issues(planning_state, allowed_day_indices)
        if not issues:
            return None

        original_day_indexes = {day.day_index for day in original_result.days}
        issues = [issue for issue in issues if issue.day_index in original_day_indexes]
        if not issues:
            return None

        affected_days = sorted({issue.day_index for issue in issues})

        reasoning_request = self._reasoning_request_builder.build_request(planning_state)
        if not reasoning_request.allowed_candidates:
            return None

        try:
            return AIItineraryRepairRequest(
                trip_id=reasoning_request.trip_id,
                destination_name=reasoning_request.destination_name,
                start_date=reasoning_request.start_date,
                end_date=reasoning_request.end_date,
                trip_duration_days=reasoning_request.trip_duration_days,
                traveler_context=reasoning_request.traveler_context,
                trip_strategy_summary=reasoning_request.trip_strategy_summary,
                factual_context=reasoning_request.factual_context,
                allowed_candidates=reasoning_request.allowed_candidates,
                original_days=original_result.days,
                affected_days=affected_days,
                issues=issues,
                attempt_number=attempt_number,
            )
        except ValidationError:
            # Defensive: the freshly-rebuilt candidate universe no longer
            # covers every candidate_id the original result references
            # (e.g. a candidate's quality tier changed between calls in a
            # test scenario) -- refuse to build an inconsistent request
            # rather than send one downstream.
            return None
