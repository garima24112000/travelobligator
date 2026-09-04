from __future__ import annotations

import json

from fastapi.testclient import TestClient

# Step 169E: end-to-end API smoke tests for the flight inventory report.
# Uses the standard `client`/`generated_trip_id` fixtures from
# conftest.py -- the default flight provider (`scraped_local`, which with
# no local HTML file present at its default path honestly reports
# `unavailable`) -- and the deterministic test places provider (never a
# real network call).


def test_generate_succeeds_with_default_flight_provider(
    client: TestClient, generated_trip_id: str
) -> None:
    """Required test 6: default generation still succeeds and stores an
    honest flight_inventory_report."""
    response = client.get(f"/trips/{generated_trip_id}")
    assert response.status_code == 200


def test_flight_inventory_report_is_unavailable_with_empty_offers(
    client: TestClient, generated_trip_id: str
) -> None:
    """Required test 6: default generation (scraped_local with no local
    HTML file present) stores an honest unavailable status with empty
    offers, and no factual field is ever fabricated (there is no offer to
    carry one)."""
    response = client.get(f"/trips/{generated_trip_id}")
    assert response.status_code == 200
    planning_state = response.json()["data"]["planning_state"]

    report = planning_state["flight_inventory_report"]
    assert report is not None
    assert report["status"] == "unavailable"
    assert report["offers"] == []


def test_provider_coverage_flights_is_unavailable(
    client: TestClient, generated_trip_id: str
) -> None:
    """Required test 13: ProviderCoverage reports the flight inventory
    result honestly as unavailable by default (no local HTML file present
    at the default scraped_local path)."""
    response = client.get(f"/trips/{generated_trip_id}/provider-coverage")
    assert response.status_code == 200
    provider_coverage = response.json()["data"]["provider_coverage"]
    assert provider_coverage["flights"] == "unavailable"


def test_validation_report_includes_flight_inventory_warning(
    client: TestClient, generated_trip_id: str
) -> None:
    """Required test 15: PlanValidator surfaces a non-blocking warning
    about unavailable flight inventory, never a critical issue, and never
    claims flights were checked/verified."""
    response = client.get(f"/trips/{generated_trip_id}/validation-report")
    assert response.status_code == 200
    validation_report = response.json()["data"]["validation_report"]

    flight_warnings = [
        warning
        for warning in validation_report["warnings"]
        if warning["category"] == "flight_inventory"
    ]
    assert len(flight_warnings) == 1
    message_lower = flight_warnings[0]["message"].lower()
    assert "no flight offers were available" in message_lower
    assert "no airline, flight number, schedule, price, availability" in message_lower

    assert not any(
        issue["category"] == "flight_inventory" for issue in validation_report["critical_issues"]
    )


def test_flight_inventory_report_never_contains_osm_poi_data(
    client: TestClient, generated_trip_id: str
) -> None:
    """No OSM accommodation-like POI (from the deterministic test places
    provider fixture) is ever converted into flight inventory. The
    full-stack report stays empty regardless of what OSM POIs the
    destination context stage found."""
    response = client.get(f"/trips/{generated_trip_id}")
    assert response.status_code == 200
    planning_state = response.json()["data"]["planning_state"]

    report = planning_state["flight_inventory_report"]
    assert report["offers"] == []
    assert "Test Fixture Accommodation One" not in json.dumps(report)


def test_regeneration_still_refused_after_flight_inventory_report(
    client: TestClient, generated_trip_id: str
) -> None:
    """Required test 19: regeneration refusal behavior is unchanged."""
    response = client.post(f"/trips/{generated_trip_id}/regenerate")
    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "REGENERATION_NOT_AVAILABLE"
