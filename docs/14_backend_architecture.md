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