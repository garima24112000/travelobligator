from __future__ import annotations

import logging

from app.models.flight import FlightSearchRequest, FlightSearchResult, FlightSearchStatus
from app.models.planning_state import PlanningState
from app.providers.gateway import ProviderGateway, provider_gateway

logger = logging.getLogger(__name__)

# Deterministic provider infrastructure, not AI reasoning (Step 169E,
# docs/12_provider_architecture.md, docs/13_llm_reasoning_pipeline.md,
# docs/14_backend_architecture.md). Builds a bookable flight inventory
# report from the trip's own request fields, using the real flight lookup
# now exposed through ProviderGateway (Step 169E), mirroring
# AccommodationInventoryService (Step 167D). This never schedules a
# flight into the itinerary as a daily experience, never adds flight
# recommendation logic, and never invents an airline, flight number,
# airport, departure/arrival time, duration, price, availability, baggage
# policy, cancellation policy, or booking link -- with the default
# `scraped_local` flight provider and no local HTML file present,
# `build_report` always returns an honest `unavailable` result with an
# empty `offers` list, without any network call.

_UNEXPECTED_FAILURE_MESSAGE = "The flight inventory provider request failed unexpectedly."


class FlightInventoryService:
    """Builds a `FlightSearchResult` for the current trip (Step 169E). Run
    by `PlanningOrchestrator.run_stay_transport_stage`, alongside
    `AccommodationInventoryService`.

    `build_report` is a pure read: it never mutates `planning_state`
    itself (the orchestrator stores the returned result), never reads or
    modifies `destination_context`/`experience_plan`, and only ever calls
    `ProviderGateway.search_flights` -- never a provider adapter directly,
    never Groq/Anthropic/Kiwi/MCP, and never scrapes a live website.

    `TripRequest` has no dedicated flight-search fields (no explicit
    origin airport, cabin class, or per-flight traveler count), so this
    service maps the closest existing trip fields: `origin_city` (which
    may be `None` -- never guessed when absent) becomes `origin`,
    `primary_destination` becomes `destination`, `start_date`/`end_date`
    become `departure_date`/`return_date` (a same-day trip is treated as
    one-way, `return_date=None`), `travelers_count` becomes `adults`, and
    `budget_currency` becomes `currency`. No cabin class or children count
    is ever guessed -- both stay unset.

    Step 166D-style hardening: an unexpected exception building the
    request or from `ProviderGateway.search_flights` is caught and
    converted into an honest `failed` result with a generic, safe message
    -- never the raw exception text or a provider payload, and never a
    fabricated offer.
    """

    def __init__(self, gateway: ProviderGateway | None = None) -> None:
        self.gateway = gateway or provider_gateway

    def build_report(self, planning_state: PlanningState) -> FlightSearchResult:
        provider_name = _provider_name(self.gateway)
        trip_request = planning_state.trip_request

        try:
            request = FlightSearchRequest(
                origin=trip_request.origin_city,
                destination=trip_request.primary_destination,
                departure_date=trip_request.start_date,
                return_date=(
                    trip_request.end_date
                    if trip_request.end_date != trip_request.start_date
                    else None
                ),
                adults=trip_request.travelers_count,
                currency=trip_request.budget_currency,
                trip_id=planning_state.trip_id,
            )
        except Exception:
            logger.warning(
                "Could not build a FlightSearchRequest from trip_request; treating this "
                "as failed.",
                exc_info=True,
            )
            return FlightSearchResult(
                provider=provider_name,
                status=FlightSearchStatus.FAILED,
                offers=[],
                message=_UNEXPECTED_FAILURE_MESSAGE,
                destination=trip_request.primary_destination,
                departure_date=trip_request.start_date,
            )

        return _safe_search_flights(self.gateway, request)


def _provider_name(gateway: ProviderGateway) -> str:
    return getattr(gateway.flight_inventory, "provider_name", "flight_inventory_provider")


def _safe_search_flights(
    gateway: ProviderGateway, request: FlightSearchRequest
) -> FlightSearchResult:
    try:
        return gateway.search_flights(request)
    except Exception:
        logger.warning(
            "ProviderGateway.search_flights raised unexpectedly; treating this as failed.",
            exc_info=True,
        )
        return FlightSearchResult(
            provider=_provider_name(gateway),
            status=FlightSearchStatus.FAILED,
            offers=[],
            message=_UNEXPECTED_FAILURE_MESSAGE,
            origin=request.origin,
            destination=request.destination,
            departure_date=request.departure_date,
            return_date=request.return_date,
            adults=request.adults,
            children=request.children,
            currency=request.currency,
        )


flight_inventory_service = FlightInventoryService()
