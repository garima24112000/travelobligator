from __future__ import annotations

import logging

from typing import Any

from app.models.flight import (
    FlightAvailabilityReason,
    FlightLocationResolution,
    FlightSearchRequest,
    FlightSearchResult,
    FlightSearchStatus,
)
from app.models.planning_state import PlanningState
from app.providers.flights import airport_lookup
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

    def build_report(
        self, planning_state: PlanningState, provider_context: Any | None = None
    ) -> FlightSearchResult:
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
                destination_city=_resolved_city(planning_state),
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
                availability_reason=FlightAvailabilityReason.PROVIDER_FAILED,
                destination=trip_request.primary_destination,
                departure_date=trip_request.start_date,
            )

        first = ensure_availability_reason(_safe_search_flights(self.gateway, request))
        if first.availability_reason != FlightAvailabilityReason.LOCATION_UNCONFIRMED:
            return first
        return _retry_with_nearby_airports(self.gateway, request, first, provider_context)


def _resolved_city(planning_state: PlanningState) -> str | None:
    """M2 Phase 2: the destination city as the destination geocoder already
    resolved it (`destination_context.resolved_destination`), or None."""
    context = planning_state.destination_context
    resolved = context.resolved_destination if context is not None else None
    if not isinstance(resolved, dict):
        return None
    for key in ("city", "town", "village", "municipality"):
        value = resolved.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:160]
    return None


def ensure_availability_reason(result: FlightSearchResult) -> FlightSearchResult:
    """M2 Phase 1 (D2): a result whose adapter did not name a cause gets one
    only where its status alone proves it. An `unavailable` result without a
    reason stays unclassified -- it is never assumed to be a completed
    search with zero offers."""
    if result.availability_reason is not None:
        return result
    if result.status == FlightSearchStatus.SUCCESS and result.offers:
        return result.with_reason(FlightAvailabilityReason.OFFERS_FOUND)
    if result.status == FlightSearchStatus.NOT_CONNECTED:
        return result.with_reason(FlightAvailabilityReason.NO_SOURCE_CONFIGURED)
    if result.status == FlightSearchStatus.FAILED:
        return result.with_reason(FlightAvailabilityReason.PROVIDER_FAILED)
    return result


# M2 Phase 2 (Kiwi remediation): at most ONE further flight search per trip,
# after a search by place NAME returned nothing, against the best airport with
# evidence of scheduled airline service the lookup provider found for each side
# searched by name (never simply the nearest -- providers/flights/airport_lookup.py).
# Bounded: two flight searches and two airport lookups per trip at most.
_NO_AIRPORT_MESSAGE = (
    "No flight search could be made from {place}: {why} Flights were not checked for this place."
)
_LOOKUP_UNAVAILABLE_NOTE = "Nearby airports with scheduled airline service could not be looked up ({status})."
_UNVERIFIED_AIRPORT_NOTE = (
    "A nearby airport was searched without evidence of scheduled airline service, so finding no offers there "
    "does not show that no flights exist."
)


def _airport_note(requested: str, airport: "airport_lookup.NearbyAirport") -> str:
    code = f" ({airport.iata_code})" if airport.iata_code else ""
    return (
        f"Searched from {airport.name}{code}, an airport with scheduled airline service near {requested} "
        f"(about {airport.straight_line_km:g} km in a straight line). It is not in the place itself."
    )


def _has_commercial_evidence(entry: FlightLocationResolution) -> bool:
    return entry.method != "nearby_airport" or bool(entry.commercial_service_evidence)


