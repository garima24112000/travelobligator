from __future__ import annotations

from fastapi.testclient import TestClient

# Section 199A (Tasks 26/31/39): API-level tests for the three read-only
# branch/revision endpoints --
#   GET /trips/{trip_id}/branches
#   GET /trips/{trip_id}/branches/{branch_id}/revisions
#   GET /trips/{trip_id}/revisions/{revision_id}
# -- exercised through the real FastAPI app/TestClient, over the real
# generation pipeline (`generated_trip_id` fixture), not by calling
# `RevisionLineageService` directly (that's `test_revision_lineage_service.py`'s
# job).


def test_fresh_trip_has_one_default_branch_with_a_captured_head_revision(
    client: TestClient, generated_trip_id: str
) -> None:
    response = client.get(f"/trips/{generated_trip_id}/branches")
    assert response.status_code == 200
    branches = response.json()["data"]["branches"]
    assert len(branches) == 1
    branch = branches[0]
    assert branch["display_name"] == "Main"
    assert branch["is_default"] is True
    assert branch["branch_id"].startswith("branch_")
    assert branch["head_revision_id"] is not None

    revisions_response = client.get(
        f"/trips/{generated_trip_id}/branches/{branch['branch_id']}/revisions"
    )
    assert revisions_response.status_code == 200
    revisions = revisions_response.json()["data"]["revisions"]
    assert len(revisions) == 1
    revision = revisions[0]
    assert revision["version_label"] == "v1"
    assert revision["parent_revision_id"] is None
    assert revision["snapshot_available"] is True
    # Task 20/27: a list response must never carry each revision's full
    # snapshot.
    assert "snapshot" not in revision
    assert "planning_state" not in revision

    detail_response = client.get(f"/trips/{generated_trip_id}/revisions/{revision['revision_id']}")
    assert detail_response.status_code == 200
    detail = detail_response.json()["data"]
    assert detail["snapshot_available"] is True
    assert detail["planning_state"] is not None
    assert detail["planning_state"]["trip_id"] == generated_trip_id
    assert detail["planning_state"]["metadata"]["current_version"] == "v1"


def test_branches_endpoint_lazily_creates_branch_for_a_never_generated_trip(
    client: TestClient, created_trip_id: str
) -> None:
    """A trip that has never been generated still gets a real, stable
    default branch when queried (Task 3/6/17: lazy/backward-compatible
    initialization) -- with no head revision yet, honestly."""
    response = client.get(f"/trips/{created_trip_id}/branches")
    assert response.status_code == 200
    branches = response.json()["data"]["branches"]
    assert len(branches) == 1
    assert branches[0]["head_revision_id"] is None

    revisions_response = client.get(
        f"/trips/{created_trip_id}/branches/{branches[0]['branch_id']}/revisions"
    )
    assert revisions_response.status_code == 200
    assert revisions_response.json()["data"]["revisions"] == []


def test_unknown_trip_returns_404_for_all_three_endpoints(client: TestClient) -> None:
    for path in (
        "/trips/does-not-exist/branches",
        "/trips/does-not-exist/branches/branch_does_not_exist/revisions",
        "/trips/does-not-exist/revisions/revision_does_not_exist",
    ):
        response = client.get(path)
        assert response.status_code == 404
        assert response.json()["errors"][0]["code"] == "TRIP_NOT_FOUND"


def test_unknown_branch_id_returns_branch_not_found(
    client: TestClient, generated_trip_id: str
) -> None:
    response = client.get(f"/trips/{generated_trip_id}/branches/branch_does_not_exist/revisions")
    assert response.status_code == 404
    assert response.json()["errors"][0]["code"] == "BRANCH_NOT_FOUND"


def test_unknown_revision_id_returns_revision_not_found(
    client: TestClient, generated_trip_id: str
) -> None:
    response = client.get(f"/trips/{generated_trip_id}/revisions/revision_does_not_exist")
    assert response.status_code == 404
    assert response.json()["errors"][0]["code"] == "REVISION_NOT_FOUND"


# ---------------------------------------------------------------------------
# Task 39: ownership isolation
# ---------------------------------------------------------------------------


def test_second_user_cannot_read_another_users_branches(
    client: TestClient, second_client: TestClient, generated_trip_id: str
) -> None:
    response = second_client.get(f"/trips/{generated_trip_id}/branches")
    assert response.status_code == 403
    assert response.json()["errors"][0]["code"] == "FORBIDDEN"


def test_second_user_cannot_read_another_users_revision_list(
    client: TestClient, second_client: TestClient, generated_trip_id: str
) -> None:
    own_response = client.get(f"/trips/{generated_trip_id}/branches")
    branch_id = own_response.json()["data"]["branches"][0]["branch_id"]

    response = second_client.get(f"/trips/{generated_trip_id}/branches/{branch_id}/revisions")
    assert response.status_code == 403
    assert response.json()["errors"][0]["code"] == "FORBIDDEN"


def test_second_user_cannot_read_another_users_revision_snapshot(
    client: TestClient, second_client: TestClient, generated_trip_id: str
) -> None:
    own_branches = client.get(f"/trips/{generated_trip_id}/branches").json()["data"]["branches"]
    own_revisions = client.get(
        f"/trips/{generated_trip_id}/branches/{own_branches[0]['branch_id']}/revisions"
    ).json()["data"]["revisions"]
    revision_id = own_revisions[0]["revision_id"]

    response = second_client.get(f"/trips/{generated_trip_id}/revisions/{revision_id}")
    assert response.status_code == 403
    assert response.json()["errors"][0]["code"] == "FORBIDDEN"


def test_unauthenticated_cannot_read_any_lineage_endpoint(generated_trip_id: str) -> None:
    from app.main import app

    unauthenticated_client = TestClient(app)

    response = unauthenticated_client.get(f"/trips/{generated_trip_id}/branches")
    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "AUTHENTICATION_REQUIRED"
