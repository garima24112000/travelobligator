# TravelObligator Architecture

TravelObligator is built as a staged travel decision platform.

It should not work like:

```text
user input → one LLM prompt → itinerary
```

It should work like:

```text
User Input
→ Traveler Profile
→ Destination Context
→ Trip Strategy
→ Stay + Transport
→ Experience Planner
→ Plan Validator
→ Feedback Pipeline
→ Final Itinerary
```

The itinerary is the final artifact.  
The decision pipeline is the product.

---

## 1. Core Architecture Principle

The backend should operate around one central object:

```text
PlanningState
```

Every stage reads from PlanningState, updates only the section it owns, and passes the updated state forward.

PlanningState stores:

- trip request
- traveler profile
- destination context
- trip strategy
- stay and transport decisions
- accommodation recommendations
- experience plan
- validation report
- feedback history
- user locks
- explanation cards
- provider status
- provider coverage
- unavailable data
- version history

---

## 2. MVP Scope

The MVP supports single-city trips.

The architecture should still remain flexible enough for future multi-city support.

For MVP:

```text
destination_scope = single_city
```

Future versions may support:

- multiple destination segments
- intercity transport
- city-level date ranges
- city-specific stay decisions
- city-specific experience plans
- cross-city validation

---

## 3. Planning Flow

### Stage 1: Traveler Profile

Purpose:

Convert raw user input into structured planning preferences.

Consumes:

- trip request
- user preferences
- free-text constraints
- intensity scale

Produces:

- traveler profile
- decision weights
- confidence levels
- assumptions

AI may interpret free text, but should not invent user constraints.

---

### Stage 2: Destination Context

Purpose:

Create a provider-backed or open-data-backed snapshot of the destination.

Consumes:

- trip request
- traveler profile when available

Produces:

- destination overview
- candidate POI clusters
- neighborhood candidates
- rough transport feasibility
- average cost hints when available
- provider coverage
- unavailable data

Destination Context should not choose final attractions, restaurants, accommodations, or itinerary days.

---

### Stage 3: Trip Strategy

Purpose:

Define the high-level planning direction.

Consumes:

- traveler profile
- destination context
- provider coverage
- unavailable data

Produces:

- destination suitability
- duration assessment
- budget assessment
- recommended trip style
- planning strategy
- planning targets
- tradeoffs
- assumptions
- confidence

Trip Strategy should not select final attractions, restaurants, or accommodation options.

---

### Stage 4: Stay + Transport

Purpose:

Decide where the traveler should stay and how they should move.

Consumes:

- traveler profile
- destination context
- trip strategy

Produces:

- recommended stay area
- alternative stay areas
- transport strategy
- top accommodation options
- stay and transport decision cards
- provider coverage notes
- unavailable accommodation fields

The system should recommend accommodation options, not final bookings.

Accommodation options may include hotels, motels, hostels, resorts, serviced apartments, guesthouses, boutique stays, or vacation rentals only when supported by a legitimate connected source or open-data source.

---

### Stage 5: Experience Planner

Purpose:

Create the day-wise experience plan.

Consumes:

- traveler profile
- destination context
- trip strategy
- stay and transport decisions

Produces:

- trip overview
- daily plan
- selected experiences
- restaurant recommendations when provider-backed or open-data-backed
- meal-area suggestions when restaurant data is insufficient
- experience cards
- decision cards
- estimated walking
- estimated cost
- planning metadata

The Experience Planner must not invent attractions, restaurants, ratings, opening hours, prices, availability, or route times.

---

### Stage 6: Plan Validator

Purpose:

Review the itinerary before it is shown as final.

Consumes:

- traveler profile
- destination context
- trip strategy
- stay and transport decisions
- experience plan
- provider coverage
- unavailable data

Produces:

- validation report
- validation cards
- critical issues
- warnings
- suggestions
- readiness status

Allowed readiness statuses:

```text
ready
needs_review
blocked
```

The validator does not modify the itinerary directly.

---

### Stage 7: Feedback Pipeline

Purpose:

Update the plan when the user gives feedback.

Consumes:

- full PlanningState
- user feedback
- user locks
- version history

Produces:

- feedback interpretation
- affected stages
- regeneration strategy
- updated PlanningState
- change summary
- new version when needed

The Feedback Pipeline should update only affected sections whenever possible.

---

## 4. Provider Architecture

TravelObligator should use a replaceable provider layer.

Planning services should not call external APIs directly.

They should go through:

```text
ProviderGateway
```

ProviderGateway is responsible for:

- calling providers
- retrying failed requests
- using fallbacks when available
- normalizing responses
- tracking provider status
- tracking provider coverage
- marking unavailable fields
- preventing fake fallback data

Provider interfaces:

```text
PlacesProvider
RoutesProvider
TransitProvider
AccommodationProvider
FlightProvider
WeatherProvider
HolidayProvider
CurrencyProvider
AIReasoningProvider
```

---

## 5. Legit-Only Data Policy

TravelObligator must not use:

- mock accommodation listings
- mock restaurant ratings
- mock prices
- mock availability
- scraped restricted provider data
- AI-invented factual travel data

Allowed factual sources:

- user-provided data
- official APIs
- approved partner or affiliate APIs
- open-data sources
- deterministic calculations based on legitimate data

If data is unavailable, the system should mark it as unavailable.

Unavailable data is acceptable.  
Fake data is not.

