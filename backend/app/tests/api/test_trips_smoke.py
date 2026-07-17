from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.models.common import DataStatus, GeoPoint, ProviderStatus
from app.models.providers import NormalizedPlace, ProviderResponse
from app.providers.base import PlacesProvider, failed_response, unavailable_response
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
    # was never called. The only other entries come from the routes lookup
    # and the separate (still-unconnected) booking-capable accommodation
    # provider, which StayTransportService also records under "accommodations".
    assert all(
        entry["provider_name"] in {"openstreetmap_places", "routes_provider", "accommodation_provider"}
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

    # The captured budget range must be named exactly as provided, with no
    # invented/estimated cost figures layered on top of it.
    assert "1500" in budget_warning["message"]
    assert "2500" in budget_warning["message"]
    # The report must clearly say cost validation isn't implemented and the
    # budget fit is unconfirmed, rather than claiming the plan fits.
    assert "not implemented yet" in budget_warning["message"]
    assert "does not confirm" in budget_warning["message"]


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
