# Backend Architecture

## 1. Purpose

This document defines the backend architecture for TravelObligator.

The backend should turn the product architecture into clear services that operate on one central object:

```text
PlanningState
```

The goal is to make the backend:

- modular
- testable
- provider-safe
- explainable
- easy to debug
- easy to extend
- safe from mock or hallucinated data
- ready for partial regeneration after feedback

---

## 2. Core Backend Principle

The backend should not behave like one large endpoint that sends everything to an LLM.

Instead, it should behave like a staged planning system:

```text
Trip Request
→ PlanningState
→ TravelerProfileService
→ DestinationContextService
→ TripStrategyService
→ StayTransportService
→ ExperiencePlannerService
→ PlanValidatorService
→ FeedbackService
```

Each service owns one section of the Planning State.

Each service should update only the section it owns unless an explicit regeneration path allows otherwise.

---

## 3. High-Level Backend Flow

Normal plan generation should follow this flow:

```text
POST /trips
→ create initial PlanningState

POST /trips/{trip_id}/generate
→ TravelerProfileService
→ DestinationContextService
→ TripStrategyService
→ StayTransportService
→ ExperiencePlannerService
→ PlanValidatorService
→ return updated PlanningState
```

Feedback flow should follow this flow:

```text
POST /trips/{trip_id}/feedback
→ FeedbackService
→ determine affected stages
→ rerun only required services
→ PlanValidatorService
→ create new version
→ return updated PlanningState
```

---

## 4. Main Backend Components

The backend should be organized around these components:

```text
API Layer
Service Layer
Provider Layer
Repository Layer
Validation Layer
LLM Reasoning Layer
Orchestration Layer
```

---

## 5. API Layer

The API Layer exposes FastAPI endpoints.

Responsibilities:

- receive requests
- validate request shape
- call orchestration or stage services
- return standard API responses
- never contain business logic directly
- never call external providers directly
- never call LLMs directly

Example endpoints:

```text
POST /trips
GET /trips/{trip_id}
POST /trips/{trip_id}/generate
POST /trips/{trip_id}/traveler-profile
POST /trips/{trip_id}/destination-context
POST /trips/{trip_id}/trip-strategy
POST /trips/{trip_id}/stay-transport
POST /trips/{trip_id}/experience-plan
POST /trips/{trip_id}/validate
POST /trips/{trip_id}/feedback
POST /trips/{trip_id}/locks
GET /trips/{trip_id}/versions
GET /trips/{trip_id}/provider-coverage
```

---

## 6. Orchestration Layer

The Orchestration Layer controls the full pipeline.

Main class:

```text
PlanningOrchestrator
```

Responsibilities:

- load current Planning State
- run stages in the correct order
- stop when required data is missing
- continue with low confidence when safe
- update pipeline status
- call validation after planning
- save Planning State after each major stage
- create version history
- prevent invalid stage order
- handle full generation and partial regeneration

The orchestrator should not contain provider-specific logic.

It should call services.

---

## 7. PlanningOrchestrator

Suggested methods:

```text
create_trip(trip_request)
generate_full_plan(trip_id, force_regenerate)
run_traveler_profile_stage(planning_state)
run_destination_context_stage(planning_state)
run_trip_strategy_stage(planning_state)
run_stay_transport_stage(planning_state)
run_experience_plan_stage(planning_state)
run_validation_stage(planning_state)
apply_feedback(trip_id, feedback_request)
rerun_affected_stages(planning_state, affected_stages)
```

Normal full generation order:

```text
traveler_profile
destination_context
trip_strategy
stay_transport
experience_plan
validation_report
```

The orchestrator should not skip required upstream stages unless the Planning State already contains valid current output.

---

## 8. Service Layer

The Service Layer contains one service per planning stage.

Required services:

```text
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

Each service should:

- receive Planning State
- read only needed sections
- update owned section
- add cards or metadata if needed
- return updated Planning State
- avoid unrelated modifications

---

## 9. TravelerProfileService

Owns:

```text
traveler_profile
```

Consumes:

```text
trip_request
```

May use:

```text
AIReasoningProvider
```

Does not use:

```text
travel providers
```

Responsibilities:

- interpret user input
- create structured traveler profile
- derive decision weights
- derive mobility, budget, stay, transport, and interest profiles
- assign confidence levels
- mark assumptions

Must not:

- invent user constraints
- overstate confidence when input is vague
- call places, routes, accommodation, or flight providers

---

## 10. DestinationContextService

Owns:

```text
destination_context
provider_status
provider_coverage
unavailable_data
data_sources_used
```

Consumes:

```text
trip_request
traveler_profile
```

Uses:

```text
ProviderGateway
PlacesProvider
RoutesProvider
TransitProvider
WeatherProvider when enabled
HolidayProvider when enabled
CurrencyProvider when needed
```

Responsibilities:

- resolve destination
- collect candidate POIs
- collect neighborhood candidates
- collect attraction clusters
- collect rough transport feasibility
- collect average cost hints when available
- record provider coverage
- record unavailable provider data

Must not:

- select final attractions
- select final restaurants
- select final accommodation options
- create day-wise itinerary

Destination Context is a candidate-data snapshot, not the final plan.

---

## 11. TripStrategyService

Owns:

```text
trip_strategy
decision_cards when needed
```

Consumes:

```text
traveler_profile
destination_context
provider_coverage
unavailable_data
```

May use:

```text
AIReasoningProvider
deterministic strategy rules
```

Responsibilities:

- evaluate destination suitability
- assess duration
- assess budget
- recommend trip style
- create planning strategy
- create planning targets
- explain tradeoffs
- record assumptions
- set confidence

Must not:

- select final attractions
- select final restaurants
- select final accommodations
- invent destination facts
- invent costs not supported by source data or explicit estimates

---

## 12. StayTransportService

Owns:

```text
stay_transport
decision_cards related to stay and transport
provider_status when provider calls are made
provider_coverage when provider coverage changes
unavailable_data when relevant
```

Consumes:

```text
traveler_profile
destination_context
trip_strategy
provider_coverage
```

Uses:

```text
ProviderGateway
PlacesProvider
RoutesProvider
TransitProvider
AccommodationProvider
AIReasoningProvider for explanations only
```

Responsibilities:

- recommend stay area
- recommend alternative stay areas
- recommend transport strategy
- rank top accommodation options
- explain stay and transport tradeoffs
- label accommodation coverage
- mark unavailable price, rating, review, or availability fields

Must not:

- start with accommodation before stay area
- imply restricted providers were searched when not connected
- invent accommodation names
- invent prices
- invent availability
- invent ratings
- generate direct safety scores

---

## 13. ExperiencePlannerService

Owns:

```text
experience_plan
experience_cards
decision_cards related to itinerary choices
provider_status when provider calls are made
provider_coverage when provider coverage changes
unavailable_data when relevant
```

Consumes:

```text
traveler_profile
destination_context
trip_strategy
stay_transport
provider_coverage
```

Uses:

```text
ProviderGateway
PlacesProvider
RoutesProvider
TransitProvider
WeatherProvider when enabled
HolidayProvider when enabled
AIReasoningProvider for explanations only
```

Responsibilities:

- select provider/open-data-backed experiences
- select provider/open-data-backed restaurants when available
- use meal-area fallback when restaurant data is insufficient
- group activities geographically
- schedule activities into days
- respect planning targets
- respect walking and pace constraints
- add meal breaks
- estimate walking and cost where allowed
- create experience cards
- create day summaries

Must not:

- invent attractions
- invent restaurants
- invent ratings
- invent opening hours
- invent exact route times
- invent exact walking distances
- invent prices
- overload days just because time exists

---

## 14. PlanValidatorService

Owns:

```text
validation_report
validation_cards
```

Consumes:

```text
traveler_profile
destination_context
trip_strategy
stay_transport
experience_plan
provider_status
provider_coverage
unavailable_data
```

Uses:

```text
Rule validators
AIReasoningProvider for subjective reasoning only
ProviderGateway when validation needs fresh provider checks
```

Responsibilities:

- run deterministic validation first
- validate route feasibility
- validate walking burden
- validate timing
- validate budget
- validate pace
- validate experience variety
- validate safety-related planning considerations
- assign readiness status
- create validation cards
- surface unavailable critical data

Must not:

- modify itinerary directly
- invent validation facts
- invent closure issues
- invent route problems
- invent safety ratings
- override deterministic validation results

Allowed readiness statuses:

```text
ready
needs_review
blocked
```

As of Step 156G (docs/18_candidate_quality.md section 9), `PlanValidatorService`
surfaces unscheduled must-visits honestly: it compares `trip_request.must_visit`
(or `traveler_profile.must_visit`) against the names of experiences actually
present in `experience_plan.daily_plans`, not just against
`destination_context.candidate_pois`. This catches both a must-visit that
was never grounded to a real candidate at all, and a grounded must-visit
candidate that trust-over-fullness scheduling (Step 156E) excluded for a
severe candidate-quality reason (missing coordinates, insufficient
provider confidence). Any unmatched must-visits produce a single
`category="must_visit"` warning naming them, never a substituted/invented
attraction, and never a `ready` readiness status by itself.

---

## 15. FeedbackService

Owns:

```text
feedback_history
affected_sections decision
regeneration_strategy decision
change_summary
```

May update affected sections through orchestrator-controlled regeneration.

Consumes:

```text
full PlanningState
user feedback
user_locks
version_history
provider_coverage
```

Uses:

```text
AIReasoningProvider
VersioningService
UserLockService
PlanningOrchestrator
```

Responsibilities:

- interpret feedback
- classify feedback type
- identify affected stages
- choose smallest valid regeneration path
- preserve user-approved sections
- respect user locks
- generate change summary
- trigger revalidation
- create new version when plan changes

Must not:

- regenerate everything by default
- ignore locks
- silently remove must-visit items
- invent replacement options
- claim unavailable provider data exists

---

## 16. UserLockService

Owns:

```text
user_locks
```

Responsibilities:

- create locks
- remove locks
- check whether a section/item is locked
- protect locked items during feedback regeneration
- explain when a lock must be overridden

Allowed locked item types:

```text
stay_area
accommodation
experience
restaurant
day_plan
transport_strategy
```

---

## 17. VersioningService

Owns:

```text
version_history
metadata.current_version
```

Responsibilities:

- create initial version
- create new version after feedback
- track changed sections
- track preserved sections
- attach feedback event IDs
- retrieve old versions
- support future rollback

A new version should be created when user-visible planning output changes.

---

## 18. Provider Layer

The Provider Layer should be accessed only through:

```text
ProviderGateway
```

Planning services should not call provider adapters directly.

ProviderGateway responsibilities:

- choose provider adapter
- retry when appropriate
- use fallback when available
- normalize response
- mark unavailable fields
- update provider status
- update provider coverage
- prevent fake fallback data
- prevent restricted provider claims

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

## 19. Repository Layer

The Repository Layer handles persistence.

Required repositories:

```text
TripRepository
PlanningStateRepository
VersionRepository
FeedbackRepository
ProviderLogRepository
CacheRepository
UserLockRepository
```

Repositories should not contain planning logic.

They should only store and retrieve data.

---

## 20. TripRepository

Responsibilities:

- create trip record
- get trip by ID
- update trip status
- link trip to latest Planning State
- store basic trip metadata

---

## 21. PlanningStateRepository

Responsibilities:

- save Planning State
- get latest Planning State
- get Planning State by version
- update specific sections when needed
- store provider status and coverage
- store unavailable data

PlanningState should be stored after each major pipeline stage.

This helps debugging and recovery.

---

## 22. ProviderLogRepository

Responsibilities:

- store provider request status
- store provider type
- store unavailable fields
- store fallback usage
- store response freshness
- store related trip ID
- store related Planning State ID

Should not store:

- API keys
- tokens
- secrets
- full sensitive provider responses unless needed and allowed

---

## 23. CacheRepository

Responsibilities:

- cache allowed provider responses
- respect freshness rules
- avoid caching restricted data when not allowed
- separate user-specific state from reusable provider data

Cacheable examples:

- place coordinates
- open-data POIs
- public holidays
- destination context fragments
- route matrix when allowed

Short-cache examples:

- accommodation price
- accommodation availability
- route travel time
- weather

---

## 24. Validation Layer

The Validation Layer includes deterministic validators.

Suggested validators:

```text
DateValidator
BudgetValidator
WalkingValidator
RouteValidator
TimeWindowValidator
OpeningHoursValidator
PaceValidator
MealBreakValidator
ProviderCoverageValidator
SafetyRelatedPlanningValidator
SchemaValidator
```

Rule validators should produce consistent results for the same input.

AI validation should run only after deterministic validators.

---

## 25. LLM Reasoning Layer

The LLM Reasoning Layer should be accessed through:

```text
AIReasoningProvider
```

Responsibilities:

- build controlled prompts
- pass only allowed input sections
- enforce structured output
- validate schema
- reject hallucinated output
- retry invalid output
- return safe reasoning result

The service layer should not directly construct uncontrolled prompts.

`backend/app/models/ai_reasoning.py` contains future AI reasoning
contracts (`AIReasoningRequest`, `AIReasoningResult`, and the task-specific
result models, docs/13_llm_reasoning_pipeline.md section 26). These models
prepare for LangGraph/LangSmith integration but do not activate either
yet -- no LLM provider, LangGraph, or LangSmith dependency is wired up as
of this step.

`backend/app/services/ai_reasoning_contract_builder.py` contains
`AIReasoningContractBuilder` (docs/13_llm_reasoning_pipeline.md section 27),
which prepares typed `AIReasoningRequest` inputs for a future
`AIReasoningProvider`/LangGraph node from existing `PlanningState` data.
It is still pre-LLM and behavior-neutral -- it does not call a provider or
LLM, and nothing in the app currently calls it.

`backend/app/models/ai_candidate_proposal.py` contains contract-only
models for a future AI-assisted candidate discovery layer (Step 157A,
docs/13_llm_reasoning_pipeline.md section 28,
itinerary-generator-build-spec.md Stage 5). They do not call an LLM yet,
and they do not create schedulable candidates -- every `AICandidateProposal`
still requires independent grounding/verification against provider/open
data (build spec Stage 6) before it can be scheduled.

`backend/app/providers/ai_candidate_proposal/` contains the provider
boundary for those contract models (Step 157B,
docs/13_llm_reasoning_pipeline.md section 29): an `abc.ABC` interface,
`AICandidateProposalProvider`, with one abstract method (`propose`), and a
default `NotConnectedAICandidateProposalProvider` adapter that always
returns an honest `not_connected` result -- empty proposals, zero
confidence, and a failed guardrail report. There is no real LLM behavior
yet, and this provider is not wired into `PlanningOrchestrator` or any
stage service.

`backend/app/models/candidate_grounding.py` contains contract-only models
for the candidate grounding/verification step that would sit between AI
candidate proposals and future scheduling (Step 158A,
docs/13_llm_reasoning_pipeline.md section 30, build spec Stage 6). They
are contract-only and do not call a provider or LLM yet, and nothing
mutates `PlanningState`. `GroundedCandidate` prepares the bridge from an
`AICandidateProposal` to a future scheduling-eligible candidate -- it may
carry a coordinate only because it comes from `CandidateGroundingEvidence`
(a real provider/open-data fact), never from the AI proposal's own
wording -- and `RejectedCandidateProposal` prepares the bridge to a future
"what the AI suggested but we didn't use" trust UI.

`backend/app/services/candidate_grounding_service.py` contains
`CandidateGroundingService` for the Stage 6 grounding step. Its NotConnected
skeleton (Step 158B, docs/13_llm_reasoning_pipeline.md section 31) has
evolved into deterministic supplied-candidate grounding (Step 159A,
docs/13_llm_reasoning_pipeline.md section 32): `ground` now matches each
`AICandidateProposal` against `ProviderCandidateForGrounding` entries
explicitly supplied on `CandidateGroundingRequest.provider_candidates`,
using exact case-insensitive or normalized name matching only -- no fuzzy
matching, no substring matching, and no provider/open-data lookup of its
own. Empty `proposals` still returns `skipped`; proposals present with
empty `provider_candidates` still returns `not_connected`. A proposal
grounds into a `GroundedCandidate` only when exactly one supplied provider
candidate matches it; zero or multiple matches produce a
`RejectedCandidateProposal` instead. Still no LLM call, no provider call,
and no orchestration wiring -- it is not wired into `PlanningOrchestrator`,
scheduling, validation, or regeneration.

`backend/app/services/candidate_grounding_request_builder.py` contains
`CandidateGroundingRequestBuilder` (Step 159B,
docs/13_llm_reasoning_pipeline.md section 33), which bridges existing
`PlanningState.destination_context` candidates (`candidate_pois`,
`candidate_restaurants`, `candidate_accommodation_pois`) into
`ProviderCandidateForGrounding` entries and assembles a
`CandidateGroundingRequest` from them plus caller-supplied
`AICandidateProposal` objects. It performs no provider calls and no LLM
calls -- every candidate it produces already existed on `PlanningState`;
when no explicit provider/place-id field is available it falls back to
internal references (`"destination_context"` / `"destination_context.
candidate_pois[0]"`) that point back at the existing record, never a new
provider claim. It never calls `CandidateGroundingService.ground`, never
mutates `PlanningState`, and is not wired into `PlanningOrchestrator` or
any orchestration path yet.

