# TravelObligator Tasks

This file tracks implementation tasks for TravelObligator.

Architecture V1 is finalized.  
The next phase is implementation.

---

## 1. Current Status

Completed:

- Product vision finalized
- MVP scope finalized
- Planning pipeline finalized
- Production data policy finalized
- PlanningState architecture finalized
- Data model documented
- API contracts documented
- Provider architecture documented
- LLM reasoning policy documented
- Backend architecture documented
- Database schema documented
- Frontend architecture documented
- Root project docs updated

Next focus:

```text
Implement the backend foundation first.
```

---

## 2. Implementation Principles

All implementation work must follow these rules:

- PlanningState is the source of truth.
- Do not use mock data as production truth.
- Do not invent provider-backed facts.
- Do not scrape restricted travel platforms.
- Mark unavailable data explicitly.
- Track provider status and provider coverage.
- AI is used for reasoning and explanation, not factual data generation.
- Every major recommendation should be explainable.
- Validation must run before the plan is shown as final.
- Feedback should regenerate only affected sections where possible.

---

## 3. Phase 1: Shared Types and Backend Models

Goal:

Create implementation models that match `docs/10_data_model.md`.

Tasks:

- [ ] Define shared TypeScript types in `shared/types.ts`
- [ ] Define backend Pydantic models
- [ ] Define common enums:
  - [ ] `DataStatus`
  - [ ] `ProviderStatus`
  - [ ] `ClaimSourceType`
  - [ ] `ReadinessStatus`
  - [ ] `ValidationSeverity`
  - [ ] `RegenerationStrategy`
  - [ ] `AccommodationType`
- [ ] Define common models:
  - [ ] `SourceAttribution`
  - [ ] `DataQuality`
  - [ ] `GeoPoint`
  - [ ] `MoneyAmount`
  - [ ] `TimeWindow`
  - [ ] `ClaimSource`
  - [ ] `ProviderStatusEntry`
  - [ ] `ProviderCoverage`
- [ ] Define planning models:
  - [ ] `TripRequest`
  - [ ] `TravelerProfile`
  - [ ] `DestinationContext`
  - [ ] `TripStrategy`
  - [ ] `StayTransportDecision`
  - [ ] `AccommodationOption`
  - [ ] `TransportStrategy`
  - [ ] `ExperiencePlan`
  - [ ] `DailyPlan`
  - [ ] `ExperienceItem`
  - [ ] `RestaurantOption`
  - [ ] `MealPlanItem`
  - [ ] `ValidationReport`
  - [ ] `FeedbackEvent`
  - [ ] `UserLock`
  - [ ] `PlanningState`

---

## 4. Phase 2: API Foundation

Goal:

Create a clean FastAPI foundation based on `docs/11_api_contracts.md`.

Tasks:

- [ ] Create standard API response wrapper
- [ ] Create standard error response shape
- [ ] Add API error codes
- [ ] Implement health endpoint
- [ ] Implement trip creation endpoint
- [ ] Implement get trip endpoint
- [ ] Implement provider coverage endpoint
- [ ] Add request validation
- [ ] Add response validation

Endpoints:

- [ ] `GET /health`
- [ ] `POST /trips`
- [ ] `GET /trips/{trip_id}`
- [ ] `GET /trips/{trip_id}/provider-coverage`

---

## 5. Phase 3: Repository Layer

Goal:

Create storage interfaces before adding real database persistence.

Tasks:

- [ ] Create `TripRepository`
- [ ] Create `PlanningStateRepository`
- [ ] Create `VersionRepository`
- [ ] Create `FeedbackRepository`
- [ ] Create `UserLockRepository`
- [ ] Create `ProviderLogRepository`
- [ ] Create `CacheRepository`
- [ ] Start with in-memory repositories for development
- [ ] Keep repository interface compatible with future PostgreSQL implementation

Important:

In-memory repositories are allowed for development.

Fake travel facts are not allowed.

---

## 6. Phase 4: Provider Gateway Skeleton

Goal:

Create replaceable provider interfaces based on `docs/12_provider_architecture.md`.

Tasks:

- [ ] Create `ProviderGateway`
- [ ] Create standard provider response model
- [ ] Create provider status tracking
- [ ] Create provider coverage tracking
- [ ] Create unavailable field handling
- [ ] Create fallback handling structure
- [ ] Create provider log writing

Provider interfaces:

- [ ] `PlacesProvider`
- [ ] `RoutesProvider`
- [ ] `TransitProvider`
- [ ] `AccommodationProvider`
- [ ] `FlightProvider`
- [ ] `WeatherProvider`
- [ ] `HolidayProvider`
- [ ] `CurrencyProvider`
- [ ] `AIReasoningProvider`

Development behavior:

- [ ] Return `not_connected` for providers not implemented
- [ ] Return `unavailable` for unavailable fields
- [ ] Do not return fake places, hotels, restaurants, ratings, prices, or availability

