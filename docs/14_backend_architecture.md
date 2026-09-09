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

## 23. CacheRepository / ProviderCacheStore (Step 164A)

This section originally described an aspirational cache layer; as of Step
164A, a real (but not-yet-wired) implementation exists:
`backend/app/storage/provider_cache_store.py` (`ProviderCacheStore`), a
small local SQLite store (`sqlite3`, Python stdlib only) keyed by
`(source, query_hash)`, alongside `Settings.provider_cache_path`/
`provider_cache_enabled` in `backend/app/core/config.py`
(docs/12_provider_architecture.md section 25 has the full design:
canonical-JSON `query_hash`, TTL semantics, and per-source TTL guidance).

**Wired into four provider adapters so far (Step 164B, extended in Steps
164C, 164D, and 164E).** `OpenMeteoWeatherAdapter` is the first consumer of
`ProviderCacheStore` (docs/12_provider_architecture.md section 26,
docs/13_llm_reasoning_pipeline.md section 47) -- it caches successful,
usable Open-Meteo weather responses under source `"open_meteo"`, keyed by
a hash of the normalized request (latitude, longitude, start/end date,
timezone). `NagerDateHolidaysAdapter` is the second consumer
(docs/12_provider_architecture.md section 27, docs/13_llm_reasoning_pipeline.md
section 48) -- it caches successful, usable public-holiday responses under
source `"nager_date"`, keyed per calendar year by a hash of the normalized
request (`country_code`, `year`), matching Nager.Date's own per-year API
shape rather than the trip's date range. `FrankfurterCurrencyAdapter` is
the third consumer (docs/12_provider_architecture.md section 28,
docs/13_llm_reasoning_pipeline.md section 49) -- it caches successful
exchange-rate responses fetched over the network under source
`"frankfurter"`, keyed by a hash of the normalized request
(`base_currency`, `destination_currency`, a fixed `"latest"` marker); the
same-currency identity result (no HTTP call at all) is not cached.
`OpenStreetMapPlacesAdapter` is the fourth consumer
(docs/12_provider_architecture.md sections 29-30,
docs/13_llm_reasoning_pipeline.md sections 50 and 52). Its Nominatim
destination-lookup step (`_resolve_destination`, Step 164E) caches
successful, plausibility-checked results under source
`"openstreetmap_geocode"`, keyed by a hash of the normalized search text.
**As of Step 164G, its Overpass POI search path is also cache-wired** --
`_try_query` (used by `search_attractions`/`search_restaurants`/
`search_accommodation_pois`, both the primary and every fallback tag
query) caches successful, non-empty, destination-contained results under
source `"openstreetmap_poi"`, keyed by a hash of the normalized Overpass
request (`lat`, `lon`, `radius_meters`, sorted tags, `limit`).
`search_must_visit_place`'s Nominatim named-place lookup remains
uncached. `ProviderGateway` and `PlanningOrchestrator` still do not import
or call `ProviderCacheStore` -- every call through them still goes out
live (or reports honestly as `not_connected`/`unavailable`) exactly as
before these steps. A cache miss from this store always means "miss,"
never a guessed or fabricated payload -- confirmed for Open-Meteo,
Nager.Date, Frankfurter, OSM geocoding, and OSM POI search specifically by
`unavailable`/`failed`/unresolved/empty responses never being written to
the cache. **Provider behavior is otherwise identical apart from cache
hit/miss as the source of a result** -- the `ProviderResponse.status`/
`data_status`/`fallback_used` envelope returned by `search_attractions`/
`search_restaurants`/`search_accommodation_pois` is unchanged by caching;
only each individual cached place's own `data_status` field is relabeled
`"cached"`, which nothing in candidate quality, scheduling, validation, or
provider coverage tracking reads.

**Cache failure is non-fatal for all four consumers.** A broken cache
read falls back to the live Open-Meteo/Nager.Date/Frankfurter/Nominatim/
Overpass request; a broken cache write still returns the already-computed
live result. Weather, holiday, currency, geocoding, and POI search
retrieval can never fail because the cache layer failed.

**Manual live smoke coverage (Step 164F, extended in Step 164H).**
`backend/scripts/manual_provider_cache_smoke.py` (manual/dev-only,
docs/21_manual_provider_cache_smoke.md) exercises all four cache-wired
providers against their real public APIs, including OSM geocoding
(`resolve_coordinates`) and, as of Step 164H, one respectful OSM/Overpass
POI search (`search_attractions`, one known destination) -- proving a
real Overpass response parses and the persistent cache is used, but not
validating `search_restaurants`, `search_accommodation_pois`,
`search_must_visit_place`, or any other destination/category.

**Test isolation fix (Step 164G).** Wiring POI search into the same
lazily-resolved cache singleton geocoding already used surfaced a latent
cross-test contamination risk for any test constructing a real provider
adapter without an explicit `cache_store` (not limited to
`test_openstreetmap_adapter.py`). `backend/app/tests/conftest.py` gained
an autouse `_isolate_provider_cache_store` fixture, mirroring the
pre-existing `_reset_in_memory_repositories` fixture, giving every test a
fresh, throwaway provider cache store -- a test suite fix, not a
production behavior change.

**Routing provider skeleton (Step 165A) -- originally separate from this
cache, now partly wired to it (Step 165C, below).**
`backend/app/providers/routing/` adds a `RoutingProvider` contract
(`backend/app/models/routing.py`'s `RouteRequest`/`RouteResult`) and two
adapters: `NotConnectedRoutingProvider` (the default,
`Settings.routing_provider="not_connected"`) and `OSRMRoutingAdapter`
(selected via `routing_provider="osrm"`, but itself still `not_connected`
unless `Settings.osrm_base_url` is also explicitly set -- conservative by
design).

**`ProviderGateway` has routing lookup capability (Step 165B) -- but
planning behavior is unchanged until a later Section 165 step.**
`ProviderGateway` (section 18) gained a `routing` attribute (defaulting to
`app.providers.routing.factory.get_routing_provider()`) and a
`get_route(request: RouteRequest) -> RouteResult` method that delegates to
it -- exposure through the gateway only, matching how every other provider
is accessed. `ProviderGateway.routes` (the older, generic, always-
`not_connected` `RoutesProvider()` base interface) is untouched and
remains separate. **`PlanningOrchestrator`, `ExperiencePlannerService`, and
`PlanValidatorService` still do not call `get_route` or reference
`routing` at all** -- confirmed by dedicated tests -- so scheduling,
validation, and provider coverage behavior are exactly as before this
step. See docs/12_provider_architecture.md sections 31-32 and
docs/13_llm_reasoning_pipeline.md sections 54-55 for the full design.

**OSRM route cache (Step 165C).** `OSRMRoutingAdapter` now reads through
and writes to this same `ProviderCacheStore`, under source label
`"osrm_route"`, keyed by a hash of the normalized route request
(origin/destination coordinates + resolved profile) and TTL-governed by
`Settings.osrm_route_cache_ttl_seconds` (default 24 hours). Only a
successful, usable route is cached; `not_connected`/`unavailable`/`failed`
results never are. Exactly like every other cache-wired adapter, cache
failure is non-fatal -- a broken read falls back to a live OSRM call, and a
broken write still returns the live result. `ProviderGateway` needed no
change since it already delegates through to whichever `RoutingProvider`
it holds. Route data is still not used in itinerary planning -- this is
purely about how a repeated identical route lookup is answered, not about
introducing routing into scheduling or validation. See
docs/12_provider_architecture.md section 33 and
docs/13_llm_reasoning_pipeline.md section 56.

**Manual live smoke coverage for OSRM route cache (Step 165D).**
`backend/scripts/manual_provider_cache_smoke.py` (docs/21_manual_provider_cache_smoke.md)
now also calls the real `OSRMRoutingAdapter` twice for one small, fixed
Lisbon-area route, confirming a live OSRM response parses and the route
cache is populated/reused -- structural checks only (`status=success`,
positive numeric distance/duration, a cache row present), never an exact
distance/duration/geometry value. This is manual-only, exactly like the
rest of that script: never run by pytest, `python -m compileall`, or CI.
Route data is still not used in itinerary planning -- this manual check
only confirms the adapter and its cache work when called directly.

Responsibilities (intended once wired in a future step):

- cache allowed provider responses
- respect freshness rules via `ttl_seconds`/`expires_at`
- avoid caching restricted data when not allowed
- separate reusable provider data from user-specific trip state (this
  store is not for `PlanningState`/trip content)

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

`backend/app/services/ai_candidate_proposal_request_builder.py` contains
`AICandidateProposalRequestBuilder` (Step 160A,
docs/13_llm_reasoning_pipeline.md section 34), which prepares the input
for the future AI candidate proposal provider (Step 157B's
`AICandidateProposalProvider`): it builds an `AICandidateProposalRequest`
from existing `PlanningState` trip metadata, explicit
`trip_request`/`traveler_profile` preference fields, `destination_context`
candidate counts only (never candidate names), and unavailable-data field
names from `unavailable_data`/`provider_status`. No LLM call, no provider
call, no proposal generation, no grounding, and no orchestration wiring
happens here -- it never calls `AICandidateProposalProvider`,
`NotConnectedAICandidateProposalProvider`, or `CandidateGroundingService`,
never mutates `PlanningState`, and is not wired into
`PlanningOrchestrator` yet.

`backend/app/services/ai_candidate_discovery_service.py` contains
`AICandidateDiscoveryService` (Step 160B, docs/13_llm_reasoning_pipeline.md
section 35), a dry-run composition service only: `dry_run` chains
`AICandidateProposalRequestBuilder` -> a proposal provider ->
`CandidateGroundingRequestBuilder` -> `CandidateGroundingService` and
returns all four intermediate objects together as
`AICandidateDiscoveryDryRunResult`. Its default proposal provider is the
Step 157B `NotConnectedAICandidateProposalProvider`, so by default it makes
no real LLM call, no provider call, and produces no proposals and no
grounded candidates; every dependency is constructor-injectable so tests
can prove the composition path with a deterministic fake provider without
adding a real one. It never mutates `PlanningState`, never persists
anything, and is not wired into `PlanningOrchestrator` yet.

`backend/app/models/planning_state.py` now also carries two optional
storage fields for the candidate-discovery flow above (Step 160C,
docs/13_llm_reasoning_pipeline.md section 36):
`ai_candidate_proposal_batch: AICandidateProposalBatch | None = None` and
`candidate_grounding_batch: CandidateGroundingBatch | None = None`. Both
reuse existing, already-validated batch models -- no new validation rule,
no raw prompt text, and no unvalidated provider response can be stored
there. Both default to `None` and are not runtime-populated by anything
yet: `PlanningOrchestrator` is unchanged by this step, so
`generate_full_plan` still leaves both fields `None` on every generated
`PlanningState`. This only prepares the storage shape for a future
shadow-mode integration; it does not wire `AICandidateDiscoveryService`
into generation.

`backend/app/tests/services/test_ai_candidate_discovery_safety.py`
(Step 160D, docs/13_llm_reasoning_pipeline.md section 37) adds safety
end-to-end tests for the whole candidate-discovery composition above
(Steps 157A-160C) using only in-file deterministic fake proposal
providers -- no real LLM, LangGraph, LangSmith, or provider adapter. It
proves unsafe/invalid AI-like output fails schema validation before
`CandidateGroundingService.ground` is ever called, unsupported (unmatched
or ambiguous) proposals become `RejectedCandidateProposal` rather than
being silently dropped or force-matched, matching proposals ground only
from evidence already present on `PlanningState.destination_context`, and
`dry_run` stays storage-neutral (Step 160C fields) and runtime-neutral
(`PlanningOrchestrator`, API routes, scheduling, validation, and
regeneration/feedback/versioning services still do not import
`AICandidateDiscoveryService`). This step changed no production code --
it is a precondition check performed before a real LLM-backed adapter is
ever connected, not an integration step.

`backend/app/providers/ai_candidate_proposal/factory.py` contains
`get_ai_candidate_proposal_provider` (Step 160E, docs/13_llm_reasoning_
pipeline.md section 38), a config-gated provider-selection boundary read
via `Settings.ai_candidate_proposal_provider`
(`AI_CANDIDATE_PROPOSAL_PROVIDER`, default `"not_connected"`). The default
AI candidate proposal provider remains `"not_connected"` -- no real LLM
provider is connected yet, and an unsupported/unrecognized config value
falls back to the same honest `NotConnectedAICandidateProposalProvider`
rather than raising or fabricating output. `AICandidateDiscoveryService`
now resolves its default `proposal_provider` through this factory instead
of constructing the not-connected adapter directly; explicit dependency
injection still bypasses the factory entirely, and default `dry_run`
behavior is unchanged.

`backend/app/providers/ai_candidate_proposal/anthropic_adapter.py`
contains `AnthropicAICandidateProposalProvider` (Step 161A,
docs/13_llm_reasoning_pipeline.md section 39) -- **Claude/Anthropic is the
planned LLM base for candidate proposal**. It calls Claude through the
official `anthropic` Python SDK's Messages API (`client.messages.create`
with a forced tool call), never through the Claude Code CLI. The adapter
is config-gated and not the default: `get_ai_candidate_proposal_provider`
now supports `"anthropic"` in addition to `"not_connected"`, but the
factory's default stays `"not_connected"`. With no `ANTHROPIC_API_KEY`
configured -- the default -- the adapter itself returns an honest
`not_connected` result rather than calling the network, and the
`anthropic` package is only ever imported lazily so the app and test
suite work whether or not it is installed. Every output still validates
through `AICandidateProposalResult`/`AICandidateProposal`, never calls
`CandidateGroundingService` or a provider adapter, and never mutates
`PlanningState`. There is no scheduling/regeneration integration -- it
stays reachable only through explicit injection, config, or (Step 161B
below) the optional shadow-mode orchestration.

`backend/app/providers/ai_candidate_proposal/groq_adapter.py` contains
`GroqAICandidateProposalProvider` (Step 162A,
docs/13_llm_reasoning_pipeline.md section 41) -- **Groq is a second,
low-cost dev-iteration LLM base for AI candidate proposals, alongside (not
replacing) Anthropic/Claude.** It calls Groq through
`langchain_groq.ChatGroq`'s structured-output support, never a raw HTTP
call. Like the Anthropic adapter, it is config-gated and not the default:
`get_ai_candidate_proposal_provider` now supports `"groq"` in addition to
`"not_connected"` and `"anthropic"`, but the factory's default stays
`"not_connected"`. With no `GROQ_API_KEY` configured -- the default -- the
adapter itself returns an honest `not_connected` result rather than
calling the network, and the `langchain_groq` package is only ever
imported lazily so the app and test suite work whether or not it is
installed. Every output still validates through
`AICandidateProposalResult`/`AICandidateProposal`, never calls
`CandidateGroundingService` or a provider adapter, and never mutates
`PlanningState`. There is no scheduling/regeneration integration -- it
stays reachable only through explicit injection or config, and it uses the
exact same shadow-mode-only path (Step 161B) as Anthropic, since that
stage calls whichever provider the factory resolves.

`backend/app/services/planning_orchestrator.py` now also defines a
private `_run_ai_candidate_discovery_shadow_stage` helper (Step 161B,
docs/13_llm_reasoning_pipeline.md section 40), giving
`AICandidateDiscoveryService.dry_run` its first, still-optional,
runtime caller. It is gated by
`Settings.ai_candidate_discovery_shadow_mode_enabled`
(`AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED`, default `False`); when
disabled -- the default -- `generate_full_plan` behaves exactly as
before and `ai_candidate_proposal_batch`/`candidate_grounding_batch`
stay `None`. When enabled, it runs after the destination-context stage
and stores only the two already-validated Step 160C batch objects for
inspection -- it never mutates `destination_context` candidates, never
feeds a proposal or `GroundedCandidate` into `ExperiencePlannerService`
or `CandidateQualityService`, never changes `validation_report`
readiness or regeneration state, and never adds an AI-related name to
`provider_coverage`/`data_sources_used`. If `dry_run` raises, the helper
fails safe (nothing stored, generation continues unaffected) instead of
crashing or fabricating a result. This step does not alter itinerary
generation itself and does not touch the frontend.

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

**Optional LangGraph graph wrapper (Step 162B, extended in Step 162C,
`backend/app/graphs/`, docs/13_llm_reasoning_pipeline.md sections 42-43).**
A `PlanningGraphRunner`/`build_planning_graph` LangGraph `StateGraph`
skeleton mirrors `PlanningOrchestrator`'s stage order, calling the same
stage services `PlanningOrchestrator` calls -- no stage logic is
duplicated in a graph node. **`PlanningOrchestrator` remains the active
runtime path and this graph is still not production runtime**: it is not
wired into `generate_full_plan`, not imported by any API route, and does
not change `/trips/{trip_id}/generate` behavior, scheduling, validation,
or regeneration.

As of Step 162C, the graph's `ai_candidate_shadow` node (renamed from Step
162B's always-no-op `ai_candidate_shadow_placeholder`) is real but
**shadow-only**: it calls the existing `AICandidateDiscoveryService.dry_run`
-- the same service `PlanningOrchestrator._run_ai_candidate_discovery_shadow_stage`
already uses -- but only when `Settings.ai_candidate_discovery_shadow_mode_enabled`
(default `False`) is explicitly on and `destination_context` already
exists; disabled or missing context both leave the node a no-op. On
success it stores `ai_candidate_proposal_batch`/`candidate_grounding_batch`
for inspection only, using the exact same batch models the orchestrator's
shadow stage already produces; on any exception it fails safe (a generic,
secret-free marker appended to the graph's `errors`, nothing stored, no
raise). Nothing downstream of this node -- `experience_plan`,
`stay_transport`, `trip_strategy`, `validation` -- has any parameter
through which a proposal or grounded candidate could reach it, and
`candidate_quality` already ran earlier, so it can never consume one
either. No module-level graph singleton is constructed, matching the "not
yet wired" boundary used by every other unwired piece of the
AI-candidate-discovery track.

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

### 28.1 Generation Progress Metadata (Step 163B)

`PlanningState.generation_progress` (`GenerationProgress`,
`backend/app/models/planning_state.py`) is backend pipeline-progress
*metadata* only, recorded by `PlanningOrchestrator.generate_full_plan`
alongside the stage-persistence saves above -- it never changes planning
behavior. It carries no travel fact (no flight route, route/travel time,
booking status, price, rating, or availability) and no scheduling,
validation, or regeneration decision reads or depends on it; it exists
purely so a caller can ask "which pipeline stage is running/has run"
(`GET /trips/{trip_id}/generation-progress`, docs/11_api_contracts.md
section 29, docs/13_llm_reasoning_pipeline.md section 44). The frontend is
not wired to it yet -- the Step 163A decorative loading animation
continues to run off local UI state only (docs/16_frontend_architecture.md
section 29).

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
route_feasibility_report
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

## 35. Route Feasibility for Scheduled Experiences (Step 165E)

`RouteFeasibilityService` (`backend/app/services/route_feasibility_service.py`,
docs/12_provider_architecture.md section 35,
docs/13_llm_reasoning_pipeline.md section 58) runs inside
`PlanningOrchestrator.run_experience_plan_stage`, immediately after
`ExperiencePlannerService.run` and before `PlanValidatorService.run`. It
builds `PlanningState.route_feasibility_report` by calling
`ProviderGateway.get_route` for every consecutive pair of scheduled
experiences within each day -- the same routing call path Step 165B
exposed and Step 165C cache-backs, never a provider adapter directly.

**Default routing remains `not_connected` unless configured.**
`Settings.routing_provider` still defaults to `"not_connected"`, so a
default deployment's `route_feasibility_report` honestly reports every leg
(and the report as a whole) `not_connected`, with no network call --
`/generate` succeeds exactly as it did before this step, just with this
additional honest report attached. `ProviderCoverage.routes` is updated to
match (`"not_connected"` by default; `"success"` only when
`RouteFeasibilityReport.status == success`, never upgraded otherwise).

**Unavailable routing remains `needs_review`.** Whenever a leg's routing
provider call is `not_connected`/`unavailable`/`failed`, or a leg is
`unavailable` because one of its experiences is missing coordinates,
`PlanValidatorService`'s feasibility warning keeps its existing
needs-review framing -- it never claims a route was checked when it
wasn't, and `readiness_status` never reaches `ready` from this alone.

This is feasibility *reporting* only: it never reorders, adds, or drops a
scheduled experience, and `ExperiencePlannerService`'s straight-line/
haversine scheduling (Step 156C) is untouched. Full route-aware scheduling
is Section 166's job, not this step's.

## 36. Route-Aware Day Sequencing, Shadow/Report-Only (Step 166A)

`RouteAwareSequencingService`
(`backend/app/services/route_aware_sequencing_service.py`,
docs/12_provider_architecture.md,
docs/13_llm_reasoning_pipeline.md section 59) runs inside
`PlanningOrchestrator.run_experience_plan_stage`, immediately after
`RouteFeasibilityService.build_report` (section 35 above) and before
`PlanValidatorService.run`. It builds `PlanningState.route_aware_
sequencing_report` (a `RouteAwareSequencingReport`) by calling
`ProviderGateway.get_route` for every pair of coordinate-backed
experiences within each scheduled day that has two or more experiences --
the same routing call path Step 165B exposed and Step 165C cache-backs.

**Not applied to scheduling.** `RouteAwareSequencingReport.is_shadow_only`
stays `True` and `applied_to_itinerary` stays `False`, always. This step
never reorders, adds, or drops a scheduled experience --
`ExperiencePlannerService`'s actual scheduled order
(`ExperiencePlan.daily_plans[*].experiences`) is completely untouched, and
the suggested order is not fed back into `ExperiencePlannerService`,
`PlanValidatorService`, or `ProviderCoverage` by this step.

**Default routing remains `not_connected` unless configured**, exactly as
for `route_feasibility_report` (section 35): with
`Settings.routing_provider="not_connected"`, every day suggestion (and the
report as a whole) honestly reports `not_connected`, with no network call
-- `/generate` succeeds exactly as it did before this step. A day with
fewer than two coordinate-backed experiences is `unavailable` without ever
calling the provider; a day where some (but not all) needed route lookups
succeeded is `partial`, with no duration/distance total reported --
`route_duration_seconds`/`route_distance_meters`/`improvement_seconds` are
only ever populated when every route lookup a day's suggestion depends on
returned a real, successful route.

This is a shadow/report-only step: it does not change
`PlanValidatorService`'s behavior or `ProviderCoverage.routes`, both of
which remain driven solely by `route_feasibility_report`, exactly as after
Step 165E. Full route-aware scheduling that actually changes itinerary
order remains a later Section 166 step, not this one.

## 37. Config-Gated Route-Aware Scheduling Application (Step 166B)

`RouteAwareSequencingService.apply_report` (Step 166B,
docs/12_provider_architecture.md,
docs/13_llm_reasoning_pipeline.md section 60) is called by
`PlanningOrchestrator.run_experience_plan_stage`, right after building
`route_aware_sequencing_report` (section 36 above), and **only when
`Settings.route_aware_scheduling_enabled` is `True`**. This setting
defaults to `False`, so this application path is completely inert by
default -- `PlanningOrchestrator` never calls `apply_report`, and the
scheduled itinerary order stays exactly as `ExperiencePlannerService` left
it.

When enabled, `apply_report` reorders a day's real scheduled experiences
only when its suggestion is `success`, its real improvement exceeds
`Settings.route_aware_scheduling_min_improvement_seconds` (default
`0.0`), the day's current experience IDs still match the suggestion's
`original_order`, and `suggested_order` is a verified exact permutation
of those IDs. It never adds, removes, or duplicates an experience, and it
never changes any experience field other than order. When at least one
day is actually reordered, `route_aware_sequencing_report.is_shadow_only`
flips to `False` and `applied_to_itinerary` flips to `True`; otherwise
both stay exactly as Step 166A set them.

