from __future__ import annotations

import ast
import datetime
import inspect
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.graphs.planning_graph as planning_graph_module
import app.services.planning_orchestrator as orchestrator_module
from app.graphs import PlanningGraphRunner, build_planning_graph, run_planning_graph
from app.models.ai_candidate_proposal import (
    AICandidateProposalGuardrailReport,
    AICandidateProposalRequest,
    AICandidateProposalResult,
    AICandidateProposalStatus,
    AICandidateProposalTask,
    AICandidateType,
    AICandidateVerificationRequirement,
)
from app.models.candidate_grounding import (
    CandidateGroundingConfidenceTier,
    CandidateGroundingEvidence,
    CandidateGroundingGuardrailReport,
    CandidateGroundingMatchType,
    CandidateGroundingRejectReason,
    CandidateGroundingRequest,
    CandidateGroundingResult,
    CandidateGroundingStatus,
    GroundedCandidate,
    RejectedCandidateProposal,
)
from app.models.candidate_quality import CandidateQualityReport
from app.models.common import DataStatus, GeoPoint
from app.models.planning_state import (
    DestinationContext,
    PlanningState,
    TravelGroupType,
    TripRequest,
)
from app.repositories.planning_state_repository import PlanningStateRepository
from app.repositories.trip_repository import TripRepository
from app.services.ai_candidate_discovery_service import (
    AICandidateDiscoveryDryRunResult,
    AICandidateDiscoveryService,
)
from app.services.candidate_quality_service import CandidateQualityService
from app.services.experience_planner_service import ExperiencePlannerService

# Tests for the LangGraph skeleton around the deterministic planning
# pipeline (Step 162B, extended with a real AI candidate shadow node in
# Step 162C, docs/13_llm_reasoning_pipeline.md sections 42-43). Every test
# here uses only in-file fake stage-service doubles -- no real
# provider/network call, no real LLM call (Groq/Anthropic/other), and no
# persistence. This module is architecture/resume foundation only: it does
# not replace `PlanningOrchestrator.generate_full_plan`, is not imported by
# any API route, and does not change `/trips/{trip_id}/generate` behavior.