---

## 7. Phase 5: Planning Orchestrator Skeleton

Goal:

Create the orchestration layer that controls pipeline order.

Tasks:

- [ ] Create `PlanningOrchestrator`
- [ ] Add `create_trip`
- [ ] Add `generate_full_plan`
- [ ] Add stage runner methods
- [ ] Add partial regeneration method
- [ ] Save PlanningState after each stage
- [ ] Track active stage
- [ ] Track pipeline status
- [ ] Return updated PlanningState

Stage order:

```text
TravelerProfileService
→ DestinationContextService
→ TripStrategyService
→ StayTransportService
→ ExperiencePlannerService
→ PlanValidatorService
```

---

## 8. Phase 6: Stage Service Skeletons

Goal:

Create one service per planning stage.

Tasks:

- [ ] Create `TravelerProfileService`
- [ ] Create `DestinationContextService`
- [ ] Create `TripStrategyService`
- [ ] Create `StayTransportService`
- [ ] Create `ExperiencePlannerService`
- [ ] Create `PlanValidatorService`
- [ ] Create `FeedbackService`
- [ ] Create `UserLockService`
- [ ] Create `VersioningService`
- [ ] Create `ProviderCoverageService`

Initial behavior:

- [ ] Each service reads PlanningState
- [ ] Each service updates only its owned section
- [ ] Each service records assumptions and confidence
- [ ] Each service handles unavailable data honestly
- [ ] Each service returns updated PlanningState

---

## 9. Phase 7: LLM Reasoning Layer

Goal:

Implement controlled AI reasoning based on `docs/13_llm_reasoning_pipeline.md`.

Tasks:

- [ ] Create `AIReasoningProvider` interface
- [ ] Create OpenAI structured output adapter
- [ ] Add schema validation for AI output
- [ ] Add retry on invalid JSON
- [ ] Add hallucination checks
- [ ] Add claim source validation
- [ ] Add confidence reduction when data is missing
- [ ] Add unavailable data handling

AI must not invent:

- [ ] places
- [ ] restaurants
- [ ] accommodation options
- [ ] flights
- [ ] prices
- [ ] ratings
- [ ] review counts
- [ ] opening hours
- [ ] availability
- [ ] route times
- [ ] walking distances
- [ ] booking links
- [ ] provider coverage
- [ ] safety ratings

---

## 10. Phase 8: Real/Open Data Providers

Goal:

Start replacing unavailable provider responses with legitimate open-data-backed responses.

Recommended order:

- [ ] OpenStreetMap / Overpass place search
- [ ] OpenStreetMap / Overpass restaurant and cafe POIs
- [ ] OpenStreetMap / Overpass accommodation POIs
- [ ] Nominatim or GeoNames destination resolution
- [ ] Open-Meteo weather
- [ ] Nager.Date holidays
- [ ] Frankfurter currency conversion
- [ ] OpenTripPlanner / GTFS routing where available
- [ ] Amadeus hotel APIs when production access is available
- [ ] Amadeus flight APIs when production access is available
- [ ] Google Places / Google Routes / Mapbox as optional approved providers

Rules:

- [ ] If OpenStreetMap has no rating, show rating unavailable
- [ ] If OpenStreetMap has no price, show price unavailable
- [ ] If OpenStreetMap has no availability, show availability unavailable
- [ ] If a provider is not connected, do not imply it was searched

---

## 11. Phase 9: Stage Implementations

Goal:

Implement each planning stage with real logic.

### Traveler Profile

- [ ] Convert trip request into structured traveler profile
- [ ] Interpret free text
- [ ] Derive decision weights
- [ ] Add confidence levels
- [ ] Add assumptions

### Destination Context

- [ ] Resolve destination
- [ ] Fetch candidate POIs
- [ ] Fetch restaurant/food area candidates
- [ ] Fetch accommodation POIs
- [ ] Build attraction clusters
- [ ] Build neighborhood candidates
- [ ] Record provider coverage
- [ ] Record unavailable data

### Trip Strategy

- [ ] Generate destination suitability
- [ ] Generate duration assessment
- [ ] Generate budget assessment
- [ ] Generate recommended trip style
- [ ] Generate planning targets
- [ ] Generate decision cards

### Stay + Transport

- [ ] Recommend stay area
- [ ] Recommend alternative stay areas
- [ ] Generate transport strategy
- [ ] Rank accommodation options
- [ ] Mark unavailable price/rating/availability fields
- [ ] Generate stay and transport decision cards

### Experience Planner

- [ ] Select experiences from provider/open-data candidates
- [ ] Group experiences geographically
- [ ] Build day-wise itinerary
- [ ] Add meal breaks
- [ ] Use restaurant recommendations only when backed by legitimate data
- [ ] Use meal-area fallback when restaurant data is insufficient
- [ ] Generate experience cards

