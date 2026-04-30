from __future__ import annotations

from app.schemas.trips import AccommodationRecommendationSchema, AccommodationType


def get_mock_accommodation_recommendations(
    destination: str,
    accommodation_type: AccommodationType,
    traveler_count: int,
) -> list[AccommodationRecommendationSchema]:
    normalized_destination = destination.title()
    preferred_type = accommodation_type if accommodation_type != "no_preference" else "hotel"

    return [
        AccommodationRecommendationSchema(
            id=f"mock-stay-1-{normalized_destination.lower().replace(' ', '-')}",
            name=f"Mock Demo Stay Central {normalized_destination}",
            type=preferred_type,
            neighborhood=f"Central {normalized_destination}",
            nightlyPrice=180.0 + (traveler_count * 15),
            currency="USD",
            rating=4.6,
            bookingUrl="https://example.com/mock-booking/demo-stay-1",
            reasons=[
                "Mock/demo option near major attractions.",
                "Selected to keep the trip structure realistic for planning.",
            ],
            amenities=["wifi", "breakfast", "air_conditioning", "mock_demo_listing"],
            latitude=0.0,
            longitude=0.0,
            isMockData=True,
        ),
        AccommodationRecommendationSchema(
            id=f"mock-stay-2-{normalized_destination.lower().replace(' ', '-')}",
            name=f"Mock Demo Stay Boutique {normalized_destination}",
            type=preferred_type,
            neighborhood=f"Boutique Quarter, {normalized_destination}",
            nightlyPrice=220.0 + (traveler_count * 20),
            currency="USD",
            rating=4.8,
            bookingUrl="https://example.com/mock-booking/demo-stay-2",
            reasons=[
                "Mock/demo boutique-style option for higher comfort.",
                "Included to demonstrate ranking and comparison output.",
            ],
            amenities=["wifi", "concierge", "gym", "mock_demo_listing"],
            latitude=0.0,
            longitude=0.0,
            isMockData=True,
        ),
    ]