from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.models.planning_state import PlanningState, TravelGroupType, TripRequest
from app.repositories.planning_state_repository import planning_state_repository
from app.repositories.trip_repository import trip_repository
from app.tests.conftest import create_trip_payload, new_authenticated_client

# P0: size limits of a NEW trip request (`schemas.trips.TripCreateRequest`) and the saved-trip limit of
# one account. Request validation only -- nothing here plans anything.

_START = date(2027, 3, 1)


def _payload(**overrides: object) -> dict:
    return {**create_trip_payload(), **overrides}


def _dates(days: int) -> dict:
    return {"start_date": _START.isoformat(), "end_date": (_START + timedelta(days=days - 1)).isoformat()}


def _env(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def _rejection(response) -> dict:
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["success"] is False and body["data"] is None
    assert body["message"] == "Request validation failed."
    assert all(error["code"] == "VALIDATION_ERROR" for error in body["errors"])
    return body["errors"][0]


# -- defaults ---------------------------------------------------------------------------------------


def test_the_documented_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.max_trip_days == 7
    assert settings.trip_request_max_list_items == 20
    assert settings.trip_request_max_item_length == 120
    assert settings.max_trips_per_user == 20


@pytest.mark.parametrize(
    "variable, value",
    [
        ("MAX_TRIP_DAYS", "0"),
        ("MAX_TRIP_DAYS", "61"),
        ("TRIP_REQUEST_MAX_LIST_ITEMS", "0"),
        ("TRIP_REQUEST_MAX_ITEM_LENGTH", "0"),
        ("MAX_TRIPS_PER_USER", "-1"),
    ],
)
def test_an_out_of_range_limit_is_rejected_when_settings_load(variable: str, value: str) -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, **{variable: value})


# -- trip length ------------------------------------------------------------------------------------


@pytest.mark.parametrize("days", [1, 3, 7])
def test_a_trip_up_to_seven_calendar_days_is_accepted(client: TestClient, days: int) -> None:
    response = client.post("/trips", json=_payload(**_dates(days)))
    assert response.status_code == 201, response.text


@pytest.mark.parametrize("days", [8, 30, 366])
def test_a_longer_trip_is_a_422_that_names_the_end_date(client: TestClient, days: int) -> None:
    error = _rejection(client.post("/trips", json=_payload(**_dates(days))))
    assert error["field"] == "end_date"
    assert "at most 7 days" in error["message"]
    assert client.get("/trips").json()["data"]["trips"] == []  # nothing was stored


def test_the_maximum_is_inclusive_of_the_first_and_the_last_day(client: TestClient) -> None:
    # 1 March .. 7 March is seven days; 1 March .. 8 March is eight.
    assert client.post("/trips", json=_payload(start_date="2027-03-01", end_date="2027-03-07")).status_code == 201
    assert client.post("/trips", json=_payload(start_date="2027-03-01", end_date="2027-03-08")).status_code == 422


def test_the_trip_length_limit_is_configurable(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, MAX_TRIP_DAYS="3")
    assert client.post("/trips", json=_payload(**_dates(3))).status_code == 201
    assert "at most 3 days" in _rejection(client.post("/trips", json=_payload(**_dates(4))))["message"]
    _env(monkeypatch, MAX_TRIP_DAYS="10")
    assert client.post("/trips", json=_payload(**_dates(10))).status_code == 201


def test_an_end_before_the_start_is_still_a_422(client: TestClient) -> None:
    _rejection(client.post("/trips", json=_payload(start_date="2027-03-05", end_date="2027-03-01")))


# -- lists ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field, label",
    [
        ("interests", "interests"),
        ("must_visit", "must-visit places"),
        ("must_avoid", "places to avoid"),
        ("constraints", "constraints"),
    ],
)
def test_text_lists_are_bounded_in_count_and_entry_length(client: TestClient, field: str, label: str) -> None:
    at_limit = [f"entry {index}" for index in range(20)]
    assert client.post("/trips", json=_payload(**{field: at_limit})).status_code == 201

    too_many = _rejection(client.post("/trips", json=_payload(**{field: [*at_limit, "one more"]})))
    assert too_many["field"] == field
    assert too_many["message"].endswith(f"Please list at most 20 {label}.")

    assert client.post("/trips", json=_payload(**{field: ["x" * 120]})).status_code == 201
    too_long = _rejection(client.post("/trips", json=_payload(**{field: ["x" * 121]})))
    assert too_long["field"] == field
    assert f"Each entry in {label} can be at most 120 characters long." in too_long["message"]


def test_entries_are_measured_after_trimming(client: TestClient) -> None:
    padded = "   " + "x" * 120 + "   "
    response = client.post("/trips", json=_payload(interests=[padded, "   "]))
    assert response.status_code == 201
    assert response.json()["data"]["planning_state"]["trip_request"]["interests"] == ["x" * 120]