class _FakeStageService:
    """Deterministic test double for any single-method (`run`) stage
    service. Never calls a provider, LLM, or network -- just marks that it
    ran on the planning state, so tests can assert per-node mutation
    without depending on any real stage service's actual logic.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.call_count = 0

    def run(self, planning_state: PlanningState) -> PlanningState:
        self.call_count += 1
        planning_state.data_sources_used = list(planning_state.data_sources_used) + [
            f"fake_{self.name}"
        ]
        return planning_state


class _FakeDestinationContextServiceWithContext:
    """Like `_FakeStageService`, but also sets `destination_context` --
    needed to exercise the "shadow mode enabled and destination_context
    exists" path without any real provider call.
    """

    def __init__(self) -> None:
        self.call_count = 0

    def run(self, planning_state: PlanningState) -> PlanningState:
        self.call_count += 1
        planning_state.data_sources_used = list(planning_state.data_sources_used) + [
            "fake_destination_context"
        ]
        planning_state.destination_context = DestinationContext(
            destination_name=planning_state.trip_request.primary_destination,
        )
        return planning_state


class _FakeCandidateQualityService:
    """Deterministic test double for `CandidateQualityService.build_report`."""

    def __init__(self) -> None:
        self.call_count = 0

    def build_report(self, planning_state: PlanningState) -> CandidateQualityReport:
        self.call_count += 1
        return CandidateQualityReport(
            destination_name=planning_state.trip_request.primary_destination,
            generated_at=datetime.datetime.now(datetime.timezone.utc),
        )


class _FakeAICandidateDiscoveryService:
    """Deterministic test double for `AICandidateDiscoveryService.dry_run`.
    Returns a canned `AICandidateDiscoveryDryRunResult` built from real
    models (never a dict), or raises a configured exception -- never a real
    network/LLM call either way.
    """

    def __init__(
        self,
        result: AICandidateDiscoveryDryRunResult | None = None,
        raises: BaseException | None = None,
    ) -> None:
        self._result = result
        self._raises = raises
        self.call_count = 0

    def dry_run(
        self,
        planning_state: PlanningState,
        task: AICandidateProposalTask = AICandidateProposalTask.DESTINATION_CANDIDATE_DISCOVERY,
        max_candidates: int = 15,
    ) -> AICandidateDiscoveryDryRunResult:
        self.call_count += 1
        if self._raises is not None:
            raise self._raises
        assert self._result is not None
        return self._result


def _trip_request(**overrides: Any) -> TripRequest:
    fields: dict[str, Any] = {
        "primary_destination": "Testville, Testland",
        "origin_city": "Home City",
        "start_date": "2026-08-10",
        "end_date": "2026-08-12",
        "travelers_count": 2,
        "travel_group_type": TravelGroupType.COUPLE,
    }
    fields.update(overrides)
    return TripRequest(**fields)


def _planning_state() -> PlanningState:
    return PlanningState(trip_request=_trip_request())


def _fake_services() -> dict[str, Any]:
    return {
        "traveler_profile_service": _FakeStageService("traveler_profile"),
        "destination_context_service": _FakeStageService("destination_context"),
        "candidate_quality_service": _FakeCandidateQualityService(),
        "ai_candidate_discovery_service": _FakeAICandidateDiscoveryService(
            raises=AssertionError(
                "AICandidateDiscoveryService.dry_run must not be called unless shadow "
                "mode is enabled and destination_context exists"
            )
        ),
        "trip_strategy_service": _FakeStageService("trip_strategy"),
        "stay_transport_service": _FakeStageService("stay_transport"),
        "experience_planner_service": _FakeStageService("experience_plan"),
        "plan_validator_service": _FakeStageService("validation"),
    }


def _create_trip_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "destination_scope": "single_city",
        "primary_destination": "Testville, Testland",
        "origin_city": "Home City",
        "start_date": "2026-08-10",
        "end_date": "2026-08-12",
        "travelers_count": 2,
        "travel_group_type": "couple",
    }
    payload.update(overrides)
    return payload


def _proposal_request(**overrides: Any) -> AICandidateProposalRequest:
    fields: dict[str, Any] = {
        "task": AICandidateProposalTask.DESTINATION_CANDIDATE_DISCOVERY,
        "trip_id": "trip_001",
        "destination_name": "Testville, Testland",
        "trip_duration_days": 3,
    }
    fields.update(overrides)
    return AICandidateProposalRequest(**fields)


def _grounding_request(**overrides: Any) -> CandidateGroundingRequest:
    fields: dict[str, Any] = {
        "trip_id": "trip_001",
        "destination_name": "Testville, Testland",
    }
    fields.update(overrides)
    return CandidateGroundingRequest(**fields)


def _not_connected_dry_run_result() -> AICandidateDiscoveryDryRunResult:
    """Mirrors the default not_connected proposal provider's output paired
    with a skipped grounding result -- built entirely from real models, the
    same shape `AICandidateDiscoveryService.dry_run` produces by default.
    """
    return AICandidateDiscoveryDryRunResult(
        proposal_request=_proposal_request(),
        proposal_result=AICandidateProposalResult(
            task=AICandidateProposalTask.DESTINATION_CANDIDATE_DISCOVERY,
            status=AICandidateProposalStatus.NOT_CONNECTED,
            proposals=[],
            guardrail_report=AICandidateProposalGuardrailReport(
                passed=False,
                blocked_reasons=["AI candidate proposal provider is not connected yet."],
            ),
            confidence=0.0,
        ),
        grounding_request=_grounding_request(),
        grounding_result=CandidateGroundingResult(
            status=CandidateGroundingStatus.SKIPPED,
            grounded_candidates=[],
            rejected_proposals=[],
            guardrail_report=CandidateGroundingGuardrailReport(
                passed=False,
                blocked_reasons=["No proposals were supplied to ground."],
            ),
            confidence=0.0,
        ),
    )


def _grounded_candidate(**overrides: Any) -> GroundedCandidate:
    fields: dict[str, Any] = {
        "grounding_id": "grounding_001",
        "proposal_id": "proposal_001",
        "candidate_name": "Old Town Waterfront",
        "candidate_type": AICandidateType.NEIGHBORHOOD,
        "matched_name": "Old Town Waterfront",
        "confidence_tier": CandidateGroundingConfidenceTier.HIGH,
        "confidence": 0.8,
        "evidence": CandidateGroundingEvidence(
            provider_name="openstreetmap_places",
            provider_place_id="way/12345",
            matched_name="Old Town Waterfront",
            match_type=CandidateGroundingMatchType.EXACT_NAME,
            coordinates=GeoPoint(lat=38.7223, lng=-9.1393),
            data_status=DataStatus.LIVE,
            confidence=0.8,
        ),
        "verification_requirements_satisfied": [
            AICandidateVerificationRequirement.MUST_GROUND_BY_NAME_AND_LOCATION
        ],
    }
    fields.update(overrides)
    return GroundedCandidate(**fields)


def _completed_dry_run_result() -> AICandidateDiscoveryDryRunResult:
    proposal = {
        "proposal_id": "proposal_001",
        "candidate_name": "Old Town Waterfront",
        "candidate_type": "neighborhood",
        "why_consider": "Locally known for evening walks, may be under-tagged in provider data.",
        "verification_requirements": ["must_ground_by_name_and_location"],
        "confidence": 0.6,
    }
    from app.models.ai_candidate_proposal import AICandidateProposal

    return AICandidateDiscoveryDryRunResult(
        proposal_request=_proposal_request(),
        proposal_result=AICandidateProposalResult(
            task=AICandidateProposalTask.DESTINATION_CANDIDATE_DISCOVERY,
            status=AICandidateProposalStatus.COMPLETED,
            proposals=[AICandidateProposal(**proposal)],
            guardrail_report=AICandidateProposalGuardrailReport(passed=True),
            provider_name="fake_ai_candidate_proposal_provider",
            confidence=0.6,
        ),
        grounding_request=_grounding_request(),
        grounding_result=CandidateGroundingResult(
            status=CandidateGroundingStatus.COMPLETED,
            grounded_candidates=[_grounded_candidate()],
            rejected_proposals=[],
            guardrail_report=CandidateGroundingGuardrailReport(passed=True),
            confidence=0.8,
        ),
    )


def _partial_dry_run_result() -> AICandidateDiscoveryDryRunResult:
    result = _completed_dry_run_result()
    partial_grounding = CandidateGroundingResult(
        status=CandidateGroundingStatus.PARTIAL,
        grounded_candidates=[_grounded_candidate()],
        rejected_proposals=[
            RejectedCandidateProposal(
                proposal_id="proposal_002",
                candidate_name="Unmatched Idea",
                candidate_type=AICandidateType.ATTRACTION,
                reject_reason=CandidateGroundingRejectReason.NO_PROVIDER_MATCH,
                message="No supplied provider candidate matched this proposal by name.",
            )
        ],
        guardrail_report=CandidateGroundingGuardrailReport(passed=True),
        confidence=0.7,
    )
    return result.model_copy(update={"grounding_result": partial_grounding})


# ---------------------------------------------------------------------------
# 1. The graph compiles.
# ---------------------------------------------------------------------------


def test_planning_graph_compiles() -> None:
    runner = PlanningGraphRunner(**_fake_services())
    assert runner._graph is not None


def test_build_planning_graph_compiles_directly() -> None:
    services = _fake_services()
    graph = build_planning_graph(
        services["traveler_profile_service"],
        services["destination_context_service"],
        services["candidate_quality_service"],
        services["ai_candidate_discovery_service"],
        services["trip_strategy_service"],
        services["stay_transport_service"],
        services["experience_planner_service"],
        services["plan_validator_service"],
    )
    assert graph is not None


# ---------------------------------------------------------------------------
# 9. Nodes run in the exact expected order.
# ---------------------------------------------------------------------------


def test_graph_runs_nodes_in_exact_expected_order() -> None:
    services = _fake_services()
    runner = PlanningGraphRunner(**services)

    initial_state = {"planning_state": _planning_state(), "executed_nodes": [], "errors": []}
    result = runner._graph.invoke(initial_state)

    assert result["executed_nodes"] == [
        "traveler_profile",
        "destination_context",
        "candidate_quality",
        "ai_candidate_shadow",
        "trip_strategy",
        "stay_transport",
        "experience_plan",
        "validation",
    ]


# ---------------------------------------------------------------------------
# The graph returns a PlanningState.
# ---------------------------------------------------------------------------


def test_run_returns_a_planning_state() -> None:
    runner = PlanningGraphRunner(**_fake_services())
    result = runner.run(_planning_state())
    assert isinstance(result, PlanningState)


def test_run_planning_graph_convenience_function_returns_a_planning_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uses the module-level convenience function, but with a
    PlanningGraphRunner constructed from fakes injected via monkeypatching
    the class itself -- still no real provider/network call.
    """
    fake_runner = PlanningGraphRunner(**_fake_services())
    monkeypatch.setattr(
        planning_graph_module, "PlanningGraphRunner", lambda: fake_runner
    )

    result = run_planning_graph(_planning_state())

    assert isinstance(result, PlanningState)


