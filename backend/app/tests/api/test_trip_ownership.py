from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.repositories.factory import get_trip_repository
from app.tests.conftest import create_trip_payload

# Step 184D: POST /trips (create, owner assignment) and GET /trips ("My
# Trips") -- both new/changed this step, tested separately from the
# per-route auth-enforcement matrix in test_trip_route_auth_enforcement.py.


def test_post_trips_requires_auth() -> None:
    unauthenticated_client = TestClient(app)

    response = unauthenticated_client.post("/trips", json=create_trip_payload())

    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "AUTHENTICATION_REQUIRED"


def test_post_trips_assigns_owner_id_to_current_user(client: TestClient) -> None:
    me_response = client.get("/auth/me")
    current_user_id = me_response.json()["data"]["user"]["user_id"]

    create_response = client.post("/trips", json=create_trip_payload())
    trip_id = create_response.json()["data"]["trip_id"]

    # Goes through the same factory the route itself uses -- not the raw
    # local_json singleton directly -- so this also works correctly under
    # the optional PERSISTENCE_BACKEND=postgres run.
    trip_record = get_trip_repository().get(trip_id)
    assert trip_record is not None
    assert trip_record.owner_id == current_user_id


def test_post_trips_response_shape_is_unchanged(client: TestClient) -> None:
    """`owner_id` lives only on `TripRecord`, never in the API response --
    `TripResponseData`/`PlanningState` are byte-for-byte the same shape as
    before Step 184D."""
    response = client.post("/trips", json=create_trip_payload())

    data = response.json()["data"]
    assert set(data.keys()) == {"trip_id", "planning_state"}
    assert "owner_id" not in data
    assert "owner_id" not in data["planning_state"]


def test_get_trips_requires_auth() -> None:
    unauthenticated_client = TestClient(app)

    response = unauthenticated_client.get("/trips")

    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "AUTHENTICATION_REQUIRED"


def test_get_trips_returns_only_current_users_own_trips(
    client: TestClient, second_client: TestClient
) -> None:
    own_trip_response = client.post("/trips", json=create_trip_payload())
    own_trip_id = own_trip_response.json()["data"]["trip_id"]

    other_trip_response = second_client.post("/trips", json=create_trip_payload())
    other_trip_id = other_trip_response.json()["data"]["trip_id"]

    my_trips_response = client.get("/trips")
    assert my_trips_response.status_code == 200
    trip_ids = [item["trip_id"] for item in my_trips_response.json()["data"]["trips"]]

    assert own_trip_id in trip_ids
    assert other_trip_id not in trip_ids


def test_get_trips_does_not_expose_full_planning_state(client: TestClient) -> None:
    client.post("/trips", json=create_trip_payload())

    response = client.get("/trips")

    trips = response.json()["data"]["trips"]
    assert len(trips) == 1
    item = trips[0]
    assert set(item.keys()) == {
        "trip_id",
        "status",
        "primary_destination",
        "origin_city",
        "start_date",
        "end_date",
        "created_at",
        "updated_at",
    }
    # No plan/feedback/version/lock data -- this is a summary list, never
    # the full PlanningState.
    assert "planning_state" not in item
    assert "feedback_history" not in item
    assert "experience_plan" not in item


def test_get_trips_item_reflects_the_real_trip_request(client: TestClient) -> None:
    payload = create_trip_payload()
    client.post("/trips", json=payload)

    response = client.get("/trips")

    item = response.json()["data"]["trips"][0]
    assert item["primary_destination"] == payload["primary_destination"]
    assert item["origin_city"] == payload["origin_city"]
    assert item["start_date"] == payload["start_date"]
    assert item["end_date"] == payload["end_date"]
    assert item["status"] == "draft"


def test_get_trips_returns_empty_list_for_a_user_with_no_trips(client: TestClient) -> None:
    response = client.get("/trips")

    assert response.status_code == 200
    assert response.json()["data"]["trips"] == []


def test_get_trips_lists_multiple_own_trips(client: TestClient) -> None:
    client.post("/trips", json=create_trip_payload())
    client.post("/trips", json=create_trip_payload())

    response = client.get("/trips")

    assert len(response.json()["data"]["trips"]) == 2


def test_manual_trip_id_access_of_another_users_trip_returns_403(
    client: TestClient, second_client: TestClient
) -> None:
    """Simulates a user manually pasting/guessing another user's
    trip_id -- confirms it is rejected the same way the route-matrix
    tests already prove for every other route, specifically via the
    plain `GET /trips/{trip_id}` "load existing trip" path."""
    other_trip_response = second_client.post("/trips", json=create_trip_payload())
    other_trip_id = other_trip_response.json()["data"]["trip_id"]

    response = client.get(f"/trips/{other_trip_id}")

    assert response.status_code == 403
    assert response.json()["errors"][0]["code"] == "FORBIDDEN"
