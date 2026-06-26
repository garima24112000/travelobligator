from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MockPoiCandidate:
    name: str
    category: str
    latitude: float
    longitude: float
    durationMinutes: int
    travelMinutesFromPrevious: int
    distanceKmFromPrevious: float
    whyIncluded: str
    foodSuggestions: list[str]
    notes: list[str]


def get_mock_poi_candidates(destination: str) -> list[MockPoiCandidate]:
    city = destination.title()
    return [
        MockPoiCandidate(
            name=f"Mock Demo Old Town Walk - {city}",
            category="sightseeing",
            latitude=0.01,
            longitude=0.01,
            durationMinutes=120,
            travelMinutesFromPrevious=0,
            distanceKmFromPrevious=0.0,
            whyIncluded="Mock/demo landmark walk to anchor the first day.",
            foodSuggestions=["Local cafe breakfast", "Casual lunch nearby"],
            notes=["Best for a light arrival day.", "Mock/demo activity."],
        ),
        MockPoiCandidate(
            name=f"Mock Demo Market & Food District - {city}",
            category="food",
            latitude=0.02,
            longitude=0.02,
            durationMinutes=150,
            travelMinutesFromPrevious=20,
            distanceKmFromPrevious=3.2,
            whyIncluded="Mock/demo area for local food and flexible exploration.",
            foodSuggestions=["Street food", "Regional specialties"],
            notes=["Good fit for food-led planning.", "Mock/demo activity."],
        ),
        MockPoiCandidate(
            name=f"Mock Demo Scenic Viewpoint - {city}",
            category="nature",
            latitude=0.03,
            longitude=0.03,
            durationMinutes=90,
            travelMinutesFromPrevious=15,
            distanceKmFromPrevious=2.4,
            whyIncluded="Mock/demo scenic stop to add variety to the itinerary.",
            foodSuggestions=["Picnic snacks", "Coffee stop"],
            notes=["Pairs well with a relaxed pace.", "Mock/demo activity."],
        ),
    ]
