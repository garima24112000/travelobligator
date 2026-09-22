from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.models.ai_feedback_interpretation import (
    AIFeedbackClarification,
    AIFeedbackInterpretationResult,
    AIFeedbackInterpretationStatus,
    RegenerateDayAction,
    RemoveExperienceAction,
)
from app.services.targeted_regeneration_application_service import (
    targeted_regeneration_application_service,
)

# Section 197C (docs/14_backend_architecture.md, following section
# 149.1) HTTP-level regression/integration tests. Only the AI
# interpretation boundary is faked (via monkeypatching the module-level
# `targeted_regeneration_application_service` singleton's own
# `interpreter_service` attribute) -- 197A's plan builder and 197B's
# executor run for real against a real generated plan (built from the
# deterministic test places provider every test already gets, autouse,
# from conftest.py), so these tests exercise the real compiler/executor
# end-to-end, not just the application service's own branching.


def _create_trip_payload() -> dict[str, Any]:
    return {
        "destination_scope": "single_city",
        "primary_destination": "Testville, Testland",
        "origin_city": "Home City",
        "start_date": "2026-09-10",
        "end_date": "2026-09-12",
        "travelers_count": 2,
        "travel_group_type": "couple",
    }


def _create_and_generate_trip(client: TestClient) -> str:
    create_response = client.post("/trips", json=_create_trip_payload())
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]
    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200
    return trip_id


def _completed_interpretation(**overrides: Any) -> AIFeedbackInterpretationResult:
    fields: dict[str, Any] = {
        "status": AIFeedbackInterpretationStatus.COMPLETED,
        "confidence": 0.9,
        "provider_name": "fake_test_provider",
        "model_name": "fake_test_model",
    }
    fields.update(overrides)
    return AIFeedbackInterpretationResult(**fields)


class _FakeInterpreter:
    def __init__(self, result: AIFeedbackInterpretationResult) -> None:
        self._result = result
        self.received_feedback_text: str | None = None

    def interpret(self, planning_state: Any, feedback_text: str) -> AIFeedbackInterpretationResult:
        self.received_feedback_text = feedback_text
        return self._result


@pytest.fixture(autouse=True)
def _targeted_mode_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "targeted_regeneration_enabled", True, raising=False)


def _first_experience_id(planning_state: dict[str, Any]) -> tuple[str, int]:
    for day in planning_state["experience_plan"]["daily_plans"]:
        if day["experiences"]:
            return day["experiences"][0]["experience_id"], day["day_number"]
    raise AssertionError("No scheduled experience found in generated plan.")


# ---------------------------------------------------------------------------
# Task 34: feature-disabled regression already proven by
# test_ai_feedback_interpreter_no_wiring.py's three existing HTTP tests
# (targeted_regeneration_enabled defaults to False there). Below, targeted
# mode is explicitly on for every test in this file (autouse fixture).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Task 37: deterministic remove, full HTTP round trip.
# ---------------------------------------------------------------------------


