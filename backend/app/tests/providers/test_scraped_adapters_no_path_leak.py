from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.core.config import Settings
from app.models.accommodation import AccommodationSearchRequest, AccommodationSearchStatus
from app.models.flight import FlightSearchRequest, FlightSearchStatus
from app.models.hotel_ratings import HotelRatingsRequest, HotelRatingsStatus
from app.providers.accommodation import ScrapedAccommodationProvider
from app.providers.accommodation import scraped_adapter as accommodation_module
from app.providers.flights import scraped_adapter as flight_module
from app.providers.flights.scraped_adapter import ScrapedLocalFlightProvider
from app.providers.hotel_ratings import scraped_adapter as hotel_ratings_module
from app.providers.hotel_ratings.scraped_adapter import ScrapedLocalHotelRatingsProvider

# Step 190C (docs/14_backend_architecture.md section 134): regression
# guard against the exact bug Section 190B's real browser screenshots
# surfaced -- a real, absolute local machine filesystem path (e.g.
# "/Users/apple/Project/travelobligator/backend/.data/manual_scrapes/
# accommodations.html") appearing inside a frontend-visible provider
# `message`/`warnings` field whenever a configured local manual-scrape
# file doesn't exist. Not a secret (no credential/token/session value),
# but unpolished and unnecessary to expose -- the safe `source_id` label
# already says everything a user or portfolio viewer needs. These tests
# use real pytest `tmp_path` locations (genuinely absolute paths, same
# shape as a real machine's) so "the path never appears" is a real,
# specific assertion, not a vacuous one.


def _accommodation_request() -> AccommodationSearchRequest:
    return AccommodationSearchRequest(
        destination="Testville, Testland",
        check_in_date=date(2026, 10, 10),
        check_out_date=date(2026, 10, 14),
        adults=2,
        rooms=1,
    )


def _flight_request() -> FlightSearchRequest:
    return FlightSearchRequest(
        origin="JFK", destination="LIS", departure_date=date(2026, 10, 10)
    )


def _hotel_ratings_request() -> HotelRatingsRequest:
    return HotelRatingsRequest(offer_id="offer_0", property_name="Test Hotel")


def _all_result_text(*, message: str | None, warnings: list[str]) -> str:
    return " ".join([message or "", *warnings])


# ---------------------------------------------------------------------------
# Single-source (the legacy/default one-file-only configuration)
# ---------------------------------------------------------------------------


def test_accommodation_single_source_missing_file_leaks_no_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing_path = tmp_path / "does_not_exist.html"
    settings = Settings(
        _env_file=None,
        scraping_enabled=True,
        scraped_accommodation_provider_enabled=True,
        scraped_accommodation_html_path=str(missing_path),
        scraped_accommodation_html_path_booking=None,
        scraped_accommodation_html_path_expedia=None,
        scraped_accommodation_html_path_hotelbeds=None,
        scraped_accommodation_html_path_hostelworld=None,
        scraped_accommodation_html_path_vrbo=None,
        scraped_accommodation_html_path_airbnb=None,
    )
    monkeypatch.setattr(accommodation_module, "get_settings", lambda: settings)

    result = ScrapedAccommodationProvider().search_accommodations(_accommodation_request())

    assert result.status == AccommodationSearchStatus.UNAVAILABLE
    assert result.offers == []
    text = _all_result_text(message=result.message, warnings=result.warnings)
    assert str(missing_path) not in text
    assert str(tmp_path) not in text
    assert "does_not_exist.html" not in text
    assert "no local manual scrape file found" in text.lower()


def test_flight_single_source_missing_file_leaks_no_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing_path = tmp_path / "does_not_exist.html"
    settings = Settings(
        _env_file=None,
        scraping_enabled=True,
        scraped_flight_provider_enabled=True,
        scraped_flight_html_path=str(missing_path),
        scraped_flight_html_path_skyscanner=None,
        scraped_flight_html_path_google_flights=None,
        scraped_flight_html_path_kiwi_manual=None,
    )
    monkeypatch.setattr(flight_module, "get_settings", lambda: settings)

    result = ScrapedLocalFlightProvider().search_flights(_flight_request())

    assert result.status == FlightSearchStatus.UNAVAILABLE
    assert result.offers == []
    text = _all_result_text(message=result.message, warnings=result.warnings)
    assert str(missing_path) not in text
    assert str(tmp_path) not in text
    assert "does_not_exist.html" not in text
    assert "was not found" in text.lower()


def test_hotel_ratings_single_source_missing_file_leaks_no_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing_path = tmp_path / "does_not_exist.html"
    settings = Settings(
        _env_file=None,
        scraping_enabled=True,
        scraped_hotel_ratings_provider_enabled=True,
        scraped_hotel_ratings_cache_enabled=False,
        scraped_hotel_ratings_html_path=str(missing_path),
        scraped_hotel_ratings_html_path_tripadvisor=None,
        scraped_hotel_ratings_html_path_google_places_ratings=None,
    )
    monkeypatch.setattr(hotel_ratings_module, "get_settings", lambda: settings)

    result = ScrapedLocalHotelRatingsProvider().get_ratings([_hotel_ratings_request()])

    assert result.status == HotelRatingsStatus.UNAVAILABLE
    assert result.items == []
    text = _all_result_text(message=result.message, warnings=result.warnings)
    assert str(missing_path) not in text
    assert str(tmp_path) not in text
    assert "does_not_exist.html" not in text
    assert "was not found" in text.lower()


