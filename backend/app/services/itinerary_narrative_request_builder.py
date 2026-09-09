from __future__ import annotations

from app.core.config import get_settings
from app.models.accommodation import AccommodationSearchStatus
from app.models.flight import FlightSearchStatus
from app.models.itinerary_narrative import (
    ItineraryNarrativeDayInput,
    ItineraryNarrativeExperienceInput,
    ItineraryNarrativeRequest,
)
from app.models.planning_state import PlanningState, WeatherContext
from app.models.routing import TravelTimeBufferStatus

# Step 182F: strict, read-only input builder for the itinerary narrator.
# `build_request` only ever reads `planning_state` -- it never calls a
# provider/LLM/network service, never mutates `planning_state`, and never
# includes a price, rating, review count, route/travel-time duration,
# booking link, availability flag, opening hour, or flight-schedule
# field anywhere in the `ItineraryNarrativeRequest` it returns. See that
# model's own docstring for the complete allow-list this builder is
# limited to.
#
# Every list is truncated to `Settings.itinerary_narrator_max_days`/
# `itinerary_narrator_max_items_per_day` for prompt-size safety --
# `ItineraryNarrativeRequest.truncated` is set `True` whenever any
# truncation actually happened, so a narrator provider (and, via
# `source_fields_used`, Developer view) can be honest about seeing only
# a prefix of the real plan.


class ItineraryNarrativeRequestBuilder:
    def build_request(self, planning_state: PlanningState) -> ItineraryNarrativeRequest:
        settings = get_settings()
        max_days = settings.itinerary_narrator_max_days
        max_items = settings.itinerary_narrator_max_items_per_day

        trip_request = planning_state.trip_request
        traveler_profile = planning_state.traveler_profile

        all_daily_plans = (
            planning_state.experience_plan.daily_plans if planning_state.experience_plan else []
        )
        truncated = len(all_daily_plans) > max_days

        movement_success_experience_ids = self._movement_success_experience_ids(planning_state)

        days: list[ItineraryNarrativeDayInput] = []
        for day_plan in all_daily_plans[:max_days]:
            if len(day_plan.experiences) > max_items:
                truncated = True
            if len(day_plan.restaurant_suggestions) > max_items:
                truncated = True

            experiences = [
                ItineraryNarrativeExperienceInput(
                    name=experience.name,
                    category=experience.category,
                    reason=experience.why_included,
                )
                for experience in day_plan.experiences[:max_items]
            ]
            restaurant_names = [
                restaurant.name for restaurant in day_plan.restaurant_suggestions[:max_items]
            ]
            has_movement_data = any(
                experience.experience_id in movement_success_experience_ids
                for experience in day_plan.experiences
            )

            days.append(
                ItineraryNarrativeDayInput(
                    day_number=day_plan.day_number,
                    date=day_plan.date,
                    experiences=experiences,
                    restaurant_names=restaurant_names,
                    has_movement_data=has_movement_data,
                )
            )

        stay_area_names: list[str] = []
        if planning_state.experience_plan is not None:
            anchors = planning_state.experience_plan.stay_area_guidance.suggested_anchor_accommodation_pois
            if len(anchors) > max_items:
                truncated = True
            stay_area_names = [poi.name for poi in anchors[:max_items]]

        accommodation_offer_count = 0
        accommodation_report = planning_state.accommodation_inventory_report
        if (
            accommodation_report is not None
            and accommodation_report.status == AccommodationSearchStatus.SUCCESS
        ):
            accommodation_offer_count = len(accommodation_report.offers)

        flight_offer_count = 0
        flight_report = planning_state.flight_inventory_report
        if flight_report is not None and flight_report.status == FlightSearchStatus.SUCCESS:
            flight_offer_count = len(flight_report.offers)

        weather_available = False
        weather_summary: str | None = None
        weather_context = planning_state.weather_context
        if weather_context is not None and weather_context.daily_weather:
            weather_summary = self._summarize_weather(weather_context)
            weather_available = weather_summary is not None

        validation_status: str | None = None
        warning_count = 0
        critical_issue_count = 0
        if planning_state.validation_report is not None:
            validation_status = planning_state.validation_report.readiness_status.value
            warning_count = len(planning_state.validation_report.warnings)
            critical_issue_count = len(planning_state.validation_report.critical_issues)

        unavailable_data_fields = [item.field for item in planning_state.unavailable_data]

        pace = traveler_profile.pace.value if traveler_profile else trip_request.pace.value
        interests = traveler_profile.interests if traveler_profile else trip_request.interests

        return ItineraryNarrativeRequest(
            destination=trip_request.primary_destination,
            start_date=trip_request.start_date,
            end_date=trip_request.end_date,
            travelers_count=trip_request.travelers_count,
            travel_group_type=trip_request.travel_group_type.value
            if trip_request.travel_group_type
            else None,
            pace=pace,
            interests=list(interests),
            days=days,
            stay_area_names=stay_area_names,
            accommodation_offer_count=accommodation_offer_count,
            flight_offer_count=flight_offer_count,
            weather_available=weather_available,
            weather_summary=weather_summary,
            validation_status=validation_status,
            warning_count=warning_count,
            critical_issue_count=critical_issue_count,
            unavailable_data_fields=unavailable_data_fields,
            truncated=truncated,
        )

    @staticmethod
    def _movement_success_experience_ids(planning_state: PlanningState) -> set[str]:
        """Experience ids that are the *start* of at least one leg whose
        real, provider-backed `TravelTimeBuffer.status == success` --
        never a distance/duration value itself, only whether real
        movement data exists for that day.
        """
        report = planning_state.travel_time_buffer_report
        if report is None:
            return set()
        return {
            buffer.from_experience_id
            for buffer in report.buffers
            if buffer.status == TravelTimeBufferStatus.SUCCESS
        }

    @staticmethod
    def _summarize_weather(weather_context: WeatherContext) -> str | None:
        """A plain, deterministic min/max range string computed from real
        `temperature_max_c`/`temperature_min_c` values only -- mirrors
        the frontend's own `summarizeWeatherForTravelerView` (Step 182D).
        Never a forecast, never an invented figure. Returns `None` when
        no usable numeric value exists, even if `daily_weather` itself is
        non-empty.
        """
        temperatures = [
            value
            for day in weather_context.daily_weather
            for value in (day.temperature_max_c, day.temperature_min_c)
            if value is not None
        ]
        if not temperatures:
            return None
        low = round(min(temperatures))
        high = round(max(temperatures))
        day_count = len(weather_context.daily_weather)
        return f"Around {low}-{high}°C over {day_count} day{'s' if day_count != 1 else ''}."


itinerary_narrative_request_builder = ItineraryNarrativeRequestBuilder()