If any day was reordered, `PlanningOrchestrator` immediately recomputes
`route_feasibility_report` (and `ProviderCoverage.routes`) against the
new order, so `PlanValidatorService` never describes a stale schedule.
`PlanValidatorService` itself, `provider_coverage` for every other field,
and regeneration-refusal behavior are otherwise unaffected by this step.

## 38. Provider-Backed Travel-Time Buffer Reporting (Step 166C)

`TravelTimeBufferService` (`backend/app/services/travel_time_buffer_service.py`,
docs/12_provider_architecture.md,
docs/13_llm_reasoning_pipeline.md section 61) runs inside
`PlanningOrchestrator.run_experience_plan_stage`, immediately after
`route_feasibility_report` is built and after any Step 166B config-gated
route-aware-scheduling application (section 37 above) -- so
`PlanningState.travel_time_buffer_report` always reflects this run's
*final* scheduled order, whether or not a reorder happened. It builds a
`TravelTimeBuffer` for every consecutive pair of scheduled experiences
within each day by calling `ProviderGateway.get_route` (the same Step
165B/165C call path RouteFeasibilityService/RouteAwareSequencingService
already use).

Each buffer's `recommended_buffer_seconds` is never anything but an exact
restatement of a real, successful `RouteResult.duration_seconds` -- no
invented padding, no straight-line (haversine) substitute. A leg missing
a coordinate is `not_computable` without calling the provider; a leg
whose provider call is `not_connected`/`unavailable`/`failed` mirrors
that status with no duration/distance/buffer populated. Since this app's
scheduling does not currently populate `ExperienceItem.start_time`/
`end_time`, `available_gap_seconds` stays `None` and
`buffer_status=not_computable` for essentially every plan generated
today, even when a real duration exists -- sufficiency is never guessed.

`PlanValidatorService` consumes this report to add one non-blocking
`WARNING` per leg whose `buffer_status == insufficient` (a real duration
exceeding a real known gap); a `sufficient`, `not_computable`, or
`unavailable` leg never produces this warning. This never changes
`provider_coverage`, regeneration-refusal behavior, or Step 166B's config
defaults.

## 39. Hardened Unavailable-Routing Fallback Behavior (Step 166D)

Step 166D hardens fallback behavior across Sections 165E/166A-166C
rather than adding a new report or endpoint. Route fallback statuses are
documented here as the single shared vocabulary every route-dependent
report already uses:

| Status | Meaning | Appears in |
|---|---|---|
| `success` | A real, provider-backed duration/distance exists for this leg/day. | all three reports |
| `partial` | Some legs/days succeeded, others didn't. | all three reports (report-level aggregate) |
| `unavailable` | The routing provider responded but returned no usable route (or, for `route_feasibility_report`/`route_aware_sequencing_report`, coordinates are missing). | all three reports |
| `not_connected` | No routing provider is configured at all. | all three reports |
| `not_computable` | A required input (coordinates, or a schedule gap) is missing, so no duration/sufficiency judgement is even attempted. | `travel_time_buffer_report` (per leg `status`), and `buffer_status` specifically for "duration known, no gap known" |
| `failed` | The provider request failed, or an unexpected exception was safely contained. | all three reports |

**Generation remains safe when routing is unavailable.** Each of
`RouteFeasibilityService`, `RouteAwareSequencingService`, and
`TravelTimeBufferService` wraps its own `ProviderGateway.get_route` call
in a self-contained `_safe_get_route` helper: a genuinely unexpected
exception (as opposed to an honest non-`success` `RouteResult`, which
every real adapter already returns on its own) is converted into a
`status=failed` result with a generic message, never raw exception text
or a provider payload. `RouteAwareSequencingService.apply_report`
additionally contains an unexpected error applying one day's suggestion
without aborting other, still-safe days, and never leaves a day
partially reordered.

As a second line of defense, `PlanningOrchestrator.
run_experience_plan_stage` wraps every `build_report`/`apply_report`
call for these three services in its own try/except. If one still
raises unexpectedly, the orchestrator stores an honest, empty
`status=failed` report (`_failed_route_feasibility_report`/
`_failed_route_aware_sequencing_report`/
`_failed_travel_time_buffer_report`) and generation continues to
`PlanValidatorService` and beyond -- `/generate` never fails just
because route-dependent reporting did.

`PlanValidatorService` was also hardened: the travel-time-buffer warning
path is now deduplicated by `(from_experience_id, to_experience_id)`, so
a report can never produce more than one warning about the same leg.
Nothing about `RouteAwareSequencingService.apply_report`'s existing
safety contract changed -- it already refused every non-`success`
suggestion, and this step adds regression tests, not new logic, to lock
that in.

## 40. Movement-Data Provenance and Final Section 166 Integration (Step 166E)

Step 166E is the final Section 166 step. It adds a shared
`MovementDataProvenance` field (`provider_backed`/`not_connected`/
`unavailable`/`not_computable`/`failed`/`not_applied`,
`backend/app/models/routing.py`) to every route-dependent model, and
locks in the complete route-aware planning flow with integration tests
-- no new report, no new endpoint, no behavior change beyond that
labeling.

**Final Section 166 route-aware planning flow**, all inside
`PlanningOrchestrator.run_experience_plan_stage`, in this fixed order:

1. `RouteFeasibilityService.build_report` -- route feasibility for
   consecutive scheduled pairs (Step 165E).
2. `RouteAwareSequencingService.build_report` -- shadow-only day
   sequencing suggestions (Step 166A).
3. `RouteAwareSequencingService.apply_report` -- **only** when
   `Settings.route_aware_scheduling_enabled` is `True` (default `False`)
   -- config-gated application onto the real schedule (Step 166B). If
   this reorders at least one day, `route_feasibility_report` is
   recomputed against the new order immediately.
4. `TravelTimeBufferService.build_report` -- travel-time buffers using
   this run's *final* scheduled order, whether or not step 3 reordered
   anything (Step 166C).
5. `PlanValidatorService.run` -- consumes `route_feasibility_report`/
   `travel_time_buffer_report` (Step 165E/166C consumption, Step 166D
   dedup hardening).

**Safe failure behavior at every one of steps 1-4**: each service
contains an unexpected routing-provider exception itself
(`_safe_get_route`, Step 166D), and `PlanningOrchestrator` wraps every
one of these four calls in its own try/except as a second line of
defense, storing an honest empty `status=failed` report
(`movement_data_provenance=failed`) if a call still raises unexpectedly.
`/generate` never fails, and no raw exception text or provider payload
is ever stored in a user-facing field.

**Movement-data provenance sits alongside every existing status field**,
never replacing it: `RouteLegFeasibility.status`,
`RouteAwareSequenceSuggestion.status`, and `TravelTimeBuffer.status`/
`buffer_status` are all unchanged in meaning and values. The new
`movement_data_provenance` field (and its report-level counterpart on
all three parent reports) is purely additive, computed via the shared
`movement_data_provenance_from_status` helper (and, for sequencing
suggestions specifically, `route_aware_suggestion_provenance`, which
adds the `not_applied` distinction). `RouteAwareSequenceSuggestion.
movement_data_provenance` starts as `not_applied` for every `success`
suggestion and only ever flips to `provider_backed` inside
`apply_report`, in lockstep with `applied`/`message` -- so it can never
imply a reorder happened when the config gate was off or the suggestion
was otherwise rejected.

`ProviderCoverage.routes` (already wired since Step 165E) continues to
report honestly off `route_feasibility_report.status` alone -- `success`
only when that status is genuinely `success`, confirmed by a dedicated
regression test.

---

## 41. Accommodation Provider Contract (Step 167A)

`backend/app/models/accommodation.py` (`AccommodationSearchRequest`,
`AccommodationOffer`, `AccommodationSearchResult`) and
`backend/app/providers/accommodation/base.py`
(`AccommodationInventoryProvider`, an `abc.ABC`) add a normalized
contract for future real accommodation inventory providers (docs/12_
provider_architecture.md section 41, docs/13_llm_reasoning_pipeline.md
section 64) -- the accommodation-domain counterpart to the OSRM routing
contract (Step 165A, section 35 above).

**This is a contract only, exists but is not wired into planning yet.**
`ProviderGateway.accommodation` still defaults to the pre-existing,
always-`not_connected` `AccommodationProvider()` from `app.providers.base`
(section 18 above); no stage service, `PlanningOrchestrator`,
`ProviderCoverage`, or `provider_status` references this new contract.
No concrete adapter (not-connected default, OSM-backed, or a real
Booking/Expedia/Hotelbeds/Hostelworld/Amadeus/Vrbo/Airbnb integration)
exists yet either. `AccommodationOffer` is deliberately kept distinct
from the existing OSM-backed `DestinationContext.
candidate_accommodation_pois`/`AccommodationSuggestion`/`StayAreaGuidance`
data already flowing through the pipeline -- those remain open-data
location candidates only, never bookable inventory.

## 42. Accommodation Not-Connected Provider and Factory (Step 167B)

`backend/app/providers/accommodation/not_connected_adapter.py`
(`NotConnectedAccommodationProvider`) and `factory.py`
(`get_accommodation_provider`) add the first concrete
`AccommodationInventoryProvider` and a config-gated factory to select
it (docs/12_provider_architecture.md section 42, docs/13_llm_reasoning_
pipeline.md section 65) -- mirroring `NotConnectedRoutingProvider`/
`get_routing_provider` (Step 165A/165B, section 35 above). A new
`Settings.accommodation_provider` field (alias `ACCOMMODATION_PROVIDER`,
default `"not_connected"`) gates the factory's selection.

**Still not wired into planning.** `ProviderGateway.accommodation`
remains the pre-existing, always-`not_connected` `AccommodationProvider()`
from `app.providers.base`; neither `ProviderGateway` nor
`PlanningOrchestrator` imports `app.providers.accommodation` or
references `get_accommodation_provider`/`AccommodationInventoryProvider`.
`NotConnectedAccommodationProvider.search_accommodations` always
returns `status=not_connected` with an empty `offers` list, never
calling a network service or inventing a property, price, availability,
rating, amenity, cancellation policy, or booking link. The factory
falls back to this same provider for any unrecognized
`accommodation_provider` config value.

## 43. Accommodation Lookup Exposed Through ProviderGateway (Step 167C)

`ProviderGateway` (`backend/app/providers/gateway.py`) gains an
`accommodation_inventory` constructor slot (defaulting to
`get_accommodation_provider()`) and a `search_accommodations(request:
AccommodationSearchRequest) -> AccommodationSearchResult` method
(docs/12_provider_architecture.md section 43, docs/13_llm_reasoning_
pipeline.md section 66) -- the accommodation-domain counterpart to
`routing`/`get_route` (Step 165B, section 35 above). Existing
`ProviderGateway()` construction with no arguments is unaffected; the
pre-existing `accommodation` slot (`app.providers.base.
AccommodationProvider` stub) is untouched and stays separate.

**Not yet consumed by planning or validation.** No stage service
(`PlanningOrchestrator`, `StayTransportService`, `PlanValidatorService`)
calls `search_accommodations` or references `accommodation_inventory`
-- itinerary scheduling, plan validation, and `ProviderCoverage`
reporting are all unchanged by this step. The default remains
`not_connected` with an empty `offers` list and no network call.

## 44. Accommodation Inventory Report, Coverage, and Validation Clarity (Step 167D)

`backend/app/services/accommodation_inventory_service.py`
(`AccommodationInventoryService`) is a new stage-adjacent service, run
by `PlanningOrchestrator.run_stay_transport_stage` right after
`StayTransportService.run` (docs/12_provider_architecture.md section
44, docs/13_llm_reasoning_pipeline.md section 67). It builds an
`AccommodationSearchRequest` from `PlanningState.trip_request` and
calls `ProviderGateway.search_accommodations` (Step 167C), storing the
result on the new `PlanningState.accommodation_inventory_report`
field. Exception-hardened like the Section 166 route reports: an
unexpected exception is caught and converted to a `failed` result
rather than crashing `generate_full_plan`.

The result's status is mapped onto `ProviderCoverage.hotel_prices`
(not `accommodations`, which already carries the OSM-backed
accommodation-location-candidate coverage value) -- `success` is set
only when the result actually carries a real offer.
`PlanValidatorService` adds one non-blocking
`category="accommodation_inventory"` `WARNING` explaining the current
status in plain terms, never a critical issue.

**Still not consumed by scheduling.** No lodging is scheduled into the
itinerary, no hotel recommendation logic is added, and `StayTransportDecision.
accommodation_recommendations` remains empty exactly as before this
step -- this only makes the inventory status honest and visible.

## 45. Final Accommodation Flow (Section 167, Steps 167A-167E)

The complete, current accommodation inventory data flow, end to end:

```text
ProviderGateway.search_accommodations (Step 167C)
  -> AccommodationInventoryService.build_report (Step 167D)
  -> PlanningState.accommodation_inventory_report (Step 167D)
  -> ProviderCoverage.hotel_prices (Step 167D, mapped in PlanningOrchestrator)
  -> PlanValidatorService's non-blocking "accommodation_inventory" warning (Step 167D)
  -> AccommodationInventorySection in frontend/app/page.tsx (Step 167E)
```

Every arrow above is a plain read of already-computed data -- no stage
re-calls the provider, no stage re-derives a status from scratch, and no
step upgrades a `not_connected`/`unavailable`/`failed` result into
anything stronger. With the default `Settings.accommodation_provider =
"not_connected"` (Step 167B), every link in this chain resolves to an
honest "not connected"/empty-offers state, all the way to the rendered
UI. `ProviderGateway.accommodation` (the pre-existing, generic
`app.providers.base.AccommodationProvider` stub used by
`StayTransportService`) and `DestinationContext.
candidate_accommodation_pois`/`AccommodationSuggestion`/`StayAreaGuidance`
(OSM-backed open-data location candidates, feeding `ProviderCoverage.
accommodations`) remain two entirely separate flows, never merged with
the chain above at any point.

## 46. Scraping Policy, Registry, and Provenance Foundation (Step 168A)

`backend/app/models/scraping.py` adds the first Section 168 contract:
`ScrapingSourcePolicy` (one approved-or-not-yet-approved scraping
source), `ScrapedDataProvenance` (provenance metadata a future scraped
item would carry), and `ScrapingSourceRegistry` (an in-memory holder
whose `active_sources()` returns only explicitly-enabled, safe sources;
its default instance starts empty) -- docs/12_provider_architecture.md
section 46, docs/13_llm_reasoning_pipeline.md section 69. Three new
config fields gate it: `Settings.scraping_enabled`, `Settings.
scraped_accommodation_provider_enabled` (both default `False`), and
`Settings.scraping_default_rate_limit_seconds` (default `10`, unread by
any code path yet).

**Not wired into planning yet.** `ProviderGateway` and
`PlanningOrchestrator` do not import or reference anything in
`backend/app/models/scraping.py` -- confirmed by dedicated
source-inspection tests. No live scraper is implemented, no external
HTML is parsed, and no website is called.

## 47. Static Scraped Accommodation Parser Framework (Step 168B)

`backend/app/providers/accommodation/scraped_parser.py`
(`parse_scraped_accommodation_html`) adds a pure parsing function: given
an already-provided HTML string plus a `ScrapingSourcePolicy` (section
46), an `AccommodationSearchRequest`, and provenance metadata
(`source_url`/`parser_version`), it returns an `AccommodationSearchResult`
whose offers each carry a `scraped_provenance` (docs/12_provider_
architecture.md section 47, docs/13_llm_reasoning_pipeline.md section
70). Uses only the Python standard library `html.parser.HTMLParser` --
no new dependency.

`AccommodationOffer` gained one new optional field,
`scraped_provenance: ScrapedDataProvenance | None`, and a validator
tying it to `data_status == DataStatus.SCRAPED_PUBLIC_PAGE` (a new
`DataStatus` member, `backend/app/models/common.py`) -- a scraped offer
can never carry an official-looking `data_status`.

**Still not wired into planning.** `ProviderGateway` and
`PlanningOrchestrator` do not import or reference `scraped_parser` --
confirmed by dedicated source-inspection tests. No live scraper adapter
exists, and this function is never called outside its own tests.

## 48. Config-Gated Scraped Accommodation Provider (Step 168C)

`backend/app/providers/accommodation/scraped_adapter.py`
(`ScrapedAccommodationProvider`) is the first concrete adapter to call
the Step 168B parser (docs/12_provider_architecture.md section 48,
docs/13_llm_reasoning_pipeline.md section 71), selectable via
`get_accommodation_provider("scraped_local")`
(`backend/app/providers/accommodation/factory.py`). Four new config
fields support it: `Settings.scraped_accommodation_html_path` (default
`None` at the time this step was written; changed to a fixed local path
by Step 168F, section 51), `scraped_accommodation_source_id`,
`scraped_accommodation_source_name`, and
`scraped_accommodation_base_url`.

**Originally not enabled by default; Step 168F later made it the
default (section 51) -- but it still never fetches a live website.**
Three independent gates (`scraping_enabled`, `scraped_accommodation_
provider_enabled`, and selecting `"scraped_local"`) must all line up,
and even then the provider only reads a local file path the
developer/operator configured themselves -- `not_connected` when any
gate is off, `unavailable` when the path is unset or the file doesn't
exist, `failed` (never a raw exception) on an unexpected read/parse
error. `ProviderGateway`'s default construction and
`PlanningOrchestrator` are untouched.

## 49. Scraped Accommodation Cache and Rate-Limit Guard (Step 168D)

`ScrapedAccommodationProvider` now caches a successful parse in
`ProviderCacheStore` (source `"scraped_accommodation"`), gated by new
`Settings.scraped_accommodation_cache_enabled` (default `True`) and
`scraped_accommodation_cache_ttl_seconds` (default `3600`) --
docs/12_provider_architecture.md section 49, docs/13_llm_reasoning_
pipeline.md section 72. The cache key covers the request fields, parser
version, and the local file's own path/mtime/size, so an edited file is
never served stale data. Only the normalized result is cached, never
raw HTML; cache reads/writes never fail the provider.

`backend/app/models/scraping.py` also gains `ScrapingRateLimitGuard`, a
standalone, injectable-clock timing utility for a *future* live scraper
adapter -- nothing calls it yet.

At the time this step was written the provider was still disabled by
default (Step 168F, section 51, later changed that); **live website
fetching is still not implemented anywhere in this codebase** either way
-- this step only changes how the local/manual path caches its own
output.

## 50. Final Scraped Accommodation Flow (Section 168, Steps 168A-168F)

The complete, current scraped-accommodation data flow, end to end:

```text
SCRAPED_ACCOMMODATION_HTML_PATH (local file, manually supplied)
  -> ScrapedAccommodationProvider.search_accommodations (Step 168C)
  -> parse_scraped_accommodation_html (Step 168B, stdlib HTML parser only)
  -> normalized AccommodationSearchResult / AccommodationOffer
     (scraped_provenance, data_status=scraped_public_page)
  -> ProviderCacheStore cache (Step 168D, normalized result only)
  -> PlanningState.accommodation_inventory_report (Step 167D, via
     AccommodationInventoryService / ProviderGateway.search_accommodations)
  -> ProviderCoverage.hotel_prices (Step 167D; success only with real offers)
  -> PlanValidatorService's non-blocking "accommodation_inventory" warning
     (Step 167D, scraped-aware wording added in Step 168E)
  -> GET /trips/{trip_id} (full PlanningState, including scraped_provenance)
  -> AccommodationInventorySection / ScrapedProvenanceBadge in
     frontend/app/page.tsx (Step 168E)
```

Every link is a plain read of already-computed data. **As of Step 168F
(section 51), all three gates (`scraping_enabled`,
`scraped_accommodation_provider_enabled`,
`accommodation_provider="scraped_local"`) default to selecting this
chain** -- with no local HTML file present at the default path, it
reports `unavailable` with empty offers rather than `not_connected`
(the app-level gates are satisfied; only the file is missing), and
never a fabricated offer either way. Explicitly setting
`accommodation_provider="not_connected"` (or either flag to `false`)
still opts back out to the always-`not_connected`
`NotConnectedAccommodationProvider`. **No live website fetching exists
anywhere in this codebase** -- the only fetch this chain ever performs
is a local filesystem read of a path the developer/operator configured
(or left at its default).

## 51. Scraped Accommodation Made the Default Provider (Step 168F)

`Settings.accommodation_provider` defaults to `"scraped_local"`
(previously `"not_connected"`), and `Settings.scraping_enabled`/
`scraped_accommodation_provider_enabled` both default to `True`
(previously `False`) -- docs/12_provider_architecture.md section 51.
`Settings.scraped_accommodation_html_path` now defaults to
`".data/manual_scrapes/accommodations.html"`, resolved against the
backend project root by a new `Settings.resolved_scraped_
accommodation_html_path()` method (mirroring `resolved_local_storage_
path`/`resolved_provider_cache_path`).

With no file at that default path -- the state of a fresh checkout --
`ProviderGateway.accommodation_inventory` (a `ScrapedAccommodationProvider`
by default now) reports `status=unavailable`/`offers=[]`, and
`ProviderCoverage.hotel_prices` reports `"unavailable"` accordingly;
generation still succeeds. Once an operator places a real file there,
behavior is identical to the proven Step 168C/168E end-to-end flow
(section 50). Explicitly setting `accommodation_provider="not_connected"`
(or either scraping flag to `false`) still selects/produces the
always-`not_connected` `NotConnectedAccommodationProvider` exactly as
before this step.

## 52. Flight Provider Model/Interface Foundation (Step 169A)

`backend/app/models/flight.py` (`FlightSearchRequest`/`FlightSegment`/
`FlightOffer`/`FlightSearchResult`) and
`backend/app/providers/flights/base.py` (`FlightInventoryProvider`, an
`abc.ABC` with one abstract method, `search_flights`) add the flight
equivalent of Section 167's accommodation contract foundation (167A) --
docs/12_provider_architecture.md section 52.

**Not wired into `ProviderGateway` or planning yet.** `ProviderGateway`'s
constructor, its pre-existing `flight` slot (`app.providers.base.
FlightProvider`, still the always-`not_connected` stub), and its
`default_provider_coverage`/`to_status_entry` methods are all untouched.
No factory (no `backend/app/providers/flights/factory.py`) and no
`Settings.flight_provider`-style config field exist yet -- unlike
accommodation's `Settings.accommodation_provider`, there is nothing to
select between here yet, only the one interface. `TripStrategyService`,
`StayTransportService`, `PlanValidatorService`, and
`PlanningOrchestrator` do not reference any of the new flight models or
`FlightInventoryProvider`. No API route, repository, or frontend code
constructs a `FlightSearchRequest` or reads a `FlightSearchResult`.

Every optional fact field on `FlightSegment`/`FlightOffer` stays `None`
unless a real future adapter sets it, and `FlightOffer.scraped_provenance`
reuses the same `ScrapedDataProvenance` model and
`data_status=scraped_public_page` consistency rule as
`AccommodationOffer` (section 47/68) -- so a future scraped flight
adapter, when it exists, inherits the same honesty contract without
needing new plumbing.

## 53. Flight Provider Factory and Default Scraped-Local Stub (Step 169B)