# ---------------------------------------------------------------------------
# Nodes call injected fake services, never real ones.
# ---------------------------------------------------------------------------


def test_nodes_call_injected_fake_services_not_real_ones() -> None:
    services = _fake_services()
    runner = PlanningGraphRunner(**services)

    runner.run(_planning_state())

    assert services["traveler_profile_service"].call_count == 1
    assert services["destination_context_service"].call_count == 1
    assert services["candidate_quality_service"].call_count == 1
    assert services["trip_strategy_service"].call_count == 1
    assert services["stay_transport_service"].call_count == 1
    assert services["experience_planner_service"].call_count == 1
    assert services["plan_validator_service"].call_count == 1
    # Shadow mode is disabled by default -- the discovery service fake
    # (configured to raise if called) must never have been invoked.
    assert services["ai_candidate_discovery_service"].call_count == 0


def test_fake_service_mutations_are_visible_on_final_planning_state() -> None:
    runner = PlanningGraphRunner(**_fake_services())
    result = runner.run(_planning_state())

    assert result.data_sources_used == [
        "fake_traveler_profile",
        "fake_destination_context",
        "fake_trip_strategy",
        "fake_stay_transport",
        "fake_experience_plan",
        "fake_validation",
    ]
    assert result.candidate_quality_report is not None


