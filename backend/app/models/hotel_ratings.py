from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from app.models.common import DataStatus

# Hotel ratings provider foundation (Step 177B, docs/13_llm_reasoning_pipeline.md,
# docs/14_backend_architecture.md). This is a contract only:
#
# - No adapter here is wired into `ProviderGateway`, `PlanningOrchestrator`,
#   `AccommodationInventoryService`, or `ProviderCoverage` yet.
# - No real Google Places/Tripadvisor/Amadeus/Yelp (or any other) rating
#   provider is connected, called, or scraped by this step.
# - No fuzzy identity matching is implemented or performed anywhere in this
#   module -- every provider-specific hotel/property ID a real adapter would
#   need is out of scope here; only conservative, already-known identity
#   fields (already present on an `AccommodationOffer`) are carried.
#
# `AccommodationRating` is a deliberately separate, richer shape from the
# pre-existing bare `AccommodationOffer.rating: float | None` (Step 167A) --
# it is never auto-populated from that field, and this step does not change
# `AccommodationOffer.rating`'s own behavior at all. Every optional fact
# field here stays `None` (never a guessed/default value) unless a real
# rating provider adapter actually returned it.


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class HotelRatingsStatus(str, Enum):
    SUCCESS = "success"
    NOT_CONNECTED = "not_connected"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class AccommodationRating(BaseModel):
    """Normalized, provider-backed hotel rating snapshot (Step 177B).

    Distinct from `AccommodationOffer.rating` (a bare `float | None` that
    exists today only because `ScrapedAccommodationProvider` parses a
    free-form numeric string with no known scale/source/review-count) --
    this model exists to carry that context honestly once a real rating
    source is wired in. `value`/`review_count` stay `None` unless a real
    provider actually returned them; there is no default that implies a
    rating exists. `scale_max` is not a fabricated fact -- it is this
    app's own fixed normalization convention (every `value` here is
    expected on a 0-5 scale), not a value read from any provider.
    """

    value: float | None = Field(default=None, ge=0.0, le=5.0)
    scale_max: float = Field(default=5.0, gt=0.0)
    review_count: int | None = Field(default=None, ge=0)
    provider: str | None = None
    source_name: str | None = None
    source_url: str | None = None
    data_status: DataStatus
    retrieved_at: datetime | None = None


class HotelRatingsRequest(BaseModel):
    """Conservative identity input for one hotel-ratings lookup, built only
    from fields an `AccommodationOffer` (or its caller) already has on
    hand. This model performs no matching itself -- it only carries
    identity context for a future provider adapter to match exactly
    against, or refuse to match at all.

    `offer_id` is an optional, caller-assigned correlation identifier (an
    `AccommodationOffer` has no `offer_id` field of its own) used only to
    let a caller line up a `HotelRatingLookupItem` in the response with
    the specific offer it was requested for -- it is never read from, or
    written back onto, `AccommodationOffer` itself.
    """

    offer_id: str | None = None
    provider_property_id: str | None = None
    provider: str | None = None
    property_name: str | None = None
    address: str | None = None
    latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    longitude: float | None = Field(default=None, ge=-180.0, le=180.0)
    source_name: str | None = None


class HotelRatingLookupItem(BaseModel):
    """One lookup outcome for a single `HotelRatingsRequest`, echoing back
    only the identity fields it was resolved against -- never a different
    property than the one requested.

    `matched=False` (the default) means the provider was connected and
    the lookup was attempted, but produced no safe, exact match -- `rating`
    stays `None` in that case, on purpose; this never falls back to a
    fuzzy/best-guess match. `rating` may only be set when `matched=True`.
    """

    offer_id: str | None = None
    provider_property_id: str | None = None
    matched: bool = False
    rating: AccommodationRating | None = None

    @model_validator(mode="after")
    def validate_rating_only_when_matched(self) -> "HotelRatingLookupItem":
        if self.rating is not None and not self.matched:
            raise ValueError(
                "HotelRatingLookupItem.rating can only be set when matched is True -- "
                "an unmatched lookup must never carry a guessed rating."
            )
        return self


class HotelRatingsResult(BaseModel):
    """Normalized response envelope returned by
    `HotelRatingsProvider.get_ratings` (Step 177B).

    `items` may only be non-empty when `status == success` -- a
    `not_connected`/`unavailable`/`failed` result must never carry a
    fabricated or leftover lookup item alongside its honest failure
    status. A `success` result may still contain unmatched items
    (`matched=False`, `rating=None`) for requests the provider searched
    but could not safely resolve.
    """

    provider: str
    status: HotelRatingsStatus
    items: list[HotelRatingLookupItem] = Field(default_factory=list)
    message: str | None = None
    generated_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def validate_items_match_status(self) -> "HotelRatingsResult":
        if self.status != HotelRatingsStatus.SUCCESS and self.items:
            raise ValueError(
                f"items must be empty when status is '{self.status.value}' -- "
                "only a 'success' result may carry real lookup items."
            )
        return self
