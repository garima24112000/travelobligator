from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.common import DataStatus, GeoPoint, ProviderStatus
from app.models.providers import NormalizedPlace, ProviderResponse
from app.providers.base import PlacesProvider
from app.providers.gateway import provider_gateway
from app.repositories.planning_state_repository import planning_state_repository
from app.repositories.trip_repository import trip_repository
from app.storage.local_json_store import LocalJsonStore


class DeterministicTestPlacesProvider(PlacesProvider):
    """Deterministic test double standing in for `OpenStreetMapPlacesAdapter`.

    Lets API smoke tests exercise the full pipeline without depending on
    real Overpass/Nominatim network availability. This data is test-only
    and is never wired into application runtime code; the `provider_name`
    matches the real OSM adapter so ProviderCoverageService labels coverage
    exactly as it would for a real OSM response (e.g. `accommodations:
    open_poi_available`).
    """

    provider_name = "openstreetmap_places"

    def search_attractions(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return self._test_provider_response(
            "attraction",
            [
                ("test/attraction/1", "Test Fixture Attraction One"),
                ("test/attraction/2", "Test Fixture Attraction Two"),
            ],
        )

    def search_restaurants(
        self, area: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return self._test_provider_response(
            "restaurant", [("test/restaurant/1", "Test Fixture Restaurant One")]
        )

    def search_accommodation_pois(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return self._test_provider_response(
            "hotel", [("test/accommodation/1", "Test Fixture Accommodation One")]
        )

    def _test_provider_response(
        self, category: str, entries: list[tuple[str, str]]
    ) -> ProviderResponse[Any]:
        places = [
            NormalizedPlace(
                place_id=place_id,
                name=name,
                category=category,
                coordinates=GeoPoint(lat=0.0, lng=0.0),
                source=self.provider_name,
                data_status=DataStatus.LIVE,
                confidence=0.6,
            )
            for place_id, name in entries
        ]
        return ProviderResponse[list[NormalizedPlace]](
            provider_name=self.provider_name,
            provider_type=self.provider_type,
            status=ProviderStatus.SUCCESS,
            data_status=DataStatus.LIVE,
            data=places,
            confidence=0.65,
            message="Test fixture data; not a real provider call.",
        )


@pytest.fixture(autouse=True)
def _deterministic_places_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the real OSM adapter with a deterministic test double for every test."""
    monkeypatch.setattr(provider_gateway, "places", DeterministicTestPlacesProvider())


@pytest.fixture(autouse=True)
def _reset_in_memory_repositories(tmp_path: Path) -> None:
    """Isolate the trip/planning-state repositories between test functions.

    Points both module-level repository singletons at a fresh temporary
    JSON file (shared between them, matching how they share the real
    storage file in app.core.config.Settings.local_storage_path) instead of
    the real local development storage file, so test runs never read or
    write persistent project data under backend/.data/.
    """
    test_store = LocalJsonStore(tmp_path / "test_travelobligator_state.json")
    trip_repository._store = test_store
    trip_repository._trips = {}
    planning_state_repository._store = test_store
    planning_state_repository._states = {}
    yield


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def create_trip_payload() -> dict[str, Any]:
    return {
        "destination_scope": "single_city",
        "primary_destination": "Testville, Testland",
        "origin_city": "Home City",
        "start_date": "2026-08-10",
        "end_date": "2026-08-12",
        "travelers_count": 2,
        "travel_group_type": "couple",
    }


@pytest.fixture()
def created_trip_id(client: TestClient) -> str:
    response = client.post("/trips", json=create_trip_payload())
    assert response.status_code == 201
    return response.json()["data"]["trip_id"]


@pytest.fixture()
def generated_trip_id(client: TestClient, created_trip_id: str) -> str:
    response = client.post(f"/trips/{created_trip_id}/generate")
    assert response.status_code == 200
    return created_trip_id
