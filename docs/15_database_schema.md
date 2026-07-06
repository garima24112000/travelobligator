# Database Schema

## 1. Purpose

This document defines the database schema for TravelObligator.

The database should support:

- trip creation
- Planning State storage
- version history
- feedback regeneration
- user locks
- provider status tracking
- provider coverage tracking
- unavailable data tracking
- safe caching
- debugging provider failures

The schema should preserve the core architecture rule:

```text
PlanningState is the source of truth for the current trip plan.
```

---

## 2. Database Strategy

TravelObligator should use a hybrid schema:

```text
Relational columns for IDs, status, timestamps, and lookup fields.
JSONB columns for flexible planning objects.
```

This is useful because many planning objects will evolve as the product grows.

Examples:

- traveler_profile
- destination_context
- trip_strategy
- stay_transport
- experience_plan
- validation_report
- provider_coverage

These are structured, but they may change often during early development.

---

## 3. Recommended Database

Recommended MVP database:

```text
PostgreSQL
```

Recommended cache layer later:

```text
Redis
```

For early local development, the backend may start with in-memory repositories, but the production schema should be designed around PostgreSQL.

---

## 4. Core Tables

Required MVP tables:

```text
trips
planning_states
planning_state_versions
feedback_events
user_locks
provider_logs
provider_cache
```

Optional future tables:

```text
users
shared_trips
saved_places
saved_accommodations
audit_logs
```

---

## 5. trips

Stores the top-level trip record.

Each trip has one latest Planning State.