def test_choice_lists_are_bounded_too(client: TestClient) -> None:
    error = _rejection(client.post("/trips", json=_payload(transport_preferences=["walking"] * 21)))
    assert error["field"] == "transport_preferences"
    error = _rejection(client.post("/trips", json=_payload(preferred_accommodation_types=["hotel"] * 21)))
    assert error["field"] == "preferred_accommodation_types"


def test_list_limits_are_configurable(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, TRIP_REQUEST_MAX_LIST_ITEMS="2", TRIP_REQUEST_MAX_ITEM_LENGTH="5")
    assert client.post("/trips", json=_payload(must_visit=["a", "b"])).status_code == 201
    assert "at most 2 must-visit places" in _rejection(
        client.post("/trips", json=_payload(must_visit=["a", "b", "c"]))
    )["message"]
    assert "at most 5 characters" in _rejection(client.post("/trips", json=_payload(interests=["abcdef"])))["message"]


def test_every_violation_is_reported_in_one_response(client: TestClient) -> None:
    response = client.post(
        "/trips", json=_payload(**_dates(9), interests=["i"] * 21, must_visit=["y" * 300])
    )
    assert response.status_code == 422
    assert {error["field"] for error in response.json()["errors"]} == {"end_date", "interests", "must_visit"}


def test_validation_messages_are_written_for_a_traveller(client: TestClient) -> None:
    responses = [
        client.post("/trips", json=_payload(**_dates(9))),
        client.post("/trips", json=_payload(constraints=["c"] * 21)),
        client.post("/trips", json=_payload(must_avoid=["z" * 121])),
    ]
    for response in responses:
        message = response.json()["errors"][0]["message"].removeprefix("Value error, ")
        assert "_" not in message and "TripCreateRequest" not in message and "MAX_" not in message


# -- stored trips are not re-validated --------------------------------------------------------------


def test_a_stored_trip_beyond_the_limits_still_loads(client: TestClient) -> None:
    """The limits belong to a NEW request. A trip saved under a larger limit (or before one existed)
    must keep loading: `TripRequest`, the stored shape, carries no such limit."""
    owner_id = client.get("/auth/me").json()["data"]["user"]["user_id"]
    stored = PlanningState(
        trip_request=TripRequest(
            primary_destination="Testville, Testland",
            start_date=_START,
            end_date=_START + timedelta(days=29),
            travelers_count=2,
            travel_group_type=TravelGroupType.COUPLE,
            interests=[f"interest {index}" for index in range(60)],
            must_visit=["m" * 500],
        )
    )
    trip_repository.create(stored.trip_id, owner_id=owner_id)
    planning_state_repository.save(stored)

    response = client.get(f"/trips/{stored.trip_id}")
    assert response.status_code == 200
    request = response.json()["data"]["planning_state"]["trip_request"]
    assert request["end_date"] == (_START + timedelta(days=29)).isoformat()
    assert len(request["interests"]) == 60
    assert client.get(f"/trips/{stored.trip_id}/summary").status_code == 200


def test_the_stored_request_is_a_plain_trip_request(client: TestClient) -> None:
    trip_id = client.post("/trips", json=_payload(interests=["food"])).json()["data"]["trip_id"]
    assert type(planning_state_repository.get_by_trip_id(trip_id).trip_request) is TripRequest


# -- saved trips per account ------------------------------------------------------------------------


def test_an_account_holds_at_most_the_configured_number_of_trips(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, MAX_TRIPS_PER_USER="3")
    for _ in range(3):
        assert client.post("/trips", json=_payload()).status_code == 201

    refused = client.post("/trips", json=_payload())
    assert refused.status_code == 429
    body = refused.json()
    assert body["success"] is False and body["data"] is None
    assert body["errors"][0]["code"] == "TRIP_LIMIT_REACHED"
    assert body["errors"][0]["message"] == "You have reached the limit of 3 saved trips for one account."
    assert len(client.get("/trips").json()["data"]["trips"]) == 3  # no fourth trip, no orphan state
    assert len(planning_state_repository._states) == 3


def test_the_trip_limit_is_per_account(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, MAX_TRIPS_PER_USER="1")
    assert client.post("/trips", json=_payload()).status_code == 201
    assert client.post("/trips", json=_payload()).status_code == 429
    other = new_authenticated_client()
    assert other.post("/trips", json=_payload()).status_code == 201


def test_the_default_trip_limit_is_twenty(client: TestClient) -> None:
    for _ in range(20):
        assert client.post("/trips", json=_payload()).status_code == 201
    assert client.post("/trips", json=_payload()).json()["errors"][0]["code"] == "TRIP_LIMIT_REACHED"


def test_zero_switches_the_trip_limit_off(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, MAX_TRIPS_PER_USER="0")
    for _ in range(22):
        assert client.post("/trips", json=_payload()).status_code == 201


def test_an_invalid_request_does_not_use_up_the_trip_limit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, MAX_TRIPS_PER_USER="1")
    assert client.post("/trips", json=_payload(**_dates(9))).status_code == 422
    assert client.post("/trips", json=_payload()).status_code == 201
