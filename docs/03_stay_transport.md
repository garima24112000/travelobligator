# Stay and Transport Decisions

## 1. Purpose

This stage decides where the traveler should stay and how they should move around.

It should not start by recommending hotels.

It should first answer:
- Which neighborhood or area best fits this traveler?
- What transport strategy makes sense for this destination?
- Only after that, which accommodations fit the selected area and budget?

The goal is to make stay and transport decisions explainable, not just searchable.

## 2. Inputs

Primary inputs:
- Traveler Profile
- Trip Strategy

Production data inputs:
- neighborhood data
- safety context
- public transport access
- attraction clusters
- accommodation availability
- accommodation prices
- route/travel time data
- parking and driving feasibility where available

## 3. Neighborhood Strategy

The system should recommend stay areas before recommending individual properties.

Neighborhoods should be scored using:
- safety
- proximity to planned attraction clusters
- public transport access
- restaurant access
- budget fit
- quiet vs lively preference
- family/solo/couple suitability
- parking availability if self-drive is likely
- airport/train station access when relevant

Example decision:

```json
{
  "title": "Stay near Dupont Circle",
  "recommendation": "Dupont Circle is the best stay area for this trip.",
  "why": [
    "It is safer and more comfortable for a parent-friendly trip.",
    "It has strong Metro connectivity.",
    "It offers good restaurant access.",
    "It keeps major sightseeing areas within reasonable travel time."
  ],
  "tradeoffs": [
    "Hotels may cost slightly more than areas farther from the center."
  ],
  "confidence": 0.88,
  "alternatives": ["Capitol Hill", "Foggy Bottom"]
}
```

## 4. Transport Strategy

The system should decide the transport approach before planning daily movement.

It should answer:

Should the traveler rely on public transport?
Should they use rideshare/taxis?
Should they rent a car?
Should car rental be used only for day trips?
Is walking realistic?
Are late-night return options safe and practical?

Transport should be evaluated using:

destination transport quality
traveler comfort
budget
group size
luggage needs
parking difficulty
attraction spread
safety at night
walking tolerance

Example:

```json
{
  "title": "Use Metro + rideshare instead of renting a car",
  "recommendation": "Use public transport for most city travel and rideshare when walking distance is high.",
  "why": [
    "Driving inside the city adds parking cost and traffic complexity.",
    "Metro access is good from the recommended stay area.",
    "Rideshare provides flexibility for evenings or tired travelers."
  ],
  "tradeoffs": [
    "Public transport may take longer during off-peak hours.",
    "Rideshare costs can increase at night."
  ],
  "confidence": 0.86,
  "alternatives": ["Taxi-only for comfort", "Rental car only for day trips"]
}
```

## 5. Accommodation Ranking

Only after the stay area is selected should the system rank accommodations.

Accommodation ranking should consider:

selected neighborhood
nightly price
total budget
review score
accommodation type preference
amenities
safety
distance to public transport
distance to planned attractions
parking availability if needed
family/couple/solo suitability

The output should not simply list hotels.

Each accommodation should explain why it fits.

Example:
```json
{
  "name": "Example Central Hotel",
  "area": "Dupont Circle",
  "price_level": "mid_range",
  "why_it_fits": [
    "Within the recommended stay area.",
    "Close to Metro access.",
    "Fits the selected hotel preference.",
    "Good for travelers prioritizing comfort and safety."
  ],
  "tradeoffs": [
    "Not the cheapest available option."
  ],
  "booking_url": "...",
  "confidence": 0.81
}
```

## 6. Stay + Transport Tradeoffs

This stage should clearly explain tradeoffs.

Examples:

Staying central costs more but reduces transit time.
Staying farther away is cheaper but increases daily travel.
Renting a car gives flexibility but creates parking stress.
Public transport is cheaper but may require more walking.
Rideshare is convenient but may exceed budget if used heavily.

Tradeoffs are important because users often need help choosing between convenience, cost, and comfort.

## 7. Decision Card Pattern

Every major recommendation should be returned as a Decision Card.

Decision Card fields:

title
recommendation
why
tradeoffs
confidence
alternatives
data_sources_used

Example:

```json
{
  "title": "Stay in a central, transit-connected area",
  "recommendation": "Choose a hotel near Dupont Circle or Foggy Bottom.",
  "why": [
    "This reduces daily travel time.",
    "The area fits the comfort and safety priorities.",
    "Restaurants and transit are nearby."
  ],
  "tradeoffs": [
    "Central hotels may be more expensive."
  ],
  "confidence": 0.87,
  "alternatives": [
    "Stay farther out to reduce hotel cost."
  ],
  "data_sources_used": [
    "places_provider",
    "routes_provider",
    "accommodation_provider"
  ]
}
```

## 8. Output Contract

The Stay + Transport stage should return:

```json
{
  "recommended_stay_area": {
    "name": "Dupont Circle",
    "score": 0.88,
    "decision_card": {}
  },
  "alternative_stay_areas": [
    {
      "name": "Capitol Hill",
      "score": 0.76,
      "reason": "Quieter and close to monuments, but less ideal for food access."
    }
  ],
  "transport_strategy": {
    "primary_mode": "metro",
    "secondary_mode": "rideshare",
    "car_rental_recommendation": "not_needed_inside_city",
    "decision_card": {}
  },
  "accommodation_recommendations": [
    {
      "name": "Example Central Hotel",
      "area": "Dupont Circle",
      "estimated_price_per_night": 220,
      "booking_url": "...",
      "score": 0.81,
      "why_it_fits": []
    }
  ],
  "tradeoffs": [],
  "confidence": 0.84
}
```

## 9. Design Rule

Stay and transport decisions should be made before the day-wise itinerary.

The itinerary should adapt to the recommended stay area and transport strategy, not the other way around.

