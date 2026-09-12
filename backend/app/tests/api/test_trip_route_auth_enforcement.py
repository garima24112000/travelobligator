from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.planning_state import PlanningState, TravelGroupType, TripRequest
from app.repositories.factory import get_planning_state_repository, get_trip_repository
from app.tests.conftest import create_trip_payload, new_authenticated_client

# Step 184D: the full auth-enforcement matrix across every `/trips/*`
# route -- not just one or two representative ones. Every route below
# (18 of them, everything except `POST /trips` and `GET /trips`, which
# have their own dedicated tests in test_trip_ownership.py) is checked
# for:
#   1. Unauthenticated -> 401 AUTHENTICATION_REQUIRED.
#   2. A different, real, logged-in user (not the trip's owner) -> 403
#      FORBIDDEN.
#   3. The real owner -> passes the gate (never 401/403; whatever status
#      the route's own business logic then produces is out of scope
#      here -- covered by the pre-existing, still-passing smoke suite).
#   4. A legacy/unowned trip (owner_id=None, as any trip created before
#      Step 184D would be) -> 403 for ANY authenticated user, never
#      silently granted to whoever asks first.
#
# `lock_id` in the DELETE route is always a placeholder -- the 401/403
# checks all fire before a lock lookup would ever happen, so its actual
# validity never matters for these tests.

_TRIP_ID_ROUTES: list[tuple[str, str, dict | None]] = [
    ("GET", "/trips/{trip_id}", None),
    ("POST", "/trips/{trip_id}/generate", None),
    ("POST", "/trips/{trip_id}/feedback", {"feedback_text": "Make this less packed"}),
    ("POST", "/trips/{trip_id}/regenerate", {"confirm": True}),
    (
        "POST",
        "/trips/{trip_id}/locks",
        {"locked_item_type": "experience", "locked_item_id": "some_id"},
    ),
    ("DELETE", "/trips/{trip_id}/locks/{lock_id}", None),
    ("GET", "/trips/{trip_id}/destination-context", None),
    ("GET", "/trips/{trip_id}/candidate-quality", None),
    ("GET", "/trips/{trip_id}/experience-plan", None),
    ("GET", "/trips/{trip_id}/validation-report", None),
    ("GET", "/trips/{trip_id}/summary", None),
    ("GET", "/trips/{trip_id}/provider-coverage", None),
    ("GET", "/trips/{trip_id}/regeneration-readiness", None),
    ("GET", "/trips/{trip_id}/regeneration-attempts", None),
    ("GET", "/trips/{trip_id}/generation-progress", None),
    ("GET", "/trips/{trip_id}/ai-candidate-review", None),
    ("POST", "/trips/{trip_id}/ai-candidate-promotions", None),
    ("POST", "/trips/{trip_id}/langgraph-shadow-run", None),
]

assert len(_TRIP_ID_ROUTES) == 18


def _format_path(template: str, trip_id: str, lock_id: str = "lock_placeholder") -> str:
    return template.format(trip_id=trip_id, lock_id=lock_id)


def _call(client: TestClient, method: str, path: str, body: dict | None) -> object:
    if method == "GET":
        return client.get(path)
    if method == "POST":
        return client.post(path, json=body if body is not None else {})
    if method == "DELETE":
        return client.delete(path)
    raise AssertionError(f"Unhandled method: {method}")


def _route_ids(route: tuple[str, str, dict | None]) -> str:
    method, template, _ = route
    return f"{method} {template}"


# ---------------------------------------------------------------------------
# 1. Unauthenticated -> 401, for every route
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", _TRIP_ID_ROUTES, ids=_route_ids)
def test_unauthenticated_request_returns_401_for_every_trip_route(
    route: tuple[str, str, dict | None],
) -> None:
    method, template, body = route
    unauthenticated_client = TestClient(app)

    response = _call(
        unauthenticated_client, method, _format_path(template, "irrelevant_trip_id"), body
    )

    assert response.status_code == 401, f"{method} {template} did not 401 unauthenticated"
    assert response.json()["errors"][0]["code"] == "AUTHENTICATION_REQUIRED"