```sql
CREATE TABLE trips (
  trip_id UUID PRIMARY KEY,
  user_id UUID NULL,

  destination_scope TEXT NOT NULL DEFAULT 'single_city',
  primary_destination JSONB NOT NULL,
  start_date DATE NOT NULL,
  end_date DATE NOT NULL,

  trip_status TEXT NOT NULL DEFAULT 'draft',
  latest_planning_state_id UUID NULL,

  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### Notes

`trip_status` values may include:

```text
draft
generating
validated
needs_review
blocked
updated_after_feedback
failed
```

`primary_destination` example:

```json
{
  "city": "Washington DC",
  "country": "United States",
  "coordinates": {
    "lat": 38.9072,
    "lng": -77.0369
  }
}
```

---

## 6. planning_states

Stores the latest working Planning State.

This table may store one row per active Planning State update or one current row per trip depending on implementation preference.

Recommended MVP approach:

```text
Save a new Planning State row after every major stage.
The trip points to the latest Planning State.
```

```sql
CREATE TABLE planning_states (
  planning_state_id UUID PRIMARY KEY,
  trip_id UUID NOT NULL REFERENCES trips(trip_id) ON DELETE CASCADE,

  current_version TEXT NOT NULL DEFAULT 'v1',
  pipeline_status TEXT NOT NULL DEFAULT 'draft',
  active_stage TEXT NULL,

  trip_request JSONB NOT NULL,
  traveler_profile JSONB NULL,
  destination_context JSONB NULL,
  trip_strategy JSONB NULL,
  stay_transport JSONB NULL,
  experience_plan JSONB NULL,
  validation_report JSONB NULL,

  decision_cards JSONB NOT NULL DEFAULT '[]',
  experience_cards JSONB NOT NULL DEFAULT '[]',
  validation_cards JSONB NOT NULL DEFAULT '[]',

  feedback_history JSONB NOT NULL DEFAULT '[]',
  user_locks JSONB NOT NULL DEFAULT '[]',

  provider_status JSONB NOT NULL DEFAULT '{}',
  provider_coverage JSONB NOT NULL DEFAULT '{}',
  unavailable_data JSONB NOT NULL DEFAULT '[]',
  data_sources_used JSONB NOT NULL DEFAULT '[]',

  metadata JSONB NOT NULL DEFAULT '{}',

  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### Notes

This table should support fast dashboard loading.

The frontend should be able to render the trip directly from this table’s Planning State fields.

---

## 7. planning_state_versions

Stores immutable snapshots of major Planning State versions.

A new version should be created when user-visible planning output changes.

```sql
CREATE TABLE planning_state_versions (
  version_id UUID PRIMARY KEY,
  trip_id UUID NOT NULL REFERENCES trips(trip_id) ON DELETE CASCADE,
  planning_state_id UUID NOT NULL REFERENCES planning_states(planning_state_id) ON DELETE CASCADE,

  version_label TEXT NOT NULL,
  created_by TEXT NOT NULL,
  feedback_event_id UUID NULL,

  summary TEXT NULL,
  changed_sections JSONB NOT NULL DEFAULT '[]',
  preserved_sections JSONB NOT NULL DEFAULT '[]',

  planning_state_snapshot JSONB NOT NULL,

  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### `created_by` values

```text
initial_generation
user_feedback
manual_regeneration
system_revalidation
```

### Notes

`planning_state_snapshot` should store the full Planning State at that version.

This allows:

- version comparison
- rollback later
- debugging
- showing what changed after feedback

---

## 8. feedback_events

Stores each user feedback event.

```sql
CREATE TABLE feedback_events (
  feedback_event_id UUID PRIMARY KEY,
  trip_id UUID NOT NULL REFERENCES trips(trip_id) ON DELETE CASCADE,

  planning_state_id_before UUID NOT NULL,
  planning_state_id_after UUID NULL,

  feedback_text TEXT NOT NULL,
  feedback_type TEXT NULL,
  affected_stages JSONB NOT NULL DEFAULT '[]',
  regeneration_strategy TEXT NOT NULL,

  change_summary JSONB NOT NULL DEFAULT '{}',
  follow_up_question TEXT NULL,

  created_new_version TEXT NULL,

  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### Regeneration strategy values

```text
explanation_only
section_level_update
day_level_update
pipeline_level_update
full_regeneration
```

### Notes

If feedback is vague, `follow_up_question` may be populated and no regeneration may occur.

Example:

```text
I want it better.
```

Should create a follow-up question instead of blindly regenerating the full plan.

---

## 9. user_locks

Stores user-approved items that should not be changed accidentally.

```sql
CREATE TABLE user_locks (
  lock_id UUID PRIMARY KEY,
  trip_id UUID NOT NULL REFERENCES trips(trip_id) ON DELETE CASCADE,

  locked_item_type TEXT NOT NULL,
  locked_item_id TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT 'user_approved',

  is_active BOOLEAN NOT NULL DEFAULT TRUE,

  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  removed_at TIMESTAMPTZ NULL
);
```

### Allowed locked item types

```text
stay_area
accommodation
experience
restaurant
day_plan
transport_strategy
```

### Notes

The Feedback Pipeline should check this table before regenerating affected sections.

---

## 10. provider_logs

Stores provider calls for debugging and transparency.

```sql
CREATE TABLE provider_logs (
  provider_log_id UUID PRIMARY KEY,
  trip_id UUID NULL REFERENCES trips(trip_id) ON DELETE CASCADE,
  planning_state_id UUID NULL REFERENCES planning_states(planning_state_id) ON DELETE CASCADE,

  provider_name TEXT NOT NULL,
  provider_type TEXT NOT NULL,

  request_summary JSONB NULL,
  status TEXT NOT NULL,
  data_status TEXT NOT NULL,

  fallback_used BOOLEAN NOT NULL DEFAULT FALSE,
  fallback_provider TEXT NULL,

  unavailable_fields JSONB NOT NULL DEFAULT '[]',
  error_message TEXT NULL,

  retrieved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  freshness_expires_at TIMESTAMPTZ NULL,

  confidence NUMERIC(4,3) NULL
);
```

### Provider types

```text
places
routes
transit
accommodation
flight
weather
holiday
currency
ai_reasoning
```

### Important Rule

Provider logs should not store:

- API keys
- tokens
- secrets
- full restricted provider responses unless allowed
- sensitive user data unless necessary

---

## 11. provider_cache

Stores reusable provider results when caching is allowed.

```sql
CREATE TABLE provider_cache (
  cache_id UUID PRIMARY KEY,

  provider_name TEXT NOT NULL,
  provider_type TEXT NOT NULL,

  cache_key TEXT NOT NULL UNIQUE,
  request_hash TEXT NOT NULL,

  response_data JSONB NOT NULL,
  data_status TEXT NOT NULL,
  source_license TEXT NULL,

  retrieved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at TIMESTAMPTZ NOT NULL,

  can_reuse_across_users BOOLEAN NOT NULL DEFAULT FALSE
);
```

### Cacheable examples

```text
place coordinates
place categories
OpenStreetMap POIs
public holidays
currency conversion for short periods
destination context fragments
```

### Short-cache examples

```text
route travel time
weather
accommodation availability
accommodation price
flight price
flight availability
```

### Not safe for broad reuse

```text
Traveler Profile
Trip Strategy
Experience Plan
Validation Report
Feedback Interpretation
user-entered accommodation or flight details
```

---

## 12. Optional users table

If authentication is added later:

```sql
CREATE TABLE users (
  user_id UUID PRIMARY KEY,
  email TEXT UNIQUE NULL,
  display_name TEXT NULL,

  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

For MVP, `user_id` can remain nullable if authentication is not implemented yet.

---

## 13. Indexes

Recommended indexes:

```sql
CREATE INDEX idx_trips_user_id ON trips(user_id);
CREATE INDEX idx_trips_status ON trips(trip_status);

CREATE INDEX idx_planning_states_trip_id ON planning_states(trip_id);
CREATE INDEX idx_planning_states_status ON planning_states(pipeline_status);
CREATE INDEX idx_planning_states_created_at ON planning_states(created_at);

CREATE INDEX idx_versions_trip_id ON planning_state_versions(trip_id);
CREATE INDEX idx_versions_label ON planning_state_versions(version_label);

CREATE INDEX idx_feedback_trip_id ON feedback_events(trip_id);
CREATE INDEX idx_feedback_created_at ON feedback_events(created_at);

CREATE INDEX idx_user_locks_trip_id ON user_locks(trip_id);
CREATE INDEX idx_user_locks_active ON user_locks(is_active);

CREATE INDEX idx_provider_logs_trip_id ON provider_logs(trip_id);
CREATE INDEX idx_provider_logs_provider ON provider_logs(provider_name, provider_type);
CREATE INDEX idx_provider_logs_status ON provider_logs(status);

CREATE INDEX idx_provider_cache_key ON provider_cache(cache_key);
CREATE INDEX idx_provider_cache_expires_at ON provider_cache(expires_at);
```

---

## 14. Planning State Persistence Rule

Planning State should be saved after every major stage:

```text
after TravelerProfileService
after DestinationContextService
after TripStrategyService
after StayTransportService
after ExperiencePlannerService
after PlanValidatorService
after FeedbackService
```

This supports:

- frontend progress display
- debugging failed stages
- recovery after provider failures
- partial regeneration
- version history

---

## 15. Versioning Rules

Create a new `planning_state_versions` row when:

- initial full plan is generated
- feedback changes user-visible output
- stay area changes
- accommodation recommendations change
- itinerary changes
- validation status changes meaningfully
- user locks affect regeneration behavior

Do not create a new version for:

- internal logging only
- provider retry with no user-visible change
- metadata-only update that does not affect the plan

---

## 16. Provider Coverage Storage

Provider coverage should be stored in `planning_states.provider_coverage`.

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

This lets the frontend show:

- what was searched
- what was unavailable
- what was not connected
- what was open-data-backed
- what was provider-confirmed

---

## 17. Unavailable Data Storage

Unavailable data should be stored in `planning_states.unavailable_data`.

Example:

```json
[
  {
    "field": "vacation_rental_prices",
    "reason": "No approved vacation-rental provider is connected.",
    "data_status": "not_connected",
    "source": null
  },
  {
    "field": "restaurant_ratings",
    "reason": "OpenStreetMap restaurant data does not provide ratings.",
    "data_status": "unavailable",
    "source": "openstreetmap"
  }
]
```

Unavailable data should not be treated as an error by default.

It becomes an error only when the missing data makes the plan misleading or infeasible.

---

## 18. Data Safety Rules

The database should never store fake production facts.

Do not store:

- fake accommodation listings
- fake restaurant ratings
- fake prices
- fake availability
- fake flight options
- scraped restricted provider results
- AI-invented provider data

If a value is unknown, store it as:

```text
null
unavailable
not_connected
low confidence
```

Do not store it as a made-up value.

---

## 19. JSONB Validation Strategy

Even though flexible fields are stored as JSONB, the backend should validate them using Pydantic models before saving.

Validate:

- enum values
- required fields
- data status fields
- provider status fields
- confidence values
- claim sources
- unavailable fields
- card structures

Database flexibility should not mean unvalidated data.

---

## 20. Future Normalized Tables

Later, if the product grows, some JSONB data can be normalized.

Possible future tables:

```text
places
restaurants
accommodations
routes
daily_plans
experience_items
cards
provider_sources
```

For MVP, JSONB is acceptable because the data model is still evolving.

---

## 21. Migration Tool

Recommended migration tool:

```text
Alembic
```

All schema changes should be added through migrations once the backend moves beyond early development.

---

## 22. Design Principles

The database schema should follow these principles:

- Store PlanningState as the source of truth.
- Save after every major planning stage.
- Preserve version history.
- Preserve feedback history.
- Preserve user locks.
- Store provider coverage clearly.
- Store unavailable data explicitly.
- Do not require fake fallback values.
- Do not store mock data as production data.
- Keep provider logs useful but safe.
- Cache only when allowed and not misleading.
- Use JSONB for evolving planning objects.
- Use relational columns for IDs, status, and lookup fields.