`backend/app/services/candidate_quality_service.py` contains
`CandidateQualityService` (Step 156A, docs/18_candidate_quality.md), a
deterministic pre-ranking layer that sits between provider-backed
candidate collection (`DestinationContextService`) and future
scheduling/ranking work. It scores and classifies existing
`candidate_pois`/`candidate_restaurants`/`candidate_accommodation_pois`
entries by category/name/address keyword heuristics, provider confidence,
and coordinate presence only. It does not call an LLM, LangGraph,
LangSmith, or any provider; it does not create a new place, restaurant, or
accommodation; and it does not mutate `PlanningState` -- `build_report`
only reads it. A high `CandidateQualityTier` is a pre-ranking signal only,
never a claim of final quality, availability, or bookability.

As of Step 156B, `PlanningOrchestrator.run_destination_context_stage`
calls `CandidateQualityService.build_report` immediately after
`DestinationContextService.run` and stores the result on
`planning_state.candidate_quality_report`, exposed read-only via
`GET /trips/{trip_id}/candidate-quality` (docs/11_api_contracts.md section
28). This happens between destination-context generation and any future
scheduling improvement.

As of Step 156C, `ExperiencePlannerService` uses `candidate_quality_report`
as deterministic pre-ranking metadata before scheduling: it reads the
report (never mutating it) to exclude `rejected` candidates and prefer
higher-quality candidates when scheduling attractions and suggesting
nearby restaurants/accommodation POIs. See docs/18_candidate_quality.md
section 7 for the full scheduling behavior. If no report is available,
scheduling falls back to the exact pre-156C behavior.

