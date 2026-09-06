from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.graphs.planning_graph import PlanningGraphRunner
from app.models.planning_state import (
    GenerationProgress,
    PipelineStatus,
    PlanningStage,
    PlanningState,
    TripRequest,
)
from app.providers.gateway import provider_gateway
from app.services.accommodation_inventory_service import AccommodationInventoryService
from app.services.ai_candidate_promotion_service import AICandidatePromotionService
from app.services.candidate_quality_service import CandidateQualityService
from app.services.destination_context_service import DestinationContextService
from app.services.experience_planner_service import ExperiencePlannerService
from app.services.flight_inventory_service import FlightInventoryService
from app.services.plan_validator_service import PlanValidatorService
from app.services.regeneration_readiness_service import (
    RegenerationReadinessService,
    regeneration_readiness_service,
)
from app.services.route_aware_sequencing_service import RouteAwareSequencingService
from app.services.route_feasibility_service import RouteFeasibilityService
from app.services.stay_transport_service import StayTransportService
from app.services.travel_time_buffer_service import TravelTimeBufferService
from app.services.traveler_profile_service import TravelerProfileService
from app.services.trip_strategy_service import TripStrategyService

logger = logging.getLogger(__name__)

# LangGraph planning runner/service (Step 171B, docs/13_llm_reasoning_
# pipeline.md, docs/14_backend_architecture.md). Wraps the
# `PlanningGraphRunner`/`build_planning_graph` graph (Step 171A, extended
# for stage parity with legacy generation in Step 171E) so the graph can be
# run end to end with injected deterministic services.
#
# As of Step 171E, `Settings.planning_engine_mode` defaults to
# `"langgraph"`, and `POST /trips/{trip_id}/generate` calls this service
# (via `PlanningOrchestrator.generate_full_plan_via_langgraph`) by default
# -- `PLANNING_ENGINE_MODE=legacy` remains available and calls
# `PlanningOrchestrator.generate_full_plan` (completely unmodified)
# instead.
#
# - `PlanningState` remains the single source of truth. This service never
#   duplicates a stage service's logic; it only decides whether to build a
#   fresh `PlanningState` (when none is given) and then delegates the
#   actual run to `PlanningGraphRunner`, which itself delegates every node
#   to exactly one existing deterministic service's own method.
# - This module is never imported by `app.api.routes.trips` directly for
#   the `/generate` path -- only `PlanningOrchestrator.
#   generate_full_plan_via_langgraph` calls it, keeping persistence and
#   pipeline-status/version bookkeeping the orchestrator's own
#   responsibility (see that method's docstring). It never calls Groq/
#   Anthropic/OpenAI or any other LLM, never calls an AI candidate
#   proposal provider directly, and never calls Kiwi/MCP or a live
#   scraper.
# - It never persists anything: no `PlanningStateRepository`/
#   `TripRepository` call anywhere in this module. Persistence remains the
#   exclusive responsibility of the route/orchestrator layer that decides
#   to use this service, exactly like `PlanningGraphRunner`/
#   `PlanningOrchestrator`'s own stage-runner methods already work.
# - No module-level singleton is constructed for `LangGraphPlanningService`
#   itself -- only ever instantiated explicitly (by
#   `PlanningOrchestrator.__init__`, by the shadow-run route, or by
#   tests).


