from __future__ import annotations

from typing import Any, Callable

from app.core.config import get_settings
from app.graphs.planning_graph_state import PlanningGraphState
from app.models.accommodation import AccommodationSearchResult, AccommodationSearchStatus
from app.models.common import ProviderStatus
from app.models.flight import FlightSearchResult, FlightSearchStatus
from app.models.hotel_ratings import HotelRatingsStatus
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

# Deterministic LangGraph node skeleton for the planning graph (Step 171A,
# extended for stage parity with `PlanningOrchestrator.generate_full_plan`
# in Step 171E -- docs/13_llm_reasoning_pipeline.md,
# docs/14_backend_architecture.md).
#
# Every node here is a thin wrapper around exactly one already-existing,
# already-deterministic service call -- no node duplicates a service's own
# logic, and no node calls Groq/Anthropic/OpenAI or any other LLM directly.
# `PlanningState` remains the single source of truth: a node either calls
# a service's `run`/`build_report`/`apply_promotion` (which mutates and
# returns `PlanningState`, or returns a report this node stores on
# `PlanningState` itself, exactly mirroring what
# `PlanningOrchestrator.generate_full_plan`'s own stage-runner methods
# already do for that same field) or is a pure checkpoint that touches
# nothing.
#
# Each `build_*_node` factory takes an optional injected service instance
# (defaulting to constructing the real one, mirroring the existing
# `DestinationContextService`/`StayTransportService`/etc. default-
# construction pattern already used by `PlanningOrchestrator`) and returns
# a plain node function `(state: PlanningGraphState) -> dict[str, Any]`.
# Tests should always inject a fake/no-op double -- never a real service
# backed by a live network call -- so this module is safe to exercise in
# any test environment.
#
# Every node fails safe: an unexpected exception from the underlying
# service is caught, `failed_nodes`/`errors` record a generic, secret-free
# marker (never the raw exception, a prompt, or an LLM response), and
# `planning_state` is left completely untouched for that node -- a failed
# node never fabricates a fact (or a "failed" placeholder report) to
# compensate for the failure. This is a deliberate, honest difference from
# `PlanningOrchestrator`'s own Step 166D-style fallback-report hardening
# (which stores an explicit `status=failed` report object on an unexpected
# exception): every node in this graph instead simply leaves the affected
# `PlanningState` field exactly as it already was, which is never a
# fabricated value. A successful node records itself in `completed_nodes`
# and returns the (possibly mutated) `planning_state`.
#
# `_accommodation_coverage_value`/`_flight_coverage_value`/
# `_route_status_to_coverage_value` below intentionally mirror the
# equivalent mapping helpers in `planning_orchestrator.py`
# (`_accommodation_coverage_value`/`_flight_coverage_value`/
# `_ROUTE_STATUS_TO_COVERAGE_VALUE`) -- these are pure, static,
# side-effect-free status-to-label mappings (never a scheduling/
# eligibility/quality decision), duplicated here rather than imported
# because `planning_orchestrator.py` imports this graphs package
# (indirectly, via `LangGraphPlanningService`) and importing anything back
# from `planning_orchestrator.py` at module load time would be a circular
# import. `test_langgraph_planning_nodes.py` asserts both copies stay in
# sync so this intentional duplication can never silently drift.

PlanningGraphNode = Callable[[PlanningGraphState], dict[str, Any]]


def _safe_error(node_name: str) -> str:
    """A generic, secret-free failure marker -- never the raw exception
    text, a stack trace, a prompt, or an LLM response. Matches the same
    safety contract the superseded Step 162C `ai_candidate_shadow_node`
    already established for this codebase.
    """
    return f"{node_name}_node failed safely; planning_state left unchanged."


# ---------------------------------------------------------------------------
# Step 171E: coverage-value mapping helpers, intentionally mirroring
# planning_orchestrator.py's own mappings (see module docstring above).
# ---------------------------------------------------------------------------