# ---------------------------------------------------------------------------
# 1. Default graph run keeps AI batches unchanged when shadow mode is
#    disabled (the conftest autouse fixture already forces
#    AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED=false for every test; this
#    also checks the explicit shadow_mode_enabled=False override path).
# ---------------------------------------------------------------------------


def test_default_shadow_mode_leaves_batches_unchanged() -> None:
    runner = PlanningGraphRunner(**_fake_services())
    planning_state = _planning_state()
    assert planning_state.ai_candidate_proposal_batch is None
    assert planning_state.candidate_grounding_batch is None

    result = runner.run(planning_state)

    assert result.ai_candidate_proposal_batch is None
    assert result.candidate_grounding_batch is None


def test_explicit_shadow_mode_disabled_leaves_batches_unchanged() -> None:
    services = _fake_services()
    runner = PlanningGraphRunner(shadow_mode_enabled=False, **services)

    result = runner.run(_planning_state())

    assert result.ai_candidate_proposal_batch is None
    assert result.candidate_grounding_batch is None
    assert services["ai_candidate_discovery_service"].call_count == 0


def test_ai_candidate_shadow_node_still_appears_in_executed_nodes_when_disabled() -> None:
    runner = PlanningGraphRunner(**_fake_services())
    initial_state = {"planning_state": _planning_state(), "executed_nodes": [], "errors": []}
    result = runner._graph.invoke(initial_state)
    assert "ai_candidate_shadow" in result["executed_nodes"]
    assert result["errors"] == []


# ---------------------------------------------------------------------------
# 2. Shadow node is a no-op when destination_context is missing, even with
#    shadow mode explicitly enabled.
# ---------------------------------------------------------------------------


def test_shadow_node_is_noop_when_destination_context_missing() -> None:
    services = _fake_services()  # destination_context_service fake never sets destination_context
    runner = PlanningGraphRunner(shadow_mode_enabled=True, **services)

    result = runner.run(_planning_state())

    assert result.destination_context is None
    assert result.ai_candidate_proposal_batch is None
    assert result.candidate_grounding_batch is None
    assert services["ai_candidate_discovery_service"].call_count == 0


# ---------------------------------------------------------------------------
# 3-4. Shadow mode enabled + destination_context exists -> calls the
#      injected fake discovery service exactly once and stores both
#      batches from its result.
# ---------------------------------------------------------------------------