---

## 6. Open Data and Provider Data

The MVP may use:

- OpenStreetMap / Overpass for POIs
- Nominatim or GeoNames for location resolution
- OpenTripPlanner + GTFS + OpenStreetMap for routing and transit feasibility
- Open-Meteo for weather
- Nager.Date for public holidays
- Frankfurter for currency conversion
- Amadeus APIs where production access is available
- Google Places, Google Routes, Mapbox, or other approved providers where available
- OpenAI Structured Outputs for reasoning and explanation only

Restricted providers such as Airbnb, Booking.com, Expedia, Vrbo, Tripadvisor, and Google Flights should not be scraped or implied as searched unless approved access exists.

---

## 7. AI Reasoning Policy

AI is a reasoning layer, not a data provider.

AI may:

- interpret free-text preferences
- explain recommendations
- summarize provider-backed or open-data-backed facts
- generate decision card wording
- perform subjective validation reasoning
- interpret feedback
- explain provider coverage limitations

AI must not invent:

- places
- restaurants
- accommodations
- flights
- prices
- ratings
- review counts
- availability
- opening hours
- route times
- walking distances
- booking links
- provider coverage
- safety ratings

All AI outputs should be structured and schema-validated.

---

## 8. Safety Policy

The MVP does not generate direct safety scores.

The system should focus on safety-related planning considerations such as:

- late-night travel
- long walking segments
- poor transit alignment
- remote or isolated movement
- weather exposure
- traveler-specific comfort constraints
- route uncertainty
- low provider confidence

The system should not label places as safe or unsafe without authoritative data.

---

## 9. Backend Structure

Suggested backend structure:

```text
backend/
  app/
    api/
    core/
    models/
    services/
    providers/
    validators/
    repositories/
    schemas/
    tests/
```

Main backend services:

```text
PlanningOrchestrator
TravelerProfileService
DestinationContextService
TripStrategyService
StayTransportService
ExperiencePlannerService
PlanValidatorService
FeedbackService
UserLockService
VersioningService
ProviderCoverageService
```

---

## 10. Frontend Structure

Suggested frontend structure:

```text
frontend/
  app/
  components/
  lib/
```

The frontend should render from PlanningState.

Main dashboard sections:

- trip header
- planning status
- provider coverage banner
- trip strategy summary
- stay and transport section
- accommodation options section
- day-wise experience plan
- validation section
- feedback box
- version history panel
- provider transparency panel

---

## 11. Database Strategy

Recommended database:

```text
PostgreSQL
```

Recommended storage approach:

```text
Relational columns for IDs, status, timestamps, and lookup fields.
JSONB columns for evolving planning objects.
```

Core tables:

```text
trips
planning_states
planning_state_versions
feedback_events
user_locks
provider_logs
provider_cache
```

PlanningState should be saved after every major stage.

---

## 12. Implementation Direction

Implementation should happen in this order:

1. Shared data models
2. Standard API response wrapper
3. In-memory repositories for development
4. PlanningState model
5. PlanningOrchestrator skeleton
6. Stage service skeletons
7. ProviderGateway interface
8. AIReasoningProvider
9. OpenStreetMap / Overpass provider
10. Destination Context Service
11. Traveler Profile Service
12. Trip Strategy Service
13. Stay + Transport Service
14. Experience Planner Service
15. Plan Validator Service
16. Feedback and User Lock flow
17. PostgreSQL persistence
18. Frontend dashboard rendering

---

## 12a. Local Development Persistence (Interim, Pre-Database)

Before PostgreSQL persistence (see section 11 and step 17 of section 12) is
implemented, trip and planning-state repositories persist to a local JSON
file so data survives backend reloads during local development:

```text
backend/.data/travelobligator_state.json
```

Key points:

- File-backed only, Python standard library only (`json`, `tempfile`,
  `os.replace`). No cloud database, no external services, no auth.
- The file holds two top-level collections, `trips` and `planning_states`,
  keyed by `trip_id`. Records are the same Pydantic models used everywhere
  else, serialized with `model_dump(mode="json")` and rehydrated with
  `model_validate`.
- Writes are atomic: a temp file is written in the same directory and then
  swapped into place with `os.replace`, so a crash mid-write cannot corrupt
  the file. A missing file is treated as empty storage. A file that exists
  but is not valid JSON raises a clear error instead of being silently
  replaced -- the persistence layer never fabricates data to paper over a
  corrupt file.
- The storage path is configurable via `LOCAL_STORAGE_PATH` (see
  `app/core/config.py`); the default is safe for local development and
  resolves relative to the backend project root regardless of the process's
  working directory.
- `backend/.data/` is local-only and is `.gitignore`d. It is not part of the
  product's data model and is not shared between developers or environments.
- This is **not** production persistence: no multi-worker/multi-process
  coordination, no migrations, no user accounts, no auth. It exists purely
  so a single local developer doesn't lose in-progress trips when the
  backend process restarts. The PostgreSQL plan in section 11 remains the
  intended production replacement.

---

## 13. Design Principles

- PlanningState is the source of truth.
- Each stage owns one section.
- Providers supply facts.
- AI supplies reasoning and explanation.
- Missing data must be explicit.
- Provider coverage must be visible.
- Feedback should trigger the smallest valid update path.
- User locks should preserve approved items.
- Validation should happen before final presentation.
- The itinerary is the final artifact, not the whole product.