_ACCOMMODATION_STATUS_TO_COVERAGE_VALUE = {
    AccommodationSearchStatus.SUCCESS: "success",
    AccommodationSearchStatus.NOT_CONNECTED: "not_connected",
    AccommodationSearchStatus.FAILED: "failed",
    AccommodationSearchStatus.UNAVAILABLE: "unavailable",
}


def _accommodation_coverage_value(result: AccommodationSearchResult) -> str:
    if result.status == AccommodationSearchStatus.SUCCESS and not result.offers:
        return "unavailable"
    return _ACCOMMODATION_STATUS_TO_COVERAGE_VALUE.get(result.status, "not_connected")


# Step 177D: mirrors planning_orchestrator.py's own
# _HOTEL_RATINGS_STATUS_TO_COVERAGE_VALUE/_hotel_ratings_coverage_value
# exactly (see that module's comment for the full mapping rationale) --
# duplicated here for the same circular-import reason as the other
# coverage-mapping helpers in this module (see the module docstring
# above).
_HOTEL_RATINGS_STATUS_TO_COVERAGE_VALUE = {
    HotelRatingsStatus.NOT_CONNECTED: "not_connected",
    HotelRatingsStatus.FAILED: "failed",
    HotelRatingsStatus.UNAVAILABLE: "unavailable",
}


def _hotel_ratings_coverage_value(result: AccommodationSearchResult) -> str | None:
    status = result.hotel_ratings_status
    if status is None:
        return None
    if status != HotelRatingsStatus.SUCCESS:
        return _HOTEL_RATINGS_STATUS_TO_COVERAGE_VALUE.get(status, "not_connected")

    total_offers = len(result.offers)
    enriched = result.hotel_ratings_enriched_offer_count
    if enriched <= 0:
        return "unavailable"
    if enriched < total_offers:
        return "partial"
    return "success"


_FLIGHT_STATUS_TO_COVERAGE_VALUE = {
    FlightSearchStatus.SUCCESS: "success",
    FlightSearchStatus.NOT_CONNECTED: "not_connected",
    FlightSearchStatus.FAILED: "failed",
    FlightSearchStatus.UNAVAILABLE: "unavailable",
}


def _flight_coverage_value(result: FlightSearchResult) -> str:
    if result.status == FlightSearchStatus.SUCCESS and not result.offers:
        return "unavailable"
    return _FLIGHT_STATUS_TO_COVERAGE_VALUE.get(result.status, "not_connected")


_ROUTE_STATUS_TO_COVERAGE_VALUE = {
    ProviderStatus.SUCCESS: "success",
    ProviderStatus.PARTIAL: "partial",
    ProviderStatus.NOT_CONNECTED: "not_connected",
    ProviderStatus.FAILED: "failed",
    ProviderStatus.UNAVAILABLE: "unavailable",
}


def build_traveler_profile_node(
    service: TravelerProfileService | None = None,
) -> PlanningGraphNode:
    """Wraps `TravelerProfileService.run` -- the same deterministic stage
    `PlanningOrchestrator.run_traveler_profile_stage` calls. Never calls an
    LLM.
    """
    resolved_service = service or TravelerProfileService()

    def traveler_profile_node(state: PlanningGraphState) -> dict[str, Any]:
        try:
            planning_state = resolved_service.run(state["planning_state"])
        except Exception:
            return {
                "failed_nodes": ["traveler_profile"],
                "errors": [_safe_error("traveler_profile")],
            }
        return {"planning_state": planning_state, "completed_nodes": ["traveler_profile"]}

    return traveler_profile_node


def build_destination_context_node(
    service: DestinationContextService | None = None,
) -> PlanningGraphNode:
    """Wraps `DestinationContextService.run` -- the same deterministic
    provider-backed destination-context stage `PlanningOrchestrator`
    already calls. Never calls an LLM; any provider call it makes goes
    through the existing `ProviderGateway`, unaffected by this wrapper.
    """
    resolved_service = service or DestinationContextService()

    def destination_context_node(state: PlanningGraphState) -> dict[str, Any]:
        try:
            planning_state = resolved_service.run(state["planning_state"])
        except Exception:
            return {
                "failed_nodes": ["destination_context"],
                "errors": [_safe_error("destination_context")],
            }
        return {"planning_state": planning_state, "completed_nodes": ["destination_context"]}

    return destination_context_node


