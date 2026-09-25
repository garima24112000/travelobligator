from __future__ import annotations

import pytest

from fastapi.testclient import TestClient

from app.repositories.planning_state_repository import planning_state_repository

# Section 199C (Task 23/52): API-level tests for
# GET /trips/{trip_id}/revisions/compare and the new branch head/base
# display context, through the real app over a real generated trip.


def _branches(client: TestClient, trip_id: str) -> dict:
    return client.get(f"/trips/{trip_id}/branches").json()["data"]


def _regenerate_on_fork(client: TestClient, trip_id: str) -> tuple[str, str, str]:
    data = _branches(client, trip_id)
    main = data["branches"][0]
    fork = client.post(
        f"/trips/{trip_id}/branches",
        json={"source_revision_id": main["head_revision_id"], "display_name": "Option B", "activate_after_create": True},
    ).json()["data"]["branch"]
    client.post(f"/trips/{trip_id}/feedback", json={"feedback_text": "Make this less packed, it's too much walking"})
    assert client.post(f"/trips/{trip_id}/regenerate", json={"confirm": True}).status_code == 200
    after = _branches(client, trip_id)
    fork_after = next(b for b in after["branches"] if b["branch_id"] == fork["branch_id"])
    return main["head_revision_id"], fork_after["head_revision_id"], fork["branch_id"]


@pytest.mark.usefixtures("synthetic_legacy_regeneration_support")
def test_compare_main_head_against_fork_head(client: TestClient, generated_trip_id: str) -> None:
    main_head, fork_head, _ = _regenerate_on_fork(client, generated_trip_id)
    response = client.get(
        f"/trips/{generated_trip_id}/revisions/compare",
        params={"left_revision_id": main_head, "right_revision_id": fork_head},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["left"]["revision_id"] == main_head
    assert data["right"]["revision_id"] == fork_head
    assert data["left"]["branch_display_name"] == "Main"
    assert data["right"]["branch_display_name"] == "Option B"
    assert "planning_state" not in data and "snapshot" not in data
    for forbidden in ("winner", "better", "recommended", "score"):
        assert not any(forbidden in key for key in data)


def test_compare_same_revision_reports_no_compared_differences(client: TestClient, generated_trip_id: str) -> None:
    head = _branches(client, generated_trip_id)["branches"][0]["head_revision_id"]
    data = client.get(
        f"/trips/{generated_trip_id}/revisions/compare",
        params={"left_revision_id": head, "right_revision_id": head},
    ).json()["data"]
    assert data["no_compared_differences"] is True


def test_compare_unknown_revision_is_404_and_is_not_captured_as_revision_id(
    client: TestClient, generated_trip_id: str
) -> None:
    head = _branches(client, generated_trip_id)["branches"][0]["head_revision_id"]
    response = client.get(
        f"/trips/{generated_trip_id}/revisions/compare",
        params={"left_revision_id": head, "right_revision_id": "revision_nope"},
    )
    assert response.status_code == 404
    assert response.json()["errors"][0]["code"] == "REVISION_NOT_FOUND"


def test_compare_requires_ownership(client: TestClient, second_client: TestClient, generated_trip_id: str) -> None:
    head = _branches(client, generated_trip_id)["branches"][0]["head_revision_id"]
    response = second_client.get(
        f"/trips/{generated_trip_id}/revisions/compare",
        params={"left_revision_id": head, "right_revision_id": head},
    )
    assert response.status_code == 403


def test_compare_does_not_mutate_live_state(client: TestClient, generated_trip_id: str) -> None:
    head = _branches(client, generated_trip_id)["branches"][0]["head_revision_id"]
    before = planning_state_repository.get_by_trip_id(generated_trip_id).model_dump(mode="json")
    client.get(
        f"/trips/{generated_trip_id}/revisions/compare",
        params={"left_revision_id": head, "right_revision_id": head},
    )
    assert planning_state_repository.get_by_trip_id(generated_trip_id).model_dump(mode="json") == before


@pytest.mark.usefixtures("synthetic_legacy_regeneration_support")
def test_branch_response_carries_head_and_base_display_context(client: TestClient, generated_trip_id: str) -> None:
    _, _, fork_id = _regenerate_on_fork(client, generated_trip_id)
    data = _branches(client, generated_trip_id)
    main = next(b for b in data["branches"] if b["is_default"])
    fork = next(b for b in data["branches"] if b["branch_id"] == fork_id)
    assert main["head_version_label"] == "v1" and main["head_branch_display_name"] == "Main"
    assert main["base_revision_id"] is None and main["base_version_label"] is None
    assert fork["head_version_label"] == "v2" and fork["head_branch_display_name"] == "Option B"
    assert fork["base_version_label"] == "v1"
    assert fork["base_branch_display_name"] == "Main"
    assert fork["base_branch_id"] == main["branch_id"]
