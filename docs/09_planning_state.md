# Planning State

## 1. Purpose

`PlanningState` is the central object that flows through the TravelObligator planning pipeline.

Instead of passing many disconnected objects between services, each stage should read from and write to one shared planning state.

The goal is to make the planning system:

* easier to implement
* easier to debug
* easier to validate
* easier to regenerate partially
* easier to extend from single-city trips to multi-city trips later

The Planning State represents the current version of the trip plan at any point in the pipeline.

---

## 2. Why Planning State Exists

TravelObligator is not a simple itinerary generator.

It has multiple reasoning stages:

1. Traveler Profile
2. Destination Context
3. Trip Strategy
4. Stay + Transport
5. Experience Planner
6. Plan Validator
7. Feedback Pipeline

Each stage produces important structured outputs.

Without a central state object, the backend would need to pass many separate objects between services, which can become confusing and error-prone.

`PlanningState` solves this by acting as the single source of truth for the current planning process.

---

## 3. High-Level Structure

The MVP Planning State should follow this structure:

```json
{
  "planning_state_id": "",
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

Each section has a clear owner.

No stage should overwrite another stage’s data unless the regeneration path explicitly requires it.

---

## 4. Trip Request

`trip_request` stores the original structured request from the user.

It represents what the user asked for before the system interprets it.

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
  "raw_preferences": {
    "budget_range": "$1500-$2500",
    "interests": ["food", "culture", "scenic views"],
    "free_text": "Traveling with parents, not too much walking."
  }
}
```

### MVP Rule

For MVP:

```text
destination_scope = single_city
```

However, the structure should not block future multi-city support.

Future versions can add multiple destination segments without redesigning the whole pipeline.

---

## 5. Traveler Profile

`traveler_profile` stores the structured understanding of the traveler.

Owned by:

```text
Traveler Profile stage
```

It should include:

* basic context
* travel intent
* budget profile
* mobility profile
* interest profile
* stay profile
* transport profile
* avoidance profile
* decision weights
* confidence levels

Example:

```json
{
  "travel_intent": {
    "intensity_scale": 2,
    "pace": "relaxed",
    "trip_goals": ["food", "culture", "scenic"]
  },
  "budget_profile": {
    "budget_min": 1500,
    "budget_max": 2500,
    "budget_tier": "mid_range",
    "cost_sensitivity": 0.65
  },
  "mobility_profile": {
    "walking_tolerance": "moderate",
    "stairs_tolerance": "low",
    "elderly_consideration": true
  },
  "decision_weights": {
    "comfort_weight": 0.9,
    "walkability_weight": 0.85,
    "food_weight": 0.8,
    "budget_weight": 0.65
  },
  "confidence": {
    "budget": 0.9,
    "mobility": 0.7,
    "activities": 0.85
  }
}
```

### Ownership Rule

Traveler Profile owns traveler preferences and constraints.

Other stages may use these values, but they should not rewrite them unless feedback explicitly changes the traveler profile.

---

## 6. Destination Context

`destination_context` stores provider-backed information about the destination.

It is not the final itinerary.

It exists to help early stages reason about the destination without depending on final selected experiences.

Owned by:

```text
Destination Context builder
```

Used by:

* Trip Strategy
* Stay + Transport
* Experience Planner
* Plan Validator

Example:

```json
{
  "destination": {
    "city": "Washington DC",
    "country": "United States"
  },
  "candidate_poi_clusters": [
    {
      "cluster_id": "national_mall",
      "name": "National Mall",
      "categories": ["monuments", "museums", "history"],
      "approximate_center": {
        "lat": 38.8895,
        "lng": -77.0353
      }
    }
  ],
  "neighborhood_candidates": [
    {
      "name": "Dupont Circle",
      "known_for": ["restaurants", "metro access", "central location"]
    }
  ],
  "rough_transport_feasibility": {
    "public_transport_available": true,
    "rideshare_available": true,
    "car_rental_needed_for_city_core": false
  },
  "average_cost_hints": {
    "accommodation_tier": "mid_range",
    "food_tier": "mid_range"
  },
  "data_sources_used": [
    "places_provider",
    "routes_provider",
    "accommodation_provider"
  ],
  "confidence": 0.78
}
```

### Important Rule

Destination Context provides candidate information.

It should not decide:

* final attractions
* final restaurants
* final accommodation options
* final daily itinerary

Those decisions belong to later stages.

---

