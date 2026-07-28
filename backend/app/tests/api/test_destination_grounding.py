from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.providers.base import CurrencyProvider, HolidayProvider, WeatherProvider
from app.providers.gateway import provider_gateway
from app.providers.places import openstreetmap_adapter
from app.providers.places.openstreetmap_adapter import OpenStreetMapPlacesAdapter

# ---------------------------------------------------------------------------
# Step 155C: destination grounding and provider containment regression test.
#
# Reproduces the exact reported bug: primary_destination="New York",
# origin_city="Seattle", must_visit=["Empire State Building"] generated a
# plan scheduling real, named OpenStreetMap places from Rheydt/
# Mönchengladbach/Wegberg, Germany. This exercises the real
# `OpenStreetMapPlacesAdapter` (not the deterministic test double used
# elsewhere) with a fake httpx client that deliberately mixes real-looking
# German-town POIs into the raw Overpass responses, proving the
# containment fix (`_is_within_destination`/bounding-box filtering) throws
# them out even though they are real, named provider results.
# ---------------------------------------------------------------------------

_FORBIDDEN_GERMAN_NAMES = (
    "Rathaus Rheydt",
    "Balderich",
    "Fischerturm",
    "Gedenkstein f.d. Synagoge",
    "Wegberg",
    "Mönchengladbach",
    "Rheydt",
)

_NYC_BBOX = ["40.4961", "40.9153", "-74.2557", "-73.7002"]


class _FakeHttpResponse:
    def __init__(self, json_data: Any) -> None:
        self._json_data = json_data

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._json_data


def _node(element_id: int, name: str, lat: float, lon: float, **tags: str) -> dict[str, Any]:
    merged_tags = {"name": name, **tags}
    return {"type": "node", "id": element_id, "lat": lat, "lon": lon, "tags": merged_tags}


class _FakeOsmClient:
    """Fake httpx.Client dispatching Nominatim GET lookups by the `q` param
    and Overpass POST queries by which tag filter they contain.
    """

    def __init__(self) -> None:
        self.get_queries: list[str] = []
        self.post_queries: list[str] = []

    def __enter__(self) -> "_FakeOsmClient":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def get(self, url: str, params: dict[str, Any] | None = None) -> _FakeHttpResponse:
        query = (params or {}).get("q")
        self.get_queries.append(query)
        if query == "New York":
            return _FakeHttpResponse(
                [
                    {
                        "lat": "40.7128",
                        "lon": "-74.0060",
                        "display_name": "New York, United States",
                        "boundingbox": _NYC_BBOX,
                    }
                ]
            )
        if query == "Empire State Building, New York":
            return _FakeHttpResponse(
                [
                    {
                        "place_id": 1,
                        "osm_type": "way",
                        "osm_id": 5001,
                        "lat": "40.7484",
                        "lon": "-73.9857",
                        "display_name": (
                            "Empire State Building, 350 Fifth Avenue, Manhattan, "
                            "New York, United States"
                        ),
                        "namedetails": {"name": "Empire State Building"},
                        "type": "attraction",
                        "class": "tourism",
                    }
                ]
            )
        return _FakeHttpResponse([])

    def post(self, url: str, data: dict[str, Any] | None = None) -> _FakeHttpResponse:
        query = (data or {}).get("data", "")
        self.post_queries.append(query)
        if "historic" in query or "zoo|theme_park" in query:
            return _FakeHttpResponse({"elements": self._attraction_elements()})
        if "restaurant|cafe|fast_food" in query:
            return _FakeHttpResponse({"elements": self._restaurant_elements()})
        if "hotel|hostel|guest_house" in query:
            return _FakeHttpResponse({"elements": self._accommodation_elements()})
        return _FakeHttpResponse({"elements": []})

    @staticmethod
    def _attraction_elements() -> list[dict[str, Any]]:
        return [
            _node(1, "Central Park", 40.785091, -73.968285, tourism="attraction"),
            _node(2, "Times Square", 40.7580, -73.9855, tourism="attraction"),
            # Contaminated results from the reported bug -- must be
            # filtered out by containment even though they are real,
            # named results a provider genuinely returned.
            _node(3, "Rathaus Rheydt", 51.1743, 6.4453, tourism="attraction"),
            _node(4, "Balderich", 51.1805, 6.4428, historic="yes"),
            _node(5, "Gedenkstein f.d. Synagoge", 51.181, 6.443, historic="memorial"),
        ]

    @staticmethod
    def _restaurant_elements() -> list[dict[str, Any]]:
        return [
            _node(10, "Katz's Delicatessen", 40.7223, -73.9874, amenity="restaurant"),
            _node(11, "Joe's Pizza", 40.7301, -74.0021, amenity="restaurant"),
            _node(12, "Fischerturm", 51.19, 6.45, amenity="cafe"),
        ]

    @staticmethod
    def _accommodation_elements() -> list[dict[str, Any]]:
        return [
            _node(20, "The Plaza Hotel", 40.7645, -73.9743, tourism="hotel"),
            _node(21, "Pod Times Square", 40.7590, -73.9880, tourism="hotel"),
            _node(22, "Wegberg Inn", 51.15, 6.28, tourism="hotel"),
        ]