def test_shadow_node_calls_discovery_service_exactly_once_when_enabled() -> None:
    services = _fake_services()
    services["destination_context_service"] = _FakeDestinationContextServiceWithContext()
    services["ai_candidate_discovery_service"] = _FakeAICandidateDiscoveryService(
        result=_completed_dry_run_result()
    )
    runner = PlanningGraphRunner(shadow_mode_enabled=True, **services)

    runner.run(_planning_state())

    assert services["ai_candidate_discovery_service"].call_count == 1


def test_shadow_node_stores_batches_from_fake_completed_result() -> None:
    services = _fake_services()
    services["destination_context_service"] = _FakeDestinationContextServiceWithContext()
    services["ai_candidate_discovery_service"] = _FakeAICandidateDiscoveryService(
        result=_completed_dry_run_result()
    )
    runner = PlanningGraphRunner(shadow_mode_enabled=True, **services)

    result = runner.run(_planning_state())

    assert result.ai_candidate_proposal_batch is not None
    assert result.ai_candidate_proposal_batch.result.status == AICandidateProposalStatus.COMPLETED
    assert len(result.ai_candidate_proposal_batch.result.proposals) == 1
    assert result.candidate_grounding_batch is not None
    assert result.candidate_grounding_batch.result.status == CandidateGroundingStatus.COMPLETED
    assert len(result.candidate_grounding_batch.result.grounded_candidates) == 1


# ---------------------------------------------------------------------------
# 5. Fake not_connected dry_run result -> stores not_connected/skipped
#    batches when enabled.
# ---------------------------------------------------------------------------


def test_shadow_node_stores_not_connected_and_skipped_batches_when_enabled() -> None:
    services = _fake_services()
    services["destination_context_service"] = _FakeDestinationContextServiceWithContext()
    services["ai_candidate_discovery_service"] = _FakeAICandidateDiscoveryService(
        result=_not_connected_dry_run_result()
    )
    runner = PlanningGraphRunner(shadow_mode_enabled=True, **services)

    result = runner.run(_planning_state())

    assert result.ai_candidate_proposal_batch is not None
    assert result.ai_candidate_proposal_batch.result.status == AICandidateProposalStatus.NOT_CONNECTED
    assert result.ai_candidate_proposal_batch.result.proposals == []
    assert result.candidate_grounding_batch is not None
    assert result.candidate_grounding_batch.result.status == CandidateGroundingStatus.SKIPPED
    assert result.candidate_grounding_batch.result.grounded_candidates == []


# ---------------------------------------------------------------------------
# 6. Fake completed/partial dry_run results -> stores validated batches.
# ---------------------------------------------------------------------------


def test_shadow_node_stores_partial_grounding_batch_when_enabled() -> None:
    services = _fake_services()
    services["destination_context_service"] = _FakeDestinationContextServiceWithContext()
    services["ai_candidate_discovery_service"] = _FakeAICandidateDiscoveryService(
        result=_partial_dry_run_result()
    )
    runner = PlanningGraphRunner(shadow_mode_enabled=True, **services)

    result = runner.run(_planning_state())

    assert result.candidate_grounding_batch is not None
    grounding_result = result.candidate_grounding_batch.result
    assert grounding_result.status == CandidateGroundingStatus.PARTIAL
    assert len(grounding_result.grounded_candidates) == 1
    assert len(grounding_result.rejected_proposals) == 1


# ---------------------------------------------------------------------------
# 7-8. Shadow node exception is swallowed fail-safe; the graph still
#      completes, and nothing else about the plan changes.
# ---------------------------------------------------------------------------


def test_shadow_node_exception_is_swallowed_and_graph_still_completes() -> None:
    services = _fake_services()
    services["destination_context_service"] = _FakeDestinationContextServiceWithContext()
    services["ai_candidate_discovery_service"] = _FakeAICandidateDiscoveryService(
        raises=RuntimeError("simulated discovery failure")
    )
    runner = PlanningGraphRunner(shadow_mode_enabled=True, **services)

    result = runner.run(_planning_state())  # must not raise

    assert result.ai_candidate_proposal_batch is None
    assert result.candidate_grounding_batch is None
    # The full pipeline still ran to completion after the shadow node.
    assert services["trip_strategy_service"].call_count == 1
    assert services["experience_planner_service"].call_count == 1
    assert services["plan_validator_service"].call_count == 1