## 7. Trip Strategy

`trip_strategy` stores the high-level direction of the trip.

Owned by:

```text
Trip Strategy stage
```

It should include:

* destination suitability
* duration assessment
* budget assessment
* recommended trip style
* planning strategy
* planning targets
* tradeoffs
* assumptions
* confidence

Example:

```json
{
  "destination_suitability": {
    "score": 0.88,
    "label": "high",
    "reasons": [
      "Strong match with food, culture, and scenic interests.",
      "Major attractions can be grouped without excessive travel."
    ]
  },
  "recommended_trip_style": "relaxed scenic cultural trip",
  "planning_strategy": [
    "Group attractions geographically.",
    "Avoid overloading each day.",
    "Use public transport where convenient and rideshare for comfort."
  ],
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
  "confidence": 0.82
}
```

### Important Rule

Trip Strategy defines the planning philosophy.

It should not select final attractions or restaurants.

---

## 8. Stay + Transport

`stay_transport` stores the recommended stay area, accommodation options, and transport strategy.

Owned by:

```text
Stay + Transport stage
```

It should include:

* recommended stay area
* alternative stay areas
* transport strategy
* top 5 accommodation options
* decision cards
* tradeoffs
* confidence

Example:

```json
{
  "recommended_stay_area": {
    "name": "Dupont Circle",
    "score": 0.88,
    "decision_card_id": "card_stay_area_001"
  },
  "alternative_stay_areas": [
    {
      "name": "Foggy Bottom",
      "score": 0.79,
      "reason": "Good access to major sightseeing areas, but fewer restaurant options."
    }
  ],
  "transport_strategy": {
    "primary_mode": "metro",
    "secondary_mode": "rideshare",
    "car_rental_recommendation": "not_needed_inside_city",
    "decision_card_id": "card_transport_001"
  },
  "accommodation_recommendations": [
    {
      "accommodation_id": "stay_001",
      "name": "Example Central Stay",
      "accommodation_type": "hotel",
      "area": "Dupont Circle",
      "estimated_price_per_night": 220,
      "score": 0.84,
      "why_it_fits": [
        "Within the recommended stay area.",
        "Close to public transport.",
        "Fits the traveler’s comfort and budget preferences."
      ],
      "tradeoffs": [
        "Not the cheapest available option."
      ],
      "data_sources_used": [
        "accommodation_provider",
        "routes_provider"
      ],
      "confidence": 0.81
    }
  ],
  "confidence": 0.84
}
```

### Accommodation Rule

The MVP recommends the best 5 accommodation options, not one final booking decision.

Accommodation options may include:

* hotels
* motels
* hostels
* resorts
* serviced apartments
* vacation rentals
* guesthouses
* boutique stays

The system should not guarantee final price, availability, or booking completion unless confirmed by a provider.

---

## 9. Experience Plan

`experience_plan` stores the selected experiences and day-wise itinerary proposal.

Owned by:

```text
Experience Planner stage
```

It should include:

* trip overview
* daily plan
* selected attractions
* restaurant recommendations when provider-backed
* meal-area suggestions when restaurant data is unavailable
* experience cards
* decision cards
* estimated walking
* estimated travel time
* estimated budget
* planning metadata

Example:

```json
{
  "trip_overview": {
    "title": "Relaxed Scenic Cultural Trip",
    "summary": "A parent-friendly 3-day plan focused on monuments, food, and scenic neighborhoods."
  },
  "daily_plan": [
    {
      "day": 1,
      "theme": "Arrival + Local Exploration",
      "activities": [
        {
          "experience_id": "exp_001",
          "name": "Lincoln Memorial",
          "category": "historical",
          "start_time": "10:00",
          "estimated_duration_minutes": 75,
          "experience_card_id": "card_exp_001"
        }
      ],
      "meal_plan": [
        {
          "meal": "lunch",
          "recommendation_type": "restaurant",
          "restaurant_name": "Provider-backed Restaurant Name",
          "rating": 4.5,
          "data_source": "places_provider",
          "confidence": 0.82
        }
      ],
      "estimated_walking_km": 5.2,
      "estimated_cost": 85,
      "energy_level": "moderate"
    }
  ],
  "confidence": 0.8
}
```

### Restaurant Rule

Actual restaurant recommendations should only be shown when supported by provider or resource data.

The system should not invent:

* restaurant names
* ratings
* review counts
* opening hours
* availability
* prices

