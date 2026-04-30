from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from app.providers.mock_accommodation_provider import get_mock_accommodation_recommendations
from app.providers.mock_poi_provider import get_mock_poi_candidates
from app.providers.mock_transport_provider import get_mock_transport_strategy
from app.schemas.trips import (
    BudgetBreakdownSchema,
    ItineraryActivitySchema,
    ItineraryDaySchema,
    ItinerarySchema,
    StayRecommendationSchema,
    TripGenerationMetadataSchema,
    TripGenerationResponseSchema,
    TripRequestSchema,
    TripSummarySchema,
    TravelFromPreviousSchema,
)


@dataclass(frozen=True)
class _DayTemplate:
    theme: str
    notes: list[str]


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _calculate_duration_days(start_date: str, end_date: str) -> int:
    start = _parse_date(start_date)
    end = _parse_date(end_date)
    return (end - start).days + 1


def _travel_style_for_request(trip_request: TripRequestSchema) -> str:
    pieces = [trip_request.pace, trip_request.travelGroupType, trip_request.accommodationType]
    return " / ".join(piece.replace("_", " ") for piece in pieces)


def _build_day_templates(duration_days: int, trip_request: TripRequestSchema) -> list[_DayTemplate]:
    templates: list[_DayTemplate] = []
    for day_number in range(1, duration_days + 1):
        if day_number == 1:
            templates.append(
                _DayTemplate(
                    theme="Arrival and orientation",
                    notes=["Keep the first day light to respect arrival energy.", "Mock/demo day structure."],
                )
            )
        elif day_number == duration_days:
            templates.append(
                _DayTemplate(
                    theme="Wrap-up and flexible exploration",
                    notes=["Leave room for last-minute adjustments.", "Mock/demo day structure."],
                )
            )
        elif any(interest.lower() == "food" for interest in trip_request.interests):
            templates.append(
                _DayTemplate(
                    theme="Food and neighborhood discovery",
                    notes=["Prioritize local dining and walkable exploration.", "Mock/demo day structure."],
                )
            )
        elif trip_request.pace == "packed":
            templates.append(
                _DayTemplate(
                    theme="High-activity sightseeing",
                    notes=["Use shorter transfers and tightly sequenced stops.", "Mock/demo day structure."],
                )
            )
        else:
            templates.append(
                _DayTemplate(
                    theme="Balanced city exploration",
                    notes=["Mix one anchor activity with open time.", "Mock/demo day structure."],
                )
            )
    return templates


