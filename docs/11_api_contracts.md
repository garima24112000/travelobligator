# API Contracts

## 1. Purpose

This document defines the backend API contracts for TravelObligator.

The goal is to make the frontend and backend communicate through clear, structured JSON.

The API should operate around one central object:

```text
PlanningState
```

Each major planning stage should either create, update, retrieve, validate, or version the Planning State.

---

## 2. API Design Principles

The API should follow these principles:

* Use structured JSON only.
* Use `snake_case` field names.
* Return Planning State after major updates.
* Do not return mock data as production data.
* Make unavailable data explicit.
* Include provider status and provider coverage.
* Keep stage endpoints separate for development and debugging.
* Allow a full pipeline endpoint later for production convenience.
* Validate every request and response against schemas.
* Never hide provider failures.

---

## 3. Base URL

During local development:

```text
http://localhost:8000
```

Example frontend environment variable:

```text
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

---

## 4. Standard Response Shape

Every successful API response should use this shape:

```json
{
  "success": true,
  "data": {},
  "message": null,
  "errors": [],
  "metadata": {
    "request_id": "",
    "timestamp": "",
    "environment": "development"
  }
}
```

Every failed API response should use this shape:

```json
{
  "success": false,
  "data": null,
  "message": "Request failed.",
  "errors": [
    {
      "code": "VALIDATION_ERROR",
      "field": "trip_request.start_date",
      "message": "Start date must be before end date."
    }
  ],
  "metadata": {
    "request_id": "",
    "timestamp": "",
    "environment": "development"
  }
}
```

---

## 5. Error Codes

Suggested error codes:

```text
VALIDATION_ERROR
TRIP_NOT_FOUND
PLANNING_STATE_NOT_FOUND
PROVIDER_FAILED
PROVIDER_NOT_CONNECTED
DATA_UNAVAILABLE
AI_OUTPUT_INVALID
STAGE_ALREADY_RUNNING
STAGE_FAILED
UNSUPPORTED_OPERATION
INTERNAL_ERROR
```

Provider failures should not be hidden behind generic errors when the user-facing result depends on provider data.

---

## 6. Pipeline Stages

The API supports these planning stages:

```text
create_trip
generate_traveler_profile
build_destination_context
generate_trip_strategy
generate_stay_transport
generate_experience_plan
validate_plan
apply_feedback
```

Each stage reads the current Planning State, updates only the section it owns, and returns the updated Planning State.

---

## 7. Health Check

### GET `/health`

Used to verify that the backend is running.

Response:

```json
{
  "success": true,
  "data": {
    "status": "ok",
    "service": "travelobligator-api"
  },
  "message": null,
  "errors": [],
  "metadata": {}
}
```

---

## 8. Create Trip

### POST `/trips`

Creates a new trip and initial Planning State.

Request body:

```json
{
  "destination_scope": "single_city",
  "primary_destination": {
    "city": "Washington DC",
    "country": "United States"
  },
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

Response data:

```json
{
  "trip_id": "trip_001",
  "planning_state": {
    "planning_state_id": "ps_001",
    "trip_request": {},
    "traveler_profile": null,
    "destination_context": null,
    "trip_strategy": null,
    "stay_transport": null,
    "experience_plan": null,
    "validation_report": null,
    "feedback_history": [],
    "user_locks": [],
    "decision_cards": [],
    "experience_cards": [],
    "validation_cards": [],
    "provider_status": {},
    "provider_coverage": {},
    "unavailable_data": [],
    "data_sources_used": [],
    "metadata": {
      "pipeline_status": "draft",
      "active_stage": "create_trip",
      "current_version": "v1"
    },
    "version_history": []
  }
}
```

---

## 9. Get Trip Planning State

### GET `/trips/{trip_id}`

Returns the latest Planning State for a trip.

Response data:

```json
{
  "trip_id": "trip_001",
  "planning_state": {}
}
```

---

## 10. Generate Traveler Profile

### POST `/trips/{trip_id}/traveler-profile`

Generates or regenerates the Traveler Profile from the trip request.

Consumes:

* trip_request

Updates:

* traveler_profile
* decision_cards when needed
* metadata

Request body:

```json
{
  "force_regenerate": false
}
```

Response data:

```json
{
  "trip_id": "trip_001",
  "updated_sections": ["traveler_profile"],
  "planning_state": {}
}
```

Rules:

* This stage may use AI for free-text interpretation.
* This stage should not call travel providers.
* Later stages should use Traveler Profile instead of raw trip request fields when possible.

---

## 11. Build Destination Context

### POST `/trips/{trip_id}/destination-context`

Builds a provider-backed or open-data-backed snapshot of the destination.

Consumes:

* trip_request
* traveler_profile when available

Updates:

* destination_context
* provider_status
* provider_coverage
* unavailable_data
* data_sources_used
* metadata

Request body:

```json
{
  "force_refresh": false,
  "allowed_sources": [
    "openstreetmap",
    "overpass",
    "nominatim",
    "geonames",
    "google_places"
  ]
}
```

Response data:

```json
{
  "trip_id": "trip_001",
  "updated_sections": [
    "destination_context",
    "provider_status",
    "provider_coverage"
  ],
  "planning_state": {}
}
```

Rules:

* Destination Context provides candidate data only.
* It should not select final attractions, restaurants, accommodations, or itinerary days.
* If a provider is unavailable or not connected, this must be reflected in provider coverage.

---

## 12. Generate Trip Strategy

### POST `/trips/{trip_id}/trip-strategy`

Generates the high-level trip strategy.

Consumes:

* traveler_profile
* destination_context

Updates:

* trip_strategy
* decision_cards
* metadata

Request body:

```json
{
  "force_regenerate": false
}
```

Response data:

```json
{
  "trip_id": "trip_001",
  "updated_sections": ["trip_strategy", "decision_cards"],
  "planning_state": {}
}
```

Rules:

* Trip Strategy defines planning direction.
* It should not select final attractions, restaurants, or accommodations.
* It should produce planning targets used by the Experience Planner.

---

## 13. Generate Stay + Transport

### POST `/trips/{trip_id}/stay-transport`

Generates stay area, accommodation options, and transport strategy.

Consumes:

* traveler_profile
* destination_context
* trip_strategy

Updates:

* stay_transport
* decision_cards
* provider_status
* provider_coverage
* unavailable_data
* metadata

Request body:

```json
{
  "force_regenerate": false,
  "max_accommodation_options": 5,
  "allowed_accommodation_sources": [
    "openstreetmap",
    "amadeus_hotels",
    "approved_accommodation_provider"
  ]
}
```

Response data:

```json
{
  "trip_id": "trip_001",
  "updated_sections": [
    "stay_transport",
    "decision_cards",
    "provider_status",
    "provider_coverage"
  ],
  "planning_state": {}
}
```

Rules:

* Recommend stay area before accommodation options.
* Recommend top accommodation options, not final booking decisions.
* If only open accommodation POIs are available, mark price, availability, ratings, and review counts as unavailable unless returned by a legitimate source.
* Do not imply that Airbnb, Booking.com, Expedia, Vrbo, Tripadvisor, or Google Flights were searched unless approved provider access is connected.

---

## 14. Generate Experience Plan

### POST `/trips/{trip_id}/experience-plan`

Generates the day-wise experience plan.

Consumes:

* traveler_profile
* destination_context
* trip_strategy
* stay_transport

Updates:

* experience_plan
* experience_cards
* decision_cards
* provider_status
* provider_coverage
* unavailable_data
* metadata

Request body:

```json
{
  "force_regenerate": false,
  "restaurant_mode": "provider_backed_when_available",
  "allow_meal_area_fallback": true
}
```

Response data:

```json
{
  "trip_id": "trip_001",
  "updated_sections": [
    "experience_plan",
    "experience_cards",
    "decision_cards",
    "provider_status",
    "provider_coverage"
  ],
  "planning_state": {}
}
```

Rules:

* Do not invent attractions.
* Do not invent restaurants.
* Do not invent ratings, opening hours, prices, or availability.
* If restaurant data is unavailable, use meal-area suggestions instead of fake restaurant names.
* Estimated durations are allowed only when marked as estimated.

---

## 15. Validate Plan

### POST `/trips/{trip_id}/validate`

Validates the current experience plan.

Consumes:

* traveler_profile
* destination_context
* trip_strategy
* stay_transport
* experience_plan
* provider_status
* provider_coverage

Updates:

* validation_report
* validation_cards
* metadata

Request body:

```json
{
  "force_revalidate": false
}
```

Response data:

```json
{
  "trip_id": "trip_001",
  "updated_sections": [
    "validation_report",
    "validation_cards"
  ],
  "planning_state": {}
}
```

Rules:

* Deterministic validation should run before AI reasoning validation.
* Validator must not modify the itinerary.
* Validator should return `readiness_status` as one of:

  * ready
  * needs_review
  * blocked
* Safety validation should focus on safety-related planning considerations, not safety scores.

---

## 16. Generate Full Plan

### POST `/trips/{trip_id}/generate`

Runs the full planning pipeline.

Pipeline order:

```text
Traveler Profile
→ Destination Context
→ Trip Strategy
→ Stay + Transport
→ Experience Planner
→ Plan Validator
```

Request body:

```json
{
  "force_regenerate": false,
  "stop_on_blocked": true
}
```

Response data:

```json
{
  "trip_id": "trip_001",
  "pipeline_status": "validated",
  "updated_sections": [
    "traveler_profile",
    "destination_context",
    "trip_strategy",
    "stay_transport",
    "experience_plan",
    "validation_report"
  ],
  "planning_state": {}
}
```

Rules:

* This endpoint should be used by the frontend for normal MVP generation.
* Individual stage endpoints can be used for debugging, testing, or partial regeneration.
* If a critical provider is unavailable, the pipeline may continue only if the output can remain honest and useful.
* If missing data makes the itinerary misleading or infeasible, validation should mark it as `blocked`.

---

## 17. Apply Feedback

### POST `/trips/{trip_id}/feedback`

Applies user feedback and performs the smallest valid regeneration path.

Consumes:

* full current Planning State
* user feedback
* user locks
* version history

Updates:

* feedback_history
* affected planning sections
* validation_report
* version_history
* metadata

Request body:

```json
{
  "feedback_text": "Day 2 is too packed. Make it lighter.",
  "user_approved_sections": [],
  "user_rejected_sections": ["day_2"],
  "allow_full_regeneration": false
}
```

Response data:

```json
{
  "trip_id": "trip_001",
  "feedback_event_id": "fb_001",
  "regeneration_strategy": "day_level_update",
  "updated_sections": [
    "experience_plan",
    "validation_report",
    "feedback_history",
    "version_history"
  ],
  "planning_state": {}
}
```

Rules:

* Do not regenerate everything by default.
* Preserve user-approved sections unless directly contradicted.
* If feedback is vague, ask a follow-up question instead of guessing.
* If provider data required for feedback is not connected, mark the request as unavailable or low-confidence instead of inventing options.

---

## 18. Add User Lock

### POST `/trips/{trip_id}/locks`

Locks a user-approved section so future feedback does not accidentally overwrite it.

Request body:

```json
{
  "locked_item_type": "experience",
  "locked_item_id": "exp_lincoln_memorial",
  "reason": "user_approved"
}
```

Response data:

```json
{
  "trip_id": "trip_001",
  "lock_id": "lock_001",
  "planning_state": {}
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

## 19. Remove User Lock

### DELETE `/trips/{trip_id}/locks/{lock_id}`

Removes a lock.

Response data:

```json
{
  "trip_id": "trip_001",
  "removed_lock_id": "lock_001",
  "planning_state": {}
}
```

---

## 20. Get Versions

### GET `/trips/{trip_id}/versions`

Returns version history.

Response data:

```json
{
  "trip_id": "trip_001",
  "versions": [
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
  ]
}
```

---

## 21. Get Specific Version

### GET `/trips/{trip_id}/versions/{version_id}`

Returns a specific Planning State version.

Response data:

```json
{
  "trip_id": "trip_001",
  "version": "v1",
  "planning_state": {}
}
```

---

## 22. Provider Coverage Endpoint

### GET `/trips/{trip_id}/provider-coverage`

Returns provider coverage for the current Planning State.

Response data:

```json
{
  "trip_id": "trip_001",
  "provider_coverage": {
    "places": "available",
    "routes": "available",
    "restaurants": "open_data_available",
    "accommodations": "open_poi_available",
    "hotel_prices": "provider_available",
    "vacation_rentals": "not_connected",
    "airbnb": "not_connected",
    "flights": "not_enabled",
    "weather": "available"
  },
  "unavailable_data": [
    "airbnb_inventory",
    "vacation_rental_prices"
  ],
  "data_sources_used": [
    "openstreetmap",
    "overpass",
    "opentripplanner"
  ]
}
```

This endpoint is useful for frontend transparency panels.

---

## 23. Frontend Page Usage

### Trip Creation Page

Uses:

```text
POST /trips
```

### Generate Plan Button

Uses:

```text
POST /trips/{trip_id}/generate
```

### Dashboard Load

Uses:

```text
GET /trips/{trip_id}
```

### Feedback Box

Uses:

```text
POST /trips/{trip_id}/feedback
```

### Lock / Keep This Button

Uses:

```text
POST /trips/{trip_id}/locks
```

### Version History Panel

Uses:

```text
GET /trips/{trip_id}/versions
GET /trips/{trip_id}/versions/{version_id}
```

### Provider Transparency Panel

Uses:

```text
GET /trips/{trip_id}/provider-coverage
```

---

## 24. Development vs Production Use

During development, individual stage endpoints are useful:

```text
POST /traveler-profile
POST /destination-context
POST /trip-strategy
POST /stay-transport
POST /experience-plan
POST /validate
```

For product usage, the frontend should usually call:

```text
POST /trips/{trip_id}/generate
```

and then render the returned Planning State.

---

## 25. API Contract Rules

* API contracts should not require mock data.
* API contracts should allow unavailable fields.
* API responses should include provider coverage.
* Provider failures should be visible when they affect output quality.
* AI output should be schema-validated before returning to frontend.
* Stage endpoints should return updated Planning State.
* Feedback should create a new version when it changes the plan.
* The frontend should render from Planning State, not scattered endpoint responses.

---

## 26. Next Implementation Notes

The backend should implement:

* Pydantic request models
* Pydantic response models
* PlanningState model
* service layer per stage
* provider gateway
* provider coverage tracker
* validation error handling
* versioning logic

The frontend should implement:

* Trip creation form
* Planning progress state
* itinerary dashboard
* decision card components
* experience card components
* validation card components
* provider coverage labels
* feedback controls
* version history panel
