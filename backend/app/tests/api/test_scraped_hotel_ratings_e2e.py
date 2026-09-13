from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.models.accommodation import (
    AccommodationOffer,
    AccommodationSearchRequest,
    AccommodationSearchResult,
    AccommodationSearchStatus,
)
from app.models.common import DataStatus
from app.providers.accommodation.base import AccommodationInventoryProvider
from app.providers.gateway import provider_gateway
from app.providers.hotel_ratings import scraped_adapter as hotel_ratings_scraped_adapter_module
from app.providers.hotel_ratings.scraped_adapter import ScrapedLocalHotelRatingsProvider
from app.services import hotel_rating_enrichment_service as enrichment_module
from app.tests.conftest import create_trip_payload

# Step 185E: full end-to-end integration tests proving the real
# ScrapedLocalHotelRatingsProvider works through the real HTTP API --
# generation, provider coverage, and serialization -- while still only
# ever reading a manually-supplied local HTML fixture. Never a real
# website, never a network call.
#
# Hotel ratings is not part of `ProviderGateway` (see
# test_hotel_ratings_provider.py's `test_provider_gateway_does_not_
# reference_hotel_ratings`) -- it is reached through the shared, already-
# constructed `hotel_rating_enrichment_service` singleton
# (`app.services.hotel_rating_enrichment_service`), which every
# `AccommodationInventoryService` instance (including the real ones
# `PlanningOrchestrator`/the LangGraph node build at import time) shares
# by reference. So this file patches that singleton's own `_provider`
# instance attribute directly -- mutating the real, already-constructed
# object every code path already holds a reference to -- rather than
# trying to swap out the singleton itself (which would arrive too late).


class _FakeAccommodationInventoryProvider(AccommodationInventoryProvider):
    provider_name = "fake_accommodation_inventory_provider"

    def __init__(self, offers: list[AccommodationOffer]) -> None:
        self._offers = offers

    def search_accommodations(
        self, request: AccommodationSearchRequest
    ) -> AccommodationSearchResult:
        return AccommodationSearchResult(
            provider=self.provider_name,
            status=AccommodationSearchStatus.SUCCESS,
            offers=self._offers,
        )


def _offer(property_name: str, property_id: str) -> AccommodationOffer:
    return AccommodationOffer(
        provider="fake_accommodation_inventory_provider",
        provider_property_id=property_id,
        property_name=property_name,
        data_status=DataStatus.LIVE,
    )


def _write_ratings_html(tmp_path: Path, property_name: str, rating: str, review_count: str) -> str:
    html_file = tmp_path / "scraped_hotel_ratings_fixture.html"
    html_file.write_text(
        f"""
<html><body>
<div class="hotel-rating">
  <span class="property-name">{property_name}</span>
  <span class="rating-value">{rating}</span>
  <span class="review-count">{review_count}</span>
</div>
</body></html>
""",
        encoding="utf-8",
    )
    return str(html_file)


def _enable_real_scraped_hotel_ratings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, html_path: str
) -> None:
    settings = Settings(
        _env_file=None,
        scraping_enabled=True,
        scraped_hotel_ratings_provider_enabled=True,
        scraped_hotel_ratings_html_path=html_path,
        scraped_hotel_ratings_html_path_tripadvisor=str(
            tmp_path / "does-not-exist-tripadvisor.html"
        ),
        scraped_hotel_ratings_html_path_google_places_ratings=str(
            tmp_path / "does-not-exist-google-places-ratings.html"
        ),
        hotel_ratings_provider="scraped_local",
    )
    monkeypatch.setattr(hotel_ratings_scraped_adapter_module, "get_settings", lambda: settings)
    monkeypatch.setattr(
        enrichment_module.hotel_rating_enrichment_service,
        "_provider",
        ScrapedLocalHotelRatingsProvider(),
    )


def _generate_trip(client: TestClient) -> str:
    create_response = client.post("/trips", json=create_trip_payload())
    assert create_response.status_code == 201
    trip_id = create_response.json()["data"]["trip_id"]

    generate_response = client.post(f"/trips/{trip_id}/generate")
    assert generate_response.status_code == 200
    return trip_id


# ---------------------------------------------------------------------------
# Real local ratings file, matching accommodation offer -> rating attaches.
# ---------------------------------------------------------------------------


