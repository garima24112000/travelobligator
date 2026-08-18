from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.models.planning_state import PlanningState
from app.services.candidate_quality_service import CandidateQualityService
from app.services.destination_context_service import DestinationContextService
from app.services.experience_planner_service import ExperiencePlannerService
from app.services.plan_validator_service import PlanValidatorService
from app.services.stay_transport_service import StayTransportService
from app.services.traveler_profile_service import TravelerProfileService
from app.services.trip_strategy_service import TripStrategyService

# LangGraph skeleton around the deterministic planning pipeline (Step 162B,
# docs/13_llm_reasoning_pipeline.md section 42, docs/14_backend_architecture.md
# section 25). This is architecture/resume foundation only:
#
# - PlanningState remains the single source of truth. Every node here reads
#   it from PlanningGraphState, calls exactly one existing deterministic
#   stage service's `run` (or, for candidate quality, `build_report`), and
#   returns the mutated PlanningState back into the graph state -- no node
#   duplicates any stage service's own logic.
# - The node order mirrors PlanningOrchestrator.generate_full_plan's
#   existing stage order exactly, plus the candidate-quality step
#   PlanningOrchestrator already runs inline inside
#   run_destination_context_stage (docs/14_backend_architecture.md section
#   25, Step 156B), and a placeholder node standing in for a future
#   AI-candidate-discovery shadow node (Step 162C).
# - This module is not imported by any API route or by
#   PlanningOrchestrator's runtime path. It does not call a provider
#   network, an LLM (Groq/Anthropic/other), Kiwi/MCP, or a scraper, and it
#   does not persist anything (no PlanningStateRepository/TripRepository
#   call anywhere in this module). It does not change /trips/{trip_id}/
#   generate behavior, scheduling, validation, or regeneration behavior.
# - `ai_candidate_shadow_placeholder_node` is a deliberate no-op in this
#   step: it does not call AICandidateDiscoveryService, Groq, or Anthropic,
#   and it leaves `ai_candidate_proposal_batch`/`candidate_grounding_batch`
#   untouched. Step 162C will wire the real shadow-mode call here.
# - No module-level singleton is constructed -- `PlanningGraphRunner` and
#   `build_planning_graph` are only ever instantiated/called explicitly (by
#   tests, or by a future step that decides to wire this in), matching the
#   existing "no premature singleton" boundary used by every other
#   not-yet-wired piece of this AI-candidate-discovery track (Steps
#   157B-161B).


class PlanningGraphState(TypedDict):
    """LangGraph state schema for the planning graph.

    `planning_state` is the single source of truth, carried through and
    mutated by each node's underlying service call -- never duplicated or
    reconstructed by a node itself. `executed_nodes` is a test/debug-only
    trace of which nodes ran, in order; `errors` is reserved for a future
    step's error-handling policy and is never populated in Step 162B (every
    stage service `run` is documented to never raise for missing upstream/
    provider data -- see `PlanningStageService`).
    """

    planning_state: PlanningState
    executed_nodes: Annotated[list[str], operator.add]
    errors: Annotated[list[str], operator.add]


