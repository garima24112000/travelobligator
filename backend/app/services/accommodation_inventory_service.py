from __future__ import annotations

import logging

from app.models.accommodation import (
    AccommodationSearchRequest,
    AccommodationSearchResult,
    AccommodationSearchStatus,
)
from app.models.planning_state import PlanningState
from app.providers.gateway import ProviderGateway, provider_gateway
from app.services.hotel_rating_enrichment_service import (
    HotelRatingEnrichmentService,
    hotel_rating_enrichment_service,
)

logger = logging.getLogger(__name__)

# Deterministic provider infrastructure, not AI reasoning (Step 167D,
# docs/12_provider_architecture.md, docs/13_llm_reasoning_pipeline.md,
# docs/14_backend_architecture.md). Builds a bookable accommodation
# inventory report from the trip's own request fields, using the real
# accommodation lookup already exposed through ProviderGateway (Step
# 167C). This never schedules lodging into the itinerary, never adds hotel
# recommendation logic, and never converts
# `DestinationContext.candidate_accommodation_pois` (open-data OSM
# location candidates) into bookable inventory -- this service does not
# read `destination_context` at all. No property, price, availability,
# rating, amenity, cancellation policy, or booking link is ever
# fabricated: with the default `not_connected` accommodation provider,
# `build_report` always returns an honest `not_connected` result with an
# empty `offers` list, without any network call.
#
# Step 177C: after the base provider returns, `build_report` additionally
# runs the result through `HotelRatingEnrichmentService.enrich`, which may
# conservatively attach `rating_details` to individual offers -- see that
# module's own docstring for the full matching contract. With the default
# `not_connected` hotel ratings provider (`Settings.hotel_ratings_provider`),
# this enrichment step is a complete no-op, so default behavior (status,
# offer count, and every existing offer field) is unchanged.

_DEFAULT_ROOMS = 1
_NOT_ENOUGH_NIGHTS_MESSAGE = (
    "Trip dates do not span at least one night, so accommodation inventory was not requested."
)
_UNEXPECTED_FAILURE_MESSAGE = (
    "The accommodation inventory provider request failed unexpectedly."
)


class AccommodationInventoryService:
    """Builds an `AccommodationSearchResult` for the current trip (Step
    167D). Run by `PlanningOrchestrator.run_stay_transport_stage`, after
    `StayTransportService.run`.

    `build_report` is a pure read: it never mutates `planning_state`
    itself (the orchestrator stores the returned result), never reads or
    modifies `destination_context`/`experience_plan`, and only ever calls
    `ProviderGateway.search_accommodations` -- never a provider adapter
    directly, never Groq/Anthropic/Kiwi/MCP, and never scrapes.

    Step 166D-style hardening: an unexpected exception from
    `ProviderGateway.search_accommodations` is caught and converted into
    an honest `failed` result with a generic, safe message -- never the
    raw exception text or a provider payload, and never a fabricated
    offer.
    """

    def __init__(
        self,
        gateway: ProviderGateway | None = None,
        hotel_rating_enrichment_service_override: HotelRatingEnrichmentService | None = None,
    ) -> None:
        self.gateway = gateway or provider_gateway
        self.hotel_rating_enrichment_service = (
            hotel_rating_enrichment_service_override or hotel_rating_enrichment_service
        )

    def build_report(self, planning_state: PlanningState) -> AccommodationSearchResult:
        provider_name = _provider_name(self.gateway)
        trip_request = planning_state.trip_request

        if trip_request.end_date <= trip_request.start_date:
            return AccommodationSearchResult(
                provider=provider_name,
                status=AccommodationSearchStatus.UNAVAILABLE,
                offers=[],
                message=_NOT_ENOUGH_NIGHTS_MESSAGE,
            )

        request = AccommodationSearchRequest(
            destination=trip_request.primary_destination,
            check_in_date=trip_request.start_date,
            check_out_date=trip_request.end_date,
            adults=trip_request.travelers_count,
            rooms=_DEFAULT_ROOMS,
            currency=trip_request.budget_currency,
        )
        result = _safe_search_accommodations(self.gateway, request)
        return self.hotel_rating_enrichment_service.enrich(result)


def _provider_name(gateway: ProviderGateway) -> str:
    return getattr(
        gateway.accommodation_inventory, "provider_name", "accommodation_inventory_provider"
    )


def _safe_search_accommodations(
    gateway: ProviderGateway, request: AccommodationSearchRequest
) -> AccommodationSearchResult:
    try:
        return gateway.search_accommodations(request)
    except Exception:
        logger.warning(
            "ProviderGateway.search_accommodations raised unexpectedly; treating this as failed.",
            exc_info=True,
        )
        return AccommodationSearchResult(
            provider=_provider_name(gateway),
            status=AccommodationSearchStatus.FAILED,
            offers=[],
            message=_UNEXPECTED_FAILURE_MESSAGE,
        )


accommodation_inventory_service = AccommodationInventoryService()