### Plan Validator

- [ ] Validate walking burden
- [ ] Validate route realism
- [ ] Validate timing
- [ ] Validate budget
- [ ] Validate pace
- [ ] Validate provider coverage issues
- [ ] Validate safety-related planning considerations
- [ ] Assign readiness status

### Feedback Pipeline

- [ ] Interpret feedback
- [ ] Detect affected stages
- [ ] Respect user locks
- [ ] Choose regeneration strategy
- [ ] Rerun affected stages
- [ ] Create change summary
- [ ] Create new version

---

## 12. Phase 10: Frontend Implementation

Goal:

Render PlanningState in the dashboard.

Tasks:

- [ ] Create frontend API client
- [ ] Create shared frontend types
- [ ] Create trip creation form
- [ ] Create trip dashboard page
- [ ] Create planning progress UI
- [ ] Create provider coverage banner
- [ ] Create trip strategy section
- [ ] Create stay and transport section
- [ ] Create accommodation options section
- [ ] Create experience plan section
- [ ] Create daily plan cards
- [ ] Create meal plan cards
- [ ] Create validation section
- [ ] Create feedback box
- [ ] Create user lock buttons
- [ ] Create version history panel
- [ ] Create provider transparency panel

Frontend rule:

Do not hardcode fake travel facts.

Show:

```text
Unavailable
Not connected
Estimated
Open-data-backed
Provider-confirmed
```

instead of fake values.

---

## 13. Phase 11: Database Persistence

Goal:

Move from in-memory repositories to PostgreSQL.

Tasks:

- [ ] Add PostgreSQL connection
- [ ] Add Alembic
- [ ] Create database migrations
- [ ] Create `trips` table
- [ ] Create `planning_states` table
- [ ] Create `planning_state_versions` table
- [ ] Create `feedback_events` table
- [ ] Create `user_locks` table
- [ ] Create `provider_logs` table
- [ ] Create `provider_cache` table
- [ ] Replace in-memory repositories with database repositories
- [ ] Add indexes
- [ ] Add JSONB validation before save

---

## 14. Phase 12: Testing

Goal:

Make sure the architecture rules are enforced.

Test categories:

### API Tests

- [ ] Create trip
- [ ] Get trip
- [ ] Generate full plan
- [ ] Apply feedback
- [ ] Add lock
- [ ] Remove lock
- [ ] Get provider coverage

### Service Tests

- [ ] Each service updates only owned section
- [ ] PlanningState is saved after each stage
- [ ] Partial regeneration updates correct sections
- [ ] User locks are respected

### Provider Tests

- [ ] Provider success
- [ ] Provider failure
- [ ] Provider not connected
- [ ] Fallback used
- [ ] Unavailable fields returned
- [ ] Provider coverage updated

### AI Safety Tests

- [ ] AI invents restaurant
- [ ] AI invents accommodation
- [ ] AI invents rating
- [ ] AI invents price
- [ ] AI claims unavailable provider was searched
- [ ] AI returns invalid JSON
- [ ] AI ignores unavailable data

Unsafe AI output should be rejected.

### Validator Tests

- [ ] Excessive walking
- [ ] Missing route data
- [ ] Budget exceeded
- [ ] Accommodation price unavailable
- [ ] Restaurant rating unavailable
- [ ] Provider coverage mismatch
- [ ] Blocked itinerary

---

## 15. Not In MVP

Do not implement yet:

- final hotel booking
- final flight booking
- final tour booking
- scraping Airbnb
- scraping Booking.com
- scraping Expedia or Vrbo
- scraping Tripadvisor
- scraping Google Flights
- direct safety scoring
- live crowd prediction
- visa logic
- SIM card recommendations
- emergency healthcare logic
- full multi-city optimization

---

## 16. Immediate Next Tasks

Start implementation in this order:

1. Update `shared/types.ts`
2. Create backend Pydantic models
3. Create API response wrapper
4. Create in-memory repositories
5. Create PlanningOrchestrator skeleton
6. Create service skeletons
7. Create ProviderGateway skeleton
8. Connect trip creation endpoint to PlanningState
9. Connect frontend trip creation to backend
10. Render PlanningState in dashboard

---

## 17. Definition of Done for MVP Architecture Implementation

The MVP architecture implementation is complete when:

- [ ] User can create a trip
- [ ] Backend creates PlanningState
- [ ] Pipeline stages update PlanningState
- [ ] Provider coverage is visible
- [ ] Unavailable data is explicit
- [ ] No fake travel facts are shown
- [ ] AI output is schema-validated
- [ ] Experience plan is generated from available data
- [ ] Plan is validated before final display
- [ ] Feedback can update affected sections
- [ ] User locks are respected
- [ ] Frontend renders decision cards, experience cards, validation cards, and provider transparency