def build_planning_graph(
    traveler_profile_service: TravelerProfileService,
    destination_context_service: DestinationContextService,
    candidate_quality_service: CandidateQualityService,
    trip_strategy_service: TripStrategyService,
    stay_transport_service: StayTransportService,
    experience_planner_service: ExperiencePlannerService,
    plan_validator_service: PlanValidatorService,
) -> CompiledStateGraph:
    """Builds and compiles the planning `StateGraph` from already-constructed
    stage services (real or fake/injected). Every node below calls exactly
    one of these services' existing `run`/`build_report` method -- no stage
    logic is duplicated here.
    """

    def traveler_profile_node(state: PlanningGraphState) -> dict[str, Any]:
        planning_state = traveler_profile_service.run(state["planning_state"])
        return {"planning_state": planning_state, "executed_nodes": ["traveler_profile"]}

    def destination_context_node(state: PlanningGraphState) -> dict[str, Any]:
        planning_state = destination_context_service.run(state["planning_state"])
        return {"planning_state": planning_state, "executed_nodes": ["destination_context"]}

    def candidate_quality_node(state: PlanningGraphState) -> dict[str, Any]:
        planning_state = state["planning_state"]
        # Stores the report on planning_state exactly like
        # PlanningOrchestrator.run_destination_context_stage does (Step
        # 156B) -- never re-derives or duplicates the scoring logic itself,
        # which lives entirely in CandidateQualityService.
        planning_state.candidate_quality_report = candidate_quality_service.build_report(
            planning_state
        )
        return {"planning_state": planning_state, "executed_nodes": ["candidate_quality"]}

    def ai_candidate_shadow_placeholder_node(state: PlanningGraphState) -> dict[str, Any]:
        """Deliberate no-op placeholder (Step 162B). Does not call
        AICandidateDiscoveryService, Groq, or Anthropic, and does not touch
        `ai_candidate_proposal_batch`/`candidate_grounding_batch` -- it only
        records that this node executed. Step 162C wires the real
        config-gated shadow-mode call here.
        """
        return {"executed_nodes": ["ai_candidate_shadow_placeholder"]}

    def trip_strategy_node(state: PlanningGraphState) -> dict[str, Any]:
        planning_state = trip_strategy_service.run(state["planning_state"])
        return {"planning_state": planning_state, "executed_nodes": ["trip_strategy"]}

    def stay_transport_node(state: PlanningGraphState) -> dict[str, Any]:
        planning_state = stay_transport_service.run(state["planning_state"])
        return {"planning_state": planning_state, "executed_nodes": ["stay_transport"]}

    def experience_plan_node(state: PlanningGraphState) -> dict[str, Any]:
        planning_state = experience_planner_service.run(state["planning_state"])
        return {"planning_state": planning_state, "executed_nodes": ["experience_plan"]}

    def validation_node(state: PlanningGraphState) -> dict[str, Any]:
        planning_state = plan_validator_service.run(state["planning_state"])
        return {"planning_state": planning_state, "executed_nodes": ["validation"]}

    graph = StateGraph(PlanningGraphState)
    graph.add_node("traveler_profile", traveler_profile_node)
    graph.add_node("destination_context", destination_context_node)
    graph.add_node("candidate_quality", candidate_quality_node)
    graph.add_node("ai_candidate_shadow_placeholder", ai_candidate_shadow_placeholder_node)
    graph.add_node("trip_strategy", trip_strategy_node)
    graph.add_node("stay_transport", stay_transport_node)
    graph.add_node("experience_plan", experience_plan_node)
    graph.add_node("validation", validation_node)

    graph.add_edge(START, "traveler_profile")
    graph.add_edge("traveler_profile", "destination_context")
    graph.add_edge("destination_context", "candidate_quality")
    graph.add_edge("candidate_quality", "ai_candidate_shadow_placeholder")
    graph.add_edge("ai_candidate_shadow_placeholder", "trip_strategy")
    graph.add_edge("trip_strategy", "stay_transport")
    graph.add_edge("stay_transport", "experience_plan")
    graph.add_edge("experience_plan", "validation")
    graph.add_edge("validation", END)

    return graph.compile()


class PlanningGraphRunner:
    """DI-friendly wrapper around the compiled planning graph.

    Defaults to constructing the real stage services (mirroring
    `PlanningOrchestrator.__init__`'s own default-construction pattern), but
    every service can be injected -- tests should inject fakes so no real
    provider/network call ever happens. This class is never instantiated as
    a module-level singleton, and nothing in `app/api/routes/` or
    `PlanningOrchestrator` constructs or imports it.
    """

    def __init__(
        self,
        traveler_profile_service: TravelerProfileService | None = None,
        destination_context_service: DestinationContextService | None = None,
        candidate_quality_service: CandidateQualityService | None = None,
        trip_strategy_service: TripStrategyService | None = None,
        stay_transport_service: StayTransportService | None = None,
        experience_planner_service: ExperiencePlannerService | None = None,
        plan_validator_service: PlanValidatorService | None = None,
    ) -> None:
        self.traveler_profile_service = traveler_profile_service or TravelerProfileService()
        self.destination_context_service = (
            destination_context_service or DestinationContextService()
        )
        self.candidate_quality_service = candidate_quality_service or CandidateQualityService()
        self.trip_strategy_service = trip_strategy_service or TripStrategyService()
        self.stay_transport_service = stay_transport_service or StayTransportService()
        self.experience_planner_service = (
            experience_planner_service or ExperiencePlannerService()
        )
        self.plan_validator_service = plan_validator_service or PlanValidatorService()

        self._graph = build_planning_graph(
            self.traveler_profile_service,
            self.destination_context_service,
            self.candidate_quality_service,
            self.trip_strategy_service,
            self.stay_transport_service,
            self.experience_planner_service,
            self.plan_validator_service,
        )

    def run(self, planning_state: PlanningState) -> PlanningState:
        """Runs the full graph once and returns the resulting PlanningState.

        Never saves to any repository -- persistence stays the exclusive
        responsibility of the caller (as it does for
        `PlanningOrchestrator`'s stage-runner methods).
        """
        initial_state: PlanningGraphState = {
            "planning_state": planning_state,
            "executed_nodes": [],
            "errors": [],
        }
        result = self._graph.invoke(initial_state)
        return result["planning_state"]


def run_planning_graph(planning_state: PlanningState) -> PlanningState:
    """Convenience entry point using default real services. Not called by
    any API route or by `PlanningOrchestrator` in this step.
    """
    return PlanningGraphRunner().run(planning_state)
