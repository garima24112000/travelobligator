from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.accommodation import (
    AccommodationOffer,
    AccommodationSearchResult,
    AccommodationSearchStatus,
)
from app.models.common import DataStatus
from app.models.hotel_ratings import (
    AccommodationRating,
    HotelRatingLookupItem,
    HotelRatingsRequest,
    HotelRatingsResult,
    HotelRatingsStatus,
)
from app.providers.hotel_ratings import HotelRatingsProvider, NotConnectedHotelRatingsProvider
from app.services.hotel_rating_enrichment_service import HotelRatingEnrichmentService

# Step 177C: conservative hotel-rating enrichment tests. Every fake
# provider here is an in-memory test double injected directly into
# `HotelRatingEnrichmentService` -- never registered in
# `app.providers.hotel_ratings.factory._SUPPORTED_PROVIDERS`, and never a
# real network/LLM call.


class _FakeHotelRatingsProvider(HotelRatingsProvider):
    provider_name = "fake_hotel_ratings_provider"

    def __init__(self, result: HotelRatingsResult | None = None, *, raises: bool = False) -> None:
        self._result = result
        self._raises = raises
        self.calls: list[list[HotelRatingsRequest]] = []

    def get_ratings(self, requests: list[HotelRatingsRequest]) -> HotelRatingsResult:
        self.calls.append(requests)
        if self._raises:
            raise RuntimeError("simulated unexpected ratings provider failure")
        if self._result is not None:
            return self._result
        return HotelRatingsResult(
            provider=self.provider_name,
            status=HotelRatingsStatus.NOT_CONNECTED,
            items=[],
            message="Fake provider for test purposes only.",
        )


def _offer(**overrides: object) -> AccommodationOffer:
    fields: dict[str, object] = {
        "provider": "fake_lodging_provider",
        "provider_property_id": "prop_1",
        "property_name": "Test Property",
        "data_status": DataStatus.LIVE,
    }
    fields.update(overrides)
    return AccommodationOffer(**fields)


def _success_result(offers: list[AccommodationOffer]) -> AccommodationSearchResult:
    return AccommodationSearchResult(
        provider="fake_lodging_provider",
        status=AccommodationSearchStatus.SUCCESS,
        offers=offers,
    )


def _rating(**overrides: object) -> AccommodationRating:
    fields: dict[str, object] = {"value": 4.5, "data_status": DataStatus.LIVE}
    fields.update(overrides)
    return AccommodationRating(**fields)


# ---------------------------------------------------------------------------
# 1/2. Default not_connected hotel ratings provider leaves all
#      rating_details null and doesn't change offer count/status.
# ---------------------------------------------------------------------------


def test_default_not_connected_provider_leaves_rating_details_null() -> None:
    service = HotelRatingEnrichmentService(provider=NotConnectedHotelRatingsProvider())
    offer = _offer()
    result = _success_result([offer])

    enriched = service.enrich(result)

    assert enriched.status == AccommodationSearchStatus.SUCCESS
    assert len(enriched.offers) == 1
    assert enriched.offers[0].rating_details is None


def test_enrich_is_a_no_op_when_base_result_is_not_success() -> None:
    fake_provider = _FakeHotelRatingsProvider()
    service = HotelRatingEnrichmentService(provider=fake_provider)
    result = AccommodationSearchResult(
        provider="accommodation_inventory_provider",
        status=AccommodationSearchStatus.NOT_CONNECTED,
        offers=[],
    )

    enriched = service.enrich(result)

    assert enriched is result
    assert fake_provider.calls == []


def test_enrich_is_a_no_op_when_there_are_no_offers() -> None:
    fake_provider = _FakeHotelRatingsProvider()
    service = HotelRatingEnrichmentService(provider=fake_provider)
    result = _success_result([])

    enriched = service.enrich(result)

    assert enriched is result
    assert fake_provider.calls == []


# ---------------------------------------------------------------------------
# 3. Successful exact matched fake provider attaches rating_details to the
#    correct offer.
# ---------------------------------------------------------------------------