def _format_time(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


def _build_activities_for_day(
    day_number: int,
    destination: str,
    poi_candidates,
    base_time_offset: int,
) -> tuple[list[ItineraryActivitySchema], int]:
    activities: list[ItineraryActivitySchema] = []
    current_hour = 9 + base_time_offset
    total_travel_minutes = 0

    selected_poi = poi_candidates[(day_number - 1) % len(poi_candidates)]
    second_poi = poi_candidates[day_number % len(poi_candidates)]

    for index, candidate in enumerate([selected_poi, second_poi], start=1):
        start = _format_time(current_hour, 0 if index == 1 else 45)
        end = _format_time(current_hour + 2, 0 if index == 1 else 15)
        travel = candidate.travelMinutesFromPrevious if index > 1 else 0
        total_travel_minutes += travel

        activities.append(
            ItineraryActivitySchema(
                id=str(uuid4()),
                name=f"{candidate.name} (Mock Demo)",
                category=candidate.category,
                startTime=start,
                endTime=end,
                durationMinutes=candidate.durationMinutes,
                latitude=candidate.latitude,
                longitude=candidate.longitude,
                whyIncluded=f"{candidate.whyIncluded} Planned for {destination.title()}.",
                travelFromPrevious=(
                    TravelFromPreviousSchema(
                        mode="walk/taxi mix",
                        timeMinutes=travel,
                        distanceKm=candidate.distanceKmFromPrevious,
                    )
                    if index > 1
                    else None
                ),
            )
        )
        current_hour += 3

    return activities, total_travel_minutes


def build_mock_itinerary(trip_request: TripRequestSchema) -> TripGenerationResponseSchema:
    duration_days = _calculate_duration_days(trip_request.startDate, trip_request.endDate)
    start_date = _parse_date(trip_request.startDate)
    accommodation_options = get_mock_accommodation_recommendations(
        trip_request.destination,
        trip_request.accommodationType,
        trip_request.travelersCount,
    )
    poi_candidates = get_mock_poi_candidates(trip_request.destination)
    transport_strategy = get_mock_transport_strategy(trip_request.pace, trip_request.destination)

    day_templates = _build_day_templates(duration_days, trip_request)
    daily_plan: list[ItineraryDaySchema] = []
    estimated_stay = 0.0
    estimated_food = 0.0
    estimated_transport = 0.0
    estimated_activities = 0.0
    estimated_misc = 0.0

    for day_index, template in enumerate(day_templates, start=1):
        activities, travel_minutes = _build_activities_for_day(
            day_index,
            trip_request.destination,
            poi_candidates,
            base_time_offset=1 if trip_request.pace == "relaxed" else 0,
        )
        day_date = (start_date + timedelta(days=day_index - 1)).isoformat()
        day_activity_cost = 70.0 + (15.0 * len(activities))
        day_food_cost = 45.0 + (5.0 * len(trip_request.interests))
        day_transport_cost = 18.0 + (4.0 * travel_minutes / 10.0)
        day_misc_cost = 22.0

        estimated_food += day_food_cost
        estimated_transport += day_transport_cost
        estimated_activities += day_activity_cost
        estimated_misc += day_misc_cost

        daily_plan.append(
            ItineraryDaySchema(
                dayNumber=day_index,
                date=day_date,
                baseCity=trip_request.destination.title(),
                theme=template.theme,
                activities=activities,
                foodSuggestions=[
                    "Mock/demo breakfast nearby the hotel area.",
                    "Mock/demo lunch around the planned activity zone.",
                ],
                notes=template.notes,
                totalTravelTimeMinutes=travel_minutes,
                estimatedCost=day_activity_cost + day_food_cost + day_transport_cost + day_misc_cost,
            )
        )

    stay_recommendation = StayRecommendationSchema(
        recommendedArea=f"Central {trip_request.destination.title()}",
        reasons=[
            "Mock/demo stays are positioned near the main activity cluster.",
            "The area balances access and itinerary simplicity for planning.",
        ],
        topAccommodations=accommodation_options,
    )

    estimated_stay = sum(option.nightlyPrice for option in accommodation_options[:1]) * duration_days
    estimated_total = estimated_stay + estimated_food + estimated_transport + estimated_activities + estimated_misc

    itinerary = ItinerarySchema(
        id=str(uuid4()),
        tripRequestId=trip_request.id,
        versionNumber=1,
        tripSummary=TripSummarySchema(
            destination=trip_request.destination.title(),
            durationDays=duration_days,
            travelStyle=_travel_style_for_request(trip_request),
            summaryText=(
                f"Mock/demo itinerary for {trip_request.destination.title()} with {duration_days} days of {trip_request.pace} travel."
            ),
        ),
        stayRecommendation=stay_recommendation,
        transportStrategy=transport_strategy,
        dailyPlan=daily_plan,
        importantTips=[
            "Mock/demo advice only: verify opening hours before finalizing real travel plans.",
            "Keep one flexible block per day for real-world adjustments.",
        ],
        alternatives=[
            f"Swap in a lower-paced day if the group wants a slower rhythm in {trip_request.destination.title()}.",
            "Use the alternative stay list if the budget changes before booking.",
        ],
        estimatedBudgetBreakdown=BudgetBreakdownSchema(
            stay=estimated_stay,
            food=estimated_food,
            transport=estimated_transport,
            activities=estimated_activities,
            misc=estimated_misc,
            total=estimated_total,
            currency="USD",
        ),
        isMockData=True,
    )

    return TripGenerationResponseSchema(
        tripRequest=trip_request,
        itinerary=itinerary,
        metadata=TripGenerationMetadataSchema(
            generatedAt=datetime.now(timezone.utc).isoformat(),
        ),
    )