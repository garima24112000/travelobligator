from __future__ import annotations

from typing import Any

import pytest

from app.graphs.planning_graph_nodes import (
    build_ai_candidate_node,
    build_destination_context_node,
    build_experience_planning_node,
    build_final_state_node,
    build_provider_coverage_node,
    build_stay_transport_node,
    build_validation_node,
)
from app.graphs.planning_graph_state import build_initial_planning_graph_state
from app.models.planning_state import PlanningState, TravelGroupType, TripRequest

# Tests for the LangGraph planning node skeleton (Step 171A). Every node
# here is exercised only with in-file fake/no-op service doubles -- no
# real provider/network call, no real LLM call (Groq/Anthropic/OpenAI/
# other), and no persistence.


class _FakeStageService:
    """Deterministic test double for a single-method (`run`) stage
    service. Never calls a provider, LLM, or network -- just marks that it
    ran, so tests can assert per-node behavior without depending on any
    real stage service's actual logic.
    """

    def __init__(self, marker: str, *, raises: bool = False) -> None:
        self.marker = marker
        self.raises = raises
        self.call_count = 0

    def run(self, planning_state: PlanningState) -> PlanningState:
        self.call_count += 1
        if self.raises:
            raise RuntimeError(f"simulated failure in {self.marker}")
        planning_state.data_sources_used = list(planning_state.data_sources_used) + [
            f"fake_{self.marker}"
        ]
        return planning_state


class _FakeAICandidatePromotionService:
    """Deterministic test double for `AICandidatePromotionService`. Never
    calls a provider, LLM, or network.
    """

    def __init__(self, *, raises: bool = False) -> None:
        self.raises = raises
        self.call_count = 0

    def apply_promotion(self, planning_state: PlanningState) -> PlanningState:
        self.call_count += 1
        if self.raises:
            raise RuntimeError("simulated ai_candidate failure")
        planning_state.data_sources_used = list(planning_state.data_sources_used) + [
            "fake_ai_candidate_promotion"
        ]
        return planning_state


def _trip_request(**overrides: Any) -> TripRequest:
    fields: dict[str, Any] = {
        "primary_destination": "New York",
        "start_date": "2026-08-10",
        "end_date": "2026-08-12",
        "travelers_count": 2,
        "travel_group_type": TravelGroupType.COUPLE,
    }
    fields.update(overrides)
    return TripRequest(**fields)


def _initial_state(trip_request: TripRequest | None = None) -> Any:
    trip_request = trip_request or _trip_request()
    planning_state = PlanningState(trip_request=trip_request)
    return build_initial_planning_graph_state(planning_state.trip_id, trip_request, planning_state)


# ---------------------------------------------------------------------------
# 3. Each node returns state and records completion.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "node_name, build_node",
    [
        ("destination_context", lambda service: build_destination_context_node(service)),
        ("stay_transport", lambda service: build_stay_transport_node(service)),
        ("experience_planning", lambda service: build_experience_planning_node(service)),
        ("validation", lambda service: build_validation_node(service)),
    ],
)
def test_service_backed_node_returns_state_and_records_completion(
    node_name: str, build_node: Any
) -> None:
    fake_service = _FakeStageService(node_name)
    node = build_node(fake_service)
    state = _initial_state()

    result = node(state)

    assert result["completed_nodes"] == [node_name]
    assert "failed_nodes" not in result
    assert fake_service.call_count == 1
    assert f"fake_{node_name}" in result["planning_state"].data_sources_used


def test_ai_candidate_node_with_injected_service_returns_state_and_records_completion() -> None:
    fake_service = _FakeAICandidatePromotionService()
    node = build_ai_candidate_node(fake_service)
    state = _initial_state()

    result = node(state)

    assert result["completed_nodes"] == ["ai_candidate"]
    assert fake_service.call_count == 1
    assert "fake_ai_candidate_promotion" in result["planning_state"].data_sources_used