def build_candidate_quality_node(
    service: CandidateQualityService | None = None,
) -> PlanningGraphNode:
    """Wraps `CandidateQualityService.build_report` -- deterministic
    pre-ranking metadata only (Step 156A/156B, docs/18_candidate_quality.md),
    the same report `PlanningOrchestrator.run_destination_context_stage`
    already computes right after `DestinationContextService.run`. Never
    calls a provider/AI/LLM, never mutates
    `candidate_pois`/`candidate_restaurants`/`candidate_accommodation_pois`.
    Handles a trip with no `destination_context` yet exactly as the
    service itself does (it never raises for that case).
    """
    resolved_service = service or CandidateQualityService()

    def candidate_quality_node(state: PlanningGraphState) -> dict[str, Any]:
        planning_state = state["planning_state"]
        try:
            report = resolved_service.build_report(planning_state)
        except Exception:
            return {
                "failed_nodes": ["candidate_quality"],
                "errors": [_safe_error("candidate_quality")],
            }
        planning_state.candidate_quality_report = report
        return {"planning_state": planning_state, "completed_nodes": ["candidate_quality"]}

    return candidate_quality_node


def build_ai_candidate_node(
    service: AICandidatePromotionService | None = None,
) -> PlanningGraphNode:
    """AI candidate review/promotion checkpoint (Section 170).

    Deliberately conservative: with no service injected (the default),
    this is a pure no-op checkpoint -- it never calls
    `AICandidateDiscoveryService`/an AI candidate proposal provider, and
    therefore never calls Groq/Anthropic/OpenAI or any other LLM, and
    never mutates `planning_state`. If a caller explicitly injects an
    `AICandidatePromotionService` (itself fully deterministic -- it only
    reads `ai_candidate_proposal_batch`/`candidate_grounding_batch`/
    `candidate_quality_report`, already computed elsewhere, never calls a
    provider or LLM), this node calls its `apply_promotion` to materialize
    an already-eligible candidate into `ai_candidate_promotion_report`
    (Step 170C/170D) -- never anything more than that.

    Step 171E scope note: the optional, off-by-default Step 161B AI
    candidate *discovery* shadow stage
    (`PlanningOrchestrator._run_ai_candidate_discovery_shadow_stage`,
    gated by `Settings.ai_candidate_discovery_shadow_mode_enabled`,
    default `False`) is intentionally *not* wrapped by this or any other
    graph node. That stage is a separate, explicitly opt-in shadow-mode
    subsystem (CLAUDE.md: "Don't wire it into the real pipeline unless
    explicitly asked to") whose only effect is populating
    `ai_candidate_proposal_batch`/`candidate_grounding_batch` for
    inspection -- with shadow mode off (the default, and the only
    supported configuration for the LangGraph engine today), it is already
    a complete no-op in the legacy path too, so this is not a parity gap
    for the default configuration this step makes the new default engine.
    `POST /trips/{trip_id}/ai-candidate-review` and
    `POST /trips/{trip_id}/ai-candidate-promotions` remain fully available
    and unaffected regardless of which engine generated the plan --
    running either engine, then calling the promotion endpoint, is an
    equivalent way to reach the same `ai_candidate_promotion_report`
    output as long as shadow mode was enabled and a real AI candidate
    proposal provider is connected. See
    `test_langgraph_generate_mode.py` for the tests documenting this.
    """

    def ai_candidate_node(state: PlanningGraphState) -> dict[str, Any]:
        if service is None:
            return {"completed_nodes": ["ai_candidate"]}
        try:
            planning_state = service.apply_promotion(state["planning_state"])
        except Exception:
            return {
                "failed_nodes": ["ai_candidate"],
                "errors": [_safe_error("ai_candidate")],
            }
        return {"planning_state": planning_state, "completed_nodes": ["ai_candidate"]}

    return ai_candidate_node