def test_shadow_node_exception_appends_safe_error_marker_only() -> None:
    services = _fake_services()
    services["destination_context_service"] = _FakeDestinationContextServiceWithContext()
    services["ai_candidate_discovery_service"] = _FakeAICandidateDiscoveryService(
        raises=RuntimeError("simulated failure containing a fake secret sk-ant-should-not-leak")
    )
    runner = PlanningGraphRunner(shadow_mode_enabled=True, **services)

    initial_state = {"planning_state": _planning_state(), "executed_nodes": [], "errors": []}
    result = runner._graph.invoke(initial_state)

    assert len(result["errors"]) == 1
    # The marker is generic and never echoes the raw exception text -- no
    # secret/prompt/response fragment can leak through it.
    assert "sk-ant-should-not-leak" not in result["errors"][0]
    assert "simulated failure" not in result["errors"][0]


def test_shadow_node_exception_path_does_not_change_other_plan_fields() -> None:
    """Compares the same trip run through the graph twice -- once with a
    raising fake discovery service (shadow enabled), once with shadow mode
    disabled entirely -- and asserts every other observable field the fake
    services touch is identical either way.
    """

    def _run(shadow_enabled: bool, discovery_service: Any) -> PlanningState:
        services = _fake_services()
        services["destination_context_service"] = _FakeDestinationContextServiceWithContext()
        services["ai_candidate_discovery_service"] = discovery_service
        runner = PlanningGraphRunner(shadow_mode_enabled=shadow_enabled, **services)
        return runner.run(_planning_state())

    disabled_result = _run(False, _FakeAICandidateDiscoveryService(raises=AssertionError("unused")))
    enabled_raising_result = _run(
        True, _FakeAICandidateDiscoveryService(raises=RuntimeError("simulated failure"))
    )

    assert disabled_result.data_sources_used == enabled_raising_result.data_sources_used
    # generated_at is a fresh timestamp per run, deliberately excluded.
    disabled_report = disabled_result.candidate_quality_report.model_dump(exclude={"generated_at"})
    enabled_report = enabled_raising_result.candidate_quality_report.model_dump(
        exclude={"generated_at"}
    )
    assert disabled_report == enabled_report
    assert enabled_raising_result.ai_candidate_proposal_batch is None
    assert enabled_raising_result.candidate_grounding_batch is None


# ---------------------------------------------------------------------------
# 10. Experience planner never receives AI proposals as a scheduling input
#     -- structurally (its `run` signature takes only `planning_state`) and
#     behaviorally (it is called exactly once, after the shadow node,
#     regardless of what the shadow node stored).
# ---------------------------------------------------------------------------


def test_experience_planner_run_signature_has_no_ai_proposal_channel() -> None:
    signature = inspect.signature(ExperiencePlannerService.run)
    assert list(signature.parameters.keys()) == ["self", "planning_state"]


def test_experience_planner_fake_called_once_regardless_of_shadow_result() -> None:
    services = _fake_services()
    services["destination_context_service"] = _FakeDestinationContextServiceWithContext()
    services["ai_candidate_discovery_service"] = _FakeAICandidateDiscoveryService(
        result=_completed_dry_run_result()
    )
    runner = PlanningGraphRunner(shadow_mode_enabled=True, **services)

    runner.run(_planning_state())

    assert services["experience_planner_service"].call_count == 1


# ---------------------------------------------------------------------------
# 11. CandidateQualityService runs before the AI shadow node, and cannot
#     consume grounded AI candidates -- structurally (its `build_report`
#     signature takes only `planning_state`) and by node order.
# ---------------------------------------------------------------------------


def test_candidate_quality_build_report_signature_has_no_grounded_candidate_channel() -> None:
    signature = inspect.signature(CandidateQualityService.build_report)
    assert list(signature.parameters.keys()) == ["self", "planning_state"]


def test_candidate_quality_runs_before_ai_shadow_node_in_executed_order() -> None:
    services = _fake_services()
    runner = PlanningGraphRunner(**services)
    initial_state = {"planning_state": _planning_state(), "executed_nodes": [], "errors": []}

    result = runner._graph.invoke(initial_state)

    executed = result["executed_nodes"]
    assert executed.index("candidate_quality") < executed.index("ai_candidate_shadow")


# ---------------------------------------------------------------------------
# 12. Graph runner never saves to any repository, even with shadow mode
#     enabled and a completed fake result.
# ---------------------------------------------------------------------------