As of Step 156E, `ExperiencePlannerService` follows trust-over-fullness
scheduling (itinerary-generator-build-spec.md Stage 8): it does not fill
days with `low_priority` candidates by default. Only `primary_anchor`/
`good_candidate`/`secondary_candidate` attractions are eligible for
scheduling; if there aren't enough of them to fill every day/pace slot,
the day is left lighter instead, with an honest per-day warning explaining
why. See docs/18_candidate_quality.md section 8 for the full behavior.

---

## 26. Suggested Backend Folder Structure

Suggested structure:

```text
backend/
  app/
    main.py
    core/
      config.py
      errors.py
      logging.py
      response.py
    api/
      routes/
        health.py
        trips.py
        planning.py
        feedback.py
        locks.py
        versions.py
        provider_coverage.py
    models/
      trip_request.py
      planning_state.py
      traveler_profile.py
      destination_context.py
      trip_strategy.py
      stay_transport.py
      experience_plan.py
      validation_report.py
      feedback.py
      cards.py
      providers.py
      common.py
      ai_reasoning.py
    services/
      planning_orchestrator.py
      traveler_profile_service.py
      destination_context_service.py
      trip_strategy_service.py
      stay_transport_service.py
      experience_planner_service.py
      plan_validator_service.py
      feedback_service.py
      user_lock_service.py
      versioning_service.py
      provider_coverage_service.py
      ai_reasoning_contract_builder.py
    providers/
      gateway.py
      base.py
      places/
        base.py
        openstreetmap_adapter.py
        google_places_adapter.py
      routes/
        base.py
        opentripplanner_adapter.py
        google_routes_adapter.py
        mapbox_adapter.py
      transit/
        base.py
        gtfs_adapter.py
      accommodation/
        base.py
        openstreetmap_adapter.py
        amadeus_adapter.py
      weather/
        base.py
        open_meteo_adapter.py
      holidays/
        base.py
        nager_date_adapter.py
      currency/
        base.py
        frankfurter_adapter.py
      ai/
        base.py
        openai_structured_outputs_adapter.py
    validators/
      date_validator.py
      budget_validator.py
      walking_validator.py
      route_validator.py
      pace_validator.py
      provider_coverage_validator.py
      safety_related_planning_validator.py
      schema_validator.py
    repositories/
      trip_repository.py
      planning_state_repository.py
      version_repository.py
      feedback_repository.py
      provider_log_repository.py
      cache_repository.py
      user_lock_repository.py
    schemas/
      api_responses.py
      errors.py
    tests/
      services/
      providers/
      validators/
      api/
```

