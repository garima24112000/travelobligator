from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.common import DataStatus
from app.models.hotel_ratings import (
    AccommodationRating,
    HotelRatingLookupItem,
    HotelRatingsRequest,
    HotelRatingsResult,
    HotelRatingsStatus,
)

# Model/contract tests for Step 177B's hotel ratings provider foundation.
# Never calls a real network service, never requires any rating provider
# configuration -- these only exercise pydantic validation.


def _rating(**overrides: object) -> AccommodationRating:
    fields: dict[str, object] = {"data_status": DataStatus.NOT_CONNECTED}
    fields.update(overrides)
    return AccommodationRating(**fields)


# ---------------------------------------------------------------------------
# 2. AccommodationRating accepts a valid 0-5 rating and review_count.
# ---------------------------------------------------------------------------


def test_rating_accepts_valid_value_and_review_count() -> None:
    rating = _rating(value=4.3, review_count=512, data_status=DataStatus.LIVE)
    assert rating.value == pytest.approx(4.3)
    assert rating.review_count == 512
    assert rating.scale_max == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# 3. AccommodationRating rejects a negative rating value.
# ---------------------------------------------------------------------------


def test_rating_rejects_negative_value() -> None:
    with pytest.raises(ValidationError):
        _rating(value=-0.1)


# ---------------------------------------------------------------------------
# 4. AccommodationRating rejects a rating value above 5.
# ---------------------------------------------------------------------------


def test_rating_rejects_value_above_five() -> None:
    with pytest.raises(ValidationError):
        _rating(value=5.1)


# ---------------------------------------------------------------------------
# 5. AccommodationRating rejects a negative review_count.
# ---------------------------------------------------------------------------


def test_rating_rejects_negative_review_count() -> None:
    with pytest.raises(ValidationError):
        _rating(review_count=-1)


# ---------------------------------------------------------------------------
# 6. AccommodationRating allows null value/review_count when a provider
#    returns no rating for a property it otherwise matched.
# ---------------------------------------------------------------------------


def test_rating_allows_null_value_and_review_count() -> None:
    rating = _rating(data_status=DataStatus.UNAVAILABLE)
    assert rating.value is None
    assert rating.review_count is None
    assert rating.provider is None
    assert rating.source_name is None
    assert rating.source_url is None
    assert rating.retrieved_at is None


# ---------------------------------------------------------------------------
# HotelRatingsRequest carries only conservative, already-known identity
# fields -- no fuzzy-matching input, no required provider credential.
# ---------------------------------------------------------------------------


def test_request_accepts_minimal_identity_fields() -> None:
    request = HotelRatingsRequest(
        offer_id="offer_1",
        provider_property_id="prop_123",
        property_name="Test Property",
    )
    assert request.offer_id == "offer_1"
    assert request.provider_property_id == "prop_123"
    assert request.address is None
    assert request.latitude is None
    assert request.longitude is None


def test_request_rejects_invalid_coordinates() -> None:
    with pytest.raises(ValidationError):
        HotelRatingsRequest(latitude=200.0)


# ---------------------------------------------------------------------------
# HotelRatingLookupItem never carries a rating unless matched=True.
# ---------------------------------------------------------------------------


def test_lookup_item_defaults_to_unmatched_with_no_rating() -> None:
    item = HotelRatingLookupItem(offer_id="offer_1")
    assert item.matched is False
    assert item.rating is None


def test_lookup_item_allows_rating_when_matched() -> None:
    item = HotelRatingLookupItem(
        offer_id="offer_1",
        matched=True,
        rating=_rating(value=4.0, data_status=DataStatus.LIVE),
    )
    assert item.rating is not None
    assert item.rating.value == pytest.approx(4.0)


def test_lookup_item_rejects_rating_when_unmatched() -> None:
    with pytest.raises(ValidationError):
        HotelRatingLookupItem(
            offer_id="offer_1",
            matched=False,
            rating=_rating(value=4.0, data_status=DataStatus.LIVE),
        )


# ---------------------------------------------------------------------------
# HotelRatingsResult mirrors AccommodationSearchResult's own
# status/items-must-agree safety rule.
# ---------------------------------------------------------------------------


def test_result_not_connected_with_empty_items() -> None:
    result = HotelRatingsResult(
        provider="hotel_ratings_provider",
        status=HotelRatingsStatus.NOT_CONNECTED,
        items=[],
        message="No hotel ratings provider is configured.",
    )
    assert result.status == HotelRatingsStatus.NOT_CONNECTED
    assert result.items == []


def test_result_not_connected_rejects_nonempty_items() -> None:
    with pytest.raises(ValidationError):
        HotelRatingsResult(
            provider="hotel_ratings_provider",
            status=HotelRatingsStatus.NOT_CONNECTED,
            items=[HotelRatingLookupItem(offer_id="offer_1")],
        )


def test_result_success_allows_unmatched_items_without_inventing_a_rating() -> None:
    result = HotelRatingsResult(
        provider="fake_ratings_provider",
        status=HotelRatingsStatus.SUCCESS,
        items=[HotelRatingLookupItem(offer_id="offer_1", matched=False)],
    )
    assert result.items[0].rating is None
