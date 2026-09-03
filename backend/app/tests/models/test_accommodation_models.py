from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from app.models.accommodation import (
    AccommodationAvailabilityStatus,
    AccommodationOffer,
    AccommodationSearchRequest,
    AccommodationSearchResult,
    AccommodationSearchStatus,
)
from app.models.common import DataStatus

# Model/contract tests for Step 167A's accommodation inventory foundation.
# Never calls a real network service, never requires any lodging provider
# configuration -- these only exercise pydantic validation.


def _request(**overrides: object) -> AccommodationSearchRequest:
    fields: dict[str, object] = {
        "destination": "Lisbon, Portugal",
        "check_in_date": date(2026, 10, 10),
        "check_out_date": date(2026, 10, 14),
        "adults": 2,
        "rooms": 1,
    }
    fields.update(overrides)
    return AccommodationSearchRequest(**fields)


def _offer(**overrides: object) -> AccommodationOffer:
    fields: dict[str, object] = {
        "provider": "fake_lodging_provider",
        "provider_property_id": "prop_123",
        "property_name": "Test Property",
        "data_status": DataStatus.NOT_CONNECTED,
    }
    fields.update(overrides)
    return AccommodationOffer(**fields)


# ---------------------------------------------------------------------------
# 1. AccommodationSearchRequest accepts a valid search request.
# ---------------------------------------------------------------------------


def test_search_request_accepts_valid_request() -> None:
    request = _request()
    assert request.destination == "Lisbon, Portugal"
    assert request.adults == 2
    assert request.rooms == 1
    assert request.children is None
    assert request.currency is None
    assert request.latitude is None
    assert request.longitude is None


def test_search_request_accepts_optional_fields() -> None:
    request = _request(
        children=1,
        currency="EUR",
        latitude=38.7223,
        longitude=-9.1393,
        radius_meters=2000.0,
    )
    assert request.children == 1
    assert request.currency == "EUR"
    assert request.latitude == pytest.approx(38.7223)
    assert request.longitude == pytest.approx(-9.1393)
    assert request.radius_meters == pytest.approx(2000.0)


# ---------------------------------------------------------------------------
# 2. AccommodationSearchRequest rejects check_out_date <= check_in_date.
# ---------------------------------------------------------------------------


def test_search_request_rejects_check_out_equal_to_check_in() -> None:
    with pytest.raises(ValidationError):
        _request(check_in_date=date(2026, 10, 10), check_out_date=date(2026, 10, 10))


def test_search_request_rejects_check_out_before_check_in() -> None:
    with pytest.raises(ValidationError):
        _request(check_in_date=date(2026, 10, 10), check_out_date=date(2026, 10, 9))


# ---------------------------------------------------------------------------
# 3. AccommodationSearchRequest rejects non-positive adults/rooms.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("invalid_adults", [0, -1])
def test_search_request_rejects_non_positive_adults(invalid_adults: int) -> None:
    with pytest.raises(ValidationError):
        _request(adults=invalid_adults)


@pytest.mark.parametrize("invalid_rooms", [0, -1])
def test_search_request_rejects_non_positive_rooms(invalid_rooms: int) -> None:
    with pytest.raises(ValidationError):
        _request(rooms=invalid_rooms)


def test_search_request_rejects_negative_children() -> None:
    with pytest.raises(ValidationError):
        _request(children=-1)


# ---------------------------------------------------------------------------
# 4. AccommodationSearchRequest rejects invalid coordinates if present.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field_name,invalid_value",
    [
        ("latitude", 91.0),
        ("latitude", -91.0),
        ("longitude", 181.0),
        ("longitude", -181.0),
    ],
)
def test_search_request_rejects_invalid_coordinates(field_name: str, invalid_value: float) -> None:
    with pytest.raises(ValidationError):
        _request(**{field_name: invalid_value})


def test_search_request_rejects_non_positive_radius() -> None:
    with pytest.raises(ValidationError):
        _request(radius_meters=0)


# ---------------------------------------------------------------------------
# 5. AccommodationOffer allows provider-backed missing optional fields as
#    None.
# ---------------------------------------------------------------------------


def test_offer_allows_missing_optional_fields_as_none() -> None:
    offer = _offer()
    assert offer.latitude is None
    assert offer.longitude is None
    assert offer.address is None
    assert offer.nightly_price_amount is None
    assert offer.total_price_amount is None
    assert offer.currency is None
    assert offer.booking_url is None
    assert offer.rating is None
    assert offer.cancellation_policy is None
    assert offer.amenities == []
    assert offer.source_name is None
    assert offer.source_url is None


