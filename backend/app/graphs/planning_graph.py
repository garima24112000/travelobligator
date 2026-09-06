from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.graphs.planning_graph_nodes import (
    build_accommodation_inventory_node,
    build_ai_candidate_node,
    build_candidate_quality_node,
    build_destination_context_node,
    build_experience_planning_node,
    build_final_state_node,
    build_flight_inventory_node,
    build_provider_coverage_node,
    build_route_aware_sequencing_node,
    build_route_feasibility_node,
    build_stay_transport_node,
    build_travel_time_buffer_node,
    build_traveler_profile_node,
    build_trip_strategy_node,
    build_validation_node,
)
from app.graphs.planning_graph_state import PlanningGraphState, build_initial_planning_graph_state
from app.models.planning_state import PlanningState, TripRequest
from app.services.accommodation_inventory_service import AccommodationInventoryService
from app.services.ai_candidate_promotion_service import AICandidatePromotionService
from app.services.candidate_quality_service import CandidateQualityService
from app.services.destination_context_service import DestinationContextService
from app.services.experience_planner_service import ExperiencePlannerService
from app.services.flight_inventory_service import FlightInventoryService
from app.services.plan_validator_service import PlanValidatorService
from app.services.route_aware_sequencing_service import RouteAwareSequencingService
from app.services.route_feasibility_service import RouteFeasibilityService
from app.services.stay_transport_service import StayTransportService
from app.services.travel_time_buffer_service import TravelTimeBufferService
from app.services.traveler_profile_service import TravelerProfileService
from app.services.trip_strategy_service import TripStrategyService

# LangGraph orchestration skeleton for the planning pipeline (Step 171A,
# extended for stage parity with `PlanningOrchestrator.generate_full_plan`
# in Step 171E -- docs/13_llm_reasoning_pipeline.md,
# docs/14_backend_architecture.md).
#
# As of Step 171E, this graph's stage order and outputs are the same
# planning-stage concepts `PlanningOrchestrator.generate_full_plan` covers
# (docs/CLAUDE.md's Traveler Profile -> Destination Context -> Trip
# Strategy -> Stay + Transport -> Experience Planner -> Plan Validator
# pipeline), reached via the exact same deterministic services in the
# same relative order:
#
#   START -> traveler_profile -> destination_context -> candidate_quality
#   -> ai_candidate -> trip_strategy -> stay_transport
#   -> accommodation_inventory -> flight_inventory -> experience_planning
#   -> route_feasibility -> route_aware_sequencing -> travel_time_buffer
#   -> validation -> provider_coverage -> final_state -> END
#
# One documented, intentional gap remains: the optional, off-by-default
# Step 161B AI candidate *discovery* shadow stage is not wrapped by any
# node here -- see `build_ai_candidate_node`'s docstring for why that is
# not a parity gap for the default (and only supported) LangGraph
# configuration.
#
# - `PlanningState` remains the single source of truth. Every node
#   (`planning_graph_nodes.py`) calls exactly one existing deterministic
#   service's own method (`run`/`build_report`/`apply_report`/
#   `apply_promotion`) -- no node duplicates any service's logic, and no
#   node calls Groq/Anthropic/OpenAI or any other LLM. LangGraph here
#   orchestrates existing deterministic services; it does not replace them
#   with LLM reasoning.
#   `ProviderGateway` (which defaults to safe not_connected
#   adapters, exactly like `PlanningOrchestrator` today), does not call
#   Kiwi/MCP or a scraper, and does not persist anything (no
#   `PlanningStateRepository`/`TripRepository` call anywhere in this
#   module).
# - No module-level singleton is constructed -- `PlanningGraphRunner` and
#   `build_planning_graph` are only ever instantiated/called explicitly.


