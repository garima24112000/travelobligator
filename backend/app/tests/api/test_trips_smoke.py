from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.models.common import DataStatus, GeoPoint, ProviderStatus
from app.models.providers import (
    NormalizedDailyWeather,
    NormalizedExchangeRate,
    NormalizedHoliday,
    NormalizedPlace,
    ProviderResponse,
)
from app.providers.base import (
    CurrencyProvider,
    HolidayProvider,
    PlacesProvider,
    RoutesProvider,
    WeatherProvider,
    failed_response,
    unavailable_response,
)
from app.providers.gateway import provider_gateway


def assert_api_response_shape(body: dict[str, Any]) -> None:
    assert set(["success", "data", "message", "errors", "metadata"]).issubset(body.keys())
    assert isinstance(body["success"], bool)
    assert isinstance(body["errors"], list)
    assert isinstance(body["metadata"], dict)
    assert "request_id" in body["metadata"]
    assert "timestamp" in body["metadata"]


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["status"] == "ok"


def test_create_trip(client: TestClient) -> None:
    response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-12",
            "travelers_count": 2,
            "travel_group_type": "couple",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert_api_response_shape(body)
    assert body["success"] is True
    assert body["data"]["trip_id"]
    assert body["data"]["planning_state"]["destination_context"] is None
    assert body["data"]["planning_state"]["experience_plan"] is None
    assert body["data"]["planning_state"]["validation_report"] is None


def test_get_trip(client: TestClient, created_trip_id: str) -> None:
    response = client.get(f"/trips/{created_trip_id}")
    assert response.status_code == 200
    body = response.json()
    assert_api_response_shape(body)
    assert body["data"]["trip_id"] == created_trip_id


def test_destination_context_before_generate_returns_409(
    client: TestClient, created_trip_id: str
) -> None:
    response = client.get(f"/trips/{created_trip_id}/destination-context")
    assert response.status_code == 409
    body = response.json()
    assert_api_response_shape(body)
    assert body["success"] is False
    assert body["data"] is None
    assert body["errors"][0]["code"] == "DATA_UNAVAILABLE"
    assert "not been generated" in body["errors"][0]["message"]


def test_experience_plan_before_generate_returns_409(
    client: TestClient, created_trip_id: str
) -> None:
    response = client.get(f"/trips/{created_trip_id}/experience-plan")
    assert response.status_code == 409
    body = response.json()
    assert_api_response_shape(body)
    assert body["success"] is False
    assert body["errors"][0]["code"] == "DATA_UNAVAILABLE"
    assert "not been generated" in body["errors"][0]["message"]


def test_validation_report_before_generate_returns_409(
    client: TestClient, created_trip_id: str
) -> None:
    response = client.get(f"/trips/{created_trip_id}/validation-report")
    assert response.status_code == 409
    body = response.json()
    assert_api_response_shape(body)
    assert body["success"] is False
    assert body["errors"][0]["code"] == "DATA_UNAVAILABLE"
    assert "not been generated" in body["errors"][0]["message"]


def test_generate_trip_plan(client: TestClient, created_trip_id: str) -> None:
    response = client.post(f"/trips/{created_trip_id}/generate")
    assert response.status_code == 200
    body = response.json()
    assert_api_response_shape(body)
    planning_state = body["data"]["planning_state"]
    assert planning_state["destination_context"] is not None
    assert planning_state["experience_plan"] is not None
    assert planning_state["validation_report"] is not None
    # The deterministic test places provider returns real candidate_pois, so the
    # conservative scheduling step schedules them and the plan is no longer blocked;
    # it is still not "ready" because route/timing feasibility is not implemented.
    assert planning_state["validation_report"]["readiness_status"] == "needs_review"


def test_destination_context_after_generate(
    client: TestClient, generated_trip_id: str
) -> None:
    response = client.get(f"/trips/{generated_trip_id}/destination-context")
    assert response.status_code == 200
    body = response.json()
    assert_api_response_shape(body)
    destination_context = body["data"]["destination_context"]
    assert len(destination_context["candidate_pois"]) > 0
    assert len(destination_context["candidate_restaurants"]) > 0
    assert len(destination_context["candidate_accommodation_pois"]) > 0


def test_experience_plan_after_generate(client: TestClient, generated_trip_id: str) -> None:
    response = client.get(f"/trips/{generated_trip_id}/experience-plan")
    assert response.status_code == 200
    body = response.json()
    assert_api_response_shape(body)
    experience_plan = body["data"]["experience_plan"]

    assert len(experience_plan["daily_plans"]) > 0

    # The deterministic test provider returns real candidate_pois, so at least
    # one day should have scheduled experiences drawn from those candidates.
    all_experiences = [
        experience
        for day_plan in experience_plan["daily_plans"]
        for experience in day_plan["experiences"]
    ]
    assert len(all_experiences) > 0

    for day_plan in experience_plan["daily_plans"]:
        # No route/timing/cost estimation is implemented yet at the day level.
        assert day_plan["estimated_walking_km"] is None
        assert day_plan["estimated_travel_time_minutes"] is None
        assert day_plan["estimated_cost"] is None

    for experience in all_experiences:
        # Scheduled experiences must come from real candidates with no invented
        # times, durations, or cost/route facts.
        assert experience["name"] in {
            "Test Fixture Attraction One",
            "Test Fixture Attraction Two",
        }
        assert experience["start_time"] is None
        assert experience["end_time"] is None
        assert experience["estimated_duration_minutes"] is None

    assert body["data"]["validation_report"]["readiness_status"] == "needs_review"


def test_validation_report_after_generate(client: TestClient, generated_trip_id: str) -> None:
    response = client.get(f"/trips/{generated_trip_id}/validation-report")
    assert response.status_code == 200
    body = response.json()
    assert_api_response_shape(body)
    validation_report = body["data"]["validation_report"]
    # Experiences were scheduled from real candidates, so the plan is no longer
    # blocked, but route/timing feasibility is not implemented yet, so it is
    # needs_review rather than ready.
    assert validation_report["readiness_status"] == "needs_review"
    assert validation_report["critical_issues"] == []
    assert len(validation_report["warnings"]) > 0
    assert any(
        "feasibility" in warning["category"] for warning in validation_report["warnings"]
    )


def test_trip_summary_before_generate(client: TestClient, created_trip_id: str) -> None:
    response = client.get(f"/trips/{created_trip_id}/summary")
    assert response.status_code == 200
    body = response.json()
    assert_api_response_shape(body)
    summary = body["data"]

    assert summary["trip_id"] == created_trip_id
    assert summary["primary_destination"] == "Testville, Testland"
    assert summary["destination_context_generated"] is False
    assert summary["experience_plan_generated"] is False
    assert summary["validation_report_generated"] is False
    assert summary["candidate_pois_count"] == 0
    assert summary["candidate_restaurants_count"] == 0
    assert summary["candidate_accommodation_pois_count"] == 0
    assert summary["scheduled_experiences_count"] == 0
    assert summary["validation_status"] is None
    assert summary["main_blocking_reason"] is None
    assert summary["main_review_reason"] is None


def test_trip_summary_after_generate(client: TestClient, generated_trip_id: str) -> None:
    response = client.get(f"/trips/{generated_trip_id}/summary")
    assert response.status_code == 200
    body = response.json()
    assert_api_response_shape(body)
    summary = body["data"]

    assert summary["trip_id"] == generated_trip_id
    assert summary["destination_context_generated"] is True
    assert summary["experience_plan_generated"] is True
    assert summary["validation_report_generated"] is True

    # The deterministic test places provider returns 2 attractions, 1 restaurant,
    # and 1 accommodation POI, all of which should be reflected honestly.
    assert summary["candidate_pois_count"] == 2
    assert summary["candidate_restaurants_count"] == 1
    assert summary["candidate_accommodation_pois_count"] == 1
    assert summary["scheduled_experiences_count"] > 0

    assert summary["validation_status"] == "needs_review"
    assert summary["main_blocking_reason"] is None
    assert summary["main_review_reason"] is not None
    assert "feasibility" in summary["main_review_reason"].lower()


def test_provider_coverage_after_generate(client: TestClient, generated_trip_id: str) -> None:
    response = client.get(f"/trips/{generated_trip_id}/provider-coverage")
    assert response.status_code == 200
    body = response.json()
    assert_api_response_shape(body)
    assert body["data"]["provider_coverage"]["places"] == "success"


