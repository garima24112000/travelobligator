# Data Model

## 1. Purpose

This document defines the core data objects used by TravelObligator.

The goal is to convert the product architecture into implementation-ready structures.

The data model should make it clear:

* what objects exist
* which stage owns each object
* what fields each object should contain
* which fields are user-provided
* which fields are provider-backed
* which fields are open-data-backed
* which fields are AI-inferred
* which fields may be unavailable
* how confidence, provider coverage, and assumptions are represented

This document should guide both:

* backend Pydantic models
* frontend TypeScript interfaces

---

## 2. Naming Convention

TravelObligator should use `snake_case` for API JSON fields.

Reason:

* backend is FastAPI/Python
* existing architecture docs use `snake_case`
* it avoids unnecessary frontend-backend translation

Example:

```json
{
  "planning_state_id": "ps_001",
  "trip_request": {},
  "traveler_profile": {}
}
```

Frontend TypeScript types may also use `snake_case` to match API contracts directly.

---

## 3. Core Enums

### DestinationScope

```text
single_city
multi_city_future
```

For MVP:

```text
single_city
```

---

### DataStatus

Used to describe the status of a specific data field.

```text
live
cached
fallback_used
estimated
scheduled
user_provided
ai_inferred
unavailable
failed
not_connected
```

---

### ProviderStatus

Used to describe provider request state.

```text
not_requested
success
retrying
fallback_used
partial
failed
unavailable
not_connected
```

---

### ClaimSourceType

Used to distinguish facts from reasoning.

```text
provider_fact
open_data_fact
user_input
system_rule
ai_inference
assumption
unavailable_data
```

---

### ReadinessStatus

Used by the Plan Validator.

```text
ready
needs_review
blocked
```

---

### ValidationSeverity

```text
critical
warning
suggestion
```

---

### RegenerationStrategy

Used by the Feedback Pipeline.

```text
explanation_only
section_level_update
day_level_update
pipeline_level_update
full_regeneration
```

---

### AccommodationType

```text
hotel
motel
hostel
resort
serviced_apartment
vacation_rental
guesthouse
boutique_stay
airbnb_style
unknown
```

Accommodation types should only be shown when supported by a legitimate connected source or open-data source.

---

### RecommendationType

```text
attraction
restaurant
meal_area
accommodation
transport
stay_area
route
warning
summary
```

---

## 4. Shared Supporting Objects

## SourceAttribution

Used to identify where a field or recommendation came from.

```json
{
  "source_name": "openstreetmap",
  "source_type": "open_data",
  "data_status": "cached",
  "retrieved_at": "2026-07-03T18:00:00Z",
  "freshness": "7_days",
  "provider_verified": true
}
```

Allowed `source_type` values:

```text
user_input
provider
open_data
system_rule
ai_reasoning
assumption
unavailable
```

---

## DataQuality

Used for confidence and missing-data handling.

```json
{
  "confidence": 0.82,
  "data_status": "live",
  "missing_fields": [],
  "assumptions": [],
  "warnings": []
}
```

---

## GeoPoint

```json
{
  "lat": 38.8895,
  "lng": -77.0353
}
```

---

## MoneyAmount

```json
{
  "amount": 220,
  "currency": "USD",
  "data_status": "live",
  "source": "accommodation_provider",
  "confidence": 0.86
}
```

If price is unavailable:

```json
{
  "amount": null,
  "currency": "USD",
  "data_status": "unavailable",
  "source": null,
  "confidence": 0.0
}
```

---

## TimeWindow

```json
{
  "start_time": "09:30",
  "end_time": "20:00",
  "timezone": "America/New_York"
}
```

---

## ClaimSource

Used inside explanation cards.

```json
{
  "claim": "This restaurant is near the previous activity.",
  "source_type": "provider_fact",
  "source": "places_provider",
  "based_on": ["coordinates", "route_distance"]
}
```

---

## ProviderStatusEntry

```json
{
  "provider_name": "openstreetmap",
  "status": "success",
  "fallback_used": false,
  "last_checked_at": "2026-07-03T18:00:00Z",
  "unavailable_fields": [],
  "message": null
}
```

---