def build_planning_graph(
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
) -> CompiledStateGraph:
    """Builds and compiles the planning `StateGraph` from already-
    constructed stage services (real or fake/injected). Every node calls
    exactly one of these services' existing method -- no stage logic is
    duplicated here.

    `ai_candidate_promotion_service` defaults to `None`, which keeps
    `ai_candidate` a pure no-op (see `build_ai_candidate_node`'s
    docstring) -- passing a real `AICandidatePromotionService` is
    deterministic and still never calls an LLM.

    Every other service defaults to constructing the real one (mirroring
    `PlanningOrchestrator.__init__`'s own default-construction pattern),
    which is safe to call in any environment since each service's
    provider calls go through `ProviderGateway`'s existing safe defaults
    -- tests should still inject fakes to keep runs fully deterministic
    and network-free.
    """
    resolved_traveler_profile_service = traveler_profile_service or TravelerProfileService()
    resolved_destination_context_service = (
        destination_context_service or DestinationContextService()
    )
    resolved_candidate_quality_service = candidate_quality_service or CandidateQualityService()
    resolved_trip_strategy_service = trip_strategy_service or TripStrategyService()
    resolved_stay_transport_service = stay_transport_service or StayTransportService()
    resolved_accommodation_inventory_service = (
        accommodation_inventory_service or AccommodationInventoryService()
    )
    resolved_flight_inventory_service = flight_inventory_service or FlightInventoryService()
    resolved_experience_planner_service = (
        experience_planner_service or ExperiencePlannerService()
    )
    resolved_route_feasibility_service = route_feasibility_service or RouteFeasibilityService()
    resolved_route_aware_sequencing_service = (
        route_aware_sequencing_service or RouteAwareSequencingService()
    )
    resolved_travel_time_buffer_service = (
        travel_time_buffer_service or TravelTimeBufferService()
    )
    resolved_plan_validator_service = plan_validator_service or PlanValidatorService()

    graph = StateGraph(PlanningGraphState)
    graph.add_node(
        "traveler_profile", build_traveler_profile_node(resolved_traveler_profile_service)
    )
    graph.add_node(
        "destination_context", build_destination_context_node(resolved_destination_context_service)
    )
    graph.add_node(
        "candidate_quality", build_candidate_quality_node(resolved_candidate_quality_service)
    )
    graph.add_node("ai_candidate", build_ai_candidate_node(ai_candidate_promotion_service))
    graph.add_node("trip_strategy", build_trip_strategy_node(resolved_trip_strategy_service))
    graph.add_node("stay_transport", build_stay_transport_node(resolved_stay_transport_service))
    graph.add_node(
        "accommodation_inventory",
        build_accommodation_inventory_node(resolved_accommodation_inventory_service),
    )
    graph.add_node(
        "flight_inventory", build_flight_inventory_node(resolved_flight_inventory_service)
    )
    graph.add_node(
        "experience_planning", build_experience_planning_node(resolved_experience_planner_service)
    )
    graph.add_node(
        "route_feasibility", build_route_feasibility_node(resolved_route_feasibility_service)
    )
    graph.add_node(
        "route_aware_sequencing",
        build_route_aware_sequencing_node(
            resolved_route_aware_sequencing_service, resolved_route_feasibility_service
        ),
    )
    graph.add_node(
        "travel_time_buffer", build_travel_time_buffer_node(resolved_travel_time_buffer_service)
    )
    graph.add_node("validation", build_validation_node(resolved_plan_validator_service))
    graph.add_node("provider_coverage", build_provider_coverage_node())
    graph.add_node("final_state", build_final_state_node())

    graph.add_edge(START, "traveler_profile")
    graph.add_edge("traveler_profile", "destination_context")
    graph.add_edge("destination_context", "candidate_quality")
    graph.add_edge("candidate_quality", "ai_candidate")
    graph.add_edge("ai_candidate", "trip_strategy")
    graph.add_edge("trip_strategy", "stay_transport")
    graph.add_edge("stay_transport", "accommodation_inventory")
    graph.add_edge("accommodation_inventory", "flight_inventory")
    graph.add_edge("flight_inventory", "experience_planning")
    graph.add_edge("experience_planning", "route_feasibility")
    graph.add_edge("route_feasibility", "route_aware_sequencing")
    graph.add_edge("route_aware_sequencing", "travel_time_buffer")
    graph.add_edge("travel_time_buffer", "validation")
    graph.add_edge("validation", "provider_coverage")
    graph.add_edge("provider_coverage", "final_state")
    graph.add_edge("final_state", END)

    return graph.compile()


class PlanningGraphRunner:
    """DI-friendly wrapper around the compiled planning graph.

    Defaults to constructing the real stage services (mirroring
    `PlanningOrchestrator.__init__`'s own default-construction pattern),
    but every service can be injected -- tests should inject fakes so no
    real provider/network call ever happens. This class is never
    instantiated as a module-level singleton, and nothing in
    `app/api/routes/` or `PlanningOrchestrator` constructs or imports it
    directly.
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
    ) -> None:
        self.traveler_profile_service = traveler_profile_service or TravelerProfileService()
        self.destination_context_service = (
            destination_context_service or DestinationContextService()
        )
        self.candidate_quality_service = candidate_quality_service or CandidateQualityService()
        # None (the default) keeps ai_candidate a pure no-op -- see
        # build_ai_candidate_node's docstring.
        self.ai_candidate_promotion_service = ai_candidate_promotion_service
        self.trip_strategy_service = trip_strategy_service or TripStrategyService()
        self.stay_transport_service = stay_transport_service or StayTransportService()
        self.accommodation_inventory_service = (
            accommodation_inventory_service or AccommodationInventoryService()
        )
        self.flight_inventory_service = flight_inventory_service or FlightInventoryService()
        self.experience_planner_service = (
            experience_planner_service or ExperiencePlannerService()
        )
        self.route_feasibility_service = route_feasibility_service or RouteFeasibilityService()
        self.route_aware_sequencing_service = (
            route_aware_sequencing_service or RouteAwareSequencingService()
        )
        self.travel_time_buffer_service = (
            travel_time_buffer_service or TravelTimeBufferService()
        )
        self.plan_validator_service = plan_validator_service or PlanValidatorService()

        self._graph = build_planning_graph(
            self.traveler_profile_service,
            self.destination_context_service,
            self.candidate_quality_service,
            self.ai_candidate_promotion_service,
            self.trip_strategy_service,
            self.stay_transport_service,
            self.accommodation_inventory_service,
            self.flight_inventory_service,
            self.experience_planner_service,
            self.route_feasibility_service,
            self.route_aware_sequencing_service,
            self.travel_time_buffer_service,
            self.plan_validator_service,
        )

    def run(
        self,
        trip_id: str,
        trip_request: TripRequest,
        planning_state: PlanningState,
    ) -> PlanningGraphState:
        """Runs the full graph once and returns the resulting
        `PlanningGraphState` (including `completed_nodes`/`failed_nodes`/
        `errors`/`warnings`, not just `planning_state`).

        Never saves to any repository -- persistence stays the exclusive
        responsibility of the caller, exactly like
        `PlanningOrchestrator`'s own stage-runner methods.
        """
        initial_state = build_initial_planning_graph_state(trip_id, trip_request, planning_state)
        result = self._graph.invoke(initial_state)
        return result


def run_planning_graph(
    trip_id: str,
    trip_request: TripRequest,
    planning_state: PlanningState,
) -> PlanningGraphState:
    """Convenience entry point using default real (but LLM-free-by-default)
    services.
    """
    return PlanningGraphRunner().run(trip_id, trip_request, planning_state)