If reliable restaurant data is unavailable, the system should recommend meal areas instead.

---

## 10. Validation Report

`validation_report` stores the review of the itinerary’s feasibility and quality.

Owned by:

```text
Plan Validator stage
```

It should include:

* overall score
* category scores
* readiness status
* validation cards
* critical issues
* warnings
* suggestions
* validation summary
* planning metadata

Example:

```json
{
  "overall_score": 91,
  "readiness_status": "needs_review",
  "category_scores": {
    "feasibility": 95,
    "comfort": 88,
    "efficiency": 90,
    "budget": 92,
    "safety_related_planning": 94,
    "experience_variety": 87
  },
  "critical_issues": [],
  "warnings": [
    {
      "validation_card_id": "card_val_001",
      "category": "walking",
      "severity": "warning"
    }
  ],
  "validation_summary": {
    "summary": "The itinerary is realistic and well balanced. Minor improvements are recommended to reduce walking on Day 2."
  }
}
```

### Readiness Status

Allowed values:

```text
ready
needs_review
blocked
```

### Safety Rule

The MVP does not generate direct safety scores.

Validation should focus on safety-related planning considerations such as:

* late-night travel
* isolated or remote movement
* long walking segments
* poor transit alignment
* weather exposure
* traveler-specific comfort constraints

The validator should not label a place, route, hotel, accommodation, attraction, or restaurant as safe or unsafe without authoritative data.

---

## 11. Feedback History

`feedback_history` stores user feedback and how the system responded.

Owned by:

```text
Feedback Pipeline
```

Example:

```json
[
  {
    "feedback_id": "fb_001",
    "user_feedback": "Day 2 is too packed.",
    "feedback_type": "schedule_feedback",
    "affected_stages": [
      "experience_planner",
      "plan_validator"
    ],
    "regeneration_strategy": "day_level_update",
    "change_summary": {
      "changed": [
        "Removed one activity from Day 2.",
        "Added a longer rest break."
      ],
      "unchanged": [
        "Stay area remained the same.",
        "Transport strategy remained the same."
      ]
    },
    "created_new_version": "v2"
  }
]
```

---

## 12. User Locks

`user_locks` stores parts of the plan that the user has approved or explicitly wants to keep.

Owned by:

```text
Feedback Pipeline
```

Example:

```json
[
  {
    "lock_id": "lock_001",
    "locked_item_type": "experience",
    "locked_item_id": "exp_lincoln_memorial",
    "reason": "user_approved",
    "created_at": "2026-07-03T18:00:00Z"
  }
]
```

Locked items should not be changed unless:

* the user directly asks to change them
* the lock conflicts with a higher-priority constraint
* the plan becomes infeasible
* provider data shows the locked item is unavailable

If a locked item must be changed, the system should explain why.

---

## 13. Explanation Cards

TravelObligator should use a shared card structure for user-facing explanations.

### BaseExplanationCard

Common fields:

```json
{
  "id": "",
  "card_type": "decision | experience | validation",
  "stage": "",
  "title": "",
  "summary": "",
  "reasons": [],
  "tradeoffs": [],
  "alternatives": [],
  "confidence": 0.0,
  "data_sources": [],
  "assumptions": [],
  "claim_sources": []
}
```

### DecisionCard

Used for major recommendations.

Examples:

* destination suitability
* trip strategy
* stay area
* transport strategy
* accommodation ranking
* major itinerary choices
* feedback updates

### ExperienceCard

Used for selected activities, attractions, restaurants, and meal-area suggestions.

Adds:

```json
{
  "experience_id": "",
  "category": "",
  "priority": "",
  "estimated_duration_minutes": 0,
  "best_time_to_visit": "",
  "coordinates": {},
  "estimated_cost": {}
}
```

### ValidationCard

Used for warnings, issues, and suggestions.

Adds:

```json
{
  "severity": "critical | warning | suggestion",
  "category": "",
  "issue": "",
  "suggested_fix": ""
}
```

---

## 14. Claim Sources

Every factual or reasoning claim should be traceable.

A claim should be labeled as one of:

```text
provider_fact
user_input
system_rule
ai_inference
assumption
open_data_fact
unavailable_data
```

Example:

```json
{
  "claim": "This accommodation is close to public transport.",
  "source_type": "provider_fact",
  "source": "routes_provider"
}
```

Example:

```json
{
  "claim": "This day may feel tiring for the traveler.",
  "source_type": "ai_inference",
  "based_on": [
    "estimated walking distance",
    "traveler mobility profile",
    "daily activity count"
  ]
}
```

### Important Rule

AI may interpret and explain.

AI should not invent factual data.

---

## 15. Provider Status

`provider_status` tracks the health and outcome of provider-backed data requests.

Example:

```json
{
  "places_provider": {
    "status": "success",
    "fallback_used": false,
    "last_checked_at": "2026-07-03T18:00:00Z"
  },
  "routes_provider": {
    "status": "fallback_used",
    "fallback_provider": "openstreetmap",
    "last_checked_at": "2026-07-03T18:01:00Z"
  },
  "accommodation_provider": {
    "status": "failed",
    "fallback_used": false,
    "unavailable_fields": [
      "live_availability",
      "latest_price"
    ]
  }
}
```

Allowed statuses:

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

Provider failures should reduce confidence.

They should not be replaced with fake data.

---

## 16. Provider Coverage

`provider_coverage` stores what data categories were actually available, unavailable, partial, or not connected during the planning run.

Example:

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

## 17. Metadata

`metadata` stores system-level information about the planning run.

Example:

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

Possible pipeline statuses:

```text
draft
profile_created
destination_context_created
strategy_created
stay_transport_created
experience_plan_created
validated
needs_review
blocked
feedback_pending
updated_after_feedback
```

---

## 18. Version History

`version_history` stores every major version of the plan.

Example:

```json
[
  {
    "version": "v1",
    "created_at": "2026-07-03T18:00:00Z",
    "created_by": "initial_generation",
    "summary": "Initial itinerary generated.",
    "changed_sections": [
      "traveler_profile",
      "trip_strategy",
      "stay_transport",
      "experience_plan",
      "validation_report"
    ]
  },
  {
    "version": "v2",
    "created_at": "2026-07-03T18:15:00Z",
    "created_by": "user_feedback",
    "summary": "Reduced Day 2 intensity based on user feedback.",
    "changed_sections": [
      "experience_plan",
      "validation_report"
    ],
    "feedback_id": "fb_001"
  }
]
```

Version history is required for:

* comparing plan versions
* preserving user-approved sections
* debugging regeneration
* explaining changes to the user

---

## 19. Stage Update Rules

Each stage should update only the section it owns.

### Traveler Profile Stage May Update

* traveler_profile
* metadata
* decision_cards if needed

### Destination Context Builder May Update

* destination_context
* provider_status
* metadata

### Trip Strategy Stage May Update

* trip_strategy
* decision_cards
* metadata

### Stay + Transport Stage May Update

* stay_transport
* decision_cards
* provider_status
* metadata

### Experience Planner May Update

* experience_plan
* experience_cards
* decision_cards
* provider_status
* metadata

### Plan Validator May Update

* validation_report
* validation_cards
* provider_status
* metadata

### Feedback Pipeline May Update

* feedback_history
* user_locks
* version_history
* affected sections based on regeneration strategy
* metadata

No stage should modify unrelated sections without an explicit regeneration path.

---

## 20. Regeneration Rules

When feedback is received, the system should choose the smallest valid update path.

### Explanation Only

No Planning State section changes.

Used for:

* “Why did you choose this area?”
* “Why is this day lighter?”

### Section-Level Update

Only one section changes.

Used for:

* “Show cheaper accommodation options.”
* “Use public transport instead of rideshare.”

### Day-Level Update

Only one day or part of the experience plan changes.

Used for:

* “Day 2 is too packed.”
* “Replace this museum.”

### Pipeline-Level Update

Multiple stages are rerun.

Used for:

* “Actually, this is a luxury trip.”
* “My parents cannot walk much.”
* “Change the stay area.”

### Full Regeneration

Last resort only.

Used when:

* destination changes
* dates change significantly
* trip duration changes
* core traveler profile changes
* previous state is no longer valid

---

## 21. Design Principles

The Planning State should follow these principles:

* One central object should flow through the planning pipeline.
* Every stage should have clear ownership.
* No stage should silently overwrite another stage’s output.
* Provider-backed facts should be separated from AI reasoning.
* Missing data should reduce confidence, not create hallucinated certainty.
* Feedback should update only affected sections whenever possible.
* User-approved sections should be preserved through locks.
* Version history should make every plan change traceable.
* The MVP should support single-city trips while remaining extensible to multi-city planning later.
* The itinerary is the final artifact, not the source of intelligence.