---

## 27. Dependency Direction

Allowed dependency direction:

```text
API routes
→ Services / Orchestrator
→ ProviderGateway / Repositories / Validators
→ Provider Adapters / Database
```

Not allowed:

```text
Provider Adapter → Planning Service
Repository → Service
Frontend → Provider directly
API Route → Provider Adapter directly
LLM → Provider directly
```

This keeps the backend modular.

---

## 28. Stage Persistence Rule

After each major stage, save Planning State.

Example:

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

- debugging
- partial recovery
- version history
- frontend progress display
- failed-stage diagnosis

---

## 29. Provider Failure Handling

Provider failures should be handled consistently.

Provider failure flow:

```text
provider call fails
→ retry if appropriate
→ fallback if available
→ mark unavailable if still failed
→ update provider_status
→ update provider_coverage
→ lower confidence
→ continue only if honest output is still possible
→ validator flags feasibility risk if needed
```

The backend must never replace missing provider data with:

- fake data
- mock data
- scraped data
- AI-generated facts

---

## 30. Development Mode Rules

Development mode can use incomplete providers.

But incomplete providers must return:

```text
not_connected
unavailable
low confidence
```

Development mode must not return fake production facts.

Allowed:

```json
{
  "provider_name": "accommodation_provider",
  "status": "not_connected",
  "data": null,
  "unavailable_fields": ["price", "availability", "rating"],
  "confidence": 0.0
}
```

