from __future__ import annotations

import logging

from app.models.accommodation import (
    AccommodationOffer,
    AccommodationSearchResult,
    AccommodationSearchStatus,
)
from app.models.hotel_ratings import (
    AccommodationRating,
    HotelRatingLookupItem,
    HotelRatingsRequest,
    HotelRatingsStatus,
)
from app.providers.hotel_ratings import HotelRatingsProvider, get_hotel_ratings_provider

logger = logging.getLogger(__name__)

# Deterministic provider infrastructure, not AI reasoning (Step 177C,
# docs/13_llm_reasoning_pipeline.md, docs/14_backend_architecture.md).
# Enriches an already-built `AccommodationSearchResult` with
# `rating_details` on individual offers, using the hotel ratings provider
# contract added in Step 177B. With the default `not_connected` hotel
# ratings provider, `enrich` is a complete no-op: it returns the exact
# same `result` object it was given, unmodified.
#
# Matching is deliberately conservative and never fuzzy: an offer's own
# fields are used only to build an internal, per-call correlation id
# (`offer_id`, synthesized from list position -- never a field read from
# or written back onto `AccommodationOffer` itself) that a provider's
# response must echo back exactly. There is no name/address/coordinate
# fuzzy matching, no confidence threshold, and no provider search call --
# an offer_id the provider didn't echo, a duplicate/ambiguous match for
# the same offer_id, or a `provider_property_id` mismatch all leave that
# offer's `rating_details` at `None`, exactly as if enrichment had never
# run for it.

_OFFER_ID_PREFIX = "offer_"


def _synthetic_offer_id(index: int) -> str:
    """Per-call-only correlation id, never persisted on `AccommodationOffer`
    and never exposed outside this enrichment pass."""
    return f"{_OFFER_ID_PREFIX}{index}"


def _build_request(offer: AccommodationOffer, index: int) -> HotelRatingsRequest:
    """Builds a conservative identity request from fields `offer` already
    carries -- never a new/derived/guessed field."""
    return HotelRatingsRequest(
        offer_id=_synthetic_offer_id(index),
        provider_property_id=offer.provider_property_id,
        provider=offer.provider,
        property_name=offer.property_name,
        address=offer.address,
        latitude=offer.latitude,
        longitude=offer.longitude,
        source_name=offer.source_name,
    )


def _resolve_safe_ratings_by_offer_id(
    requests: list[HotelRatingsRequest],
    items: list[HotelRatingLookupItem],
) -> dict[str, AccommodationRating]:
    """Resolves the subset of `items` that may safely be attached to a
    known offer, keyed by the internal `offer_id` correlation id.

    Every rejection path below leaves the offer's `rating_details` at
    `None` rather than guessing:

    - An item with `matched=False` or `rating=None` is never considered
      (the model itself already forbids a non-`None` rating when
      `matched=False`).
    - An item referencing an `offer_id` this call never sent (unknown/
      stale correlation id) is ignored.
    - Two or more items referencing the *same* `offer_id` are ambiguous --
      none of them is attached, and no arbitrary choice is made between
      them.
    - When both sides carry a `provider_property_id`, they must match
      exactly, or the item is ignored.
    """
    request_by_offer_id = {
        request.offer_id: request for request in requests if request.offer_id is not None
    }

    candidate_items_by_offer_id: dict[str, list[HotelRatingLookupItem]] = {}
    for item in items:
        if not item.matched or item.rating is None or item.offer_id is None:
            continue
        candidate_items_by_offer_id.setdefault(item.offer_id, []).append(item)

    safe_ratings: dict[str, AccommodationRating] = {}
    for offer_id, candidate_items in candidate_items_by_offer_id.items():
        request = request_by_offer_id.get(offer_id)
        if request is None:
            # Unknown/stale offer_id -- never seen in this call's own
            # requests, so it can't correspond to exactly one offer here.
            continue
        if len(candidate_items) > 1:
            # Duplicate/ambiguous matches for the same offer -- never
            # chosen between arbitrarily.
            continue

        item = candidate_items[0]
        if (
            item.provider_property_id is not None
            and request.provider_property_id is not None
            and item.provider_property_id != request.provider_property_id
        ):
            # Both sides stated a provider_property_id and they disagree --
            # never trusted over the mismatch.
            continue

        safe_ratings[offer_id] = item.rating

    return safe_ratings


class HotelRatingEnrichmentService:
    """Conservative, additive enrichment of `accommodation_inventory_report`
    offers with `rating_details` (Step 177C).

    `enrich` never mutates its input `result` or any `AccommodationOffer`
    in place -- it returns either the exact same `result` (when there is
    nothing to enrich, or enrichment produced no safe match) or a new
    `AccommodationSearchResult` built via `model_copy(update=...)` whose
    `offers` list contains new `AccommodationOffer` objects (also via
    `model_copy`) for exactly the offers that received a safe match.
    Every other offer object in the returned list is the same object that
    was passed in.
    """

    def __init__(self, provider: HotelRatingsProvider | None = None) -> None:
        self._provider = provider

    def _resolve_provider(self) -> HotelRatingsProvider:
        return self._provider or get_hotel_ratings_provider()

    def enrich(self, result: AccommodationSearchResult) -> AccommodationSearchResult:
        if result.status != AccommodationSearchStatus.SUCCESS or not result.offers:
            # Nothing to enrich -- returned exactly as given, with no
            # hotel_ratings_* metadata stamped, since enrichment was never
            # attempted at all.
            return result

        requests = [_build_request(offer, index) for index, offer in enumerate(result.offers)]
        provider = self._resolve_provider()

        try:
            ratings_result = provider.get_ratings(requests)
        except Exception:
            logger.warning(
                "HotelRatingsProvider.get_ratings raised unexpectedly; leaving "
                "accommodation inventory offers unenriched.",
                exc_info=True,
            )
            return result

        if ratings_result.status != HotelRatingsStatus.SUCCESS or not ratings_result.items:
            return result.model_copy(
                update={
                    "hotel_ratings_status": ratings_result.status,
                    "hotel_ratings_provider": ratings_result.provider,
                    "hotel_ratings_message": ratings_result.message,
                    "hotel_ratings_enriched_offer_count": 0,
                }
            )

        safe_ratings_by_offer_id = _resolve_safe_ratings_by_offer_id(
            requests, ratings_result.items
        )

        enriched_offers: list[AccommodationOffer] = []
        enriched_count = 0
        for index, offer in enumerate(result.offers):
            rating = safe_ratings_by_offer_id.get(_synthetic_offer_id(index))
            if rating is None or offer.rating_details is not None:
                # No safe match, or this offer already carries
                # rating_details from an earlier pass -- never overwritten
                # here.
                enriched_offers.append(offer)
                continue
            enriched_offers.append(offer.model_copy(update={"rating_details": rating}))
            enriched_count += 1

        return result.model_copy(
            update={
                "offers": enriched_offers,
                "hotel_ratings_status": ratings_result.status,
                "hotel_ratings_provider": ratings_result.provider,
                "hotel_ratings_message": ratings_result.message,
                "hotel_ratings_enriched_offer_count": enriched_count,
            }
        )


hotel_rating_enrichment_service = HotelRatingEnrichmentService()
