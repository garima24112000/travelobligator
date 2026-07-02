# Stay and Transport Decisions

## 1. Purpose

This stage decides where the traveler should stay and how they should move around.

It should not start by recommending accommodation.

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

- safety-related planning considerations when reliable data is available
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
    "It better supports comfort-focused and lower-friction planning for a parent-friendly trip.",
    "It has strong Metro connectivity.",
    "It offers good restaurant access.",
    "It keeps major sightseeing areas within reasonable travel time."
  ],
  "tradeoffs": [
    "Accommodation may cost slightly more than areas farther from the center."
  ],
  "confidence": 0.88,
  "alternatives": ["Capitol Hill", "Foggy Bottom"]
}
```

## 4. Transport Strategy

The system should decide the transport approach before planning daily movement.

It should answer:

- Should the traveler rely on public transport?
- Should they use rideshare/taxis?
- Should they rent a car?
- Should car rental be used only for day trips?
- Is walking realistic?
- Are late-night return options safe and practical?

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

Only after the stay area is selected should the system rank accommodation options.

Accommodation options may include:

* hotels
* motels
* hostels
* resorts
* serviced apartments
* vacation rentals or Airbnb-style stays
* guesthouses or boutique stays

The system should not assume that a hotel is always the best option.

The best accommodation type depends on:

* destination
* traveler budget
* travel group type
* comfort requirements
* length of stay
* preferred accommodation type
* safety-related planning considerations
* distance to planned attraction clusters
* transport access
* parking needs
* family/solo/couple suitability
* work-friendly needs
* amenities
* review/rating data when available
* availability and price data when available

For MVP, the system should recommend the best 5 accommodation options, not one final booking decision.

These recommendations should be ranked based on overall fit, not only price or rating.

Accommodation ranking should consider:

* selected neighborhood
* accommodation type suitability
* nightly price
* total estimated stay cost
* budget fit
* review score
* review count when available
* amenities
* distance to public transport
* distance to planned attraction clusters
* parking availability if needed
* family/couple/solo suitability
* quiet vs lively preference
* cancellation flexibility when available
* data freshness
* provider confidence

The output should not simply list properties.

Each accommodation option should explain:

* why it fits the traveler
* what tradeoffs it has
* what type of traveler it is best for
* which data sources were used
* how confident the system is

The system should not guarantee final price, availability, or booking completion unless confirmed by a production provider.

Example:

```json
{
  "name": "Example Central Stay",
  "accommodation_type": "hotel",
  "area": "Dupont Circle",
  "price_level": "mid_range",
  "estimated_price_per_night": 220,
  "why_it_fits": [
    "Within the recommended stay area.",
    "Close to public transport.",
    "Fits the traveler’s comfort and safety-related planning preferences.",
    "Good for travelers prioritizing convenience and lower daily travel time."
  ],
  "tradeoffs": [
    "Not the cheapest available option."
  ],
  "best_for": [
    "family travelers",
    "comfort-focused travelers",
    "first-time visitors"
  ],
  "booking_url": "...",
  "data_sources_used": [
    "accommodation_provider",
    "places_provider",
    "routes_provider"
  ],
  "confidence": 0.81
}
```

## 6. Stay + Transport Tradeoffs

This stage should clearly explain tradeoffs.

Examples:

- Staying central costs more but reduces transit time.
- Staying farther away is cheaper but increases daily travel.
- Renting a car gives flexibility but creates parking stress.
- Public transport is cheaper but may require more walking.
- Rideshare is convenient but may exceed budget if used heavily.

Tradeoffs are important because users often need help choosing between convenience, cost, and comfort.

## 7. Decision Card Pattern

Every major recommendation should be returned as a Decision Card.

Decision Card fields:

- title
- recommendation
- why
- tradeoffs
- confidence
- alternatives
- data_sources_used

Example:

```json
{
  "title": "Stay in a central, transit-connected area",
  "recommendation": "Choose an accommodation near Dupont Circle or Foggy Bottom.",
  "why": [
    "This reduces daily travel time.",
    "The area fits the comfort and safety priorities.",
    "Restaurants and transit are nearby."
  ],
  "tradeoffs": ["Central accommodation may be more expensive."],
  "confidence": 0.87,
  "alternatives": ["Stay farther out to reduce hoteaccommodation cost."],
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
      "name": "Example Central Stay",
      "accommodation_type": "hotel",
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