def test_exact_matched_provider_attaches_rating_to_correct_offer() -> None:
    offer_a = _offer(provider_property_id="prop_a", property_name="Hotel A")
    offer_b = _offer(provider_property_id="prop_b", property_name="Hotel B")
    result = _success_result([offer_a, offer_b])

    rating_for_b = _rating(value=4.8, review_count=42, provider="fake_hotel_ratings_provider")
    fake_provider = _FakeHotelRatingsProvider(
        result=HotelRatingsResult(
            provider="fake_hotel_ratings_provider",
            status=HotelRatingsStatus.SUCCESS,
            items=[
                HotelRatingLookupItem(offer_id="offer_1", matched=True, rating=rating_for_b),
            ],
        )
    )
    service = HotelRatingEnrichmentService(provider=fake_provider)

    enriched = service.enrich(result)

    assert enriched.offers[0].rating_details is None
    assert enriched.offers[1].rating_details is not None
    assert enriched.offers[1].rating_details.value == pytest.approx(4.8)
    assert enriched.offers[1].rating_details.review_count == 42
    assert enriched.hotel_ratings_enriched_offer_count == 1
    assert enriched.hotel_ratings_status == HotelRatingsStatus.SUCCESS
    assert enriched.hotel_ratings_provider == "fake_hotel_ratings_provider"
    # Original offer objects are untouched -- enrichment never mutates in
    # place.
    assert offer_a.rating_details is None
    assert offer_b.rating_details is None


# ---------------------------------------------------------------------------
# 4. Unknown returned offer id is ignored.
# ---------------------------------------------------------------------------


def test_unknown_offer_id_is_ignored() -> None:
    offer = _offer()
    result = _success_result([offer])
    fake_provider = _FakeHotelRatingsProvider(
        result=HotelRatingsResult(
            provider="fake_hotel_ratings_provider",
            status=HotelRatingsStatus.SUCCESS,
            items=[
                HotelRatingLookupItem(
                    offer_id="offer_999_never_requested", matched=True, rating=_rating()
                ),
            ],
        )
    )
    service = HotelRatingEnrichmentService(provider=fake_provider)

    enriched = service.enrich(result)

    assert enriched.offers[0].rating_details is None
    assert enriched.hotel_ratings_enriched_offer_count == 0


# ---------------------------------------------------------------------------
# 5. Duplicate returned matches for the same offer do not attach an
#    arbitrary rating.
# ---------------------------------------------------------------------------


def test_duplicate_matches_for_same_offer_attach_nothing() -> None:
    offer = _offer()
    result = _success_result([offer])
    fake_provider = _FakeHotelRatingsProvider(
        result=HotelRatingsResult(
            provider="fake_hotel_ratings_provider",
            status=HotelRatingsStatus.SUCCESS,
            items=[
                HotelRatingLookupItem(
                    offer_id="offer_0", matched=True, rating=_rating(value=4.0)
                ),
                HotelRatingLookupItem(
                    offer_id="offer_0", matched=True, rating=_rating(value=3.0)
                ),
            ],
        )
    )
    service = HotelRatingEnrichmentService(provider=fake_provider)

    enriched = service.enrich(result)

    assert enriched.offers[0].rating_details is None
    assert enriched.hotel_ratings_enriched_offer_count == 0


# ---------------------------------------------------------------------------
# 6. Unmatched item with rating is rejected/ignored safely.
# ---------------------------------------------------------------------------


def test_unmatched_item_cannot_carry_a_rating() -> None:
    with pytest.raises(ValidationError):
        HotelRatingLookupItem(offer_id="offer_0", matched=False, rating=_rating())


def test_unmatched_item_without_rating_attaches_nothing() -> None:
    offer = _offer()
    result = _success_result([offer])
    fake_provider = _FakeHotelRatingsProvider(
        result=HotelRatingsResult(
            provider="fake_hotel_ratings_provider",
            status=HotelRatingsStatus.SUCCESS,
            items=[HotelRatingLookupItem(offer_id="offer_0", matched=False)],
        )
    )
    service = HotelRatingEnrichmentService(provider=fake_provider)

    enriched = service.enrich(result)

    assert enriched.offers[0].rating_details is None
    assert enriched.hotel_ratings_enriched_offer_count == 0


# ---------------------------------------------------------------------------
# 7. provider_property_id mismatch does not attach a rating.
# ---------------------------------------------------------------------------


def test_provider_property_id_mismatch_attaches_nothing() -> None:
    offer = _offer(provider_property_id="prop_real")
    result = _success_result([offer])
    fake_provider = _FakeHotelRatingsProvider(
        result=HotelRatingsResult(
            provider="fake_hotel_ratings_provider",
            status=HotelRatingsStatus.SUCCESS,
            items=[
                HotelRatingLookupItem(
                    offer_id="offer_0",
                    provider_property_id="prop_different",
                    matched=True,
                    rating=_rating(),
                )
            ],
        )
    )
    service = HotelRatingEnrichmentService(provider=fake_provider)

    enriched = service.enrich(result)

    assert enriched.offers[0].rating_details is None
    assert enriched.hotel_ratings_enriched_offer_count == 0


