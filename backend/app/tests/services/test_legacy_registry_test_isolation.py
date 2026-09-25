from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.models.planning_state import PlanningState
from app.services import regeneration_mutation_service
from app.services.feedback_service import _FEEDBACK_TYPE_RULES
from app.repositories.planning_state_repository import planning_state_repository

# Section 202B.1.1: the test suite must exercise PRODUCTION legacy-
# regeneration semantics by default. Synthetic success support exists only
# behind the explicit `synthetic_legacy_regeneration_support` fixture, and
# the guard in conftest (autouse start check + teardown hook) fails any test
# that leaks it.


def test_default_registry_matches_production() -> None:
    assert regeneration_mutation_service.LEGACY_SUPPORTED_OPERATIONS == {}
    assert regeneration_mutation_service.legacy_regeneration_can_apply([]) is False


def test_free_text_legacy_request_is_refused_by_default(client: TestClient, generated_trip_id: str) -> None:
    client.post(f"/trips/{generated_trip_id}/feedback", json={"feedback_text": "Make this less packed."})

    response = client.post(f"/trips/{generated_trip_id}/regenerate", json={"confirm": True})

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "REGENERATION_FEEDBACK_NOT_INTERPRETABLE"


def test_synthetic_fixture_enables_only_its_explicit_operation(
    synthetic_legacy_regeneration_support: None,
) -> None:
    registry = regeneration_mutation_service.LEGACY_SUPPORTED_OPERATIONS
    assert set(registry) == {*_FEEDBACK_TYPE_RULES, "general_feedback", None}
    assert "made_up_feedback_type" not in registry  # nothing else is supported

    # Deterministic, keyword-free postcondition: true only when the rerun
    # left a generated experience plan on the resulting state.
    postcondition = registry["pace_change"]
    with_plan = PlanningState.model_construct(experience_plan=object())
    without_plan = PlanningState.model_construct(experience_plan=None)
    assert postcondition(with_plan, with_plan, None) is True
    assert postcondition(with_plan, without_plan, None) is False


def test_synthetic_fixture_makes_a_legacy_request_succeed_only_when_requested(
    client: TestClient, generated_trip_id: str, synthetic_legacy_regeneration_support: None
) -> None:
    client.post(f"/trips/{generated_trip_id}/feedback", json={"feedback_text": "Make this less packed."})

    response = client.post(f"/trips/{generated_trip_id}/regenerate", json={"confirm": True})

    assert response.status_code == 200
    state = planning_state_repository.get_by_trip_id(generated_trip_id)
    assert state is not None and state.metadata.current_version == "v2"


def test_fixture_cleanup_restores_the_production_registry(
    synthetic_legacy_regeneration_support: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert regeneration_mutation_service.LEGACY_SUPPORTED_OPERATIONS  # populated
    # The fixture registers through this same function-scoped
    # `monkeypatch`; undoing it is exactly what teardown does.
    monkeypatch.undo()
    assert regeneration_mutation_service.LEGACY_SUPPORTED_OPERATIONS == {}


def test_registry_is_empty_again_after_fixture_tests_regardless_of_order() -> None:
    # Runs after the fixture-using tests above in file order; the autouse
    # guard in conftest additionally asserts this at every test's start and
    # (after all fixture finalizers) end, so ordering cannot leak synthetic
    # support either way.
    assert regeneration_mutation_service.LEGACY_SUPPORTED_OPERATIONS == {}