def build_trip_strategy_node(
    service: TripStrategyService | None = None,
) -> PlanningGraphNode:
    """Wraps `TripStrategyService.run` -- the same deterministic stage
    `PlanningOrchestrator.run_trip_strategy_stage` calls. Never calls an
    LLM.
    """
    resolved_service = service or TripStrategyService()

    def trip_strategy_node(state: PlanningGraphState) -> dict[str, Any]:
        try:
            planning_state = resolved_service.run(state["planning_state"])
        except Exception:
            return {
                "failed_nodes": ["trip_strategy"],
                "errors": [_safe_error("trip_strategy")],
            }
        return {"planning_state": planning_state, "completed_nodes": ["trip_strategy"]}

    return trip_strategy_node


def build_stay_transport_node(
    service: StayTransportService | None = None,
) -> PlanningGraphNode:
    """Wraps `StayTransportService.run` -- never calls an LLM."""
    resolved_service = service or StayTransportService()

    def stay_transport_node(state: PlanningGraphState) -> dict[str, Any]:
        try:
            planning_state = resolved_service.run(state["planning_state"])
        except Exception:
            return {
                "failed_nodes": ["stay_transport"],
                "errors": [_safe_error("stay_transport")],
            }
        return {"planning_state": planning_state, "completed_nodes": ["stay_transport"]}

    return stay_transport_node


def build_accommodation_inventory_node(
    service: AccommodationInventoryService | None = None,
) -> PlanningGraphNode:
    """Wraps `AccommodationInventoryService.build_report` -- the same
    bookable-inventory report `PlanningOrchestrator.run_stay_transport_stage`
    already computes (Step 167D) right after `StayTransportService.run`.
    Never schedules lodging into the itinerary, never adds hotel
    recommendation logic, never fabricates a property/price/rating/
    availability/booking link -- only records an honest inventory status,
    plus the derived `ProviderCoverage.hotel_prices` value and (Step
    177D) `ProviderCoverage.hotel_ratings` value.
    """
    resolved_service = service or AccommodationInventoryService()

    def accommodation_inventory_node(state: PlanningGraphState) -> dict[str, Any]:
        planning_state = state["planning_state"]
        try:
            report = resolved_service.build_report(planning_state)
        except Exception:
            return {
                "failed_nodes": ["accommodation_inventory"],
                "errors": [_safe_error("accommodation_inventory")],
            }
        planning_state.accommodation_inventory_report = report
        planning_state.provider_coverage.hotel_prices = _accommodation_coverage_value(report)
        planning_state.provider_coverage.hotel_ratings = _hotel_ratings_coverage_value(report)
        return {"planning_state": planning_state, "completed_nodes": ["accommodation_inventory"]}

    return accommodation_inventory_node


def build_flight_inventory_node(
    service: FlightInventoryService | None = None,
) -> PlanningGraphNode:
    """Wraps `FlightInventoryService.build_report` -- the same bookable
    flight-inventory report `PlanningOrchestrator.run_stay_transport_stage`
    already computes (Step 169E) alongside `accommodation_inventory_report`.
    Never schedules a flight into the itinerary as a daily experience,
    never adds flight recommendation logic, never fabricates an airline/
    flight number/airport/time/duration/price/availability/baggage policy/
    cancellation policy/booking link -- only records an honest inventory
    status, plus the derived `ProviderCoverage.flights` value.
    """
    resolved_service = service or FlightInventoryService()

    def flight_inventory_node(state: PlanningGraphState) -> dict[str, Any]:
        planning_state = state["planning_state"]
        try:
            report = resolved_service.build_report(planning_state)
        except Exception:
            return {
                "failed_nodes": ["flight_inventory"],
                "errors": [_safe_error("flight_inventory")],
            }
        planning_state.flight_inventory_report = report
        planning_state.provider_coverage.flights = _flight_coverage_value(report)
        return {"planning_state": planning_state, "completed_nodes": ["flight_inventory"]}

    return flight_inventory_node