class _MixedFieldStatusTestPlacesProvider(PlacesProvider):
    """Test-only double where the same provider (`openstreetmap_places`)
    succeeds for attractions, falls back for restaurants, and fails for
    accommodation POIs -- used to prove `provider_status` keeps a separate
    entry per provider+field instead of the later accommodation failure
    overwriting the earlier places/restaurants results (see
    ProviderCoverageService.record_provider_result).
    """

    provider_name = "openstreetmap_places"

    def search_attractions(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        places = [
            NormalizedPlace(
                place_id="test/mixed/attraction",
                name="Mixed Fixture Attraction",
                category="landmark",
                coordinates=GeoPoint(lat=0.0, lng=0.0),
                source=self.provider_name,
                data_status=DataStatus.LIVE,
                confidence=0.6,
            )
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

    def search_restaurants(
        self, area: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        places = [
            NormalizedPlace(
                place_id="test/mixed/restaurant",
                name="Mixed Fixture Restaurant",
                category="restaurant",
                coordinates=GeoPoint(lat=0.0, lng=0.0),
                source=self.provider_name,
                data_status=DataStatus.FALLBACK_USED,
                confidence=0.4,
            )
        ]
        return ProviderResponse[list[NormalizedPlace]](
            provider_name=self.provider_name,
            provider_type=self.provider_type,
            status=ProviderStatus.FALLBACK_USED,
            data_status=DataStatus.FALLBACK_USED,
            data=places,
            fallback_used=True,
            confidence=0.4,
            message="Test fixture fallback data; not a real provider call.",
        )

    def search_accommodation_pois(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return failed_response(
            self.provider_name,
            self.provider_type,
            unavailable_fields=["accommodation_pois"],
            message="Test fixture failure; not a real provider call.",
        )


def test_provider_status_keeps_separate_entries_per_field_for_same_provider(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provider_gateway, "places", _MixedFieldStatusTestPlacesProvider())

    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-12",
            "travelers_count": 2,
            "travel_group_type": "couple",
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{trip_id}/provider-coverage")
    assert response.status_code == 200
    data = response.json()["data"]
    provider_status = data["provider_status"]

    # Each field gets its own status entry, keyed by provider + field, so the
    # later accommodation failure does not overwrite the earlier
    # places/restaurants results under a single "openstreetmap_places" key.
    assert "openstreetmap_places" not in provider_status
    places_entry = provider_status["openstreetmap_places:places"]
    restaurants_entry = provider_status["openstreetmap_places:restaurants"]
    accommodations_entry = provider_status["openstreetmap_places:accommodations"]

    assert places_entry["status"] == "success"
    assert places_entry["provider_name"] == "openstreetmap_places"

    assert restaurants_entry["status"] == "fallback_used"
    assert restaurants_entry["provider_name"] == "openstreetmap_places"

    assert accommodations_entry["status"] == "failed"
    assert accommodations_entry["provider_name"] == "openstreetmap_places"

    # provider_coverage (the per-field summary) is untouched by this fix;
    # places/restaurants still reflect the real openstreetmap_places result.
    # accommodations is later overwritten to "not_connected" by
    # StayTransportService's separate (still-unconnected) accommodation
    # provider -- that single-value-per-field overwrite is pre-existing
    # behavior this fix does not change, which is exactly why
    # provider_status (asserted above) needs a per-field key instead.
    provider_coverage = data["provider_coverage"]
    assert provider_coverage["places"] == "success"
    assert provider_coverage["restaurants"] == "fallback_used"
    assert provider_coverage["accommodations"] == "not_connected"

    # No fake provider status was created for any provider/field pair that
    # was never called. The only other entries come from the routes lookup,
    # the weather lookup (Open-Meteo; unavailable here since this test's
    # places double never resolves coordinates), the holidays lookup
    # (Nager.Date; unavailable here since "Testland" isn't a real country),
    # the currency lookup (Frankfurter; unavailable here for the same
    # reason), and the separate (still-unconnected) booking-capable
    # accommodation provider, which StayTransportService also records
    # under "accommodations".
    assert all(
        entry["provider_name"]
        in {
            "openstreetmap_places",
            "routes_provider",
            "accommodation_provider",
            "open_meteo",
            "nager_date",
            "frankfurter",
        }
        for entry in provider_status.values()
    )
    assert provider_status["accommodation_provider:accommodations"]["status"] == "not_connected"


class _NoCandidatesTestPlacesProvider(PlacesProvider):
    """Test-only double that reports no usable places, for the still-blocked case."""

    provider_name = "openstreetmap_places"

    def search_attractions(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> Any:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["attractions"]
        )

    def search_restaurants(
        self, area: str, filters: dict[str, Any] | None = None
    ) -> Any:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["restaurants"]
        )

    def search_accommodation_pois(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> Any:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["accommodation_pois"]
        )


def test_validation_report_blocked_when_no_candidate_pois(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provider_gateway, "places", _NoCandidatesTestPlacesProvider())

    generate_response = client.post(f"/trips/{created_trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{created_trip_id}/experience-plan")
    assert response.status_code == 200
    body = response.json()
    experience_plan = body["data"]["experience_plan"]
    for day_plan in experience_plan["daily_plans"]:
        assert day_plan["experiences"] == []

    validation_report = body["data"]["validation_report"]
    assert validation_report["readiness_status"] == "blocked"

    summary_response = client.get(f"/trips/{created_trip_id}/summary")
    assert summary_response.status_code == 200
    summary = summary_response.json()["data"]
    assert summary["candidate_pois_count"] == 0
    assert summary["scheduled_experiences_count"] == 0
    assert summary["validation_status"] == "blocked"
    assert summary["main_blocking_reason"] is not None
    assert summary["main_review_reason"] is None


class _RankingTestPlacesProvider(PlacesProvider):
    """Test-only double with attractions crafted to exercise experience
    ranking: one must-visit match, two interest matches (one matched via
    `category`, one matched only via `address`), and two unmatched
    candidates, all returned in a fixed provider order.
    """

    provider_name = "openstreetmap_places"

    _ATTRACTIONS: list[tuple[str, str, str, str]] = [
        ("test/poi/1", "Riverside Park", "park", "1 River Rd"),
        ("test/poi/2", "Central Plaza", "plaza", "5 Museum Lane"),
        ("test/poi/3", "Old Town Hall", "landmark", "3 Old Town Hall Square"),
        ("test/poi/4", "Sunset Beach", "beach", "4 Coast Ave"),
        ("test/poi/5", "City History Museum", "museum", "2 Main St"),
    ]

    def search_attractions(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        places = [
            NormalizedPlace(
                place_id=place_id,
                name=name,
                category=category,
                address=address,
                coordinates=GeoPoint(lat=0.0, lng=0.0),
                source=self.provider_name,
                data_status=DataStatus.LIVE,
                confidence=0.6,
            )
            for place_id, name, category, address in self._ATTRACTIONS
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

    def search_restaurants(
        self, area: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return unavailable_response(self.provider_name, self.provider_type, unavailable_fields=["restaurants"])

    def search_accommodation_pois(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["accommodation_pois"]
        )


def test_experience_plan_orders_must_visit_then_interest_then_provider_order(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provider_gateway, "places", _RankingTestPlacesProvider())

    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-12",
            "travelers_count": 2,
            "travel_group_type": "couple",
            "pace": "balanced",
            "interests": ["museum"],
            "must_visit": ["Old Town Hall"],
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{trip_id}/experience-plan")
    assert response.status_code == 200
    body = response.json()
    daily_plans = body["data"]["experience_plan"]["daily_plans"]

    all_experiences = [
        experience for day_plan in daily_plans for experience in day_plan["experiences"]
    ]

    known_fixture_names = {
        "Riverside Park",
        "Central Plaza",
        "Old Town Hall",
        "Sunset Beach",
        "City History Museum",
    }
    # No unmatched/invented candidate is created: every scheduled experience
    # is one of the real fixture candidates returned by the provider.
    for experience in all_experiences:
        assert experience["name"] in known_fixture_names

    scheduled_names = [experience["name"] for experience in all_experiences]

    # balanced pace caps 3 attractions/day; must-visit first, then interest
    # matches (category match, then address-only match, in provider order),
    # then unmatched candidates in provider order.
    assert scheduled_names == [
        "Old Town Hall",
        "Central Plaza",
        "City History Museum",
        "Riverside Park",
        "Sunset Beach",
    ]

    by_name = {experience["name"]: experience for experience in all_experiences}

    must_visit_index = scheduled_names.index("Old Town Hall")
    interest_indexes = [
        scheduled_names.index("City History Museum"),
        scheduled_names.index("Central Plaza"),
    ]
    unmatched_indexes = [
        scheduled_names.index("Riverside Park"),
        scheduled_names.index("Sunset Beach"),
    ]

    # must_visit matches are prioritized above interest matches.
    assert must_visit_index < min(interest_indexes)
    # interest matches are prioritized above unmatched candidates.
    assert max(interest_indexes) < min(unmatched_indexes)

    assert by_name["Old Town Hall"]["why_included"] == "Matches your must-visit request."
    for name in ("City History Museum", "Central Plaza"):
        why_included = by_name[name]["why_included"]
        assert "interests" in why_included
        assert "provider-backed" in why_included
    for name in ("Riverside Park", "Sunset Beach"):
        assert (
            by_name[name]["why_included"]
            == "Selected from provider-backed attraction candidates."
        )


def test_validation_report_warns_about_unvalidated_constraints(client: TestClient) -> None:
    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-12",
            "travelers_count": 2,
            "travel_group_type": "couple",
            "constraints": ["wheelchair accessible", "no more than 2km of walking per day"],
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{trip_id}/validation-report")
    assert response.status_code == 200
    validation_report = response.json()["data"]["validation_report"]

    # Experiences were scheduled (deterministic test provider), so constraints
    # alone must not block the plan or flip it to "ready".
    assert validation_report["readiness_status"] == "needs_review"

    constraint_warnings = [
        warning
        for warning in validation_report["warnings"]
        if warning["category"] == "constraints"
    ]
    assert len(constraint_warnings) == 1
    constraint_warning = constraint_warnings[0]

    # The captured constraint strings must be named, not paraphrased away...
    assert "wheelchair accessible" in constraint_warning["message"]
    assert "no more than 2km of walking per day" in constraint_warning["message"]
    # ...and the report must not claim the constraints are satisfied.
    assert "not fully validated" in constraint_warning["message"]
    assert "does not confirm" in constraint_warning["message"]


def test_validation_report_no_constraint_warning_when_none_captured(
    client: TestClient, generated_trip_id: str
) -> None:
    response = client.get(f"/trips/{generated_trip_id}/validation-report")
    assert response.status_code == 200
    validation_report = response.json()["data"]["validation_report"]

    assert not any(
        warning["category"] == "constraints" for warning in validation_report["warnings"]
    )


def test_validation_report_warns_about_unmatched_must_visit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provider_gateway, "places", _RankingTestPlacesProvider())

    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-12",
            "travelers_count": 2,
            "travel_group_type": "couple",
            "pace": "balanced",
            # "Old Town Hall" is a real fixture candidate; "Space Needle" is not.
            "must_visit": ["Old Town Hall", "Space Needle"],
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    experience_response = client.get(f"/trips/{trip_id}/experience-plan")
    assert experience_response.status_code == 200
    daily_plans = experience_response.json()["data"]["experience_plan"]["daily_plans"]
    all_experiences = [
        experience for day_plan in daily_plans for experience in day_plan["experiences"]
    ]
    scheduled_names = {experience["name"] for experience in all_experiences}

    # The matched must-visit request is scheduled exactly like before...
    assert "Old Town Hall" in scheduled_names
    by_name = {experience["name"]: experience for experience in all_experiences}
    assert by_name["Old Town Hall"]["why_included"] == "Matches your must-visit request."
    # ...but the unmatched one is never invented as a fake attraction.
    assert "Space Needle" not in scheduled_names
    known_fixture_names = {
        "Riverside Park",
        "Central Plaza",
        "Old Town Hall",
        "Sunset Beach",
        "City History Museum",
    }
    for experience in all_experiences:
        assert experience["name"] in known_fixture_names

    validation_response = client.get(f"/trips/{trip_id}/validation-report")
    assert validation_response.status_code == 200
    validation_report = validation_response.json()["data"]["validation_report"]

    # A single unmatched must-visit does not block the plan by itself.
    assert validation_report["readiness_status"] == "needs_review"
    assert validation_report["critical_issues"] == []

    must_visit_warnings = [
        warning
        for warning in validation_report["warnings"]
        if warning["category"] == "must_visit"
    ]
    assert len(must_visit_warnings) == 1
    must_visit_warning = must_visit_warnings[0]

    # The unmatched must-visit string is named exactly, and the matched one
    # is not mentioned since it was found.
    assert "Space Needle" in must_visit_warning["message"]
    assert "Old Town Hall" not in must_visit_warning["message"]
    assert "not scheduled" in must_visit_warning["message"]
    assert "not found in provider-backed attraction candidates" in must_visit_warning["message"]


def test_validation_report_no_must_visit_warning_when_all_matched(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provider_gateway, "places", _RankingTestPlacesProvider())

    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-12",
            "travelers_count": 2,
            "travel_group_type": "couple",
            "pace": "balanced",
            "must_visit": ["Old Town Hall"],
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{trip_id}/validation-report")
    assert response.status_code == 200
    validation_report = response.json()["data"]["validation_report"]

    assert not any(
        warning["category"] == "must_visit" for warning in validation_report["warnings"]
    )


def test_validation_report_warns_about_unvalidated_budget(client: TestClient) -> None:
    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-12",
            "travelers_count": 2,
            "travel_group_type": "couple",
            "budget_min": 1500,
            "budget_max": 2500,
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{trip_id}/validation-report")
    assert response.status_code == 200
    validation_report = response.json()["data"]["validation_report"]

    # Experiences were scheduled (deterministic test provider), so a captured
    # budget alone must not block the plan or flip it to "ready".
    assert validation_report["readiness_status"] == "needs_review"
    assert validation_report["critical_issues"] == []

    budget_warnings = [
        warning
        for warning in validation_report["warnings"]
        if warning["category"] == "budget"
    ]
    assert len(budget_warnings) == 1
    budget_warning = budget_warnings[0]

    # The captured budget range must be formatted cleanly (1500-2500, not
    # 1500.0-2500.0), with no invented/estimated cost figures layered on
    # top of it.
    assert "A budget of 1500-2500 USD was captured" in budget_warning["message"]
    assert "1500.0" not in budget_warning["message"]
    assert "2500.0" not in budget_warning["message"]
    # The report must clearly say cost validation isn't implemented and the
    # budget fit is unconfirmed, rather than claiming the plan fits.
    assert "budget/cost validation is not implemented yet" in budget_warning["message"]
    assert "does not confirm" in budget_warning["message"]
    assert "fits within that budget" in budget_warning["message"]
    assert "this plan fits" not in budget_warning["message"].lower()


def test_validation_report_budget_warning_formats_min_only(client: TestClient) -> None:
    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-12",
            "travelers_count": 2,
            "travel_group_type": "couple",
            "budget_min": 1500,
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{trip_id}/validation-report")
    assert response.status_code == 200
    validation_report = response.json()["data"]["validation_report"]

    budget_warning = next(
        warning for warning in validation_report["warnings"] if warning["category"] == "budget"
    )
    assert "A budget of 1500+ USD was captured" in budget_warning["message"]
    assert "1500.0" not in budget_warning["message"]
    assert "not implemented yet" in budget_warning["message"]
    assert "does not confirm" in budget_warning["message"]


def test_validation_report_budget_warning_formats_max_only(client: TestClient) -> None:
    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-12",
            "travelers_count": 2,
            "travel_group_type": "couple",
            "budget_max": 2500,
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{trip_id}/validation-report")
    assert response.status_code == 200
    validation_report = response.json()["data"]["validation_report"]

    budget_warning = next(
        warning for warning in validation_report["warnings"] if warning["category"] == "budget"
    )
    assert "A budget of up to 2500 USD was captured" in budget_warning["message"]
    assert "2500.0" not in budget_warning["message"]
    assert "not implemented yet" in budget_warning["message"]
    assert "does not confirm" in budget_warning["message"]


def test_validation_report_budget_warning_formats_fractional_amount(
    client: TestClient,
) -> None:
    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-12",
            "travelers_count": 2,
            "travel_group_type": "couple",
            "budget_min": 1500.5,
            "budget_max": 2500,
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{trip_id}/validation-report")
    assert response.status_code == 200
    validation_report = response.json()["data"]["validation_report"]

    budget_warning = next(
        warning for warning in validation_report["warnings"] if warning["category"] == "budget"
    )
    # A genuinely fractional amount keeps a clean decimal, never an
    # unnecessary trailing ".0", and is never rounded to a different value.
    assert "1500.5-2500 USD" in budget_warning["message"]


def test_validation_report_no_budget_warning_when_none_captured(
    client: TestClient, generated_trip_id: str
) -> None:
    response = client.get(f"/trips/{generated_trip_id}/validation-report")
    assert response.status_code == 200
    validation_report = response.json()["data"]["validation_report"]

    assert not any(
        warning["category"] == "budget" for warning in validation_report["warnings"]
    )


def _geo_place(place_id: str, name: str, category: str, lng: float | None) -> NormalizedPlace:
    coordinates = GeoPoint(lat=0.0, lng=lng) if lng is not None else None
    return NormalizedPlace(
        place_id=place_id,
        name=name,
        category=category,
        coordinates=coordinates,
        source="openstreetmap_places",
        data_status=DataStatus.LIVE,
        confidence=0.6,
    )


class _GeoOrderingTestPlacesProvider(PlacesProvider):
    """Test-only double with attractions at known coordinates, used to prove
    within-day nearest-neighbor ordering by straight-line distance actually
    changes scheduling order rather than passing provider order through.

    All points sit on the equator so straight-line distance is monotonic in
    longitude: Anchor(lng=0) is ~111km from Near(lng=1) and ~555km from
    Far(lng=5). Provider order is deliberately [Anchor, Far, Near] so a
    correct nearest-neighbor result ([Anchor, Near, Far]) can only come from
    actual distance calculation, not pass-through.
    """

    provider_name = "openstreetmap_places"

    def search_attractions(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        places = [
            _geo_place("test/geo/anchor", "Anchor Point", "landmark", 0.0),
            _geo_place("test/geo/far", "Far Point", "landmark", 5.0),
            _geo_place("test/geo/near", "Near Point", "landmark", 1.0),
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

    def search_restaurants(
        self, area: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["restaurants"]
        )

    def search_accommodation_pois(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["accommodation_pois"]
        )


def test_experience_plan_orders_within_day_by_nearest_straight_line_distance(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provider_gateway, "places", _GeoOrderingTestPlacesProvider())

    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-10",
            "travelers_count": 2,
            "travel_group_type": "couple",
            "pace": "balanced",
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{trip_id}/experience-plan")
    assert response.status_code == 200
    daily_plans = response.json()["data"]["experience_plan"]["daily_plans"]
    assert len(daily_plans) == 1

    day_plan = daily_plans[0]
    scheduled_names = [experience["name"] for experience in day_plan["experiences"]]

    # Provider returned [Anchor, Far, Near]; nearest-neighbor from Anchor must
    # pick Near (~111km) before Far (~555km), proving straight-line distance
    # -- not provider order -- determines the within-day sequence.
    assert scheduled_names == ["Anchor Point", "Near Point", "Far Point"]

    # The reordering step must not invent any route/timing/walking/cost facts.
    for experience in day_plan["experiences"]:
        assert experience["start_time"] is None
        assert experience["end_time"] is None
        assert experience["estimated_duration_minutes"] is None
    assert day_plan["estimated_walking_km"] is None
    assert day_plan["estimated_travel_time_minutes"] is None
    assert day_plan["estimated_cost"] is None
    assert any("straight-line" in warning for warning in day_plan["warnings"])
    assert any("not route optimization" in warning for warning in day_plan["warnings"])


class _GeoOrderingMissingCoordinatesTestPlacesProvider(PlacesProvider):
    """Test-only double mixing coordinate-backed and coordinate-less
    candidates, used to prove missing coordinates are handled safely (no
    crash) and coordinate-less candidates are kept in stable/provider order
    rather than guessed at geographically.
    """

    provider_name = "openstreetmap_places"

    def search_attractions(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        places = [
            _geo_place("test/geo/start", "Start Point", "landmark", 0.0),
            _geo_place("test/geo/nocoords", "No Coordinates Point", "landmark", None),
            _geo_place("test/geo/near", "Near Point", "landmark", 1.0),
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

    def search_restaurants(
        self, area: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["restaurants"]
        )

    def search_accommodation_pois(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["accommodation_pois"]
        )


def test_experience_plan_handles_missing_coordinates_without_crashing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        provider_gateway, "places", _GeoOrderingMissingCoordinatesTestPlacesProvider()
    )

    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-10",
            "travelers_count": 2,
            "travel_group_type": "couple",
            "pace": "balanced",
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{trip_id}/experience-plan")
    assert response.status_code == 200
    daily_plans = response.json()["data"]["experience_plan"]["daily_plans"]
    experiences = daily_plans[0]["experiences"]
    scheduled_names = [experience["name"] for experience in experiences]

    # Start and Near are geographically ordered; the coordinate-less
    # candidate is kept in its stable/provider-order position at the end
    # instead of crashing planning or being guessed at geographically.
    assert scheduled_names == ["Start Point", "Near Point", "No Coordinates Point"]

    by_name = {experience["name"]: experience for experience in experiences}
    # No coordinates are invented for the candidate that never had any.
    assert by_name["No Coordinates Point"]["coordinates"] is None


class _GeoOrderingPriorityTestPlacesProvider(PlacesProvider):
    """Test-only double combining must-visit, interest, and unmatched tiers
    with coordinates chosen so a correct nearest-neighbor reorder would place
    the unmatched candidate ahead of the interest candidate -- proving
    must-visit priority still wins the first slot even though within-day
    ordering after that point follows geography rather than tier.
    """

    provider_name = "openstreetmap_places"

    def search_attractions(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        places = [
            _geo_place("test/geo/mustvisit", "Old Fort", "landmark", 10.0),
            _geo_place("test/geo/interest", "City Museum", "museum", 0.0),
            _geo_place("test/geo/unmatched", "Riverside Walk", "park", 0.5),
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

    def search_restaurants(
        self, area: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["restaurants"]
        )

    def search_accommodation_pois(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["accommodation_pois"]
        )


def test_experience_plan_geo_ordering_keeps_must_visit_before_interest_and_unmatched(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provider_gateway, "places", _GeoOrderingPriorityTestPlacesProvider())

    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-10",
            "travelers_count": 2,
            "travel_group_type": "couple",
            "pace": "balanced",
            "interests": ["museum"],
            "must_visit": ["Old Fort"],
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{trip_id}/experience-plan")
    assert response.status_code == 200
    experiences = response.json()["data"]["experience_plan"]["daily_plans"][0]["experiences"]
    scheduled_names = [experience["name"] for experience in experiences]

    # Old Fort (must-visit) is geographically far from the other two, so a
    # pure nearest-neighbor walk starting elsewhere would not pick it first --
    # it stays first only because must-visit priority still wins the day's
    # first slot. Riverside Walk (unmatched) legitimately lands ahead of City
    # Museum (interest) because it is geographically closer to Old Fort,
    # proving distance -- not tier -- governs ordering after the first slot.
    assert scheduled_names == ["Old Fort", "Riverside Walk", "City Museum"]
    assert scheduled_names.index("Old Fort") < scheduled_names.index("City Museum")
    assert scheduled_names.index("Old Fort") < scheduled_names.index("Riverside Walk")

    by_name = {experience["name"]: experience for experience in experiences}
    assert by_name["Old Fort"]["why_included"] == "Matches your must-visit request."
    assert "interests" in by_name["City Museum"]["why_included"]


class _GeographicSpreadTestPlacesProvider(PlacesProvider):
    """Test-only double with two attractions ~11km apart (straight-line),
    used to prove the validator's geographic-spread warning fires once
    consecutive coordinate-backed experiences exceed the conservative 8km
    threshold.
    """

    provider_name = "openstreetmap_places"

    def search_attractions(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        places = [
            _geo_place("test/geo/spread/a", "Spread Point A", "landmark", 0.0),
            _geo_place("test/geo/spread/b", "Spread Point B", "landmark", 0.1),
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

    def search_restaurants(
        self, area: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["restaurants"]
        )

    def search_accommodation_pois(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["accommodation_pois"]
        )


def test_validation_report_warns_about_geographic_spread(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provider_gateway, "places", _GeographicSpreadTestPlacesProvider())

    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-10",
            "travelers_count": 2,
            "travel_group_type": "couple",
            "pace": "relaxed",
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{trip_id}/validation-report")
    assert response.status_code == 200
    validation_report = response.json()["data"]["validation_report"]

    spread_warnings = [
        warning
        for warning in validation_report["warnings"]
        if warning["category"] == "geographic_spread"
    ]
    assert len(spread_warnings) == 1
    spread_warning = spread_warnings[0]

    # The warning must be explicit that this is straight-line-only, not a
    # walking/route feasibility claim, and that the day needs review.
    assert "straight-line" in spread_warning["message"]
    assert "not walking or route distance" in spread_warning["message"]
    assert "not implemented yet" in spread_warning["message"]
    assert "needs review" in spread_warning["message"]
    assert "walking" not in spread_warning["category"]
    assert "route" not in spread_warning["category"]

    # This plan is otherwise unremarkable, so the plan must stay
    # needs_review, not blocked, and no critical issue is raised.
    assert validation_report["readiness_status"] == "needs_review"
    assert not any(
        issue["category"] == "geographic_spread"
        for issue in validation_report["critical_issues"]
    )

    # The warning must not fabricate any walking distance, route time,
    # duration, or cost data.
    experience_response = client.get(f"/trips/{trip_id}/experience-plan")
    assert experience_response.status_code == 200
    day_plan = experience_response.json()["data"]["experience_plan"]["daily_plans"][0]
    assert day_plan["estimated_walking_km"] is None
    assert day_plan["estimated_travel_time_minutes"] is None
    assert day_plan["estimated_cost"] is None
    for experience in day_plan["experiences"]:
        assert experience["start_time"] is None
        assert experience["end_time"] is None
        assert experience["estimated_duration_minutes"] is None


class _GeographicCloseTestPlacesProvider(PlacesProvider):
    """Test-only double with two attractions ~1.1km apart (straight-line),
    used to prove the geographic-spread warning does not fire below the
    conservative 8km threshold.
    """

    provider_name = "openstreetmap_places"

    def search_attractions(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        places = [
            _geo_place("test/geo/close/a", "Close Point A", "landmark", 0.0),
            _geo_place("test/geo/close/b", "Close Point B", "landmark", 0.01),
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

    def search_restaurants(
        self, area: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["restaurants"]
        )

    def search_accommodation_pois(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["accommodation_pois"]
        )


def test_validation_report_no_geographic_spread_warning_when_close_together(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provider_gateway, "places", _GeographicCloseTestPlacesProvider())

    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-10",
            "travelers_count": 2,
            "travel_group_type": "couple",
            "pace": "relaxed",
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{trip_id}/validation-report")
    assert response.status_code == 200
    validation_report = response.json()["data"]["validation_report"]

    assert not any(
        warning["category"] == "geographic_spread"
        for warning in validation_report["warnings"]
    )


class _GeographicSparseCoordinatesTestPlacesProvider(PlacesProvider):
    """Test-only double where only one of three scheduled attractions has
    coordinates, used to prove missing coordinates never crash validation
    and never get an invented distance -- spread can't be measured from a
    single coordinate-backed point, so the check is safely skipped.
    """

    provider_name = "openstreetmap_places"

    def search_attractions(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        places = [
            _geo_place("test/geo/sparse/a", "Sparse Point A", "landmark", 0.0),
            _geo_place("test/geo/sparse/b", "Sparse Point B (no coords)", "landmark", None),
            _geo_place("test/geo/sparse/c", "Sparse Point C (no coords)", "landmark", None),
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

    def search_restaurants(
        self, area: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["restaurants"]
        )

    def search_accommodation_pois(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["accommodation_pois"]
        )


def test_validation_report_missing_coordinates_do_not_crash(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        provider_gateway, "places", _GeographicSparseCoordinatesTestPlacesProvider()
    )

    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-10",
            "travelers_count": 2,
            "travel_group_type": "couple",
            "pace": "balanced",
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{trip_id}/validation-report")
    assert response.status_code == 200
    validation_report = response.json()["data"]["validation_report"]

    # With fewer than two coordinate-backed experiences in the day, spread
    # can't be measured, so no geographic_spread warning is invented.
    assert not any(
        warning["category"] == "geographic_spread"
        for warning in validation_report["warnings"]
    )
    assert validation_report["readiness_status"] == "needs_review"


class _DayGroupingClustersTestPlacesProvider(PlacesProvider):
    """Test-only double with two geographic clusters interleaved in provider
    order, used to prove day grouping is driven by geographic proximity to
    each day's anchor rather than naive sequential slicing of provider
    order. Provider order is [Cluster One Landmark, Cluster Two Landmark,
    Cluster One Annex, Cluster Two Annex] -- naive slicing into 2-per-day
    would pair Landmark/Landmark and Annex/Annex (both far apart); correct
    geographic grouping must instead pair each Landmark with its own
    ~11km-away Annex.
    """

    provider_name = "openstreetmap_places"

    def search_attractions(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        places = [
            _geo_place("test/geo/cluster1/landmark", "Cluster One Landmark", "landmark", 0.0),
            _geo_place("test/geo/cluster2/landmark", "Cluster Two Landmark", "landmark", 10.0),
            _geo_place("test/geo/cluster1/annex", "Cluster One Annex", "landmark", 0.1),
            _geo_place("test/geo/cluster2/annex", "Cluster Two Annex", "landmark", 10.1),
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

    def search_restaurants(
        self, area: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["restaurants"]
        )

    def search_accommodation_pois(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["accommodation_pois"]
        )


def test_experience_plan_groups_nearby_attractions_into_the_same_day(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provider_gateway, "places", _DayGroupingClustersTestPlacesProvider())

    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-11",
            "travelers_count": 2,
            "travel_group_type": "couple",
            "pace": "relaxed",
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{trip_id}/experience-plan")
    assert response.status_code == 200
    daily_plans = response.json()["data"]["experience_plan"]["daily_plans"]
    assert len(daily_plans) == 2

    day1_names = [experience["name"] for experience in daily_plans[0]["experiences"]]
    day2_names = [experience["name"] for experience in daily_plans[1]["experiences"]]

    # Each geographic cluster (Landmark + its ~11km-away Annex) is grouped
    # onto the same day, proving grouping follows distance to the day's
    # anchor rather than the provider's original interleaved order.
    assert day1_names == ["Cluster One Landmark", "Cluster One Annex"]
    assert day2_names == ["Cluster Two Landmark", "Cluster Two Annex"]

    # The grouping/ordering step must not invent any route/timing/walking/
    # cost facts.
    for day_plan in daily_plans:
        assert day_plan["estimated_walking_km"] is None
        assert day_plan["estimated_travel_time_minutes"] is None
        assert day_plan["estimated_cost"] is None
        assert any("straight-line" in warning for warning in day_plan["warnings"])
        assert any("not route optimization" in warning for warning in day_plan["warnings"])
        for experience in day_plan["experiences"]:
            assert experience["start_time"] is None
            assert experience["end_time"] is None
            assert experience["estimated_duration_minutes"] is None


class _DayGroupingMustVisitAnchorTestPlacesProvider(PlacesProvider):
    """Test-only double where the must-visit candidate is returned last in
    provider order and is geographically far from everything else, used to
    prove must-visit priority -- not geography or provider order -- decides
    which candidate anchors the earliest day.
    """

    provider_name = "openstreetmap_places"

    def search_attractions(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        places = [
            _geo_place("test/geo/anchor/x", "Nearby Point X", "landmark", 0.0),
            _geo_place("test/geo/anchor/y", "Nearby Point Y", "landmark", 0.1),
            _geo_place("test/geo/anchor/z", "Nearby Point Z", "landmark", 0.2),
            _geo_place("test/geo/anchor/mv", "Distant Must-Visit Fort", "landmark", 100.0),
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

    def search_restaurants(
        self, area: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["restaurants"]
        )

    def search_accommodation_pois(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["accommodation_pois"]
        )


def test_experience_plan_must_visit_anchors_earliest_day(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        provider_gateway, "places", _DayGroupingMustVisitAnchorTestPlacesProvider()
    )

    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-11",
            "travelers_count": 2,
            "travel_group_type": "couple",
            "pace": "relaxed",
            "must_visit": ["Distant Must-Visit Fort"],
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{trip_id}/experience-plan")
    assert response.status_code == 200
    daily_plans = response.json()["data"]["experience_plan"]["daily_plans"]

    # The must-visit candidate anchors day 1 despite being returned last in
    # provider order and being geographically far from every other
    # candidate -- a pure nearest-neighbor grouping would never choose it
    # first, so this proves must-visit priority still governs anchor order.
    day1_experiences = daily_plans[0]["experiences"]
    assert day1_experiences[0]["name"] == "Distant Must-Visit Fort"
    assert day1_experiences[0]["why_included"] == "Matches your must-visit request."


class _DayGroupingSparseCoordinatesTestPlacesProvider(PlacesProvider):
    """Test-only double where one of three same-day candidates has no
    coordinates, used to prove missing coordinates never crash day grouping
    and are never guessed at -- the coordinate-less candidate is kept in
    stable priority/provider order instead.
    """

    provider_name = "openstreetmap_places"

    def search_attractions(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        places = [
            _geo_place("test/geo/sparse-group/a", "Point A", "landmark", 0.0),
            _geo_place("test/geo/sparse-group/b", "Point B (no coords)", "landmark", None),
            _geo_place("test/geo/sparse-group/c", "Point C", "landmark", 0.05),
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

    def search_restaurants(
        self, area: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["restaurants"]
        )

    def search_accommodation_pois(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["accommodation_pois"]
        )


def test_experience_plan_day_grouping_handles_missing_coordinates_without_crashing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        provider_gateway, "places", _DayGroupingSparseCoordinatesTestPlacesProvider()
    )

    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-10",
            "travelers_count": 2,
            "travel_group_type": "couple",
            "pace": "balanced",
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{trip_id}/experience-plan")
    assert response.status_code == 200
    experiences = response.json()["data"]["experience_plan"]["daily_plans"][0]["experiences"]
    scheduled_names = [experience["name"] for experience in experiences]

    # A and C are geographically grouped/ordered; the coordinate-less
    # candidate is kept in its stable/provider-order position at the end
    # instead of crashing grouping or being guessed at geographically.
    assert scheduled_names == ["Point A", "Point C", "Point B (no coords)"]

    by_name = {experience["name"]: experience for experience in experiences}
    assert by_name["Point B (no coords)"]["coordinates"] is None


class _MustVisitLookupTestPlacesProvider(PlacesProvider):
    """Test-only double proving the targeted must-visit lookup fallback:
    general attraction search returns one candidate ("City Museum"); a
    must-visit term matching it should never trigger a targeted lookup, a
    missing must-visit term should be resolved via `search_must_visit_place`
    and appended, and a term the lookup can't resolve should leave the
    existing unmatched-must-visit warning untouched. `search_must_visit_place`
    is only implemented here -- every other test double in this file inherits
    `PlacesProvider`'s honest `not_connected` default for it.
    """

    provider_name = "openstreetmap_places"

    def __init__(self) -> None:
        self.must_visit_lookup_calls: list[str] = []

    def search_attractions(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        places = [_geo_place("test/mv/general", "City Museum", "museum", 0.0)]
        return ProviderResponse[list[NormalizedPlace]](
            provider_name=self.provider_name,
            provider_type=self.provider_type,
            status=ProviderStatus.SUCCESS,
            data_status=DataStatus.LIVE,
            data=places,
            confidence=0.65,
            message="Test fixture data; not a real provider call.",
        )

    def search_restaurants(
        self, area: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["restaurants"]
        )

    def search_accommodation_pois(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["accommodation_pois"]
        )

    def search_must_visit_place(
        self,
        must_visit_term: str,
        primary_destination: str,
        filters: dict[str, Any] | None = None,
    ) -> ProviderResponse[Any]:
        self.must_visit_lookup_calls.append(must_visit_term)
        if must_visit_term == "Old Fort Tower":
            place = _geo_place("test/mv/lookup/oldfort", "Old Fort Tower", "landmark", 1.0)
            return ProviderResponse[list[NormalizedPlace]](
                provider_name=self.provider_name,
                provider_type=self.provider_type,
                status=ProviderStatus.SUCCESS,
                data_status=DataStatus.LIVE,
                data=[place],
                confidence=0.5,
                message="Found via targeted must-visit lookup test fixture.",
            )
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["must_visit_place"]
        )


def test_must_visit_already_matched_does_not_trigger_targeted_lookup(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _MustVisitLookupTestPlacesProvider()
    monkeypatch.setattr(provider_gateway, "places", provider)

    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-10",
            "travelers_count": 2,
            "travel_group_type": "couple",
            "must_visit": ["City Museum"],
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    # Already matched by the general search result, so no targeted lookup
    # should have been attempted at all.
    assert provider.must_visit_lookup_calls == []

    response = client.get(f"/trips/{trip_id}/destination-context")
    assert response.status_code == 200
    candidate_pois = response.json()["data"]["destination_context"]["candidate_pois"]

    # No duplicate was appended: still exactly the one general-search candidate.
    assert len(candidate_pois) == 1
    assert candidate_pois[0]["name"] == "City Museum"


def test_missing_must_visit_is_added_through_targeted_lookup(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _MustVisitLookupTestPlacesProvider()
    monkeypatch.setattr(provider_gateway, "places", provider)

    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-10",
            "travelers_count": 2,
            "travel_group_type": "couple",
            "must_visit": ["Old Fort Tower"],
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    assert provider.must_visit_lookup_calls == ["Old Fort Tower"]

    destination_context_response = client.get(f"/trips/{trip_id}/destination-context")
    assert destination_context_response.status_code == 200
    candidate_pois = destination_context_response.json()["data"]["destination_context"][
        "candidate_pois"
    ]
    names = {poi["name"] for poi in candidate_pois}
    assert "City Museum" in names
    assert "Old Fort Tower" in names

    # No fake rating/price/review/opening-hour/route/availability/booking
    # fields were created for the appended candidate.
    added = next(poi for poi in candidate_pois if poi["name"] == "Old Fort Tower")
    assert set(added.keys()) == {
        "place_id",
        "name",
        "category",
        "coordinates",
        "address",
        "source",
        "data_status",
        "confidence",
    }

    # The scheduler naturally picks up the newly added candidate.
    experience_response = client.get(f"/trips/{trip_id}/experience-plan")
    assert experience_response.status_code == 200
    daily_plans = experience_response.json()["data"]["experience_plan"]["daily_plans"]
    scheduled_names = {
        experience["name"]
        for day_plan in daily_plans
        for experience in day_plan["experiences"]
    }
    assert "Old Fort Tower" in scheduled_names

    # The must-visit request is now matched, so no unmatched-must-visit
    # warning should remain.
    validation_response = client.get(f"/trips/{trip_id}/validation-report")
    assert validation_response.status_code == 200
    validation_report = validation_response.json()["data"]["validation_report"]
    assert not any(
        warning["category"] == "must_visit" for warning in validation_report["warnings"]
    )


def test_failed_targeted_lookup_keeps_unmatched_must_visit_warning(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _MustVisitLookupTestPlacesProvider()
    monkeypatch.setattr(provider_gateway, "places", provider)

    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-10",
            "travelers_count": 2,
            "travel_group_type": "couple",
            # The test provider's targeted lookup can't resolve this one.
            "must_visit": ["Hidden Cave"],
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    assert provider.must_visit_lookup_calls == ["Hidden Cave"]

    destination_context_response = client.get(f"/trips/{trip_id}/destination-context")
    assert destination_context_response.status_code == 200
    candidate_pois = destination_context_response.json()["data"]["destination_context"][
        "candidate_pois"
    ]
    # No place was invented to satisfy the failed lookup.
    assert len(candidate_pois) == 1
    assert candidate_pois[0]["name"] == "City Museum"

    validation_response = client.get(f"/trips/{trip_id}/validation-report")
    assert validation_response.status_code == 200
    validation_report = validation_response.json()["data"]["validation_report"]

    must_visit_warnings = [
        warning
        for warning in validation_report["warnings"]
        if warning["category"] == "must_visit"
    ]
    assert len(must_visit_warnings) == 1
    assert "Hidden Cave" in must_visit_warnings[0]["message"]
    assert "not found in provider-backed attraction candidates" in must_visit_warnings[0]["message"]


class _RestaurantSuggestionTestPlacesProvider(PlacesProvider):
    """Test-only double with one coordinate-backed anchor attraction and
    three restaurants at known straight-line distances, used to prove
    `restaurant_suggestions` are drawn only from `candidate_restaurants` and
    picked by nearest straight-line distance -- not provider order.

    All points sit on the equator so straight-line distance is monotonic in
    longitude. Restaurant provider order is deliberately [Far, Near, Mid] so
    a correct nearest-2 result ([Near, Mid]) can only come from actual
    distance calculation, not pass-through.
    """

    provider_name = "openstreetmap_places"

    def search_attractions(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        places = [
            _geo_place("test/restaurant-suggest/anchor", "Anchor Attraction", "landmark", 0.0)
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

    def search_restaurants(
        self, area: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        places = [
            _geo_place("test/restaurant-suggest/far", "Far Restaurant", "restaurant", 6.0),
            _geo_place("test/restaurant-suggest/near", "Near Restaurant", "restaurant", 1.0),
            _geo_place("test/restaurant-suggest/mid", "Mid Restaurant", "restaurant", 3.0),
        ]
        return ProviderResponse[list[NormalizedPlace]](
            provider_name=self.provider_name,
            provider_type=self.provider_type,
            status=ProviderStatus.SUCCESS,
            data_status=DataStatus.LIVE,
            data=places,
            confidence=0.6,
            message="Test fixture data; not a real provider call.",
        )

    def search_accommodation_pois(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["accommodation_pois"]
        )


def test_restaurant_suggestions_nearest_two_by_straight_line_distance(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provider_gateway, "places", _RestaurantSuggestionTestPlacesProvider())

    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-10",
            "travelers_count": 2,
            "travel_group_type": "couple",
            "pace": "balanced",
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{trip_id}/experience-plan")
    assert response.status_code == 200
    day_plan = response.json()["data"]["experience_plan"]["daily_plans"][0]

    suggestions = day_plan["restaurant_suggestions"]
    suggested_names = [item["name"] for item in suggestions]

    # Only the 2 nearest restaurants (by straight-line distance from the
    # anchor attraction) are suggested, proving distance -- not the provider
    # order [Far, Near, Mid] -- determines selection, and the excluded "Far"
    # candidate proves suggestions are capped, not just passed through.
    assert suggested_names == ["Near Restaurant", "Mid Restaurant"]

    # Every suggestion is one of the real fixture restaurant candidates --
    # nothing is drawn from anywhere else / invented.
    known_restaurant_names = {"Far Restaurant", "Near Restaurant", "Mid Restaurant"}
    for suggestion in suggestions:
        assert suggestion["name"] in known_restaurant_names

    for suggestion in suggestions:
        # Only provider-backed fields plus why_suggested are ever present --
        # no rating/price/review/opening-hours/reservation/booking/
        # availability/route/walking/cost field is invented.
        assert set(suggestion.keys()) == {
            "name",
            "category",
            "coordinates",
            "address",
            "source",
            "data_status",
            "confidence",
            "why_suggested",
        }
        assert suggestion["source"] == "openstreetmap_places"
        why_suggested = suggestion["why_suggested"]
        assert "provider-backed restaurant candidates" in why_suggested
        assert "straight-line" in why_suggested
        assert "not a reservation, rating, price, or route recommendation" in why_suggested


class _NoRestaurantCandidatesTestPlacesProvider(PlacesProvider):
    """Test-only double with a real coordinate-backed attraction but no
    restaurant candidates, used to prove `restaurant_suggestions` stay
    empty (never invented) and an honest day warning is added instead.
    """

    provider_name = "openstreetmap_places"

    def search_attractions(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        places = [_geo_place("test/no-restaurants/anchor", "Anchor Attraction", "landmark", 0.0)]
        return ProviderResponse[list[NormalizedPlace]](
            provider_name=self.provider_name,
            provider_type=self.provider_type,
            status=ProviderStatus.SUCCESS,
            data_status=DataStatus.LIVE,
            data=places,
            confidence=0.65,
            message="Test fixture data; not a real provider call.",
        )

    def search_restaurants(
        self, area: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["restaurants"]
        )

    def search_accommodation_pois(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["accommodation_pois"]
        )


def test_restaurant_suggestions_empty_when_no_restaurant_candidates(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provider_gateway, "places", _NoRestaurantCandidatesTestPlacesProvider())

    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-10",
            "travelers_count": 2,
            "travel_group_type": "couple",
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{trip_id}/experience-plan")
    assert response.status_code == 200
    day_plan = response.json()["data"]["experience_plan"]["daily_plans"][0]

    assert day_plan["restaurant_suggestions"] == []
    assert any(
        "No restaurant candidates are available yet" in warning
        for warning in day_plan["warnings"]
    )


class _RestaurantsMissingCoordinatesTestPlacesProvider(PlacesProvider):
    """Test-only double where the attraction anchor has coordinates but the
    only restaurant candidate does not, used to prove a restaurant
    candidate without coordinates never crashes suggestion and is never
    guessed at geographically.
    """

    provider_name = "openstreetmap_places"

    def search_attractions(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        places = [
            _geo_place("test/restaurant-no-coords/anchor", "Anchor Attraction", "landmark", 0.0)
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

    def search_restaurants(
        self, area: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        places = [
            _geo_place(
                "test/restaurant-no-coords/restaurant",
                "No Coordinates Restaurant",
                "restaurant",
                None,
            )
        ]
        return ProviderResponse[list[NormalizedPlace]](
            provider_name=self.provider_name,
            provider_type=self.provider_type,
            status=ProviderStatus.SUCCESS,
            data_status=DataStatus.LIVE,
            data=places,
            confidence=0.6,
            message="Test fixture data; not a real provider call.",
        )

    def search_accommodation_pois(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["accommodation_pois"]
        )


def test_restaurant_suggestions_missing_restaurant_coordinates_do_not_crash(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        provider_gateway, "places", _RestaurantsMissingCoordinatesTestPlacesProvider()
    )

    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-10",
            "travelers_count": 2,
            "travel_group_type": "couple",
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{trip_id}/experience-plan")
    assert response.status_code == 200
    day_plan = response.json()["data"]["experience_plan"]["daily_plans"][0]

    # No coordinates are invented for the coordinate-less restaurant
    # candidate, so it can't be measured and no suggestion is made.
    assert day_plan["restaurant_suggestions"] == []
    assert any(
        "No coordinate-backed restaurant candidates are available" in warning
        for warning in day_plan["warnings"]
    )


class _NoExperienceCoordinatesTestPlacesProvider(PlacesProvider):
    """Test-only double where the only scheduled attraction has no
    coordinates but a restaurant candidate does, used to prove a missing
    day anchor never crashes suggestion and restaurant_suggestions stay
    empty with an honest warning instead of guessing at a day anchor.
    """

    provider_name = "openstreetmap_places"

    def search_attractions(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        places = [
            _geo_place(
                "test/no-experience-coords/attraction", "No Coordinates Attraction", "landmark", None
            )
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

    def search_restaurants(
        self, area: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        places = [
            _geo_place(
                "test/no-experience-coords/restaurant", "Coordinate Restaurant", "restaurant", 0.0
            )
        ]
        return ProviderResponse[list[NormalizedPlace]](
            provider_name=self.provider_name,
            provider_type=self.provider_type,
            status=ProviderStatus.SUCCESS,
            data_status=DataStatus.LIVE,
            data=places,
            confidence=0.6,
            message="Test fixture data; not a real provider call.",
        )

    def search_accommodation_pois(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["accommodation_pois"]
        )


def test_restaurant_suggestions_missing_experience_coordinates_do_not_crash(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        provider_gateway, "places", _NoExperienceCoordinatesTestPlacesProvider()
    )

    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-10",
            "travelers_count": 2,
            "travel_group_type": "couple",
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{trip_id}/experience-plan")
    assert response.status_code == 200
    day_plan = response.json()["data"]["experience_plan"]["daily_plans"][0]

    # The attraction is still scheduled with no invented coordinates...
    assert len(day_plan["experiences"]) == 1
    assert day_plan["experiences"][0]["name"] == "No Coordinates Attraction"

    # ...but with no coordinate-backed day anchor, no restaurant suggestion
    # can be made, and this is stated honestly rather than guessed at.
    assert day_plan["restaurant_suggestions"] == []
    assert any(
        "No coordinate-backed scheduled experiences are available for this day" in warning
        for warning in day_plan["warnings"]
    )


class _AccommodationSuggestionTestPlacesProvider(PlacesProvider):
    """Test-only double with one coordinate-backed anchor attraction and
    three accommodation POIs at known straight-line distances, used to
    prove `accommodation_suggestions` are drawn only from
    `candidate_accommodation_pois` and picked by nearest straight-line
    distance -- not provider order.

    All points sit on the equator so straight-line distance is monotonic in
    longitude. Accommodation POI provider order is deliberately
    [Far, Near, Mid] so a correct nearest-2 result ([Near, Mid]) can only
    come from actual distance calculation, not pass-through.
    """

    provider_name = "openstreetmap_places"

    def search_attractions(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        places = [
            _geo_place("test/accommodation-suggest/anchor", "Anchor Attraction", "landmark", 0.0)
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

    def search_restaurants(
        self, area: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["restaurants"]
        )

    def search_accommodation_pois(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        places = [
            _geo_place("test/accommodation-suggest/far", "Far Hotel", "hotel", 6.0),
            _geo_place("test/accommodation-suggest/near", "Near Hotel", "hotel", 1.0),
            _geo_place("test/accommodation-suggest/mid", "Mid Hotel", "hotel", 3.0),
        ]
        return ProviderResponse[list[NormalizedPlace]](
            provider_name=self.provider_name,
            provider_type=self.provider_type,
            status=ProviderStatus.SUCCESS,
            data_status=DataStatus.LIVE,
            data=places,
            confidence=0.6,
            message="Test fixture data; not a real provider call.",
        )


def test_accommodation_suggestions_nearest_two_by_straight_line_distance(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provider_gateway, "places", _AccommodationSuggestionTestPlacesProvider())

    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-10",
            "travelers_count": 2,
            "travel_group_type": "couple",
            "pace": "balanced",
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{trip_id}/experience-plan")
    assert response.status_code == 200
    day_plan = response.json()["data"]["experience_plan"]["daily_plans"][0]

    suggestions = day_plan["accommodation_suggestions"]
    suggested_names = [item["name"] for item in suggestions]

    # Only the 2 nearest accommodation POIs (by straight-line distance from
    # the anchor attraction) are suggested, proving distance -- not the
    # provider order [Far, Near, Mid] -- determines selection, and the
    # excluded "Far" candidate proves suggestions are capped, not just
    # passed through.
    assert suggested_names == ["Near Hotel", "Mid Hotel"]

    # Every suggestion is one of the real fixture accommodation candidates --
    # nothing is drawn from anywhere else / invented.
    known_accommodation_names = {"Far Hotel", "Near Hotel", "Mid Hotel"}
    for suggestion in suggestions:
        assert suggestion["name"] in known_accommodation_names

    for suggestion in suggestions:
        # Only provider-backed fields plus why_suggested are ever present --
        # no rating/price/review/opening-hours/booking/availability/route/
        # walking/cost field is invented.
        assert set(suggestion.keys()) == {
            "name",
            "category",
            "coordinates",
            "address",
            "source",
            "data_status",
            "confidence",
            "why_suggested",
        }
        assert suggestion["source"] == "openstreetmap_places"
        why_suggested = suggestion["why_suggested"]
        assert "provider-backed accommodation POI candidates" in why_suggested
        assert "straight-line" in why_suggested
        assert "open-data location candidates only" in why_suggested
        assert "not bookable inventory" in why_suggested
        assert (
            "not a price, availability, rating, booking, or route recommendation"
            in why_suggested
        )


class _NoAccommodationCandidatesTestPlacesProvider(PlacesProvider):
    """Test-only double with a real coordinate-backed attraction but no
    accommodation POI candidates, used to prove `accommodation_suggestions`
    stay empty (never invented) and an honest day warning is added instead.
    """

    provider_name = "openstreetmap_places"

    def search_attractions(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        places = [
            _geo_place("test/no-accommodations/anchor", "Anchor Attraction", "landmark", 0.0)
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

    def search_restaurants(
        self, area: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["restaurants"]
        )

    def search_accommodation_pois(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["accommodation_pois"]
        )


def test_accommodation_suggestions_empty_when_no_accommodation_candidates(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        provider_gateway, "places", _NoAccommodationCandidatesTestPlacesProvider()
    )

    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-10",
            "travelers_count": 2,
            "travel_group_type": "couple",
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{trip_id}/experience-plan")
    assert response.status_code == 200
    day_plan = response.json()["data"]["experience_plan"]["daily_plans"][0]

    assert day_plan["accommodation_suggestions"] == []
    assert any(
        "No accommodation POI candidates are available yet" in warning
        for warning in day_plan["warnings"]
    )


class _AccommodationsMissingCoordinatesTestPlacesProvider(PlacesProvider):
    """Test-only double where the attraction anchor has coordinates but the
    only accommodation POI candidate does not, used to prove an
    accommodation candidate without coordinates never crashes suggestion
    and is never guessed at geographically.
    """

    provider_name = "openstreetmap_places"

    def search_attractions(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        places = [
            _geo_place(
                "test/accommodation-no-coords/anchor", "Anchor Attraction", "landmark", 0.0
            )
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

    def search_restaurants(
        self, area: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["restaurants"]
        )

    def search_accommodation_pois(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        places = [
            _geo_place(
                "test/accommodation-no-coords/hotel",
                "No Coordinates Hotel",
                "hotel",
                None,
            )
        ]
        return ProviderResponse[list[NormalizedPlace]](
            provider_name=self.provider_name,
            provider_type=self.provider_type,
            status=ProviderStatus.SUCCESS,
            data_status=DataStatus.LIVE,
            data=places,
            confidence=0.6,
            message="Test fixture data; not a real provider call.",
        )


def test_accommodation_suggestions_missing_accommodation_coordinates_do_not_crash(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        provider_gateway, "places", _AccommodationsMissingCoordinatesTestPlacesProvider()
    )

    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-10",
            "travelers_count": 2,
            "travel_group_type": "couple",
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{trip_id}/experience-plan")
    assert response.status_code == 200
    day_plan = response.json()["data"]["experience_plan"]["daily_plans"][0]

    # No coordinates are invented for the coordinate-less accommodation
    # candidate, so it can't be measured and no suggestion is made.
    assert day_plan["accommodation_suggestions"] == []
    assert any(
        "No coordinate-backed accommodation POI candidates are available" in warning
        for warning in day_plan["warnings"]
    )


class _NoExperienceCoordinatesForAccommodationTestPlacesProvider(PlacesProvider):
    """Test-only double where the only scheduled attraction has no
    coordinates but an accommodation POI candidate does, used to prove a
    missing day anchor never crashes accommodation suggestion and
    accommodation_suggestions stay empty with an honest warning instead of
    guessing at a day anchor.
    """

    provider_name = "openstreetmap_places"

    def search_attractions(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        places = [
            _geo_place(
                "test/no-experience-coords-accom/attraction",
                "No Coordinates Attraction",
                "landmark",
                None,
            )
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

    def search_restaurants(
        self, area: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["restaurants"]
        )

    def search_accommodation_pois(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        places = [
            _geo_place(
                "test/no-experience-coords-accom/hotel", "Coordinate Hotel", "hotel", 0.0
            )
        ]
        return ProviderResponse[list[NormalizedPlace]](
            provider_name=self.provider_name,
            provider_type=self.provider_type,
            status=ProviderStatus.SUCCESS,
            data_status=DataStatus.LIVE,
            data=places,
            confidence=0.6,
            message="Test fixture data; not a real provider call.",
        )


def test_accommodation_suggestions_missing_experience_coordinates_do_not_crash(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        provider_gateway, "places", _NoExperienceCoordinatesForAccommodationTestPlacesProvider()
    )

    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-10",
            "travelers_count": 2,
            "travel_group_type": "couple",
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{trip_id}/experience-plan")
    assert response.status_code == 200
    day_plan = response.json()["data"]["experience_plan"]["daily_plans"][0]

    # The attraction is still scheduled with no invented coordinates...
    assert len(day_plan["experiences"]) == 1
    assert day_plan["experiences"][0]["name"] == "No Coordinates Attraction"

    # ...but with no coordinate-backed day anchor, no accommodation
    # suggestion can be made, and this is stated honestly rather than
    # guessed at.
    assert day_plan["accommodation_suggestions"] == []
    assert any(
        "No coordinate-backed scheduled experiences are available for this day"
        in warning
        for warning in day_plan["warnings"]
    )


class _StayAreaGuidanceTestPlacesProvider(PlacesProvider):
    """Test-only double with attractions and accommodation POIs at known
    coordinates, used to prove plan-level stay-area guidance selects the 3
    lowest-average-straight-line-distance accommodation candidates across
    every scheduled attraction, drawing only from
    `candidate_accommodation_pois`.

    All points sit on the equator, so straight-line distance is monotonic in
    longitude. Attractions cluster near lng=0 and lng=0.05; of the 4
    accommodation candidates, "Very Far Hotel" (lng=10.0) has the highest
    average distance to both attractions and must be excluded from the top
    3.
    """

    provider_name = "openstreetmap_places"

    def search_attractions(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        places = [
            _geo_place("test/stay/attraction/a", "Stay Attraction A", "landmark", 0.0),
            _geo_place("test/stay/attraction/b", "Stay Attraction B", "landmark", 0.05),
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

    def search_restaurants(
        self, area: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["restaurants"]
        )

    def search_accommodation_pois(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        places = [
            _geo_place("test/stay/accommodation/near", "Near Hotel", "hotel", 0.02),
            _geo_place("test/stay/accommodation/mid", "Mid Hotel", "hotel", 0.5),
            _geo_place("test/stay/accommodation/far", "Far Hotel", "hotel", 5.0),
            _geo_place("test/stay/accommodation/veryfar", "Very Far Hotel", "hotel", 10.0),
        ]
        return ProviderResponse[list[NormalizedPlace]](
            provider_name=self.provider_name,
            provider_type=self.provider_type,
            status=ProviderStatus.SUCCESS,
            data_status=DataStatus.LIVE,
            data=places,
            confidence=0.6,
            message="Test fixture data; not a real provider call.",
        )


def _generate_stay_area_guidance(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, provider: PlacesProvider
) -> dict[str, Any]:
    monkeypatch.setattr(provider_gateway, "places", provider)

    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-10",
            "travelers_count": 2,
            "travel_group_type": "couple",
            "pace": "balanced",
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{trip_id}/experience-plan")
    assert response.status_code == 200
    return response.json()["data"]["experience_plan"]["stay_area_guidance"]


def test_stay_area_guidance_selects_lowest_average_distance_accommodation_pois(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stay_area_guidance = _generate_stay_area_guidance(
        client, monkeypatch, _StayAreaGuidanceTestPlacesProvider()
    )

    suggested_names = [
        poi["name"] for poi in stay_area_guidance["suggested_anchor_accommodation_pois"]
    ]
    # The 3 lowest-average-distance candidates are chosen, in ascending
    # distance order; "Very Far Hotel" (the highest average distance) is
    # excluded.
    assert suggested_names == ["Near Hotel", "Mid Hotel", "Far Hotel"]
    assert stay_area_guidance["warnings"] == []
    assert stay_area_guidance["summary"] != ""


def test_stay_area_guidance_uses_only_candidate_accommodation_pois(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stay_area_guidance = _generate_stay_area_guidance(
        client, monkeypatch, _StayAreaGuidanceTestPlacesProvider()
    )

    # Every suggested anchor is a real candidate returned by
    # search_accommodation_pois -- nothing outside that set (e.g. an
    # attraction name, or an invented hotel) is ever suggested.
    known_accommodation_names = {"Near Hotel", "Mid Hotel", "Far Hotel", "Very Far Hotel"}
    for poi in stay_area_guidance["suggested_anchor_accommodation_pois"]:
        assert poi["name"] in known_accommodation_names
    assert len(stay_area_guidance["suggested_anchor_accommodation_pois"]) <= 3


class _StayAreaGuidanceNoAccommodationTestPlacesProvider(PlacesProvider):
    """Test-only double with a real, coordinate-backed attraction but no
    accommodation POI candidates at all, used to prove stay-area guidance
    stays honestly empty rather than inventing an accommodation suggestion.
    """

    provider_name = "openstreetmap_places"

    def search_attractions(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        places = [_geo_place("test/stay/none/attraction", "Solo Attraction", "landmark", 0.0)]
        return ProviderResponse[list[NormalizedPlace]](
            provider_name=self.provider_name,
            provider_type=self.provider_type,
            status=ProviderStatus.SUCCESS,
            data_status=DataStatus.LIVE,
            data=places,
            confidence=0.6,
            message="Test fixture data; not a real provider call.",
        )

    def search_restaurants(
        self, area: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["restaurants"]
        )

    def search_accommodation_pois(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["accommodation_pois"]
        )


def test_stay_area_guidance_no_accommodation_pois_stays_honestly_empty(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stay_area_guidance = _generate_stay_area_guidance(
        client, monkeypatch, _StayAreaGuidanceNoAccommodationTestPlacesProvider()
    )

    assert stay_area_guidance["suggested_anchor_accommodation_pois"] == []
    assert len(stay_area_guidance["warnings"]) == 1
    assert "accommodation" in stay_area_guidance["warnings"][0].lower()


class _StayAreaGuidanceNoExperienceCoordinatesTestPlacesProvider(PlacesProvider):
    """Test-only double where the only scheduled attraction has no
    coordinates but a real accommodation POI candidate does, used to prove
    missing scheduled-experience coordinates are handled honestly (empty
    suggestions plus a warning) rather than crashing or guessing.
    """

    provider_name = "openstreetmap_places"

    def search_attractions(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        places = [
            _geo_place("test/stay/nocoord/attraction", "No Coordinates Attraction", "landmark", None)
        ]
        return ProviderResponse[list[NormalizedPlace]](
            provider_name=self.provider_name,
            provider_type=self.provider_type,
            status=ProviderStatus.SUCCESS,
            data_status=DataStatus.LIVE,
            data=places,
            confidence=0.6,
            message="Test fixture data; not a real provider call.",
        )

    def search_restaurants(
        self, area: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["restaurants"]
        )

    def search_accommodation_pois(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        places = [_geo_place("test/stay/nocoord/hotel", "Some Hotel", "hotel", 0.0)]
        return ProviderResponse[list[NormalizedPlace]](
            provider_name=self.provider_name,
            provider_type=self.provider_type,
            status=ProviderStatus.SUCCESS,
            data_status=DataStatus.LIVE,
            data=places,
            confidence=0.6,
            message="Test fixture data; not a real provider call.",
        )


def test_stay_area_guidance_missing_scheduled_experience_coordinates_does_not_crash(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stay_area_guidance = _generate_stay_area_guidance(
        client, monkeypatch, _StayAreaGuidanceNoExperienceCoordinatesTestPlacesProvider()
    )

    assert stay_area_guidance["suggested_anchor_accommodation_pois"] == []
    assert len(stay_area_guidance["warnings"]) == 1
    assert "coordinate" in stay_area_guidance["warnings"][0].lower()


def test_stay_area_guidance_creates_no_fake_fields(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stay_area_guidance = _generate_stay_area_guidance(
        client, monkeypatch, _StayAreaGuidanceTestPlacesProvider()
    )

    assert set(stay_area_guidance.keys()) == {
        "summary",
        "suggested_anchor_accommodation_pois",
        "assumptions",
        "warnings",
    }
    assert len(stay_area_guidance["suggested_anchor_accommodation_pois"]) == 3

    for poi in stay_area_guidance["suggested_anchor_accommodation_pois"]:
        assert set(poi.keys()) == {
            "name",
            "category",
            "coordinates",
            "address",
            "source",
            "data_status",
            "confidence",
            "why_suggested",
        }
        for forbidden_field in (
            "rating",
            "price",
            "review",
            "opening_hours",
            "booking_url",
            "reservation_url",
            "availability",
            "route_time",
            "walking_distance",
            "cost",
        ):
            assert forbidden_field not in poi

        why_suggested = poi["why_suggested"]
        assert "accommodation POI candidates" in why_suggested
        assert "straight-line" in why_suggested
        assert "open-data location candidate" in why_suggested
        assert "not bookable inventory" in why_suggested
        assert "not a hotel recommendation" in why_suggested
        assert "price" in why_suggested
        assert "availability" in why_suggested
        assert "rating" in why_suggested
        assert "booking" in why_suggested


def test_decision_summary_exists_after_generate(
    client: TestClient, generated_trip_id: str
) -> None:
    response = client.get(f"/trips/{generated_trip_id}/experience-plan")
    assert response.status_code == 200
    experience_plan = response.json()["data"]["experience_plan"]

    assert "decision_summary" in experience_plan
    decision_summary = experience_plan["decision_summary"]

    assert set(decision_summary.keys()) == {
        "summary",
        "provider_backed_facts",
        "proximity_based_decisions",
        "unvalidated_items",
        "user_review_required",
    }
    assert decision_summary["summary"] != ""
    assert isinstance(decision_summary["provider_backed_facts"], list)
    assert isinstance(decision_summary["proximity_based_decisions"], list)
    assert isinstance(decision_summary["unvalidated_items"], list)
    assert isinstance(decision_summary["user_review_required"], list)

    # This section is purely explanatory and must never flip readiness by
    # itself; the deterministic test provider still leaves the plan
    # needs_review (route/timing feasibility is not implemented yet).
    assert experience_plan["decision_summary"] is not None
    validation_response = client.get(f"/trips/{generated_trip_id}/validation-report")
    assert validation_response.status_code == 200
    assert (
        validation_response.json()["data"]["validation_report"]["readiness_status"]
        == "needs_review"
    )


def test_decision_summary_reflects_available_provider_backed_data(
    client: TestClient, generated_trip_id: str
) -> None:
    destination_response = client.get(f"/trips/{generated_trip_id}/destination-context")
    assert destination_response.status_code == 200
    destination_context = destination_response.json()["data"]["destination_context"]

    experience_response = client.get(f"/trips/{generated_trip_id}/experience-plan")
    assert experience_response.status_code == 200
    experience_plan = experience_response.json()["data"]["experience_plan"]
    decision_summary = experience_plan["decision_summary"]

    scheduled_experiences_count = sum(
        len(day_plan["experiences"]) for day_plan in experience_plan["daily_plans"]
    )
    restaurant_count = len(destination_context["candidate_restaurants"])
    accommodation_count = len(destination_context["candidate_accommodation_pois"])
    assert scheduled_experiences_count > 0
    assert restaurant_count > 0
    assert accommodation_count > 0

    facts_text = " ".join(decision_summary["provider_backed_facts"])
    assert "candidate_pois" in facts_text
    assert "candidate_restaurants" in facts_text
    assert "candidate_accommodation_pois" in facts_text
    assert str(scheduled_experiences_count) in facts_text
    assert str(restaurant_count) in facts_text
    assert str(accommodation_count) in facts_text

    summary = decision_summary["summary"]
    assert "candidate_pois" in summary
    assert "candidate_restaurants" in summary
    assert "candidate_accommodation_pois" in summary


def test_decision_summary_honestly_reports_unavailable_restaurants_and_accommodations(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Attractions are available but restaurants/accommodation POIs are not,
    # from the same fixture used elsewhere to prove must-visit/interest
    # ranking; here it proves decision_summary states unavailability
    # honestly instead of silently omitting it or inventing data.
    monkeypatch.setattr(provider_gateway, "places", _RankingTestPlacesProvider())

    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-10",
            "travelers_count": 2,
            "travel_group_type": "couple",
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{trip_id}/experience-plan")
    assert response.status_code == 200
    decision_summary = response.json()["data"]["experience_plan"]["decision_summary"]

    summary = decision_summary["summary"]
    assert "restaurant" in summary.lower() and "unavailable" in summary.lower()
    assert "accommodation" in summary.lower()

    facts_text = " ".join(decision_summary["provider_backed_facts"])
    # No restaurant/accommodation provider-backed fact is fabricated when
    # those candidates are unavailable.
    assert "candidate_restaurants" not in facts_text
    assert "candidate_accommodation_pois" not in facts_text
    # Attractions were available, so that fact is still honestly present.
    assert "candidate_pois" in facts_text


def test_decision_summary_includes_route_feasibility_and_cost_limitations(
    client: TestClient, generated_trip_id: str
) -> None:
    response = client.get(f"/trips/{generated_trip_id}/experience-plan")
    assert response.status_code == 200
    decision_summary = response.json()["data"]["experience_plan"]["decision_summary"]

    combined_text = " ".join(
        [decision_summary["summary"], *decision_summary["unvalidated_items"]]
    ).lower()

    for expected_term in (
        "route",
        "opening hours",
        "feasibility",
        "walking time",
        "cost",
        "hotel price",
        "availability",
        "rating",
        "booking",
    ):
        assert expected_term in combined_text


def test_decision_summary_creates_no_fake_fields(
    client: TestClient, generated_trip_id: str
) -> None:
    response = client.get(f"/trips/{generated_trip_id}/experience-plan")
    assert response.status_code == 200
    decision_summary = response.json()["data"]["experience_plan"]["decision_summary"]

    assert set(decision_summary.keys()) == {
        "summary",
        "provider_backed_facts",
        "proximity_based_decisions",
        "unvalidated_items",
        "user_review_required",
    }

    # Every value is plain explanatory text -- no structured
    # price/rating/review/opening-hour/booking/availability/route/walking/
    # cost field is ever attached to the decision summary itself.
    assert isinstance(decision_summary["summary"], str)
    for list_field in (
        "provider_backed_facts",
        "proximity_based_decisions",
        "unvalidated_items",
        "user_review_required",
    ):
        for item in decision_summary[list_field]:
            assert isinstance(item, str)

    for forbidden_field in (
        "rating",
        "price",
        "review",
        "opening_hours",
        "booking_url",
        "reservation_url",
        "availability",
        "route_time",
        "walking_distance",
        "cost",
        "estimated_cost",
    ):
        assert forbidden_field not in decision_summary


def test_implementation_gaps_exists_after_generate(
    client: TestClient, generated_trip_id: str
) -> None:
    response = client.get(f"/trips/{generated_trip_id}/experience-plan")
    assert response.status_code == 200
    experience_plan = response.json()["data"]["experience_plan"]

    assert "implementation_gaps" in experience_plan
    implementation_gaps = experience_plan["implementation_gaps"]

    assert set(implementation_gaps.keys()) == {
        "summary",
        "connected_data",
        "missing_data",
        "next_data_needed",
        "why_needs_review",
    }
    assert implementation_gaps["summary"] != ""
    assert isinstance(implementation_gaps["connected_data"], list)
    assert isinstance(implementation_gaps["missing_data"], list)
    assert isinstance(implementation_gaps["next_data_needed"], list)
    assert isinstance(implementation_gaps["why_needs_review"], list)

    # This section is purely explanatory and must never flip readiness by
    # itself; the deterministic test provider still leaves the plan
    # needs_review (route/timing feasibility is not implemented yet).
    validation_response = client.get(f"/trips/{generated_trip_id}/validation-report")
    assert validation_response.status_code == 200
    assert (
        validation_response.json()["data"]["validation_report"]["readiness_status"]
        == "needs_review"
    )


def test_implementation_gaps_reflects_connected_provider_backed_data(
    client: TestClient, generated_trip_id: str
) -> None:
    coverage_response = client.get(f"/trips/{generated_trip_id}/provider-coverage")
    assert coverage_response.status_code == 200
    provider_coverage = coverage_response.json()["data"]["provider_coverage"]

    # The deterministic test provider returns real data for all three
    # fields, so all three must be reflected as connected.
    assert provider_coverage["places"] == "success"
    assert provider_coverage["restaurants"] == "success"
    assert provider_coverage["accommodations"] == "open_poi_available"

    response = client.get(f"/trips/{generated_trip_id}/experience-plan")
    assert response.status_code == 200
    implementation_gaps = response.json()["data"]["experience_plan"]["implementation_gaps"]

    connected_text = " ".join(implementation_gaps["connected_data"])
    assert "places=success" in connected_text
    assert "restaurants=success" in connected_text
    assert "accommodations=open_poi_available" in connected_text
    assert "attraction" in connected_text.lower()
    assert "restaurant" in connected_text.lower()
    assert "accommodation" in connected_text.lower()
    # Honest about accommodations being open-data, not booking inventory.
    assert "not a booking-capable accommodation provider" in connected_text


def test_implementation_gaps_reflects_missing_data_sources(
    client: TestClient, generated_trip_id: str
) -> None:
    response = client.get(f"/trips/{generated_trip_id}/experience-plan")
    assert response.status_code == 200
    implementation_gaps = response.json()["data"]["experience_plan"]["implementation_gaps"]

    missing_text = " ".join(implementation_gaps["missing_data"]).lower()
    next_needed_text = " ".join(implementation_gaps["next_data_needed"]).lower()

    # Routes/transit feasibility is never connected in this deployment.
    assert "route" in missing_text or "transit" in missing_text
    assert "route" in next_needed_text

    # A booking-capable accommodation provider is never connected either,
    # distinct from the open-data accommodation POI candidates.
    assert "booking-capable accommodation provider" in missing_text
    assert "accommodation provider" in next_needed_text

    # Hotel prices, vacation rentals/Airbnb, weather, holidays, and currency
    # are never connected in this deployment.
    assert "hotel price" in missing_text
    assert "vacation rental" in missing_text and "airbnb" in missing_text
    assert "weather" in missing_text
    assert "weather" in next_needed_text
    assert "holiday" in missing_text
    assert "holiday" in next_needed_text
    assert "currency" in missing_text
    assert "currency" in next_needed_text


def test_implementation_gaps_explains_why_needs_review(
    client: TestClient, generated_trip_id: str
) -> None:
    response = client.get(f"/trips/{generated_trip_id}/experience-plan")
    assert response.status_code == 200
    implementation_gaps = response.json()["data"]["experience_plan"]["implementation_gaps"]

    why_text = " ".join(implementation_gaps["why_needs_review"]).lower()

    assert "route ordering" in why_text
    assert "opening hours" in why_text
    assert "walking time" in why_text
    assert "cost" in why_text and "budget" in why_text
    assert "not bookable inventory" in why_text


def test_implementation_gaps_creates_no_fake_values(
    client: TestClient, generated_trip_id: str
) -> None:
    response = client.get(f"/trips/{generated_trip_id}/experience-plan")
    assert response.status_code == 200
    implementation_gaps = response.json()["data"]["experience_plan"]["implementation_gaps"]

    assert set(implementation_gaps.keys()) == {
        "summary",
        "connected_data",
        "missing_data",
        "next_data_needed",
        "why_needs_review",
    }

    # Every value is plain explanatory text -- no structured
    # price/rating/review/opening-hour/booking/availability/route/walking/
    # cost value is ever attached to this section itself.
    assert isinstance(implementation_gaps["summary"], str)
    for list_field in (
        "connected_data",
        "missing_data",
        "next_data_needed",
        "why_needs_review",
    ):
        for item in implementation_gaps[list_field]:
            assert isinstance(item, str)

    for forbidden_field in (
        "rating",
        "price",
        "review",
        "opening_hours",
        "booking_url",
        "reservation_url",
        "availability",
        "route_time",
        "walking_distance",
        "cost",
        "estimated_cost",
    ):
        assert forbidden_field not in implementation_gaps


_NOT_YET_CHECKED_ITEM_LABELS = {
    "Route times checked",
    "Opening hours checked",
    "Walking feasibility checked",
    "Budget/cost fit checked",
    "Accommodation price/availability checked",
    "Weather impact checked",
    "Holiday/closure context checked",
    "Booking links available",
}

_CANDIDATE_AVAILABILITY_ITEM_LABELS = {
    "Provider-backed attractions available",
    "Restaurant candidates available",
    "Accommodation POI candidates available",
}


def _checklist_items_by_label(checklist: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["label"]: item for item in checklist["items"]}


def test_readiness_checklist_exists_after_generate(
    client: TestClient, generated_trip_id: str
) -> None:
    response = client.get(f"/trips/{generated_trip_id}/experience-plan")
    assert response.status_code == 200
    experience_plan = response.json()["data"]["experience_plan"]

    assert "readiness_checklist" in experience_plan
    readiness_checklist = experience_plan["readiness_checklist"]

    assert set(readiness_checklist.keys()) == {"summary", "items"}
    assert readiness_checklist["summary"] != ""
    assert isinstance(readiness_checklist["items"], list)
    assert len(readiness_checklist["items"]) > 0

    valid_statuses = {"checked", "needs_review", "missing_data", "not_implemented"}
    for item in readiness_checklist["items"]:
        assert set(item.keys()) == {"label", "status", "explanation"}
        assert isinstance(item["label"], str) and item["label"] != ""
        assert item["status"] in valid_statuses
        assert isinstance(item["explanation"], str) and item["explanation"] != ""

    # This checklist is purely explanatory and must never flip readiness by
    # itself; the deterministic test provider still leaves the plan
    # needs_review (route/timing feasibility is not implemented yet).
    validation_response = client.get(f"/trips/{generated_trip_id}/validation-report")
    assert validation_response.status_code == 200
    assert (
        validation_response.json()["data"]["validation_report"]["readiness_status"]
        == "needs_review"
    )


def test_readiness_checklist_checks_candidates_only_when_available(
    client: TestClient, generated_trip_id: str
) -> None:
    # The deterministic test provider returns real attraction/restaurant/
    # accommodation POI candidates, so all three availability items must be
    # "checked".
    response = client.get(f"/trips/{generated_trip_id}/experience-plan")
    assert response.status_code == 200
    readiness_checklist = response.json()["data"]["experience_plan"]["readiness_checklist"]
    items_by_label = _checklist_items_by_label(readiness_checklist)

    for label in _CANDIDATE_AVAILABILITY_ITEM_LABELS:
        assert items_by_label[label]["status"] == "checked"


def test_readiness_checklist_marks_candidates_missing_when_unavailable(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # With no attraction/restaurant/accommodation POI candidates available at
    # all, the availability items must not be reported as "checked".
    monkeypatch.setattr(provider_gateway, "places", _NoCandidatesTestPlacesProvider())

    generate_response = client.post(f"/trips/{created_trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{created_trip_id}/experience-plan")
    assert response.status_code == 200
    readiness_checklist = response.json()["data"]["experience_plan"]["readiness_checklist"]
    items_by_label = _checklist_items_by_label(readiness_checklist)

    for label in _CANDIDATE_AVAILABILITY_ITEM_LABELS:
        assert items_by_label[label]["status"] == "missing_data"


def test_readiness_checklist_checks_candidates_even_with_partial_or_fallback_coverage(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Candidate availability is a data-availability check, not a
    # travel-readiness check: real candidates from a "fallback_used"
    # restaurants field must still be "checked", not "needs_review", even
    # though the overall plan stays "needs_review" for unrelated reasons
    # (route/timing feasibility is not implemented yet).
    monkeypatch.setattr(provider_gateway, "places", _MixedFieldStatusTestPlacesProvider())

    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-12",
            "travelers_count": 2,
            "travel_group_type": "couple",
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    coverage_response = client.get(f"/trips/{trip_id}/provider-coverage")
    assert coverage_response.status_code == 200
    provider_coverage = coverage_response.json()["data"]["provider_coverage"]
    assert provider_coverage["places"] == "success"
    assert provider_coverage["restaurants"] == "fallback_used"
    assert provider_coverage["accommodations"] == "not_connected"

    response = client.get(f"/trips/{trip_id}/experience-plan")
    assert response.status_code == 200
    readiness_checklist = response.json()["data"]["experience_plan"]["readiness_checklist"]
    items_by_label = _checklist_items_by_label(readiness_checklist)

    assert items_by_label["Provider-backed attractions available"]["status"] == "checked"
    # Real restaurant candidates exist via fallback -- still "checked", not
    # "needs_review", since this item only checks candidate availability.
    assert items_by_label["Restaurant candidates available"]["status"] == "checked"
    # No accommodation POI candidates at all (search_accommodation_pois
    # failed for this double) -- correctly "missing_data".
    assert items_by_label["Accommodation POI candidates available"]["status"] == "missing_data"

    validation_response = client.get(f"/trips/{trip_id}/validation-report")
    assert validation_response.status_code == 200
    assert (
        validation_response.json()["data"]["validation_report"]["readiness_status"]
        == "needs_review"
    )


def test_readiness_checklist_marks_unimplemented_checks_honestly(
    client: TestClient, generated_trip_id: str
) -> None:
    # Route times, opening hours, walking feasibility, budget/cost fit,
    # accommodation price/availability, weather impact, holiday/closure
    # context, and booking links are never actually checked yet in this
    # deployment, no matter how the candidate data looks, so none of these
    # may ever be reported as "checked" or "needs_review".
    response = client.get(f"/trips/{generated_trip_id}/experience-plan")
    assert response.status_code == 200
    readiness_checklist = response.json()["data"]["experience_plan"]["readiness_checklist"]
    items_by_label = _checklist_items_by_label(readiness_checklist)

    assert set(items_by_label.keys()) == (
        _CANDIDATE_AVAILABILITY_ITEM_LABELS | _NOT_YET_CHECKED_ITEM_LABELS
    )

    for label in _NOT_YET_CHECKED_ITEM_LABELS:
        assert items_by_label[label]["status"] in {"missing_data", "not_implemented"}

    # Opening hours and budget/cost fit have no connected provider at all in
    # this deployment, and the app also has no code path for them yet --
    # honestly "not_implemented" rather than blaming a missing provider.
    assert items_by_label["Opening hours checked"]["status"] == "not_implemented"
    assert items_by_label["Budget/cost fit checked"]["status"] == "not_implemented"


def test_readiness_checklist_labels_missing_providers_honestly(
    client: TestClient, generated_trip_id: str
) -> None:
    # A routes provider and a booking-capable accommodation provider are
    # never connected at all in this deployment, so the checklist items
    # that depend on them must say "missing_data" and accurately say "not
    # connected" (the provider itself is absent) -- never blaming
    # unimplemented app logic.
    response = client.get(f"/trips/{generated_trip_id}/experience-plan")
    assert response.status_code == 200
    readiness_checklist = response.json()["data"]["experience_plan"]["readiness_checklist"]
    items_by_label = _checklist_items_by_label(readiness_checklist)

    for label in (
        "Route times checked",
        "Walking feasibility checked",
        "Accommodation price/availability checked",
        "Booking links available",
    ):
        item = items_by_label[label]
        assert item["status"] == "missing_data"
        assert "not connected" in item["explanation"].lower()

    # The deterministic test places provider never resolves coordinates, so
    # the real Open-Meteo weather adapter is reached but has nothing to
    # request -- it honestly reports "unavailable", not "not_connected".
    # The explanation must say so accurately instead of claiming "not
    # connected" for a provider that is, in fact, connected and was tried.
    weather_item = items_by_label["Weather impact checked"]
    assert weather_item["status"] == "missing_data"
    assert "not connected" not in weather_item["explanation"].lower()
    assert "returned no usable weather data" in weather_item["explanation"].lower()

    # "Testville, Testland" isn't a real country, so the real Nager.Date
    # holidays adapter is reached but can't infer a country code -- it
    # honestly reports "unavailable", not "not_connected".
    holidays_item = items_by_label["Holiday/closure context checked"]
    assert holidays_item["status"] == "missing_data"
    assert "not connected" not in holidays_item["explanation"].lower()
    assert "returned no usable holiday data" in holidays_item["explanation"].lower()


def test_readiness_checklist_creates_no_fake_values(
    client: TestClient, generated_trip_id: str
) -> None:
    response = client.get(f"/trips/{generated_trip_id}/experience-plan")
    assert response.status_code == 200
    readiness_checklist = response.json()["data"]["experience_plan"]["readiness_checklist"]

    assert set(readiness_checklist.keys()) == {"summary", "items"}
    assert isinstance(readiness_checklist["summary"], str)

    # Every item is plain explanatory text plus an enum status -- no
    # structured price/rating/review/opening-hour/booking/availability/
    # route/walking/cost value is ever attached to this section itself.
    for item in readiness_checklist["items"]:
        assert set(item.keys()) == {"label", "status", "explanation"}
        assert isinstance(item["label"], str)
        assert isinstance(item["status"], str)
        assert isinstance(item["explanation"], str)

    for forbidden_field in (
        "rating",
        "review",
        "opening_hours",
        "booking_url",
        "reservation_url",
        "route_time",
        "walking_distance",
        "estimated_cost",
    ):
        assert forbidden_field not in readiness_checklist
        for item in readiness_checklist["items"]:
            assert forbidden_field not in item


class _ConnectedTestWeatherProvider(WeatherProvider):
    """Deterministic test double standing in for `OpenMeteoWeatherAdapter`,
    always returning usable daily forecast data regardless of coordinates.
    Only used by tests that need to exercise the "weather is connected"
    path; other tests keep the real `OpenMeteoWeatherAdapter`, which stays
    honestly unavailable in tests because the deterministic test places
    provider never resolves coordinates."""

    provider_name = "open_meteo"

    def get_weather_forecast(
        self,
        destination: str,
        dates: dict[str, Any],
        coordinates: GeoPoint | None = None,
    ) -> ProviderResponse[Any]:
        daily = [
            NormalizedDailyWeather(
                date=date(2026, 8, 10),
                temperature_max_c=28.0,
                temperature_min_c=18.0,
                precipitation_probability_max=10.0,
                precipitation_sum_mm=0.0,
                weather_code=1,
                source=self.provider_name,
                data_status=DataStatus.LIVE,
            )
        ]
        return ProviderResponse[list[NormalizedDailyWeather]](
            provider_name=self.provider_name,
            provider_type=self.provider_type,
            status=ProviderStatus.SUCCESS,
            data_status=DataStatus.LIVE,
            data=daily,
            confidence=0.6,
            message="Test fixture weather data; not a real provider call.",
        )


def test_weather_context_exists_after_generate(
    client: TestClient, generated_trip_id: str
) -> None:
    response = client.get(f"/trips/{generated_trip_id}/destination-context")
    assert response.status_code == 200
    data = response.json()["data"]

    assert "weather_context" in data
    weather_context = data["weather_context"]
    assert weather_context is not None

    assert set(weather_context.keys()) == {
        "destination",
        "start_date",
        "end_date",
        "daily_weather",
        "source",
        "data_status",
        "confidence",
        "assumptions",
        "warnings",
    }
    assert weather_context["destination"]
    assert weather_context["start_date"] == "2026-08-10"
    assert weather_context["end_date"] == "2026-08-12"
    assert isinstance(weather_context["daily_weather"], list)

    # The deterministic test places provider never resolves coordinates
    # (it doesn't override `resolve_coordinates`), so weather honestly stays
    # unavailable in this default test environment -- never invented.
    assert weather_context["daily_weather"] == []
    assert weather_context["data_status"] == "unavailable"


def test_weather_context_has_usable_daily_data_when_connected(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provider_gateway, "weather", _ConnectedTestWeatherProvider())

    generate_response = client.post(f"/trips/{created_trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{created_trip_id}/destination-context")
    assert response.status_code == 200
    weather_context = response.json()["data"]["weather_context"]

    assert weather_context["data_status"] == "live"
    assert weather_context["source"] == "open_meteo"
    assert len(weather_context["daily_weather"]) == 1

    day = weather_context["daily_weather"][0]
    assert set(day.keys()) == {
        "date",
        "temperature_max_c",
        "temperature_min_c",
        "precipitation_probability_max",
        "precipitation_sum_mm",
        "weather_code",
        "source",
        "data_status",
    }
    assert day["date"] == "2026-08-10"
    assert day["temperature_max_c"] == 28.0
    assert day["source"] == "open_meteo"


def test_provider_coverage_weather_success_only_with_usable_data(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Default deterministic test environment: weather stays unavailable
    # because coordinates cannot be resolved, so coverage must never claim
    # "success" for weather that doesn't exist.
    generate_response = client.post(f"/trips/{created_trip_id}/generate")
    assert generate_response.status_code == 200

    coverage_response = client.get(f"/trips/{created_trip_id}/provider-coverage")
    assert coverage_response.status_code == 200
    assert coverage_response.json()["data"]["provider_coverage"]["weather"] != "success"

    # With a connected weather provider returning usable daily data, coverage
    # must become "success".
    monkeypatch.setattr(provider_gateway, "weather", _ConnectedTestWeatherProvider())

    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-12",
            "travelers_count": 2,
            "travel_group_type": "couple",
        },
    )
    assert create_response.status_code == 201
    connected_trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{connected_trip_id}/generate")
    assert generate_response.status_code == 200

    coverage_response = client.get(f"/trips/{connected_trip_id}/provider-coverage")
    assert coverage_response.status_code == 200
    assert coverage_response.json()["data"]["provider_coverage"]["weather"] == "success"


def test_readiness_checklist_marks_weather_needs_review_when_connected(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provider_gateway, "weather", _ConnectedTestWeatherProvider())

    generate_response = client.post(f"/trips/{created_trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{created_trip_id}/experience-plan")
    assert response.status_code == 200
    readiness_checklist = response.json()["data"]["experience_plan"]["readiness_checklist"]
    items_by_label = _checklist_items_by_label(readiness_checklist)

    weather_item = items_by_label["Weather impact checked"]
    # Weather data exists, but weather-aware itinerary adjustment is not
    # implemented yet -- needs_review, never checked.
    assert weather_item["status"] == "needs_review"
    assert "not implemented" in weather_item["explanation"].lower()


def test_readiness_checklist_checked_count_is_three_with_weather_needs_review(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The deterministic test places provider returns real attraction/
    # restaurant/accommodation POI candidates (all three "checked"), and a
    # connected weather provider makes "Weather impact checked" only
    # "needs_review" -- so exactly 3 items are "checked" overall.
    monkeypatch.setattr(provider_gateway, "weather", _ConnectedTestWeatherProvider())

    generate_response = client.post(f"/trips/{created_trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{created_trip_id}/experience-plan")
    assert response.status_code == 200
    readiness_checklist = response.json()["data"]["experience_plan"]["readiness_checklist"]
    items_by_label = _checklist_items_by_label(readiness_checklist)

    checked_labels = {
        label for label, item in items_by_label.items() if item["status"] == "checked"
    }
    assert checked_labels == _CANDIDATE_AVAILABILITY_ITEM_LABELS
    assert len(checked_labels) == 3

    assert items_by_label["Weather impact checked"]["status"] == "needs_review"
    assert f"{len(checked_labels)} of" in readiness_checklist["summary"]


def test_implementation_gaps_lists_weather_connected_when_usable(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provider_gateway, "weather", _ConnectedTestWeatherProvider())

    generate_response = client.post(f"/trips/{created_trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{created_trip_id}/experience-plan")
    assert response.status_code == 200
    implementation_gaps = response.json()["data"]["experience_plan"]["implementation_gaps"]

    connected_text = " ".join(implementation_gaps["connected_data"]).lower()
    missing_text = " ".join(implementation_gaps["missing_data"]).lower()

    assert "weather" in connected_text
    assert "weather" not in missing_text


class _ConnectedTestHolidayProvider(HolidayProvider):
    """Deterministic test double standing in for `NagerDateHolidaysAdapter`,
    always returning one usable in-range public holiday regardless of the
    destination it's given. Only used by tests that need to exercise the
    "holidays is connected" path; other tests keep the real
    `NagerDateHolidaysAdapter`, which stays honestly unavailable in tests
    because "Testville, Testland" isn't a real country."""

    provider_name = "nager_date"

    def get_public_holidays(
        self, destination: str, dates: dict[str, Any]
    ) -> ProviderResponse[Any]:
        holidays = [
            NormalizedHoliday(
                date=date(2026, 8, 11),
                local_name="Feriado de Teste",
                name="Test Holiday",
                country_code="PT",
                is_global=True,
                counties=[],
                types=["Public"],
                source=self.provider_name,
                data_status=DataStatus.LIVE,
            )
        ]
        return ProviderResponse[list[NormalizedHoliday]](
            provider_name=self.provider_name,
            provider_type=self.provider_type,
            status=ProviderStatus.SUCCESS,
            data_status=DataStatus.LIVE,
            data=holidays,
            confidence=0.6,
            message="Test fixture holiday data; not a real provider call.",
        )


def test_holiday_context_exists_after_generate(
    client: TestClient, generated_trip_id: str
) -> None:
    response = client.get(f"/trips/{generated_trip_id}/destination-context")
    assert response.status_code == 200
    data = response.json()["data"]

    assert "holiday_context" in data
    holiday_context = data["holiday_context"]
    assert holiday_context is not None

    assert set(holiday_context.keys()) == {
        "destination",
        "start_date",
        "end_date",
        "country_code",
        "holidays",
        "source",
        "data_status",
        "confidence",
        "assumptions",
        "warnings",
    }
    assert holiday_context["destination"]
    assert holiday_context["start_date"] == "2026-08-10"
    assert holiday_context["end_date"] == "2026-08-12"
    assert isinstance(holiday_context["holidays"], list)

    # "Testville, Testland" isn't a real country, so the real Nager.Date
    # adapter honestly can't infer a country code -- unavailable, never
    # invented.
    assert holiday_context["country_code"] is None
    assert holiday_context["holidays"] == []
    assert holiday_context["data_status"] == "unavailable"

    # This section is purely provider-backed and must never flip readiness
    # by itself; the deterministic test provider still leaves the plan
    # needs_review (route/timing feasibility is not implemented yet).
    validation_response = client.get(f"/trips/{generated_trip_id}/validation-report")
    assert validation_response.status_code == 200
    assert (
        validation_response.json()["data"]["validation_report"]["readiness_status"]
        == "needs_review"
    )


def test_holiday_context_has_usable_holiday_data_when_connected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provider_gateway, "holiday", _ConnectedTestHolidayProvider())

    # A real, country-mappable destination, unlike the default
    # "Testville, Testland" fixture, so `country_code` (independently
    # inferred by the service from the destination text) genuinely lines up
    # with the "PT" holiday data this test double fabricates.
    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Lisbon, Portugal",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-12",
            "travelers_count": 2,
            "travel_group_type": "couple",
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{trip_id}/destination-context")
    assert response.status_code == 200
    holiday_context = response.json()["data"]["holiday_context"]

    assert holiday_context["data_status"] == "live"
    assert holiday_context["source"] == "nager_date"
    assert holiday_context["country_code"] == "PT"
    assert len(holiday_context["holidays"]) == 1

    holiday = holiday_context["holidays"][0]
    assert set(holiday.keys()) == {
        "date",
        "local_name",
        "name",
        "country_code",
        "is_global",
        "counties",
        "types",
        "source",
        "data_status",
    }
    assert holiday["date"] == "2026-08-11"
    assert holiday["source"] == "nager_date"


def test_provider_coverage_holidays_success_only_with_usable_data(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Default deterministic test environment: holidays stays unavailable
    # because "Testville, Testland" isn't a real country, so coverage must
    # never claim "success" for holiday data that doesn't exist.
    generate_response = client.post(f"/trips/{created_trip_id}/generate")
    assert generate_response.status_code == 200

    coverage_response = client.get(f"/trips/{created_trip_id}/provider-coverage")
    assert coverage_response.status_code == 200
    assert coverage_response.json()["data"]["provider_coverage"]["holidays"] != "success"

    # With a connected holidays provider returning usable data, coverage
    # must become "success".
    monkeypatch.setattr(provider_gateway, "holiday", _ConnectedTestHolidayProvider())

    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-12",
            "travelers_count": 2,
            "travel_group_type": "couple",
        },
    )
    assert create_response.status_code == 201
    connected_trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{connected_trip_id}/generate")
    assert generate_response.status_code == 200

    coverage_response = client.get(f"/trips/{connected_trip_id}/provider-coverage")
    assert coverage_response.status_code == 200
    assert coverage_response.json()["data"]["provider_coverage"]["holidays"] == "success"


def test_readiness_checklist_marks_holidays_needs_review_when_connected(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provider_gateway, "holiday", _ConnectedTestHolidayProvider())

    generate_response = client.post(f"/trips/{created_trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{created_trip_id}/experience-plan")
    assert response.status_code == 200
    readiness_checklist = response.json()["data"]["experience_plan"]["readiness_checklist"]
    items_by_label = _checklist_items_by_label(readiness_checklist)

    holiday_item = items_by_label["Holiday/closure context checked"]
    # Holiday data exists, but checking it against venue closures/crowd risk
    # is not implemented yet -- needs_review, never checked.
    assert holiday_item["status"] == "needs_review"
    assert "not implemented" in holiday_item["explanation"].lower()


def test_implementation_gaps_lists_holidays_connected_when_usable(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provider_gateway, "holiday", _ConnectedTestHolidayProvider())

    generate_response = client.post(f"/trips/{created_trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{created_trip_id}/experience-plan")
    assert response.status_code == 200
    implementation_gaps = response.json()["data"]["experience_plan"]["implementation_gaps"]

    connected_text = " ".join(implementation_gaps["connected_data"]).lower()
    missing_text = " ".join(implementation_gaps["missing_data"]).lower()

    assert "holiday" in connected_text
    assert "holiday" not in missing_text


def test_holiday_context_success_with_no_holidays_in_range(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """holiday_context stays live/success with an empty list and an honest
    assumption when the provider has real data for the year but none of it
    falls inside the trip's date range."""

    class _NoInRangeHolidayProvider(HolidayProvider):
        provider_name = "nager_date"

        def get_public_holidays(
            self, destination: str, dates: dict[str, Any]
        ) -> ProviderResponse[Any]:
            return ProviderResponse[list[NormalizedHoliday]](
                provider_name=self.provider_name,
                provider_type=self.provider_type,
                status=ProviderStatus.SUCCESS,
                data_status=DataStatus.LIVE,
                data=[],
                confidence=0.6,
                message="Test fixture: provider has data, none in range.",
            )

    monkeypatch.setattr(provider_gateway, "holiday", _NoInRangeHolidayProvider())

    generate_response = client.post(f"/trips/{created_trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{created_trip_id}/destination-context")
    assert response.status_code == 200
    holiday_context = response.json()["data"]["holiday_context"]

    assert holiday_context["data_status"] == "live"
    assert holiday_context["holidays"] == []
    assert any(
        "no public holidays from provider data fall within this trip date range" in a.lower()
        for a in holiday_context["assumptions"]
    )

    coverage_response = client.get(f"/trips/{created_trip_id}/provider-coverage")
    assert coverage_response.status_code == 200
    assert coverage_response.json()["data"]["provider_coverage"]["holidays"] == "success"

    readiness_response = client.get(f"/trips/{created_trip_id}/experience-plan")
    assert readiness_response.status_code == 200
    readiness_checklist = readiness_response.json()["data"]["experience_plan"][
        "readiness_checklist"
    ]
    holiday_item = _checklist_items_by_label(readiness_checklist)["Holiday/closure context checked"]
    assert holiday_item["status"] == "needs_review"


class _CustomTestHolidayProvider(HolidayProvider):
    """Test double returning caller-specified in-range holiday entries
    (mirroring what a real adapter would return after filtering to the
    trip's date range), so tests can exercise specific holiday-warning
    scenarios without depending on real Nager.Date network data."""

    provider_name = "nager_date"

    def __init__(self, holidays: list[NormalizedHoliday]) -> None:
        self._holidays = holidays

    def get_public_holidays(
        self, destination: str, dates: dict[str, Any]
    ) -> ProviderResponse[Any]:
        return ProviderResponse[list[NormalizedHoliday]](
            provider_name=self.provider_name,
            provider_type=self.provider_type,
            status=ProviderStatus.SUCCESS,
            data_status=DataStatus.LIVE,
            data=self._holidays,
            confidence=0.6,
            message="Test fixture holiday data; not a real provider call.",
        )


def _find_holiday_warning(warnings: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((issue for issue in warnings if issue["category"] == "holidays"), None)


def test_validation_report_includes_holiday_warning_when_usable_holiday_data_exists(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provider_gateway, "holiday", _ConnectedTestHolidayProvider())

    generate_response = client.post(f"/trips/{created_trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{created_trip_id}/validation-report")
    assert response.status_code == 200
    validation_report = response.json()["data"]["validation_report"]

    holiday_warning = _find_holiday_warning(validation_report["warnings"])
    assert holiday_warning is not None
    assert holiday_warning["severity"] == "warning"

    message = holiday_warning["message"].lower()
    assert "provider-backed" in message
    assert "public holiday" in message
    assert "closures" in message
    assert "opening hours" in message
    assert "crowd" in message
    assert "review" in message and "manually" in message

    # Holidays never mark the plan ready by itself.
    assert validation_report["readiness_status"] == "needs_review"


def test_validation_report_holiday_warning_includes_dates_and_names_in_range(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    holidays = [
        NormalizedHoliday(
            date=date(2026, 8, 11),
            local_name="Feriado de Teste",
            name="Test Holiday",
            country_code="PT",
            is_global=True,
            counties=[],
            types=["Public"],
            source="nager_date",
            data_status=DataStatus.LIVE,
        ),
        NormalizedHoliday(
            date=date(2026, 8, 12),
            local_name="Segundo Feriado",
            name="Second Holiday",
            country_code="PT",
            is_global=True,
            counties=[],
            types=["Public"],
            source="nager_date",
            data_status=DataStatus.LIVE,
        ),
    ]
    monkeypatch.setattr(provider_gateway, "holiday", _CustomTestHolidayProvider(holidays))

    generate_response = client.post(f"/trips/{created_trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{created_trip_id}/validation-report")
    assert response.status_code == 200
    warnings = response.json()["data"]["validation_report"]["warnings"]

    holiday_warning = _find_holiday_warning(warnings)
    assert holiday_warning is not None
    assert "2026-08-11" in holiday_warning["message"]
    assert "Test Holiday" in holiday_warning["message"]
    assert "2026-08-12" in holiday_warning["message"]
    assert "Second Holiday" in holiday_warning["message"]


def test_validation_report_softer_holiday_warning_when_none_in_range(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provider_gateway, "holiday", _CustomTestHolidayProvider([]))

    generate_response = client.post(f"/trips/{created_trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{created_trip_id}/validation-report")
    assert response.status_code == 200
    warnings = response.json()["data"]["validation_report"]["warnings"]

    holiday_warning = _find_holiday_warning(warnings)
    assert holiday_warning is not None
    message = holiday_warning["message"].lower()
    assert "no public holidays fall within" in message
    assert "not implemented" in message


def test_validation_report_no_holiday_warning_when_holidays_unavailable(
    client: TestClient, generated_trip_id: str
) -> None:
    # "Testville, Testland" isn't a real country, so holidays stays
    # honestly unavailable in this default test environment -- no holiday
    # warning should be fabricated.
    response = client.get(f"/trips/{generated_trip_id}/validation-report")
    assert response.status_code == 200
    validation_report = response.json()["data"]["validation_report"]

    assert _find_holiday_warning(validation_report["warnings"]) is None
    assert validation_report["readiness_status"] == "needs_review"


def test_validation_report_no_holiday_warning_when_request_failed(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FailedTestHolidayProvider(HolidayProvider):
        provider_name = "nager_date"

        def get_public_holidays(
            self, destination: str, dates: dict[str, Any]
        ) -> ProviderResponse[Any]:
            return failed_response(
                self.provider_name,
                self.provider_type,
                unavailable_fields=["public_holidays"],
                message="Test fixture failure; not a real provider call.",
            )

    monkeypatch.setattr(provider_gateway, "holiday", _FailedTestHolidayProvider())

    generate_response = client.post(f"/trips/{created_trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{created_trip_id}/validation-report")
    assert response.status_code == 200
    validation_report = response.json()["data"]["validation_report"]

    assert _find_holiday_warning(validation_report["warnings"]) is None
    assert validation_report["readiness_status"] == "needs_review"


def test_validation_report_holiday_warning_creates_no_fake_claims(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    holidays = [
        NormalizedHoliday(
            date=date(2026, 8, 11),
            local_name="Feriado de Teste",
            name="Test Holiday",
            country_code="PT",
            is_global=True,
            counties=[],
            types=["Public"],
            source="nager_date",
            data_status=DataStatus.LIVE,
        )
    ]
    monkeypatch.setattr(provider_gateway, "holiday", _CustomTestHolidayProvider(holidays))

    generate_response = client.post(f"/trips/{created_trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{created_trip_id}/validation-report")
    assert response.status_code == 200
    warnings = response.json()["data"]["validation_report"]["warnings"]

    holiday_warning = _find_holiday_warning(warnings)
    assert holiday_warning is not None

    assert set(holiday_warning.keys()) == {
        "issue_id",
        "severity",
        "category",
        "message",
        "affected_section",
        "suggested_fix",
        "claim_sources",
    }

    combined_text = f"{holiday_warning['message']} {holiday_warning['suggested_fix']}".lower()
    for forbidden_term in (
        "closed",
        "crowded",
        "crowd risk",
        "closure risk",
        "safety risk",
        "event",
        "festival",
        "strike",
        "uv",
        "humidity",
        "alert",
        "rating",
        "price",
    ):
        assert forbidden_term not in combined_text

    # This warning never edits daily plans -- scheduling stays unchanged.
    experience_plan_response = client.get(f"/trips/{created_trip_id}/experience-plan")
    assert experience_plan_response.status_code == 200
    daily_plans = experience_plan_response.json()["data"]["experience_plan"]["daily_plans"]
    for day_plan in daily_plans:
        assert isinstance(day_plan["experiences"], list)


class _ConnectedTestCurrencyProvider(CurrencyProvider):
    """Deterministic test double standing in for
    `FrankfurterCurrencyAdapter`, always returning a usable USD -> EUR
    exchange rate regardless of the destination it's given. Only used by
    tests that need to exercise the "currency is connected" path; other
    tests keep the real `FrankfurterCurrencyAdapter`, which stays honestly
    unavailable in tests because "Testville, Testland" isn't a real
    country."""

    provider_name = "frankfurter"

    def get_exchange_rate(
        self, base_currency: str, destination: str
    ) -> ProviderResponse[Any]:
        return ProviderResponse[NormalizedExchangeRate](
            provider_name=self.provider_name,
            provider_type=self.provider_type,
            status=ProviderStatus.SUCCESS,
            data_status=DataStatus.LIVE,
            data=NormalizedExchangeRate(
                base_currency=base_currency,
                destination_currency="EUR",
                exchange_rate=0.92,
                rate_date=date(2026, 8, 10),
                source=self.provider_name,
                data_status=DataStatus.LIVE,
            ),
            confidence=0.6,
            message="Test fixture exchange rate data; not a real provider call.",
        )


def test_currency_context_exists_after_generate(
    client: TestClient, generated_trip_id: str
) -> None:
    response = client.get(f"/trips/{generated_trip_id}/destination-context")
    assert response.status_code == 200
    data = response.json()["data"]

    assert "currency_context" in data
    currency_context = data["currency_context"]
    assert currency_context is not None

    assert set(currency_context.keys()) == {
        "base_currency",
        "destination_currency",
        "exchange_rate",
        "rate_date",
        "source",
        "data_status",
        "confidence",
        "assumptions",
        "warnings",
    }
    assert currency_context["base_currency"] == "USD"

    # "Testville, Testland" isn't a real country, so the real Frankfurter
    # adapter honestly can't infer a destination currency -- unavailable,
    # never invented.
    assert currency_context["destination_currency"] is None
    assert currency_context["exchange_rate"] is None
    assert currency_context["data_status"] == "unavailable"

    # This section is purely provider-backed and must never flip readiness
    # by itself; the deterministic test provider still leaves the plan
    # needs_review (route/timing feasibility is not implemented yet).
    validation_response = client.get(f"/trips/{generated_trip_id}/validation-report")
    assert validation_response.status_code == 200
    assert (
        validation_response.json()["data"]["validation_report"]["readiness_status"]
        == "needs_review"
    )


def test_currency_context_has_usable_rate_when_connected(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provider_gateway, "currency", _ConnectedTestCurrencyProvider())

    generate_response = client.post(f"/trips/{created_trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{created_trip_id}/destination-context")
    assert response.status_code == 200
    currency_context = response.json()["data"]["currency_context"]

    assert currency_context["data_status"] == "live"
    assert currency_context["source"] == "frankfurter"
    assert currency_context["base_currency"] == "USD"
    assert currency_context["destination_currency"] == "EUR"
    assert currency_context["exchange_rate"] == 0.92
    assert currency_context["rate_date"] == "2026-08-10"


def test_provider_coverage_currency_success_only_with_usable_data(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Default deterministic test environment: currency stays unavailable
    # because "Testville, Testland" isn't a real country, so coverage must
    # never claim "success" for currency data that doesn't exist.
    generate_response = client.post(f"/trips/{created_trip_id}/generate")
    assert generate_response.status_code == 200

    coverage_response = client.get(f"/trips/{created_trip_id}/provider-coverage")
    assert coverage_response.status_code == 200
    assert coverage_response.json()["data"]["provider_coverage"]["currency"] != "success"

    # With a connected currency provider returning a usable rate, coverage
    # must become "success".
    monkeypatch.setattr(provider_gateway, "currency", _ConnectedTestCurrencyProvider())

    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-12",
            "travelers_count": 2,
            "travel_group_type": "couple",
        },
    )
    assert create_response.status_code == 201
    connected_trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{connected_trip_id}/generate")
    assert generate_response.status_code == 200

    coverage_response = client.get(f"/trips/{connected_trip_id}/provider-coverage")
    assert coverage_response.status_code == 200
    assert coverage_response.json()["data"]["provider_coverage"]["currency"] == "success"


def test_readiness_checklist_budget_cost_fit_stays_not_implemented_when_currency_connected(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provider_gateway, "currency", _ConnectedTestCurrencyProvider())

    generate_response = client.post(f"/trips/{created_trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{created_trip_id}/experience-plan")
    assert response.status_code == 200
    readiness_checklist = response.json()["data"]["experience_plan"]["readiness_checklist"]
    items_by_label = _checklist_items_by_label(readiness_checklist)

    # A connected currency provider only supplies a single-unit exchange
    # rate, never a calculated total cost -- budget/cost fit must stay
    # not_implemented, never checked or needs_review.
    budget_item = items_by_label["Budget/cost fit checked"]
    assert budget_item["status"] == "not_implemented"

    # No new "currency" readiness checklist item was added.
    assert "Currency" not in " ".join(items_by_label.keys())


def test_implementation_gaps_lists_currency_connected_when_usable(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provider_gateway, "currency", _ConnectedTestCurrencyProvider())

    generate_response = client.post(f"/trips/{created_trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{created_trip_id}/experience-plan")
    assert response.status_code == 200
    implementation_gaps = response.json()["data"]["experience_plan"]["implementation_gaps"]

    connected_text = " ".join(implementation_gaps["connected_data"]).lower()
    missing_text = " ".join(implementation_gaps["missing_data"]).lower()

    assert "currency" in connected_text
    assert "currency" not in missing_text

    # Costs/budget fit still aren't validated, currency connection or not.
    why_needs_review_text = " ".join(implementation_gaps["why_needs_review"]).lower()
    assert "costs and budget fit are not validated" in why_needs_review_text


def test_validation_report_budget_warning_mentions_currency_without_claiming_fit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provider_gateway, "currency", _ConnectedTestCurrencyProvider())

    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-12",
            "travelers_count": 2,
            "travel_group_type": "couple",
            "budget_min": 1500,
            "budget_max": 2500,
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{trip_id}/validation-report")
    assert response.status_code == 200
    validation_report = response.json()["data"]["validation_report"]

    budget_warnings = [
        warning
        for warning in validation_report["warnings"]
        if warning["category"] == "budget"
    ]
    assert len(budget_warnings) == 1
    message = budget_warnings[0]["message"]

    # The captured budget range is still named exactly, and cost validation
    # is still honestly unimplemented.
    assert "1500" in message
    assert "2500" in message
    assert "not implemented yet" in message
    assert "does not confirm" in message

    # Currency context is mentioned, but budget fit is never claimed.
    assert "exchange-rate" in message.lower()
    assert "USD" in message and "EUR" in message
    assert "still does not confirm" in message.lower()
    assert "fits the budget" not in message.lower()
    assert "meets the budget" not in message.lower()
    assert "within budget" not in message.lower()

    # Currency never marks the plan ready by itself.
    assert validation_report["readiness_status"] == "needs_review"


def test_validation_report_budget_warning_without_currency_still_does_not_claim_fit(
    client: TestClient,
) -> None:
    # Default deterministic test environment: currency stays unavailable,
    # so the budget warning must not mention exchange-rate context that
    # doesn't exist.
    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-12",
            "travelers_count": 2,
            "travel_group_type": "couple",
            "budget_min": 1500,
            "budget_max": 2500,
        },
    )
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{trip_id}/validation-report")
    assert response.status_code == 200
    validation_report = response.json()["data"]["validation_report"]

    budget_warnings = [
        warning
        for warning in validation_report["warnings"]
        if warning["category"] == "budget"
    ]
    assert len(budget_warnings) == 1
    message = budget_warnings[0]["message"]

    assert "not implemented yet" in message
    assert "does not confirm" in message
    assert "exchange-rate" not in message.lower()
    assert "fits the budget" not in message.lower()


class _CustomTestWeatherProvider(WeatherProvider):
    """Test double returning caller-specified daily forecast entries, so
    tests can exercise specific precipitation/temperature threshold values
    without depending on real Open-Meteo network data."""

    provider_name = "open_meteo"

    def __init__(self, daily: list[NormalizedDailyWeather]) -> None:
        self._daily = daily

    def get_weather_forecast(
        self,
        destination: str,
        dates: dict[str, Any],
        coordinates: GeoPoint | None = None,
    ) -> ProviderResponse[Any]:
        return ProviderResponse[list[NormalizedDailyWeather]](
            provider_name=self.provider_name,
            provider_type=self.provider_type,
            status=ProviderStatus.SUCCESS,
            data_status=DataStatus.LIVE,
            data=self._daily,
            confidence=0.6,
            message="Test fixture weather data; not a real provider call.",
        )


def _mild_daily_weather(day: date) -> NormalizedDailyWeather:
    return NormalizedDailyWeather(
        date=day,
        temperature_max_c=22.0,
        temperature_min_c=14.0,
        precipitation_probability_max=10.0,
        precipitation_sum_mm=0.0,
        weather_code=1,
        source="open_meteo",
        data_status=DataStatus.LIVE,
    )


def _find_weather_warning(warnings: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((issue for issue in warnings if issue["category"] == "weather"), None)


def test_validation_report_includes_weather_warning_when_usable_daily_weather_exists(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provider_gateway, "weather", _ConnectedTestWeatherProvider())

    generate_response = client.post(f"/trips/{created_trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{created_trip_id}/validation-report")
    assert response.status_code == 200
    validation_report = response.json()["data"]["validation_report"]

    weather_warning = _find_weather_warning(validation_report["warnings"])
    assert weather_warning is not None
    assert weather_warning["severity"] == "warning"

    message = weather_warning["message"].lower()
    assert "provider-backed" in message
    assert "weather" in message
    assert "not been adjusted" in message
    assert "review" in message and "manually" in message

    # Weather never marks the plan ready by itself.
    assert validation_report["readiness_status"] == "needs_review"


def test_validation_report_weather_warning_includes_high_precipitation_date(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    daily = [
        _mild_daily_weather(date(2026, 8, 10)),
        NormalizedDailyWeather(
            date=date(2026, 8, 11),
            temperature_max_c=22.0,
            temperature_min_c=14.0,
            precipitation_probability_max=60.0,
            precipitation_sum_mm=8.0,
            weather_code=61,
            source="open_meteo",
            data_status=DataStatus.LIVE,
        ),
    ]
    monkeypatch.setattr(provider_gateway, "weather", _CustomTestWeatherProvider(daily))

    generate_response = client.post(f"/trips/{created_trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{created_trip_id}/validation-report")
    assert response.status_code == 200
    warnings = response.json()["data"]["validation_report"]["warnings"]

    weather_warning = _find_weather_warning(warnings)
    assert weather_warning is not None
    assert "2026-08-11" in weather_warning["message"]
    assert "2026-08-10" not in weather_warning["message"]


def test_validation_report_weather_warning_includes_high_temperature_date(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    daily = [
        _mild_daily_weather(date(2026, 8, 10)),
        NormalizedDailyWeather(
            date=date(2026, 8, 12),
            temperature_max_c=32.0,
            temperature_min_c=20.0,
            precipitation_probability_max=5.0,
            precipitation_sum_mm=0.0,
            weather_code=1,
            source="open_meteo",
            data_status=DataStatus.LIVE,
        ),
    ]
    monkeypatch.setattr(provider_gateway, "weather", _CustomTestWeatherProvider(daily))

    generate_response = client.post(f"/trips/{created_trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{created_trip_id}/validation-report")
    assert response.status_code == 200
    warnings = response.json()["data"]["validation_report"]["warnings"]

    weather_warning = _find_weather_warning(warnings)
    assert weather_warning is not None
    assert "2026-08-12" in weather_warning["message"]
    assert "2026-08-10" not in weather_warning["message"]


def test_validation_report_weather_warning_includes_low_temperature_date(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    daily = [
        _mild_daily_weather(date(2026, 8, 11)),
        NormalizedDailyWeather(
            date=date(2026, 8, 10),
            temperature_max_c=8.0,
            temperature_min_c=3.0,
            precipitation_probability_max=5.0,
            precipitation_sum_mm=0.0,
            weather_code=1,
            source="open_meteo",
            data_status=DataStatus.LIVE,
        ),
    ]
    monkeypatch.setattr(provider_gateway, "weather", _CustomTestWeatherProvider(daily))

    generate_response = client.post(f"/trips/{created_trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{created_trip_id}/validation-report")
    assert response.status_code == 200
    warnings = response.json()["data"]["validation_report"]["warnings"]

    weather_warning = _find_weather_warning(warnings)
    assert weather_warning is not None
    assert "2026-08-10" in weather_warning["message"]
    assert "2026-08-11" not in weather_warning["message"]


def test_validation_report_no_weather_warning_when_weather_unavailable(
    client: TestClient, generated_trip_id: str
) -> None:
    # The deterministic test places provider never resolves coordinates, so
    # weather stays honestly unavailable in this default test environment --
    # no weather warning should be fabricated.
    response = client.get(f"/trips/{generated_trip_id}/validation-report")
    assert response.status_code == 200
    validation_report = response.json()["data"]["validation_report"]

    assert _find_weather_warning(validation_report["warnings"]) is None
    assert validation_report["readiness_status"] == "needs_review"


def test_validation_report_weather_warning_creates_no_fake_fields(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    daily = [
        NormalizedDailyWeather(
            date=date(2026, 8, 10),
            temperature_max_c=32.0,
            temperature_min_c=3.0,
            precipitation_probability_max=60.0,
            precipitation_sum_mm=8.0,
            weather_code=61,
            source="open_meteo",
            data_status=DataStatus.LIVE,
        )
    ]
    monkeypatch.setattr(provider_gateway, "weather", _CustomTestWeatherProvider(daily))

    generate_response = client.post(f"/trips/{created_trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{created_trip_id}/validation-report")
    assert response.status_code == 200
    warnings = response.json()["data"]["validation_report"]["warnings"]

    weather_warning = _find_weather_warning(warnings)
    assert weather_warning is not None

    assert set(weather_warning.keys()) == {
        "issue_id",
        "severity",
        "category",
        "message",
        "affected_section",
        "suggested_fix",
        "claim_sources",
    }

    combined_text = f"{weather_warning['message']} {weather_warning['suggested_fix']}".lower()
    for forbidden_term in (
        "uv",
        "humidity",
        "alert",
        "severe weather",
        "condition",
        "description",
        "comfort risk",
        "rain",
        "heat",
        "cold",
    ):
        assert forbidden_term not in combined_text

    # This warning never edits daily plans -- scheduling stays unchanged.
    experience_plan_response = client.get(f"/trips/{created_trip_id}/experience-plan")
    assert experience_plan_response.status_code == 200
    daily_plans = experience_plan_response.json()["data"]["experience_plan"]["daily_plans"]
    for day_plan in daily_plans:
        assert isinstance(day_plan["experiences"], list)


class _FailedTestWeatherProvider(WeatherProvider):
    """Test double standing in for a weather provider that was reached but
    whose request failed -- distinct from no provider being connected."""

    provider_name = "open_meteo"

    def get_weather_forecast(
        self,
        destination: str,
        dates: dict[str, Any],
        coordinates: GeoPoint | None = None,
    ) -> ProviderResponse[Any]:
        return failed_response(
            self.provider_name,
            self.provider_type,
            unavailable_fields=["weather_forecast"],
            message="Test fixture failure; not a real provider call.",
        )


def test_readiness_checklist_weather_explanation_when_not_connected(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The plain WeatherProvider base class honestly reports not_connected --
    # no provider is wired up at all.
    monkeypatch.setattr(provider_gateway, "weather", WeatherProvider())

    generate_response = client.post(f"/trips/{created_trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{created_trip_id}/experience-plan")
    assert response.status_code == 200
    readiness_checklist = response.json()["data"]["experience_plan"]["readiness_checklist"]
    weather_item = _checklist_items_by_label(readiness_checklist)["Weather impact checked"]

    assert weather_item["status"] == "missing_data"
    assert "a weather provider is not connected" in weather_item["explanation"].lower()


def test_readiness_checklist_weather_explanation_when_failed(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A provider was reached and tried, but the request failed -- this must
    # never be worded as "not connected".
    monkeypatch.setattr(provider_gateway, "weather", _FailedTestWeatherProvider())

    generate_response = client.post(f"/trips/{created_trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{created_trip_id}/experience-plan")
    assert response.status_code == 200
    readiness_checklist = response.json()["data"]["experience_plan"]["readiness_checklist"]
    weather_item = _checklist_items_by_label(readiness_checklist)["Weather impact checked"]

    assert weather_item["status"] == "missing_data"
    explanation = weather_item["explanation"].lower()
    assert "not connected" not in explanation
    assert "the weather provider request failed or returned no usable weather data" in explanation


def test_readiness_checklist_weather_explanation_when_unavailable(
    client: TestClient, generated_trip_id: str
) -> None:
    # The deterministic test places provider never resolves coordinates, so
    # the real Open-Meteo adapter is reached but has nothing to request for
    # -- it honestly reports "unavailable" (a provider that responded but
    # had no usable data), never "not_connected".
    response = client.get(f"/trips/{generated_trip_id}/experience-plan")
    assert response.status_code == 200
    readiness_checklist = response.json()["data"]["experience_plan"]["readiness_checklist"]
    weather_item = _checklist_items_by_label(readiness_checklist)["Weather impact checked"]

    assert weather_item["status"] == "missing_data"
    explanation = weather_item["explanation"].lower()
    assert "not connected" not in explanation
    assert "the weather provider returned no usable weather data" in explanation


class _FailedTestRoutesProvider(RoutesProvider):
    """Test double standing in for a routes provider that was reached but
    whose request failed -- distinct from no routes provider being
    connected."""

    provider_name = "test_routes_provider"

    def estimate_transit_feasibility(
        self, origin: dict[str, Any], destination: dict[str, Any], date_time: str | None = None
    ) -> ProviderResponse[Any]:
        return failed_response(
            self.provider_name,
            self.provider_type,
            unavailable_fields=["transit_feasibility"],
            message="Test fixture failure; not a real provider call.",
        )


def test_readiness_checklist_routes_explanation_distinguishes_failed_from_not_connected(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provider_gateway, "routes", _FailedTestRoutesProvider())

    generate_response = client.post(f"/trips/{created_trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{created_trip_id}/experience-plan")
    assert response.status_code == 200
    readiness_checklist = response.json()["data"]["experience_plan"]["readiness_checklist"]
    items_by_label = _checklist_items_by_label(readiness_checklist)

    route_item = items_by_label["Route times checked"]
    assert route_item["status"] == "missing_data"
    route_explanation = route_item["explanation"].lower()
    assert "not connected" not in route_explanation
    assert "the routes provider request failed or returned no usable route data" in route_explanation

    walking_item = items_by_label["Walking feasibility checked"]
    assert walking_item["status"] == "missing_data"
    walking_explanation = walking_item["explanation"].lower()
    assert "not connected" not in walking_explanation
    assert "the routes provider request failed or returned no usable route data" in walking_explanation

    # Holidays is unaffected by this test's routes double: "Testville,
    # Testland" isn't a real country, so the real Nager.Date adapter is
    # reached but can't infer a country code -- it honestly reports
    # "unavailable", not "not_connected".
    holidays_item = items_by_label["Holiday/closure context checked"]
    assert holidays_item["status"] == "missing_data"
    assert "not connected" not in holidays_item["explanation"].lower()
    assert "returned no usable holiday data" in holidays_item["explanation"].lower()


def test_implementation_gaps_routes_and_holidays_explanations_distinguish_failure_reasons(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provider_gateway, "routes", _FailedTestRoutesProvider())

    generate_response = client.post(f"/trips/{created_trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{created_trip_id}/experience-plan")
    assert response.status_code == 200
    implementation_gaps = response.json()["data"]["experience_plan"]["implementation_gaps"]
    missing_text = " ".join(implementation_gaps["missing_data"]).lower()

    assert "the routes/transit provider request failed or returned no usable route/transit data" in missing_text
    # Holidays is unaffected by this test's routes double: "Testville,
    # Testland" isn't a real country, so the real Nager.Date adapter is
    # reached but can't infer a country code -- it honestly reports
    # "unavailable", not "not_connected".
    assert "a holidays provider is not connected" not in missing_text
    assert "the holidays provider returned no usable holiday data" in missing_text


def test_route_feasibility_context_exists_after_generate(
    client: TestClient, generated_trip_id: str
) -> None:
    response = client.get(f"/trips/{generated_trip_id}/experience-plan")
    assert response.status_code == 200
    experience_plan = response.json()["data"]["experience_plan"]

    assert "route_feasibility_context" in experience_plan
    route_feasibility_context = experience_plan["route_feasibility_context"]

    assert set(route_feasibility_context.keys()) == {
        "source",
        "data_status",
        "confidence",
        "daily_route_feasibility",
        "assumptions",
        "warnings",
    }

    # No route provider is connected in this deployment, so this must stay
    # honestly not_connected -- never a fake/estimated route feasibility.
    assert route_feasibility_context["data_status"] == "not_connected"
    assert route_feasibility_context["confidence"] == 0.0
    assert route_feasibility_context["daily_route_feasibility"] == []
    assert len(route_feasibility_context["assumptions"]) > 0
    assert len(route_feasibility_context["warnings"]) > 0
    assert any(
        "no route provider is connected" in warning.lower()
        for warning in route_feasibility_context["warnings"]
    )

    # This never marks the plan ready by itself; the deterministic test
    # provider still leaves the plan needs_review.
    validation_response = client.get(f"/trips/{generated_trip_id}/validation-report")
    assert validation_response.status_code == 200
    assert (
        validation_response.json()["data"]["validation_report"]["readiness_status"]
        == "needs_review"
    )


def test_route_feasibility_context_status_is_not_connected_without_route_provider(
    client: TestClient, created_trip_id: str
) -> None:
    # No RoutesProvider adapter is wired into this deployment at all (the
    # provider_gateway is untouched by any monkeypatch here), so
    # route_feasibility_context must honestly reflect that.
    generate_response = client.post(f"/trips/{created_trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{created_trip_id}/experience-plan")
    assert response.status_code == 200
    route_feasibility_context = response.json()["data"]["experience_plan"][
        "route_feasibility_context"
    ]
    assert route_feasibility_context["data_status"] == "not_connected"
    assert route_feasibility_context["daily_route_feasibility"] == []

    coverage_response = client.get(f"/trips/{created_trip_id}/provider-coverage")
    assert coverage_response.status_code == 200
    assert coverage_response.json()["data"]["provider_coverage"]["routes"] == "not_connected"


def test_route_feasibility_daily_route_feasibility_is_empty_regardless_of_other_providers(
    client: TestClient, created_trip_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Even when every other provider (weather/holidays/currency) is
    # connected, route feasibility must stay empty/not_connected -- it is a
    # data-model foundation only, with no calculation logic yet.
    monkeypatch.setattr(provider_gateway, "weather", _ConnectedTestWeatherProvider())
    monkeypatch.setattr(provider_gateway, "holiday", _ConnectedTestHolidayProvider())
    monkeypatch.setattr(provider_gateway, "currency", _ConnectedTestCurrencyProvider())

    generate_response = client.post(f"/trips/{created_trip_id}/generate")
    assert generate_response.status_code == 200

    response = client.get(f"/trips/{created_trip_id}/experience-plan")
    assert response.status_code == 200
    route_feasibility_context = response.json()["data"]["experience_plan"][
        "route_feasibility_context"
    ]
    assert route_feasibility_context["data_status"] == "not_connected"
    assert route_feasibility_context["daily_route_feasibility"] == []


def test_route_feasibility_readiness_checklist_stays_missing_data(
    client: TestClient, generated_trip_id: str
) -> None:
    response = client.get(f"/trips/{generated_trip_id}/experience-plan")
    assert response.status_code == 200
    readiness_checklist = response.json()["data"]["experience_plan"]["readiness_checklist"]
    items_by_label = _checklist_items_by_label(readiness_checklist)

    # Adding the route_feasibility_context data model must not flip these
    # to "checked" or "needs_review" -- no route provider is connected.
    assert items_by_label["Route times checked"]["status"] == "missing_data"
    assert items_by_label["Walking feasibility checked"]["status"] == "missing_data"


def test_route_feasibility_context_creates_no_fake_values(
    client: TestClient, generated_trip_id: str
) -> None:
    response = client.get(f"/trips/{generated_trip_id}/experience-plan")
    assert response.status_code == 200
    experience_plan = response.json()["data"]["experience_plan"]
    route_feasibility_context = experience_plan["route_feasibility_context"]

    assert route_feasibility_context["daily_route_feasibility"] == []

    # No fake route time, distance, travel duration, walking estimate, or
    # feasibility score is created anywhere in route_feasibility_context --
    # since daily_route_feasibility is always empty, no RouteSegment field
    # ever gets serialized at all. (Scoped to route_feasibility_context
    # itself, not the whole experience_plan, since DailyPlan already has
    # its own pre-existing, always-null estimated_travel_time_minutes/
    # estimated_walking_km fields unrelated to this data model.)
    serialized = json.dumps(route_feasibility_context).lower()
    for forbidden_field in (
        "distance_meters",
        "duration_minutes",
        "feasibility_score",
        "travel_time",
        "walking_time",
        "driving_time",
        "transit_time",
    ):
        assert forbidden_field not in serialized