class _AllContaminatedFakeOsmClient(_FakeOsmClient):
    """Every candidate result -- attractions, restaurants, accommodation
    POIs, and the must-visit lookup -- is outside the resolved New York
    destination. Used to prove that when containment filters everything
    out, nothing unrelated gets scheduled and the plan is blocked.
    """

    @staticmethod
    def _attraction_elements() -> list[dict[str, Any]]:
        return [
            _node(3, "Rathaus Rheydt", 51.1743, 6.4453, tourism="attraction"),
            _node(4, "Balderich", 51.1805, 6.4428, historic="yes"),
        ]

    @staticmethod
    def _restaurant_elements() -> list[dict[str, Any]]:
        return [_node(12, "Fischerturm", 51.19, 6.45, amenity="cafe")]

    @staticmethod
    def _accommodation_elements() -> list[dict[str, Any]]:
        return [_node(22, "Wegberg Inn", 51.15, 6.28, tourism="hotel")]

    def get(self, url: str, params: dict[str, Any] | None = None) -> _FakeHttpResponse:
        query = (params or {}).get("q")
        self.get_queries.append(query)
        if query == "New York":
            return _FakeHttpResponse(
                [
                    {
                        "lat": "40.7128",
                        "lon": "-74.0060",
                        "display_name": "New York, United States",
                        "boundingbox": _NYC_BBOX,
                    }
                ]
            )
        if query == "Empire State Building, New York":
            # Even the targeted must-visit lookup comes back contaminated
            # -- must still be rejected by containment, never accepted or
            # silently swapped for an unrelated attraction.
            return _FakeHttpResponse(
                [
                    {
                        "place_id": 2,
                        "osm_type": "way",
                        "osm_id": 5002,
                        "lat": "51.1743",
                        "lon": "6.4453",
                        "display_name": "Rathaus Rheydt, Mönchengladbach, Germany",
                        "namedetails": {"name": "Rathaus Rheydt"},
                        "type": "attraction",
                        "class": "tourism",
                    }
                ]
            )
        return _FakeHttpResponse([])


def _new_york_trip_payload() -> dict[str, Any]:
    return {
        "destination_scope": "single_city",
        "primary_destination": "New York",
        "origin_city": "Seattle",
        "start_date": "2026-08-10",
        "end_date": "2026-08-12",
        "travelers_count": 2,
        "travel_group_type": "couple",
        "pace": "balanced",
        "must_visit": ["Empire State Building"],
    }


def _assert_no_forbidden_names(*payloads: object) -> None:
    all_text = "".join(str(payload) for payload in payloads)
    for forbidden in _FORBIDDEN_GERMAN_NAMES:
        assert forbidden not in all_text, f"Unrelated German POI leaked into the plan: {forbidden}"