def build_experience_planning_node(
    service: ExperiencePlannerService | None = None,
) -> PlanningGraphNode:
    """Wraps `ExperiencePlannerService.run` -- never calls an LLM, never
    invents a coordinate/rating/price/route/opening-hour/description.
    """
    resolved_service = service or ExperiencePlannerService()

    def experience_planning_node(state: PlanningGraphState) -> dict[str, Any]:
        try:
            planning_state = resolved_service.run(state["planning_state"])
        except Exception:
            return {
                "failed_nodes": ["experience_planning"],
                "errors": [_safe_error("experience_planning")],
            }
        return {"planning_state": planning_state, "completed_nodes": ["experience_planning"]}

    return experience_planning_node


def build_route_feasibility_node(
    service: RouteFeasibilityService | None = None,
) -> PlanningGraphNode:
    """Wraps `RouteFeasibilityService.build_report` -- the same route
    feasibility report `PlanningOrchestrator.run_experience_plan_stage`
    already computes (Step 165E) right after `experience_plan` exists.
    Never reorders/drops a scheduled experience -- see
    `RouteFeasibilityReport`'s own docstring for the route-aware-scheduling
    boundary (Section 166). Also sets the derived
    `ProviderCoverage.routes` value.
    """
    resolved_service = service or RouteFeasibilityService()

    def route_feasibility_node(state: PlanningGraphState) -> dict[str, Any]:
        planning_state = state["planning_state"]
        try:
            report = resolved_service.build_report(planning_state)
        except Exception:
            return {
                "failed_nodes": ["route_feasibility"],
                "errors": [_safe_error("route_feasibility")],
            }
        planning_state.route_feasibility_report = report
        planning_state.provider_coverage.routes = _ROUTE_STATUS_TO_COVERAGE_VALUE.get(
            report.status, "not_connected"
        )
        return {"planning_state": planning_state, "completed_nodes": ["route_feasibility"]}

    return route_feasibility_node


def build_route_aware_sequencing_node(
    sequencing_service: RouteAwareSequencingService | None = None,
    route_feasibility_service: RouteFeasibilityService | None = None,
) -> PlanningGraphNode:
    """Wraps `RouteAwareSequencingService.build_report`/`apply_report` --
    the same shadow/report-only day-sequencing suggestion
    `PlanningOrchestrator.run_experience_plan_stage` already computes (Step
    166A), plus its config-gated application (Step 166B).

    `build_report` always runs (mirroring legacy) and is always shadow/
    report-only (`is_shadow_only=True`) until applied. Applying it onto
    the real schedule only ever happens when
    `Settings.route_aware_scheduling_enabled` is `True` -- the exact same
    config flag legacy checks, read via the exact same `get_settings()`
    call -- preserving that config's behavior identically between engines.
    When applied, `route_feasibility_report` is rebuilt (via
    `route_feasibility_service`) so it stays consistent with the
    now-reordered schedule, exactly mirroring
    `PlanningOrchestrator.run_experience_plan_stage`'s own behavior after a
    successful `apply_report`.
    """
    resolved_sequencing_service = sequencing_service or RouteAwareSequencingService()
    resolved_route_feasibility_service = route_feasibility_service or RouteFeasibilityService()

    def route_aware_sequencing_node(state: PlanningGraphState) -> dict[str, Any]:
        planning_state = state["planning_state"]
        try:
            report = resolved_sequencing_service.build_report(planning_state)
            planning_state.route_aware_sequencing_report = report

            settings = get_settings()
            if settings.route_aware_scheduling_enabled:
                applied = resolved_sequencing_service.apply_report(
                    planning_state,
                    report,
                    settings.route_aware_scheduling_min_improvement_seconds,
                )
                if applied:
                    rebuilt = resolved_route_feasibility_service.build_report(planning_state)
                    planning_state.route_feasibility_report = rebuilt
                    planning_state.provider_coverage.routes = _ROUTE_STATUS_TO_COVERAGE_VALUE.get(
                        rebuilt.status, "not_connected"
                    )
        except Exception:
            return {
                "failed_nodes": ["route_aware_sequencing"],
                "errors": [_safe_error("route_aware_sequencing")],
            }
        return {
            "planning_state": planning_state,
            "completed_nodes": ["route_aware_sequencing"],
        }

    return route_aware_sequencing_node