def test_ai_candidate_node_default_is_a_pure_noop() -> None:
    """With no service injected (the default), ai_candidate_node never
    calls AICandidateDiscoveryService/an AI candidate proposal provider,
    and never mutates planning_state -- see build_ai_candidate_node's
    docstring."""
    node = build_ai_candidate_node()
    state = _initial_state()
    before = state["planning_state"].model_copy(deep=True)

    result = node(state)

    assert result == {"completed_nodes": ["ai_candidate"]}
    assert state["planning_state"] == before


def test_provider_coverage_node_returns_state_and_records_completion() -> None:
    node = build_provider_coverage_node()
    state = _initial_state()

    result = node(state)

    assert result == {"completed_nodes": ["provider_coverage"]}


def test_final_state_node_returns_state_and_records_completion() -> None:
    node = build_final_state_node()
    state = _initial_state()

    result = node(state)

    assert result["completed_nodes"] == ["final_state"]
    assert result["warnings"] == []


def test_final_state_node_reports_honest_warning_when_earlier_nodes_failed() -> None:
    node = build_final_state_node()
    state = _initial_state()
    state["failed_nodes"] = ["destination_context"]

    result = node(state)

    assert result["completed_nodes"] == ["final_state"]
    assert len(result["warnings"]) == 1
    assert "destination_context" in result["warnings"][0]


# ---------------------------------------------------------------------------
# 4-5. Node failure records failed_nodes and a safe error message, and
#      does not fabricate PlanningState data.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "node_name, build_node",
    [
        ("destination_context", lambda service: build_destination_context_node(service)),
        ("stay_transport", lambda service: build_stay_transport_node(service)),
        ("experience_planning", lambda service: build_experience_planning_node(service)),
        ("validation", lambda service: build_validation_node(service)),
    ],
)
def test_service_backed_node_failure_records_failed_node_and_safe_error(
    node_name: str, build_node: Any
) -> None:
    fake_service = _FakeStageService(node_name, raises=True)
    node = build_node(fake_service)
    state = _initial_state()
    before = state["planning_state"].model_copy(deep=True)

    result = node(state)

    assert result["failed_nodes"] == [node_name]
    assert "completed_nodes" not in result
    assert len(result["errors"]) == 1
    error_message = result["errors"][0]
    # Safe, generic, secret-free -- never the raw exception text.
    assert "simulated failure" not in error_message
    assert node_name in error_message
    # No PlanningState data fabricated: state passed back to the graph
    # (i.e. what the caller already had) is left completely untouched.
    assert state["planning_state"] == before


def test_ai_candidate_node_failure_records_failed_node_and_safe_error() -> None:
    fake_service = _FakeAICandidatePromotionService(raises=True)
    node = build_ai_candidate_node(fake_service)
    state = _initial_state()
    before = state["planning_state"].model_copy(deep=True)

    result = node(state)

    assert result["failed_nodes"] == ["ai_candidate"]
    assert len(result["errors"]) == 1
    assert "simulated" not in result["errors"][0]
    assert state["planning_state"] == before


# ---------------------------------------------------------------------------
# 8-10. No LLM/provider/network calls from any node in this module.
# ---------------------------------------------------------------------------


def test_nodes_module_has_no_llm_or_network_imports() -> None:
    import ast
    import inspect

    import app.graphs.planning_graph_nodes as nodes_module

    source = inspect.getsource(nodes_module)
    tree = ast.parse(source)
    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    disallowed_substrings = (
        "anthropic",
        "groq",
        "openai",
        "langsmith",
        "ai_candidate_discovery_service",
        "ai_candidate_proposal_provider",
        "app.providers",
        "requests",
        "httpx",
    )
    for name in imported_names:
        lowered = name.lower()
        for disallowed in disallowed_substrings:
            assert disallowed not in lowered, f"Disallowed import found: {name}"
