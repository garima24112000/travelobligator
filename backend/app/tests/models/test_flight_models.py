from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models.common import DataStatus
from app.models.flight import (
    FlightOffer,
    FlightSearchRequest,
    FlightSearchResult,
    FlightSearchStatus,
    FlightSegment,
)
from app.models.scraping import ScrapedDataConfidence, ScrapedDataProvenance

# Model/contract tests for Step 169A's flight inventory foundation. Never
# calls a real network service, never requires any flight provider
# configuration -- these only exercise pydantic validation.


def _request(**overrides: object) -> FlightSearchRequest:
    fields: dict[str, object] = {
        "destination": "LIS",
        "departure_date": date(2026, 10, 10),
    }
    fields.update(overrides)
    return FlightSearchRequest(**fields)


def _segment(**overrides: object) -> FlightSegment:
    fields: dict[str, object] = {
        "data_status": DataStatus.NOT_CONNECTED,
    }
    fields.update(overrides)
    return FlightSegment(**fields)


def _offer(**overrides: object) -> FlightOffer:
    fields: dict[str, object] = {
        "offer_id": "offer_123",
        "provider": "fake_flight_provider",
        "data_status": DataStatus.NOT_CONNECTED,
        "outbound_segments": [_segment()],
    }
    fields.update(overrides)
    return FlightOffer(**fields)


def _scraped_provenance(**overrides: object) -> ScrapedDataProvenance:
    fields: dict[str, object] = {
        "source_id": "example_test_only_flight_search_page",
        "source_name": "Example Test-Only Flight Search Page",
        "confidence": ScrapedDataConfidence.EXPERIMENTAL,
    }
    fields.update(overrides)
    return ScrapedDataProvenance(**fields)


# ---------------------------------------------------------------------------
# 1. FlightSearchRequest validates basic one-way trip.
# ---------------------------------------------------------------------------


def test_search_request_validates_one_way_trip() -> None:
    request = _request(origin="JFK", destination="LIS", departure_date=date(2026, 10, 10))
    assert request.origin == "JFK"
    assert request.destination == "LIS"
    assert request.departure_date == date(2026, 10, 10)
    assert request.return_date is None
    assert request.adults == 1
    assert request.children == 0
    assert request.cabin_class is None
    assert request.currency is None
    assert request.trip_id is None


# ---------------------------------------------------------------------------
# 2. FlightSearchRequest validates round trip.
# ---------------------------------------------------------------------------


def test_search_request_validates_round_trip() -> None:
    request = _request(
        origin="JFK",
        destination="LIS",
        departure_date=date(2026, 10, 10),
        return_date=date(2026, 10, 17),
        adults=2,
        children=1,
        cabin_class="economy",
        currency="EUR",
        trip_id="trip_abc",
    )
    assert request.return_date == date(2026, 10, 17)
    assert request.adults == 2
    assert request.children == 1
    assert request.cabin_class == "economy"
    assert request.currency == "EUR"
    assert request.trip_id == "trip_abc"


# ---------------------------------------------------------------------------
# 3. FlightSearchRequest rejects negative adults.
# ---------------------------------------------------------------------------


def test_search_request_rejects_negative_adults() -> None:
    with pytest.raises(ValidationError):
        _request(adults=-1)


# ---------------------------------------------------------------------------
# 4. FlightSearchRequest rejects negative children.
# ---------------------------------------------------------------------------


def test_search_request_rejects_negative_children() -> None:
    with pytest.raises(ValidationError):
        _request(children=-1)


# ---------------------------------------------------------------------------
# 5. FlightSearchRequest rejects return_date before departure_date.
# ---------------------------------------------------------------------------


def test_search_request_rejects_return_date_before_departure_date() -> None:
    with pytest.raises(ValidationError):
        _request(departure_date=date(2026, 10, 10), return_date=date(2026, 10, 9))


def test_search_request_accepts_return_date_equal_to_departure_date() -> None:
    request = _request(departure_date=date(2026, 10, 10), return_date=date(2026, 10, 10))
    assert request.return_date == date(2026, 10, 10)


def test_search_request_requires_departure_date() -> None:
    with pytest.raises(ValidationError):
        FlightSearchRequest(destination="LIS")