def build_travel_time_buffer_node(
    service: TravelTimeBufferService | None = None,
) -> PlanningGraphNode:
    """Wraps `TravelTimeBufferService.build_report` -- the same
    provider-backed travel-time buffer report
    `PlanningOrchestrator.run_experience_plan_stage` already computes (Step
    166C), after route feasibility/route-aware sequencing. Never reorders/
    drops a scheduled experience, never inserts a fake travel segment,
    never fabricates a duration/distance/buffer.
    """
    resolved_service = service or TravelTimeBufferService()

    def travel_time_buffer_node(state: PlanningGraphState) -> dict[str, Any]:
        planning_state = state["planning_state"]
        try:
            report = resolved_service.build_report(planning_state)
        except Exception:
            return {
                "failed_nodes": ["travel_time_buffer"],
                "errors": [_safe_error("travel_time_buffer")],
            }
        planning_state.travel_time_buffer_report = report
        return {"planning_state": planning_state, "completed_nodes": ["travel_time_buffer"]}

    return travel_time_buffer_node


def build_validation_node(
    service: PlanValidatorService | None = None,
) -> PlanningGraphNode:
    """Wraps `PlanValidatorService.run` -- never calls an LLM."""
    resolved_service = service or PlanValidatorService()

    def validation_node(state: PlanningGraphState) -> dict[str, Any]:
        try:
            planning_state = resolved_service.run(state["planning_state"])
        except Exception:
            return {
                "failed_nodes": ["validation"],
                "errors": [_safe_error("validation")],
            }
        return {"planning_state": planning_state, "completed_nodes": ["validation"]}

    return validation_node


def build_provider_coverage_node() -> PlanningGraphNode:
    """Provider-coverage checkpoint (docs/14_backend_architecture.md
    section 8). `ProviderCoverageService` has no single `run`/
    `build_report` entry point of its own -- every real stage service
    already calls `record_provider_result` as it goes, and the
    accommodation/flight/route inventory nodes above already set their own
    `ProviderCoverage` fields directly, so `planning_state.provider_coverage`
    is already up to date by the time this node runs. This node is
    therefore a pure, honest checkpoint: it never recomputes or invents
    coverage data, never calls a provider, and only records that the graph
    reached this point.
    """

    def provider_coverage_node(state: PlanningGraphState) -> dict[str, Any]:
        try:
            # Read-only sanity check only -- never mutates
            # planning_state.provider_coverage, never invents a value for
            # a field that isn't already there.
            _ = state["planning_state"].provider_coverage
        except Exception:
            return {
                "failed_nodes": ["provider_coverage"],
                "errors": [_safe_error("provider_coverage")],
            }
        return {"completed_nodes": ["provider_coverage"]}

    return provider_coverage_node


def build_final_state_node() -> PlanningGraphNode:
    """Terminal bookkeeping checkpoint. Never mutates `planning_state`
    itself -- it only reports an honest summary if earlier nodes failed
    (`warnings`), never a fabricated success claim. This is where a
    caller (`LangGraphPlanningService`/`PlanningOrchestrator.
    generate_full_plan_via_langgraph`) decides whether/how to persist the
    graph's `planning_state`; no node in this module ever persists
    anything itself.
    """

    def final_state_node(state: PlanningGraphState) -> dict[str, Any]:
        try:
            warnings: list[str] = []
            failed_nodes = state.get("failed_nodes") or []
            if failed_nodes:
                warnings.append(
                    "Some graph nodes failed during this run: "
                    f"{', '.join(failed_nodes)}. planning_state may be incomplete."
                )
        except Exception:
            return {
                "failed_nodes": ["final_state"],
                "errors": [_safe_error("final_state")],
            }
        return {"completed_nodes": ["final_state"], "warnings": warnings}

    return final_state_node