# ---------------------------------------------------------------------------
# 2. A different, real user -> 403, for every route
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", _TRIP_ID_ROUTES, ids=_route_ids)
def test_wrong_user_returns_403_for_every_trip_route(
    route: tuple[str, str, dict | None],
    client: TestClient,
    second_client: TestClient,
) -> None:
    method, template, body = route
    create_response = client.post("/trips", json=create_trip_payload())
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    response = _call(second_client, method, _format_path(template, trip_id), body)

    assert response.status_code == 403, f"{method} {template} did not 403 for the wrong user"
    assert response.json()["errors"][0]["code"] == "FORBIDDEN"
    # Never leaks who the real owner is, or that the trip is owned at all
    # (vs. simply unowned) -- both produce this identical response.
    assert "email" not in response.text


# ---------------------------------------------------------------------------
# 3. The real owner -> always passes the gate, for every route
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", _TRIP_ID_ROUTES, ids=_route_ids)
def test_owner_passes_the_auth_gate_for_every_trip_route(
    route: tuple[str, str, dict | None], client: TestClient
) -> None:
    method, template, body = route
    create_response = client.post("/trips", json=create_trip_payload())
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    response = _call(client, method, _format_path(template, trip_id), body)

    assert response.status_code not in (401, 403), (
        f"{method} {template} incorrectly blocked the real owner "
        f"(status={response.status_code}, body={response.text})"
    )


# ---------------------------------------------------------------------------
# 4. A legacy/unowned trip (owner_id=None) -> 403 for any authenticated
#    user, for every route -- never silently granted.
# ---------------------------------------------------------------------------


def _create_unowned_trip() -> str:
    """Creates a trip directly through the repositories -- bypassing
    `POST /trips` entirely -- with `owner_id=None`, simulating a trip
    that existed before Step 184D (see `TripRecord.owner_id`'s own
    docstring). Deliberately goes through the same factory
    (`get_trip_repository()`/`get_planning_state_repository()`) the real
    routes use, not the raw local_json singletons directly -- so this
    helper writes to whichever backend is actually active
    (`Settings.persistence_backend`), matching the optional live-Postgres
    run this suite can also be exercised under."""
    trip_request = TripRequest(
        primary_destination="Legacy City",
        start_date="2026-09-01",
        end_date="2026-09-03",
        travelers_count=1,
        travel_group_type=TravelGroupType.SOLO,
    )
    planning_state = PlanningState(trip_request=trip_request)
    get_trip_repository().create(planning_state.trip_id, owner_id=None)
    get_planning_state_repository().save(planning_state)
    return planning_state.trip_id


@pytest.mark.parametrize("route", _TRIP_ID_ROUTES, ids=_route_ids)
def test_unowned_legacy_trip_returns_403_for_every_trip_route(
    route: tuple[str, str, dict | None], client: TestClient
) -> None:
    method, template, body = route
    trip_id = _create_unowned_trip()

    response = _call(client, method, _format_path(template, trip_id), body)

    assert response.status_code == 403, (
        f"{method} {template} did not 403 for an unowned legacy trip "
        f"(status={response.status_code})"
    )
    assert response.json()["errors"][0]["code"] == "FORBIDDEN"


# ---------------------------------------------------------------------------
# Invalid/expired session -> 401 (one representative route each -- the
# full per-mechanism coverage already lives in test_dependencies.py/
# test_auth_routes.py; these confirm the same guarantee holds specifically
# for a /trips/* route, not just /auth/me).
# ---------------------------------------------------------------------------


def test_invalid_session_cookie_returns_401_on_a_trips_route(client: TestClient) -> None:
    create_response = client.post("/trips", json=create_trip_payload())
    trip_id = create_response.json()["data"]["trip_id"]
    client.cookies.set("travelobligator_session", "not-a-real-token")

    response = client.get(f"/trips/{trip_id}")

    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "AUTHENTICATION_REQUIRED"


def test_expired_session_cookie_returns_401_on_a_trips_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    from app.core.config import get_settings

    monkeypatch.setenv("SESSION_TTL_SECONDS", "1")
    get_settings.cache_clear()

    short_lived_client = new_authenticated_client()
    create_response = short_lived_client.post("/trips", json=create_trip_payload())
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    time.sleep(2)

    response = short_lived_client.get(f"/trips/{trip_id}")

    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "AUTHENTICATION_REQUIRED"
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Logout then protected route -> 401
# ---------------------------------------------------------------------------


def test_logout_then_protected_route_returns_401(client: TestClient) -> None:
    create_response = client.post("/trips", json=create_trip_payload())
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    logout_response = client.post("/auth/logout")
    assert logout_response.status_code == 200

    response = client.get(f"/trips/{trip_id}")

    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "AUTHENTICATION_REQUIRED"
