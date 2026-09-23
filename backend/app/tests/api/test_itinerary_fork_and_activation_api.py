from __future__ import annotations

from fastapi.testclient import TestClient

from app.repositories.planning_state_repository import planning_state_repository

# Section 199B (Tasks 20/21/27/29/39): API-level tests for
# `POST /trips/{trip_id}/branches` (fork creation) and
# `POST /trips/{trip_id}/branches/{branch_id}/activate`, exercised
# through the real FastAPI app/TestClient over the real generation +
# legacy regeneration pipeline (`generated_trip_id` fixture + real
# `POST /regenerate`), not by calling the services directly (that's
# `test_itinerary_fork_service.py`/`test_revision_lineage_branch_
# activation.py`'s job).


def _main_branch_id(client: TestClient, trip_id: str) -> str:
    response = client.get(f"/trips/{trip_id}/branches")
    assert response.status_code == 200
    return response.json()["data"]["active_branch_id"]


def _head_revision_id(client: TestClient, trip_id: str, branch_id: str) -> str:
    response = client.get(f"/trips/{trip_id}/branches")
    assert response.status_code == 200
    branches = {b["branch_id"]: b for b in response.json()["data"]["branches"]}
    return branches[branch_id]["head_revision_id"]


def _submit_and_regenerate(client: TestClient, trip_id: str, feedback_text: str) -> None:
    feedback_response = client.post(
        f"/trips/{trip_id}/feedback", json={"feedback_text": feedback_text}
    )
    assert feedback_response.status_code == 200
    regen_response = client.post(f"/trips/{trip_id}/regenerate", json={"confirm": True})
    assert regen_response.status_code == 200, regen_response.text


def test_create_fork_via_api(client: TestClient, generated_trip_id: str) -> None:
    main_branch_id = _main_branch_id(client, generated_trip_id)
    head_revision_id = _head_revision_id(client, generated_trip_id, main_branch_id)

    response = client.post(
        f"/trips/{generated_trip_id}/branches",
        json={"source_revision_id": head_revision_id, "display_name": "Option B"},
    )
    assert response.status_code == 201
    data = response.json()["data"]
    assert data["status"] == "created"
    assert data["branch"]["display_name"] == "Option B"
    assert data["branch"]["is_default"] is False
    assert data["branch"]["base_revision_id"] == head_revision_id
    assert data["branch"]["head_revision_id"] == head_revision_id
    assert data["activated"] is False

    # Main remains active -- creating a fork never switches by itself.
    assert _main_branch_id(client, generated_trip_id) == main_branch_id

    list_response = client.get(f"/trips/{generated_trip_id}/branches")
    branch_ids = {b["branch_id"] for b in list_response.json()["data"]["branches"]}
    assert data["branch"]["branch_id"] in branch_ids
    assert len(branch_ids) == 2


def test_create_and_activate_fork_via_api(client: TestClient, generated_trip_id: str) -> None:
    main_branch_id = _main_branch_id(client, generated_trip_id)
    head_revision_id = _head_revision_id(client, generated_trip_id, main_branch_id)

    response = client.post(
        f"/trips/{generated_trip_id}/branches",
        json={
            "source_revision_id": head_revision_id,
            "display_name": "Option B",
            "activate_after_create": True,
        },
    )
    assert response.status_code == 201
    data = response.json()["data"]
    assert data["activated"] is True
    assert data["activation_status"] == "activated"

    assert _main_branch_id(client, generated_trip_id) == data["branch"]["branch_id"]


def test_fork_from_unknown_revision_returns_404(client: TestClient, generated_trip_id: str) -> None:
    response = client.post(
        f"/trips/{generated_trip_id}/branches",
        json={"source_revision_id": "revision_does_not_exist", "display_name": "Option B"},
    )
    assert response.status_code == 404
    assert response.json()["errors"][0]["code"] == "REVISION_NOT_FOUND"


def test_fork_with_empty_name_returns_400(client: TestClient, generated_trip_id: str) -> None:
    main_branch_id = _main_branch_id(client, generated_trip_id)
    head_revision_id = _head_revision_id(client, generated_trip_id, main_branch_id)

    response = client.post(
        f"/trips/{generated_trip_id}/branches",
        json={"source_revision_id": head_revision_id, "display_name": "   "},
    )
    assert response.status_code == 400
    assert response.json()["errors"][0]["code"] == "VALIDATION_ERROR"


