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

This endpoint is informational only. It does not run any provider call
itself, does not change `experience_plan`, `validation_report`, or
`regeneration_readiness`, and does not prove that a restricted or paid
provider (Booking.com, Airbnb, Expedia, Vrbo, Tripadvisor, Google Flights)
is connected — only `provider_coverage`/`provider_status` say that.
Reading this endpoint also does not make an unavailable field usable; a
field marked `unavailable`/`not_connected` stays that way until a real
provider is connected.

`data_sources_used` lists provider names that returned usable data; a
provider is included only when its `provider_status` entry has both a
usable call status (`success`/`partial`/`fallback_used`) and a usable data
status (e.g. `live`/`cached`/`fallback_used`) — `not_connected`, `failed`,
and `unavailable` providers are excluded (Step 152:
`ProviderCoverageService.record_provider_result`).

The frontend transparency panel (docs/16_frontend_architecture.md section
28) may display, directly from this response and without adding any new
field:

- `provider_coverage` summary values, one per tracked coverage area
- `provider_status` entries grouped by `provider_type` (Places, Weather,
  Holidays, Currency, Routes, Accommodation, Other)
- `unavailable_data` entries with their `field`, `reason`, and
  `data_status`
- `data_sources_used` exactly as returned, with no reordering or filtering
  that would misrepresent what was actually used

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

---

## 27. Regeneration Safety Endpoints

Regeneration is not implemented yet. These three endpoints exist so the
frontend can honestly show why, without pretending a regeneration engine is
connected. None of them run the planning pipeline, and none of them create
a new plan version.

### GET `/trips/{trip_id}/regeneration-readiness`

Purpose: explains whether feedback-driven regeneration could run right
now, and what it would need.

Mutation behavior: none. This is a pure read of `regeneration_readiness`,
which is recomputed from scratch on the write paths that can affect it
(trip creation, `POST /generate`, `POST /feedback`, lock create, lock
remove) — never by this endpoint itself.

Response data:

```json
{
  "trip_id": "trip_001",
  "regeneration_readiness": {
    "status": "blocked",
    "can_regenerate": false,
    "current_version": "v1",
    "would_create_version": "v2",
    "pending_feedback_count": 1,
    "active_lock_count": 0,
    "required_inputs": ["generated_plan", "pending_feedback", "regeneration_engine"],
    "available_inputs": ["generated_plan", "version_history", "pending_feedback", "plan_diff_preview"],
    "missing_capabilities": ["regeneration_engine"],
    "blocked_by": ["Feedback exists, but the regeneration engine is not implemented yet."],
    "next_step": "Implement the regeneration engine before applying feedback."
  }
}
```

`status` is always `"blocked"` and `can_regenerate` is always `false`
today. `would_create_version` is a hint only (what a version would be
labeled if regeneration existed) — it is never created.

### POST `/trips/{trip_id}/regenerate`

**POST /regenerate is a hard-refusal endpoint.** It never runs the
planning pipeline, never calls `POST /generate` internally, never calls an
AI or provider, and never creates a new plan version.

Response behavior:

* Unknown `trip_id` → `404 TRIP_NOT_FOUND`, same shape as every other
  endpoint. Nothing is recorded for a trip that does not exist.
* Existing `trip_id` → always `409 Conflict` with error code
  `REGENERATION_NOT_AVAILABLE` and this exact message:

  ```text
  Feedback-driven regeneration is not available yet. The regeneration
  engine has not been implemented, so no plan changes were made.
  ```

Mutation behavior: the only state change is appending one
`RegenerationAttempt` to `regeneration_attempts` (plus the resulting
`metadata.updated_at` bump). No itinerary content is changed. This call
never touches `experience_plan`, `destination_context`,
`validation_report`, `provider_coverage`, `route_feasibility_context`,
`feedback_history`, `pending_feedback_summary`, `user_locks`,
`version_history`, `plan_diff_preview`, or `regeneration_readiness`. It is
safe to call repeatedly.

Error response shape (same envelope as every other error):

```json
{
  "success": false,
  "data": null,
  "message": "Feedback-driven regeneration is not available yet. The regeneration engine has not been implemented, so no plan changes were made.",
  "errors": [
    {
      "code": "REGENERATION_NOT_AVAILABLE",
      "field": "regeneration",
      "message": "Feedback-driven regeneration is not available yet. The regeneration engine has not been implemented, so no plan changes were made."
    }
  ],
  "metadata": {}
}
```

### GET `/trips/{trip_id}/regeneration-attempts`

Purpose: returns the audit trail of every `POST /regenerate` call made
for a trip. The audit trail records blocked attempts only — it is never
evidence that regeneration ran.

Mutation behavior: none. Pure read of `regeneration_attempts`, returned in
stored (request) order.

Response data:

```json
{
  "trip_id": "trip_001",
  "regeneration_attempts": [
    {
      "attempt_id": "regen_attempt_9f1c2a...",
      "status": "blocked",
      "requested_at": "2026-07-27T21:32:46Z",
      "current_version": "v1",
      "would_create_version": "v2",
      "pending_feedback_count": 1,
      "active_lock_count": 0,
      "reason_code": "REGENERATION_NOT_AVAILABLE",
      "message": "Feedback-driven regeneration is not available yet. The regeneration engine has not been implemented, so no plan changes were made."
    }
  ]
}
```

Each attempt record contains only these nine fields. It never contains a
place name, coordinate, route, price, rating, or opening-hours value, and
it never claims feedback was applied, a diff was generated, or that any
locked item will definitely survive a future regeneration.

---

## 28. Candidate Quality Endpoint

### GET `/trips/{trip_id}/candidate-quality`

Purpose: returns the deterministic candidate quality report (Step 156A/
156B, docs/18_candidate_quality.md) computed from
`destination_context`'s existing `candidate_pois`/`candidate_restaurants`/
`candidate_accommodation_pois`. This is metadata/pre-ranking only — it
does not select, reorder, or alter `experience_plan` scheduling in this
step.

Mutation behavior: none. `candidate_quality_report` is recomputed only on
the destination-context write path (trip generation, or any future
regeneration that reruns that stage) — never by this endpoint itself.

If `destination_context` has not been generated yet for this trip,
`candidate_quality_report` is honestly `null` rather than fabricated.

Response data:

```json
{
  "trip_id": "trip_001",
  "candidate_quality_report": {
    "destination_name": "New York",
    "generated_at": "2026-07-29T18:00:00Z",
    "attraction_scores": [
      {
        "candidate_id": "way/123456",
        "candidate_name": "Empire State Building",
        "use_case": "attraction",
        "quality_tier": "primary_anchor",
        "total_score": 0.76,
        "score_components": {"category_signal": 0.9, "provider_confidence": 0.5},
        "positive_signals": ["Matches a must-visit request, which overrides weak-category signals."],
        "negative_signals": [],
        "reject_reasons": [],
        "source": "openstreetmap_places",
        "data_status": "live",
        "confidence": 0.5
      }
    ],
    "restaurant_scores": [],
    "accommodation_poi_scores": [],
    "summary": {
      "primary_anchor": 1,
      "good_candidate": 0,
      "secondary_candidate": 0,
      "low_priority": 0,
      "rejected": 0,
      "attraction_total": 1,
      "restaurant_total": 0,
      "accommodation_poi_total": 0
    }
  }
}
```

No score entry ever contains a price, rating, opening-hours, route-time,
review-count, booking-link, or safety-score field. A high `quality_tier`
is a pre-ranking signal only, never a claim of final quality, availability,
or bookability.

---

## 29. Generation Progress Endpoint

### GET `/trips/{trip_id}/generation-progress`

Purpose: returns real backend `PlanningOrchestrator` pipeline stage-progress
bookkeeping (Step 163B, docs/13_llm_reasoning_pipeline.md section 44) --
which stage of `POST /trips/{trip_id}/generate` is running or has run.
This is preparation for future frontend progress polling/animation; the
frontend is not wired to this endpoint yet, and the Step 163A decorative
loading animation continues to run entirely off local UI state, not this
data.

Mutation behavior: none. This endpoint never triggers generation and never
mutates state -- it only reads whatever `generation_progress` already
holds. If `generation_progress` hasn't been set yet (a planning state
persisted before this step), an idle default is returned instead of null.

Response data (idle, before generation):

```json
{
  "trip_id": "trip_001",
  "generation_progress": {
    "status": "idle",
    "current_stage": null,
    "current_stage_label": null,
    "completed_stages": [],
    "total_stages": 9,
    "progress_percent": 0,
    "message": "Generation has not started yet.",
    "updated_at": "2026-08-19T18:00:00Z",
    "is_real_backend_stage_progress": true
  }
}
```

Response data (after a successful generate):

```json
{
  "trip_id": "trip_001",
  "generation_progress": {
    "status": "completed",
    "current_stage": "post_processing",
    "current_stage_label": "Finalizing plan bookkeeping",
    "completed_stages": [
      "traveler_profile",
      "destination_context",
      "candidate_quality",
      "ai_candidate_shadow",
      "trip_strategy",
      "stay_transport",
      "experience_plan",
      "validation",
      "post_processing"
    ],
    "total_stages": 9,
    "progress_percent": 100,
    "message": "Backend pipeline generation completed.",
    "updated_at": "2026-08-19T18:00:03Z",
    "is_real_backend_stage_progress": true
  }
}
```

`generation_progress` never contains a flight route, flight number,
route/travel time, booking status, price, rating, or availability field --
`is_real_backend_stage_progress` exists specifically so this can never be
mistaken for real flight tracking or real travel movement. Returns 404
(`TRIP_NOT_FOUND`) if `trip_id` does not exist.