## ProviderCoverage

```json
{
  "places": "available",
  "routes": "available",
  "restaurants": "open_data_available",
  "accommodations": "open_poi_available",
  "hotel_prices": "provider_available",
  "vacation_rentals": "not_connected",
  "airbnb": "not_connected",
  "flights": "not_enabled",
  "weather": "available"
}
```

Provider coverage should make clear what was searched, what was not connected, and what data was unavailable.

---

## 5. TripRequest

`TripRequest` stores the user’s original structured trip request.

Owned by:

```text
Trip creation endpoint
```

Example:

```json
{
  "destination_scope": "single_city",
  "primary_destination": {
    "city": "Washington DC",
    "country": "United States"
  },
  "destination_list": [
    {
      "city": "Washington DC",
      "country": "United States",
      "start_date": "2026-08-10",
      "end_date": "2026-08-13"
    }
  ],
  "origin_city": "New York",
  "start_date": "2026-08-10",
  "end_date": "2026-08-13",
  "travelers_count": 3,
  "travel_group_type": "family",
  "budget_range": {
    "min": 1500,
    "max": 2500,
    "currency": "USD"
  },
  "accommodation_preference": ["hotel", "serviced_apartment"],
  "transport_preference": ["public_transport", "rideshare"],
  "interests": ["food", "culture", "scenic_views"],
  "must_visit_places": [],
  "must_avoid_places": [],
  "constraints": [],
  "free_text_preferences": "Traveling with parents, not too much walking.",
  "itinerary_intensity_scale": 2
}
```

---

## 6. TravelerProfile

`TravelerProfile` stores the structured interpretation of the traveler.

Owned by:

```text
Traveler Profile stage
```

Core fields:

```json
{
  "basic_context": {},
  "travel_intent": {},
  "budget_profile": {},
  "mobility_profile": {},
  "interest_profile": {},
  "stay_profile": {},
  "transport_profile": {},
  "avoidance_profile": {},
  "decision_weights": {},
  "confidence": {}
}
```

Important rule:

Raw form input should not be used by later stages if the same information exists in `traveler_profile`.

---

## 7. DestinationContext

`DestinationContext` stores provider-backed or open-data-backed destination information.

Owned by:

```text
Destination Context Builder
```

Example:

```json
{
  "destination": {
    "city": "Washington DC",
    "country": "United States",
    "coordinates": {
      "lat": 38.9072,
      "lng": -77.0369
    }
  },
  "candidate_poi_clusters": [],
  "neighborhood_candidates": [],
  "rough_transport_feasibility": {},
  "average_cost_hints": {},
  "provider_coverage": {},
  "data_sources_used": [],
  "confidence": 0.78
}
```

Important rule:

Destination Context provides candidate information.

It should not choose the final itinerary.

---

## 8. TripStrategy

`TripStrategy` stores the high-level direction of the trip.

Owned by:

```text
Trip Strategy stage
```

Example:

```json
{
  "destination_suitability": {
    "score": 0.88,
    "label": "high",
    "reasons": []
  },
  "duration_assessment": {
    "label": "enough",
    "recommended_days": 3,
    "reason": ""
  },
  "budget_assessment": {
    "label": "realistic",
    "risk_level": "low",
    "reason": ""
  },
  "recommended_trip_style": "relaxed scenic cultural trip",
  "planning_strategy": [],
  "planning_targets": {
    "preferred_activities_per_day": 3,
    "max_activities_per_day": 4,
    "max_walking_km_per_day": 8,
    "preferred_sightseeing_start_time": "09:30",
    "preferred_sightseeing_end_time": "20:00",
    "meal_break_expectations": "reserve lunch and dinner windows",
    "buffer_level": "medium",
    "experience_mix_targets": {
      "culture": 0.35,
      "food": 0.25,
      "scenic": 0.25,
      "shopping": 0.15
    }
  },
  "tradeoffs": [],
  "assumptions": [],
  "confidence": 0.82
}
```

Important rule:

Trip Strategy defines planning direction.

It should not select final attractions, restaurants, or accommodation options.

---

## 9. StayTransportDecision

`StayTransportDecision` stores stay area, accommodation recommendations, and transport strategy.

