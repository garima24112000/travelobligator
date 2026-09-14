from __future__ import annotations

from fastapi.testclient import TestClient

from app.repositories.job_repository import job_repository

# Regression tests for Step 186B (docs/14_backend_architecture.md section
# 116): the job model/config/repository foundation must be completely
# inert. POST /trips/{trip_id}/generate and .../regenerate stay fully
# synchronous, their response shapes are unchanged, and no GenerationJob
# is ever created by either -- ASYNC_GENERATION_ENABLED defaults to False
# and, as of this step, nothing reads it at all yet.


def test_generate_response_shape_has_no_job_fields(
    client: TestClient, created_trip_id: str
) -> None:
    response = client.post(f"/trips/{created_trip_id}/generate")
    assert response.status_code == 200

    data = response.json()["data"]
    assert "job_id" not in data
    assert "job_status" not in data
    assert set(data.keys()) == {"trip_id", "planning_state"}


def test_generate_does_not_create_any_generation_job(
    client: TestClient, created_trip_id: str
) -> None:
    client.post(f"/trips/{created_trip_id}/generate")

    assert job_repository.list_by_trip_id(created_trip_id) == []


def test_regenerate_refusal_response_shape_unchanged(
    client: TestClient, generated_trip_id: str
) -> None:
    response = client.post(f"/trips/{generated_trip_id}/regenerate")
    assert response.status_code == 409

    body = response.json()
    assert body["success"] is False
    assert body["errors"][0]["code"] == "REGENERATION_NOT_AVAILABLE"
    assert "job_id" not in body


def test_regenerate_does_not_create_any_generation_job(
    client: TestClient, generated_trip_id: str
) -> None:
    client.post(f"/trips/{generated_trip_id}/regenerate")

    assert job_repository.list_by_trip_id(generated_trip_id) == []


def test_no_generation_job_created_across_full_lifecycle(
    client: TestClient, created_trip_id: str
) -> None:
    """Broader smoke check across create -> generate -> feedback ->
    regenerate: at no point does Step 186B's foundation cause a job to be
    created, since nothing calls JobRepository.create yet."""
    client.post(f"/trips/{created_trip_id}/generate")
    client.post(
        f"/trips/{created_trip_id}/feedback",
        json={"feedback_text": "Make this less packed"},
    )
    client.post(f"/trips/{created_trip_id}/regenerate", json={"confirm": True})

    assert job_repository.list_by_trip_id(created_trip_id) == []