def test_provider_property_id_match_still_attaches() -> None:
    offer = _offer(provider_property_id="prop_real")
    result = _success_result([offer])
    fake_provider = _FakeHotelRatingsProvider(
        result=HotelRatingsResult(
            provider="fake_hotel_ratings_provider",
            status=HotelRatingsStatus.SUCCESS,
            items=[
                HotelRatingLookupItem(
                    offer_id="offer_0",
                    provider_property_id="prop_real",
                    matched=True,
                    rating=_rating(),
                )
            ],
        )
    )
    service = HotelRatingEnrichmentService(provider=fake_provider)

    enriched = service.enrich(result)

    assert enriched.offers[0].rating_details is not None


# ---------------------------------------------------------------------------
# 8. Failed/unavailable/not_connected ratings provider does not populate
#    rating_details.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status",
    [HotelRatingsStatus.NOT_CONNECTED, HotelRatingsStatus.UNAVAILABLE, HotelRatingsStatus.FAILED],
)
def test_non_success_ratings_status_never_populates_rating_details(
    status: HotelRatingsStatus,
) -> None:
    offer = _offer()
    result = _success_result([offer])
    fake_provider = _FakeHotelRatingsProvider(
        result=HotelRatingsResult(provider="fake_hotel_ratings_provider", status=status, items=[])
    )
    service = HotelRatingEnrichmentService(provider=fake_provider)

    enriched = service.enrich(result)

    assert enriched.offers[0].rating_details is None
    assert enriched.hotel_ratings_status == status
    assert enriched.hotel_ratings_enriched_offer_count == 0


def test_ratings_provider_exception_leaves_result_unenriched() -> None:
    offer = _offer()
    result = _success_result([offer])
    fake_provider = _FakeHotelRatingsProvider(raises=True)
    service = HotelRatingEnrichmentService(provider=fake_provider)

    enriched = service.enrich(result)

    assert enriched.offers[0].rating_details is None


# ---------------------------------------------------------------------------
# 9. Existing legacy AccommodationOffer.rating is not copied into
#    rating_details.
# ---------------------------------------------------------------------------


def test_legacy_rating_is_never_copied_into_rating_details() -> None:
    offer = _offer(rating=4.2)
    result = _success_result([offer])
    fake_provider = _FakeHotelRatingsProvider(
        result=HotelRatingsResult(
            provider="fake_hotel_ratings_provider",
            status=HotelRatingsStatus.SUCCESS,
            items=[],
        )
    )
    service = HotelRatingEnrichmentService(provider=fake_provider)

    enriched = service.enrich(result)

    assert enriched.offers[0].rating == pytest.approx(4.2)
    assert enriched.offers[0].rating_details is None


# ---------------------------------------------------------------------------
# 10. Existing legacy AccommodationOffer.rating is not overwritten by
#     enrichment.
# ---------------------------------------------------------------------------


def test_legacy_rating_is_never_overwritten_by_enrichment() -> None:
    offer = _offer(rating=4.2)
    result = _success_result([offer])
    fake_provider = _FakeHotelRatingsProvider(
        result=HotelRatingsResult(
            provider="fake_hotel_ratings_provider",
            status=HotelRatingsStatus.SUCCESS,
            items=[
                HotelRatingLookupItem(
                    offer_id="offer_0", matched=True, rating=_rating(value=1.0)
                )
            ],
        )
    )
    service = HotelRatingEnrichmentService(provider=fake_provider)

    enriched = service.enrich(result)

    # rating_details got the provider's value, but the legacy bare
    # `rating` field (scraped/manual, here 4.2) is completely untouched --
    # never overwritten with the provider's 1.0.
    assert enriched.offers[0].rating == pytest.approx(4.2)
    assert enriched.offers[0].rating_details.value == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Existing rating_details is never overwritten by a later enrichment pass.
# ---------------------------------------------------------------------------


def test_existing_rating_details_is_never_overwritten() -> None:
    existing_rating = _rating(value=3.3, provider="already_set_provider")
    offer = _offer(rating_details=existing_rating)
    result = _success_result([offer])
    fake_provider = _FakeHotelRatingsProvider(
        result=HotelRatingsResult(
            provider="fake_hotel_ratings_provider",
            status=HotelRatingsStatus.SUCCESS,
            items=[
                HotelRatingLookupItem(
                    offer_id="offer_0", matched=True, rating=_rating(value=5.0)
                )
            ],
        )
    )
    service = HotelRatingEnrichmentService(provider=fake_provider)

    enriched = service.enrich(result)

    assert enriched.offers[0].rating_details.value == pytest.approx(3.3)
    assert enriched.offers[0].rating_details.provider == "already_set_provider"
    assert enriched.hotel_ratings_enriched_offer_count == 0