# ---------------------------------------------------------------------------
# 6. AccommodationOffer rejects negative price fields.
# ---------------------------------------------------------------------------


def test_offer_rejects_negative_nightly_price() -> None:
    with pytest.raises(ValidationError):
        _offer(nightly_price_amount=-1.0)


def test_offer_rejects_negative_total_price() -> None:
    with pytest.raises(ValidationError):
        _offer(total_price_amount=-1.0)


def test_offer_rejects_negative_rating() -> None:
    with pytest.raises(ValidationError):
        _offer(rating=-0.1)


def test_offer_rejects_invalid_coordinates() -> None:
    with pytest.raises(ValidationError):
        _offer(latitude=200.0)


# ---------------------------------------------------------------------------
# 7. AccommodationOffer does not default rating/price/availability/
#    booking_url to fake values.
# ---------------------------------------------------------------------------


def test_offer_defaults_are_honest_not_fake() -> None:
    offer = _offer()
    assert offer.rating is None
    assert offer.nightly_price_amount is None
    assert offer.total_price_amount is None
    assert offer.booking_url is None
    assert offer.availability_status == AccommodationAvailabilityStatus.UNKNOWN


# ---------------------------------------------------------------------------
# 8. AccommodationSearchResult can represent not_connected with empty
#    offers.
# ---------------------------------------------------------------------------


def test_search_result_not_connected_with_empty_offers() -> None:
    result = AccommodationSearchResult(
        provider="accommodation_inventory_provider",
        status=AccommodationSearchStatus.NOT_CONNECTED,
        offers=[],
        message="Accommodation inventory provider is not connected.",
    )
    assert result.status == AccommodationSearchStatus.NOT_CONNECTED
    assert result.offers == []


def test_search_result_not_connected_rejects_nonempty_offers() -> None:
    with pytest.raises(ValidationError):
        AccommodationSearchResult(
            provider="accommodation_inventory_provider",
            status=AccommodationSearchStatus.NOT_CONNECTED,
            offers=[_offer()],
        )


# ---------------------------------------------------------------------------
# 9. AccommodationSearchResult can represent unavailable with empty offers.
# ---------------------------------------------------------------------------


def test_search_result_unavailable_with_empty_offers() -> None:
    result = AccommodationSearchResult(
        provider="accommodation_inventory_provider",
        status=AccommodationSearchStatus.UNAVAILABLE,
        offers=[],
        message="No accommodation inventory available for this search.",
    )
    assert result.status == AccommodationSearchStatus.UNAVAILABLE
    assert result.offers == []


def test_search_result_failed_rejects_nonempty_offers() -> None:
    with pytest.raises(ValidationError):
        AccommodationSearchResult(
            provider="accommodation_inventory_provider",
            status=AccommodationSearchStatus.FAILED,
            offers=[_offer()],
        )


# ---------------------------------------------------------------------------
# 10. AccommodationSearchResult success supports provider-backed offers
#     without inventing missing fields.
# ---------------------------------------------------------------------------


def test_search_result_success_supports_provider_backed_offers() -> None:
    offer = _offer(
        provider="fake_lodging_provider",
        data_status=DataStatus.LIVE,
        property_name="Real Property",
        nightly_price_amount=120.0,
        currency="EUR",
        availability_status=AccommodationAvailabilityStatus.AVAILABLE,
    )
    result = AccommodationSearchResult(
        provider="fake_lodging_provider",
        status=AccommodationSearchStatus.SUCCESS,
        offers=[offer],
    )
    assert result.status == AccommodationSearchStatus.SUCCESS
    assert len(result.offers) == 1
    assert result.offers[0].nightly_price_amount == pytest.approx(120.0)
    # Fields the fake adapter didn't supply stay honestly None -- success
    # never backfills a missing fact.
    assert result.offers[0].rating is None
    assert result.offers[0].booking_url is None
    assert result.offers[0].total_price_amount is None


def test_search_result_success_allows_empty_offers() -> None:
    result = AccommodationSearchResult(
        provider="fake_lodging_provider",
        status=AccommodationSearchStatus.SUCCESS,
        offers=[],
        message="No properties matched this search.",
    )
    assert result.offers == []