def test_targeted_remove_e2e(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    trip_id = _create_and_generate_trip(client)
    trip_response = client.get(f"/trips/{trip_id}")
    planning_state = trip_response.json()["data"]["planning_state"]
    target_experience_id, target_day = _first_experience_id(planning_state)

    client.post(f"/trips/{trip_id}/feedback", json={"feedback_text": "Remove that experience, please."})

    fake_interpreter = _FakeInterpreter(
        _completed_interpretation(actions=[RemoveExperienceAction(experience_id=target_experience_id)])
    )
    monkeypatch.setattr(targeted_regeneration_application_service, "interpreter_service", fake_interpreter)

    response = client.post(f"/trips/{trip_id}/regenerate", json={"confirm": True})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["targeted"] is True
    assert data["status"] == "applied"
    assert data["current_version"] == "v2"
    assert data["execution_status"] == "completed"
    assert data["diff"]["removed_experience_ids"] == [target_experience_id]
    assert data["diff"]["added_experience_ids"] == []

    reloaded = client.get(f"/trips/{trip_id}").json()["data"]["planning_state"]
    assert reloaded["metadata"]["current_version"] == "v2"
    all_ids = {
        e["experience_id"] for day in reloaded["experience_plan"]["daily_plans"] for e in day["experiences"]
    }
    assert target_experience_id not in all_ids
    applied_event = reloaded["feedback_history"][0]
    assert applied_event["handling_status"] == "applied"
    assert applied_event["applied_in_version"] == "v2"
    assert applied_event["interpretation"]["method"] == "ai_interpreted"

    # Section 197C.1: exactly one "applied" RegenerationAttempt, agreeing
    # with the version everywhere else it's recorded.
    assert len(reloaded["regeneration_attempts"]) == 1
    attempt = reloaded["regeneration_attempts"][0]
    assert attempt["status"] == "applied"
    assert attempt["current_version"] == "v2" == applied_event["applied_in_version"]

    # Unrelated days preserved byte-for-byte.
    original_days_by_number = {d["day_number"]: d for d in planning_state["experience_plan"]["daily_plans"]}
    reloaded_days_by_number = {d["day_number"]: d for d in reloaded["experience_plan"]["daily_plans"]}
    for day_number, original_day in original_days_by_number.items():
        if day_number == target_day:
            continue
        assert reloaded_days_by_number[day_number]["experiences"] == original_day["experiences"]


# ---------------------------------------------------------------------------
# Task 36/48: clarification never mutates or versions.
# ---------------------------------------------------------------------------


def test_targeted_clarification_e2e(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    trip_id = _create_and_generate_trip(client)
    client.post(f"/trips/{trip_id}/feedback", json={"feedback_text": "Remove the museum."})

    fake_interpreter = _FakeInterpreter(
        AIFeedbackInterpretationResult(
            status=AIFeedbackInterpretationStatus.NEEDS_CLARIFICATION,
            clarification=AIFeedbackClarification(
                reason="Two museums are currently scheduled.", possible_experience_ids=["exp_1", "exp_2"]
            ),
            confidence=0.4,
        )
    )
    monkeypatch.setattr(targeted_regeneration_application_service, "interpreter_service", fake_interpreter)

    response = client.post(f"/trips/{trip_id}/regenerate", json={"confirm": True})

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "REGENERATION_NEEDS_CLARIFICATION"

    reloaded = client.get(f"/trips/{trip_id}").json()["data"]["planning_state"]
    assert reloaded["metadata"]["current_version"] == "v1"
    assert len(reloaded["version_history"]) == 1
    event = reloaded["feedback_history"][0]
    assert event["handling_status"] == "captured"
    assert event["applied_at"] is None
    assert event["interpretation"]["status"] == "needs_clarification"
    assert (
        event["interpretation"]["structured_result"]["clarification"]["reason"]
        == "Two museums are currently scheduled."
    )
    assert len(reloaded["regeneration_attempts"]) == 1
    assert reloaded["regeneration_attempts"][0]["status"] == "blocked"


# ---------------------------------------------------------------------------
# Task 6/35: interpreter unavailable never silently falls back to legacy.
# ---------------------------------------------------------------------------


def test_targeted_interpreter_unavailable_never_falls_back_to_legacy(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    trip_id = _create_and_generate_trip(client)
    client.post(f"/trips/{trip_id}/feedback", json={"feedback_text": "Remove the museum."})

    fake_interpreter = _FakeInterpreter(
        AIFeedbackInterpretationResult(
            status=AIFeedbackInterpretationStatus.NOT_CONNECTED,
            blocked_reasons=["AI feedback interpreter is disabled."],
            confidence=0.0,
        )
    )
    monkeypatch.setattr(targeted_regeneration_application_service, "interpreter_service", fake_interpreter)

    response = client.post(f"/trips/{trip_id}/regenerate", json={"confirm": True})

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "REGENERATION_PROVIDER_UNAVAILABLE"
    reloaded = client.get(f"/trips/{trip_id}").json()["data"]["planning_state"]
    # No legacy coarse stage rerun happened either -- version unchanged.
    assert reloaded["metadata"]["current_version"] == "v1"


# ---------------------------------------------------------------------------
# Task 42: lock blocks targeted mode too.
# ---------------------------------------------------------------------------


def test_targeted_lock_blocks_regeneration(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    trip_id = _create_and_generate_trip(client)
    trip_response = client.get(f"/trips/{trip_id}")
    planning_state = trip_response.json()["data"]["planning_state"]
    target_experience_id, _ = _first_experience_id(planning_state)

    client.post(f"/trips/{trip_id}/feedback", json={"feedback_text": "Remove that experience."})
    lock_response = client.post(
        f"/trips/{trip_id}/locks",
        json={"locked_item_type": "experience", "locked_item_id": target_experience_id},
    )
    assert lock_response.status_code == 201

    fake_interpreter = _FakeInterpreter(
        _completed_interpretation(actions=[RemoveExperienceAction(experience_id=target_experience_id)])
    )
    monkeypatch.setattr(targeted_regeneration_application_service, "interpreter_service", fake_interpreter)

    response = client.post(f"/trips/{trip_id}/regenerate", json={"confirm": True})

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "REGENERATION_BLOCKED_BY_LOCKS"
    reloaded = client.get(f"/trips/{trip_id}").json()["data"]["planning_state"]
    assert reloaded["metadata"]["current_version"] == "v1"


# ---------------------------------------------------------------------------
# Task 38-analog: day-scoped regeneration only touches the named day.
# ---------------------------------------------------------------------------


def test_targeted_day_scoped_regeneration_only_touches_named_day(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    trip_id = _create_and_generate_trip(client)
    trip_response = client.get(f"/trips/{trip_id}")
    planning_state = trip_response.json()["data"]["planning_state"]
    day_numbers = sorted(d["day_number"] for d in planning_state["experience_plan"]["daily_plans"])
    assert len(day_numbers) >= 2
    target_day = day_numbers[0]

    client.post(f"/trips/{trip_id}/feedback", json={"feedback_text": f"Day {target_day} is too packed."})

    fake_interpreter = _FakeInterpreter(
        _completed_interpretation(
            scope="single_day",
            actions=[RegenerateDayAction(day_index=target_day, instruction="Make it more relaxed.")],
        )
    )
    monkeypatch.setattr(targeted_regeneration_application_service, "interpreter_service", fake_interpreter)

    # Enable scoped AI reasoning for this one test and inject a fake
    # provider (via the executor's own resolved-provider factory
    # reference) that picks a real candidate from whatever restricted
    # universe the executor actually built -- exercising the real 197A
    # plan compiler and 197B executor end-to-end, only faking the LLM
    # call itself.
    monkeypatch.setattr(get_settings(), "ai_itinerary_reasoning_enabled", True, raising=False)

    class _FakeReasoningProvider:
        provider_name = "fake_http_test_reasoning_provider"

        def reason(self, request: Any) -> Any:
            from app.models.ai_itinerary_reasoning import (
                AIItineraryReasoningGuardrailReport,
                AIItineraryReasoningResult,
                AIItineraryReasoningStatus,
                ItineraryReasoningDayPlan,
                ItineraryReasoningStrategy,
            )

            assert request.allowed_candidates, "expected a non-empty restricted candidate universe"
            chosen = request.allowed_candidates[0]
            return AIItineraryReasoningResult(
                status=AIItineraryReasoningStatus.COMPLETED,
                strategy=ItineraryReasoningStrategy(
                    summary="Relax the day.", pace="relaxed", reason="User requested a calmer day."
                ),
                days=[
                    ItineraryReasoningDayPlan(
                        day_index=target_day, candidate_ids=[chosen.candidate_id], rationale="Keep it light."
                    )
                ],
                guardrail_report=AIItineraryReasoningGuardrailReport(passed=True, checked_fields=["candidate_ids"]),
                confidence=0.9,
            )

    monkeypatch.setattr(
        "app.services.targeted_regeneration_executor.get_ai_itinerary_reasoning_provider",
        lambda: _FakeReasoningProvider(),
    )

    response = client.post(f"/trips/{trip_id}/regenerate", json={"confirm": True})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["execution_status"] == "completed"
    assert data["diff"]["affected_day_indices"] == [target_day]

    reloaded = client.get(f"/trips/{trip_id}").json()["data"]["planning_state"]
    original_days_by_number = {d["day_number"]: d for d in planning_state["experience_plan"]["daily_plans"]}
    reloaded_days_by_number = {d["day_number"]: d for d in reloaded["experience_plan"]["daily_plans"]}
    for day_number, original_day in original_days_by_number.items():
        if day_number == target_day:
            continue
        assert reloaded_days_by_number[day_number]["experiences"] == original_day["experiences"]


def test_targeted_day_scoped_regeneration_honestly_fails_when_reasoning_unavailable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 6/35: with AI itinerary reasoning unavailable (this suite's
    default), a day-scoped request never silently falls back to legacy
    coarse regeneration -- it fails honestly, and nothing is mutated."""
    trip_id = _create_and_generate_trip(client)
    trip_response = client.get(f"/trips/{trip_id}")
    planning_state = trip_response.json()["data"]["planning_state"]
    target_day = planning_state["experience_plan"]["daily_plans"][0]["day_number"]

    client.post(f"/trips/{trip_id}/feedback", json={"feedback_text": f"Day {target_day} is too packed."})

    fake_interpreter = _FakeInterpreter(
        _completed_interpretation(
            scope="single_day",
            actions=[RegenerateDayAction(day_index=target_day, instruction="Make it more relaxed.")],
        )
    )
    monkeypatch.setattr(targeted_regeneration_application_service, "interpreter_service", fake_interpreter)

    response = client.post(f"/trips/{trip_id}/regenerate", json={"confirm": True})

    assert response.status_code == 409
    reloaded = client.get(f"/trips/{trip_id}").json()["data"]["planning_state"]
    assert reloaded["metadata"]["current_version"] == "v1"


# ---------------------------------------------------------------------------
# Task 40: new-place provider failure never mutates.
# ---------------------------------------------------------------------------


def test_targeted_new_place_provider_unavailable_e2e(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.models.ai_feedback_interpretation import NewPlaceRequest

    trip_id = _create_and_generate_trip(client)
    client.post(f"/trips/{trip_id}/feedback", json={"feedback_text": "I also want to visit Nonexistent Place XYZ."})

    fake_interpreter = _FakeInterpreter(
        _completed_interpretation(
            scope="new_place_request",
            actions=[],
            new_place_requests=[NewPlaceRequest(query="Nonexistent Place XYZ")],
        )
    )
    monkeypatch.setattr(targeted_regeneration_application_service, "interpreter_service", fake_interpreter)

    response = client.post(f"/trips/{trip_id}/regenerate", json={"confirm": True})

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "REGENERATION_PROVIDER_UNAVAILABLE"
    reloaded = client.get(f"/trips/{trip_id}").json()["data"]["planning_state"]
    assert reloaded["metadata"]["current_version"] == "v1"


# ---------------------------------------------------------------------------
# Task 39: new-place success (real provider-response shape), originals
# preserved.
# ---------------------------------------------------------------------------


def test_targeted_new_place_success_e2e(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.models.ai_feedback_interpretation import NewPlaceRequest
    from app.models.common import DataStatus, GeoPoint, ProviderStatus
    from app.models.providers import NormalizedPlace, ProviderResponse
    from app.providers.gateway import provider_gateway

    trip_id = _create_and_generate_trip(client)
    trip_response = client.get(f"/trips/{trip_id}")
    planning_state = trip_response.json()["data"]["planning_state"]
    original_ids = {
        e["experience_id"] for day in planning_state["experience_plan"]["daily_plans"] for e in day["experiences"]
    }

    client.post(f"/trips/{trip_id}/feedback", json={"feedback_text": "I also want to visit New Fixture Place."})

    fake_interpreter = _FakeInterpreter(
        _completed_interpretation(
            scope="new_place_request", actions=[], new_place_requests=[NewPlaceRequest(query="New Fixture Place")]
        )
    )
    monkeypatch.setattr(targeted_regeneration_application_service, "interpreter_service", fake_interpreter)

    grounded_response = ProviderResponse[list[NormalizedPlace]](
        provider_name="openstreetmap_places",
        provider_type=provider_gateway.places.provider_type,
        status=ProviderStatus.SUCCESS,
        data_status=DataStatus.LIVE,
        data=[
            NormalizedPlace(
                place_id="test/new_fixture_place",
                name="New Fixture Place",
                category="attraction",
                coordinates=GeoPoint(lat=1.0, lng=2.0),
                source="openstreetmap_places",
                data_status=DataStatus.LIVE,
                confidence=0.8,
            )
        ],
    )
    monkeypatch.setattr(provider_gateway.places, "search_must_visit_place", lambda *a, **k: grounded_response)
    monkeypatch.setattr(get_settings(), "ai_itinerary_reasoning_enabled", True, raising=False)

    class _FakeInsertionReasoningProvider:
        provider_name = "fake_http_test_insertion_provider"

        def reason(self, request: Any) -> Any:
            from app.models.ai_itinerary_reasoning import (
                AIItineraryReasoningGuardrailReport,
                AIItineraryReasoningResult,
                AIItineraryReasoningStatus,
                ItineraryReasoningDayPlan,
                ItineraryReasoningStrategy,
            )

            assert len(request.allowed_candidates) == 1
            candidate = request.allowed_candidates[0]
            return AIItineraryReasoningResult(
                status=AIItineraryReasoningStatus.COMPLETED,
                strategy=ItineraryReasoningStrategy(summary="Add it.", pace="balanced", reason="User requested it."),
                days=[
                    ItineraryReasoningDayPlan(
                        day_index=1, candidate_ids=[candidate.candidate_id], rationale="Fits well on day 1."
                    )
                ],
                guardrail_report=AIItineraryReasoningGuardrailReport(passed=True, checked_fields=["candidate_ids"]),
                confidence=0.9,
            )

    monkeypatch.setattr(
        "app.services.targeted_regeneration_executor.get_ai_itinerary_reasoning_provider",
        lambda: _FakeInsertionReasoningProvider(),
    )

    response = client.post(f"/trips/{trip_id}/regenerate", json={"confirm": True})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["execution_status"] == "completed"
    assert len(data["diff"]["added_experience_ids"]) == 1
    assert data["diff"]["removed_experience_ids"] == []
    assert data["diff"]["moved_experiences"] == []

    reloaded = client.get(f"/trips/{trip_id}").json()["data"]["planning_state"]
    all_ids_after = {
        e["experience_id"] for day in reloaded["experience_plan"]["daily_plans"] for e in day["experiences"]
    }
    assert original_ids.issubset(all_ids_after)
    new_ids = all_ids_after - original_ids
    assert len(new_ids) == 1
    new_experience = next(
        e
        for day in reloaded["experience_plan"]["daily_plans"]
        for e in day["experiences"]
        if e["experience_id"] in new_ids
    )
    assert new_experience["provider_place_id"] == "test/new_fixture_place"


# ---------------------------------------------------------------------------
# Task 41: stale version is a conflict, never an overwrite.
# ---------------------------------------------------------------------------


def test_targeted_stale_version_conflict_e2e(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.repositories.planning_state_repository import planning_state_repository

    trip_id = _create_and_generate_trip(client)
    trip_response = client.get(f"/trips/{trip_id}")
    planning_state = trip_response.json()["data"]["planning_state"]
    target_experience_id, _ = _first_experience_id(planning_state)

    client.post(f"/trips/{trip_id}/feedback", json={"feedback_text": "Remove that experience."})

    fake_interpreter = _FakeInterpreter(
        _completed_interpretation(actions=[RemoveExperienceAction(experience_id=target_experience_id)])
    )
    monkeypatch.setattr(targeted_regeneration_application_service, "interpreter_service", fake_interpreter)

    # Simulate a concurrent regeneration advancing the version between
    # this request's own state load and its commit -- the executor still
    # runs against the version it loaded, but the final pre-commit
    # recheck must catch the drift.
    real_get_by_trip_id = targeted_regeneration_application_service.planning_state_repository.get_by_trip_id
    call_count = {"n": 0}

    def _flaky_get_by_trip_id(trip_id_arg: str):
        call_count["n"] += 1
        state = real_get_by_trip_id(trip_id_arg)
        # Call 1: the route handler's own top-level load (before this
        # service is even reached). Call 2: this service's own initial
        # load (must still see v1, so the plan/execution run correctly
        # against it). Call 3: this service's pre-commit conflict
        # recheck -- bump the version right here, simulating another
        # request completing a regeneration in between.
        if call_count["n"] == 3 and state is not None:
            state.metadata.current_version = "v2"
            planning_state_repository.save(state)
        return state

    monkeypatch.setattr(
        targeted_regeneration_application_service.planning_state_repository,
        "get_by_trip_id",
        _flaky_get_by_trip_id,
    )

    response = client.post(f"/trips/{trip_id}/regenerate", json={"confirm": True})

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "REGENERATION_CONFLICT"


# ---------------------------------------------------------------------------
# Task 28: async targeted regeneration has semantic parity with sync --
# both call the exact same TargetedRegenerationApplicationService.regenerate.
# ---------------------------------------------------------------------------


def test_targeted_async_regenerate_has_parity_with_sync(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    trip_id = _create_and_generate_trip(client)
    trip_response = client.get(f"/trips/{trip_id}")
    planning_state = trip_response.json()["data"]["planning_state"]
    target_experience_id, _ = _first_experience_id(planning_state)

    client.post(f"/trips/{trip_id}/feedback", json={"feedback_text": "Remove that experience, please."})

    fake_interpreter = _FakeInterpreter(
        _completed_interpretation(actions=[RemoveExperienceAction(experience_id=target_experience_id)])
    )
    monkeypatch.setattr(targeted_regeneration_application_service, "interpreter_service", fake_interpreter)

    # Mirrors test_async_regenerate.py's own _enable_async_generation
    # pattern: env var + cache_clear (a real Settings() rebuild), then
    # re-apply targeted mode on the freshly rebuilt singleton -- the
    # autouse `_targeted_mode_enabled` fixture already patched the OLD
    # cached instance before this test body ran.
    monkeypatch.setenv("ASYNC_GENERATION_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(get_settings(), "targeted_regeneration_enabled", True, raising=False)

    response = client.post(f"/trips/{trip_id}/regenerate", json={"confirm": True})

    assert response.status_code == 202
    job = response.json()["data"]
    assert job["job_type"] == "regenerate"
    assert job["status"] in ("queued", "running", "succeeded")

    job_id = job["job_id"]
    poll_response = client.get(f"/trips/{trip_id}/jobs/{job_id}")
    assert poll_response.status_code == 200
    polled_job = poll_response.json()["data"]
    assert polled_job["status"] == "succeeded"
    assert polled_job["result_version"] == "v2"

    reloaded = client.get(f"/trips/{trip_id}").json()["data"]["planning_state"]
    assert reloaded["metadata"]["current_version"] == "v2"
    all_ids = {
        e["experience_id"] for day in reloaded["experience_plan"]["daily_plans"] for e in day["experiences"]
    }
    assert target_experience_id not in all_ids

    # Section 197C.1: async produces the exact same canonical attempt
    # outcome as sync -- one "applied" attempt, version-consistent.
    assert len(reloaded["regeneration_attempts"]) == 1
    attempt = reloaded["regeneration_attempts"][0]
    assert attempt["status"] == "applied"
    assert attempt["current_version"] == "v2"


# ---------------------------------------------------------------------------
# Task 30/8: targeted async duplicate-job protection is inherited, not
# reimplemented -- reuses the exact same guard the legacy async path uses.
# ---------------------------------------------------------------------------


def test_targeted_async_duplicate_running_job_returns_409(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.models.generation_job import GenerationJobType, create_queued_job
    from app.repositories.job_repository import job_repository

    trip_id = _create_and_generate_trip(client)
    owner_id = client.get("/auth/me").json()["data"]["user"]["user_id"]
    client.post(f"/trips/{trip_id}/feedback", json={"feedback_text": "Remove something, please."})

    existing_job = create_queued_job(trip_id=trip_id, owner_id=owner_id, job_type=GenerationJobType.REGENERATE)
    job_repository.create(existing_job)

    monkeypatch.setenv("ASYNC_GENERATION_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(get_settings(), "targeted_regeneration_enabled", True, raising=False)

    response = client.post(f"/trips/{trip_id}/regenerate", json={"confirm": True})

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "JOB_ALREADY_RUNNING"