def test_fork_with_duplicate_name_returns_409(client: TestClient, generated_trip_id: str) -> None:
    main_branch_id = _main_branch_id(client, generated_trip_id)
    head_revision_id = _head_revision_id(client, generated_trip_id, main_branch_id)

    first = client.post(
        f"/trips/{generated_trip_id}/branches",
        json={"source_revision_id": head_revision_id, "display_name": "Option B"},
    )
    assert first.status_code == 201

    second = client.post(
        f"/trips/{generated_trip_id}/branches",
        json={"source_revision_id": head_revision_id, "display_name": "option b"},
    )
    assert second.status_code == 409
    assert second.json()["errors"][0]["code"] == "BRANCH_NAME_CONFLICT"


def test_activate_unknown_branch_returns_404(client: TestClient, generated_trip_id: str) -> None:
    response = client.post(f"/trips/{generated_trip_id}/branches/branch_does_not_exist/activate")
    assert response.status_code == 404
    assert response.json()["errors"][0]["code"] == "BRANCH_NOT_FOUND"


def test_activation_blocked_by_pending_feedback_via_api(
    client: TestClient, generated_trip_id: str
) -> None:
    main_branch_id = _main_branch_id(client, generated_trip_id)
    head_revision_id = _head_revision_id(client, generated_trip_id, main_branch_id)

    fork_response = client.post(
        f"/trips/{generated_trip_id}/branches",
        json={"source_revision_id": head_revision_id, "display_name": "Option B"},
    )
    branch_id = fork_response.json()["data"]["branch"]["branch_id"]

    feedback_response = client.post(
        f"/trips/{generated_trip_id}/feedback", json={"feedback_text": "less packed please"}
    )
    assert feedback_response.status_code == 200

    activate_response = client.post(f"/trips/{generated_trip_id}/branches/{branch_id}/activate")
    assert activate_response.status_code == 409
    assert activate_response.json()["errors"][0]["code"] == "BRANCH_SWITCH_BLOCKED"

    # Still on Main.
    assert _main_branch_id(client, generated_trip_id) == main_branch_id


# ---------------------------------------------------------------------------
# Tasks 27/29: legacy regeneration on an activated fork is branch-aware
# ---------------------------------------------------------------------------


def test_legacy_regeneration_on_activated_fork_updates_only_that_branch(
    client: TestClient, generated_trip_id: str
) -> None:
    main_branch_id = _main_branch_id(client, generated_trip_id)
    main_head_before = _head_revision_id(client, generated_trip_id, main_branch_id)

    fork_response = client.post(
        f"/trips/{generated_trip_id}/branches",
        json={
            "source_revision_id": main_head_before,
            "display_name": "Option B",
            "activate_after_create": True,
        },
    )
    assert fork_response.status_code == 201
    branch_id = fork_response.json()["data"]["branch"]["branch_id"]
    assert _main_branch_id(client, generated_trip_id) == branch_id

    _submit_and_regenerate(client, generated_trip_id, "less packed please, remove something")

    # Real production assertions, straight from the persisted state.
    reloaded_state = planning_state_repository.get_by_trip_id(generated_trip_id)
    assert reloaded_state is not None
    assert reloaded_state.metadata.active_branch_id == branch_id

    # Main's own head is untouched by the regeneration that ran on the fork.
    main_head_after = _head_revision_id(client, generated_trip_id, main_branch_id)
    assert main_head_after == main_head_before

    # The fork's head advanced, with the fork's OWN base revision as its
    # real parent (Task 25).
    fork_head_after = _head_revision_id(client, generated_trip_id, branch_id)
    assert fork_head_after != main_head_before

    revision_response = client.get(f"/trips/{generated_trip_id}/revisions/{fork_head_after}")
    assert revision_response.status_code == 200
    revision_data = revision_response.json()["data"]
    assert revision_data["branch_id"] == branch_id
    assert revision_data["parent_revision_id"] == main_head_before


# ---------------------------------------------------------------------------
# Task 41: deterministic branch-list ordering
# ---------------------------------------------------------------------------


def test_branch_list_orders_default_first_then_created_at(
    client: TestClient, generated_trip_id: str
) -> None:
    main_branch_id = _main_branch_id(client, generated_trip_id)
    head_revision_id = _head_revision_id(client, generated_trip_id, main_branch_id)

    client.post(
        f"/trips/{generated_trip_id}/branches",
        json={"source_revision_id": head_revision_id, "display_name": "Zeta"},
    )
    client.post(
        f"/trips/{generated_trip_id}/branches",
        json={"source_revision_id": head_revision_id, "display_name": "Alpha"},
    )

    response = client.get(f"/trips/{generated_trip_id}/branches")
    branch_names = [b["display_name"] for b in response.json()["data"]["branches"]]
    assert branch_names == ["Main", "Zeta", "Alpha"]