Owned by:

```text
Stay + Transport stage
```

Example:

```json
{
  "recommended_stay_area": {},
  "alternative_stay_areas": [],
  "transport_strategy": {},
  "accommodation_recommendations": [],
  "decision_cards": [],
  "tradeoffs": [],
  "provider_coverage": {},
  "confidence": 0.84
}
```

---

## 10. StayArea

```json
{
  "area_id": "area_dupont_circle",
  "name": "Dupont Circle",
  "score": 0.88,
  "coordinates": {},
  "why_it_fits": [],
  "tradeoffs": [],
  "data_sources_used": [],
  "confidence": 0.88
}
```

Stay area recommendations should be based on:

* destination context
* traveler profile
* transport feasibility
* attraction clusters
* safety-related planning considerations
* provider coverage

---

## 11. AccommodationOption

```json
{
  "accommodation_id": "stay_001",
  "name": "Example Central Stay",
  "accommodation_type": "hotel",
  "area": "Dupont Circle",
  "coordinates": {},
  "estimated_price_per_night": {
    "amount": 220,
    "currency": "USD",
    "data_status": "live",
    "source": "accommodation_provider",
    "confidence": 0.86
  },
  "availability_status": {
    "data_status": "live",
    "available": true,
    "source": "accommodation_provider",
    "confidence": 0.86
  },
  "rating": {
    "value": 4.4,
    "review_count": 1200,
    "data_status": "live",
    "source": "accommodation_provider",
    "confidence": 0.86
  },
  "amenities": [],
  "booking_url": {
    "url": "",
    "data_status": "live",
    "source": "approved_provider"
  },
  "score": 0.81,
  "why_it_fits": [],
  "tradeoffs": [],
  "best_for": [],
  "data_sources_used": [],
  "unavailable_fields": [],
  "confidence": 0.81
}
```

If only OpenStreetMap accommodation POI data is available:

```json
{
  "name": "Real Accommodation Name",
  "accommodation_type": "hotel",
  "estimated_price_per_night": {
    "amount": null,
    "data_status": "unavailable"
  },
  "availability_status": {
    "available": null,
    "data_status": "unavailable"
  },
  "rating": {
    "value": null,
    "review_count": null,
    "data_status": "unavailable"
  },
  "confidence": 0.55
}
```

Important rule:

The MVP recommends accommodation options.

It does not guarantee final booking, price, or availability.

---

## 12. TransportStrategy

```json
{
  "primary_mode": "public_transport",
  "secondary_mode": "rideshare",
  "car_rental_recommendation": "not_needed_inside_city",
  "late_night_transport_note": "",
  "luggage_consideration": "",
  "tradeoffs": [],
  "data_sources_used": [],
  "confidence": 0.86
}
```

---

## 13. ExperiencePlan

`ExperiencePlan` stores the selected experiences and day-wise itinerary proposal.

Owned by:

```text
Experience Planner stage
```

Example:

```json
{
  "trip_overview": {},
  "daily_plan": [],
  "experience_cards": [],
  "decision_cards": [],
  "estimated_budget": {},
  "estimated_walking": {},
  "provider_coverage": {},
  "planning_metadata": {},
  "confidence": 0.8
}
```

---

## 14. DailyPlan

```json
{
  "day": 1,
  "date": "2026-08-10",
  "theme": "Arrival + Local Exploration",
  "goal": "Keep the first day light and close to the stay area.",
  "activities": [],
  "meal_plan": [],
  "transport_notes": [],
  "estimated_walking_km": 5.2,
  "estimated_travel_time_minutes": 48,
  "estimated_cost": {
    "amount": 85,
    "currency": "USD",
    "data_status": "estimated",
    "source": "system_calculation",
    "confidence": 0.65
  },
  "energy_level": "moderate",
  "warnings": []
}
```

---

## 15. ExperienceItem

