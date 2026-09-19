from __future__ import annotations

from typing import Any, Callable

from app.core.config import get_settings
from app.graphs.planning_graph_state import PlanningGraphState
from app.models.accommodation import AccommodationSearchResult, AccommodationSearchStatus
from app.models.common import ProviderStatus
from app.models.flight import FlightSearchResult, FlightSearchStatus
from app.models.hotel_ratings import HotelRatingsStatus
from app.services.accommodation_inventory_service import AccommodationInventoryService
from app.services.ai_candidate_discovery_service import (
    AICandidateDiscoveryService,
    apply_discovery_to_state,
)
from app.services.ai_candidate_promotion_service import (
    AICandidatePromotionService,
    apply_promotion_safely,
)
from app.services.ai_itinerary_reasoning_service import (
    AIItineraryReasoningService,
    apply_itinerary_reasoning_safely,
)
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
# Every node here is a thin wrapper around exactly one already-existing
# service call -- no node duplicates a service's own logic, and no node
# imports/calls Groq/Anthropic/OpenAI or any other LLM client directly
# (enforced by this module's own `test_nodes_module_has_no_llm_or_network_
# imports`). As of Step 191A (docs/14_backend_architecture.md section
# 135), `build_ai_candidate_node`'s node is the one exception to "already-
# deterministic": when `Settings.ai_candidate_discovery_enabled` is
# explicitly `True` (default `False`), it can reach a real LLM -- but only
# through the same layered `AICandidateDiscoveryService` ->
# `AICandidateProposalProvider` -> Groq/Anthropic adapter abstraction the
# legacy engine already used, never a direct client import here. See that
# node's own docstring for the full gated behavior.
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
    promotion_service: AICandidatePromotionService | None = None,
    discovery_service: AICandidateDiscoveryService | None = None,
) -> PlanningGraphNode:
    """AI candidate discovery/grounding/promotion checkpoint (Section 170,
    made a real live LangGraph dependency in Step 191A,
    docs/14_backend_architecture.md section 135).

    Dual-gated, and the two gates are independent:

    1. `Settings.ai_candidate_discovery_enabled` (default `False`) is the
       Step 191A production switch this node itself reads at call time
       (never at graph-build time, so a config change takes effect on the
       next generation without restarting the process). It is a separate
       flag from the legacy-engine-only
       `Settings.ai_candidate_discovery_shadow_mode_enabled` -- this node
       never reads that one, and this flag never reinterprets it.

    2. Whether `promotion_service`/`discovery_service` were explicitly
       injected at graph-build time.

    With the live flag **off** (the default): if no `promotion_service`
    was injected either, this stays the exact pure no-op checkpoint it
    was before Step 191A -- never calls a provider/LLM, never mutates
    `planning_state`. If a `promotion_service` *was* explicitly injected
    (test-only, used by `test_langgraph_planning_graph.py`/
    `test_langgraph_planning_service.py` to prove graph-node ordering),
    this still calls its `apply_promotion` directly, exactly as it always
    has -- this specific pre-191A behavior is unchanged.

    With the live flag **on**: this node composes
    `app.services.ai_candidate_discovery_service.apply_discovery_to_state`
    (proposal -> grounding, stored onto
    `ai_candidate_proposal_batch`/`candidate_grounding_batch`) followed by
    `app.services.ai_candidate_promotion_service.apply_promotion_safely`
    (eligibility review -> promotion, stored onto
    `ai_candidate_promotion_report`) -- the exact same shared, fail-safe
    implementations `PlanningOrchestrator`'s legacy shadow stage also
    calls, so this node never duplicates that orchestration logic.
    `discovery_service`/`promotion_service` default to constructing the
    real service only in this branch, at call time -- never eagerly, and
    never a second, separate AI candidate pipeline.

    This node never imports `app.providers.*`/Groq/Anthropic/OpenAI
    directly (enforced by this module's own
    `test_nodes_module_has_no_llm_or_network_imports`) -- its only path to
    a real LLM is `AICandidateDiscoveryService` -> the injected/factory-
    resolved `AICandidateProposalProvider` -> a Groq/Anthropic adapter,
    preserving the same layered "node -> service -> provider abstraction
    -> adapter" structure every other node in this file already uses.

    Fails safe end to end: `apply_discovery_to_state`/
    `apply_promotion_safely` never raise -- a provider timeout, missing
    API key, invalid/malformed LLM output, or any other unexpected
    failure is caught inside them and logged safely (never a fabricated
    proposal, grounded, or promoted candidate). This node's own
    `try`/`except` below is defense-in-depth only, mirroring every other
    node in this file.

    Grounding stays mandatory regardless of this flag: only candidates
    `CandidateGroundingService.ground` actually matched against real
    `PlanningState.destination_context` provider candidates can ever reach
    `ai_candidate_promotion_report.promoted_candidates` -- an AI proposal
    naming a place with no real-world provider match is rejected, never
    promoted, and never reaches `ExperiencePlannerService` (which, since
    Step 170D and unchanged by this step, already merges any already-
    promoted candidates it finds on `ai_candidate_promotion_report` into
    scheduling -- this node only has to populate that report early enough,
    which its position before `trip_strategy`/`experience_planning` in the
    graph already guarantees).

    `POST /trips/{trip_id}/ai-candidate-review` and
    `POST /trips/{trip_id}/ai-candidate-promotions` remain fully available
    and unaffected regardless of which engine generated the plan or
    whether the live flag is on.
    """

    def ai_candidate_node(state: PlanningGraphState) -> dict[str, Any]:
        planning_state = state["planning_state"]

        if not get_settings().ai_candidate_discovery_enabled:
            if promotion_service is None:
                return {"completed_nodes": ["ai_candidate"]}
            try:
                planning_state = promotion_service.apply_promotion(planning_state)
            except Exception:
                return {
                    "failed_nodes": ["ai_candidate"],
                    "errors": [_safe_error("ai_candidate")],
                }
            return {"planning_state": planning_state, "completed_nodes": ["ai_candidate"]}

        resolved_discovery_service = discovery_service or AICandidateDiscoveryService()
        resolved_promotion_service = promotion_service or AICandidatePromotionService()
        try:
            planning_state = apply_discovery_to_state(
                planning_state,
                resolved_discovery_service,
                stage_label="ai_candidate_discovery",
            )
            # Mirrors PlanningOrchestrator._run_ai_candidate_promotion_stage's
            # own guard exactly: no proposal batch means there is nothing
            # real to review/promote, so ai_candidate_promotion_report stays
            # whatever it already was (None on a fresh generation) instead
            # of storing a technically-honest-but-pointless empty report.
            if planning_state.ai_candidate_proposal_batch is not None:
                planning_state = apply_promotion_safely(planning_state, resolved_promotion_service)
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