def _install_places_adapter_with_fake_network(
    monkeypatch: pytest.MonkeyPatch, fake_client: _FakeOsmClient
) -> None:
    """Wires the real `OpenStreetMapPlacesAdapter` (backed by `fake_client`
    instead of real network) into `provider_gateway.places`, and swaps
    weather/holiday/currency for their honest not-connected base-class
    implementations -- this test is about places/containment behavior
    only, so it must not depend on real network access for unrelated
    providers.
    """
    monkeypatch.setattr(provider_gateway, "places", OpenStreetMapPlacesAdapter())
    monkeypatch.setattr(provider_gateway, "weather", WeatherProvider())
    monkeypatch.setattr(provider_gateway, "holiday", HolidayProvider())
    monkeypatch.setattr(provider_gateway, "currency", CurrencyProvider())
    monkeypatch.setattr(openstreetmap_adapter.httpx, "Client", lambda **kwargs: fake_client)


def test_new_york_trip_does_not_schedule_unrelated_german_pois(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for the reported bug: a "New York" trip with
    must_visit=["Empire State Building"] must never surface the unrelated
    German POIs from the bug report, and every scheduled experience must
    be geographically contained to the resolved destination.
    """
    fake_client = _FakeOsmClient()
    _install_places_adapter_with_fake_network(monkeypatch, fake_client)

    create_response = client.post("/trips", json=_new_york_trip_payload())
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    destination_response = client.get(f"/trips/{trip_id}/destination-context")
    assert destination_response.status_code == 200
    destination_context = destination_response.json()["data"]["destination_context"]

    experience_response = client.get(f"/trips/{trip_id}/experience-plan")
    assert experience_response.status_code == 200
    experience_plan = experience_response.json()["data"]["experience_plan"]

    _assert_no_forbidden_names(destination_context, experience_plan)

    # Every scheduled experience with coordinates must fall inside the
    # resolved New York bounding box.
    south, north, west, east = (float(value) for value in _NYC_BBOX)
    scheduled_experiences = [
        experience
        for day in experience_plan["daily_plans"]
        for experience in day["experiences"]
    ]
    assert scheduled_experiences, "expected at least one grounded scheduled experience"
    for experience in scheduled_experiences:
        coordinates = experience["coordinates"]
        assert coordinates is not None
        assert south <= coordinates["lat"] <= north
        assert west <= coordinates["lng"] <= east

    # The must-visit request is honored and grounded to the resolved
    # destination -- not silently dropped or replaced with an unrelated
    # attraction.
    experience_names = {experience["name"] for experience in scheduled_experiences}
    assert "Empire State Building" in experience_names


def test_all_poi_containment_failures_result_in_no_schedule_and_blocked_validation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement 8: if every candidate result (attractions, restaurants,
    accommodation POIs, and the must-visit lookup) fails containment, the
    experience plan must not schedule any unrelated experience, and the
    plan must be blocked with clear unavailable data -- never silently
    presented as ready.
    """
    fake_client = _AllContaminatedFakeOsmClient()
    _install_places_adapter_with_fake_network(monkeypatch, fake_client)

    create_response = client.post("/trips", json=_new_york_trip_payload())
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    destination_response = client.get(f"/trips/{trip_id}/destination-context")
    destination_context = destination_response.json()["data"]["destination_context"]
    assert destination_context["candidate_pois"] == []
    assert destination_context["candidate_restaurants"] == []
    assert destination_context["candidate_accommodation_pois"] == []

    experience_response = client.get(f"/trips/{trip_id}/experience-plan")
    experience_plan = experience_response.json()["data"]["experience_plan"]
    scheduled_experiences = [
        experience
        for day in experience_plan["daily_plans"]
        for experience in day["experiences"]
    ]
    assert scheduled_experiences == []

    _assert_no_forbidden_names(destination_context, experience_plan)

    validation_response = client.get(f"/trips/{trip_id}/validation-report")
    assert validation_response.status_code == 200
    validation_report = validation_response.json()["data"]["validation_report"]
    assert validation_report["readiness_status"] == "blocked"
    assert len(validation_report["critical_issues"]) > 0

    coverage_response = client.get(f"/trips/{trip_id}/provider-coverage")
    provider_coverage = coverage_response.json()["data"]["provider_coverage"]
    # Never "success" -- the only real Overpass results were all
    # geographically unrelated to the resolved destination.
    assert provider_coverage["places"] != "success"
