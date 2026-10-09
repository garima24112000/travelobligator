from __future__ import annotations

from functools import partial
from typing import Any

from app.core.bounded_concurrency import CONTEXT_PROVIDER_BATCH_LIMIT, run_bounded
from app.models.providers import ProviderResponse
from app.models.planning_state import (
    CurrencyContext,
    DailyWeather,
    DestinationContext,
    Holiday,
    HolidayContext,
    PlanningStage,
    PlanningState,
    WeatherContext,
)
from app.providers.gateway import ProviderGateway, provider_gateway
from app.providers.holidays.nager_date_adapter import infer_country_code
from app.core import performance
from app.core.provider_usage import GenerationProviderContext
from app.services.base import PlanningStageService
from app.services.entity_collisions import apply_suspect_collisions
from app.services.pace_targets import pace_targets_for
from app.services.must_visit_matching import (
    MUST_VISIT_GROUNDING_VERSION,
    exact_name_candidates,
    record_grounded_term,
)
from app.services.provider_coverage_service import ProviderCoverageService, provider_coverage_service


class DestinationContextService(PlanningStageService):
    """Owns `destination_context`, `weather_context`, `holiday_context`,
    `currency_context`, `provider_status`, `provider_coverage`,
    `unavailable_data`, and `data_sources_used`
    (docs/14_backend_architecture.md section 10).

    Consumes `trip_request` and `traveler_profile`. Reaches provider-backed
    data only through ProviderGateway, calling only the OSM PlacesProvider
    methods it actually implements: `search_attractions`,
    `search_restaurants`, `search_accommodation_pois`, `resolve_coordinates`,
    and (as a targeted fallback, see below) `search_must_visit_place`; the
    WeatherProvider's `get_weather_forecast`; the HolidayProvider's
    `get_public_holidays`; and the CurrencyProvider's `get_exchange_rate`.

    `weather_context` is built from a real WeatherProvider call
    (Open-Meteo) using destination coordinates resolved via
    `gateway.places.resolve_coordinates` -- the exact same cached
    Nominatim geocoding the places searches above already use, never a
    second geocoding implementation. If coordinates can't be resolved, or
    Open-Meteo has no usable daily data for the trip's date range, this is
    reported honestly (`data_status`/`warnings`) rather than guessed. No
    weather description/condition, humidity, UV, alert, or severe-weather
    value is ever invented, and weather data is not yet used to adjust the
    itinerary.

    `holiday_context` is built from a real HolidayProvider call
    (Nager.Date) covering the trip's date range. The country code is
    conservatively inferred from the destination (`infer_country_code`, no
    LLM, no fuzzy guess) rather than a second geocoding call. If the
    country can't be inferred, or the provider has no usable data for the
    relevant year(s), this is reported honestly rather than guessed. If the
    provider has real data but none of it falls inside the trip's date
    range, `holidays` stays empty while `data_status` still reflects the
    successful response. No closure, crowd, opening-hour, event, festival,
    strike, or risk assessment is ever invented, and holiday data is not
    yet used to adjust the itinerary.

    `currency_context` is built from a real CurrencyProvider call
    (Frankfurter) using `trip_request.budget_currency` as the base currency
    (defaults to USD) and a destination currency conservatively inferred
    from the destination (`infer_destination_currency`, no LLM, no fuzzy
    guess). It only ever converts a single unit of currency; it never
    calculates or validates total trip cost or budget fit -- that stays
    not implemented. If the destination currency equals the base currency,
    this is still built honestly with `exchange_rate=1.0`. If the
    destination currency can't be inferred, or the provider has no usable
    rate, this is reported honestly rather than guessed. No trip cost,
    budget, hotel price, restaurant price, attraction price, fee, tax, or
    total-cost value is ever invented.

    `candidate_pois`, `candidate_restaurants`, and
    `candidate_accommodation_pois` are filled with real OSM POIs when the
    places provider returns usable data. `candidate_accommodation_pois` are
    open-data location candidates only — never bookable inventory, and never
    given a price, availability, rating, or booking link. Neighborhood
    candidates and attraction clusters stay empty since no adapter implements
    those yet.

    After general attraction search, any must_visit term (from
    `traveler_profile` if present, else `trip_request`) that exactly one
    `candidate_pois` entry does not already name gets one targeted provider lookup via
    `_append_must_visit_candidates` before scheduling ever runs, so a
    user's explicit must-visit place isn't missed just because it fell
    outside the general search. Only a real, named, coordinate-backed place
    the provider actually returns is appended -- never an invented one -- and
    if the lookup fails or finds nothing, PlanValidatorService's existing
    unmatched-must-visit warning still applies unchanged.
    """

    def __init__(
        self,
        gateway: ProviderGateway | None = None,
        coverage_service: ProviderCoverageService | None = None,
    ) -> None:
        self.gateway = gateway or provider_gateway
        self.coverage_service = coverage_service or provider_coverage_service

    def _places(self, provider_context: GenerationProviderContext | None) -> Any:
        """The places provider for THIS generation (Section 203C.2B): a view
        bound to the generation's usage tracker when a context is given."""
        places_for = getattr(self.gateway, "places_for", None)
        return places_for(provider_context) if callable(places_for) else self.gateway.places

    def run(
        self,
        planning_state: PlanningState,
        provider_context: GenerationProviderContext | None = None,
    ) -> PlanningState:
        planning_state.set_active_stage(PlanningStage.DESTINATION_CONTEXT)

        destination_name = planning_state.trip_request.primary_destination
        places = self._places(provider_context)

        # Section 203C.2B: a provider that sizes its own inventory is told
        # the trip-derived pool sizes (bounded retrieval, never "as many as
        # possible"); other providers are called exactly as before.
        attraction_filters: dict[str, Any] | None = None
        food_filters: dict[str, Any] | None = None
        if getattr(places, "supports_inventory_sizing", False):
            targets = pace_targets_for(planning_state)
            attraction_filters = {"pool_size": max(60, min(5 * targets.target_stops, 110))}
            food_filters = {"pool_size": max(20, min(8 * targets.trip_days, 40))}

        # Section 1A (measurement only): the `performance.stage` blocks in
        # this method only time what they enclose.
        with performance.stage("places_broad"):
            # Section 1B: a provider that can fetch the three broad searches'
            # requests as one bounded concurrent batch is asked to; it still
            # builds the three responses one after the other, in this order.
            # Every other provider is called three times, exactly as before.
            search_broad_inventory = getattr(places, "search_broad_inventory", None)
            if callable(search_broad_inventory):
                attractions_response, restaurants_response, accommodation_response = search_broad_inventory(
                    destination_name, attraction_filters, food_filters
                )
            else:
                attractions_response = (
                    places.search_attractions(destination_name, attraction_filters)
                    if attraction_filters is not None
                    else places.search_attractions(destination_name)
                )
                restaurants_response = (
                    places.search_restaurants(destination_name, food_filters)
                    if food_filters is not None
                    else places.search_restaurants(destination_name)
                )
                accommodation_response = places.search_accommodation_pois(destination_name)
            self.coverage_service.record_provider_result(planning_state, attractions_response, "places")
            self.coverage_service.record_provider_result(
                planning_state, restaurants_response, "restaurants"
            )
            self.coverage_service.record_provider_result(
                planning_state, accommodation_response, "accommodations"
            )

        transit_response = self.gateway.routes.estimate_transit_feasibility(
            origin={"name": destination_name}, destination={"name": destination_name}
        )
        self.coverage_service.record_provider_result(planning_state, transit_response, "routes")

        # Section 1B: the weather, holiday and currency providers are
        # independent of each other, so their three requests are fetched as
        # one bounded concurrent batch. Each context is then built from its
        # own response, in the original order; a provider that raises still
        # raises here, at the point where it was called before.
        trip_request = planning_state.trip_request
        trip_dates = {
            "start_date": trip_request.start_date.isoformat(),
            "end_date": trip_request.end_date.isoformat(),
        }
        with performance.stage("weather_holiday"):
            coordinates = places.resolve_coordinates(destination_name)
            weather_outcome, holiday_outcome, currency_outcome = run_bounded(
                "context_providers",
                [
                    partial(
                        self.gateway.weather.get_weather_forecast,
                        destination_name,
                        dict(trip_dates),
                        coordinates=coordinates,
                    ),
                    partial(self.gateway.holiday.get_public_holidays, destination_name, dict(trip_dates)),
                    partial(
                        self.gateway.currency.get_exchange_rate, trip_request.budget_currency, destination_name
                    ),
                ],
                CONTEXT_PROVIDER_BATCH_LIMIT,
            )
            weather_context = self._build_weather_context(planning_state, destination_name, weather_outcome.unwrap())
            planning_state.weather_context = weather_context

            holiday_context = self._build_holiday_context(planning_state, destination_name, holiday_outcome.unwrap())
            planning_state.holiday_context = holiday_context

        with performance.stage("currency"):
            currency_context = self._build_currency_context(
                planning_state, destination_name, currency_outcome.unwrap()
            )
            planning_state.currency_context = currency_context

        candidate_pois = (
            [poi.model_dump(mode="json") for poi in attractions_response.data]
            if attractions_response.data
            else []
        )
        with performance.stage("must_visit_grounding"):
            candidate_pois, ungrounded_must_visits = self._append_must_visit_candidates(
                planning_state, destination_name, candidate_pois, places=places
            )
        candidate_restaurants = (
            [poi.model_dump(mode="json") for poi in restaurants_response.data]
            if restaurants_response.data
            else []
        )
        # Open-data location candidates only. Do not attach price, availability,
        # rating, or booking link fields to these — OSM does not supply them.
        candidate_accommodation_pois = (
            [poi.model_dump(mode="json") for poi in accommodation_response.data]
            if accommodation_response.data
            else []
        )

        assumptions = [
            "Neighborhood candidates and attraction clusters could not be generated "
            "because no provider for that data is connected yet.",
            "Accommodation POI candidates are open-data location candidates from "
            "OpenStreetMap only, not bookable inventory. They have no price, "
            "availability, rating, or booking link.",
        ]
        if not candidate_pois:
            assumptions.insert(
                0,
                "Candidate points of interest could not be generated because the places "
                "provider returned no usable attraction data for this destination.",
            )
        if not candidate_restaurants:
            assumptions.append(
                "Candidate restaurants could not be generated because the places "
                "provider returned no usable restaurant data for this destination."
            )
        if not candidate_accommodation_pois:
            assumptions.append(
                "Candidate accommodation location POIs could not be generated because "
                "the places provider returned no usable accommodation data for this "
                "destination."
            )
        # Section 203C.2B: an ungroundable must-visit is disclosed, never
        # fabricated, and the rest of the plan continues.
        for term in ungrounded_must_visits:
            assumptions.append(
                f"Must-visit '{term}' could not be matched to a verified place in this "
                "destination, so it was not scheduled."
            )

        # Section 202B.2 (Task 33): show how the provider interpreted the
        # destination text (only when the places provider can say).
        describe = getattr(places, "describe_destination", None)
        resolved_destination = describe(destination_name) if callable(describe) else None

        context = DestinationContext(
            destination_name=destination_name,
            resolved_destination=resolved_destination or None,
            destination_resolution=(
                "unresolved"
                if attractions_response.failure_reason == "destination_unresolved"
                else None
            ),
            must_visit_grounding_version=MUST_VISIT_GROUNDING_VERSION,
            candidate_pois=candidate_pois,
            candidate_restaurants=candidate_restaurants,
            candidate_accommodation_pois=candidate_accommodation_pois,
            provider_coverage=planning_state.provider_coverage.model_copy(),
            assumptions=assumptions,
            confidence=attractions_response.confidence if candidate_pois else 0.0,
        )
        # Section 203C.2B: suspected duplicate candidates the provider could
        # not resolve are recorded, and marked so they are never scheduled
        # together.
        apply_suspect_collisions(context, provider_context)

        planning_state.destination_context = context
        planning_state.touch()
        return planning_state

    def _build_weather_context(
        self, planning_state: PlanningState, destination_name: str, weather_response: ProviderResponse[Any]
    ) -> WeatherContext:
        """Plan-level provider-backed weather forecast for the trip's date
        range (docs/12_provider_architecture.md section 15).

        Resolves real coordinates via `gateway.places.resolve_coordinates`
        (the same cached Nominatim geocoding `_search` already uses -- never
        a second geocoding implementation) and asks the WeatherProvider
        (Open-Meteo) for a daily forecast over `trip_request.start_date`..
        `trip_request.end_date`. If coordinates can't be resolved or the
        provider has no usable daily data, `daily_weather` stays empty and
        this is reported honestly via `data_status`/`warnings` -- never
        guessed. No weather description/condition, humidity, UV, alert, or
        severe-weather value is ever invented.
        """
        trip_request = planning_state.trip_request
        self.coverage_service.record_provider_result(planning_state, weather_response, "weather")

        daily_weather = (
            [
                DailyWeather(
                    date=day.date,
                    temperature_max_c=day.temperature_max_c,
                    temperature_min_c=day.temperature_min_c,
                    precipitation_probability_max=day.precipitation_probability_max,
                    precipitation_sum_mm=day.precipitation_sum_mm,
                    weather_code=day.weather_code,
                    source=day.source,
                    data_status=day.data_status,
                )
                for day in weather_response.data
            ]
            if weather_response.data
            else []
        )

        assumptions = [
            "Weather data is provider-backed daily forecast only; it is not used to "
            "adjust the itinerary (e.g. rerouting or rescheduling around rain) -- that "
            "reasoning is not implemented yet."
        ]
        warnings: list[str] = []
        if not daily_weather:
            warnings.append(
                weather_response.message
                or "No usable weather forecast data is available for this destination "
                "and date range."
            )

        return WeatherContext(
            destination=destination_name,
            start_date=trip_request.start_date,
            end_date=trip_request.end_date,
            daily_weather=daily_weather,
            source=weather_response.provider_name if daily_weather else None,
            data_status=weather_response.data_status,
            confidence=weather_response.confidence,
            assumptions=assumptions,
            warnings=warnings,
        )

    def _build_holiday_context(
        self, planning_state: PlanningState, destination_name: str, holiday_response: ProviderResponse[Any]
    ) -> HolidayContext:
        """Plan-level provider-backed public holiday context for the trip's
        date range (docs/12_provider_architecture.md section 16).

        Asks the HolidayProvider (Nager.Date) for public holidays covering
        `trip_request.start_date`..`trip_request.end_date`. The adapter
        itself conservatively infers a country code from `destination_name`
        (`infer_country_code` -- no LLM, no fuzzy guess); this method calls
        the same pure function directly (a cheap local string mapping, not
        a provider call) only to populate `HolidayContext.country_code` for
        display. If the country can't be inferred, or the provider has no
        usable data at all, `holidays` stays empty and this is reported
        honestly via `data_status`/`warnings`. If the provider has real
        data for the relevant year(s) but none of it falls inside the trip
        dates, `holidays` stays empty while `data_status` still reflects
        the successful response, noted honestly via `assumptions` instead.
        No closure, crowd, opening-hour, event, festival, strike, or risk
        assessment is ever invented.
        """
        trip_request = planning_state.trip_request
        self.coverage_service.record_provider_result(planning_state, holiday_response, "holidays")

        provider_succeeded = holiday_response.data is not None
        holidays = (
            [
                Holiday(
                    date=holiday.date,
                    local_name=holiday.local_name,
                    name=holiday.name,
                    country_code=holiday.country_code,
                    is_global=holiday.is_global,
                    counties=holiday.counties,
                    types=holiday.types,
                    source=holiday.source,
                    data_status=holiday.data_status,
                )
                for holiday in holiday_response.data
            ]
            if provider_succeeded
            else []
        )

        assumptions = [
            "Public holidays are provider-backed calendar data only; they are not used "
            "to infer closures, crowds, opening hours, events, festivals, strikes, or "
            "risk -- that reasoning is not implemented yet."
        ]
        warnings: list[str] = []
        if provider_succeeded and not holidays:
            assumptions.append(
                "No public holidays from provider data fall within this trip date range."
            )
        if not provider_succeeded:
            warnings.append(
                holiday_response.message
                or "No usable public holiday data is available for this destination "
                "and date range."
            )

        return HolidayContext(
            destination=destination_name,
            start_date=trip_request.start_date,
            end_date=trip_request.end_date,
            country_code=infer_country_code(destination_name),
            holidays=holidays,
            source=holiday_response.provider_name if provider_succeeded else None,
            data_status=holiday_response.data_status,
            confidence=holiday_response.confidence,
            assumptions=assumptions,
            warnings=warnings,
        )

    def _build_currency_context(
        self,
        planning_state: PlanningState,
        destination_name: str,
        exchange_rate_response: ProviderResponse[Any],
    ) -> CurrencyContext:
        """Plan-level provider-backed currency exchange-rate context for
        the trip (docs/12_provider_architecture.md section 17).

        Uses `trip_request.budget_currency` as the base currency (defaults
        to USD) and asks the CurrencyProvider (Frankfurter) for the
        exchange rate to a destination currency the adapter itself
        conservatively infers from `destination_name` (no LLM, no fuzzy
        guess). This only ever converts a single unit of currency; it never
        calculates or validates total trip cost or budget fit. If the
        destination currency can't be inferred, or the provider has no
        usable rate, this is reported honestly via `data_status`/
        `warnings` rather than guessed. No trip cost, budget, hotel price,
        restaurant price, attraction price, fee, tax, or total-cost value
        is ever invented.
        """
        base_currency = planning_state.trip_request.budget_currency
        self.coverage_service.record_provider_result(
            planning_state, exchange_rate_response, "currency"
        )

        rate_data = exchange_rate_response.data

        assumptions = [
            "Currency context is a provider-backed single-unit exchange rate only; it "
            "does not calculate or validate total trip cost, budget fit, hotel prices, "
            "restaurant prices, attraction prices, fees, or tax -- that reasoning is "
            "not implemented yet."
        ]
        warnings: list[str] = []
        if rate_data is None:
            warnings.append(
                exchange_rate_response.message
                or "No usable exchange-rate data is available for this destination."
            )
        elif rate_data.exchange_rate == 1.0 and rate_data.destination_currency == base_currency:
            assumptions.append(
                f"The destination currency matches the base currency ({base_currency}), "
                "so no conversion is needed."
            )

        return CurrencyContext(
            base_currency=base_currency,
            destination_currency=rate_data.destination_currency if rate_data else None,
            exchange_rate=rate_data.exchange_rate if rate_data else None,
            rate_date=rate_data.rate_date if rate_data else None,
            source=exchange_rate_response.provider_name if rate_data else None,
            data_status=exchange_rate_response.data_status,
            confidence=exchange_rate_response.confidence,
            assumptions=assumptions,
            warnings=warnings,
        )

    def _append_must_visit_candidates(
        self,
        planning_state: PlanningState,
        destination_name: str,
        candidate_pois: list[dict[str, Any]],
        places: Any = None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Establishes which PROVIDER IDENTITY each must-visit term refers to
        (Q1). Returns `(candidate_pois, ungrounded_terms)`.

        This is the only place a term becomes an identity; it runs BEFORE
        candidate ranking and records the user's own term on the provider
        candidate (`record_grounded_term`: `must_visit_terms`, plus
        `must_visit_term` for older readers). Every later stage reads that
        record and never compares a place name with a term.

        Per term, in the user's order:

          * exactly ONE broad-pool candidate has the term as its exact
            comparable provider name -> that candidate is recorded and no
            lookup is made;
          * none has it (a longer or related name is NOT a match), or
            several have it (ambiguous) -> one targeted provider lookup
            (`"{must_visit_term}, {primary_destination}"`), and only the
            place the provider returns is recorded -- on the pool candidate
            with that same provider `place_id`, otherwise appended once
            under its own id (never merged into a same-named candidate).

        Several terms the provider resolves to one place are all recorded on
        that one candidate. A term the provider cannot ground (no result, or
        a result without a provider id) is returned in `ungrounded_terms` and
        disclosed -- never fabricated, never guessed from a name -- and the
        existing unmatched-must-visit warning applies.
        """
        traveler_profile = planning_state.traveler_profile
        must_visit_terms = (
            traveler_profile.must_visit
            if traveler_profile
            else planning_state.trip_request.must_visit
        )
        if not must_visit_terms:
            return candidate_pois, []

        places = places or self.gateway.places
        ungrounded_terms: list[str] = []
        terms = list(dict.fromkeys(term for term in must_visit_terms if term and term.strip()))

        def unique_exact_match(term: str) -> dict[str, Any] | None:
            """The ONE pool candidate the provider names exactly as the user
            did, or None when there is none or the name is ambiguous."""
            exact = exact_name_candidates(candidate_pois, term)
            return exact[0] if len(exact) == 1 else None

        # Section 1B: the lookups of the terms the broad pool does not
        # uniquely identify are independent, so a provider that can fetch
        # several named places as one bounded concurrent batch is asked to.
        # The responses are then applied below in the user's own order.
        search_many = getattr(places, "search_must_visit_places", None)
        prefetched: dict[str, ProviderResponse[Any]] = {}
        if callable(search_many):
            unmatched = [term for term in terms if unique_exact_match(term) is None]
            if len(unmatched) > 1:
                for term, response in zip(unmatched, search_many(unmatched, destination_name)):
                    if isinstance(response, Exception):
                        raise response
                    prefetched[term] = response

        for term in terms:
            exact = unique_exact_match(term)
            if exact is not None:
                # The provider itself names exactly one place with the user's
                # words: that provider identity is the must-visit.
                record_grounded_term(exact, term)
                continue

            # No exact name, or several places share it (ambiguous): the
            # provider's own targeted lookup decides. Nothing is tagged by name.
            response = prefetched.pop(term, None) or places.search_must_visit_place(term, destination_name)
            grounded = False
            for place in response.data or []:
                place_dict = place.model_dump(mode="json")
                place_id = place_dict.get("place_id")
                if not place_id:
                    continue  # no provider identity to record the term on
                target = next((poi for poi in candidate_pois if poi.get("place_id") == place_id), None)
                if target is None:
                    # A provider identity the pool does not hold: appended
                    # once, as itself. It is never merged into another
                    # candidate by name -- whether two provider records are
                    # one real place is the entity-collision layer's call.
                    candidate_pois.append(place_dict)
                    target = place_dict
                # A second user term that the provider resolves to an already
                # grounded place joins it: one candidate, one slot, both terms.
                record_grounded_term(target, term)
                grounded = True
                break
            if not grounded:
                ungrounded_terms.append(term)

        return candidate_pois, ungrounded_terms
