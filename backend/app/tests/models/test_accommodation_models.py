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
from app.models.hotel_ratings import AccommodationRating
from app.models.scraping import ScrapedDataConfidence, ScrapedDataProvenance

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
    assert offer.rating_details is None


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


# ---------------------------------------------------------------------------
# 11. AccommodationOffer.scraped_provenance (Step 168B) stays None unless
#     set, and is only ever valid alongside data_status=scraped_public_page.
# ---------------------------------------------------------------------------


def _scraped_provenance(**overrides: object) -> ScrapedDataProvenance:
    fields: dict[str, object] = {
        "source_id": "example_test_only_travel_blog",
        "source_name": "Example Test-Only Travel Blog",
        "confidence": ScrapedDataConfidence.EXPERIMENTAL,
    }
    fields.update(overrides)
    return ScrapedDataProvenance(**fields)


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


def test_offer_rejects_scraped_provenance_with_official_looking_data_status() -> None:
    """`scraped_provenance` can never be attached to an offer whose
    `data_status` claims official-looking data (e.g. `live`) -- scraped
    data must never be presented as official-provider data."""
    with pytest.raises(ValidationError):
        _offer(data_status=DataStatus.LIVE, scraped_provenance=_scraped_provenance())


# ---------------------------------------------------------------------------
# 12. AccommodationOffer.rating_details (Step 177B) is a wholly optional,
#     additive field -- existing offers built without it remain valid, the
#     pre-existing bare `rating` field is completely unaffected, and this
#     step never auto-populates rating_details from anywhere.
# ---------------------------------------------------------------------------


def test_offer_without_rating_details_remains_valid() -> None:
    """An `AccommodationOffer` built exactly like every pre-Step-177B test
    in this file (no `rating_details` kwarg at all) must still validate,
    with `rating_details` defaulting to `None` and the pre-existing bare
    `rating` field untouched."""
    offer = _offer(rating=4.2)
    assert offer.rating == pytest.approx(4.2)
    assert offer.rating_details is None


def test_offer_accepts_valid_rating_details() -> None:
    offer = _offer(
        rating_details=AccommodationRating(
            value=4.5,
            review_count=128,
            provider="fake_ratings_provider",
            data_status=DataStatus.LIVE,
        )
    )
    assert offer.rating_details is not None
    assert offer.rating_details.value == pytest.approx(4.5)
    assert offer.rating_details.review_count == 128
    assert offer.rating_details.scale_max == pytest.approx(5.0)