def test_graph_runner_never_saves_to_planning_state_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fail(self: PlanningStateRepository, planning_state: object) -> None:
        raise AssertionError("PlanningGraphRunner.run must never save to PlanningStateRepository")

    monkeypatch.setattr(PlanningStateRepository, "save", _fail)

    services = _fake_services()
    services["destination_context_service"] = _FakeDestinationContextServiceWithContext()
    services["ai_candidate_discovery_service"] = _FakeAICandidateDiscoveryService(
        result=_completed_dry_run_result()
    )
    runner = PlanningGraphRunner(shadow_mode_enabled=True, **services)
    runner.run(_planning_state())  # must not raise


def test_graph_runner_never_saves_to_trip_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail(self: TripRepository, trip_id: object) -> None:
        raise AssertionError("PlanningGraphRunner.run must never call TripRepository.create")

    monkeypatch.setattr(TripRepository, "create", _fail)

    runner = PlanningGraphRunner(**_fake_services())
    runner.run(_planning_state())  # must not raise


# ---------------------------------------------------------------------------
# 13-14. Graph module is not imported by API routes or PlanningOrchestrator;
#        generate_full_plan is unchanged/not using the graph.
# ---------------------------------------------------------------------------


def _imported_names(nodes: list[ast.stmt]) -> list[str]:
    names: list[str] = []
    for node in nodes:
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_planning_orchestrator_does_not_import_graph_module() -> None:
    source = inspect.getsource(orchestrator_module)
    tree = ast.parse(source)
    all_names = _imported_names(list(ast.walk(tree)))  # type: ignore[arg-type]
    assert not any("app.graphs" in name for name in all_names)
    assert "planning_graph" not in source
    assert "PlanningGraphRunner" not in source


def test_planning_orchestrator_generate_full_plan_unchanged_by_graph() -> None:
    source = inspect.getsource(orchestrator_module.PlanningOrchestrator.generate_full_plan)
    assert "graph" not in source.lower()


def test_trips_route_does_not_import_graph_module() -> None:
    import app.api.routes.trips as trips_route_module

    source = inspect.getsource(trips_route_module)
    tree = ast.parse(source)
    all_names = _imported_names(list(ast.walk(tree)))  # type: ignore[arg-type]
    assert not any("app.graphs" in name for name in all_names)
    assert "planning_graph" not in source
    assert "PlanningGraphRunner" not in source


# ---------------------------------------------------------------------------
# 15. Existing /generate endpoint behavior remains unchanged.
# ---------------------------------------------------------------------------


def test_generate_endpoint_behavior_unchanged(client: TestClient) -> None:
    create_response = client.post("/trips", json=_create_trip_payload())
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200
    body = generate_response.json()
    assert body["success"] is True
    planning_state = body["data"]["planning_state"]
    assert planning_state["experience_plan"] is not None
    assert planning_state["validation_report"] is not None
    # The graph is not wired into generation -- these stay exactly as
    # before this step (default AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED
    # is false in the ambient test environment, per conftest's autouse
    # isolation fixture).
    assert planning_state["ai_candidate_proposal_batch"] is None
    assert planning_state["candidate_grounding_batch"] is None


# ---------------------------------------------------------------------------
# 16-18. No Groq/Anthropic/Kiwi/MCP/scraping calls are made -- static check.
# ---------------------------------------------------------------------------


def test_graph_module_has_no_disallowed_vendor_or_scraping_imports() -> None:
    source = inspect.getsource(planning_graph_module)
    tree = ast.parse(source)

    disallowed_substrings = (
        "groq",
        "anthropic",
        "openai",
        "gemini",
        "google.generativeai",
        "kiwi",
        "mcp",
        "requests",
        "beautifulsoup",
        "scrapy",
        "selenium",
        "playwright",
        "claude_code",
    )

    all_names = _imported_names(list(ast.walk(tree)))  # type: ignore[arg-type]
    for name in all_names:
        lowered = name.lower()
        for disallowed in disallowed_substrings:
            assert disallowed not in lowered, f"Disallowed import found: {name}"