# ---------------------------------------------------------------------------
# 6. FlightSegment allows missing carrier/flight number/times.
# ---------------------------------------------------------------------------


def test_segment_allows_missing_optional_fields() -> None:
    segment = _segment()
    assert segment.origin_airport is None
    assert segment.destination_airport is None
    assert segment.departure_time is None
    assert segment.arrival_time is None
    assert segment.carrier_name is None
    assert segment.carrier_code is None
    assert segment.flight_number is None
    assert segment.duration_minutes is None


# ---------------------------------------------------------------------------
# 7. FlightSegment does not default fake airports, airlines, flight
#    numbers, or times.
# ---------------------------------------------------------------------------


def test_segment_defaults_are_honest_not_fake() -> None:
    segment = _segment()
    assert segment.origin_airport is None
    assert segment.destination_airport is None
    assert segment.carrier_name is None
    assert segment.carrier_code is None
    assert segment.flight_number is None
    assert segment.departure_time is None
    assert segment.arrival_time is None


def test_segment_accepts_provider_backed_fields() -> None:
    segment = _segment(
        origin_airport="JFK",
        destination_airport="LIS",
        departure_time=datetime(2026, 10, 10, 20, 30, tzinfo=timezone.utc),
        arrival_time=datetime(2026, 10, 11, 8, 45, tzinfo=timezone.utc),
        carrier_name="Fake Airline",
        carrier_code="FA",
        flight_number="FA123",
        duration_minutes=435,
        data_status=DataStatus.LIVE,
    )
    assert segment.origin_airport == "JFK"
    assert segment.carrier_code == "FA"
    assert segment.duration_minutes == 435


def test_segment_rejects_negative_duration() -> None:
    with pytest.raises(ValidationError):
        _segment(duration_minutes=-1)


# ---------------------------------------------------------------------------
# 8. FlightOffer allows missing price.
# ---------------------------------------------------------------------------


def test_offer_allows_missing_price() -> None:
    offer = _offer()
    assert offer.total_price_amount is None
    assert offer.currency is None


# ---------------------------------------------------------------------------
# 9. FlightOffer allows missing booking_url.
# ---------------------------------------------------------------------------


def test_offer_allows_missing_booking_url() -> None:
    offer = _offer()
    assert offer.booking_url is None


# ---------------------------------------------------------------------------
# 10. FlightOffer does not default fake price, airline, availability,
#     baggage, cancellation, or booking_url.
# ---------------------------------------------------------------------------


def test_offer_defaults_are_honest_not_fake() -> None:
    offer = _offer()
    assert offer.total_price_amount is None
    assert offer.currency is None
    assert offer.availability_status is None
    assert offer.baggage_policy is None
    assert offer.cancellation_policy is None
    assert offer.booking_url is None
    assert offer.return_segments == []
    assert offer.source_name is None
    assert offer.source_url is None


def test_offer_requires_offer_id() -> None:
    with pytest.raises(ValidationError):
        _offer(offer_id="")


def test_offer_rejects_negative_price() -> None:
    with pytest.raises(ValidationError):
        _offer(total_price_amount=Decimal("-1.00"))


def test_offer_accepts_provider_backed_fields() -> None:
    offer = _offer(
        data_status=DataStatus.LIVE,
        total_price_amount=Decimal("452.10"),
        currency="USD",
        booking_url="https://example.test/book/offer_123",
        availability_status="available",
        baggage_policy="1 checked bag included",
        cancellation_policy="Non-refundable",
    )
    assert offer.total_price_amount == Decimal("452.10")
    assert offer.currency == "USD"
    assert offer.booking_url == "https://example.test/book/offer_123"
    assert offer.availability_status == "available"


# ---------------------------------------------------------------------------
# 11. FlightOffer supports scraped_provenance only with
#     data_status=scraped_public_page.
# ---------------------------------------------------------------------------


def test_offer_scraped_provenance_defaults_to_none() -> None:
    offer = _offer()
    assert offer.scraped_provenance is None


def test_offer_accepts_scraped_provenance_with_matching_data_status() -> None:
    offer = _offer(
        data_status=DataStatus.SCRAPED_PUBLIC_PAGE,
        scraped_provenance=_scraped_provenance(),
    )
    assert offer.scraped_provenance is not None
    assert offer.scraped_provenance.official_provider is False