def _retry_with_nearby_airports(
    gateway: ProviderGateway,
    request: FlightSearchRequest,
    first: FlightSearchResult,
    provider_context: Any | None,
) -> FlightSearchResult:
    resolution = {entry.role: entry for entry in (first.location_resolution or [])}
    sides = [
        ("origin", request.origin),
        ("destination", request.destination),
    ]
    replacements: dict[str, FlightLocationResolution] = {}
    unresolved_notes: list[str] = []
    for role, requested in sides:
        entry = resolution.get(role)
        if entry is None or entry.method != "place_name" or not requested:
            continue
        found = gateway.nearby_airports(requested, provider_context)
        # Only an airport with evidence of scheduled airline service AND a code
        # is ever substituted; the lookup returns them best first.
        suitable = [
            airport for airport in (found.airports if found.status == airport_lookup.STATUS_FOUND else ())
            if airport.commercial_service_evidence and airport.iata_code
        ]
        if suitable:
            airport = suitable[0]
            replacements[role] = FlightLocationResolution(
                role=role,
                requested=requested[:160],
                searched=airport.iata_code,
                method="nearby_airport",
                airport_name=airport.name,
                airport_code=airport.iata_code,
                straight_line_km=airport.straight_line_km,
                lookup_source=found.source,
                note=_airport_note(entry.searched or requested, airport),
                airport_lat=airport.lat,
                airport_lon=airport.lon,
                airport_type=airport.airport_type,
                airport_country=airport.country,
                commercial_service_evidence=airport.commercial_service_evidence,
                selection_rationale=airport.rationale,
                dataset_version=found.dataset_version,
            )
        elif found.status in (airport_lookup.STATUS_PLACE_NOT_FOUND, airport_lookup.STATUS_NONE_NEARBY):
            why = (
                "the place could not be found on the map."
                if found.status == airport_lookup.STATUS_PLACE_NOT_FOUND
                else f"no airport with scheduled airline service was found within {found.radius_km:g} km of it "
                "in the airport data used."
            )
            return first.model_copy(update={
                "availability_reason": FlightAvailabilityReason.NO_SEARCHABLE_AIRPORT,
                "message": _NO_AIRPORT_MESSAGE.format(place=requested, why=why),
                "location_resolution": [
                    *(e for e in (first.location_resolution or []) if e.role != role),
                    entry.model_copy(update={"method": "not_resolved", "searched": None,
                                             "lookup_source": found.source, "note": why,
                                             "dataset_version": found.dataset_version}),
                ],
            })
        else:
            unresolved_notes.append(_LOOKUP_UNAVAILABLE_NOTE.format(status=found.failure_kind or found.status))

    if not replacements:
        if unresolved_notes:
            return first.model_copy(update={"warnings": [*first.warnings, *dict.fromkeys(unresolved_notes)]})
        return first

    retry_request = request.model_copy(update={
        "origin": replacements["origin"].searched if "origin" in replacements else request.origin,
        "destination": replacements["destination"].searched if "destination" in replacements else request.destination,
        "destination_city": None if "destination" in replacements else request.destination_city,
    })
    second = ensure_availability_reason(_safe_search_flights(gateway, retry_request))
    # The adapter records what it was sent; the airport provenance replaces
    # that entry for every substituted side.
    merged = [
        replacements.get(entry.role, entry)
        for entry in (second.location_resolution or first.location_resolution or [])
    ]
    attempts = (first.search_attempts or 1) + (second.search_attempts or 1)
    http = None
    if first.provider_http_requests is not None or second.provider_http_requests is not None:
        http = (first.provider_http_requests or 0) + (second.provider_http_requests or 0)
    update: dict[str, Any] = {
        "location_resolution": merged,
        "search_attempts": attempts,
        "provider_http_requests": http,
        "warnings": [*second.warnings, *dict.fromkeys(unresolved_notes)],
    }
    if (
        second.availability_reason == FlightAvailabilityReason.SEARCH_COMPLETED_NO_OFFERS
        and not all(_has_commercial_evidence(entry) for entry in merged)
    ):
        # Codes alone are not enough: an airport without scheduled service can
        # have a code and still never have a flight (M2 Phase 2 live finding).
        update["availability_reason"] = FlightAvailabilityReason.LOCATION_UNCONFIRMED
        update["message"] = _UNVERIFIED_AIRPORT_NOTE
    elif second.availability_reason == FlightAvailabilityReason.LOCATION_UNCONFIRMED:
        update["message"] = (
            f"{second.message or ''} An airport with scheduled airline service was searched as well, with no offers."
        ).strip()
    return second.model_copy(update=update)


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
            availability_reason=FlightAvailabilityReason.PROVIDER_FAILED,
            origin=request.origin,
            destination=request.destination,
            departure_date=request.departure_date,
            return_date=request.return_date,
            adults=request.adults,
            children=request.children,
            currency=request.currency,
        )


flight_inventory_service = FlightInventoryService()