@dataclass
class LangGraphPlanningResult:
    """Wraps one `LangGraphPlanningService.run` call's outcome.

    Deliberately kept separate from `PlanningState` rather than adding
    `completed_nodes`/`failed_nodes`/`errors`/`warnings` as new
    `PlanningState` fields -- these are graph-run bookkeeping (a
    test/debug trace of which nodes ran and why), not a section of the
    travel plan itself, so they don't belong on the single source of
    truth alongside `destination_context`/`experience_plan`/etc.
    """

    planning_state: PlanningState
    completed_nodes: list[str] = field(default_factory=list)
    failed_nodes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class LangGraphPlanningService:
    """Runs the Step 171A planning graph end to end for a given trip.

    Every stage service is injectable (mirrors `PlanningGraphRunner`'s own
    constructor); defaults to constructing the real ones, which is safe
    in any environment since their provider calls go through
    `ProviderGateway`'s existing safe defaults -- tests should still
    inject fakes to keep runs fully deterministic and network-free.
    `ai_candidate_promotion_service` defaults to `None`, which keeps the
    `ai_candidate` graph node a pure no-op (see
    `build_ai_candidate_node`'s docstring) -- this service never calls
    `AICandidateDiscoveryService`/an AI candidate proposal provider on its
    own.
    """

    def __init__(
        self,
        traveler_profile_service: TravelerProfileService | None = None,
        destination_context_service: DestinationContextService | None = None,
        candidate_quality_service: CandidateQualityService | None = None,
        ai_candidate_promotion_service: AICandidatePromotionService | None = None,
        trip_strategy_service: TripStrategyService | None = None,
        stay_transport_service: StayTransportService | None = None,
        accommodation_inventory_service: AccommodationInventoryService | None = None,
        flight_inventory_service: FlightInventoryService | None = None,
        experience_planner_service: ExperiencePlannerService | None = None,
        route_feasibility_service: RouteFeasibilityService | None = None,
        route_aware_sequencing_service: RouteAwareSequencingService | None = None,
        travel_time_buffer_service: TravelTimeBufferService | None = None,
        plan_validator_service: PlanValidatorService | None = None,
        regeneration_readiness_service_instance: RegenerationReadinessService | None = None,
    ) -> None:
        self._runner = PlanningGraphRunner(
            traveler_profile_service=traveler_profile_service,
            destination_context_service=destination_context_service,
            candidate_quality_service=candidate_quality_service,
            ai_candidate_promotion_service=ai_candidate_promotion_service,
            trip_strategy_service=trip_strategy_service,
            stay_transport_service=stay_transport_service,
            accommodation_inventory_service=accommodation_inventory_service,
            flight_inventory_service=flight_inventory_service,
            experience_planner_service=experience_planner_service,
            route_feasibility_service=route_feasibility_service,
            route_aware_sequencing_service=route_aware_sequencing_service,
            travel_time_buffer_service=travel_time_buffer_service,
            plan_validator_service=plan_validator_service,
        )
        self.regeneration_readiness_service = (
            regeneration_readiness_service_instance or regeneration_readiness_service
        )

    def run(
        self,
        trip_id: str,
        trip_request: TripRequest,
        planning_state: PlanningState | None = None,
    ) -> LangGraphPlanningResult:
        """Runs the full planning graph once and returns a
        `LangGraphPlanningResult`.

        If `planning_state` is `None`, a fresh one is built following the
        same conventions `PlanningOrchestrator.create_trip` uses for a
        brand-new trip (`PlanningStage.CREATE_TRIP`/`PipelineStatus.DRAFT`
        bookkeeping, a `not_connected` provider-coverage snapshot, an idle
        `GenerationProgress`, and a freshly recomputed
        `regeneration_readiness` gate) -- except it never persists it:
        unlike `create_trip`, this never calls
        `TripRepository.create`/`PlanningStateRepository.save`. Persisting
        the result (if a caller wants to) remains that caller's own
        responsibility.

        Node-level failures are already caught safely by
        `planning_graph_nodes.py` and surfaced here via `failed_nodes`/
        `errors` -- never fabricated `PlanningState` data. An unexpected
        failure invoking the graph itself (as opposed to one node failing
        safely) is logged and re-raised rather than swallowed, mirroring
        `PlanningOrchestrator.generate_full_plan`'s own fail-loud-at-the-
        top-level behavior.
        """
        if planning_state is None:
            planning_state = self._build_new_planning_state(trip_id, trip_request)

        try:
            graph_state = self._runner.run(trip_id, trip_request, planning_state)
        except Exception:
            logger.warning(
                "LangGraphPlanningService.run failed unexpectedly while invoking the "
                "planning graph.",
                exc_info=True,
            )
            raise

        return LangGraphPlanningResult(
            planning_state=graph_state["planning_state"],
            completed_nodes=list(graph_state.get("completed_nodes", [])),
            failed_nodes=list(graph_state.get("failed_nodes", [])),
            errors=list(graph_state.get("errors", [])),
            warnings=list(graph_state.get("warnings", [])),
        )

    def _build_new_planning_state(self, trip_id: str, trip_request: TripRequest) -> PlanningState:
        """Builds a fresh, never-persisted `PlanningState` for `trip_id`,
        following the same construction conventions
        `PlanningOrchestrator.create_trip` uses -- minus the persistence
        calls, which stay the caller's responsibility.
        """
        planning_state = PlanningState(trip_id=trip_id, trip_request=trip_request)
        planning_state.set_active_stage(PlanningStage.CREATE_TRIP)
        planning_state.set_pipeline_status(PipelineStatus.DRAFT)
        planning_state.provider_coverage = provider_gateway.default_provider_coverage()
        planning_state.generation_progress = GenerationProgress()
        planning_state = self.regeneration_readiness_service.recompute(planning_state)
        return planning_state