def test_matching_local_rating_attaches_to_the_correct_offer(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, tmp_path: Path
) -> None:
    html_path = _write_ratings_html(tmp_path, "TEST_ONLY_MATCHING_HOTEL", "4.5", "200")
    _enable_real_scraped_hotel_ratings(monkeypatch, tmp_path, html_path)
    monkeypatch.setattr(
        provider_gateway,
        "accommodation_inventory",
        _FakeAccommodationInventoryProvider(
            [_offer("TEST_ONLY_MATCHING_HOTEL", "prop-1")]
        ),
    )

    trip_id = _generate_trip(client)
    response = client.get(f"/trips/{trip_id}")
    report = response.json()["data"]["planning_state"]["accommodation_inventory_report"]

    assert report["status"] == "success"
    assert len(report["offers"]) == 1
    rating_details = report["offers"][0]["rating_details"]
    assert rating_details is not None
    assert rating_details["value"] == 4.5
    assert rating_details["review_count"] == 200
    assert rating_details["data_status"] == "scraped_public_page"
    assert report["hotel_ratings_status"] == "success"
    assert report["hotel_ratings_enriched_offer_count"] == 1


def test_unmatched_local_rating_does_not_attach_to_the_wrong_offer(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, tmp_path: Path
) -> None:
    html_path = _write_ratings_html(tmp_path, "TEST_ONLY_UNRELATED_HOTEL", "4.5", "200")
    _enable_real_scraped_hotel_ratings(monkeypatch, tmp_path, html_path)
    monkeypatch.setattr(
        provider_gateway,
        "accommodation_inventory",
        _FakeAccommodationInventoryProvider(
            [_offer("TEST_ONLY_MATCHING_HOTEL", "prop-1")]
        ),
    )

    trip_id = _generate_trip(client)
    response = client.get(f"/trips/{trip_id}")
    report = response.json()["data"]["planning_state"]["accommodation_inventory_report"]

    assert report["offers"][0]["rating_details"] is None
    assert report["hotel_ratings_enriched_offer_count"] == 0


def test_missing_ratings_file_leaves_accommodation_offers_unchanged(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, tmp_path: Path
) -> None:
    missing_path = str(tmp_path / "does-not-exist.html")
    _enable_real_scraped_hotel_ratings(monkeypatch, tmp_path, missing_path)
    monkeypatch.setattr(
        provider_gateway,
        "accommodation_inventory",
        _FakeAccommodationInventoryProvider(
            [_offer("TEST_ONLY_MATCHING_HOTEL", "prop-1")]
        ),
    )

    trip_id = _generate_trip(client)
    response = client.get(f"/trips/{trip_id}")
    report = response.json()["data"]["planning_state"]["accommodation_inventory_report"]

    assert report["status"] == "success"
    assert len(report["offers"]) == 1
    assert report["offers"][0]["rating_details"] is None
    assert report["hotel_ratings_status"] == "unavailable"


def test_provider_coverage_reflects_hotel_ratings_status_honestly(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, tmp_path: Path
) -> None:
    html_path = _write_ratings_html(tmp_path, "TEST_ONLY_MATCHING_HOTEL", "4.5", "200")
    _enable_real_scraped_hotel_ratings(monkeypatch, tmp_path, html_path)
    monkeypatch.setattr(
        provider_gateway,
        "accommodation_inventory",
        _FakeAccommodationInventoryProvider(
            [_offer("TEST_ONLY_MATCHING_HOTEL", "prop-1")]
        ),
    )

    trip_id = _generate_trip(client)
    response = client.get(f"/trips/{trip_id}/provider-coverage")
    coverage = response.json()["data"]["provider_coverage"]

    assert coverage["hotel_ratings"] == "success"


def test_no_fake_rating_or_review_count_ever_appears_in_api_response(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, tmp_path: Path
) -> None:
    """The local fixture supplies no `review-count` at all -- the API
    response must honestly carry `review_count: null`, never an invented
    number."""
    html_file = tmp_path / "scraped_hotel_ratings_fixture.html"
    html_file.write_text(
        """
<html><body>
<div class="hotel-rating">
  <span class="property-name">TEST_ONLY_MATCHING_HOTEL</span>
  <span class="rating-value">3.7</span>
</div>
</body></html>
""",
        encoding="utf-8",
    )
    _enable_real_scraped_hotel_ratings(monkeypatch, tmp_path, str(html_file))
    monkeypatch.setattr(
        provider_gateway,
        "accommodation_inventory",
        _FakeAccommodationInventoryProvider(
            [_offer("TEST_ONLY_MATCHING_HOTEL", "prop-1")]
        ),
    )

    trip_id = _generate_trip(client)
    response = client.get(f"/trips/{trip_id}")
    rating_details = response.json()["data"]["planning_state"]["accommodation_inventory_report"][
        "offers"
    ][0]["rating_details"]

    assert rating_details["value"] == 3.7
    assert rating_details["review_count"] is None