`backend/app/core/config.py` gains `Settings.flight_provider` (default
`"scraped_local"`, matching Section 168F's accommodation default),
`scraped_flight_provider_enabled` (default `True`),
`scraped_flight_html_path` (default
`.data/manual_scrapes/flights.html`), `scraped_flight_source_id`/
`scraped_flight_source_name`/`scraped_flight_base_url`, and
`resolved_scraped_flight_html_path()` (mirroring `resolved_scraped_
accommodation_html_path()`). `backend/app/providers/flights/` gains
`not_connected_adapter.py` (`NotConnectedFlightProvider`),
`scraped_adapter.py` (`ScrapedLocalFlightProvider`), and `factory.py`
(`get_flight_provider`) -- docs/12_provider_architecture.md section 53.

**`ScrapedLocalFlightProvider` is a default-selected stub, not a working
scraper.** With no flight HTML parser implemented yet (that's Step
169C), it reports `unavailable` in every case that would otherwise reach
parsing -- including when a local file actually exists at the
configured path -- and reports `not_connected` only when
`scraping_enabled`/`scraped_flight_provider_enabled` is off. It never
reads a file's content and never calls a network service.

**Not wired into `ProviderGateway` or planning yet.** `ProviderGateway`'s
constructor, its pre-existing `flight` slot (`app.providers.base.
FlightProvider`), and `PlanningOrchestrator` are all untouched --
confirmed by dedicated tests
(`backend/app/tests/providers/test_flight_factory.py`) asserting neither
module's source references `get_flight_provider`, `FlightInventoryProvider`,
or `app.providers.flights`. No API route, repository, or frontend code
calls `get_flight_provider` either.

## 54. Static Scraped Flight Parser Framework (Step 169C)

`backend/app/providers/flights/scraped_parser.py` adds
`parse_scraped_flight_html`, the flight equivalent of the Step 168B
accommodation parser (`parse_scraped_accommodation_html`) --
docs/12_provider_architecture.md section 54. Uses only the stdlib
`html.parser.HTMLParser`; transforms an already-provided HTML string into
a normalized `FlightSearchResult`, never fetching anything itself.
`ScrapingSourcePolicy` (`backend/app/models/scraping.py`) gains
`allows_flights: bool = False`, mirroring `allows_lodging` -- the parser
refuses to run unless a source is safe, enabled, and `allows_flights`.

**Not wired into `ProviderGateway` or planning yet -- and not yet wired
into `ScrapedLocalFlightProvider` either.** `ScrapedLocalFlightProvider`
(Step 169B) still only checks whether a local file exists at
`Settings.scraped_flight_html_path`; it does not call
`parse_scraped_flight_html`, so it still reports `unavailable` (never
`success`) even when a real file is present. Wiring the provider to
actually call this parser (plus an accommodation-style cache) is Step
169D. Confirmed by dedicated tests that neither `scraped_adapter.py`,
`ProviderGateway`, nor `PlanningOrchestrator` references `scraped_parser`
or `parse_scraped_flight_html`.

## 55. ScrapedLocalFlightProvider Reads, Parses, and Caches Local Flight HTML (Step 169D)

`ScrapedLocalFlightProvider.search_flights`
(`backend/app/providers/flights/scraped_adapter.py`) now actually reads
the configured local file, calls `parse_scraped_flight_html` (section
54), and caches a successful normalized `FlightSearchResult` in the same
`ProviderCacheStore` real adapters already use (source
`"scraped_flight"`, new `Settings.scraped_flight_cache_enabled`/
`scraped_flight_cache_ttl_seconds`, default `True`/`3600`) --
docs/12_provider_architecture.md section 55, mirroring
`ScrapedAccommodationProvider` (section 48-49). The three pre-existing
config gates from Step 169B (`scraping_enabled`,
`scraped_flight_provider_enabled`, and a resolvable, existing local
file) are unchanged; only what happens once all three pass changed.

**No live website fetching exists anywhere in this path.** The provider
only ever calls `Path.read_text` on a file the developer/operator
supplied themselves; `parse_scraped_flight_html` itself fetches nothing.
Cache reads/writes are both best-effort SQLite operations against a
local file (`Settings.resolved_provider_cache_path()`) -- never a
network call, and a failure in either never crashes the provider or
fabricates a result.

**Not wired into `ProviderGateway` or planning yet.** The gateway's
pre-existing `flight` slot (`app.providers.base.FlightProvider`) and
`PlanningOrchestrator` remain untouched, confirmed by dedicated tests in
`backend/app/tests/providers/test_scraped_flight_cache.py`.

## 56. Flight Inventory Flow: Gateway to Frontend (Step 169E, final Section 169 step)

Step 169E completes the flight inventory flow
(docs/12_provider_architecture.md section 56):

```text
ProviderGateway.search_flights (new `flight_inventory` slot, default
  scraped_local)
  -> FlightInventoryService.build_report (backend/app/services/
     flight_inventory_service.py; maps TripRequest fields -- origin_
     city/primary_destination/start_date/end_date/travelers_count/
     budget_currency -- into a FlightSearchRequest; fails safe on any
     unexpected exception)
  -> PlanningState.flight_inventory_report (set in
     PlanningOrchestrator.run_stay_transport_stage, alongside
     accommodation_inventory_report)
  -> ProviderCoverage.flights (mapped from FlightSearchResult.status,
     mirroring the hotel_prices mapping -- a success with zero offers
     reports "unavailable")
  -> PlanValidatorService's non-blocking "flight_inventory" warning
     (mirrors the accommodation_inventory warning; scraped-aware
     wording for any scraped_provenance-carrying offer)
  -> GET /trips/{trip_id} (full PlanningState, including
     flight_inventory_report -- no route/schema change needed, since
     TripResponseData already serializes the whole PlanningState model)
  -> FlightInventorySection / ScrapedFlightProvenanceBadge in
     frontend/app/page.tsx
```

**Default flight provider remains `scraped_local`** (unchanged from
Step 169B) -- this step only adds consumers of its output, never
changes provider selection. **Missing local HTML still returns
`unavailable`** with an empty `offers` list at every layer above,
exactly as `ScrapedLocalFlightProvider` (Step 169D) itself already
reports. **No live website fetching exists anywhere in this flow** --
every link above is a plain read of already-computed data (or, at the
provider layer, a local file read); the only network calls a full
`/generate` run makes are the pre-existing, unrelated weather/holiday/
currency/OSM calls. Flights are never scheduled into the itinerary as a
daily experience anywhere in this chain.

## 57. Read-Only AI Candidate Review Report (Step 170A)

**Step 170A adds `AICandidateReviewService`**
(`backend/app/services/ai_candidate_review_service.py`) and
`GET /trips/{trip_id}/ai-candidate-review`
(`backend/app/api/routes/trips.py`), plus their supporting models
(`backend/app/models/ai_candidate_review.py`: `AICandidateReviewItem`,
`AICandidateReviewReport`) and response schema
(`backend/app/schemas/ai_candidate_review.py`:
`AICandidateReviewResponseData`), docs/13_llm_reasoning_pipeline.md section
80.

- **Purpose**: make the existing Step 157A-161B AI candidate discovery/
  grounding state (`PlanningState.ai_candidate_proposal_batch`/
  `candidate_grounding_batch`) visible and reviewable through a normal
  read-only endpoint, without changing what that state means or does.
- **`AICandidateReviewService.build_report`** only reads
  `planning_state.ai_candidate_proposal_batch`/`candidate_grounding_batch`
  -- it takes no provider/gateway/LLM dependency at all, never calls
  `AICandidateDiscoveryService` or any `AICandidateProposalProvider`, and
  never mutates `planning_state`. If `ai_candidate_proposal_batch` is
  `None` (the default -- shadow mode is off by default) or its result has
  no proposals, it returns an honest empty report
  (`status="no_candidate_data"`, or the underlying result's own status
  string, e.g. `"not_connected"`) rather than computing anything further.
- **Route**: `get_ai_candidate_review` follows the existing
  `get_candidate_quality`/`get_regeneration_readiness` pattern exactly --
  `trip_not_found_error` for an unknown `trip_id`, otherwise
  `success_response(AICandidateReviewResponseData(...))`. It calls
  `planning_state_repository.get_by_trip_id` only (never `.save`), so a
  planning state is never written to as a side effect of this GET.
- **Promotion stays disabled**: `AICandidateReviewItem.eligible_for_promotion`
  and `AICandidateReviewReport.eligible_for_promotion` are hardcoded
  `False`/`0`, enforced by a pydantic `field_validator` on each model (not
  just a service-level default), so no future code path can silently start
  reporting a candidate as promotable without a deliberate model change.
  `rejection_reasons` is built from a small fixed set of honest strings
  (e.g. "AI candidate promotion is not enabled yet.", "Candidate is not
  provider-grounded.") plus, when a candidate was actually rejected by
  `CandidateGroundingService`, that rejection's own `message` -- never an
  invented justification.
- **No PlanningState field was added.** Unlike `candidate_quality_report`
  (computed once per destination-context stage and stored), the AI
  candidate review report is deliberately *not* persisted -- it is
  recomputed from the existing stored batches on every call, exactly like
  `get_trip_summary`'s `scheduled_experiences_count` is recomputed live
  rather than stored. `PlanningOrchestrator` is completely untouched by
  this step.
- **Confirmed unchanged**: `ExperiencePlannerService` scheduling,
  route-aware scheduling (Section 35-40), regeneration refusal, and every
  existing candidate-quality/grounding/discovery test all pass unmodified
  -- this step only adds a new read path over already-existing data.

## 58. Deterministic AI Candidate Promotion Eligibility Rules (Step 170B)

**Step 170B adds `AICandidatePromotionEligibilityService`**
(`backend/app/services/ai_candidate_promotion_eligibility_service.py`),
consumed by `AICandidateReviewService.build_report` (Section 57), plus
model/route changes so `eligible_for_promotion` can now be `True`,
docs/13_llm_reasoning_pipeline.md section 81.

- **`evaluate_promotion_eligibility(proposal, grounded, rejected,
  candidate_quality_report) -> PromotionEligibilityResult`** is a pure
  function: no constructor dependency, no provider/gateway/LLM import, no
  mutation of any argument. It never calls `CandidateQualityService` or
  `CandidateGroundingService` -- it only *reads* their prior output
  (`GroundedCandidate`/`RejectedCandidateProposal`/`CandidateQualityReport`)
  already sitting on `PlanningState`.
- **`find_quality_score`** joins a `GroundedCandidate` to its real
  provider place's already-computed `CandidateQualityScore` by comparing
  `evidence.provider_place_id` against `CandidateQualityScore.candidate_id`
  first (both derive from the same underlying candidate's `place_id` when
  one exists), falling back to a normalized-name match. This is a lookup
  only -- it never triggers new scoring, and `CandidateQualityService`
  remains completely unaware of AI candidates (Section 40's non-consumption
  guarantee, and its dedicated test, are untouched).
- **The 8 rules** (docs/13_llm_reasoning_pipeline.md section 81 has the
  full list) each contribute either a `passed_reasons` or `failed_reasons`
  entry to the result; `eligible` is `True` only when `failed_reasons` is
  empty. Accepted quality tiers
  (`primary_anchor`/`good_candidate`/`secondary_candidate`) are the exact
  same set as `ExperiencePlannerService._ELIGIBLE_SCHEDULING_TIERS` (Section
  15) -- an AI candidate is never held to a different bar than a real
  provider candidate. Accommodation/flight/transport exclusion (rules 7-8)
  is checked only against `GroundedCandidate.evidence.matched_category`
  (the real provider category string), never against the AI proposal's own
  free-text wording.
- **`AICandidateReviewService.build_report`** now calls
  `evaluate_promotion_eligibility` once per candidate and maps its result
  onto `AICandidateReviewItem.quality_bucket`/`eligibility_reasons`/
  `eligible_for_promotion`/`rejection_reasons` -- `rejection_reasons` is
  now exactly the eligibility result's `failed_reasons` (the Step 170A
  fixed "AI candidate promotion is not enabled yet." placeholder text is
  gone, since promotion eligibility is now genuinely computed). The
  service still takes no provider/LLM dependency and still never mutates
  `PlanningState`.
- **Model changes** (`backend/app/models/ai_candidate_review.py`):
  `AICandidateReviewItem` gained `eligibility_reasons: list[str]` and
  replaced its old "always False" field validator with a
  `model_validator(mode="after")` that raises unless
  `eligible_for_promotion` implies both `provider_grounded=True` and
  empty `rejection_reasons` -- a structural safety net independent of the
  eligibility service's own correctness. `AICandidateReviewReport`
  replaced its old "always 0" validator with one that requires
  `eligible_for_promotion` (the count) to exactly equal the number of
  `items` actually marked eligible, so a report can never silently drift
  from what it's summarizing.
- **No new `PlanningState` field, no orchestrator change.** Exactly like
  Section 57, the eligibility verdict is recomputed fresh on every
  `GET /trips/{trip_id}/ai-candidate-review` call, never persisted, and
  `PlanningOrchestrator`/`ExperiencePlannerService` remain completely
  untouched -- confirmed by a dedicated test asserting
  `experience_planner_service`'s source never references
  `AICandidateReviewItem`, `ai_candidate_review_service`,
  `ai_candidate_promotion_eligibility_service`, or `eligible_for_promotion`.
- **Still not promotion**: nothing in this step adds a candidate to any
  `DailyPlan`/`experiences` list. Step 170C is the step that would act on
  `eligible_for_promotion`; this step only computes it honestly.

## 59. AI Candidate Promotion Report (Step 170C)

**Step 170C adds `AICandidatePromotionService`**
(`backend/app/services/ai_candidate_promotion_service.py`) and
`POST /trips/{trip_id}/ai-candidate-promotions`
(`backend/app/api/routes/trips.py`), plus supporting models
(`backend/app/models/ai_candidate_promotion.py`: `PromotedAICandidate`,
`AICandidatePromotionReport`) and response schema
(`backend/app/schemas/ai_candidate_promotion.py`:
`AICandidatePromotionResponseData`), docs/13_llm_reasoning_pipeline.md
section 82.

- **`AICandidatePromotionService.build_promotion_report`** is pure/
  read-only: it calls `AICandidateReviewService.build_report` (Section 57)
  to get the already-computed eligibility verdict, then partitions
  `review_report.items` by `eligible_for_promotion`. For each eligible
  item it looks up the matching real `GroundedCandidate` on
  `planning_state.candidate_grounding_batch` (by `proposal_id`, the same
  id as `AICandidateReviewItem.candidate_id`) to carry over
  `evidence.provider_place_id`/`evidence.provider_name` onto the new
  `PromotedAICandidate` -- no new provider/grounding lookup, just reading
  what's already stored. Every non-eligible item's id goes into
  `skipped_candidate_ids` instead. It takes no provider/LLM dependency and
  never mutates `planning_state`.
- **`AICandidatePromotionService.apply_promotion`** is the only mutating
  entry point: it calls `build_promotion_report` and assigns the result to
  `planning_state.ai_candidate_promotion_report` (replacing whatever was
  there before, never appending), then calls `planning_state.touch()`.
  Mirroring every other mutation-flavored service (e.g.
  `UserLockService.add_lock`), it does not persist itself -- the caller
  (the API route) still owns `planning_state_repository.save`.
- **`PlanningState.ai_candidate_promotion_report: AICandidatePromotionReport
  | None`** (`backend/app/models/planning_state.py`) is a new field,
  defaulting to `None`. It is set only by
  `POST /trips/{trip_id}/ai-candidate-promotions` -- nothing in
  `PlanningOrchestrator`/`generate_full_plan` touches it, and
  `GET /trips/{trip_id}/ai-candidate-review` never sets it either
  (confirmed by a dedicated test).
- **Route**: `promote_ai_candidates` follows the existing
  `create_trip_lock`/`delete_trip_lock` mutation pattern -- `trip_not_found_error`
  for an unknown `trip_id`, otherwise `apply_promotion` then
  `planning_state_repository.save`, returning
  `AICandidatePromotionResponseData`. No other `PlanningState` field is
  touched besides `ai_candidate_promotion_report` and
  `metadata.updated_at`.
- **`PromotedAICandidate`/`AICandidatePromotionReport` model validators**:
  `PromotedAICandidate.promoted` is enforced `True` by a field validator
  (an ineligible candidate never becomes a `PromotedAICandidate` object at
  all -- it's an id in `skipped_candidate_ids` instead).
  `AICandidatePromotionReport` has a `model_validator` requiring
  `promoted_count`/`skipped_count`/`total_reviewed_candidates` to exactly
  match their backing lists, and forbidding any id from appearing in both
  `promoted_candidates` and `skipped_candidate_ids`.
- **Idempotent**: `PromotedAICandidate.candidate_id` is deterministically
  `f"promoted_{original_ai_candidate_id}"`, and the stored report is fully
  replaced on every `POST` call -- calling it twice in a row yields the
  same promoted candidates, never a duplicate.
- **Confirmed unchanged**: `ExperiencePlannerService` scheduling
  (Section 15), route-aware scheduling (Section 35-40), regeneration
  refusal, and every existing candidate-quality/grounding/review test all
  pass unmodified -- this step only adds a new write path that stores an
  already-computed eligibility verdict, never a new itinerary mutation.

## 61. Frontend AI Candidate Review/Promotion Panel (Step 170E, final Section 170 step)

**Step 170E is a frontend-only step** (`frontend/app/page.tsx`,
`frontend/lib/types.ts`, `frontend/lib/api.ts`) consuming the endpoints
Sections 57-60 already built. No backend model, service, or route logic
changed in this step.

- **Backend endpoints consumed**: `GET /trips/{trip_id}/ai-candidate-review`
  (new `getAiCandidateReview` API helper, called alongside the existing
  `loadPlanResult` `Promise.all` fetch group) and
  `POST /trips/{trip_id}/ai-candidate-promotions` (new `promoteAiCandidates`
  helper, called only from the panel's "Refresh AI promotion report"
  button). `PlanningState.ai_candidate_promotion_report` itself is read
  from the existing `GET /trips/{trip_id}` response (`TripData.
  planning_state.ai_candidate_promotion_report`, mirroring how
  `accommodation_inventory_report`/`flight_inventory_report` are already
  read) -- no new endpoint was needed for that field.
- **New frontend types** (`frontend/lib/types.ts`): `AICandidateReviewItem`,
  `AICandidateReviewReport`, `AICandidateReviewData`, `PromotedAICandidate`,
  `AICandidatePromotionReport`, `AICandidatePromotionData` -- each a direct
  field-for-field mirror of the corresponding backend Pydantic model
  (Sections 57/59), so a TypeScript compile failure would immediately
  surface any future drift. `ExperienceItem` also gained
  `promoted_from_ai`/`original_ai_candidate_id`/`provider_place_id`/
  `provider_source` (Section 60's new fields).
- **`AICandidateReviewSection`** (`frontend/app/page.tsx`) renders the
  review report's summary counts, then groups `items` into "Eligible for
  scheduling"/"Not eligible", and (when
  `ai_candidate_promotion_report` exists) "Promoted candidates"/"Skipped
  candidates" using `promoted_candidates`/`skipped_candidate_ids`. Placed
  in the "Data sources and candidates" group, immediately after
  `FlightInventorySection` and before the destination candidate lists.
  Handles every combination of missing data gracefully: no review report
  at all (`status="no_candidate_data"`), a review report with zero items,
  and a review report present but no promotion report yet -- each renders
  an honest one-line message rather than crashing or guessing.
- **`AIPromotedBadge`** (`frontend/app/page.tsx`) renders inside
  `ScheduledExperienceCard` only when `experience.promoted_from_ai` is
  `true` -- never for a normal provider-backed experience. Shows a fixed
  "AI-suggested · Provider-grounded" label plus `provider_source`/
  `original_ai_candidate_id` only when the backend actually returned them.
- **No scheduling/regeneration behavior changed.** This step reads
  already-existing endpoints/fields only; `PlanningOrchestrator`,
  `ExperiencePlannerService`, and every backend route are untouched.

## 60. Safe Scheduling Integration for Promoted AI Candidates (Step 170D)

**Step 170D lets a promoted AI candidate (Section 59) join
`ExperiencePlannerService`'s attraction scheduling pool**, and adds the
orchestrator wiring needed for that to actually happen within a single
`POST /generate` call, docs/13_llm_reasoning_pipeline.md section 83.

- **`PlanningOrchestrator._run_ai_candidate_promotion_stage`** (new,
  called from `run_destination_context_stage` right after the existing
  Step 161B shadow discovery stage, and after `candidate_quality_report`
  is computed) auto-calls `AICandidatePromotionService.apply_promotion`
  and stores `ai_candidate_promotion_report` -- **before**
  `run_experience_plan_stage` runs later in the same `generate_full_plan`
  call. Without this, a promoted candidate could never reach scheduling at
  all: `POST /trips/{trip_id}/ai-candidate-promotions` can only run after
  `experience_plan` already exists, and there is no regeneration path back
  into scheduling (Step 138's refusal is completely untouched). A pure
  no-op whenever `ai_candidate_proposal_batch` is `None` (shadow mode
  disabled, the default) -- `ai_candidate_promotion_report` stays `None`
  exactly as it did before this step. Fails safe (mirrors every other
  Step 166D-style stage): an unexpected exception from `apply_promotion`
  is swallowed and the field stays whatever it already was, never
  crashing generation. The explicit `POST /ai-candidate-promotions`
  endpoint remains available and idempotent for a manual recompute.
- **`ExperiencePlannerService.run`** (`backend/app/services/
  experience_planner_service.py`) gained `_build_promoted_candidate_pois`,
  called right after the existing quality-based selection
  (`_select_candidates_by_quality`) and before must-visit/interest
  tiering. It reads `planning_state.ai_candidate_promotion_report` only --
  never `ai_candidate_proposal_batch`/`candidate_grounding_batch`/
  `GroundedCandidate`/`AICandidateProposal` directly (a dedicated
  source-inspection test enforces this one-way dependency: the planner may
  depend on the already-vetted `PromotedAICandidate`/
  `AICandidatePromotionReport` models, never on the raw proposal/grounding
  internals or the eligibility/review services themselves). Each
  `PromotedAICandidate` is converted into the exact same dict shape a real
  `destination_context` candidate already uses (via
  `_promoted_candidate_to_poi_dict`), then appended to the scheduling
  pool -- from that point on it goes through identical geographic
  day-grouping, nearest-neighbor ordering, and the pace-based per-day cap
  as any other candidate. It is never re-scored by `CandidateQualityService`
  (its tier was already verified once during Step 170B promotion) and
  never allowed to bump an already-scheduled real candidate the planner's
  existing rules would have picked anyway -- appending happens after real
  candidates already claimed their priority-ordered position, so a
  promoted candidate only ever fills a slot real candidates didn't
  naturally win.
- **Duplicate prevention** (`_candidate_identity_keys`): before merging, a
  promoted candidate's `provider_place_id` *and* normalized name are both
  checked against every real `candidate_pois` entry's own place id/name --
  a match on either signal skips the promoted candidate as a duplicate
  (with an honest assumption explaining why), so the same real place can
  never be scheduled twice.
- **Missing-field handling**: a `PromotedAICandidate` with no `coordinates`
  (the one field required to place it geographically -- copied verbatim
  from `GroundedCandidate.evidence.coordinates` at promotion time, Section
  59) is skipped with an explicit assumption rather than scheduled with a
  guessed location. No coordinate, rating, opening hour, price, route, or
  description is ever invented at this layer either.
- **Provenance is preserved on the schedule itself**: `ExperienceItem`
  (`backend/app/models/planning_state.py`) gained
  `promoted_from_ai: bool = False`, `original_ai_candidate_id`,
  `provider_place_id`, and `provider_source` -- all `None`/`False` for a
  normally-scheduled real candidate, and set only for a candidate that
  came from `ai_candidate_promotion_report`. `_build_experience_item`'s
  `why_included`/`claim_sources` text is adjusted accordingly (attributing
  the claim to `ai_candidate_promotion_report.promoted_candidates` instead
  of `destination_context.candidate_pois` for a promoted item).
- **Byte-for-byte no-op by default**: when `ai_candidate_promotion_report`
  is absent or has zero promoted candidates, `_build_promoted_candidate_pois`
  returns `([], [])` and scheduling behaves exactly as it did before Step
  170D -- confirmed by dedicated tests comparing a baseline run against one
  with an explicitly empty/absent report.
- **Unaffected**: accommodation/flight inventory (Sections 44/56),
  route-aware scheduling (Section 35-40), and regeneration refusal (Step
  138) -- none of their code paths changed, and the full existing test
  suite (candidate-quality, candidate-grounding, accommodation, flight,
  routing, regeneration-refusal) passes unmodified.

## 62. LangGraph Planning State and Deterministic Node Skeleton (Step 171A)

**Step 171A rebuilds the LangGraph orchestration skeleton** across three
files -- `backend/app/graphs/planning_graph_state.py` (state schema),
`planning_graph_nodes.py` (node factories), and `planning_graph.py` (graph
builder + `PlanningGraphRunner`) -- docs/13_llm_reasoning_pipeline.md
section 85 has the product-facing rationale.

- **This supersedes the earlier Step 162B/162C `planning_graph.py`
  prototype in place.** That prototype (a single-file `PlanningGraphState`
  TypedDict with `planning_state`/`executed_nodes`/`errors` only, plus
  `traveler_profile`/`destination_context`/`candidate_quality`/
  `ai_candidate_shadow`/`trip_strategy`/`stay_transport`/`experience_plan`/
  `validation` nodes mirroring `PlanningOrchestrator.generate_full_plan`'s
  exact order) is removed, along with its `backend/app/tests/graphs/
  test_planning_graph.py` test file (34 tests) -- both were fully isolated
  (only that test file imported the module; nothing else in the codebase
  referenced it), so removing them changes nothing observable elsewhere.
  Two comments in `test_scraped_flight_e2e.py`/`test_scraped_accommodation_e2e.py`
  that referenced the old test file by name were updated to point at the
  new one.
- **`PlanningGraphState`** (`planning_graph_state.py`) is a `TypedDict`
  carrying `trip_id`, `trip_request`, `planning_state` (the single source
  of truth, unchanged in shape), and four `Annotated[list[str],
  operator.add]` bookkeeping lists: `errors`, `warnings`, `completed_nodes`,
  `failed_nodes`. `build_initial_planning_graph_state(trip_id,
  trip_request, planning_state)` builds a fresh state with all four lists
  empty -- never invents any of the three required inputs.
- **Seven node factories** (`planning_graph_nodes.py`):
  `build_destination_context_node`, `build_stay_transport_node`,
  `build_ai_candidate_node`, `build_experience_planning_node`,
  `build_validation_node`, `build_provider_coverage_node`,
  `build_final_state_node`. Each takes an optional injected service
  (defaulting to the real one, mirroring `PlanningOrchestrator.__init__`'s
  own default-construction pattern) and returns a node function
  `(state) -> dict`. Every service-backed node wraps the exact existing
  method: `DestinationContextService.run`, `StayTransportService.run`,
  `ExperiencePlannerService.run`, `PlanValidatorService.run`. `provider_coverage`
  and `final_state` are pure checkpoints (no service to call --
  `ProviderCoverageService` has no single `run`/`build_report` entry
  point of its own; every real stage service already calls
  `record_provider_result` as it goes). `ai_candidate` defaults to a pure
  no-op (no `AICandidateDiscoveryService`/LLM call ever happens on its
  own); only an explicitly injected `AICandidatePromotionService` makes it
  call `apply_promotion` (Step 170C/170D, itself provider/LLM-call-free).
  Every node catches its own exceptions, appends a generic `f"{name}_node
  failed safely; planning_state left unchanged."` marker to `errors`/
  `failed_nodes` (never the raw exception, a prompt, or an LLM response),
  and returns a dict that omits `planning_state` entirely on failure --
  since keys without an `operator.add` reducer are last-write-wins in
  LangGraph and omitting the key entirely leaves the prior value
  untouched, a failed node can never fabricate `PlanningState` data.
- **Graph order** (`build_planning_graph`, `planning_graph.py`): `START ->
  destination_context -> stay_transport -> ai_candidate ->
  experience_planning -> validation -> provider_coverage -> final_state ->
  END`, built with the real `langgraph.graph.StateGraph`/`START`/`END`
  (the `langgraph` package was already a dependency as of Step 162B) and
  returned as a compiled `CompiledStateGraph` via `.compile()` -- not a
  hand-rolled substitute. `PlanningGraphRunner` (DI-friendly wrapper,
  mirrors `PlanningOrchestrator`'s own constructor pattern) and
  `run_planning_graph(trip_id, trip_request, planning_state)`
  (convenience entry point using real default services) round out the
  module, matching the superseded prototype's own public shape.
- **Not wired into `/generate` yet.** Neither
  `backend/app/api/routes/trips.py` nor `app/services/
  planning_orchestrator.py` imports `app.graphs` anywhere -- verified by
  dedicated AST-import-inspection tests in the new
  `backend/app/tests/graphs/test_langgraph_planning_graph.py`, mirroring
  the pre-existing `test_generation_progress.py` assertions that already
  covered the superseded prototype. `PlanningOrchestrator.generate_full_plan`,
  itinerary scheduling, route-aware scheduling, and regeneration refusal
  are all completely unmodified by this step.
- **No live network call in any test**: every service a node might
  default-construct resolves its provider calls through the existing
  `ProviderGateway`, which the test suite's autouse `conftest.py` fixtures
  (`_deterministic_places_provider`, `_isolate_provider_cache_store`,
  `_reset_in_memory_repositories`) already keep fully network-free and
  deterministic -- the same guarantee `PlanningOrchestrator`'s own test
  suite already relies on.

## 63. LangGraph Planning Runner/Service (Step 171B)

**Step 171B adds `LangGraphPlanningService`**
(`backend/app/services/langgraph_planning_service.py`), a thin wrapper
around Section 62's `PlanningGraphRunner` -- docs/13_llm_reasoning_pipeline.md
section 86 has the product-facing rationale.

- **`LangGraphPlanningService.run(trip_id, trip_request, planning_state=None)
  -> LangGraphPlanningResult`**: if `planning_state` is `None`, builds a
  fresh one via `_build_new_planning_state` (mirrors
  `PlanningOrchestrator.create_trip`'s construction conventions --
  `PlanningStage.CREATE_TRIP`/`PipelineStatus.DRAFT` bookkeeping, a
  `not_connected` `provider_gateway.default_provider_coverage()` snapshot,
  an idle `GenerationProgress()`, and a freshly recomputed
  `regeneration_readiness` via the existing `RegenerationReadinessService`
  singleton -- but never calls `TripRepository.create`/
  `PlanningStateRepository.save`, unlike `create_trip` itself). It then
  builds the initial `PlanningGraphState` and invokes the compiled graph
  via `PlanningGraphRunner.run`, returning the resulting `PlanningState`
  plus graph bookkeeping.
- **`LangGraphPlanningResult`** (a `dataclass`): `planning_state`,
  `completed_nodes`, `failed_nodes`, `errors`, `warnings` -- deliberately
  a separate wrapper rather than new `PlanningState` fields, since this is
  graph-run bookkeeping (a test/debug trace of which nodes ran and why),
  not a section of the travel plan itself. This keeps `PlanningState`'s
  existing shape completely untouched by this step.
- **Dependency injection**: the constructor accepts the same optional
  service parameters `PlanningGraphRunner` does
  (`destination_context_service`, `stay_transport_service`,
  `ai_candidate_promotion_service`, `experience_planner_service`,
  `plan_validator_service`), plus an optional
  `regeneration_readiness_service_instance` for the new-`PlanningState`
  path. All default to the real services/singleton (safe in any
  environment, since their provider calls already resolve through
  `ProviderGateway`'s existing safe defaults) -- tests inject fakes to
  keep every run fully deterministic and network-free.
- **Persistence remains outside the graph runner.** This service never
  imports or calls `PlanningStateRepository`/`TripRepository` -- a
  dedicated test monkeypatches both to raise if called and proves a full
  run (fresh or pre-existing `planning_state`) never triggers either.
  Persisting the result, if a caller ever wants to, stays that caller's
  own responsibility, exactly like `PlanningGraphRunner`/
  `PlanningOrchestrator`'s own stage-runner methods.
- **Fails loud, not silent, at the top level.** Node-level failures are
  already caught inside `planning_graph_nodes.py` and surfaced via the
  result's `failed_nodes`/`errors`. A genuinely unexpected exception from
  invoking the graph itself is logged (`logger.warning(..., exc_info=True)`)
  and re-raised -- mirroring `PlanningOrchestrator.generate_full_plan`'s
  own top-level try/except-and-re-raise pattern -- rather than returning a
  misleadingly empty/successful result.
- **Not wired into `/generate` yet.** Neither
  `backend/app/api/routes/trips.py` nor `planning_orchestrator.py`
  imports `langgraph_planning_service` or `app.graphs` anywhere --
  verified by dedicated AST-import-inspection tests in the new
  `backend/app/tests/services/test_langgraph_planning_service.py`.
  `PlanningOrchestrator.generate_full_plan`, itinerary scheduling,
  route-aware scheduling, and regeneration refusal remain completely
  unmodified.

## 64. Read-Only LangGraph Shadow-Run Endpoint (Step 171C)

**Step 171C adds `POST /trips/{trip_id}/langgraph-shadow-run`**
(`backend/app/api/routes/trips.py`, handler `run_langgraph_shadow`), plus
`LangGraphShadowRunResponseData`
(`backend/app/schemas/langgraph_shadow_run.py`) -- docs/13_llm_reasoning_pipeline.md
section 87 has the product-facing rationale.

- **Route**: `trip_not_found_error` for an unknown `trip_id` (same 404
  behavior as every other trip endpoint); otherwise fetches the trip's
  `PlanningState`, calls `.model_copy(deep=True)` to get an isolated
  shadow copy, and runs it through a freshly constructed
  `LangGraphPlanningService()` (no services injected -- the real
  defaults, safe because their provider calls resolve through
  `ProviderGateway`'s existing safe defaults, exactly like `/generate`
  itself). The handler never calls `planning_state_repository.save`.
- **`LangGraphShadowRunResponseData`**: `trip_id`, `status` (`"completed"`
  when `failed_nodes` is empty, else `"completed_with_failures"` --
  derived purely from the graph result, never a claim about
  `ValidationReport.readiness_status`), `planning_state` (the shadow
  run's resulting `PlanningState` -- a preview/result, never the trip's
  official one), `completed_nodes`, `failed_nodes`, `errors`, `warnings`
  (all copied straight from `LangGraphPlanningResult`), and
  `persisted: Literal[False] = False` -- structurally impossible to be
  anything but `False`, mirroring the existing `ScrapedDataProvenance.
  official_provider: Literal[False]` pattern.
- **Persistence remains outside the graph runner, confirmed at the route
  level.** Dedicated tests prove: the trip's stored `PlanningState` is
  byte-for-byte unchanged after a shadow run (both for a fresh trip and
  an already-`/generate`d one), `version_history`/`regeneration_attempts`/
  `user_locks`/`plan_diff_preview`/`regeneration_readiness` are all
  unchanged, and `metadata.updated_at` doesn't move.
- **`POST /trips/{trip_id}/generate` is untouched.** Two dedicated tests
  keep both endpoints' code paths provably independent: one parses
  `run_langgraph_shadow`'s own AST body (excluding its docstring, to
  avoid a false positive from prose that legitimately mentions
  `PlanningOrchestrator.generate_full_plan` to explain what this endpoint
  is *not*) and confirms it never calls `generate_full_plan`/
  `planning_orchestrator`; the other confirms `generate_trip_plan`'s own
  source never mentions LangGraph or the graph package, and still calls
  only `planning_orchestrator`.

## 65. Config-Gated LangGraph /generate Mode (Step 171D)

**Step 171D adds one config field** --
`Settings.planning_engine_mode: str = Field(default="legacy", alias="PLANNING_ENGINE_MODE")`
(`backend/app/core/config.py`) -- and one new
`PlanningOrchestrator` method, `generate_full_plan_via_langgraph`
(`backend/app/services/planning_orchestrator.py`), so
`POST /trips/{trip_id}/generate` can optionally run through the Section
62-64 graph. docs/13_llm_reasoning_pipeline.md section 88 has the
product-facing rationale.

- **Config field follows the existing provider-selector convention.**
  Like `accommodation_provider`/`flight_provider`/`routing_provider`,
  `planning_engine_mode` is a plain string, not a Pydantic enum -- `Settings`
  accepts any value without raising. The safe fallback to `"legacy"` for an
  unrecognized value lives entirely in the route's own branch, not in
  `Settings` itself.
- **Route change is a single branch, nothing else.**
  `generate_trip_plan` (`backend/app/api/routes/trips.py`) now reads:
  ```python
  if get_settings().planning_engine_mode == "langgraph":
      planning_state = planning_orchestrator.generate_full_plan_via_langgraph(trip_id)
  else:
      planning_state = planning_orchestrator.generate_full_plan(trip_id)
  ```
  Both branches call a method already owned by the `planning_orchestrator`
  singleton -- the route's own source never references
  `LangGraphPlanningService`, confirmed by a dedicated structural test
  (`test_langgraph_generate_mode.py`) alongside the pre-existing Step
  171A-C tests asserting the same thing, updated to allow this one new,
  intended mention of the orchestrator's own new method name.
- **`generate_full_plan_via_langgraph` is new; `generate_full_plan` is
  untouched.** The new method: loads `PlanningState` (404 via
  `trip_not_found_error` if missing, matching `generate_full_plan`), marks
  generation started, calls `self.langgraph_planning_service.run(trip_id,
  planning_state.trip_request, planning_state)` (Section 63's
  `LangGraphPlanningService`, injected via a new optional constructor
  parameter defaulting to a real instance), applies the same
  `_READINESS_TO_PIPELINE_STATUS` mapping `run_validation_stage` already
  applies (the graph's `validation` node calls `PlanValidatorService.run()`
  directly, which never sets `pipeline_status` on its own), reruns
  `VersioningService.create_initial_version`/`PlanDiffPreviewService.
  recompute`/`RegenerationReadinessService.recompute` exactly as
  `generate_full_plan` does, marks generation finished, and saves. An
  unexpected exception marks generation failed and re-raises, matching
  `generate_full_plan`'s own `except Exception` block. A dedicated test
  (`test_generate_full_plan_source_has_no_graph_or_langgraph_reference`,
  pre-existing from Step 171B) keeps asserting `generate_full_plan`'s own
  source contains no `graph` reference at all.
- **Known, honest scope limit as this step landed, not a regression --
  closed in Step 171E, see Section 66.** The Section 62 graph's node set,
  as of this step, (`destination_context`/`stay_transport`/`ai_candidate`/
  `experience_planning`/`validation`/`provider_coverage`/`final_state`)
  was narrower than `generate_full_plan`'s stage list -- it had no
  `traveler_profile`, `trip_strategy`, `route_feasibility`,
  `route_aware_sequencing`, `travel_time_buffer`, `accommodation_inventory`,
  or `flight_inventory` node, so those `PlanningState` fields stayed unset
  after a langgraph-mode generate rather than being fabricated.
- **Everything else is provably unaffected.** Dedicated tests confirm: the
  default (and any unrecognized) config value never calls
  `LangGraphPlanningService.run` at all; `langgraph` mode persists its
  result identically to a `GET /trips/{id}` fetch; `generation_progress`
  reaches `"completed"`; `validation_report`/`provider_coverage` are
  present; `regeneration_readiness` is recomputed; `POST /regenerate` is
  still always `409`; the shadow endpoint (Section 64) never reads
  `planning_engine_mode` and its behavior is unaffected across all three
  config values; no Anthropic/Groq/`AICandidateDiscoveryService` call
  happens in either mode; and a simulated node failure in `langgraph` mode
  still returns `200` with the failed section honestly left unset (never a
  guessed value) rather than raising a `500`.

## 66. LangGraph Stage Parity and New Default Engine (Step 171E, final Section 171 step)

**Step 171E closes the Section 65 scope-limit gap and flips
`Settings.planning_engine_mode`'s default from `"legacy"` to
`"langgraph"`** once that parity was confirmed by the full test suite
(1989 tests passing). docs/13_llm_reasoning_pipeline.md section 89 has
the product-facing rationale.

- **Engine modes, final state.** Default (unset `PLANNING_ENGINE_MODE`,
  or explicit `"langgraph"`): `generate_trip_plan` calls
  `PlanningOrchestrator.generate_full_plan_via_langgraph`. Explicit
  `"legacy"`, or any unrecognized value: calls the original
  `generate_full_plan`, byte-for-byte unchanged since before Step 171D.
  The shadow endpoint (`POST /trips/{trip_id}/langgraph-shadow-run`,
  Section 64) never reads `planning_engine_mode` at all -- it always runs
  the graph, and never persists, regardless of engine mode.
- **Graph stage order, final state** (`backend/app/graphs/planning_graph.py`):
  ```text
  START -> traveler_profile -> destination_context -> candidate_quality
    -> ai_candidate -> trip_strategy -> stay_transport
    -> accommodation_inventory -> flight_inventory -> experience_planning
    -> route_feasibility -> route_aware_sequencing -> travel_time_buffer
    -> validation -> provider_coverage -> final_state -> END
  ```
  This is the same relative stage order `generate_full_plan`'s
  `stage_runners` tuple plus its inline sub-steps already use. Every new
  node (`backend/app/graphs/planning_graph_nodes.py`) wraps exactly one
  existing service method, mirroring the existing `build_destination_
  context_node`/`build_stay_transport_node`/etc. pattern: `build_
  traveler_profile_node` (`TravelerProfileService.run`), `build_
  candidate_quality_node` (`CandidateQualityService.build_report`, stores
  onto `candidate_quality_report`), `build_trip_strategy_node`
  (`TripStrategyService.run`), `build_accommodation_inventory_node`/
  `build_flight_inventory_node` (`AccommodationInventoryService`/
  `FlightInventoryService.build_report`, also setting the derived
  `ProviderCoverage.hotel_prices`/`flights` value via small mapping
  helpers intentionally mirroring -- not importing, to avoid a circular
  import -- `planning_orchestrator.py`'s own equivalent mappings, kept in
  sync by a dedicated test), `build_route_feasibility_node`
  (`RouteFeasibilityService.build_report`, plus `ProviderCoverage.routes`),
  `build_route_aware_sequencing_node` (`RouteAwareSequencingService.
  build_report`, then `apply_report` only when `Settings.route_aware_
  scheduling_enabled` is `True` -- the identical config flag and threshold
  `run_experience_plan_stage` reads -- rebuilding `route_feasibility_report`
  afterward exactly like legacy does when a reorder is applied), and
  `build_travel_time_buffer_node` (`TravelTimeBufferService.build_report`).
  `ai_candidate` itself is unchanged from Step 171A: a pure no-op unless a
  caller explicitly injects an `AICandidatePromotionService`.
- **`PlanningOrchestrator.__init__` now wires its own service instances
  into `LangGraphPlanningService`**, not fresh separate ones (`self.
  langgraph_planning_service = langgraph_planning_service or
  LangGraphPlanningService(traveler_profile_service=self.traveler_
  profile_service, ..., plan_validator_service=self.plan_validator_
  service)`), so a test or operator that reconfigures/monkeypatches
  `planning_orchestrator.<stage>_service` affects both engines
  identically. `ai_candidate_promotion_service` is deliberately left at
  its default (`None`) -- see the next bullet.
- **One documented, intentional gap remains: the AI candidate discovery
  shadow stage.** `Settings.ai_candidate_discovery_shadow_mode_enabled`
  (Step 161B) still has no graph node. `planning_graph_nodes.py` and
  `planning_graph.py` are structurally forbidden (by dedicated AST-based
  import tests) from ever importing `AICandidateDiscoveryService` at
  all -- this is intentional, matching CLAUDE.md's guidance not to wire
  that subsystem into the real pipeline without being asked to. Since
  shadow mode is off by default (the only configuration the LangGraph
  engine supports today), both engines already behave identically by
  default. Tests that specifically exercise the shadow-mode-through-
  `/generate` integration (`test_ai_candidate_promotion.py`, `test_ai_
  candidate_review.py`, `test_ai_candidate_discovery_shadow_mode.py`) now
  pin `PLANNING_ENGINE_MODE=legacy` explicitly, documented in each
  helper's own docstring.
- **`generation_progress` reports the same granularity in both engines.**
  `generate_full_plan_via_langgraph` maps the graph's returned
  `completed_nodes` back onto the nine `GENERATION_STAGE_KEYS` via a new
  `_GRAPH_NODES_BY_GENERATION_STAGE_KEY` dict (`planning_orchestrator.py`)
  -- e.g. `"stay_transport"` is only marked finished once
  `stay_transport`/`accommodation_inventory`/`flight_inventory` have all
  completed -- then calls the exact same `_mark_stage_started`/
  `_mark_stage_finished` helper methods `generate_full_plan` already
  uses. `"ai_candidate_shadow"` is still marked finished unconditionally
  (mirroring `generate_full_plan`'s own unconditional marking of that
  label, regardless of whether shadow mode is even on) -- never a claim
  that discovery itself ran.
- **One remaining honest behavioral difference, on the failure path
  only.** Legacy's Step 166D hardening stores an explicit `status=failed`
  placeholder report when `route_feasibility`/`route_aware_sequencing`/
  `travel_time_buffer` computation raises unexpectedly. The graph's
  equivalent nodes instead leave that field exactly as it already was --
  this graph's existing, Step 171A-established convention for every node,
  never fabricating even a placeholder. Three tests in
  `test_trips_smoke.py` (`test_route_feasibility_computation_failure_
  does_not_crash_generation` and its two siblings) test legacy's specific
  hardening contract and are pinned to `PLANNING_ENGINE_MODE=legacy` for
  that reason; the graph's own "leave unset" behavior is exercised by
  `test_langgraph_generate_mode.py`.
- **No scheduling/regeneration/provider behavior changed.** Route-aware
  scheduling defaults, regeneration refusal, and every deterministic
  service's own decision logic are completely untouched by this step --
  only new *orchestration* wiring (which existing service method a new
  graph node calls, and a handful of intentionally-mirrored, static
  status-to-label mapping constants) was added. No LLM/provider/network
  call was added, and no fake attraction/restaurant/hotel/flight/price/
  rating/route/opening-hour/description/booking-link data was introduced.

## 67. Route-Aware Scheduling as the Backend Default (Step 172A)

**Step 172A flips `Settings.route_aware_scheduling_enabled`'s default
from `False` to `True`** (`backend/app/core/config.py`) and adds stable
itinerary ordering metadata to `ExperienceItem`
(`backend/app/models/planning_state.py`). docs/13_llm_reasoning_pipeline.md
section 90 has the product-facing rationale.

- **Default behavior.** `PlanningOrchestrator.run_experience_plan_stage`
  (legacy) and the LangGraph engine's `route_aware_sequencing` node
  (`planning_graph_nodes.py`, Section 66) both call
  `RouteAwareSequencingService.apply_report` unconditionally now that the
  gate defaults to `True` -- but `apply_report`'s own safety contract
  (Section 60, completely unchanged) is still the only thing that decides
  whether a schedule actually changes: `status == success`, a real
  positive improvement past `route_aware_scheduling_min_improvement_seconds`
  (still default `0.0`), and a verified exact permutation of the day's
  current experience IDs. With the default `routing_provider=
  "not_connected"`, no suggestion ever reaches `status == success`, so
  this default alone changes nothing in an environment with no routing
  provider configured -- `route_aware_sequencing_report` stays
  `is_shadow_only=True`/`applied_to_itinerary=False`/`status=
  "not_connected"`, identical to before this step.
- **Explicit opt-out.** `ROUTE_AWARE_SCHEDULING_ENABLED=false` restores
  Step 166A's original behavior exactly: `apply_report` is never called
  by either engine, and the scheduled itinerary order is completely
  unchanged. `backend/app/tests/core/test_route_aware_scheduling_config.py`
  covers the config surface itself (default `True`, explicit `False`
  override, no-env-var construction); `backend/app/tests/api/
  test_trips_smoke.py` covers the explicit opt-out end to end with a fake
  routing provider that would otherwise produce a genuinely favorable
  reorder.
- **Fallback behavior when route data is missing.** Unchanged from
  Section 60/63: `RouteAwareSequencingReport.status`/
  `movement_data_provenance` (and each day's own
  `RouteAwareSequenceSuggestion.status`/`movement_data_provenance`)
  already honestly report `not_connected`/`unavailable`/`partial`/
  `failed` -- never a fabricated duration/distance, and never treated as
  eligible for `apply_report` unless `status == success`. A day that
  can't be sequenced falls back to `ExperiencePlannerService`'s existing
  haversine-grouped order, exactly as before this step.
  `PlanValidatorService`'s existing `route_feasibility_report`/
  `travel_time_buffer_report`-driven warnings (`_build_feasibility_
  warning`/`_build_travel_time_buffer_warnings`, both completely
  untouched by this step) already surface this honestly in
  `validation_report.warnings` -- Step 172A adds no new validator logic
  because the existing one already does this correctly.
- **Stable itinerary ordering metadata, new `ExperienceItem` fields:**
  `day_number: int | None`, `stop_order: int | None` (`ge=1`), and
  `route_aware_provenance: MovementDataProvenance | None`. All three
  default to `None` (never fabricated for an `ExperienceItem` built
  outside the normal scheduling path, which many existing test fixtures
  do). `ExperiencePlannerService.run()` stamps `day_number`/`stop_order`
  right after building each day's `experiences` list (mirroring that
  day's own `DailyPlan.day_number` and the item's 1-based position).
  `RouteAwareSequencingService._apply_day_order` re-stamps `stop_order`
  (the new position) and sets `route_aware_provenance =
  MovementDataProvenance.PROVIDER_BACKED` for every experience in a day
  it actually reorders -- `day_number` is left untouched, since a
  reorder only changes position within a day, never which day an
  experience belongs to. A day that is never reordered keeps whatever
  `route_aware_provenance` it already had (`None`, from
  `ExperiencePlannerService`) -- never guessed. This gives the frontend
  everything it needs to render `"Day {day_number}, Stop {stop_order}"`
  reliably in a later Step 172 step, without depending on array
  position alone.
- **Promoted AI candidates (Section 83/60) are unaffected.** A promoted
  candidate is merged into the same `ExperiencePlannerService` scheduling
  pool as any other candidate (unchanged), so it receives the exact same
  `day_number`/`stop_order` stamping, and route-aware reordering treats
  it identically to any other scheduled experience -- Section 170's
  promotion rules (provider-grounded, quality-approved, deterministic
  eligibility) are the only thing that ever got it there in the first
  place, and remain completely untouched.
- **No regeneration/accommodation/flight-provider behavior changed.**
  `POST /regenerate` still always `409`s;
  `AccommodationInventoryService`/`FlightInventoryService` and their own
  coverage-mapping logic are untouched; `ProviderCoverageService`'s
  existing honest-reporting contract is untouched. No LLM/provider/
  network call was added -- `apply_report` still only ever reaches a
  provider through the pre-existing `RouteAwareSequencingService.
  _get_route -> ProviderGateway.get_route` path.

## 68. Numbered Itinerary Stop Order Rendered in the Frontend (Step 172B)

Step 172B is a frontend-only consumer of Section 67's `day_number`/
`stop_order`/`route_aware_provenance` fields -- no backend file changed.
`frontend/app/page.tsx` now reads `experience.stop_order` (falling back
to array index + 1 only when `null`) to number each scheduled
experience's card, and reads `experience.route_aware_provenance` to
choose between "Suggested stop order" and "Provider-grounded route
order" captions per day. The frontend still renders `day.experiences` in
exactly the order the backend returned it -- it never reorders that
array itself, so the backend's `ExperiencePlannerService`/
`RouteAwareSequencingService.apply_report` ordering (Section 67) remains
authoritative. See docs/16_frontend_architecture.md section 39.19 for
the full rendering details.

## 69. Frontend Route-Aware Provenance/Report Consumption for Status Display (Step 172C)

Step 172C is another frontend-only consumer, this time of
`PlanningState.route_aware_sequencing_report` and
`PlanningState.route_feasibility_report` -- both already returned on
every `GET /trips/{trip_id}`/`POST /trips/{trip_id}/generate` response
before this step (no backend serialization change was needed); only
`frontend/lib/types.ts`'s hand-maintained type mirror was missing them.
The frontend reads `route_aware_sequencing_report.suggestions[].status`
(matched per day by `day_index`) alongside the existing
`experience.route_aware_provenance` to choose a three-way per-day status
label, and `route_feasibility_report.status` to decide whether to show
an honest "movement data unavailable" note -- see
docs/16_frontend_architecture.md section 39.20 for the full behavior.
No backend file changed.

## 70. Frontend Stop-to-Stop Movement Data Consumption (Step 172D)

Step 172D is a further frontend-only consumer of
`PlanningState.travel_time_buffer_report` (Step 166C) -- already
returned on every `GET`/`POST .../generate` response before this step;
only `frontend/lib/types.ts` was missing a type for it. The frontend
looks up the `TravelTimeBuffer` entry matching each pair of consecutive
scheduled experiences by `(from_experience_id, to_experience_id)` and
renders its `status`/`route_duration_seconds`/`route_distance_meters`
directly -- it never computes a distance from `experience.coordinates`
itself, and never fills in a duration/distance the backend left `null`.
See docs/16_frontend_architecture.md section 39.21 for the full
rendering behavior. No backend file changed.

## 71. Section 172 Complete: Final Backend Summary (Step 172E, final Section 172 step)

Step 172E closes Section 172 with a small backward-compatibility test
addition (`backend/app/tests/models/test_planning_state_backward_
compatibility.py`) and no other backend code change. Final Section 172
backend behavior, all confirmed still passing by the full test suite:

- **Default route-aware scheduling.**
  `Settings.route_aware_scheduling_enabled` defaults to `True` (Step
  172A) -- `RouteAwareSequencingService.apply_report` is called by both
  the LangGraph engine's `route_aware_sequencing` node and
  `PlanningOrchestrator.run_experience_plan_stage` (legacy), but only
  ever reorders a schedule when a suggestion is `status == success`
  with a real positive improvement past the configured minimum and a
  verified exact permutation of the day's current experience IDs
  (Section 60's safety contract, unchanged since Step 166B).
- **Explicit opt-out.** `ROUTE_AWARE_SCHEDULING_ENABLED=false` restores
  the original Step 166A shadow/report-only-forever behavior in both
  engines identically -- `test_route_aware_scheduling_explicit_opt_out_
  preserves_order_even_with_favorable_route_data` (`test_trips_smoke.py`)
  verifies this even when a fake routing provider would otherwise make
  a reorder genuinely favorable.
- **Order metadata, stable across both engines and after a reorder.**
  `ExperienceItem.day_number`/`stop_order`/`route_aware_provenance`
  (Step 172A) are stamped at initial scheduling
  (`ExperiencePlannerService.run`) and re-stamped by
  `RouteAwareSequencingService._apply_day_order` for any day it
  actually reorders -- `test_route_aware_scheduling_enabled_by_
  default_langgraph_engine_applies_favorable_route_data` and its
  `..._legacy_engine_...` sibling confirm both engines produce the
  identical final scheduled order and report state under the same
  config.
- **Old persisted trips never crash.** A trip's `PlanningState` missing
  every Step 166C/172A field entirely (not just `null` -- the key
  absent) still loads through `PlanningState.model_validate` --
  `PlanningStateRepository.__init__`'s own deserialization call -- with
  every missing field honestly `None`, confirmed end to end through a
  real `PlanningStateRepository`/`LocalJsonStore` round trip in the new
  backward-compatibility test module.
- **Everything else is untouched.** Regeneration refusal
  (`POST /regenerate` still always `409`), accommodation/flight
  inventory reporting, provider coverage, and LangGraph's own stage
  parity (Section 66) are all unaffected by Section 172 end to end --
  Section 172 only ever added new orchestration-adjacent metadata
  (order stamping) and a config default flip, never new scheduling,
  regeneration, or provider logic.

## 72. Route Geometry Contract (Step 173A)

**Step 173A adds a route path geometry contract to the routing
subsystem** (`backend/app/models/routing.py`,
`backend/app/providers/routing/osrm_adapter.py`,
`backend/app/services/route_feasibility_service.py`,
`backend/app/services/travel_time_buffer_service.py`), so a later
Section 173 step can draw real itinerary paths on the map. This step
adds the contract only -- no scheduling, route-aware sequencing, or
LangGraph behavior changed.

- **New model: `RoutePathPoint`** (`lat`/`lon`, bounds matching
  `GeoPoint`) -- one ordered point along a provider-backed route's real
  path.
- **`RouteResult.geometry` changes type** from the unused `str | None`
  placeholder (Step 165A) to `list[RoutePathPoint] | None`. Nothing in
  this codebase read the old placeholder besides `OSRMRoutingAdapter`'s
  own cache round-trip, so this is a safe, backward-compatible change --
  `RouteResult` itself is never persisted in `PlanningState` or exposed
  through any API response directly.
- **`OSRMRoutingAdapter` now requests and parses real geometry.** The
  OSRM request changed from `overview=false` to
  `overview=full&geometries=geojson`; a successful route's response
  `geometry.coordinates` (a GeoJSON `LineString`, `[lon, lat]` pairs) is
  parsed into an ordered `RoutePathPoint` list via
  `_parse_geojson_linestring` -- malformed, empty, or missing geometry
  leaves `geometry=None` without failing the route's own
  distance/duration. `NotConnectedRoutingProvider` and every
  `not_connected`/`unavailable`/`failed` `RouteResult` constructor call
  already default `geometry` to `None`, unchanged.
- **Optional, nullable fields added to the two existing per-leg
  reports** (never a new report type, matching this codebase's existing
  architecture): `RouteLegFeasibility.route_geometry` and
  `TravelTimeBuffer.route_geometry` -- both `list[RoutePathPoint] |
  None`, both already carrying `from_experience_id`/`to_experience_id`
  so a future frontend step can locate a specific leg's path by backend
  ID rather than computing one itself.
  `RouteFeasibilityService`/`TravelTimeBufferService` copy
  `RouteResult.geometry` onto these fields verbatim -- no service logic,
  ordering, or aggregation status computation changed.
- **Backward compatible by construction.** `route_geometry` (and
  `RouteResult.geometry`'s new type) default to `None`; a `PlanningState`
  persisted before this step -- or even before Step 172A/166C entirely --
  still loads through `PlanningState.model_validate` with every new/
  changed field honestly absent, confirmed by new tests in
  `backend/app/tests/models/test_planning_state_backward_compatibility.py`
  and `test_route_geometry_models.py`.
- **Provider cache stays safe.** `OSRMRoutingAdapter`'s route cache
  entry now includes the same normalized `RoutePathPoint` list already
  on the `RouteResult` being cached (serialized via `model_dump(mode=
  "json")`, reconstructed via `RoutePathPoint(**point)` on read) --
  never a raw OSRM payload, never anything beyond this one route's own
  data. A malformed/corrupted cached geometry entry falls back to a
  live request exactly like any other broken cache entry, never
  fabricating a replacement.
- **No new API route or response schema was needed.**
  `GET /trips/{trip_id}`/`POST /trips/{trip_id}/generate` already
  serialize the full `PlanningState`, so `route_geometry` is already on
  the wire the moment a route succeeds with real geometry -- only
  `frontend/lib/types.ts` gained a matching, currently-unused type
  (`RoutePathPoint`, plus `TravelTimeBuffer.route_geometry`) for forward
  compatibility; no frontend rendering changed.

## 73. Frontend Route Path Rendering (Step 173B)

Step 173B is the first step that actually renders Section 173's
`route_geometry` contract: `frontend/app/page.tsx`'s `DayMapPreview`
reads `PlanningState.travel_time_buffer_report.buffers` (Step 166C) and
draws each leg's `route_geometry` (Step 173A) as a real map path only
when that leg's buffer entry has `status === "success"` and at least two
geometry points. No backend file changed -- `route_geometry` was already
serialized on the wire since Step 173A; this step only consumes it. See
docs/16_frontend_architecture.md section 39.24 for the full rendering
behavior.

## 74. Frontend Route Path Legend Hardening (Step 173C)

Step 173C is frontend-only; no backend file changed. It confirms and
hardens the consumption side of the Step 173A contract: `route_geometry`
on a `TravelTimeBuffer` remains the *only* source the frontend treats as
a drawable route path. `DayMapPreview` no longer draws any fallback line
between stop markers when a leg lacks that data -- the frontend never
substitutes a straight line between two stops' own coordinates, and
never computes or infers a path, distance, or duration itself. See
docs/16_frontend_architecture.md section 39.25 for the full rendering
and legend behavior.

## 75. Frontend Route Geometry Is Optional Per Leg (Step 173D)

Step 173D is frontend-only; no backend file changed. It notes explicitly
what was already implicit in the Step 173A contract: `route_geometry` is
optional per leg, not per day or per plan, so the frontend may render
partial path coverage -- a day with several legs can show a
provider-backed path on some legs and none on others, decided
independently per leg. The frontend also now applies its own read-side
coordinate-validity check (finite, in-range lat/lon, matching the bounds
`GeoPoint`/`RoutePathPoint` already enforce at write time) before
drawing any point, as a defensive guard against a pre-existing or
hand-edited local JSON record. See docs/16_frontend_architecture.md
section 39.26 for the full behavior.

## 76. Section 173 Complete: Full Map Path Visualization (Step 173E, final Section 173 step)

Section 173 (173A-173E) is complete. No backend file changed in 173E --
this step was a final review confirming the contract built across
173A-173D is coherent, safe, and matches what the frontend actually
consumes. Full contract, for reference:

- **`route_geometry` source of truth**: `RoutePathPoint` (`lat`/`lon`,
  bounds matching `GeoPoint`: `lat` in [-90, 90], `lon` in [-180, 180]),
  populated only on `RouteResult.geometry` by `OSRMRoutingAdapter` when a
  route request succeeds and OSRM's own GeoJSON response
  (`overview=full&geometries=geojson`) included a `LineString` geometry.
  Never populated for `not_connected`/`unavailable`/`failed`, never
  derived from `RouteRequest.origin_*`/`destination_*`, never a straight
  line.
- **Propagation**: `RouteFeasibilityService`/`TravelTimeBufferService`
  copy `RouteResult.geometry` verbatim onto
  `RouteLegFeasibility.route_geometry`/`TravelTimeBuffer.route_geometry`
  -- both `list[RoutePathPoint] | None`, optional per leg (a day's legs
  can have a mix of present/absent geometry; no aggregation or
  all-or-nothing behavior at the day or plan level).
- **Cache behavior**: `OSRMRoutingAdapter`'s route cache stores the same
  normalized `RoutePathPoint` list already on the cached `RouteResult`
  (via `model_dump(mode="json")`/`RoutePathPoint(**point)`), never a raw
  provider payload; a corrupted cache entry falls back to a live request
  like any other broken cache entry, never fabricating a replacement.
- **Backward compatibility**: `route_geometry` defaults to `None`
  everywhere it appears, so a `PlanningState` persisted before Step 173A
  (or before Step 172A/166C entirely) still loads cleanly with the field
  honestly absent -- covered by
  `test_planning_state_backward_compatibility.py` and
  `test_route_geometry_models.py`.
- **Frontend consumption** (docs/16_frontend_architecture.md sections
  39.24-39.26 for full detail): the frontend reads
  `route_geometry` only from `PlanningState.travel_time_buffer_report.
  buffers`, draws a leg's path only when that leg's `status ===
  "success"` and `route_geometry` has at least two valid (finite,
  in-range) points, and never falls back to a straight line or a
  frontend-computed path/distance/duration for any leg without one.
- **No API/schema addition needed**: `GET /trips/{trip_id}`/
  `POST /trips/{trip_id}/generate` already serialize the full
  `PlanningState`, so this entire contract has been on the wire since
  Step 173A.

No scheduling, route-aware sequencing, LangGraph, regeneration, or
accommodation/flight provider behavior changed anywhere across Section
173.

## 77. Regeneration Request Contract and Guardrails, No Mutation Yet (Step 174B)

Step 174B (Section 174, following the read-only Step 174A audit) gives
`POST /trips/{trip_id}/regenerate` a real request contract and a staged
guardrail chain, ahead of Step 174C's actual mutation. No planning
stage, provider, or LangGraph call is added anywhere in this step.

- **New request schema `RegenerateRequest`** (`backend/app/schemas/trips.py`):
  `confirm: bool = False`, `scope: str = "affected_stages"`. Both fields
  are optional with defaults, so the endpoint remains callable with no
  JSON body at all -- FastAPI falls back to `RegenerateRequest()` when
  the body is absent (`regenerate_request: RegenerateRequest | None =
  None` in the route, defaulted to a fresh `RegenerateRequest()` inside
  the function body). `scope` is accepted now to avoid a future breaking
  request-shape change, but nothing reads any value other than the
  default in this step -- no day-level or item-level scope exists yet.
- **Four ordered outcomes in `regenerate_trip_plan`**
  (`backend/app/api/routes/trips.py`), each still ending in a `409` and
  each still only appending one `RegenerationAttempt`:
  1. `confirm` missing or `false` -- byte-for-byte the original
     `REGENERATION_NOT_AVAILABLE` refusal from before this step.
  2. `confirm=true` with `sum(lock.is_active for lock in user_locks) > 0`
     -- new `REGENERATION_BLOCKED_BY_LOCKS` error
     (`regeneration_blocked_by_locks_error`, `core/errors.py`), since
     locks today are bookkeeping only (no planning stage service reads
     or respects them yet -- see Step 174A's audit) and a confirmed
     request must refuse outright rather than silently ignore one.
  3. `confirm=true` with an empty `feedback_history` -- new
     `REGENERATION_NO_PENDING_FEEDBACK` error
     (`regeneration_no_pending_feedback_error`).
  4. `confirm=true`, feedback exists, zero active locks -- exactly
     Section 174's chosen MVP scope (per the Step 174A audit), but still
     returned the original `REGENERATION_NOT_AVAILABLE` refusal in this
     step. **Superseded by Step 174C** (section 78 below), which
     replaces only this branch with a real call to
     `PlanningOrchestrator.rerun_affected_stages`.
- **`RegenerationAttemptService.record_blocked_attempt` gained optional
  `reason_code`/`message`/`status` parameters**, all defaulting to
  exactly the values it always used before this step -- every existing
  call site (including `test_regenerate_uses_regeneration_attempt_service_exactly_once_per_call`'s
  single-argument monkeypatch spy) is unaffected. The route passes the
  matching `reason_code`/`message` for whichever branch fired, so the
  audit trail and the HTTP error can never disagree.
- **Two new `ErrorCode` values** (`backend/app/schemas/errors.py`):
  `REGENERATION_BLOCKED_BY_LOCKS`, `REGENERATION_NO_PENDING_FEEDBACK`.
  `RegenerationAttempt.reason_code` is already a plain `str` field, so no
  model change was needed to carry either new value.
- **Zero mutation in every branch**: no branch touches `experience_plan`,
  `route_aware_sequencing_report`, `travel_time_buffer_report` (or its
  route geometry), `destination_context`, `validation_report`,
  `provider_coverage`, `feedback_history`, `pending_feedback_summary`,
  `user_locks`, `version_history`, or `plan_diff_preview`; none call
  `PlanningOrchestrator.rerun_affected_stages`, `generate_full_plan`,
  `generate_full_plan_via_langgraph`, `apply_feedback`, or
  `LangGraphPlanningService`; `regeneration_readiness`/`plan_diff_preview`
  are never recomputed by this endpoint, exactly as before. Covered by
  new tests in `test_regenerate_refusal.py`'s "Step 174B" section; every
  pre-existing refusal/guardrail test in that file and
  `test_regenerate_guardrails.py` continues to pass unmodified.
- **`docs/17_regeneration_manual_qa.md` updated** with the new
  `confirm=true` guardrail matrix and expanded failure signs -- see that
  doc for the full manual QA flow.

## 78. Real Deterministic Regeneration for the MVP Scope (Step 174C)

Step 174C replaces Step 174B's placeholder fourth branch (`confirm=true`,
feedback exists, zero active locks) with a real, deterministic mutation
-- still gated to that exact same MVP scope, still backend-only, still no
frontend UI. Two new safe-refusal conditions guard the boundary of that
scope before any mutation is attempted, and the mutation itself reuses
only already-existing orchestrator/versioning machinery.

- **Two additional safe-refusal conditions**, both still returning the
  existing `REGENERATION_NOT_AVAILABLE` (no new error code needed):
  no affected stage can be derived from the pending feedback (e.g. every
  event is unclassified `general_feedback`), or no plan has ever been
  generated for this trip (`experience_plan is None`). Neither widens the
  MVP scope; both just refuse rather than rerun nothing or regenerate a
  trip that doesn't have a plan yet.
- **New helper `_derive_regeneration_affected_stages`**
  (`backend/app/api/routes/trips.py`): prefers
  `planning_state.pending_feedback_summary.affected_stages` (the rollup
  `FeedbackService` already keeps fresh on every feedback submission);
  falls back to unioning `feedback_history[].affected_stages` directly
  only when that rollup is empty despite feedback existing (e.g. an
  older persisted trip from before `pending_feedback_summary` existed).
  Ordered to match `PlanningStage`'s own declaration order.
- **The mutation itself is one call**:
  `PlanningOrchestrator.rerun_affected_stages(planning_state,
  affected_stages)` -- already existing (see the Step 174A audit),
  previously never called anywhere. When `PlanningStage.EXPERIENCE_PLAN`
  is among the affected stages, its own `run_experience_plan_stage`
  already reruns `RouteFeasibilityService`/`RouteAwareSequencingService`/
  `TravelTimeBufferService` internally (Section 165/166/172/173
  behavior, completely unmodified), so route ordering, movement
  transparency, and route geometry all stay consistent automatically --
  no separate call was needed for any of them.
- **Never calls `LangGraphPlanningService`, `generate_full_plan`, or
  `generate_full_plan_via_langgraph`** -- verified by
  `test_regenerate_success_does_not_call_langgraph_or_full_generation`.
  Behavior is identical regardless of `Settings.planning_engine_mode`,
  since `rerun_affected_stages` calls the orchestrator's own `run_*_stage`
  methods directly rather than routing through the LangGraph graph.
- **After a successful rerun**: `VersioningService.create_version_after_feedback`
  (already existing, previously never called) records the new version --
  `changed_sections` is the rerun stages' own `.value` strings,
  `preserved_sections` is always `[]` (locks are disallowed entirely for
  this scope), `feedback_event_id` is the most recent pending feedback
  event's id (the model's own field is a single reference; every pending
  event's id is still reported in the response's
  `applied_feedback_event_ids`). `plan_diff_preview_service.recompute`/
  `regeneration_readiness_service.recompute` then run exactly as they do
  on every other write path in this router, and
  `RegenerationAttemptService.record_applied_attempt` (new, thin wrapper
  around `record_blocked_attempt` with `status="applied"`,
  `reason_code="REGENERATION_APPLIED"`) records the audit entry.
- **New response schema `RegenerateResponseData`**
  (`backend/app/schemas/regeneration_result.py`): `trip_id`, `status`,
  `previous_version`, `current_version`, `changed_sections`,
  `preserved_sections`, `applied_feedback_event_ids`,
  `active_lock_count`, `message`. Deliberately minimal -- no plan
  content, no fabricated diff; a caller wanting the plan's current
  content follows up with `GET /trips/{trip_id}` as usual.
- **Failure handling**: `rerun_affected_stages` is called inside a
  `try`/`except`; on any unexpected exception, no version is created, a
  `RegenerationAttempt` with `status="failed"` is recorded (reusing
  `record_blocked_attempt`'s existing `status` override from Step 174B),
  and the endpoint still raises the same `REGENERATION_NOT_AVAILABLE`
  refusal rather than returning `200`. This is a thin safety net, not new
  rollback machinery: the individual stage services already fail safe
  internally for expected provider-level failures (Step 166D), so this
  `except` only ever catches a genuinely unexpected bug.
- **`regeneration_readiness.can_regenerate` is still never set to
  `true`** -- `RegenerationReadinessService` was not touched in this
  step, on purpose, matching the Step 174B/174C boundary of not widening
  what that read-only gate advertises. **Superseded by Step 174D**
  (section 79 below), which updates this gate to honestly track the MVP
  scope this step's mutation path actually supports.
- **`feedback_history` is not cleared or marked applied** by a
  successful regeneration -- an intentional, documented limitation (see
  docs/17_regeneration_manual_qa.md), not an oversight. A second
  `{"confirm": true}` call immediately after a success will see the same
  pending feedback and, if it still derives a real affected stage, will
  regenerate again. **Superseded by Step 174D** (section 79 below),
  which marks the feedback a successful regeneration used as applied so
  a repeat call correctly refuses instead.

## 79. Applied-Feedback Lifecycle and Honest Regeneration Availability (Step 174D)

Step 174D closes Step 174C's two documented gaps: `regeneration_readiness`/
`plan_diff_preview` now honestly advertise the exact MVP scope the
mutation path supports, and a successful regeneration marks the
feedback it used as applied so it is never reprocessed by a later call.

- **New `FeedbackEvent` fields** (`backend/app/models/planning_state.py`):
  `applied_at: datetime | None = None`, `applied_in_version: str | None
  = None`, both defaulting to `None` -- an older persisted event (from
  before this step) loads as honestly pending. `handling_status`
  (already existing) is set to `"applied"` alongside them; feedback
  capture itself never sets any of the three.
- **New shared helpers in `backend/app/services/feedback_service.py`**,
  used by every consumer so none can disagree about which feedback still
  counts:
  - `pending_feedback_events(feedback_history)` -- `applied_at is None`.
  - `derive_pending_affected_stages(feedback_history)` -- union of
    pending events' `affected_stages`, ordered to match `PlanningStage`'s
    declaration order. Replaces the near-identical logic that used to
    live directly in `backend/app/api/routes/trips.py` (Step 174C) and
    the separate `_STAGE_ORDER` tuples that used to live independently
    in `PlanDiffPreviewService`/`FeedbackService` -- now one definition.
  - `FeedbackService.recompute_pending_feedback_summary(planning_state)`
    -- new public method (the underlying `_compute_pending_feedback_summary`
    now filters to pending events only, and returns an honest "all
    captured feedback has already been applied" note when
    `feedback_history` is non-empty but every event is applied, distinct
    from "no feedback captured yet").
- **`RegenerationReadinessService`/`PlanDiffPreviewService` rewritten**
  with two new branches inserted between the existing "no plan"/"no
  pending feedback" branches and the final branch: active locks present
  (still blocked/`not_available`, but now shows the derivable
  `would_consider_sections`/`would_create_version` as a preview even
  though it can't run), and pending feedback with no derivable stage
  (still blocked/`not_available`). Only the final branch --  version
  exists, pending feedback exists, zero active locks, at least one real
  derivable stage -- sets `can_regenerate=True`/`status="ready"` (readiness)
  or `regeneration_available=True`/`preview_status="regeneration_available"`
  (diff preview), with `missing_capabilities`/`blocked_by` both empty.
  Every other branch keeps its prior shape (including still listing
  `"regeneration_engine"` as a missing capability, since the MVP-scope
  engine isn't usable for *this* trip's current state until every
  condition holds).
- **`POST /trips/{trip_id}/regenerate`'s guardrails now use pending
  feedback, not raw `feedback_history`**: the "no pending feedback"
  guard and the affected-stage derivation both call
  `pending_feedback_events`/`derive_pending_affected_stages` -- so a
  trip whose only feedback has already been applied correctly hits
  `REGENERATION_NO_PENDING_FEEDBACK`, not a stale success.
- **After a successful rerun and version creation**, the route marks
  exactly the feedback event(s) whose ids were captured *before* the
  rerun started -- looked up again by id on the `planning_state`
  returned by `rerun_affected_stages`/`create_version_after_feedback`
  (never relying on object identity surviving those calls) -- setting
  `applied_at`/`applied_in_version`/`handling_status="applied"`. It then
  calls `feedback_service.recompute_pending_feedback_summary`,
  `plan_diff_preview_service.recompute`, and
  `regeneration_readiness_service.recompute`, in that order, before
  recording the applied audit attempt and saving -- so every derived
  view of "what's pending" is consistent by the time the response goes
  out.
- **Failure path unchanged in spirit, extended in effect**: if
  `rerun_affected_stages` raises, execution never reaches the
  applied-marking code at all (it comes after the `try`/`except`), so a
  failed rerun still cannot mark any feedback applied, matching Step
  174C's existing "no version, no pretended success" contract.
- **Repeat-safety, concretely**: `feedback_history` is never shortened
  or deleted -- calling `POST /trips/{trip_id}/regenerate` again with
  `{"confirm": true}` and no new feedback submitted since a success now
  correctly derives zero pending events and refuses with
  `REGENERATION_NO_PENDING_FEEDBACK`, never a second `v3` from the same
  feedback. Submitting genuinely new feedback creates a fresh pending
  event unaffected by any prior application, so a further regeneration
  call succeeds normally.

## 80. Section 174 Complete: Frontend Now Calls confirm=true, Backend Remains Sole Source of Truth (Step 174E, final Section 174 step)

Step 174E is frontend-only (`frontend/app/page.tsx`, `frontend/lib/api.ts`,
`frontend/lib/types.ts`) -- no backend file changed, and no backend
behavior changed. `frontend/lib/api.ts`'s `requestRegeneration` now
always sends `{"confirm": true, "scope": "affected_stages"}` (previously
sent no body at all), and the frontend's one "Regenerate from feedback"
button (`RegenerationReadinessSection`) is enabled only when
`RegenerationReadiness.can_regenerate` -- computed entirely server-side
by Step 174D -- is already `true`.

Critically, **this frontend gate is a UX convenience, not a security or
correctness boundary**: the backend re-validates every precondition
(`confirm`, active locks, pending feedback, derivable affected stages,
an already-generated plan) on every call regardless of what the frontend
already checked, exactly as it did before any frontend UI existed. A
stale frontend, a direct API client, or a race between two tabs can
still only ever get the outcomes Sections 174B-174D already defined --
the frontend enabling a button never widens what the backend will do.

On a successful response, the frontend performs one additional
read-only step beyond rendering `RegenerateResponseData`: it calls
`GET /trips/{trip_id}` (via the existing `loadPlanResult` helper, the
same one every trip load already uses) to refresh the itinerary,
version history, diff preview, readiness, and movement/route-path data
together. This is a fetch, not a computation -- the frontend never
derives, infers, or fabricates any of that content itself.

No `ErrorCode`, error message, `RegenerationAttempt` field, readiness/
diff-preview branch, or applied-feedback field changed in this step --
Section 174's backend contract (174B-174D) is exactly what the frontend
now calls, unmodified.

## 81. Validation Taxonomy Cleanup and Provider-Coverage Consistency Hardening (Step 175B)

Step 175B hardens `PlanValidatorService` (`backend/app/services/plan_validator_service.py`)
without changing planning, provider, LangGraph, or regeneration behavior.
Backend validation/service/test/docs only; no route, model shape, or
frontend change.

- **New provider-coverage consistency warning.** `PlanValidatorService`
  previously never read `planning_state.provider_coverage` at all, even
  though `ProviderCoverageService` maintains it independently -- the two
  could drift without either side noticing. A new function,
  `_build_provider_coverage_consistency_warnings`, cross-checks three
  already-computed pairs:
  - `accommodation_inventory_report` (status `success` with real offers)
    against `provider_coverage.hotel_prices`
  - `flight_inventory_report` (status `success` with real offers) against
    `provider_coverage.flights`
  - `route_feasibility_report` (status `success`) against
    `provider_coverage.routes`

  If the report side says success with real data but the coverage side
  says `failed`/`not_connected`/`unavailable`, a single
  `category="provider_coverage_consistency"` `ValidationIssue` is
  appended to `validation_report.warnings` -- `severity=WARNING`, never
  `CRITICAL`. If `provider_coverage` is missing (defensive-only; a
  `PlanningState` always constructs one) or the two sides already agree,
  no warning is added. The check is entirely read-only: it never writes
  to `provider_coverage`, never touches any inventory/feasibility report,
  and calls no provider, LLM, or network endpoint.
- **Readiness semantics unchanged.** `readiness_status` is still
  `ReadinessStatus.BLOCKED` only when `critical_issues` is non-empty, and
  `ReadinessStatus.NEEDS_REVIEW` otherwise; this new warning can never
  flip that outcome by itself, in either direction.
- **No new provider status vocabulary.** Every value the warning message
  quotes is an existing `AccommodationSearchStatus`/`FlightSearchStatus`/
  `ProviderStatus`-derived string already stored on `PlanningState` --
  nothing here defines a new enum or status taxonomy.
- **Stale wording cleanup.** `PlanValidatorService`'s class docstring,
  `_build_feasibility_warning`'s docstring, and the feasibility warning's
  provider-backed-success message/suggested-fix text described route
  feasibility, route-aware sequencing, and travel-time buffering as "not
  implemented yet" even after Sections 165/166/172/173 implemented them.
  Wording now says: route feasibility exists when provider-backed routing
  data is available; route-aware sequencing may be applied, unavailable,
  failed, or not connected depending on provider data/configuration; and
  validation reports that uncertainty rather than calculating a missing
  route itself. Message text that existing tests assert on verbatim (the
  no-route-data fallback message, the geographic-spread warning, and the
  budget/holiday warnings) was intentionally left unchanged -- those
  describe checks that genuinely remain unimplemented, so the prior
  wording there was already accurate.
- **Tests.** `backend/app/tests/services/test_plan_validator_service.py`
  gained coverage for: a missing `provider_coverage` not crashing
  validation; a consistent `provider_coverage` producing no warning; an
  accommodation/flight/route contradiction each producing exactly one
  `WARNING` (never a critical issue); the warning never flipping
  `readiness_status` by itself; and the warning never introducing a
  forbidden factual field. All pre-existing validator, provider-coverage,
  accommodation/flight/routing, and Section 170-174 tests continue to
  pass unchanged.

## 82. Route-Aware Sequencing, Movement-Data, and Route-Geometry Validation Hardening (Step 175C)

Step 175C adds three more additive, read-only checks to
`PlanValidatorService`, all gated behind the existing
`has_scheduled_experiences` branch (the same gate that already produces
the feasibility/travel-time-buffer warnings), so they only ever run when
an itinerary actually exists to report on. Backend validation/test/docs
only -- `RouteFeasibilityService`, `RouteAwareSequencingService`,
`TravelTimeBufferService`, the OSRM adapter, route geometry generation,
regeneration, and accommodation/flight provider code are all untouched.

- **`_build_route_aware_sequencing_issues`** reads
  `planning_state.route_aware_sequencing_report` (Step 166A/166B) and
  returns up to two `ValidationIssue`s:
  - `category="route_aware_sequencing"`: whether any
    `RouteAwareSequenceSuggestion.applied` is `True` (a provider-backed
    reorder actually happened via Step 166B's config-gated
    `apply_report`) versus every day keeping its originally scheduled/
    suggested order. When `route_aware_sequencing_report` is `None` or
    has no day suggestions, a single `SUGGESTION` says sequencing was
    never computed, and no `movement_data` issue is added for that case.
  - `category="movement_data"`: how many of the report's day suggestions
    have `status == ProviderStatus.SUCCESS` (real, provider-backed
    movement data) -- all, some (`WARNING`, since partial coverage is a
    genuine inconsistency worth reviewing), or none (`SUGGESTION`, since
    a fully-`not_connected`/`unavailable`/`failed` report is this app's
    expected default state, not an error).
- **`_build_travel_time_buffer_movement_issue`** reads
  `planning_state.travel_time_buffer_report`'s own aggregate `status`
  (Step 166C) -- a `category="movement_data"` issue distinct from, and
  added alongside, the pre-existing per-leg `category="travel_time_buffer"`
  insufficient-buffer warnings (`_build_travel_time_buffer_warnings`,
  unchanged). Returns `None` (no issue) when the report is `success`,
  since the existing per-leg warnings already cover anything worth
  reviewing; a missing report gets a `SUGGESTION`; `partial` gets a
  `WARNING`; `not_connected`/`failed`/`unavailable` get a `SUGGESTION`.
- **`_build_route_geometry_issue`** (with helper
  `_route_geometry_leg_counts`) counts legs with/without a real
  `route_geometry` (Step 173A), reading `travel_time_buffer_report.buffers`
  when present (falling back to `route_feasibility_report.legs` only when
  no buffer report exists, so the same leg is never double-counted from
  two independent reports) and returns one `category="route_geometry"`
  `SUGGESTION` naming how many legs have provider-backed geometry ("all
  N", "none of N", or "M of N"). Always `SUGGESTION`, never `WARNING` --
  missing geometry is expected, not an error, whenever routing/movement
  data itself is unavailable, and there is no field on
  `RouteLegFeasibility`/`TravelTimeBuffer` today that would let this
  function distinguish "never computed" from "computed but rejected as
  malformed/too-short," so it does not attempt that distinction. Returns
  `None` when neither report has any legs to inspect.
- **Message vocabulary is deliberately restrained**: none of these three
  checks ever says a resulting order/route is optimal, safest, or
  verified, and none of them ever computes a distance, duration, or
  geometry point of its own -- every figure quoted is a plain count of
  already-computed report/leg data.
- **Readiness semantics unchanged.** All three checks only ever append
  `WARNING`/`SUGGESTION` severity issues to the same `warnings` list
  `PlanValidatorService.run` already populates; `readiness_status` still
  becomes `BLOCKED` only via `critical_issues`, and stays `NEEDS_REVIEW`
  otherwise -- none of Step 175C's checks can flip that outcome in either
  direction, and none of them set `READY`.
- **Tests.** `backend/app/tests/services/test_plan_validator_service.py`
  gained 14 new tests covering: a missing `route_aware_sequencing_report`
  not crashing and producing only a `SUGGESTION`; `not_connected`
  sequencing producing honest, non-critical issues; an applied,
  provider-backed suggestion never overclaiming optimality/safety/
  verification; a default/suggested order without movement data being
  surfaced honestly; a missing `travel_time_buffer_report` not crashing;
  `not_connected`/`partial` buffer movement data being `SUGGESTION`/
  `WARNING` respectively and never critical; the pre-existing
  insufficient-buffer warning and its category staying unchanged; route
  geometry present/missing/partial being surfaced honestly without
  fabricating path/distance/duration detail; no issue when there are no
  legs to inspect; and none of these checks ever flipping
  `readiness_status` to `blocked`. All pre-existing validator,
  provider-coverage-consistency (175B), accommodation/flight/routing, and
  Section 170-174 tests continue to pass unchanged.

## 83. Regeneration Lifecycle Validation Plus Small Accommodation/Flight Refinement (Step 175D)

Step 175D adds two more functions to `PlanValidatorService`, called
unconditionally in `run()` (not gated behind `has_scheduled_experiences`,
since the regeneration lifecycle is a plan-level concept independent of
itinerary scheduling), plus one tiny `affected_section` refinement.
`RegenerationReadinessService`, `PlanDiffPreviewService`,
`FeedbackService`, and `POST /trips/{trip_id}/regenerate` are all
untouched -- this step only reads fields those services already write.

- **`_build_regeneration_lifecycle_issues`** reads
  `pending_feedback_summary`, `regeneration_readiness`, `user_locks`, and
  `regeneration_attempts`:
  - `pending_feedback_summary.status == "none"`: if `feedback_history` is
    also empty, nothing is added (the common default state). If
    `feedback_history` is non-empty (every event's `applied_at` is
    already set), a `category="regeneration"` `SUGGESTION` says
    previously submitted feedback has already been marked applied.
  - `pending_feedback_summary.status != "none"` (pending feedback
    exists): an active-lock count computed fresh from `user_locks`
    (rather than trusting a possibly-stale
    `regeneration_readiness.active_lock_count` snapshot) takes priority
    -> `WARNING` naming locks as the blocker. Otherwise,
    `regeneration_readiness.can_regenerate` decides between a `SUGGESTION`
    ("pending feedback is available for deterministic regeneration") and
    a `WARNING` quoting `regeneration_readiness.blocked_by` verbatim
    (covers, e.g., unclassified feedback).
  - Independently of the above, the most recent `regeneration_attempts`
    entry having `status == "failed"` adds its own `WARNING`.
  - Message vocabulary is deliberately restrained: never "feedback was
    satisfied," never "locked items will be preserved" (lock-aware
    partial regeneration is not implemented), never "regeneration will
    improve the trip."
- **`_build_regeneration_state_consistency_issues`**
  (`category="regeneration_state_consistency"`) cross-checks
  `regeneration_readiness.can_regenerate` against `plan_diff_preview.
  regeneration_available` (`WARNING` on any disagreement), and
  `metadata.current_version` against `version_history` (`WARNING` only
  when `version_history` is non-empty and no entry's `version_label`
  matches `current_version` -- an empty history with the default
  `current_version="v1"` is normal, not a mismatch).
- **Ordering caveat.** `PlanningOrchestrator.run_validation_stage` runs
  before `plan_diff_preview_service.recompute`/
  `regeneration_readiness_service.recompute` in `generate_full_plan`'s
  post-processing step (pre-existing order, unchanged by this step) --
  so on a trip's first `/generate` call, this check may read those
  fields' pre-plan defaults. This is documented in the class docstring
  rather than "fixed," since reordering `PlanningOrchestrator` is out of
  this step's strict boundaries; every check in this file already reads
  whatever a given field currently contains, never re-deriving it.
- **Small accommodation/flight refinement**: `_build_flight_inventory_warning`'s
  `affected_section` changed from `"stay_transport"` to
  `"flight_inventory"` (bookable flight search is distinct from
  `StayTransportDecision`'s transport-strategy concept); no test asserted
  the previous value. `_build_provider_coverage_consistency_warnings`'s
  flight-contradiction `affected_section` (Step 175B) was deliberately
  left as `"stay_transport"` -- the "keep existing 175B behavior intact"
  boundary applies to that function specifically. Scraped-data wording
  and success-with-zero-offers handling were already correct and
  untouched.
- **Tests.** `backend/app/tests/services/test_plan_validator_service.py`
  gained 15 new tests covering: pending feedback with
  `can_regenerate=True` producing a ready `SUGGESTION`; pending feedback
  with an active lock producing a lock-blocked `WARNING`; pending
  feedback with no derivable stage producing a `WARNING` quoting
  `blocked_by`; applied-only feedback never implying availability; no
  feedback at all producing no issue; a failed latest attempt surfaced
  honestly and coexisting with a separate pending-feedback issue;
  readiness/diff-preview mismatch and match cases; version-history/
  current-version mismatch, match, and empty-history cases; regeneration
  checks never producing a critical issue even when locks, a failed
  attempt, and both consistency mismatches all coexist; regeneration
  messages never containing "satisfied"/"improve"/"preserved"-family
  words; and the flight-inventory `affected_section` refinement. All
  pre-existing validator, provider-coverage-consistency (175B),
  route/movement/geometry (175C), accommodation/flight/routing, and
  Section 170-174 tests continue to pass unchanged.

## 84. Section 175 Complete: Validation Hardening Is Deterministic, Additive, and Never Overclaims (Step 175E, final Section 175 step)

Step 175E is frontend-only (`frontend/app/page.tsx` -- see
docs/16_frontend_architecture.md for the display-side detail) plus a
backend safety re-review that changed no backend code. This closes out
Section 175 (175A audit, 175B provider-coverage consistency, 175C route/
movement/geometry visibility, 175D regeneration lifecycle visibility,
175E frontend polish + final review).

- **Backend safety re-review, confirmed clean.** `plan_validator_service.py`
  was read end to end again and confirmed against five criteria: (1)
  every issue added by 175B/175C/175D is `WARNING`/`SUGGESTION` severity,
  never `CRITICAL`; (2) `readiness_status` is still computed solely from
  `critical_issues` (`BLOCKED` iff non-empty, `NEEDS_REVIEW` otherwise) --
  the two `critical_issues.append(...)` call sites are the same two that
  existed before 175B, untouched; (3) no message added by 175B/175C/175D
  claims a route order is superior, a fact has been confirmed beyond what
  was checked, a locked item's fate is assured, feedback's intent was
  fully met, or that the trip itself will be better as a result; (4) no
  function added by 175B/175C/175D
  computes a route geometry, distance, or duration -- `_route_geometry_
  leg_counts` only checks truthiness of `route_geometry`/`leg.
  route_geometry`, never reading `distance_meters`/`duration_seconds`;
  (5) every function added by 175B/175C/175D is a pure read returning
  `list[ValidationIssue]` -- none mutates `provider_coverage`,
  `regeneration_readiness`, `plan_diff_preview`, `version_history`,
  `feedback_history`, or any inventory/feasibility/sequencing/buffer
  report. No bug was found, so no backend file changed in this step.
- **Section 175's net effect on `PlanValidatorService`**: it went from
  emitting warnings/critical-issues only about scheduling, feasibility,
  constraints, must-visit, budget, weather, holidays, and travel-time
  buffers, to also honestly restating provider-coverage consistency
  (175B), route-aware sequencing/movement-data/route-geometry state
  (175C), and the regeneration lifecycle plus its own internal
  consistency (175D) -- all as additional `WARNING`/`SUGGESTION` issues
  layered onto the exact same `critical_issues`/`readiness_status`
  contract that existed before Section 175 started. No planning,
  provider, orchestration, route-aware sequencing, route geometry,
  accommodation/flight provider, or regeneration *behavior* changed
  anywhere in Section 175 -- only what the validator honestly reports
  about state those systems already produce.

## 85. Step 176B: No Backend Endpoint or Model Added

Step 176A's audit (frontend-only) found that every field a Section 176
trust dashboard needs is already returned by existing endpoints
(`GET /trips/{trip_id}`, `/validation-report`, `/provider-coverage`,
`/regeneration-readiness`, `/regeneration-attempts`, `/ai-candidate-review`)
-- the frontend's own `loadPlanResult` already fetches and assembles all
of them into one `PlanResult` object before any dashboard code runs. Step
176B accordingly added **no backend file, no new Pydantic model, and no
new route** -- it is a single new frontend module,
`frontend/lib/trust-dashboard.ts`, that only reads fields already
serialized by existing backend responses. `python -m compileall`/
`pytest` were re-run and are byte-for-byte unaffected (2104 tests
passing, unchanged from the end of Section 175). If a future Section 176
step finds a genuine need for a backend-computed value not already
expressible as a plain field/count on existing responses, that would be
proposed as a small, additive, read-only model mirroring
`ProviderCoverageService.summarize`'s pattern -- not before.

## 86. Step 176C: Trust Dashboard Rendered, Backend Still the Sole Source of Truth

Step 176C renders the Step 176B helper's output in the frontend
(`frontend/app/page.tsx` -- see docs/16_frontend_architecture.md for the
full detail). No backend file, model, route, or response shape changed;
no new endpoint was called. The new `TrustDashboardSection` is built from
`buildTrustDashboardModel(result)`, where `result` is the same
`PlanResult` object already assembled from the existing
`GET /trips/{trip_id}`, `/validation-report`, `/provider-coverage`,
`/regeneration-readiness`, `/regeneration-attempts`, and
`/ai-candidate-review` responses -- the backend remains the sole source
of every fact the dashboard displays; the frontend only groups, labels,
and re-renders values the backend already computed and already served
before this step existed. `python -m compileall`/`pytest` were re-run
and are byte-for-byte unaffected (2104 tests passing, unchanged from
175E/176B).

## 87. Step 176D: Dashboard Navigation/Copy Hardening, Backend Untouched

Step 176D adds ten HTML `id` attributes to existing frontend section
elements and reworks some dashboard wording (see
docs/16_frontend_architecture.md for the full detail) -- no backend
file, model, route, or response shape changed, and no new endpoint was
called. The `id` attributes and the reworded strings are purely a
client-side rendering/navigation concern; the backend continues to be
the sole source of every value the dashboard's copy restates (e.g. the
new "route data available only when this is a connected/successful
status" phrasing still reads the exact same `provider_coverage.routes`
string the backend already returns -- it explains that value more
clearly, it doesn't change or reinterpret it). `python -m compileall`/
`pytest` were re-run and are byte-for-byte unaffected (2104 tests
passing, unchanged since 175E/176B/176C).

## 88. Section 176 Complete: Trust Dashboard Is Frontend-Only, Backend Unchanged (Step 176E, final Section 176 step)

Step 176E closes Section 176 with a CSS/typography polish pass over the
frontend trust-dashboard cards (`frontend/app/page.tsx`) -- see
docs/16_frontend_architecture.md for the full detail, including one real
layout bug found and fixed (a long local file-path string overflowing
its card into a neighboring grid column) and one polish attempt that was
tried and reverted after live screenshots showed it broke word-wrapping.
None of this touches a backend file.

**Section 176's net effect on the backend, end to end (176A-176E): zero.**
176A's audit confirmed every field a trust dashboard needs already
existed on responses `frontend/app/page.tsx`'s `loadPlanResult` already
fetched; 176B-176E built the entire dashboard -- a pure data-shaping
helper, its rendering, ten new HTML anchors, copy hardening, and this
step's visual polish -- as pure frontend work reading those existing
responses. No backend model, route, or response shape was added or
changed anywhere in Section 176; `PlanningState`, `PlanValidatorService`,
`ProviderCoverageService`, and every provider/regeneration service
remain exactly as Section 175 left them. `python -m compileall`/`pytest`
were run one final time for this section and are byte-for-byte
unaffected (2104 tests passing, unchanged since 175E).

## 89. Hotel Ratings Provider Foundation, not_connected Default (Step 177B)

Step 177B adds a safe backend foundation for hotel ratings, following
177A's audit finding that three separate accommodation concepts already
exist (`DestinationContext.candidate_accommodation_pois` open-data
location candidates; the vestigial, never-constructed
`StayTransportDecision.accommodation_recommendations: list[AccommodationOption]`;
and the live `accommodation_inventory_report`/`AccommodationOffer` path)
and that no external hotel-rating provider (Google Places, Tripadvisor,
Amadeus Hotel Ratings, Yelp) can be safely wired in without first solving
conservative property-identity matching -- which this step deliberately
does not attempt.

`backend/app/models/hotel_ratings.py` adds `AccommodationRating` (`value:
float | None` bounded 0.0-5.0, `scale_max` fixed at this app's own 5.0
normalization convention -- not a provider-returned fact, `review_count:
int | None` >= 0, `provider`/`source_name`/`source_url`, `data_status`,
`retrieved_at`), plus a small request/response contract mirroring
`AccommodationSearchRequest`/`AccommodationSearchResult`'s own shape:
`HotelRatingsRequest` (conservative identity fields only -- `offer_id`
[a caller-assigned correlation id; `AccommodationOffer` has no such
field itself], `provider_property_id`, `provider`, `property_name`,
`address`, `latitude`/`longitude`, `source_name` -- no fuzzy-matching
input of any kind), `HotelRatingLookupItem` (`matched: bool = False`,
`rating: AccommodationRating | None`, with a model validator rejecting a
non-`None` rating whenever `matched=False` -- an unmatched lookup can
never carry a guessed value), and `HotelRatingsResult` (`status:
HotelRatingsStatus`, `items`, mirroring `AccommodationSearchResult`'s own
"offers/items only when status=success" validator).

`AccommodationOffer` (`backend/app/models/accommodation.py`) gains one
new optional field, `rating_details: AccommodationRating | None = None`.
This is purely additive: the pre-existing bare `rating: float | None`
field (populated only by `ScrapedAccommodationProvider`'s free-form HTML
parsing, Step 168B) is completely untouched -- no upper bound was added
to it, no renaming, no behavior change -- and `rating_details` is never
auto-copied from it or from anywhere else. Every existing
`AccommodationOffer` construction across the whole test suite (built
with no `rating_details` kwarg at all) continues to validate unchanged,
confirmed by `test_offer_without_rating_details_remains_valid`.

`backend/app/providers/hotel_ratings/` adds the provider boundary
itself, mirroring `app/providers/accommodation/`'s exact shape:
`base.py` (`HotelRatingsProvider(ABC)`, one abstract method
`get_ratings(requests: list[HotelRatingsRequest]) -> HotelRatingsResult`),
`not_connected.py` (`NotConnectedHotelRatingsProvider` -- always returns
`status=not_connected`, `items=[]`, and the message "No hotel ratings
provider is configured."), and `factory.py`
(`get_hotel_ratings_provider`, reading the new
`Settings.hotel_ratings_provider` field, default and only currently
supported value `"not_connected"` -- an unsupported/unrecognized name
falls back to `NotConnectedHotelRatingsProvider` rather than raising,
exactly like `get_accommodation_provider`/`get_flight_provider`). As of
this step, `_SUPPORTED_PROVIDERS` in this factory holds exactly one
entry -- no real Google Places/Tripadvisor/Amadeus/Yelp adapter exists to
select, and none is added by this step.

This whole subsystem is, deliberately, not wired into anything yet:
`ProviderGateway`, `PlanningOrchestrator`, the LangGraph planning nodes,
`AccommodationInventoryService`, and `ProviderCoverage` are all untouched
-- confirmed by dedicated tests asserting none of those modules'
source even mentions `hotel_ratings`. `ProviderCoverage.hotel_ratings`
is intentionally *not* added in this step (deferred to 177D, once a
real enrichment/report shape exists to justify a dedicated coverage
field -- see docs/13_llm_reasoning_pipeline.md and 177A's audit for the
reasoning). No API key, network call, LLM call, or live scrape was added
anywhere in this step; every new module was checked for disallowed
vendor/network imports the same way every other provider skeleton in
this codebase is.

## 90. Conservative Hotel-Rating Enrichment Path (Step 177C)

Step 177C adds `backend/app/services/hotel_rating_enrichment_service.py`
(`HotelRatingEnrichmentService.enrich`), the first thing in this codebase
that actually calls the Step 177B `HotelRatingsProvider` contract, and
wires it into `AccommodationInventoryService.build_report` immediately
after the base accommodation provider returns. **No real external ratings
adapter exists yet** -- the only concrete provider is still
`NotConnectedHotelRatingsProvider`, so with the default
`Settings.hotel_ratings_provider="not_connected"`, every existing
accommodation-inventory behavior (status, offer count, every offer field)
is unchanged; the only visible effect is that `AccommodationSearchResult`
gains `hotel_ratings_status=not_connected`/`hotel_ratings_provider=
"hotel_ratings_provider"`/`hotel_ratings_message="No hotel ratings
provider is configured."` metadata whenever there was at least one
`success` offer to attempt enrichment for (see below).

**Matching is conservative by construction, never fuzzy.** `enrich`
builds one `HotelRatingsRequest` per offer, carrying only fields the
offer already has (`provider_property_id`, `provider`, `property_name`,
`address`, `latitude`/`longitude`, `source_name`) plus a synthetic,
per-call-only `offer_id` (`"offer_<list index>"`) that exists purely to
correlate a response item back to the exact offer it was requested for --
this id is never read from or written onto `AccommodationOffer`, and
never persisted anywhere. A returned `HotelRatingLookupItem` is only ever
attached to an offer's `rating_details` when *all* of the following hold:
`matched=True` and `rating` is non-`None` (the model itself already
forbids the opposite combination); the item's `offer_id` exactly matches
one of this call's own request `offer_id`s (an unknown/stale id is
ignored); exactly one item references that `offer_id` (two or more is
ambiguous and *none* of them is attached -- never an arbitrary pick); and,
when both sides state a `provider_property_id`, they agree exactly (a
mismatch is ignored even if the `offer_id` matched). **Any failure of
these checks leaves that offer's `rating_details` at `None` -- "ambiguous
or unmatched" always means "leave it null," never "guess."** There is no
name/address text-similarity matching, no nearest-coordinate matching,
and no confidence-threshold matching anywhere in this function -- only
exact equality checks over fields the offer already carried.

`enrich` also never overwrites an offer's `rating_details` if it is
already non-`None` when enrichment runs (documented and tested via
`test_existing_rating_details_is_never_overwritten`) -- in the one call
path wired up by this step, `rating_details` is always `None` going in
(no earlier stage sets it), so this only matters for a future caller that
might run enrichment more than once over the same offers. It never
touches the pre-existing bare `AccommodationOffer.rating` field in any
way (neither reading it to seed a rating, per
`test_legacy_rating_is_never_copied_into_rating_details`, nor overwriting
it, per `test_legacy_rating_is_never_overwritten_by_enrichment`), never
mutates its input `AccommodationSearchResult`/`AccommodationOffer`
objects in place (uses `model_copy(update=...)` throughout, returning the
exact same objects for every offer that wasn't enriched), and is a
complete no-op (`return result` unchanged, no metadata stamped) whenever
the base result's `status != success` or it has no offers at all -- there
is nothing to enrich in that case, so nothing is attempted. A provider
exception is caught and logged, leaving the result unenriched, exactly
like `AccommodationInventoryService`'s own existing
`_safe_search_accommodations` exception handling.

`AccommodationSearchResult` gains four small, additive, backward-compatible
metadata fields for this: `hotel_ratings_status`, `hotel_ratings_provider`,
`hotel_ratings_message`, and `hotel_ratings_enriched_offer_count` (counts
only offers that actually received a `rating_details`, never a raw
"items returned" count). All default to `None`/`0`, so every
`AccommodationSearchResult` built anywhere before this step -- and every
result enrichment never touched -- stays fully valid.
**`ProviderCoverage.hotel_ratings` is still not added** (deferred to
177D, unchanged from 177A's/177B's stated reasoning).
`AccommodationInventoryService` gained one new, optional constructor
keyword (`hotel_rating_enrichment_service_override`) purely for test
injection -- no fake/test provider is registered in
`app.providers.hotel_ratings.factory._SUPPORTED_PROVIDERS`, and
`get_hotel_ratings_provider()` is still the only way production code
resolves a real provider.

## 91. Hotel-Ratings Provider Coverage and Validation Visibility (Step 177D)

Step 177D surfaces the Step 177C hotel-ratings enrichment outcome in two
more places -- `ProviderCoverage` and `PlanValidatorService` -- still with
**no real external ratings adapter wired in anywhere**; every behavior
below is driven entirely by `AccommodationSearchResult.hotel_ratings_*`
metadata that a test double already had to construct explicitly (or, in
production, that stays at its `None`/`not_connected` default).

**`ProviderCoverage.hotel_ratings: str | None = None`** (`app/models/
common.py`) is a new, purely additive field -- old, already-persisted
`ProviderCoverage` data built without it still validates, defaulting to
`None`. It is deliberately a third, separate axis from `hotel_prices`
(bookable price/availability inventory) and `accommodations` (OSM-backed
open-data location candidates, never bookable or rated) -- never
conflated with either. `_hotel_ratings_coverage_value` (duplicated,
intentionally, in both `planning_orchestrator.py` and
`planning_graph_nodes.py`, mirroring every other coverage-mapping helper
in this codebase for the same circular-import reason) maps
`hotel_ratings_status` onto this field: `None` stays `None` (enrichment
was never attempted -- never guessed as `not_connected`);
`not_connected`/`failed`/`unavailable` map straight across; a `success`
status is only ever reported as coverage `"success"` when *every*
offer that could be enriched actually was (`hotel_ratings_enriched_
offer_count == len(offers)`) -- zero enriched offers is reported
`"unavailable"` (never upgraded to imply a rating exists), and
some-but-not-all enriched is reported `"partial"`. Both
`PlanningOrchestrator._build_accommodation_inventory_report_safe` and
the LangGraph `accommodation_inventory_node` set this value right
alongside the pre-existing `hotel_prices` value, from the same already-
computed `accommodation_inventory_report` -- no new provider call, no
third inconsistent write path, and `hotel_prices`/`accommodations` are
completely untouched by this addition (confirmed by dedicated tests that
assert `hotel_prices` stays exactly what it was, and that the OSM-backed
`accommodations` value is unaffected by an unrelated hotel-ratings
outcome).

**`PlanValidatorService`** gains one additive, read-only check
(`category="hotel_ratings"`, `_build_hotel_ratings_issue`) that restates
`accommodation_inventory_report.hotel_ratings_*` honestly: no accommodation
report or no offers -> no issue at all (nothing to say); `hotel_ratings_
status is None` (enrichment never attempted) -> no issue either (the
pre-existing `accommodation_inventory` warning already covers the
lodging-inventory-itself case); `not_connected` -> a `SUGGESTION` naming
that no hotel ratings provider is configured; `failed` -> a `WARNING`;
`unavailable`, or `success` with zero enriched offers -> a `SUGGESTION`
saying no offer could be safely, exactly matched; `success` with some
(not all) offers enriched -> a `WARNING` naming both counts; `success`
with every offer enriched -> a low-severity `SUGGESTION` restating that.
Every branch is `WARNING`/`SUGGESTION` only, never `CRITICAL` -- hotel
ratings (connected or not) never affects `readiness_status`. No message
in any branch ever claims a rating's accuracy has been independently
checked or that a rating carries any kind of official endorsement, and
no message ever describes a missing rating as a signal about an offer's
quality -- each message
explicitly disclaims this where relevant, mirroring the same negated-
disclaimer pattern already used throughout this file (e.g. the existing
scraped-accommodation warning's "not been verified for price,
availability, rating, or booking-link accuracy").

This step changes no matching behavior from Step 177C: the exact-offer-id
requirement, duplicate-match-ignore rule, `provider_property_id`-mismatch
rule, no-overwrite rule, and no-legacy-rating-auto-copy rule are all
untouched -- `HotelRatingEnrichmentService` itself was not modified in
this step.

## 92. Section 177 Complete: Hotel Ratings Provider Foundation and Enrichment Layer, Not a Live Ratings Integration (Step 177E, final Section 177 step)

Step 177E closes Section 177 with a full safety re-review (no code
behavior change -- Section 177's model/provider/enrichment/coverage/
validation logic is exactly what 177B-177D left it) plus this final docs
pass. To be unambiguous for anyone reading this later: **everything built
across 177A-177E is a provider foundation and conservative enrichment
layer, not a live hotel-ratings integration.** No real Google Places,
Tripadvisor, Amadeus Hotel Ratings, Yelp, or any other external ratings
API is connected, called, or scraped anywhere in this codebase --
`Settings.hotel_ratings_provider` defaults to (and, absent an operator's
own future adapter, can only ever resolve to) `"not_connected"`, and the
only concrete `HotelRatingsProvider` implementation that exists is
`NotConnectedHotelRatingsProvider`.

Re-confirmed line by line for this step's safety review, every item
below verified by re-reading the actual current code (not assumed from
memory of 177B-177D):

- **Model**: `AccommodationOffer` still validates with no `rating_details`
  kwarg at all (fully backward compatible); the legacy `rating: float |
  None` field is untouched (no upper bound added, never auto-copied into
  `rating_details`); `AccommodationRating.value` is bounded `[0.0, 5.0]`;
  `review_count` is bounded `>= 0`; `rating_details` defaults to `None`
  and is never defaulted to a fabricated value anywhere in this codebase.
- **Provider**: `hotel_ratings_provider` defaults to `"not_connected"`;
  `get_hotel_ratings_provider` falls back to
  `NotConnectedHotelRatingsProvider` for any unrecognized name, never
  raising or guessing; `_SUPPORTED_PROVIDERS` holds exactly one entry;
  neither `base.py` nor `not_connected.py` imports any network/vendor
  library (confirmed by both the existing no-disallowed-imports tests and
  a fresh grep for `google_places`/`tripadvisor`/`amadeus hotel
  ratings`/`yelp`/`requests.get`/`httpx`/`aiohttp`/`fetch(` across
  `app/providers/hotel_ratings/` and
  `app/services/hotel_rating_enrichment_service.py` -- zero hits).
- **Enrichment**: `rating_details` attaches only on an exact `offer_id`
  correlation; a duplicate match for the same `offer_id` is ignored
  entirely (never an arbitrary pick); an unknown/stale `offer_id` is
  ignored; a `provider_property_id` mismatch (when both sides state one)
  is ignored; an offer that already carries `rating_details` is never
  overwritten; the legacy `rating` field is never read to seed
  `rating_details`; and `not_connected`/`unavailable`/`failed` provider
  results never populate any offer's `rating_details`.
- **Coverage**: `ProviderCoverage.hotel_ratings` is a field fully
  independent of `hotel_prices` (bookable inventory) and `accommodations`
  (OSM-backed open-data candidates); `hotel_prices` is byte-for-byte
  unaffected by this section's additions; both
  `PlanningOrchestrator`/the LangGraph `accommodation_inventory_node` set
  `hotel_ratings` from the same mapping rule; a `success`
  `hotel_ratings_status` with zero enriched offers is reported
  `"unavailable"`, never `"success"`.
- **Validation**: every `category="hotel_ratings"` issue is `WARNING`/
  `SUGGESTION`, never `CRITICAL` -- missing/failed/partial hotel ratings
  never block a trip or change `readiness_status`; no message infers
  hotel quality, claims a rating's accuracy was independently checked or
  officially endorsed, or says a missing rating means low quality.
- **Existing behavior preservation**: `AccommodationInventoryService`'s
  base search behavior is unchanged except for the additive enrichment
  step and its metadata fields; no routing/experience-planning/
  regeneration/feedback service was touched anywhere in Section 177; no
  new backend API route was added; `python -m compileall`/`pytest` pass
  with **2194 tests** (up from 2104 before Section 176, with every net-new
  test added across 176-177 accounted for and zero regressions).

Section 177 (177A-177E) is now feature-complete for its stated scope: a
safe, inert foundation ready for a future step to wire in one real,
conservatively-matched external provider -- which remains explicitly out
of scope for this section.

## 93. Kiwi MCP Client Foundation and Live Tool-Discovery Spike (Step 178B)

Unlike Section 177 (which stayed a permanent, `not_connected`-only
foundation with no real provider wired in), **Section 178's explicit goal
is to actually call Kiwi's real MCP server from backend runtime.** Step
178B is the first, safest slice of that: prove the connection and tool
discovery path works live, behind explicit two-flag opt-in, before any
flight-search mapping exists. See docs/12_provider_architecture.md
section 58 for the full provider-catalog writeup and the live discovery
result (the real server currently advertises a `search-flight` tool and
an unrelated `feedback-to-devs` tool); this section focuses on the
backend-architecture/layering details.

**New dependency**: `mcp==2.2.0` (`backend/requirements.txt`), the
official Model Context Protocol Python SDK, imported lazily inside
`KiwiMcpClient._default_client_factory` only -- mirroring the existing
`anthropic`/`groq` lazy-import convention exactly, so nothing else in the
app requires `mcp` to be installed. Adding this dependency required
bumping the pinned `idna` from `3.13` to `3.19` in the same file (a
transitive constraint from `mcp`'s own vendored `httpx2` dependency,
`idna>=3.18`) -- the only other line changed in that file.

**New modules**, both under `backend/app/providers/flights/`:
- `kiwi_mcp_client.py` -- `KiwiMcpClient`, `KiwiMcpDiscoveryStatus`,
  `McpToolInfo`, `KiwiMcpToolDiscoveryResult`. Scoped entirely to MCP
  tool discovery (`initialize`/`list_tools`); never calls a tool. Uses
  `mcp.Client(endpoint, read_timeout_seconds=...)` as an async context
  manager, bridged to this codebase's synchronous provider interface via
  `asyncio.run` inside the public `discover_tools()` method -- safe here
  because every caller (`KiwiMcpFlightProvider.search_flights`, called by
  `FlightInventoryService.build_report`, called by
  `PlanningOrchestrator.run_stay_transport_stage`/the LangGraph
  `flight_inventory_node`) runs synchronously with no already-running
  event loop in its thread: this app's FastAPI routes are plain `def`
  (Starlette dispatches them to a worker thread pool), and its LangGraph
  engine calls `graph.invoke()`, never `ainvoke()`. Accepts an injectable
  `client_factory` for tests, so no unit test needs network access or the
  real `mcp` package.
- `kiwi_mcp_adapter.py` -- `KiwiMcpFlightProvider(FlightInventoryProvider)`,
  registered in `get_flight_provider`'s factory as `"kiwi_mcp"`. Disabled
  by default at two independent layers: `Settings.flight_provider`
  defaults to `"scraped_local"` (selecting `"kiwi_mcp"` explicitly is
  required), and even then `Settings.kiwi_mcp_enabled` (default `False`)
  must also be explicitly `True` before any network call happens --
  identical two-flag-gating shape to
  `AI_CANDIDATE_PROPOSAL_PROVIDER=anthropic` +
  `AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED=true`. `search_flights`
  returns `not_connected` (disabled), `failed` (discovery itself failed),
  or `unavailable` (discovery succeeded, with or without a flight-search-
  looking tool found) -- **never `success`, and `offers` is always `[]`**;
  `FlightSearchResult`'s own `validate_offers_match_status` model
  validator would reject a `success` status with real offers anyway,
  structurally, so this isn't just adapter discipline. Every branch
  echoes the request's own `origin`/`destination`/dates/`adults`/
  `children`/`currency` back on the result, matching
  `NotConnectedFlightProvider`/`ScrapedLocalFlightProvider`'s existing
  convention exactly.

**Config** (`Settings`, all new, none read by any other provider):
`kiwi_mcp_enabled: bool = False`, `kiwi_mcp_endpoint: str =
"https://mcp.kiwi.com"`, `kiwi_mcp_timeout_seconds: float = 10.0` (`gt=0`,
validated), `kiwi_mcp_tool_name: str | None = None`.

**Factory**: `_SUPPORTED_PROVIDERS["kiwi_mcp"] = KiwiMcpFlightProvider`
added alongside the existing `"not_connected"`/`"scraped_local"` entries;
an unrecognized provider name still falls back to
`NotConnectedFlightProvider` exactly as before -- `"kiwi_mcp"` itself is
simply one more recognized, non-default name now.

**What 178B explicitly does not do**: no `FlightOffer`/`FlightSegment` is
ever constructed from MCP data (confirmed by a dedicated test scanning
`kiwi_mcp_adapter.py`'s own source for those constructor calls); no
airport is inferred from a city name; no booking URL is constructed; no
search tool is called; `provider_coverage.flights`/`PlanValidatorService`'s
flight-inventory wording are completely unchanged (both still see this
provider's `not_connected`/`failed`/`unavailable` results through the
exact same mapping/warning code as `scraped_local` always has); no
frontend file changed.

## 94. Real Kiwi MCP Search-Flight Invocation and Deterministic Parsing (Step 178C)

Step 178C wires the last piece 178B deliberately left unbuilt: with
`FLIGHT_PROVIDER=kiwi_mcp` and `KIWI_MCP_ENABLED=true`,
`KiwiMcpFlightProvider.search_flights` now performs discovery, then
builds real arguments, then calls Kiwi's real `search-flight` tool, then
parses the result -- and can return `status=success` with real,
provider-backed `FlightOffer`s for the first time in this codebase's
flight-provider history. **The default remains unchanged**:
`Settings.flight_provider` still defaults to `"scraped_local"`, and
`kiwi_mcp_enabled` still defaults to `False`. See
docs/12_provider_architecture.md section 59 for the full field-mapping/
schema writeup (including the real payload shape captured live during
this step); this section covers layering.

**New**: `backend/app/providers/flights/kiwi_mcp_parser.py` --
`parse_kiwi_mcp_search_result(call_result, request) -> FlightSearchResult`,
the sole constructor of Kiwi-sourced `FlightOffer`/`FlightSegment`
objects in this codebase. **Extended**: `KiwiMcpClient` gains `call_tool`
(transport-only, mirrors `discover_tools`'s synchronous-bridging/error-
handling contract exactly, returns the new `KiwiMcpToolCallResult`);
`KiwiMcpFlightProvider.search_flights` gains the full post-discovery flow
(`build_search_flight_arguments` -> `client.call_tool(flight_tool.name,
arguments)` -> `parse_kiwi_mcp_search_result`), and the new module-level
`build_search_flight_arguments(request, tool_schema)` function.

**Provider-name labeling mirrors `ScrapedLocalFlightProvider`'s own
existing asymmetry exactly**: every pre-call gate (disabled, discovery
failed, no flight tool found, arguments couldn't be safely built) still
uses `KiwiMcpFlightProvider.provider_name`
(`"kiwi_mcp_flight_provider"`, unchanged since 178B) on its
`_empty_result`; once the real `search-flight` call is actually attempted,
every outcome from that attempt onward (call failure, malformed payload,
zero offers, real success) is labeled `provider="kiwi_mcp"` by the parser
itself -- the same "adapter identity before a real attempt, specific
data-source label once one is made" pattern `parse_scraped_flight_html`
already established for the scraped provider.

**Never wired anywhere else this step**: `ProviderGateway`,
`PlanningOrchestrator`, the LangGraph flight node, `provider_coverage.
flights`'s mapping, and `PlanValidatorService`'s flight-inventory warning
are all completely untouched -- a real Kiwi success result flows through
the exact same `_flight_coverage_value`/`_build_flight_inventory_warning`
code every other flight provider's result already does (a `success`
status with real offers reports `provider_coverage.flights = "success"`,
and the validator's existing "provider-backed flight offer(s) were found"
wording already applies honestly, unchanged). No frontend file changed;
distinguishing a Kiwi-sourced offer from a scraped one in the UI/trust
dashboard is Step 178D's job.

Confirmed live during this step (never assumed): a real call to
`search-flight` with `{flyFrom: "London", flyTo: "Paris", departureDate:
"15/12/2026"}` returned 15 real itineraries with real prices (EUR),
real Kiwi booking links, real carrier/flight-number/schedule data, and
round-trip searches correctly populate `return_segments`; a nonsense
origin/destination pair returned `resultsCount: 0`/`itineraries: []`
(parsed as `unavailable`, not an error); and a malformed `departureDate`
value returned a populated `error` field with a real pydantic validation
message (parsed as `failed`, its detail truncated to one line before
being relayed).

## 95. Kiwi MCP-Specific Validation Wording (Step 178D)

Step 178D adds one new message template to
`_build_flight_inventory_warning` (`plan_validator_service.py`):
`_FLIGHT_KIWI_MCP_SUCCESS_MESSAGE_TEMPLATE`, selected when
`any(offer.provider == "kiwi_mcp" for offer in offers)` and no offer
carries `scraped_provenance` -- a new module-level constant,
`_KIWI_MCP_OFFER_PROVIDER_NAME = "kiwi_mcp"`, documents that this is a
plain string match against `FlightOffer.provider` (a public field), not
an imported cross-module constant, mirroring how this file already
detects a scraped offer via `offer.scraped_provenance` without importing
anything from `app.providers.flights.scraped_parser`. The three branches
(`is_scraped` / `is_kiwi_mcp` / generic) are mutually exclusive in
practice, since `scraped:<source_id>` and `kiwi_mcp` are disjoint
provider-name namespaces. Severity/blocking behavior is completely
unchanged: still always `WARNING`, never `CRITICAL`, regardless of
source. `provider_coverage.flights` required no code change at all --
see docs/12_provider_architecture.md section 60 for why, and for the
full frontend/trust-dashboard writeup.

Also confirmed and exercised live during this step, end to end through
the real HTTP API (`POST /trips`, `POST /trips/{id}/generate`): a
successful Kiwi search on a real trip produces
`provider_coverage.flights == "success"` and the new validation message
verbatim in `GET`-able `planning_state.validation_report.warnings`;
a search with a less Kiwi-friendly destination string (`"Paris, France"`)
produced a genuine `status=unavailable` with the pre-existing generic
unavailable wording, proving the new branch only fires for a real,
non-empty Kiwi success and never overrides the honest not-found case.

## 96. Section 178 Complete: Final Backend Safety Review (Step 178E, final Section 178 step)

Step 178E is a documentation/safety-review/small-copy-cleanup step only
-- no backend behavior changed. Every safety property claimed across
178B-178D was re-verified this step by re-reading the current source and
re-running both manual smoke scripts live:

- `Settings.flight_provider` still defaults to `"scraped_local"`;
  `Settings.kiwi_mcp_enabled` still defaults to `False`; both flags are
  independently required before any network call happens.
- `backend/requirements.txt`'s `mcp==2.2.0` pin and its accompanying
  `idna` bump are unchanged and still the only two lines touched in that
  file across all of Section 178; `python -m pip check` reports no
  broken requirements.
- `import app.main` does not import the `mcp` package -- confirmed fresh
  this step -- so a normal `uvicorn` startup never attempts, and never
  requires, a live MCP connection.
- `KiwiMcpClient.discover_tools`/`call_tool`, `build_search_flight_arguments`,
  and `parse_kiwi_mcp_search_result` are all unchanged from 178B/178C;
  every safety property documented for them in sections 58-60 above still
  holds, re-confirmed by a full, unmodified `pytest` run (2296 tests
  passing) and by both manual smoke scripts run live one final time
  (`manual_kiwi_mcp_tool_discovery_smoke.py` still finds exactly
  `search-flight`/`feedback-to-devs`; `manual_kiwi_mcp_flight_search_smoke.py`
  still returns real, priced, booking-linked offers).
- The only code changed this step is a small number of comment/docstring
  rewordings in `plan_validator_service.py` (removing a list of literal
  overclaim words from two comments in favor of describing the same
  safety property without naming them) -- no message string a user or the
  API actually sees was weakened; the real
  `_FLIGHT_KIWI_MCP_SUCCESS_MESSAGE_TEMPLATE` runtime text was already
  safe and is unchanged.

Section 178 (178A audit, 178B tool-discovery foundation, 178C real
search-flight invocation, 178D source labeling, 178E this final review)
is now complete: TravelObligator's first live, real, third-party flight
data integration, safely gated off by default and requiring explicit
operator opt-in, with zero fabricated flight data anywhere in the
pipeline.

## 97. US Destination Normalization and Open-Meteo Retry-Once (Step 182B)

Following Section 182A's real Jersey City -> Orlando frontend test, this
step touched three backend files plus one new shared utility -- no
`DestinationContextService` line changed, since the fix belongs entirely
inside the two provider adapters it already calls:

- **New**: `backend/app/utils/destination_inference.py` -- a pure,
  provider-agnostic US state/territory name-and-abbreviation ->
  `"US"` table plus `infer_us_country_code_from_state_segment`, gated so
  it only ever fires for a "City, State" (multi-segment) destination,
  never a single bare word.
- **`backend/app/providers/holidays/nager_date_adapter.py`**:
  `infer_country_code` now falls back to that shared table after its
  existing full-country-name table misses.
- **`backend/app/providers/currency/frankfurter_adapter.py`**:
  `infer_destination_currency` does the same, mapping a US-state match to
  `"USD"`.
- **`backend/app/providers/weather/open_meteo_adapter.py`**:
  `get_weather_forecast` now retries its live HTTP call once
  (`_MAX_ATTEMPTS = 2`) before reporting `failed`, with the retry backoff
  resolved as a module attribute (`time.sleep(...)`) specifically so it
  stays mockable/deterministic in tests.

See `docs/12_provider_architecture.md` section 62 for the full rationale,
the exact ambiguity this design deliberately avoids (a bare `"Georgia"`
must never resolve to the US state -- only `"Atlanta, Georgia"`/
`"Orlando, FL"` count as a real "City, State" signal), and confirmation
that `provider_coverage`/`validation_report`/the trust dashboard all pick
up the corrected result through the exact same
`ProviderCoverageService.record_provider_result` path that already
existed -- no new plumbing was added. `.env.example` also gained
expanded, explicit comments on activating the public, no-API-key OSRM
demo server (`ROUTING_PROVIDER=osrm` + `OSRM_BASE_URL=https://router.project-osrm.org`)
for local routing demos; the safe `ROUTING_PROVIDER=not_connected` default
itself is unchanged. No API key, paid/partner provider, or scraping of
any kind was added in this step.

## 98. Stay-Area Guidance Anchor Cap Raised 3 -> 5 (Step 182D)

The only backend change in Step 182D (a frontend-first Traveler-view
layout cleanup -- see `docs/16_frontend_architecture.md` "Section 182D"
for the full writeup): `_MAX_STAY_GUIDANCE_ANCHORS` in
`backend/app/services/experience_planner_service.py` changed from `3` to
`5`. This is the only place that constant is used --
`_build_stay_area_guidance`'s existing average-straight-line-distance
ranking, quality-tier exclusion, and honest-empty-on-no-candidates
behavior are all otherwise unchanged; only how many of the
already-ranked candidates get sliced off the top changed. Driven by the
frontend's new trip-level "Where to stay" section needing enough real
stay-area candidates to show 4-5 cards when no bookable inventory
provider is connected (the common default-config case).

Two new focused tests in
`backend/app/tests/services/test_experience_planner_service.py` cover
this directly: `test_stay_area_guidance_returns_at_most_five_anchors`
(7 coordinate-backed candidates in, exactly the 5 closest come out, the 2
farthest are excluded, and no suggestion ever carries a `price`/`rating`/
`booking_url` attribute) and
`test_stay_area_guidance_returns_fewer_than_five_when_fewer_candidates_exist`
(2 candidates in, 2 out -- the cap never pads a short list). Three
existing integration tests in `backend/app/tests/api/test_trips_smoke.py`
that had pinned the old cap of 3 (`test_stay_area_guidance_selects_lowest_average_distance_accommodation_pois`,
`test_stay_area_guidance_uses_only_candidate_accommodation_pois`,
`test_stay_area_guidance_creates_no_fake_fields`) were updated in place:
their fixture provider now returns 6 accommodation candidates (2 more
than before) instead of 4, so the cap-enforcement/exclusion behavior
those tests exist to check is still meaningfully exercised at the new
cap of 5, rather than the fix accidentally making every fixture
candidate always fit and the exclusion path go untested. Full suite: 2313
passed (up from 2311 before this step).

## 99. Provider Activation Config Surface: Partner Placeholders, Manual-HTML Aliases, and Source Labels (Step 182E)

Backend-config-focused companion to `docs/12_provider_architecture.md`
section 63, which has the full product-facing rationale.

`backend/app/core/config.py` gained, in order: (1) 18 new `str | None =
None` credential/base-URL fields for 8 partner-only providers (Booking
Demand API, Expedia Rapid API, Hotelbeds, Hostelworld, Vrbo, Airbnb,
Skyscanner, Tripadvisor) -- declared, tested, and referenced by exactly
zero factories or adapters, identical in spirit to the pre-existing
`google_places_api_key`/`amadeus_client_id` fields; (2)
`accommodation_manual_html_source`/`flight_manual_html_source` (default
`"generic"` for both), each backed by a `@field_validator(mode="after")`
that clamps any value outside its small allowed frozenset
(`_ALLOWED_ACCOMMODATION_MANUAL_HTML_SOURCES`/
`_ALLOWED_FLIGHT_MANUAL_HTML_SOURCES`, both module-level constants right
above the `Settings` class) back to `"generic"` -- so an invalid env
value can never reach an adapter, matching the existing
`accommodation_provider`/`flight_provider`/`hotel_ratings_provider`
"unsupported input -> safe default" convention exactly, just enforced at
the config layer instead of a factory's lookup-with-fallback.

`backend/app/providers/accommodation/factory.py` and
`backend/app/providers/flights/factory.py` each gained one line:
`"manual_html": ScrapedAccommodationProvider`/`ScrapedLocalFlightProvider`
in their `_SUPPORTED_PROVIDERS` dict, alongside the pre-existing
`"scraped_local"` entry (never removed) pointing at the identical class.
`get_accommodation_provider("manual_html")`/`get_flight_provider(
"manual_html")` return the exact same adapter instance type as
`"scraped_local"` -- confirmed by
`test_manual_html_alias_does_not_remove_scraped_local` in both
`test_accommodation_factory.py`/`test_flight_factory.py`, which asserts
`type(...)` equality across both provider-name strings.

`backend/app/providers/accommodation/scraped_adapter.py` and
`backend/app/providers/flights/scraped_adapter.py` each gained a private
`_MANUAL_HTML_SOURCE_DISPLAY_NAMES` dict and an `_effective_source_identity(settings)`
helper, called once near the top of `search_accommodations`/
`search_flights` (right before the existing `query_hash`/
`source_policy` construction, both of which now use its return value
instead of reading `settings.scraped_*_source_id`/`_source_name`
directly). The helper reads the two built-in defaults via
`Settings.model_fields["scraped_accommodation_source_id"].default`-style
introspection (not a hardcoded duplicate string) specifically so it stays
correct if those defaults ever change, and only overrides the id/name
when both are still exactly at those defaults -- an operator's own custom
naming is never touched. Every new/changed line was covered by tests
before being considered done: 4 new tests in
`test_scraped_accommodation_adapter.py` (relabel, generic-no-op,
respects-a-customization, cache-key-changes-with-label) and 4 mirrored
ones in `test_scraped_flight_cache.py` (the same four, plus
`test_manual_html_source_label_kiwi_is_distinct_from_kiwi_mcp` proving
the flight `"kiwi"` label never produces a `provider="kiwi_mcp"` offer).

No provider adapter, `ProviderGateway` method, orchestrator stage, or
Pydantic model shape changed in this step -- every change is either a new
declared-but-unused config field, a factory dict alias, or provenance
display text computed from fields (`source_id`/`source_name`) that
already existed.

## 100. Itinerary Narrator: New Model, Provider Boundary, Service, and Orchestrator Wiring (Step 182F)

Backend-architecture-focused companion to
`docs/13_llm_reasoning_pipeline.md` section 120, which has the full
product/safety rationale. This is the largest single-step addition to
the provider/service layer since Section 178 (Kiwi MCP): one new model
module, one new provider package (mirroring
`app.providers.ai_candidate_proposal`'s own four-file shape), one new
request-builder service, one new orchestration service, and small,
additive wiring into `PlanningOrchestrator` and the regenerate route --
nothing existing was restructured.

**New model module**: `backend/app/models/itinerary_narrative.py` --
`ItineraryNarrativeStatus` (`success`/`not_connected`/`unavailable`/
`failed`, matching `AccommodationSearchStatus`/`FlightSearchStatus`/
`HotelRatingsStatus`'s own four-value convention exactly),
`ItineraryNarrativeExperienceInput`/`ItineraryNarrativeDayInput`/
`ItineraryNarrativeRequest` (the strict input allow-list), and
`ItineraryNarrativeDayOutput`/`ItineraryNarrativeReport` (the output).
Added to `PlanningState` as one new optional field,
`itinerary_narrative_report: ItineraryNarrativeReport | None = None`,
placed alongside `provider_status`/`provider_coverage`/`unavailable_data`/
`data_sources_used` right before `metadata` -- last in the field order,
matching it being the last thing computed. A backward-compatibility test
(`test_planning_state_without_step_182f_narrative_report_defaults_to_none`)
confirms `PlanningState.model_validate(...)` on a record with no
`itinerary_narrative_report` key at all still loads cleanly, honestly
`None` -- exactly what `PlanningStateRepository`'s own
`model_validate(record)` load path needs for every trip persisted before
this step.

**New provider package**: `backend/app/providers/itinerary_narrator/`
(`base.py`/`not_connected_adapter.py`/`groq_adapter.py`/
`anthropic_adapter.py`/`factory.py`), structurally identical in shape to
`app.providers.ai_candidate_proposal` but registered under its own
`ItineraryNarratorProvider` `ABC` -- never mixed into
`AICandidateProposalProvider`. `get_itinerary_narrator_provider`
(`factory.py`) resolves `"not_connected"`/`"anthropic"`/`"groq"` from
`Settings.itinerary_narrator_provider`, falling back to
`NotConnectedItineraryNarratorProvider` for anything else, exactly like
every other provider factory in this codebase.

**New services**:
`backend/app/services/itinerary_narrative_request_builder.py`
(`ItineraryNarrativeRequestBuilder`, a pure read over `PlanningState`,
see docs/13 section 120 for its full allow-list) and
`backend/app/services/itinerary_narrative_service.py`
(`ItineraryNarrativeService`, the only writer of
`PlanningState.itinerary_narrative_report`). Both follow this
codebase's established singleton-plus-constructor-injection pattern
(`itinerary_narrative_request_builder`/`itinerary_narrative_service`
module-level instances; tests inject a fake request builder/provider via
the constructor, never monkeypatching a real network client).

**Orchestrator wiring**: `PlanningOrchestrator.__init__` gained one new
constructor parameter/attribute,
`itinerary_narrative_service: ItineraryNarrativeService | None = None`,
defaulting to a fresh `ItineraryNarrativeService()` like every other
stage service already does. Both `generate_full_plan` and
`generate_full_plan_via_langgraph` call
`self.itinerary_narrative_service.generate(...)` as the literal last line
of their existing "post_processing" block, after
`regeneration_readiness_service.recompute` and before
`_mark_stage_finished(..., "post_processing")` -- so both engines attach
the exact same narrator behavior in the exact same relative position,
matching how every other post-172E dual-engine feature in this codebase
stays engine-parity by construction rather than by a shared helper
function.

**Regeneration wiring**: `POST /trips/{trip_id}/regenerate`
(`backend/app/api/routes/trips.py`) imports the module-level
`itinerary_narrative_service` singleton directly (matching how
`plan_diff_preview_service`/`regeneration_readiness_service` are already
imported there) and calls `.generate(planning_state)` right after
`rerun_affected_stages` succeeds, before `changed_sections` is finalized
-- so a successful refresh's `"itinerary_narrative"` entry is included in
both the `VersionHistoryItem.changed_sections` this regeneration records
and the `RegenerateResponseData.changed_sections` the caller sees, not
just one or the other.

**Test coverage**: 63 new backend tests across one new model
backward-compatibility test, one new config test file, three new
provider test files (factory + both adapters), two new service test
files (request builder + service), and one new API test file
(`test_itinerary_narrative_generation.py`, 7 end-to-end tests covering
disabled/enabled-success/enabled-failure for both generation and
regeneration, using the real FastAPI `TestClient` and the real
orchestrator/route code paths with only the provider faked). Full suite:
2436 passed (up from 2373 before this step). No API key was added. No
paid/partner provider adapter was added -- Groq/Anthropic were already
wired providers from Sections 161-162, reused here through a completely
separate provider interface and config surface. No provider adapter,
`ProviderGateway` method, or existing service's behavior changed.

## 101. Section 182 Final Real-World Demo Verification (Step 182G)

One real trip (Jersey City, NJ -> Orlando, FL, 2026-11-21 to
2026-11-25, 2 travelers, interests parks/museums/beaches, must-visit
Universal Studios, constraints no-early-mornings/wheelchair-accessible)
was generated end to end with every no-key/optional provider this
section's stack can activate turned on at once, in a real local `.env`
(never committed, restored/removed after use): `ROUTING_PROVIDER=osrm`
+ the public OSRM demo server (182B/165A), `FLIGHT_PROVIDER=kiwi_mcp` +
`KIWI_MCP_ENABLED=true` (178B-178E), `ACCOMMODATION_PROVIDER=manual_html`
against a temporary, gitignored local fixture (182E), and
`ITINERARY_NARRATOR_ENABLED=true` + `ITINERARY_NARRATOR_PROVIDER=groq`
with a real `GROQ_API_KEY` (182F). Every one of those real integrations
responded honestly and as designed: OSRM returned real route
feasibility (`routes: success`), Kiwi MCP made a real network call and
honestly reported `unavailable` with the message "Kiwi MCP returned no
flight offers for this search" (a real zero-result search, not an
error, not a fabricated offer), the manual/local accommodation fixture
produced two real source-labeled offers, and the narrator both failed
and later succeeded against the real Groq API across the same session
(see `docs/13_llm_reasoning_pipeline.md` section 121 for the full
narrator-specific account). `provider_coverage.weather` reported
`failed` for this run (a real, transient Open-Meteo issue for a
several-months-out date range, not a regression -- 182B's retry-once
logic already ran and still failed honestly rather than masking it).
The full automated suite (2436 tests) was re-confirmed to run
completely hermetically with no `.env` file present (13.4s, zero
network calls) -- the real `.env` used for this manual demo was moved
aside for that run and restored immediately after, precisely so this
step's "real provider" testing never contaminates the suite every other
contributor (and CI) runs without any such file.

One small, real frontend bug was found and fixed during this live
run -- see `docs/16_frontend_architecture.md`'s 182G note for
`UserModeFlightSummary`'s corrected not-found-vs-not-connected wording;
no backend field, model, or provider adapter needed to change for it,
since the underlying `FlightSearchStatus` values were already correct
and honest -- only the frontend's message selection was imprecise.