# ---------------------------------------------------------------------------
# 12. FlightOffer rejects scraped_provenance with official/available/
#     non-scraped status.
# ---------------------------------------------------------------------------


def test_offer_rejects_scraped_provenance_with_official_looking_data_status() -> None:
    with pytest.raises(ValidationError):
        _offer(data_status=DataStatus.LIVE, scraped_provenance=_scraped_provenance())


@pytest.mark.parametrize(
    "data_status",
    [DataStatus.CACHED, DataStatus.ESTIMATED, DataStatus.USER_PROVIDED, DataStatus.AI_INFERRED],
)
def test_offer_rejects_scraped_provenance_with_other_non_scraped_statuses(
    data_status: DataStatus,
) -> None:
    with pytest.raises(ValidationError):
        _offer(data_status=data_status, scraped_provenance=_scraped_provenance())


# ---------------------------------------------------------------------------
# 13. FlightSearchResult can represent not_connected/unavailable with
#     empty offers.
# ---------------------------------------------------------------------------


def test_search_result_not_connected_with_empty_offers() -> None:
    result = FlightSearchResult(
        provider="flight_inventory_provider",
        status=FlightSearchStatus.NOT_CONNECTED,
        offers=[],
        message="Flight inventory provider is not connected.",
        destination="LIS",
        departure_date=date(2026, 10, 10),
    )
    assert result.status == FlightSearchStatus.NOT_CONNECTED
    assert result.offers == []


def test_search_result_unavailable_with_empty_offers() -> None:
    result = FlightSearchResult(
        provider="flight_inventory_provider",
        status=FlightSearchStatus.UNAVAILABLE,
        offers=[],
        message="No flight inventory available for this search.",
        destination="LIS",
        departure_date=date(2026, 10, 10),
    )
    assert result.status == FlightSearchStatus.UNAVAILABLE
    assert result.offers == []


def test_search_result_not_connected_rejects_nonempty_offers() -> None:
    with pytest.raises(ValidationError):
        FlightSearchResult(
            provider="flight_inventory_provider",
            status=FlightSearchStatus.NOT_CONNECTED,
            offers=[_offer()],
            destination="LIS",
            departure_date=date(2026, 10, 10),
        )


def test_search_result_failed_rejects_nonempty_offers() -> None:
    with pytest.raises(ValidationError):
        FlightSearchResult(
            provider="flight_inventory_provider",
            status=FlightSearchStatus.FAILED,
            offers=[_offer()],
            destination="LIS",
            departure_date=date(2026, 10, 10),
        )


# ---------------------------------------------------------------------------
# 14. FlightSearchResult can represent scraped_public_page success with
#     offers.
# ---------------------------------------------------------------------------


def test_search_result_success_supports_scraped_offers() -> None:
    offer = _offer(
        data_status=DataStatus.SCRAPED_PUBLIC_PAGE,
        scraped_provenance=_scraped_provenance(),
    )
    result = FlightSearchResult(
        provider="scraped:example_test_only_flight_search_page",
        status=FlightSearchStatus.SUCCESS,
        offers=[offer],
        destination="LIS",
        departure_date=date(2026, 10, 10),
    )
    assert result.status == FlightSearchStatus.SUCCESS
    assert len(result.offers) == 1
    assert result.offers[0].scraped_provenance is not None
    # Fields the fixture didn't supply stay honestly None -- success never
    # backfills a missing fact.
    assert result.offers[0].total_price_amount is None
    assert result.offers[0].booking_url is None


def test_search_result_success_allows_empty_offers() -> None:
    result = FlightSearchResult(
        provider="fake_flight_provider",
        status=FlightSearchStatus.SUCCESS,
        offers=[],
        message="No flights matched this search.",
        destination="LIS",
        departure_date=date(2026, 10, 10),
    )
    assert result.offers == []


def test_search_result_defaults_are_honest() -> None:
    result = FlightSearchResult(
        provider="flight_inventory_provider",
        status=FlightSearchStatus.NOT_CONNECTED,
        destination="LIS",
        departure_date=date(2026, 10, 10),
    )
    assert result.searched_at is None
    assert result.origin is None
    assert result.return_date is None
    assert result.currency is None