Not allowed:

```json
{
  "name": "Fake Central Hotel",
  "rating": 4.8,
  "price": 199
}
```

---

## 31. Testing Strategy

The backend should include tests for:

### Service Tests

- Traveler Profile generation
- Destination Context generation
- Trip Strategy generation
- Stay + Transport ranking
- Experience Plan generation
- Validation Report generation
- Feedback regeneration

### Provider Tests

- provider success
- provider failure
- fallback used
- not connected provider
- unavailable fields
- open-data-only response

### Validator Tests

- excessive walking
- missing route data
- budget exceeded
- unavailable accommodation price
- unavailable restaurant rating
- provider coverage mismatch
- blocked itinerary

### LLM Safety Tests

- AI invents restaurant
- AI invents accommodation
- AI invents rating
- AI claims Booking.com searched when not connected
- AI returns invalid JSON
- AI ignores unavailable data

All unsafe outputs should be rejected.

---

## 32. Implementation Order

Recommended backend implementation order:

1. Create Pydantic models from `10_data_model.md`.
2. Create standard API response wrapper.
3. Create in-memory PlanningStateRepository for development.
4. Create TripRepository.
5. Create PlanningOrchestrator skeleton.
6. Create stage service skeletons.
7. Create ProviderGateway interface.
8. Implement AIReasoningProvider adapter.
9. Implement OpenStreetMap / Overpass PlacesProvider.
10. Implement basic DestinationContextService.
11. Implement TravelerProfileService.
12. Implement TripStrategyService.
13. Implement StayTransportService with unavailable accommodation fields when providers are missing.
14. Implement ExperiencePlannerService with meal-area fallback.
15. Implement deterministic PlanValidatorService.
16. Implement FeedbackService and UserLockService.
17. Add persistence with Postgres.
18. Add caching where safe.
19. Add richer providers later.