def test_graph_module_never_imports_a_vendor_llm_sdk_directly() -> None:
    """Checks actual imports (never a raw substring scan, which would also
    flag this module's own explanatory comments about what the shadow node
    deliberately does *not* do). The graph module is allowed to import
    `AICandidateDiscoveryService` itself -- it must never import a vendor
    SDK or a concrete Groq/Anthropic provider adapter directly.
    """
    source = inspect.getsource(planning_graph_module)
    tree = ast.parse(source)
    all_names = _imported_names(list(ast.walk(tree)))  # type: ignore[arg-type]

    assert not any("groq_adapter" in name.lower() for name in all_names)
    assert not any("anthropic_adapter" in name.lower() for name in all_names)
    assert not any(name.lower() in ("groq", "anthropic", "langchain_groq") for name in all_names)


def test_no_real_groq_or_anthropic_api_call_when_shadow_enabled_with_fakes() -> None:
    """End-to-end sanity check: even with shadow mode enabled and a
    completed fake result, the only "provider" ever touched is the injected
    fake -- no real AICandidateProposalProvider (Groq/Anthropic) is
    constructed or called anywhere in this path.
    """
    services = _fake_services()
    services["destination_context_service"] = _FakeDestinationContextServiceWithContext()
    fake_discovery = _FakeAICandidateDiscoveryService(result=_completed_dry_run_result())
    services["ai_candidate_discovery_service"] = fake_discovery
    runner = PlanningGraphRunner(shadow_mode_enabled=True, **services)

    runner.run(_planning_state())  # must return instantly, never attempt real network I/O

    assert fake_discovery.call_count == 1


# ---------------------------------------------------------------------------
# 19. No LangSmith runtime import/configuration is added.
# ---------------------------------------------------------------------------


def test_graph_module_has_no_langsmith_import_or_configuration() -> None:
    source = inspect.getsource(planning_graph_module)
    tree = ast.parse(source)

    all_names = _imported_names(list(ast.walk(tree)))  # type: ignore[arg-type]
    assert not any("langsmith" in name.lower() for name in all_names)

    # No LangSmith tracing env var configuration either.
    for needle in ("LANGCHAIN_TRACING", "LANGSMITH_API_KEY", "LANGCHAIN_API_KEY"):
        assert needle not in source


# ---------------------------------------------------------------------------
# 20. Frontend remains untouched by this step.
# ---------------------------------------------------------------------------


def test_no_frontend_files_reference_planning_graph() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    needles = (
        "planning_graph",
        "PlanningGraphRunner",
        "PlanningGraphState",
        "langgraph",
        "ai_candidate_shadow_node",
    )
    for subdir in ("frontend/app", "frontend/lib"):
        target = repo_root / subdir
        if not target.exists():
            continue
        for path in target.rglob("*"):
            if not path.is_file() or "node_modules" in path.parts:
                continue
            try:
                text = path.read_text(errors="ignore")
            except OSError:
                continue
            for needle in needles:
                assert needle not in text, f"{path} unexpectedly references {needle}"


# ---------------------------------------------------------------------------
# Existing full suite still passing is verified by running the full pytest
# suite (see task verification steps) -- not something a single test in
# this file can assert about other test files.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Extra: default (non-injected) construction uses the real stage services,
# proving dependency injection is optional, not required -- without
# actually running the real graph (which would need real provider/network
# access), only that construction and node wiring succeed.
# ---------------------------------------------------------------------------


def test_default_construction_uses_real_stage_services() -> None:
    from app.services.candidate_quality_service import CandidateQualityService as RealCQS
    from app.services.destination_context_service import DestinationContextService
    from app.services.experience_planner_service import (
        ExperiencePlannerService as RealEPS,
    )
    from app.services.plan_validator_service import PlanValidatorService
    from app.services.stay_transport_service import StayTransportService
    from app.services.traveler_profile_service import TravelerProfileService
    from app.services.trip_strategy_service import TripStrategyService

    runner = PlanningGraphRunner()

    assert isinstance(runner.traveler_profile_service, TravelerProfileService)
    assert isinstance(runner.destination_context_service, DestinationContextService)
    assert isinstance(runner.candidate_quality_service, RealCQS)
    assert isinstance(runner.ai_candidate_discovery_service, AICandidateDiscoveryService)
    assert isinstance(runner.trip_strategy_service, TripStrategyService)
    assert isinstance(runner.stay_transport_service, StayTransportService)
    assert isinstance(runner.experience_planner_service, RealEPS)
    assert isinstance(runner.plan_validator_service, PlanValidatorService)
    assert runner.shadow_mode_enabled is None
