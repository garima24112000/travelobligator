from __future__ import annotations

from app.models.ai_candidate_proposal import AICandidateProposalRequest, AICandidateProposalTask
from app.models.planning_state import PlanningState, TripRequest

# Builder that prepares the future LLM candidate proposal input package
# from existing PlanningState data (Step 160A, itinerary-generator-build-
# spec.md Stage 5, docs/13_llm_reasoning_pipeline.md section 34,
# docs/14_backend_architecture.md section 25). This is still pre-LLM: no
# LLM, LangGraph, LangSmith, or provider dependency is wired up here, and
# this module never calls `AICandidateProposalProvider`/
# `NotConnectedAICandidateProposalProvider`, never calls
# `CandidateGroundingService`, and never creates an `AICandidateProposal`,
# `GroundedCandidate`, or `RejectedCandidateProposal` itself.
#
# `build_request` only reads existing, safe `PlanningState` fields --
# trip metadata, explicit user-provided preference fields, provider
# candidate *counts* (never names), and unavailable-data field names -- and
# never mutates `planning_state`.


class AICandidateProposalRequestBuilder:
    """Builds a future `AICandidateProposalRequest` from existing
    `PlanningState` data.

    Deliberately conservative: `interests`/`must_visit`/`constraints` are
    read only from fields the user (or a prior deterministic stage)
    already populated on `trip_request`/`traveler_profile` -- nothing is
    inferred or defaulted. `provider_candidate_summary` is built from
    `destination_context` candidate *counts* only, never candidate names or
    raw place data, matching `AICandidateProposalRequest`'s own contract
    (docs/13_llm_reasoning_pipeline.md section 28): the LLM is told what's
    already covered, not fed raw provider data to restate.
    """

    def build_request(
        self,
        planning_state: PlanningState,
        task: AICandidateProposalTask = AICandidateProposalTask.DESTINATION_CANDIDATE_DISCOVERY,
        max_candidates: int = 15,
    ) -> AICandidateProposalRequest:
        trip_request = planning_state.trip_request

        return AICandidateProposalRequest(
            task=task,
            trip_id=planning_state.trip_id,
            destination_name=trip_request.primary_destination,
            trip_duration_days=self._trip_duration_days(trip_request),
            interests=self._dedupe(self._collect_field(planning_state, "interests")),
            must_visit=self._dedupe(self._collect_field(planning_state, "must_visit")),
            constraints=self._dedupe(self._collect_field(planning_state, "constraints")),
            provider_candidate_summary=self._provider_candidate_summary(planning_state),
            unavailable_data=self._unavailable_data_from_state(planning_state),
            max_candidates=max_candidates,
        )

    @staticmethod
    def _trip_duration_days(trip_request: TripRequest) -> int:
        """Inclusive calendar-day count, following the same convention
        already used by `TripStrategyService`/`ExperiencePlannerService`
        (`(end_date - start_date).days + 1`). `TripRequest` already
        enforces `end_date >= start_date`, so this is always >= 1 in
        practice; `max(1, ...)` is kept defensively per the build spec.
        """
        return max(1, (trip_request.end_date - trip_request.start_date).days + 1)

    @staticmethod
    def _collect_field(planning_state: PlanningState, field_name: str) -> list[str]:
        """Reads `field_name` from `trip_request` first, then
        `traveler_profile` (only if it exists) -- never inferred, never
        defaulted, and never read from anywhere else.
        """
        values: list[str] = []

        trip_request_values = getattr(planning_state.trip_request, field_name, None)
        if trip_request_values:
            values.extend(trip_request_values)

        traveler_profile = planning_state.traveler_profile
        if traveler_profile is not None:
            profile_values = getattr(traveler_profile, field_name, None)
            if profile_values:
                values.extend(profile_values)

        return values

    @staticmethod
    def _dedupe(values: list[str]) -> list[str]:
        seen: set[str] = set()
        deduped: list[str] = []
        for value in values:
            if not value or value in seen:
                continue
            seen.add(value)
            deduped.append(value)
        return deduped

    @staticmethod
    def _provider_candidate_summary(planning_state: PlanningState) -> dict[str, int]:
        """Counts only -- never candidate names or raw place data.
        Zero counts (not omitted keys) when `destination_context` doesn't
        exist yet, so a future LLM prompt can honestly say "0 known
        attractions" rather than nothing at all.
        """
        destination_context = planning_state.destination_context
        if destination_context is None:
            return {"attraction": 0, "restaurant": 0, "accommodation": 0}

        return {
            "attraction": len(destination_context.candidate_pois),
            "restaurant": len(destination_context.candidate_restaurants),
            "accommodation": len(destination_context.candidate_accommodation_pois),
        }

    @staticmethod
    def _unavailable_data_from_state(planning_state: PlanningState) -> list[str]:
        """Field names only, from `unavailable_data` then every
        `provider_status` entry's `unavailable_fields`, deduplicated while
        preserving first-occurrence order -- the same shape
        `CandidateGroundingRequestBuilder` already uses for
        `unavailable_data`, extended to also cover `provider_status` since
        that data is just as easy to read here.
        """
        seen: set[str] = set()
        fields: list[str] = []

        for item in planning_state.unavailable_data:
            field_name = item.field
            if not field_name or field_name in seen:
                continue
            seen.add(field_name)
            fields.append(field_name)

        for entry in planning_state.provider_status.values():
            for field_name in entry.unavailable_fields:
                if not field_name or field_name in seen:
                    continue
                seen.add(field_name)
                fields.append(field_name)

        return fields