```json
{
  "experience_id": "exp_001",
  "name": "Lincoln Memorial",
  "category": "historical",
  "recommendation_type": "attraction",
  "coordinates": {},
  "start_time": "10:00",
  "estimated_duration_minutes": {
    "value": 75,
    "data_status": "estimated",
    "source": "system_default",
    "confidence": 0.65
  },
  "opening_hours": {
    "value": null,
    "data_status": "unavailable",
    "source": null,
    "confidence": 0.0
  },
  "estimated_cost": {},
  "experience_card_id": "card_exp_001",
  "data_sources_used": [],
  "confidence": 0.82
}
```

Important rule:

The AI should not invent:

* opening hours
* prices
* exact duration as provider fact
* coordinates
* ratings

Estimated duration is allowed only when labeled as `estimated`.

---

## 16. RestaurantOption

```json
{
  "restaurant_id": "rest_001",
  "name": "Provider-backed Restaurant Name",
  "cuisine_category": "Italian",
  "coordinates": {},
  "rating": {
    "value": 4.5,
    "review_count": 900,
    "data_status": "live",
    "source": "places_provider",
    "confidence": 0.85
  },
  "price_level": {
    "value": "$$",
    "data_status": "live",
    "source": "places_provider",
    "confidence": 0.8
  },
  "opening_hours": {
    "value": [],
    "data_status": "live",
    "source": "places_provider",
    "confidence": 0.75
  },
  "why_it_fits": [],
  "data_sources_used": [],
  "confidence": 0.82
}
```

If only OpenStreetMap data is available, rating and price fields should be marked unavailable unless provided by a legitimate source.

---

## 17. MealPlanItem

```json
{
  "meal": "lunch",
  "recommendation_type": "restaurant",
  "restaurant_option": {},
  "meal_area": null,
  "reason": "Close to the previous activity and avoids backtracking.",
  "confidence": 0.82
}
```

If restaurant data is unavailable:

```json
{
  "meal": "lunch",
  "recommendation_type": "meal_area",
  "restaurant_option": null,
  "meal_area": {
    "name": "Georgetown Waterfront",
    "reason": "Good area for lunch near the planned route."
  },
  "confidence": 0.68
}
```

---

## 18. ValidationReport

```json
{
  "overall_score": 91,
  "category_scores": {
    "feasibility": 95,
    "comfort": 88,
    "efficiency": 90,
    "budget": 92,
    "safety_related_planning": 94,
    "experience_variety": 87
  },
  "readiness_status": "needs_review",
  "validation_cards": [],
  "critical_issues": [],
  "warnings": [],
  "suggestions": [],
  "validation_summary": {},
  "planning_metadata": {}
}
```

---

## 19. BaseExplanationCard

All user-facing cards should extend a shared base card.

```json
{
  "id": "card_001",
  "card_type": "decision",
  "stage": "stay_transport",
  "title": "",
  "summary": "",
  "reasons": [],
  "tradeoffs": [],
  "alternatives": [],
  "confidence": 0.84,
  "data_sources": [],
  "assumptions": [],
  "claim_sources": []
}
```

---

## 20. DecisionCard

Used for major recommendations.

```json
{
  "id": "card_stay_area_001",
  "card_type": "decision",
  "stage": "stay_transport",
  "title": "Stay in a central, transit-connected area",
  "summary": "Dupont Circle is recommended because it balances comfort, access, and route efficiency.",
  "reasons": [],
  "tradeoffs": [],
  "alternatives": [],
  "confidence": 0.87,
  "data_sources": [],
  "assumptions": [],
  "claim_sources": []
}
```

---

## 21. ExperienceCard

```json
{
  "id": "card_exp_001",
  "card_type": "experience",
  "stage": "experience_planner",
  "experience_id": "exp_001",
  "title": "Lincoln Memorial",
  "summary": "",
  "category": "historical",
  "priority": "high",
  "estimated_duration_minutes": 75,
  "best_time_to_visit": "morning",
  "coordinates": {},
  "estimated_cost": {},
  "reasons": [],
  "tradeoffs": [],
  "alternatives": [],
  "confidence": 0.82,
  "data_sources": [],
  "assumptions": [],
  "claim_sources": []
}
```

---

## 22. ValidationCard