# ---------------------------------------------------------------------------
# Multi-source (every configured brand slot missing at once)
# ---------------------------------------------------------------------------


def test_accommodation_multi_source_all_missing_leaks_no_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = Settings(
        _env_file=None,
        scraping_enabled=True,
        scraped_accommodation_provider_enabled=True,
        scraped_accommodation_html_path=str(tmp_path / "generic.html"),
        scraped_accommodation_html_path_booking=str(tmp_path / "booking.html"),
        scraped_accommodation_html_path_expedia=str(tmp_path / "expedia.html"),
        scraped_accommodation_html_path_hotelbeds=str(tmp_path / "hotelbeds.html"),
        scraped_accommodation_html_path_hostelworld=str(tmp_path / "hostelworld.html"),
        scraped_accommodation_html_path_vrbo=str(tmp_path / "vrbo.html"),
        scraped_accommodation_html_path_airbnb=str(tmp_path / "airbnb.html"),
    )
    monkeypatch.setattr(accommodation_module, "get_settings", lambda: settings)

    result = ScrapedAccommodationProvider().search_accommodations(_accommodation_request())

    assert result.status == AccommodationSearchStatus.UNAVAILABLE
    assert result.offers == []
    text = _all_result_text(message=result.message, warnings=result.warnings)
    assert str(tmp_path) not in text
    assert "booking.html" not in text
    assert "expedia.html" not in text
    # Safe source_id labels are still present -- this is not a claim that
    # nothing useful is shown, only that the real filesystem path isn't.
    assert "booking" in text
    assert "expedia" in text


def test_flight_multi_source_all_missing_leaks_no_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = Settings(
        _env_file=None,
        scraping_enabled=True,
        scraped_flight_provider_enabled=True,
        scraped_flight_html_path=str(tmp_path / "generic.html"),
        scraped_flight_html_path_skyscanner=str(tmp_path / "skyscanner.html"),
        scraped_flight_html_path_google_flights=str(tmp_path / "google_flights.html"),
        scraped_flight_html_path_kiwi_manual=str(tmp_path / "kiwi_manual.html"),
    )
    monkeypatch.setattr(flight_module, "get_settings", lambda: settings)

    result = ScrapedLocalFlightProvider().search_flights(_flight_request())

    assert result.status == FlightSearchStatus.UNAVAILABLE
    assert result.offers == []
    text = _all_result_text(message=result.message, warnings=result.warnings)
    assert str(tmp_path) not in text
    assert "skyscanner.html" not in text
    assert "google_flights.html" not in text
    assert "skyscanner" in text
    assert "google_flights" in text


def test_hotel_ratings_multi_source_all_missing_leaks_no_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = Settings(
        _env_file=None,
        scraping_enabled=True,
        scraped_hotel_ratings_provider_enabled=True,
        scraped_hotel_ratings_cache_enabled=False,
        scraped_hotel_ratings_html_path=str(tmp_path / "generic.html"),
        scraped_hotel_ratings_html_path_tripadvisor=str(tmp_path / "tripadvisor.html"),
        scraped_hotel_ratings_html_path_google_places_ratings=str(
            tmp_path / "google_places_ratings.html"
        ),
    )
    monkeypatch.setattr(hotel_ratings_module, "get_settings", lambda: settings)

    result = ScrapedLocalHotelRatingsProvider().get_ratings([_hotel_ratings_request()])

    assert result.status == HotelRatingsStatus.UNAVAILABLE
    assert result.items == []
    text = _all_result_text(message=result.message, warnings=result.warnings)
    assert str(tmp_path) not in text
    assert "tripadvisor.html" not in text
    assert "google_places_ratings.html" not in text
    assert "tripadvisor" in text
    assert "google_places_ratings" in text


# ---------------------------------------------------------------------------
# Availability logic is unchanged -- a real file still produces SUCCESS.
# ---------------------------------------------------------------------------


def test_accommodation_still_succeeds_with_a_real_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = tmp_path / "generic.html"
    html_path.write_text(
        '<html><body><div class="property-card" data-property-id="p-1">'
        '<h2 class="property-name">TEST_ONLY_PROPERTY</h2></div></body></html>',
        encoding="utf-8",
    )
    settings = Settings(
        _env_file=None,
        scraping_enabled=True,
        scraped_accommodation_provider_enabled=True,
        scraped_accommodation_html_path=str(html_path),
        scraped_accommodation_html_path_booking=None,
        scraped_accommodation_html_path_expedia=None,
        scraped_accommodation_html_path_hotelbeds=None,
        scraped_accommodation_html_path_hostelworld=None,
        scraped_accommodation_html_path_vrbo=None,
        scraped_accommodation_html_path_airbnb=None,
    )
    monkeypatch.setattr(accommodation_module, "get_settings", lambda: settings)

    result = ScrapedAccommodationProvider().search_accommodations(_accommodation_request())

    assert result.status == AccommodationSearchStatus.SUCCESS
    assert len(result.offers) == 1
    assert result.offers[0].property_name == "TEST_ONLY_PROPERTY"