def build_ai_itinerary_reasoning_node(
    service: AIItineraryReasoningService | None = None,
) -> PlanningGraphNode:
    """Wraps `AIItineraryReasoningService.apply` (Section 193C,
    docs/14_backend_architecture.md section 143) -- the first real,
    live LLM #2 dependency in this graph, positioned right before
    `experience_planning` so `ExperiencePlannerService.run` can consume
    `planning_state.ai_itinerary_reasoning_result` if it completed.

    Gated entirely by `Settings.ai_itinerary_reasoning_enabled` (default
    `False`), read fresh inside `AIItineraryReasoningService.reason` on
    every call -- not by which service is injected here. With the flag
    off (the default), `reason` returns an honest `not_connected` result
    without ever resolving a provider or making a network call, so this
    node stays a safe, cheap no-op for `ExperiencePlannerService`'s own
    purposes (it only ever consumes a `completed` result).

    This node's only path to a real LLM is
    `AIItineraryReasoningService` -> the injected/factory-resolved
    `AIItineraryReasoningProvider` -> a Groq/Anthropic adapter --
    preserving the same layered "node -> service -> provider abstraction
    -> adapter" structure every other node in this file already uses.
    Never imports `app.providers.*`/Groq/Anthropic/OpenAI directly
    (enforced by this module's own
    `test_nodes_module_has_no_llm_or_network_imports`).

    Fails safe end to end via `apply_itinerary_reasoning_safely`, which
    never raises -- a provider timeout, missing API key, invalid/
    malformed LLM output, or a semantic candidate-ID safety violation is
    all caught and honestly recorded (never a fabricated day plan). This
    node's own `try`/`except` below is defense-in-depth only, mirroring
    every other node in this file. A caught failure here never blocks
    generation: `ExperiencePlannerService`'s own deterministic path runs
    exactly as it always has whenever `ai_itinerary_reasoning_result` is
    absent or not `completed`.
    """
    resolved_service = service or AIItineraryReasoningService()

    def ai_itinerary_reasoning_node(state: PlanningGraphState) -> dict[str, Any]:
        try:
            planning_state = apply_itinerary_reasoning_safely(
                state["planning_state"], resolved_service
            )
        except Exception:
            return {
                "failed_nodes": ["ai_itinerary_reasoning"],
                "errors": [_safe_error("ai_itinerary_reasoning")],
            }
        return {"planning_state": planning_state, "completed_nodes": ["ai_itinerary_reasoning"]}

    return ai_itinerary_reasoning_node


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
