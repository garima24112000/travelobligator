from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.models.common import DataStatus, GeoPoint, ProviderStatus
from app.models.providers import NormalizedPlace, ProviderResponse
from app.providers.base import PlacesProvider, unavailable_response
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
