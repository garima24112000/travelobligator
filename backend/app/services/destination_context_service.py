from __future__ import annotations

from typing import Any

from app.models.planning_state import (
    DailyWeather,
    DestinationContext,
    PlanningStage,
    PlanningState,
    WeatherContext,
)
from app.providers.gateway import ProviderGateway, provider_gateway
from app.services.base import PlanningStageService
from app.services.experience_planner_service import _matches_must_visit
from app.services.provider_coverage_service import ProviderCoverageService, provider_coverage_service


def _normalize_name(name: Any) -> str:
    return str(name or "").strip().lower()


class DestinationContextService(PlanningStageService):
    """Owns `destination_context`, `weather_context`, `provider_status`,
    `provider_coverage`, `unavailable_data`, and `data_sources_used`
    (docs/14_backend_architecture.md section 10).

    Consumes `trip_request` and `traveler_profile`. Reaches provider-backed
    data only through ProviderGateway, calling only the OSM PlacesProvider
    methods it actually implements: `search_attractions`,
    `search_restaurants`, `search_accommodation_pois`, `resolve_coordinates`,
    and (as a targeted fallback, see below) `search_must_visit_place`; and
    the WeatherProvider's `get_weather_forecast`.

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

    `candidate_pois`, `candidate_restaurants`, and
    `candidate_accommodation_pois` are filled with real OSM POIs when the
    places provider returns usable data. `candidate_accommodation_pois` are
    open-data location candidates only — never bookable inventory, and never
    given a price, availability, rating, or booking link. Neighborhood
    candidates and attraction clusters stay empty since no adapter implements
    those yet.

    After general attraction search, any must_visit term (from
    `traveler_profile` if present, else `trip_request`) not already matched
    by name in `candidate_pois` gets one targeted provider lookup via
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

    def run(self, planning_state: PlanningState) -> PlanningState:
        planning_state.set_active_stage(PlanningStage.DESTINATION_CONTEXT)

        destination_name = planning_state.trip_request.primary_destination

        attractions_response = self.gateway.places.search_attractions(destination_name)
        self.coverage_service.record_provider_result(planning_state, attractions_response, "places")

        restaurants_response = self.gateway.places.search_restaurants(destination_name)
        self.coverage_service.record_provider_result(
            planning_state, restaurants_response, "restaurants"
        )

        accommodation_response = self.gateway.places.search_accommodation_pois(destination_name)
        self.coverage_service.record_provider_result(
            planning_state, accommodation_response, "accommodations"
        )

        transit_response = self.gateway.routes.estimate_transit_feasibility(
            origin={"name": destination_name}, destination={"name": destination_name}
        )
        self.coverage_service.record_provider_result(planning_state, transit_response, "routes")

        weather_context = self._build_weather_context(planning_state, destination_name)
        planning_state.weather_context = weather_context

        candidate_pois = (
            [poi.model_dump(mode="json") for poi in attractions_response.data]
            if attractions_response.data
            else []
        )
        candidate_pois = self._append_must_visit_candidates(
            planning_state, destination_name, candidate_pois
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

        context = DestinationContext(
            destination_name=destination_name,
            candidate_pois=candidate_pois,
            candidate_restaurants=candidate_restaurants,
            candidate_accommodation_pois=candidate_accommodation_pois,
            provider_coverage=planning_state.provider_coverage.model_copy(),
            assumptions=assumptions,
            confidence=attractions_response.confidence if candidate_pois else 0.0,
        )

        planning_state.destination_context = context
        planning_state.touch()
        return planning_state

    def _build_weather_context(
        self, planning_state: PlanningState, destination_name: str
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
        coordinates = self.gateway.places.resolve_coordinates(destination_name)

        weather_response = self.gateway.weather.get_weather_forecast(
            destination_name,
            {
                "start_date": trip_request.start_date.isoformat(),
                "end_date": trip_request.end_date.isoformat(),
            },
            coordinates=coordinates,
        )
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

    def _append_must_visit_candidates(
        self,
        planning_state: PlanningState,
        destination_name: str,
        candidate_pois: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Targeted provider lookup fallback for must-visit places the
        general attraction search missed.

        For each must_visit term not already matched by name in
        `candidate_pois`, ask the places provider for that specific place
        (`"{must_visit_term}, {primary_destination}"`) instead of leaving it
        to PlanValidatorService's unmatched-must-visit warning. Only a real,
        named, coordinate-backed place returned by the provider is ever
        appended -- if the targeted lookup fails or finds nothing, no place
        is invented and the existing unmatched-must-visit warning behavior
        is unchanged. Duplicates are avoided both by `place_id` and by
        normalized name against every candidate already present, including
        ones appended earlier in this same loop.
        """
        traveler_profile = planning_state.traveler_profile
        must_visit_terms = (
            traveler_profile.must_visit
            if traveler_profile
            else planning_state.trip_request.must_visit
        )
        if not must_visit_terms:
            return candidate_pois

        seen_place_ids = {poi.get("place_id") for poi in candidate_pois if poi.get("place_id")}
        seen_names = {
            _normalize_name(poi.get("name")) for poi in candidate_pois if poi.get("name")
        }

        for term in must_visit_terms:
            if not term:
                continue

            already_matched = any(
                _matches_must_visit(poi, [term.lower()]) for poi in candidate_pois
            )
            if already_matched:
                continue

            response = self.gateway.places.search_must_visit_place(term, destination_name)
            if not response.data:
                continue

            for place in response.data:
                place_dict = place.model_dump(mode="json")
                place_id = place_dict.get("place_id")
                normalized_name = _normalize_name(place_dict.get("name"))
                if place_id in seen_place_ids or normalized_name in seen_names:
                    continue

                candidate_pois.append(place_dict)
                if place_id:
                    seen_place_ids.add(place_id)
                if normalized_name:
                    seen_names.add(normalized_name)

        return candidate_pois