---

## 33. Design Principles

The backend architecture should follow these principles:

- PlanningState is the backend source of truth.
- Services own specific Planning State sections.
- Providers supply facts.
- AI supplies reasoning and explanation.
- Provider failures must be visible.
- Missing data must be explicit.
- Development mode must not use fake production facts.
- Validation should happen before final presentation.
- Feedback should rerun only affected stages where possible.
- Repositories store data; they do not make planning decisions.

---

## 34. Regeneration Safety Lifecycle

Regeneration is not implemented yet. The backend still gives the user an
honest, staged view of what feedback-driven regeneration would need and
why it cannot run today. Each stage below is owned by its own service and
none of them call an LLM or a provider.

1. **Feedback capture** — `FeedbackService.apply_feedback` appends one
   `FeedbackEvent` to `feedback_history` using deterministic rule-based
   keyword classification. This never regenerates any plan section; it
   only records the feedback and a preliminary, honest interpretation.
2. **Pending summary** — `FeedbackService` also recomputes
   `pending_feedback_summary` from `feedback_history` on every submission,
   purely as a rollup of what has been captured so far.
3. **Plan diff preview** — `PlanDiffPreviewService.recompute` rebuilds
   `plan_diff_preview` from scratch from `version_history`,
   `feedback_history`, and `user_locks`. It is preview-only: it never
   claims a diff was generated, and it never runs regeneration.
4. **Readiness gate** — `RegenerationReadinessService.recompute` rebuilds
   `regeneration_readiness` from the same three inputs and explains why
   regeneration is blocked (`status` is always `"blocked"`,
   `can_regenerate` is always `false`).
5. **Hard-refusal endpoint** — `POST /trips/{trip_id}/regenerate` is a
   hard-refusal endpoint. For an existing trip it always responds
   `409 REGENERATION_NOT_AVAILABLE`. No new plan version is created, no
   itinerary content is changed, and no planning stage is rerun.
6. **Audit trail** — `RegenerationAttemptService.record_blocked_attempt`
   appends one `RegenerationAttempt` to `regeneration_attempts` each time
   `POST /regenerate` is called for an existing trip. The audit trail
   records blocked attempts only; `GET /trips/{trip_id}/regeneration-attempts`
   returns it read-only, in stored order.

State `POST /regenerate` is allowed to change:

```text
regeneration_attempts   (one RegenerationAttempt appended)
metadata.updated_at     (bumped as a side effect of the append)
```

State `POST /regenerate` must never change:

```text
experience_plan
destination_context
validation_report (readiness_status or any other field)
provider_coverage
route_feasibility_context
feedback_history
pending_feedback_summary
user_locks
version_history
plan_diff_preview
regeneration_readiness
```

Until a real regeneration engine is connected, `POST /trips/{trip_id}/generate`
remains the only endpoint that produces or changes a full plan.
- API routes coordinate requests; they do not contain planning logic.