```json
{
  "id": "card_val_001",
  "card_type": "validation",
  "stage": "plan_validator",
  "severity": "warning",
  "category": "walking",
  "title": "High walking distance",
  "issue": "Estimated walking distance may exceed the traveler's comfort level.",
  "why_it_matters": "The traveler profile indicates moderate walking tolerance.",
  "suggested_fix": "Move one activity to Day 3.",
  "confidence": 0.96,
  "data_sources": [],
  "claim_sources": []
}
```

---

## 23. FeedbackEvent

```json
{
  "feedback_id": "fb_001",
  "user_feedback": "Day 2 is too packed.",
  "feedback_type": "schedule_feedback",
  "affected_stages": ["experience_planner", "plan_validator"],
  "regeneration_strategy": "day_level_update",
  "change_summary": {
    "changed": [],
    "unchanged": [],
    "reason": ""
  },
  "created_new_version": "v2",
  "created_at": "2026-07-03T18:15:00Z"
}
```

---

## 24. UserLock

```json
{
  "lock_id": "lock_001",
  "locked_item_type": "experience",
  "locked_item_id": "exp_lincoln_memorial",
  "reason": "user_approved",
  "created_at": "2026-07-03T18:00:00Z"
}
```

Allowed `locked_item_type` values:

```text
stay_area
accommodation
experience
restaurant
day_plan
transport_strategy
```

---

## 25. VersionHistoryItem

```json
{
  "version": "v1",
  "created_at": "2026-07-03T18:00:00Z",
  "created_by": "initial_generation",
  "summary": "Initial itinerary generated.",
  "changed_sections": [
    "traveler_profile",
    "destination_context",
    "trip_strategy",
    "stay_transport",
    "experience_plan",
    "validation_report"
  ],
  "feedback_id": null
}
```

---

## 26. PlanningState

`PlanningState` is the root object.

```json
{
  "planning_state_id": "ps_001",
  "trip_request": {},
  "traveler_profile": {},
  "destination_context": {},
  "trip_strategy": {},
  "stay_transport": {},
  "experience_plan": {},
  "validation_report": {},
  "feedback_history": [],
  "user_locks": [],
  "decision_cards": [],
  "experience_cards": [],
  "validation_cards": [],
  "provider_status": {},
  "provider_coverage": {},
  "unavailable_data": [],
  "data_sources_used": [],
  "metadata": {},
  "version_history": []
}
```

---

## 27. PlanningMetadata

```json
{
  "created_at": "2026-07-03T18:00:00Z",
  "updated_at": "2026-07-03T18:10:00Z",
  "current_version": "v1",
  "pipeline_status": "validated",
  "active_stage": "plan_validator",
  "data_status": "provider_backed",
  "environment": "development"
}
```

---

## 28. Data Ownership Rules

### TripRequest Owns

* original structured user request

### TravelerProfile Owns

* interpreted traveler preferences
* constraints
* decision weights

### DestinationContext Owns

* provider/open-data-backed destination snapshot

### TripStrategy Owns

* planning direction
* planning targets
* trip-level assumptions

### StayTransportDecision Owns

* stay area
* transport strategy
* accommodation recommendations

### ExperiencePlan Owns

* daily itinerary
* selected experiences
* meal plan

### ValidationReport Owns

* quality review
* readiness status
* validation issues

### FeedbackEvent Owns

* user feedback interpretation
* affected stages
* regeneration strategy

### PlanningState Owns

* current full planning version
* provider coverage
* provider status
* unavailable data
* version history

---

## 29. Implementation Notes

The backend should implement these as Pydantic models.

The frontend should mirror these as TypeScript interfaces.

The API should exchange structured JSON only.

Every provider-backed or open-data-backed field should include:

* source
* data_status
* confidence
* unavailable_fields when relevant

No model should require fake fallback values.

If a value is unavailable, represent it explicitly as unavailable.

---

## 30. Design Principles

The data model should follow these principles:

* Make unavailable data explicit.
* Separate user input from provider facts.
* Separate provider facts from AI reasoning.
* Separate open data from provider-confirmed data.
* Keep PlanningState as the central object.
* Use shared card structures for frontend consistency.
* Do not design fields that require mock data.
* Do not require restricted provider data for MVP.
* Keep the model extensible for future multi-city planning.
