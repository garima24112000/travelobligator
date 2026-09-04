from __future__ import annotations

import json

from fastapi.testclient import TestClient

# Step 167D: end-to-end API smoke tests for the accommodation inventory
# report. Uses the standard `client`/`generated_trip_id` fixtures from
# conftest.py -- the default accommodation provider (Step 168F:
# `ScrapedAccommodationProvider`/`accommodation_provider="scraped_local"`,
# which with no local HTML file present at its default path honestly
# reports `unavailable`) -- and the deterministic test places provider
# (never a real network call).


def test_generate_succeeds_with_default_accommodation_provider(
    client: TestClient, generated_trip_id: str
) -> None:
    """Required test 1: default generation still succeeds."""
    response = client.get(f"/trips/{generated_trip_id}")
    assert response.status_code == 200


def test_accommodation_inventory_report_is_unavailable_with_empty_offers(
    client: TestClient, generated_trip_id: str
) -> None:
    """Required tests 2/3: default generation (Step 168F: scraped_local
    with no local HTML file present) stores an honest unavailable status
    with empty offers, and no factual field is ever fabricated (there is
    no offer to carry one)."""
    response = client.get(f"/trips/{generated_trip_id}")
    assert response.status_code == 200
    planning_state = response.json()["data"]["planning_state"]

    report = planning_state["accommodation_inventory_report"]
    assert report is not None
    assert report["status"] == "unavailable"
    assert report["offers"] == []


def test_provider_coverage_hotel_prices_is_unavailable(
    client: TestClient, generated_trip_id: str
) -> None:
    """Required test 7: ProviderCoverage reports the accommodation
    inventory result honestly as unavailable by default (no local HTML
    file present at the default scraped_local path)."""
    response = client.get(f"/trips/{generated_trip_id}/provider-coverage")
    assert response.status_code == 200
    provider_coverage = response.json()["data"]["provider_coverage"]
    assert provider_coverage["hotel_prices"] == "unavailable"
    # The pre-existing OSM-backed accommodations coverage field is
    # completely unaffected by this step.
    assert provider_coverage["accommodations"] in {"not_connected", "open_poi_available"}


def test_validation_report_includes_accommodation_inventory_warning(
    client: TestClient, generated_trip_id: str
) -> None:
    """Required tests 9/10/11: PlanValidator surfaces a non-blocking
    warning about unavailable lodging inventory, never a critical issue,
    and never claims lodging was checked/verified."""
    response = client.get(f"/trips/{generated_trip_id}/validation-report")
    assert response.status_code == 200
    validation_report = response.json()["data"]["validation_report"]

    accommodation_warnings = [
        warning
        for warning in validation_report["warnings"]
        if warning["category"] == "accommodation_inventory"
    ]
    assert len(accommodation_warnings) == 1
    message_lower = accommodation_warnings[0]["message"].lower()
    assert "no bookable lodging offers were available" in message_lower
    assert "no price, availability, rating, or booking" in message_lower

    assert not any(
        issue["category"] == "accommodation_inventory"
        for issue in validation_report["critical_issues"]
    )
    # Pre-existing feasibility warning behavior (Step 165E) is unaffected --
    # main_review_reason still surfaces it first (see test_trips_smoke.py).
    assert validation_report["readiness_status"] == "needs_review"


def test_accommodation_inventory_report_never_contains_osm_poi_data(
    client: TestClient, generated_trip_id: str
) -> None:
    """Required test 8: no OSM accommodation-like POI (from the
    deterministic test places provider fixture, `conftest.py`'s
    `DeterministicTestPlacesProvider`) is ever converted into bookable
    lodging inventory. The full-stack report stays not_connected/empty
    regardless of what OSM POIs the destination context stage found."""
    response = client.get(f"/trips/{generated_trip_id}")
    assert response.status_code == 200
    planning_state = response.json()["data"]["planning_state"]

    report = planning_state["accommodation_inventory_report"]
    assert report["offers"] == []
    # The deterministic test places provider's fixture accommodation POI
    # name never leaks into the accommodation inventory report.
    assert "Test Fixture Accommodation One" not in json.dumps(report)


def test_regeneration_still_refused_after_accommodation_inventory_report(
    client: TestClient, generated_trip_id: str
) -> None:
    """Required test 15: regeneration refusal behavior is unchanged."""
    response = client.post(f"/trips/{generated_trip_id}/regenerate")
    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "REGENERATION_NOT_AVAILABLE"
