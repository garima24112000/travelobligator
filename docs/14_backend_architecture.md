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

## 102. PostgreSQL Dependency and Connection Foundation, Not Wired In (Step 183B)

A Step 183A read-only audit first confirmed the actual persistence call
graph: every stage service (`VersioningService`,
`RegenerationAttemptService`, `RegenerationReadinessService`,
`ItineraryNarrativeService`, `FeedbackService`, `UserLockService`, etc.)
is a pure in-memory `PlanningState -> PlanningState` transformer with no
repository calls of its own -- only `app/api/routes/trips.py` and
`PlanningOrchestrator` ever call `planning_state_repository.save(...)`.
That means a future storage-backend swap only has to change two
repository classes (`TripRepository`, `PlanningStateRepository`), not any
service code, as long as their public interface is preserved.

Step 183B adds the dependency and connection *foundation* for that future
swap. As of this step, persistence behavior is **completely unchanged**:
`LocalJsonStore` (section 19-21 above) remains the only storage actually
read/written by any route, service, or repository, regardless of any new
config below.

- **Dependencies**: `SQLAlchemy==2.0.52` and `alembic==1.19.2` added to
  `backend/requirements.txt`. `psycopg[binary]==3.2.3` (already a
  dependency, previously unused by application code) is the driver --
  `asyncpg` was deliberately not added, and this step introduces no async
  SQLAlchemy usage.
- **Config gate**: `Settings.persistence_backend` (`PERSISTENCE_BACKEND`
  env var, default `"local_json"`) follows the same
  `@field_validator(mode="after")` clamp-to-safe-default convention as
  `accommodation_manual_html_source`/every provider-selection field --
  an unrecognized value normalizes to `"local_json"` rather than raising.
  `"postgres"` is accepted as an opt-in value but today changes nothing:
  no route/service/repository branches on it. Setting `DATABASE_URL`
  alone never switches persistence -- only an explicit
  `PERSISTENCE_BACKEND=postgres` does, and even that remains inert until
  a Postgres-backed repository exists (future work, Step 183D).
- **`backend/app/db/` package** (new, not imported by any route/service/
  repository yet):
  - `session.py` -- a sync SQLAlchemy engine/session foundation.
    `build_engine()`/`get_engine()`/`get_session_factory()` are all lazy:
    no engine or session is constructed at import time, and
    `create_engine(...)` itself never opens a connection eagerly (only on
    first actual use). `normalize_database_url()` rewrites a bare
    `postgres://`/`postgresql://` URL to `postgresql+psycopg://` via
    plain string-prefix matching (not `urllib.parse`, which was found to
    corrupt unrelated schemes like `sqlite:///:memory:` on round-trip);
    any URL that already names a driver, or uses a non-Postgres scheme,
    passes through unchanged.
  - `base.py` -- an empty `DeclarativeBase` subclass (`Base`), with no
    ORM models registered against it yet. Exists only so
    `alembic/env.py`'s `target_metadata` has something real to import;
    schema authoring is Step 183C's job.
- **Alembic scaffold**: `backend/alembic.ini` + `backend/alembic/`
  (`env.py`, `script.py.mako`, `versions/`) were scaffolded via
  `alembic init`, since doing so requires no live database and adds no
  production schema migration -- `versions/` is empty. `env.py` imports
  `app.core.config.get_settings` and `app.db.session.normalize_database_url`
  to set `sqlalchemy.url` at the moment an `alembic` command actually
  runs; importing `env.py` itself never connects, only invoking a real
  `alembic` command (e.g. `alembic current`) does, which was manually
  verified to fail with a clear connection error against the
  non-resolvable `postgres` host in `.env.example`'s default
  `DATABASE_URL` -- exactly the expected behavior with no real Postgres
  running. Nothing under `app/` imports `alembic`.
- **Provider cache docstring fix**: `provider_cache_store.py`'s module
  docstring incorrectly claimed it was "not imported by any provider
  adapter" -- corrected to name the eight real adapters that use it via
  `get_provider_cache_store`. Docstring-only change; no cache behavior,
  schema, or migration touched. The provider cache remains a separate
  SQLite store, independent of both `LocalJsonStore` and this new `app/db/`
  foundation -- migrating it to Postgres, if ever done, is optional future
  work (Step 183E).
- **Tests**: `backend/app/tests/core/test_persistence_backend_config.py`
  and `backend/app/tests/db/test_session.py` -- default/opt-in/invalid-value
  config behavior, `DATABASE_URL` alone not changing
  `persistence_backend`, URL normalization, engine/session construction,
  and import-time non-connection (verified by pointing `DATABASE_URL` at
  an unreachable host/port before a fresh import and confirming it still
  succeeds instantly). None require a running Postgres. The full suite
  (2453 tests, up from 2436) was re-confirmed to pass with no real `.env`
  present, matching Step 182G's hermeticity baseline.

Planned follow-on work (not part of this step): 183C (Postgres schema +
first Alembic migration, run against a real local Postgres for
verification only), 183D (repository factory pattern + Postgres-backed
repository implementations passing the existing
`test_persistence.py` contract), 183E (optional provider-cache Postgres
parity), 183F (docs finalization, docker-compose health-check hardening,
and the first point at which this Section 183 stack is committed).

## 103. `pytest` Hermetic Test Settings, Independent of a Developer's Local `.env` (Step 183B-FIX)

A full `pytest` run with a real local `.env` present (live
`FLIGHT_PROVIDER=kiwi_mcp`, `KIWI_MCP_ENABLED=true`,
`ROUTING_PROVIDER=osrm`, `OSRM_BASE_URL`, `ITINERARY_NARRATOR_ENABLED=true`,
`ITINERARY_NARRATOR_PROVIDER=groq`, a real `GROQ_API_KEY`) surfaced 32
failures. Root cause: `app/providers/gateway.py`'s module-level
`provider_gateway` singleton resolves its routing/flight providers via
`get_settings()` **at import time** -- the moment `conftest.py`'s own
`from app.providers.gateway import provider_gateway` line runs, at
collection time, before any per-test fixture exists to intervene. With a
live `.env` present, that first `get_settings()` call baked a real
`KiwiMcpFlightProvider`/`OSRMRoutingAdapter` into the singleton for the
entire test session -- no later `monkeypatch.setenv(...)` or
`get_settings.cache_clear()` could undo it, since the wrong adapter
instance was already constructed and stored. A second, narrower instance
of the same root cause: several factory tests constructed
`Settings(field="value")` directly (bypassing `get_settings()` and
`_env_file=None`) to test fallback/opt-out behavior -- pydantic-settings'
dotenv source, keyed by alias, silently outranked the by-field-name init
kwarg for those specific fields, so the real `.env`'s value won anyway
(the same by-name-vs-alias precedence quirk documented in Step 163B's
`_isolate_ai_candidate_proposal_env` fixture, here affecting fields that
fixture never covered).

Fix, in two parts:
- **`get_settings()` test-mode branch** (`app/core/config.py`): when the
  `TRAVELOB_TEST_MODE` environment variable is `"1"`, `get_settings()`
  constructs `Settings(_env_file=None)` instead of `Settings()` --
  skipping the dotenv *file* source entirely, while real OS environment
  variables (exactly what `monkeypatch.setenv(...)` sets) still apply, so
  a test can still explicitly opt into a value. `backend/app/tests/
  conftest.py` sets `os.environ.setdefault("TRAVELOB_TEST_MODE", "1")` as
  literally its first line, before any other import -- including its own
  `from app.core.config import ...` -- so this is already true by the
  time `provider_gateway` (or anything else) is first imported anywhere
  in the test session. Normal app/dev-server runtime never sets this
  variable, so `.env` is read exactly as before for real usage; nothing
  about production default behavior changed.
- **Seven affected factory tests** (`test_accommodation_factory.py`,
  `test_flight_factory.py`, `test_itinerary_narrator_factory.py`,
  `test_routing_factory.py`) added `_env_file=None` to the specific
  `Settings(field="value")` calls that were bypassing `get_settings()`
  entirely -- test-only hygiene fixes, matching the pattern already used
  correctly by sibling tests in the same files. No production code path
  changed for this half of the fix.

New regression coverage:
`backend/app/tests/core/test_pytest_hermetic_env_isolation.py` --
confirms `TRAVELOB_TEST_MODE` is set during pytest; reproduces the
original bug directly (a throwaway `.env` under `tmp_path` with live
kiwi_mcp/osrm/narrator settings, proven not to leak into
`get_settings()`); a control test proving raw `Settings(_env_file=<path>)`
still honors a real dotenv file (real app runtime is unaffected); a test
proving `monkeypatch.setenv(...)` opt-in still works after
`get_settings.cache_clear()`; a test asserting the real, module-level
`provider_gateway` singleton's defaults are safe (`ScrapedLocalFlightProvider`/
`NotConnectedRoutingProvider`, never `KiwiMcpFlightProvider`/
`OSRMRoutingAdapter`); and a test constructing `ItineraryNarrativeService()`
with its real default (no injected fake) to prove the disabled
short-circuit is reached without ever touching a real provider.

The full suite (2459 tests, up from 2453) was re-run **with the
developer's real local `.env` left in place, untouched** (not moved
aside, unlike the one-off verification technique used in Steps 182G and
183B) -- 2459 passed in ~14s with zero network calls. This is now the
durable, default guarantee rather than a manual step to repeat: any
contributor's real `.env`, whatever it contains, no longer affects
`pytest` results.

## 104. Initial Postgres Schema Migration, Still Not Wired to Any Repository (Step 183C)

The first real Alembic migration --
`backend/alembic/versions/2fe86f95d81e_create_trips_and_planning_states.py`,
`down_revision=None` (the root revision) -- creates exactly two tables for
the opt-in Postgres persistence backend
(`Settings.persistence_backend`, still `"local_json"` by default; see
section 102). This is schema-only: **no route, service, or repository
reads or writes either table** -- applying this migration to a real
Postgres database has zero effect on current app behavior. That wiring is
Step 183D's job.

- **`trips`**: `trip_id TEXT PRIMARY KEY`, `status TEXT NOT NULL DEFAULT
  'draft'`, `created_at`/`updated_at TIMESTAMPTZ NOT NULL`, plus indexes
  on `status` and `updated_at`. Mirrors
  `app.repositories.trip_repository.TripRecord` exactly.
- **`planning_states`**: `trip_id TEXT PRIMARY KEY REFERENCES
  trips(trip_id) ON DELETE CASCADE`, `planning_state_id`/`current_version`/
  `pipeline_status TEXT NOT NULL`, `state JSONB NOT NULL`,
  `created_at`/`updated_at TIMESTAMPTZ NOT NULL`, plus indexes on
  `current_version` and `pipeline_status`. Mirrors
  `PlanningStateRepository` exactly: the entire `PlanningState` is stored
  as one JSONB document per `trip_id` (`state`), matching the whole-
  document-per-trip design `PlanningStateRepository.save` already uses
  against `LocalJsonStore` today. `planning_state_id`/`current_version`/
  `pipeline_status` are lifted out of `state` into their own indexed
  columns purely as a future queryability optimization (the same
  hybrid relational-columns-plus-JSONB design `ARCHITECTURE.md` section 11
  originally sketched, and Step 183A's audit recommended) -- `state`
  remains the single source of truth for the full plan.
- **Deliberately excluded** (matching this step's explicit scope): no
  `provider_cache` table (stays SQLite, see
  `app/storage/provider_cache_store.py`, completely independent of this
  migration); no normalized `feedback_events`/`version_history`/
  `user_locks`/`regeneration_attempts` tables (the current contract is
  the whole `PlanningState` JSONB blob, same as `LocalJsonStore` today);
  no `user_id`/`owner_id`/auth column anywhere (auth/user isolation is
  unimplemented today, out of scope for this step).

**`backend/app/db/models.py`** (new) adds minimal SQLAlchemy ORM class
metadata -- `TripRow`/`PlanningStateRow`, defined against `app/db/base.py`'s
`Base` -- mirroring the migration's columns (hand-written independently,
not code-shared with the migration, so the migration stays historically
accurate even if this file changes later). Like every other `app/db/`
module, it is **not imported by any route/service/repository** -- it
exists only so a future Step 183D repository implementation, and
Alembic's `--autogenerate`, have real ORM metadata to work against.
Importing it registers tables on `Base.metadata` but never creates a
table or opens a connection (nothing in this codebase calls
`Base.metadata.create_all(...)`; the schema is created via the Alembic
migration above). `backend/alembic/env.py` now imports `app.db.models` so
`Base.metadata` is actually populated for future autogenerate diffs --
this import alone still never connects to anything, verified by running
`alembic history`/`alembic revision` (which never open a connection) and
by `alembic current` (which does, and was manually confirmed to fail only
at the connection step -- an unresolvable `postgres` hostname outside a
Docker network -- never at any Python import).

**Tests** (`backend/app/tests/db/test_migration_schema.py`,
`test_db_models.py`, `test_alembic_env.py`, 20 tests total, none
requiring Postgres/Docker): the migration file exists and is the root
revision; `upgrade()` creates exactly `trips`/`planning_states` (no
`provider_cache`, no user/owner/auth column); the `state` column is
`postgresql.JSONB`; the `trips`/`planning_states` foreign key is
`ON DELETE CASCADE`; `downgrade()` drops both tables; the SQLAlchemy
metadata's table/column names match; `app.db.models` itself never calls
`create_all`/opens a connection; `alembic history` succeeds and
`alembic current` fails with a connection error (`OperationalError`),
never an import error, proving the whole `env.py` wiring is sound without
needing a real database. The migration file's own tests use static AST
parsing rather than executing `upgrade()`/`downgrade()` against a
hermetic database, since the Postgres-only `JSONB` column type can't
compile against a SQLite stand-in.

**Optional live verification**: Docker was available in this environment
(`docker compose up -d postgres`), but the host's port 5432 was already
bound by something else, so `docker compose up` failed with "address
already in use." The container/network compose had already created were
cleaned up (`docker compose down`) without forcing anything else off that
port. Live `alembic upgrade head`/`alembic current`/`alembic downgrade
base` against a real Postgres were not run this step -- the static/
subprocess-based tests above are the only automated coverage, exactly as
the "do not require this for automated tests" instruction allows.

The full suite (2479 tests, up from 2459) still passes with the
developer's real local `.env` left in place, untouched (~15s, zero
network calls). `Settings.persistence_backend` still defaults to
`"local_json"`; nothing about trip creation, plan generation,
regeneration/version/feedback/lock/narrator persistence, or provider
cache behavior changed.

## 105. Repository Factory and Opt-In Postgres Repositories (Step 183D)

The Step 183C schema now has real repository implementations behind it,
selected by `Settings.persistence_backend` -- still `"local_json"` by
default. **API response contracts, `PlanningState` semantics, and
default (`local_json`) behavior are all unchanged** -- this step only
adds a second, opt-in implementation of the exact same two-repository
contract Step 183A already documented.

**`app/repositories/protocols.py`** (new) -- `TripRepositoryProtocol`/
`PlanningStateRepositoryProtocol`, `typing.Protocol` contracts describing
the exact public surface both backends satisfy (`create`/`get`/
`update_status`; `save`/`get_by_trip_id`). No behavior, purely a
type-checkable description; the existing local repositories already
satisfy these structurally with no changes.

**`app/repositories/factory.py`** (new) -- `get_trip_repository()`/
`get_planning_state_repository()` are now the only way production code
resolves a repository. Each call reads `Settings.persistence_backend`
fresh (never cached at import time -- see below for why that matters):
`"local_json"` (default) returns the *exact same* module-level singleton
objects that existed before this step (`trip_repository`,
`planning_state_repository`), not a fresh instance per call, so
performance, in-memory caching, and every test that monkeypatches those
singletons' `_store`/`_trips`/`_states` attributes directly (`conftest.py`'s
`_reset_in_memory_repositories` fixture) all keep working unchanged.
`"postgres"` lazily constructs and caches (`functools.lru_cache`,
matching `app/db/session.py`'s `get_engine()` pattern) one
`PostgresTripRepository`/`PostgresPlanningStateRepository` -- constructed,
and so first able to connect, only on the first call made while
`persistence_backend == "postgres"`. Setting `DATABASE_URL` alone still
never selects Postgres (unchanged from Step 183B); only an explicit
`PERSISTENCE_BACKEND=postgres` does.

**`app/repositories/postgres_trip_repository.py`** /
**`postgres_planning_state_repository.py`** (new) -- sync SQLAlchemy +
psycopg implementations matching the local repositories' exact behavior:
- `PostgresTripRepository.create()` upserts (`INSERT ... ON CONFLICT
  (trip_id) DO UPDATE`), matching `TripRepository.create()`'s own
  "always replace with a brand-new record" behavior on a duplicate
  `trip_id` (fresh `created_at`, not just `updated_at`) -- confirmed by
  inspecting the local repository's source before implementing this, per
  this step's explicit instruction. `get()`/`update_status()` match
  `TripRepository`'s exact None-on-missing-row semantics.
- `PostgresPlanningStateRepository.save()` upserts the entire
  `planning_state.model_dump(mode="json")` into `planning_states.state`
  (JSONB), lifting `planning_state_id`/`metadata.current_version`/
  `metadata.pipeline_status.value` into their own columns, and always
  refreshing `updated_at` while preserving the original `created_at`
  across updates. It never mutates the `PlanningState` object passed in,
  and returns that same object unchanged -- matching
  `PlanningStateRepository.save()` exactly. Because
  `planning_states.trip_id` has a real foreign key to `trips.trip_id`
  (Step 183C) but the two *local* repositories are completely
  independent of each other (no such coupling), `save()` first upserts a
  minimal `trips` row via `ON CONFLICT DO NOTHING` -- documented and
  tested (`test_planning_state_repository_ensures_trip_row_exists_...`)
  rather than left as an undocumented FK-violation trap for a caller who
  never called the trip repository's `create()` first.
- `get_by_trip_id()` always re-validates the stored JSONB through
  `PlanningState.model_validate(...)` -- never manually reconstructs
  fields -- so it inherits the exact same backward-compatibility handling
  the local repository already has, and a corrupt/invalid stored payload
  raises a real `pydantic.ValidationError` rather than being silently
  discarded or fabricated into a default state.

**Production wiring**: `app/api/routes/trips.py` now imports
`get_planning_state_repository` from the factory instead of the raw
`planning_state_repository` singleton, calling it fresh at each of its
~25 call sites (mechanical, behavior-preserving for `local_json` -- the
factory returns the identical singleton object every time).
`PlanningOrchestrator.planning_state_repository`/`.trip_repository`
became `@property`s that resolve via the factory on every access
(falling back to an injected override, e.g. for tests, if one was passed
to `__init__`) instead of being eagerly bound once inside `__init__`.
This distinction matters because `planning_orchestrator` is itself a
module-level singleton constructed once at import time
(`services/planning_orchestrator.py`'s last line) -- eagerly resolving a
repository inside `__init__` would repeat the exact import-time-
singleton-contamination mistake Step 183B-FIX found and fixed for
`provider_gateway` (a live setting baked in before any per-test isolation
exists). Properties instead mean `Settings.persistence_backend` is read
fresh every time the orchestrator touches a repository, so it always
reflects the current config, never a stale import-time snapshot -- and
since the factory's `local_json` branch is just an attribute lookup (no
I/O), this costs nothing in the default case.

**Tests** (48 new across
`backend/app/tests/repositories/test_repository_factory.py` (7),
`test_postgres_trip_repository.py` (5),
`test_postgres_planning_state_repository.py` (6), plus 5 skipped-by-
default integration tests in `test_postgres_repositories_integration.py`
-- see below): factory default/opt-in selection, `DATABASE_URL` alone
not selecting Postgres, a reproduction of the exact 183B-FIX scenario
(a throwaway `.env` selecting Postgres, proven not to leak into the
factory during pytest), Postgres repository unit tests against a fake
`Session` (upsert statement shape via `.compile()`, no live connection
needed -- `TripRow`/`PlanningStateRow` can be constructed directly in
memory without touching a database), missing-row-returns-None, and
invalid stored JSON surfacing a real `ValidationError`. None of the
mandatory suite requires Postgres.

**Optional live-Postgres integration tests**
(`test_postgres_repositories_integration.py`) are gated by
`TRAVELOB_RUN_POSTGRES_TESTS=1` (skipped otherwise -- confirmed via a
direct run showing "5 skipped") and additionally need
`PERSISTENCE_BACKEND=postgres` + a real `DATABASE_URL` against a database
that already has `alembic upgrade head` applied. They round-trip both
repositories against a real Postgres, including one full API-level test
(`TestClient` hitting `POST /trips`/`GET /trips/{id}`) proving the
`trips.py`/`PlanningOrchestrator` wiring genuinely reaches Postgres end
to end when opted in, not just that the repository classes work in
isolation.

**Live verification attempted, not completed**: Docker was available
again this step, but host port 5432 was still bound by something outside
this project (same as Step 183C) -- `docker compose up -d postgres`
failed with "address already in use" a second time. Cleanly aborted
(`docker compose down`) without touching whatever holds that port,
exactly as this step's explicit instructions require.

The full suite (2497 passed, 5 skipped, up from 2479 passed) still runs
with the developer's real local `.env` left in place, untouched (~16s,
zero network calls). Provider cache is untouched (still SQLite,
independent); no auth/user column exists anywhere; no frontend file
changed.

## 106. Docker Compose Port Hardening and Completed Live Postgres Verification (Step 183E)

Steps 183C and 183D both attempted live Postgres verification and both
had to abort: the host's port 5432 was already bound by something
outside this project (never identified, never touched -- this project
must never assume it owns port 5432 on a developer's machine). This step
fixes that generally, then completes the verification those steps could
only attempt.

**`docker-compose.yml`**: the `postgres` service's port mapping is now
`"${POSTGRES_HOST_PORT:-5432}:5432"` -- unset, this is byte-for-byte the
same behavior as before (`5432:5432`); set (e.g.
`POSTGRES_HOST_PORT=15432`), only the HOST side changes. The container's
own internal port, and therefore how every OTHER container in this
compose file reaches it, stays `postgres:5432` regardless -- `backend`
never needed to change and didn't. A `pg_isready`-based healthcheck was
also added (`interval: 5s`, `timeout: 5s`, `retries: 5`, using
`${POSTGRES_USER}`/`${POSTGRES_DB}`) -- purely informational (nothing's
`depends_on` was changed to require it), but it let this step's live
verification poll for real readiness instead of guessing a sleep
duration, and it's confirmed to report `healthy` in practice (`docker
inspect`).

**`.env.example`** documents both host-vs-compose-network addressing
(the same Postgres, reached two different ways depending on where the
connecting process runs) and the new `POSTGRES_HOST_PORT` variable:
- Backend running *inside* `docker compose` reaches Postgres at
  `postgres:5432` (the compose-internal network) -- always, regardless
  of `POSTGRES_HOST_PORT`.
- Alembic, `pytest`, or the backend run directly on the *host* machine
  against a `docker compose up -d postgres` container must use
  `localhost:<POSTGRES_HOST_PORT>` (`localhost:5432` if left at the
  default, `localhost:15432` if changed).
`PERSISTENCE_BACKEND=local_json` remains the documented default; setting
`DATABASE_URL` alone still never switches persistence -- both statements
are unchanged from Step 183B/183D and re-confirmed by this step's own
tests.

**Live verification, completed this time**: with Docker available and
port 5432 still occupied by something outside this project (confirmed by
re-attempting `docker compose up -d postgres` with the *default* port
mapping and getting the same "address already in use" as Steps 183C/183D
-- reported honestly, not forced), `POSTGRES_HOST_PORT=15432 docker
compose up -d postgres` was used instead. Real steps run, in order, all
succeeding:
1. `docker compose up -d postgres` on port 15432 -- started, and the new
   healthcheck reported `healthy` on the very first poll.
2. `DATABASE_URL=postgresql://travelobligator_user:change_me@localhost:15432/travelobligator alembic upgrade head`
   -- applied the Step 183C migration to a real, empty Postgres database
   for the first time ever in this project's history. Output: `Running
   upgrade  -> 2fe86f95d81e, create_trips_and_planning_states`.
3. `alembic current` against the same URL -- confirmed `2fe86f95d81e
   (head)`.
4. `TRAVELOB_RUN_POSTGRES_TESTS=1 PERSISTENCE_BACKEND=postgres DATABASE_URL=...
   python -m pytest app/tests/repositories/test_postgres_repositories_integration.py`
   -- **all 5 tests passed against a real Postgres** for the first time
   (previously only ever proven against fake sessions). A direct `psql`
   query afterward independently confirmed real rows in both `trips` (4)
   and `planning_states` (3).
5. The new opt-in API smoke test (below) also passed against the same
   real database.
6. `alembic downgrade base` -- reverted cleanly, dropping both tables.
7. `docker compose down` -- removed the container/network (the named
   volume, now empty, was left in place; nothing forced).

No real secret was printed in any of the above -- one earlier, unrelated
`docker compose config` invocation during this step *did* print resolved
container environment variables including a real API key from the local
`.env` (a general property of that Compose subcommand, not something
`docker-compose.yml` itself controls) -- caught immediately, never
repeated, and that value is not written anywhere in this repository or
in this documentation.

**New opt-in API smoke test**
(`backend/app/tests/api/test_postgres_api_smoke.py`, skipped unless both
`TRAVELOB_RUN_POSTGRES_TESTS=1` and a real `DATABASE_URL` are set) walks
create -> generate -> get -> feedback -> regenerate (accepting either a
real `200` new version or a named `409` refusal -- Section 174's
regeneration contract is refusal-first either way) -> a final read
through a *fresh* `PostgresPlanningStateRepository` instance (its own new
`Session`, nothing cached from the request handlers) to prove the whole
API-level flow, not just the repository classes in isolation, genuinely
persists to and reads back from Postgres when opted in.

**Provider cache decision (explicit, not deferred by accident)**: this
step deliberately does **not** add a `PostgresProviderCacheStore`.
`ProviderCacheStore` stays SQLite, completely independent of both
`LocalJsonStore` and the new Postgres repositories. Reasoning: the
provider cache is decoupled from trip/plan persistence by design (losing
it just means the next provider call re-fetches instead of hitting a
cached response -- never a correctness or data-loss risk), whereas trip
and plan data loss is real and irreversible; migrating the cache to
Postgres is not required for real MVP production persistence (the actual
goal of Section 183) and would add scope with no corresponding safety or
product benefit. This may be revisited later as genuinely optional future
work, not as unfinished business from this section.

**Tests** (`backend/app/tests/repositories/test_postgres_opt_in_gating.py`,
7 new, plus the 1 new API smoke test above -- none requiring Docker/live
Postgres to run as part of the default suite): `docker-compose.yml`'s
`postgres` service has the configurable `POSTGRES_HOST_PORT` mapping with
its backwards-compatible `5432` default, and a `pg_isready` healthcheck;
`backend`'s compose block still never hardcodes a conflicting
`DATABASE_URL`; `.env.example` documents `POSTGRES_HOST_PORT` and both
addressing modes; `PERSISTENCE_BACKEND=local_json` is still the
documented default; and -- the most direct regression guard for this
step's whole premise -- both gated Postgres test files are run as real
subprocesses with a clean environment (no
`TRAVELOB_RUN_POSTGRES_TESTS`/`DATABASE_URL`/`PERSISTENCE_BACKEND`) and
asserted to report "skipped", never "passed" or an error/hang.

The full suite (2504 passed, 6 skipped, up from 2497 passed/5 skipped)
still runs with the developer's real local `.env` left in place,
untouched (~17s, zero network calls). `persistence_backend` still
defaults to `local_json`; `DATABASE_URL` alone still never selects
Postgres; no auth/user column, provider_cache change, or frontend file
exists anywhere in this step's diff.

## 107. Section 183 Final Review, Docs Cleanup, and Commit Readiness (Step 183F, final Section 183 step)

Final pass over the whole 183A-183E stack: re-read every persistence-
related file fresh (not just re-cited from earlier steps' own claims),
re-ran every check, and closed one stale doc line. No product behavior
changed.

**What Section 183 is, in one place**: an opt-in PostgreSQL persistence
*foundation* -- `local_json` (the pre-existing `LocalJsonStore`-backed
`TripRepository`/`PlanningStateRepository`) remains the zero-setup
default with zero behavior change from before Section 183 started.
Postgres only activates when `PERSISTENCE_BACKEND=postgres` is explicitly
set; `DATABASE_URL` alone never does this. The schema is exactly two
tables (`trips`, `planning_states`), with `PlanningState` stored as one
whole JSONB document per trip -- no normalized feedback/version/lock/
regeneration/narrative tables, no `provider_cache` table, no
`user_id`/`owner_id`/auth column anywhere. `provider_cache` stays SQLite,
independent, by an explicit, documented decision (section 106) -- not
deferred by accident. Auth/user isolation and async/background job
processing remain explicitly out of scope (README.md's deferred-work
list, unchanged in scope by this section). Automated `pytest` is hermetic
against a developer's real `.env` (section 103) and never requires a live
database (the 6 Postgres-only tests skip unless both
`TRAVELOB_RUN_POSTGRES_TESTS=1` and a real `DATABASE_URL` are set).

**Fresh re-confirmation this step actually performed** (not just
citation of 183A-183E's own claims):
- `grep -rn "import.*postgres_trip_repository\|import.*postgres_planning_state_repository"`
  across `app/` (excluding tests) matches only
  `app/repositories/factory.py` -- no route or service imports a Postgres
  repository module directly.
- Re-read `PlanningOrchestrator.__init__`/`.planning_state_repository`/
  `.trip_repository`: confirmed the two repository attributes are
  `@property`s resolving through the factory on every access, with the
  override captured in `__init__` only as a plain attribute -- never
  eagerly bound to a singleton at construction time (the orchestrator
  itself is a module-level singleton, constructed at import time).
- Re-read the Step 183C migration's `upgrade()`/`downgrade()` side by
  side: `downgrade()` drops exactly the two indexes and one table
  `upgrade()` created, for both `trips` and `planning_states` -- fully
  symmetric.
- Re-read both Postgres repository implementations side by side with
  their local-JSON equivalents: `create()`'s upsert-on-duplicate matches
  `TripRepository.create()`'s "always a brand-new record" behavior;
  `save()` upserts JSONB, lifts `planning_state_id`/`current_version`/
  `pipeline_status` into columns, omits `created_at` from its `ON
  CONFLICT ... DO UPDATE SET` clause (preserved) while always setting
  `updated_at`, never mutates the `PlanningState` argument (only reads
  from it, returns the same object), and `get_by_trip_id` always goes
  through `PlanningState.model_validate(row.state)` -- a corrupt/invalid
  stored payload surfaces a real `pydantic.ValidationError`.
- Re-read `docker-compose.yml`: `POSTGRES_HOST_PORT` mapping and
  `pg_isready` healthcheck both present and unchanged from Step 183E;
  `backend`'s own block still has no compose-level `DATABASE_URL`
  override (it only ever comes from `.env` via `env_file`).

**Secret safety audit** (this step ran no command that could print
`.env`'s contents -- no `cat .env`, `grep .env`, `docker compose config`,
`printenv`, or `env`):
- `git check-ignore -v` confirmed `.env`, `backend/.data/
  travelobligator_state.json`, `backend/.data/provider_cache.sqlite3`,
  and both `.data/manual_scrapes/*.html` paths are all still correctly
  gitignored.
- A grep for real-looking key shapes (`sk-`, `gsk_`, and every
  `*_API_KEY=<value>` pattern) across `.env.example`, `README.md`,
  `docs/`, `backend/app`, `backend/alembic`, and `docker-compose.yml`
  found zero real keys -- every hit is either a `.env.example`/README
  placeholder (blank or literal `...`), or an explicit, obviously-fake
  test value (e.g. `"fake-not-a-real-key"`,
  `"dummy-test-key-should-never-be-printed"`).
- `.env.example` was re-read directly: every credential field
  (`ANTHROPIC_API_KEY=`, `GROQ_API_KEY=`, every partner-provider key) is
  blank.
- One transparency note carried forward from Step 183E: an unrelated
  `docker compose config` invocation during that step printed resolved
  container environment variables, including a real key from the local
  `.env` -- a general property of that Compose subcommand, not something
  `docker-compose.yml` itself controls. It was caught immediately, never
  repeated, and that value was never written to any file. This step's own
  live-Postgres re-verification (below) deliberately used only `docker
  compose up -d`/`down`, `alembic`, and `pytest` -- never `docker compose
  config`.

**Overclaim/safety copy review**: none of this step's own doc edits (or
any prior 183A-183E doc edit) introduces language implying the app is
production-ready, that validation means ready for real-world use without
review, that anything is booked/confirmed, that manual/local data is
official, or that regeneration improves a plan -- confirmed via the
project's standard banned-phrase grep across `README.md`, `docs/`, and
`backend/app`; every hit is either a pre-existing negation ("not a
confirmed booking") or an LLM-prompt instruction telling a model never to
make such a claim.

**One stale doc line fixed**: README.md's deferred-work list previously
said "the compose `postgres`/`redis` services exist but nothing in the
app talks to them yet" -- true when written (Step 181D), false after
183D/183E. Reworded to say `redis` is still fully untouched while
`postgres` is now real and opt-in (cross-referencing the point above it),
without overclaiming it as the default.

**Live Postgres re-verification, one more time**: with port 5432 still
occupied by something outside this project, `POSTGRES_HOST_PORT=15432
docker compose up -d postgres` started cleanly and reported `healthy`
immediately via the Step 183E healthcheck. `alembic upgrade head` /
`alembic current` both succeeded against the fresh database. All 6 gated
Postgres tests (`test_postgres_repositories_integration.py`'s 5 +
`test_postgres_api_smoke.py`'s 1) passed together in one run against the
real database. `alembic downgrade base` and `docker compose down` cleaned
up fully afterward -- confirmed via `docker compose ps -a` showing no
containers.

The full suite (2504 passed, 6 skipped -- unchanged from Step 183E, since
this step added no new tests, only doc/review work) was re-run one final
time with the real `.env` left in place throughout (~17s, zero network
calls). `tsc`/`lint`/`build` all clean; zero frontend files touched
anywhere across the whole of Section 183. **Section 183 (183A-183F) is
ready to commit** as one stack, pending the user's own review.

## 108. Auth Foundation: Passwords, Sessions, Errors -- No Route Wired Yet (Step 184B)

Following the Step 184A read-only audit (which found **zero** auth/
session/user concept anywhere in this codebase -- every `/trips/*` route
is scoped only by knowledge of a `trip_id`), this step adds the backend
primitives full signup/login/logout will need, without touching a single
existing route. **`app/api/routes/trips.py` is byte-for-byte unchanged**
-- every trip route keeps its current zero-authentication behavior
exactly as before this step, and no `owner_id` exists on `trips` yet.

- **Dependencies**: `bcrypt==5.0.0` (password hashing) and
  `itsdangerous==2.2.0` (signed session cookies), both pinned explicitly
  in `backend/requirements.txt`. Plain `bcrypt`, not `passlib` (version-
  compatibility friction with newer `bcrypt` releases) -- salted,
  adaptive hashing only, never a custom scheme or plain SHA/MD5.
- **Config** (`Settings`, `backend/app/core/config.py`): `session_secret_key`
  (`SESSION_SECRET_KEY`, default `None`), `session_cookie_name`
  (default `"travelobligator_session"`), `session_ttl_seconds` (default
  `604800`, one week), `session_cookie_secure` (default `False`, so
  local HTTP dev keeps working), `session_cookie_samesite` (default
  `"lax"`, normalized case-insensitively, unrecognized values clamp to
  `"lax"` matching every other constrained field's convention),
  `session_cookie_httponly` (default `True`). `session_secret_key`
  defaulting to `None` is load-bearing: it means auth is "not configured"
  out of the box, and every auth utility below refuses to operate at all
  while it's unset, rather than falling back to a shared/predictable key
  -- so importing this module, starting the app, or running the test
  suite never requires it to be set.
- **`backend/app/models/user.py`** (new): `UserRecord` (full internal
  shape, including `password_hash`), `PublicUser`/`AuthResponse`
  (API-facing, both structurally exclude `password_hash` --
  `PublicUser.model_fields` has no such key at all, not just a masked
  value), `SignupRequest`/`LoginRequest`. Email is normalized (trimmed,
  lowercased) via a `field_validator` on every one of these models, with
  a small manual shape check (non-empty local/domain parts, a `.` in the
  domain) rather than depending on `pydantic[email]`/`email-validator`
  (not already a project dependency). `SignupRequest.password` enforces
  an 8-character minimum (`Field(min_length=8)`) and a 72-*byte* maximum
  (bcrypt's own hard limit, enforced here too so an over-long password is
  a normal 422 validation error, not a raw `ValueError` from the hashing
  layer) -- `LoginRequest.password` deliberately has neither constraint,
  since an existing account's password must still verify correctly even
  if today's rules differ from whatever rules applied when it was
  created.
- **`backend/app/auth/passwords.py`** (new): `hash_password`/
  `verify_password`. `verify_password` returns `False` (never raises) for
  a wrong password, an over-length password, *or* a malformed/corrupt
  stored hash -- a corrupt stored value fails closed rather than crashing
  a future login route with a raw exception. Neither function logs or
  includes the plaintext password in any exception message.
- **`backend/app/auth/sessions.py`** (new): `create_session_token`/
  `verify_session_token` (via `itsdangerous.URLSafeTimedSerializer`) and
  `set_session_cookie`/`clear_session_cookie` (via FastAPI/Starlette's
  own `Response.set_cookie`/`delete_cookie`). The signed token payload is
  `{"user_id": ...}` **only** -- never a password, email, or API key --
  verified by confirming both the token's own tests decode that exact
  minimal payload. There is no server-side session store: the cookie's
  HMAC signature plus its own embedded issue timestamp are the only
  state `verify_session_token` needs, checked against
  `session_ttl_seconds` on every call. `AuthNotConfiguredError` (a new
  exception, not an `AppError`) is raised by both `create_session_token`
  and `verify_session_token` whenever `session_secret_key` is unset --
  callers must handle this as a distinct "server misconfigured" case, not
  "not logged in." Cookie flags (`HttpOnly`/`Secure`/`SameSite`/
  `Max-Age`) all come from `Settings`, never hardcoded.
- **Auth errors** (`app/schemas/errors.py`'s `ErrorCode` enum +
  `app/core/errors.py` constructors, matching the existing
  `trip_not_found_error`-style centralized-constructor convention):
  `AUTHENTICATION_REQUIRED`/`authentication_required_error()` (401, no or
  invalid/expired session), `FORBIDDEN`/`forbidden_error()` (403,
  authenticated but not the resource's owner -- deliberately distinct
  from the existing 404 `trip_not_found_error` once Step 184D wires
  ownership in, so "doesn't exist" and "exists, not yours" stay
  distinguishable to a caller without either response revealing who the
  real owner is), `INVALID_CREDENTIALS`/`invalid_credentials_error()`
  (401, deliberately generic wording -- "Invalid email or password."
  never reveals whether the email was registered or the password was
  wrong, so a login attempt can't enumerate accounts),
  `EMAIL_ALREADY_REGISTERED`/`email_already_registered_error()` (409, for
  Step 184C's signup route), `AUTH_NOT_CONFIGURED`/
  `auth_not_configured_error()` (503, the `AuthNotConfiguredError` →
  HTTP-error translation for Step 184D's dependency to use). None of
  these are raised by any route today.
- **`backend/app/auth/dependencies.py`** (new): `get_current_user_id`, a
  FastAPI dependency resolving a request down to a plain `user_id`
  string (not a full `User` object -- no repository exists yet; a
  `TODO` comment marks exactly where Step 184C's repository-backed
  lookup will slot in). Raises `authentication_required_error()` for a
  missing/invalid/expired cookie and `auth_not_configured_error()` when
  the secret is unset. **Not applied to any route** -- confirmed by grep
  showing zero references to it from `app/api/routes/trips.py`.

**Tests** (`backend/app/tests/core/test_session_auth_config.py`,
`test_errors.py` (extended), `backend/app/tests/models/test_user_models.py`,
`backend/app/tests/auth/test_passwords.py`/`test_sessions.py`/
`test_dependencies.py` -- ~77 new, zero requiring a live database):
config defaults/normalization, `.env.example`'s `SESSION_SECRET_KEY=`
blank placeholder, email normalization (including rejecting
blank/malformed input), password length bounds (min 8, max 72 bytes),
`PublicUser`/`AuthResponse` structurally excluding `password_hash`,
hashing produces a different salted hash each time and never equals the
plaintext, correct/wrong/malformed-hash verification, a full
create→verify session-token round trip, tampered/wrong-secret/expired
tokens all rejected, the token payload proven to contain only `user_id`
(decoded directly, no secret needed since itsdangerous signs for
tamper-evidence rather than encrypting), cookie flag tests asserting the
literal `Set-Cookie` header contains `HttpOnly`/`Secure`/`SameSite=<value>`/
`Max-Age=<value>` exactly matching config, the dependency raising 401 for
missing/invalid tokens (tested by calling it directly with a
hand-constructed `starlette.requests.Request` rather than a full
`TestClient` round trip, since `AppError` needs `app/main.py`'s own
exception handler registered to become a clean HTTP response -- exactly
the same reason `trip_not_found_error` and friends are unit-tested this
way elsewhere in this suite) and 503 for an unset secret.

The full suite (2581 passed, 6 skipped -- unchanged from Step 183F's own
6 skips, up from 2504 passed) runs with the developer's real local
`.env` left in place, untouched. `tsc`/`lint`/`build` all clean; zero
frontend files touched. `app/api/routes/trips.py` and
`app/services/planning_orchestrator.py` are unmodified -- confirmed via
`git diff --stat` showing neither file listed.

Not done in this step, by design: no `/auth/*` routes (Step 184C), no
user repository/persistence backend decision for users (Step 184C), no
`owner_id` column/field anywhere (Step 184C's migration + Step 184D's
route wiring), no route requires a session yet, no OAuth, no password
reset, no email verification, no rate limiting.

## 109. Persistent Users and Working `/auth/*` Routes, Trip Ownership Still Not Enforced (Step 184C)

Following Step 184B's primitives-only foundation, this step makes
signup/login/logout/current-user actually work end to end against a real
(if minimal) user store -- while **`/trips/*` remains completely
untouched**: no route requires a session, no trip gets an `owner_id` set
on creation, and no route checks ownership. That enforcement is Step
184D's job. `main.py` gained exactly one new line registering the new
`/auth` router alongside the existing `/trips` one.

**User repository** (mirrors `TripRepository`/`PlanningStateRepository`
exactly): `backend/app/repositories/user_repository.py`'s `UserRepository`
stores `UserRecord`s in a new `"users"` collection in the same
`LocalJsonStore` file trips/planning-states already share -- confirmed by
test that writing all three collections to one file never clobbers each
other. `create_user` raises a new, backend-agnostic
`UserAlreadyExistsError` (`app/repositories/protocols.py`) when the
(already-normalized) email already has an account -- checked via
`get_by_email` first, since local_json has no real unique-constraint
mechanism of its own. `backend/app/repositories/postgres_user_repository.py`'s
`PostgresUserRepository` satisfies the exact same
`UserRepositoryProtocol`, but detects the duplicate via the real
`users.email` UNIQUE constraint's `IntegrityError` instead of a separate
lookup -- correct under concurrent signup attempts for the same email in
a way a check-then-insert never could be. `app/repositories/factory.py`
gained `get_user_repository()`, following the exact same lazy,
per-call-`Settings.persistence_backend`-read pattern as
`get_trip_repository()`/`get_planning_state_repository()` (Step 183D) --
`local_json` (default) returns the same module-level singleton;
`"postgres"` lazily builds and `lru_cache`s one `PostgresUserRepository`;
`DATABASE_URL` alone still never selects Postgres.

**Schema** (`backend/app/db/models.py` + a second Alembic migration,
`835e5782d7a5_create_users_and_trips_owner_id.py`, chained after Step
183C's `2fe86f95d81e`): a new `users` table (`user_id` PK, `email TEXT
NOT NULL UNIQUE`, `password_hash TEXT NOT NULL`, `created_at`/
`updated_at TIMESTAMPTZ NOT NULL`) plus a single new `trips.owner_id
TEXT` column -- **nullable**, for backward compatibility with every
`trips` row that already exists (including Section 183's own live
verification rows), with a foreign key to `users.user_id` using `ON
DELETE SET NULL` (never `CASCADE` -- deleting a user must never
cascade-delete their trips' planning data) and its own index (the "My
Trips" list query's future access pattern). Deliberately excludes, same
as Step 183C's own migration: any change to `planning_states` (ownership
lives on `trips` only -- derivable by joining through the existing 1:1
`planning_states.trip_id → trips.trip_id` FK, never duplicated), a
`sessions` table (this app's sessions are stateless signed cookies, see
section 108 -- there is no server-side session state to persist), and
any role/admin/permission column. `downgrade()` removes exactly what
`upgrade()` added, in reverse order (index → FK → column → table),
verified against a real Postgres (below).

**Auth service** (`backend/app/auth/service.py`, new): `signup`
normalizes email, hashes the password, creates the `UserRecord`, and
converts a repository-level `UserAlreadyExistsError` into the existing
`email_already_registered_error()` (409) -- callers never see a
repository-specific exception. `login` looks up by email and verifies
the bcrypt hash, raising the existing generic `invalid_credentials_error()`
(401) for *either* an unknown email or a wrong password -- verified by a
test asserting both cases produce byte-identical error messages, so
neither can be used to enumerate registered accounts.
`get_current_user_by_id` returns `None` (not an exception) for an
unknown id, letting `app/auth/dependencies.py`'s `get_current_user`
decide that's an authentication failure, not a service concern. Every
function accepts an optional injected `UserRepositoryProtocol`
(defaulting to `get_user_repository()`), so all of this is testable with
a throwaway repository, never the real singleton or a live database.

**`/auth/*` routes** (`backend/app/api/routes/auth.py`, new router,
registered in `main.py`):
- `POST /auth/signup` (201) and `POST /auth/login` (200) both call
  `_require_auth_configured()` **before** touching the user repository --
  if `SESSION_SECRET_KEY` is unset, the request fails with the existing
  `auth_not_configured_error()` (503) before any account is created,
  verified by a test showing a retry after the secret is fixed can still
  succeed (no orphaned duplicate-email conflict from the failed attempt).
  Both then set the signed, `HttpOnly` session cookie via
  `app/auth/sessions.py`'s `set_session_cookie` and return `AuthResponse`
  (never `password_hash` -- structurally absent from `PublicUser`, not
  masked).
- `POST /auth/logout` (200) always succeeds, even with
  `SESSION_SECRET_KEY` unset -- clearing a cookie is a plain expired
  `Set-Cookie` header, nothing is signed or verified.
- `GET /auth/me` (200/401) uses the new `get_current_user` dependency
  (below) and returns the same `AuthResponse` shape signup/login do.

**Current-user dependency** (`app/auth/dependencies.py`): `get_current_user`
(new, alongside Step 184B's `get_current_user_id`) resolves a request all
the way to a `PublicUser` -- and, now that a repository exists, handles
one more case Step 184B's `TODO` explicitly deferred: a *valid,
correctly-signed, unexpired* token whose user no longer exists (deleted
after the cookie was issued) also raises `authentication_required_error()`
(401) rather than crashing or returning a phantom user. No test-only
bypass exists anywhere in either dependency.

**Tests** (~102 new across `test_user_repository.py`,
`test_postgres_user_repository.py` (fake session, no live DB),
`test_repository_factory.py` (extended), `test_migration_schema_users.py`
(16, AST-based -- migration chains correctly, creates only `users`,
`owner_id` nullable with the right FK/`ON DELETE`/index, no
`planning_states`/`sessions`/role/`provider_cache` touch, `downgrade()`
symmetric), `test_db_models.py` (extended -- now 3 tables, `UserRow`
columns, `TripRow.owner_id`'s FK), `test_service.py`, `test_dependencies.py`
(extended), `test_auth_routes.py` (21, full `TestClient` round trips), and
a skipped-by-default `test_postgres_user_repository_integration.py`):
signup/login/logout/me across both success and every named failure mode,
password hashing never plaintext, duplicate email 409, generic
credentials error verified byte-identical for unknown-email vs.
wrong-password, expired-token 401, auth-not-configured 503 with no
dangling user created, and -- the regression guard for this step's whole
premise -- the full pre-existing `/trips/*` suite re-confirmed passing
completely unauthenticated, since trip-route enforcement is explicitly
184D's job, not this step's.

One correctness fix made *during* this step's own live-Postgres run,
worth recording: `test_auth_routes.py`'s first draft used fixed literal
emails (`"a@b.com"`, `"user@example.com"`) -- fine against `local_json`
(conftest.py's autouse fixture gives every test a fresh, empty store) but
wrong against a real, persistent Postgres database with no such reset,
where a fixed email collides with itself across separate test runs and
produces a false `409` instead of testing what the test actually names.
Fixed by generating a unique email per test (matching
`test_postgres_repositories_integration.py`'s own `_new_trip_id()`
convention) -- now correct and passing identically under both backends,
confirmed by running the same 21 tests against `local_json` and then
against a real live Postgres in the same session.

**Live verification, completed**: `POSTGRES_HOST_PORT=15432 docker
compose up -d postgres` → healthy → `alembic upgrade head` applied both
migrations to a real, freshly-reset Postgres in one run → `psql \d trips`
independently confirmed `owner_id` nullable with the exact
`fk_trips_owner_id_users ... ON DELETE SET NULL` constraint and
`ix_trips_owner_id` index → all 25 gated tests (4 user-repository
integration + 21 full auth-route tests) passed against the real database
→ `alembic downgrade base` reverted both migrations cleanly → `docker
compose down` left zero containers running. `docker compose config` was
not used at any point.

The full suite (2654 passed, 10 skipped, up from 2581 passed/6 skipped)
runs with the developer's real local `.env` left in place, untouched;
`backend/.data/` confirmed empty before and after the whole run (no
leftover test artifact from manual verification scripts touched real
local dev storage). `tsc`/`lint`/`build` all clean; zero frontend files
touched. `app/api/routes/trips.py`/`app/services/planning_orchestrator.py`
still show zero relevant changes in `git diff` beyond what 184B already
established as unmodified.

Not done in this step, by design: no `/trips/*` route requires a session
or checks ownership (Step 184D), no trip gets `owner_id` set on creation
(Step 184D), no frontend login UI (Step 184E), no OAuth, no password
reset, no email verification, no rate limiting, no admin/role concept.

## 110. Route-Level Auth and Trip Owner Isolation -- Every `/trips/*` Route Now Requires Login (Step 184D)

Every one of `app/api/routes/trips.py`'s 18 `{trip_id}`-scoped routes,
plus `POST /trips` and the new `GET /trips`, now require a real,
verified session. **This is the step that actually closes the access-
control gap Step 184A's audit found** ("any caller who knows a
`trip_id` can read or modify it") -- Steps 184B/184C only built the
primitives/persistence/routes for auth itself, never touched a `/trips/*`
route.

**`owner_id` lives on `TripRecord` only, never on `PlanningState`** (a
strict, deliberate boundary -- `PlanningState` has no `owner_id` field
and never will): `TripRecord.owner_id: str | None = None`
(`app/repositories/trip_repository.py`), persisted by both the local_json
`TripRepository` and `PostgresTripRepository` (using the `trips.owner_id`
column Step 183C's/184C's migrations already added -- no new migration
needed this step). `TripRepository.create`/`PostgresTripRepository.create`
both gained an `owner_id: str | None = None` parameter (backward
compatible -- every pre-existing caller/test that doesn't pass one still
works, defaulting to unowned). Both repositories gained
`list_by_owner_id(owner_id) -> list[TripRecord]` -- `GET /trips`'s data
source, verified to return only matching records both against a fake
Postgres session and a real one. `PlanningOrchestrator.create_trip`
gained an `owner_id` parameter, threaded straight through to
`self.trip_repository.create(...)` -- `app/api/routes/trips.py`'s
`create_trip` route always passes `current_user.user_id` there.

**`app/auth/ownership.py`** (new): `require_trip_owner`, a FastAPI
dependency usable as `Depends(require_trip_owner)` on any route with a
`{trip_id}` path parameter -- FastAPI resolves `trip_id` from the URL and
runs the nested `get_current_user` dependency (401 for missing/invalid/
expired session) *before* this function's own body, so authentication and
ownership are both enforced before any route logic runs, including every
generation/regeneration/mutation path. Uses `TripRecord.owner_id` (via
`get_trip_repository()`) as the sole source of truth for both existence
and ownership:
- No `TripRecord` at all -> `trip_not_found_error()` (404) -- **byte-for-
  byte the same 404 behavior every route already had** before this step
  (verified: none of the pre-existing "unknown trip_id" tests needed to
  change).
- `TripRecord.owner_id is None` (a trip created before Step 184D
  existed) -> `forbidden_error()` (403) -- **never silently assigned to
  whichever authenticated user asks for it first.** Documented
  explicitly: pre-auth local dev trips (including Section 183's own live-
  Postgres verification rows) are scratch data and are expected to need
  regenerating under a real account after this step, not to remain
  reachable.
- `TripRecord.owner_id != current_user.user_id` -> the identical 403 --
  a non-owner can never distinguish "unowned" from "owned by someone
  else" from the response.

**Every route wired** (`app/api/routes/trips.py`): `POST /trips` and the
new `GET /trips` (below) use `Depends(get_current_user)` (no existing
trip to check ownership against yet); the other 18 routes all use
`Depends(require_trip_owner)`. This was a mechanical, per-route addition
of one parameter each -- no other line in any of those 18 route bodies
changed, and their existing internal `planning_state is None ->
trip_not_found_error` checks stay in place unchanged as a defensive
fallback (in normal operation `TripRecord`/`PlanningState` are always
created together, so this dependency's own 404 check already covers the
same case).

**`GET /trips` ("My Trips")** (new route + new
`TripListItem`/`TripListResponseData` schemas in `app/schemas/trips.py`):
returns only `current_user`'s own trips (`get_trip_repository().
list_by_owner_id(...)`), each summarized to `trip_id`/`status`/
`primary_destination`/`origin_city`/`start_date`/`end_date`/
`created_at`/`updated_at` -- **never the full `PlanningState`** for any
trip in the list (that stays behind the still-individually-owner-checked
`GET /trips/{trip_id}` and friends). `owner_id` itself never appears in
any API response anywhere -- it's a `TripRecord`-only, repository-
internal field.

**Test client authentication** (`backend/app/tests/conftest.py`): since
essentially the entire existing test suite exercises `/trips/*` through
the shared `client` fixture, that fixture now performs a real
`POST /auth/signup` (a fresh, unique throwaway user every call) before
handing back the `TestClient` -- so every pre-existing test that
creates/reads/mutates a trip via `client` kept passing **completely
unmodified**: it was always implicitly "the trip's owner," it just didn't
need to say so before. A new `second_client` fixture (and the underlying
`new_authenticated_client()` helper, usable directly for a third, fourth,
etc. distinct user) provides a second, distinct logged-in user for cross-
user isolation tests. A new autouse `_configured_session_secret` fixture
sets a real (test-only) `SESSION_SECRET_KEY` for the whole suite now,
superseding the narrower per-file fixtures Step 184B/184C's own auth
tests used, since auth is no longer an auth-specific concern once every
trip route requires it. None of this is a bypass: every session is a real,
signed cookie from a real signup, verified through the same code path
production traffic uses.

Fixing the existing suite for this required exactly 5 pre-existing tests
to adapt (not a design flaw in this step -- each was relying on an
absence this step's new autouse fixture changed): three `test_sessions.py`
tests and one `test_session_auth_config.py` test needed an explicit
`monkeypatch.delenv("SESSION_SECRET_KEY")`/alias-keyed override to prove
their own "secret unset" behavior against the now-ambient test secret (the
established alias-vs-field-name pydantic-settings precedence quirk, same
class of issue Step 182G/183B-FIX/184C already each found once); one
`test_auth_routes.py` test needed a genuinely fresh, never-authenticated
`TestClient` instead of the now-always-logged-in `client` fixture. A
sixth, unrelated latent flakiness was also found and fixed while re-
running everything: `test_verify_session_token_returns_none_for_tampered_
token` flipped only the token's *last* character, which base64url
encoding can render a no-op tamper for certain byte-length/secret
combinations -- fixed to corrupt a whole run of middle characters instead,
confirmed stable across repeated runs.

**Tests** (~190 new: `test_trip_route_auth_enforcement.py` (75 --
parametrized across all 18 `{trip_id}` routes × unauthenticated-401/
wrong-user-403/owner-passes/unowned-legacy-403, plus invalid-session,
expired-session, and logout-then-protected checks), `test_trip_ownership.py`
(10 -- create assigns owner, list-mine isolation, response-shape
unchanged, manual trip_id access of another user's trip), `test_
authenticated_trip_lifecycle_regression.py` (1, consolidated full create
-> generate -> feedback -> lock-blocks-regeneration -> regenerate ->
version-history lifecycle), plus repository-level owner_id/`list_by_
owner_id` tests against both local_json and a fake Postgres session):
every test in the 401/403/owner-passes/unowned-403 matrix was independently
re-run against a real live Postgres in the same session as local_json,
requiring two of the new test files' own helpers to be fixed from
directly importing the local_json singletons to going through
`get_trip_repository()`/`get_planning_state_repository()` (the same
factory the real routes use) -- otherwise they silently wrote to the
wrong backend under `PERSISTENCE_BACKEND=postgres` and produced false
404s instead of the intended 403s. Both existing gated-Postgres smoke
test files (`test_postgres_repositories_integration.py`,
`test_postgres_api_smoke.py`) needed one real `POST /auth/signup` call
added before their first `POST /trips`, for the same reason every other
`/trips/*` caller now needs one.

**Live verification, completed**: `POSTGRES_HOST_PORT=15432 docker
compose up -d postgres` → healthy → `alembic upgrade head` (both
migrations, no new one needed this step) → 117 gated tests (all Step
183D/184C Postgres tests plus every new 184D auth-enforcement/ownership/
lifecycle test) passed together against the real database → independently
confirmed via `psql`: 195 real trips, 153 owned, 42 deliberately-unowned
(from the "legacy trip" test cases), 332 real users → `alembic downgrade
base` reverted both migrations cleanly → `docker compose down` left zero
containers running. `docker compose config` was never used.

The full suite (2750 passed, 10 skipped -- unchanged skip count, up from
2654 passed) runs with the developer's real local `.env` left in place,
untouched; `backend/.data/` confirmed absent before and after the whole
run. `tsc`/`lint`/`build` all clean; zero frontend files touched --
**the frontend cannot currently create or load a trip at all** (it sends
no session cookie, and would 401 on every call), which is an expected,
temporary consequence of this step landing before Step 184E's login UI
and `credentials: "include"` wiring, not a regression to fix here.

Not done in this step, by design: no frontend login/signup/logout UI or
`credentials: "include"` (Step 184E), no roles/admin permissions, no
OAuth, no password reset, no email verification, no rate limiting, no
sessions table (cookies remain stateless), no `owner_id` on
`planning_states` (ownership stays on `trips` only).

## 111. Populated Scraping Source Registry (Step 185B)

New module `backend/app/providers/scraping_source_registry_defaults.py`
populates a real `ScrapingSourceRegistry` (Step 168A's contract, empty by
default) with 14 `ScrapingSourcePolicy` entries covering every
accommodation/flight/ratings source Section 185's audit (185A) named --
see `docs/12_provider_architecture.md` section 64 for the full
classification writeup. Every entry is `enabled=False`; representing a
source and approving it for a future live fetch are different things,
and this step only does the former. `ScrapedAccommodationProvider`/
`ScrapedLocalFlightProvider` now resolve a named `*_MANUAL_HTML_SOURCE`
label's *display name* through this registry instead of a small inline
dict -- output is byte-for-byte unchanged from before this step. The
adapters' own `ScrapingSourcePolicy` for their actual local-file-read
operation stays independently self-declared safe (`enabled=True,
approved_for_personal_use=True`) regardless of brand label, since reading
an already-supplied local file is a categorically different, always-safe
operation from whether that brand's live website is approved for
scraping (it is not, for every real brand). `ScrapingSourcePolicy` gained
one new field, `allows_reviews: bool = False`, mirroring `allows_lodging`/
`allows_flights` for a future ratings parser (Step 185E). Three new
config-only `Settings` fields (`scraped_hotel_ratings_provider_enabled`,
`hotel_ratings_manual_html_source`, `scraped_hotel_ratings_html_path`)
add zero runtime behavior -- `hotel_ratings_provider` still only supports
`"not_connected"`; no manual-ratings adapter exists until Step 185E. No
live fetcher, browser automation, or source-specific parser was added.
Full suite: 2810 passed + 10 skipped (up from 2750), zero frontend files
touched.

## 112. Accommodation Source-Specific Parsers and Multi-Source Ingestion (Step 185C)

New package `backend/app/providers/accommodation/source_parsers/` (7
modules: `generic` + `booking`/`expedia`/`hotelbeds`/`hostelworld`/`vrbo`/
`airbnb`) -- every module a pure HTML-string transform, none opening a
file/socket/requests/httpx/Playwright/Selenium. Each brand module
recognizes the same generic `property-card` micro-format plus an
optional `data-source="<brand>"` per-card marker (untagged cards always
included; a card tagged for a different brand is excluded) via a new
`required_data_source` parameter on `scraped_parser.
parse_scraped_accommodation_html` (default `None` -- every pre-185C
caller unaffected). `get_accommodation_source_parser(source_id)`/
`get_accommodation_source_parser_version(source_id)` resolve a brand to
its module fresh on every call (never a frozen import-time reference),
falling back to `generic` for anything unrecognized.

Six new independent `Settings` fields
(`scraped_accommodation_html_path_booking`/`_expedia`/`_hotelbeds`/
`_hostelworld`/`_vrbo`/`_airbnb`, each defaulting to its own real,
never-auto-created path) plus `resolved_scraped_accommodation_html_
paths_by_source()`. `ScrapedAccommodationProvider` now resolves a full
list of local-file "slots" per call (the original single-file/label slot
plus the six brand slots, deduplicated by resolved path with
deterministic precedence -- see `docs/12_provider_architecture.md`
section 65 for the exact rule), attempts each independently (each own
`try`/`except`-isolated, so one slot's parser raising can never crash
another slot's success), and merges every successful slot's offers into
one result. A new `AccommodationSearchResult.warnings: list[str]` field
(default `[]`, backward compatible) records every missing/empty/failed
source without downgrading an otherwise-successful result. One combined
`query_hash` covers every slot's file identity, so editing any one
source's file never serves a stale cache entry for another source.

`allows_lodging`/`allows_flights`/`allows_reviews` on
`ScrapingSourcePolicy` are unrelated to any of this -- the registry's
`enabled`/`approved_for_personal_use` verdict for each real brand (all
`False`, per Step 185B) is never read to gate whether this provider may
read a local file; that safety declaration is self-made per slot,
independent of brand label, because reading an already-supplied file is
categorically safe regardless of what it's labeled.

117 new tests (90 parser-focused, 13 multi-source-adapter-focused, plus
config/cache-test extensions). Full suite: 2927 passed + 10 skipped (up
from 2810), `tsc`/`lint`/`build` all clean, zero frontend files touched.
Flight and hotel-ratings provider behavior are completely untouched.

## 113. Flight Source-Specific Parsers and Multi-Source Ingestion (Step 185D)

Same architecture as section 112, applied to flights. New package
`backend/app/providers/flights/source_parsers/` (4 modules: `generic` +
`skyscanner`/`google_flights`/`kiwi_manual`) -- every module a pure
HTML-string transform, none opening a file/socket/requests/httpx/
Playwright/Selenium. Each brand module recognizes the existing generic
flight-offer micro-format plus an optional `data-source="<brand>"`
per-card marker (untagged cards always included; a card tagged for a
different brand is excluded) via a new `required_data_source` parameter
on `scraped_parser.parse_scraped_flight_html` (default `None` -- every
pre-185D caller unaffected). `get_flight_source_parser(source_id)`/
`get_flight_source_parser_version(source_id)` resolve a brand to its
module fresh on every call, falling back to `generic` for anything
unrecognized; the adapter remaps the legacy config label `"kiwi"` to the
registry/parser key `"kiwi_manual"` (and `"other"` to `"generic"`)
before calling the selector, so the selector itself never has to
recognize bare `"kiwi"`.

Three new independent `Settings` fields
(`scraped_flight_html_path_skyscanner`/`_google_flights`/`_kiwi_manual`,
each defaulting to its own real, never-auto-created path) plus
`resolved_scraped_flight_html_paths_by_source()`.
`ScrapedLocalFlightProvider` now resolves a full list of local-file
"slots" per call (the original single-file/label slot plus the three
brand slots, deduplicated by resolved path with the same deterministic
precedence rule as section 112 -- see `docs/12_provider_architecture.md`
section 66), attempts each independently (each own `try`/`except`-
isolated), and merges every successful slot's offers into one result. A
new `FlightSearchResult.warnings: list[str]` field (default `[]`,
backward compatible) records every missing/empty/failed source without
downgrading an otherwise-successful result. One combined `query_hash`
covers every slot's file identity, so editing any one source's file
never serves a stale cache entry for another source. When exactly one
slot has a configured path (the common single-source case), the
top-level result `message` is that slot's own precise reason verbatim
rather than a generic multi-source summary, preserving exact message
substrings pre-existing tests already asserted on.

`kiwi_manual` is structurally and behaviorally distinct from the live
`kiwi_mcp` integration: it never imports any `kiwi_mcp` module
(confirmed by an AST-based test), and its own `parse()` asserts every
returned offer's `provider` is never `"kiwi_mcp"` before returning. The
registry's `enabled`/`approved_for_personal_use` verdict for each real
brand (all `False`, per Step 185B) is never read to gate whether this
provider may read a local file, same invariant as section 112.

New tests: 54 parser-focused (`test_flight_source_parsers.py`), 15
multi-source-adapter-focused (`test_scraped_flight_multi_source.py`),
plus config/cache-test extensions and two new E2E tests confirming
multi-source fixtures flow through `ProviderGateway`/
`FlightInventoryService` into `PlanningState` without fabrication. Full
suite: 3007 passed + 10 skipped (up from 2927), `tsc`/`lint`/`build` all
clean, zero frontend files touched. Accommodation, hotel-ratings, and
Kiwi MCP provider behavior are completely untouched.

## 114. Hotel-Ratings Source-Specific Parsers and Multi-Source Ingestion (Step 185E)

Same architecture as sections 112/113, applied to hotel ratings, with
one structural difference: `HotelRatingsProvider.get_ratings` takes a
*list* of identity requests and must return one `HotelRatingLookupItem`
per request (echoing its `offer_id`) rather than building offers from a
single search request -- so this adapter's job splits into (1) parsing
every configured file into a flat list of `ParsedHotelRatingRecord`
(property_name + a fully-built `AccommodationRating`), then (2)
conservatively, exactly matching each record's property name against
each incoming request's `property_name` (`_match_records_to_requests` --
zero or 2+ candidates, even across two different source files, both
resolve to `matched=False, rating=None`, never guessed).

New package `backend/app/providers/hotel_ratings/source_parsers/` (3
modules: `generic` + `tripadvisor`/`google_places_ratings`) -- every
module a pure HTML-string transform, none opening a file/socket/
requests/httpx/Playwright/Selenium. Each brand module recognizes a new
`hotel-rating` micro-format (`property-name`/`rating-value` 0-5
bounded/`review-count`) plus the same optional `data-source="<brand>"`
per-card marker as 185C/185D, via a new `required_data_source` parameter
on the new `scraped_parser.parse_scraped_hotel_ratings_html`. A record
is only produced when a property name is present *and* at least one of
rating-value/review-count parses -- no raw review text is ever
extracted, and no "verified"/ranking claim exists anywhere in this
module. `get_hotel_ratings_source_parser(source_id)`/`get_hotel_ratings_
source_parser_version(source_id)` mirror the flight/accommodation
selection helpers exactly; the adapter remaps the legacy config label
`"google_places"` to the registry/parser key `"google_places_ratings"`
before calling the selector (mirroring flight's `"kiwi"` ->
`"kiwi_manual"`).

Two new independent `Settings` fields
(`scraped_hotel_ratings_html_path_tripadvisor`/`_google_places_ratings`,
each defaulting to its own real, never-auto-created path) plus
`resolved_scraped_hotel_ratings_html_paths_by_source()`, three fields
filling out this provider's previously-incomplete config surface
(`scraped_hotel_ratings_source_id`/`_source_name`/`_base_url`, added in
this step since Step 185B's config-only foundation never included
them), and a dedicated cache gate (`scraped_hotel_ratings_cache_enabled`/
`_cache_ttl_seconds`). `backend/app/providers/hotel_ratings/factory.py`
gained `"scraped_local"`/`"manual_html"` (alias) resolving to the new
`ScrapedLocalHotelRatingsProvider` -- `hotel_ratings_provider` itself
still defaults to `"not_connected"` (unlike accommodation/flight's
`scraped_local` default), matching this step's explicit-opt-in
direction. `ScrapedLocalHotelRatingsProvider` resolves a full list of
local-file "slots" per call (the original single-file/label slot plus
the two brand slots, deduplicated by resolved path with the same
deterministic precedence rule as sections 112/113), attempts each
independently (each own `try`/`except`-isolated), and merges every
successful slot's records into one flat list before matching. A new
`HotelRatingsResult.warnings: list[str]` field (default `[]`, backward
compatible) records every missing/empty/failed source without
downgrading an otherwise-successful result. One combined `query_hash`
covers both the normalized incoming `requests` list *and* every slot's
file identity -- unlike flight/accommodation's stable single search
request, a hotel-ratings request list changes with the underlying
accommodation search, so both must be part of the cache key.

Wiring required zero changes to `AccommodationInventoryService`/
`HotelRatingEnrichmentService`: `build_report` already called
`enrich(result)` unconditionally since Step 177C, and `_resolve_provider`
already called the real `get_hotel_ratings_provider()` factory -- so
setting `HOTEL_RATINGS_PROVIDER=scraped_local` alone activates
conservative enrichment through the exact same never-fuzzy matching
contract that has existed since Step 177C.

New tests: 43 parser-focused (`test_hotel_ratings_source_parsers.py`),
15 single-slot adapter tests (`test_scraped_hotel_ratings_provider.py`),
16 multi-source-adapter-focused (`test_scraped_hotel_ratings_multi_
source.py`), config/factory-test extensions, and 5 new E2E tests
(`test_scraped_hotel_ratings_e2e.py`) proving a matching rating attaches,
an unmatched one does not, a missing file leaves offers unchanged,
`provider_coverage.hotel_ratings` reflects status honestly, and no fake
rating/review-count appears in the API response -- reached by mutating
the real, shared `hotel_rating_enrichment_service` singleton's own
`_provider` attribute, since hotel ratings has no `ProviderGateway`
attribute to patch the way accommodation/flight tests patch `provider_
gateway.accommodation_inventory`/`flight_inventory`. Full suite: 3101
passed + 10 skipped (up from 3007), `tsc`/`lint`/`build` all clean, zero
frontend files touched. Accommodation, flight, and Kiwi MCP provider
behavior are completely untouched.

## 115. `AccommodationSearchResult.hotel_ratings_warnings` -- Closing a Real API-Shape Gap (Step 185F)

Step 185F's frontend work (see `docs/16_frontend_architecture.md`
section 43) found a real, minimal type-contract gap while wiring up a
Developer-view hotel-ratings diagnostic: `HotelRatingsResult.warnings`
(Step 185E) never reached any API response at all.
`HotelRatingEnrichmentService.enrich` only ever copied `status`/
`provider`/`message`/`enriched_offer_count` from the `HotelRatingsResult`
it got back from the provider onto `AccommodationSearchResult` -- the raw
per-source `warnings` list (e.g. "google_places_ratings: no local file
configured/found") was silently dropped every time, since
`HotelRatingsResult` itself is never serialized into any `PlanningState`
field or API response.

Fix: one new field, `AccommodationSearchResult.hotel_ratings_warnings:
list[str]` (default `[]`, `backend/app/models/accommodation.py`),
populated by `HotelRatingEnrichmentService.enrich` copying `ratings_
result.warnings` straight through in both its `model_copy` branches
(the non-success early-return and the success-with-items path) --
never recomputed, filtered, or reworded. Purely additive: no existing
field changed shape, no parsing/matching/provider-selection behavior
changed, and every result built before this step (`hotel_ratings_
warnings` absent from the constructor call) still validates with the
new field defaulting to `[]`. 5 new tests (2 in `test_accommodation_
models.py` confirming the default/construction, 3 in `test_hotel_
rating_enrichment_service.py` confirming the copy-through in both
`model_copy` branches and the empty-list default). Full suite: 3106
passed + 10 skipped (up from 3101). No new backend behavior beyond this
one field -- accommodation/flight parsing, hotel-ratings matching, and
every other provider adapter are completely untouched.

## 116. Async Job Model/Config/Repository Foundation, No Orchestration Yet (Step 186B)

Section 186 (a multi-step audit + build-out, starting with a read-only
audit at Step 186A) is moving `POST /trips/{trip_id}/generate` and
`.../regenerate` from today's fully synchronous, request-response
execution toward async/background jobs, without breaking the MVP along
the way. Step 186B adds only the inert foundation those routes will sit
on top of once a later step (186C) wires them up -- **no route, service,
or background task calls any of this yet**, and `/generate`/`/regenerate`
remain exactly as synchronous as before this step (confirmed by
`backend/app/tests/api/test_async_job_foundation_noop.py`).

**Config** (`backend/app/core/config.py`): three new fields, all with
safe, current-behavior-preserving defaults and no new required env var --
`async_generation_enabled: bool = False` (`ASYNC_GENERATION_ENABLED`),
`generation_job_ttl_seconds: int = 86400` (`GENERATION_JOB_TTL_SECONDS`,
must be `> 0`), and `generation_job_max_running_per_trip: int = 1`
(`GENERATION_JOB_MAX_RUNNING_PER_TRIP`, must be `>= 1`). `Settings.redis_url`
is untouched and still never read by any code path in `backend/app/` --
this step adds no Redis/Celery/RQ usage and no worker process.

**Model** (`backend/app/models/generation_job.py`, new file):
`GenerationJobStatus` (`queued`/`running`/`succeeded`/`failed`/
`cancelled`), `GenerationJobType` (`generate`/`regenerate`), and
`GenerationJob` (`job_id` -- `job_<uuid4 hex>`, mirroring `_new_id`'s
convention elsewhere; `trip_id`; required `owner_id`; `job_type`;
`status`; `progress_stage` -- validated against exactly
`GENERATION_STAGE_KEYS`, the same real stage keys
`PlanningState.generation_progress` already uses, so a job can never
claim a fabricated/cosmetic phase; `message`; `error_code`/
`error_message`; `created_at`/`started_at`/`finished_at`;
`result_version`/`changed_sections`, mirroring
`RegenerateResponseData`'s own fields for a regeneration job). No
percent/ETA field exists here -- percent-complete stays owned entirely
by `PlanningState.generation_progress`. No secret/password/session-
token/API-key field exists. `GenerationJob.status="succeeded"` documents
that it means "the pipeline ran to completion," exactly like today's
`200` from `/generate`, never "travel-ready/final/guaranteed" -- that
judgment stays with `validation_report`/`regeneration_readiness`/
`provider_coverage`, all untouched by this model. Helper functions
(`create_queued_job`, `mark_job_running`, `mark_job_succeeded`,
`mark_job_failed`, `mark_job_cancelled`) mutate-and-return a
`GenerationJob` exactly like `PlanningState.set_pipeline_status` does for
`PlanningState` -- none of them persist anything themselves.

**Repository** (`backend/app/repositories/job_repository.py`, new file):
`JobRepository`, local-JSON-backed exactly like `PlanningStateRepository`/
`TripRepository` -- same file, new `"jobs"` collection, loaded into
memory at construction, whole collection rewritten on every
`create`/`save`. `create`/`save`/`get_by_job_id`/`list_by_trip_id`/
`list_by_trip_id_and_status`/`list_running_by_trip_id` (queued+running
only) round out the `GenerationJobRepositoryProtocol` added to
`backend/app/repositories/protocols.py`. `list_by_trip_id` (and the
status-filtered variants built on it) returns jobs oldest-first by
`created_at` -- matching this codebase's existing audit-trail convention
(`regeneration_attempts`/`version_history`, both read back in append
order) rather than newest-first; documented and tested in
`test_job_repository.py`. Writing the `"jobs"` collection never touches
`"trips"`/`"planning_states"`/`"users"` in the same file
(`LocalJsonStore.write_collection` only ever replaces the one named
collection).

**Factory** (`backend/app/repositories/factory.py`): new
`get_job_repository()`. Deliberately returns the local_json `JobRepository`
singleton **regardless of `Settings.persistence_backend`** -- unlike
`get_trip_repository()`/`get_planning_state_repository()`/
`get_user_repository()`, there is no `PostgresJobRepository` yet. This is
a documented, temporary Step 186B decision (a real Postgres-backed jobs
table/repository is deferred to Step 186F), not an oversight; setting
`PERSISTENCE_BACKEND=postgres` today has zero effect on job storage.
`DATABASE_URL` alone still never selects Postgres for anything, matching
every other repository's existing contract; constructing this module
never opens a DB connection.

**Errors** (`backend/app/schemas/errors.py`/`backend/app/core/errors.py`):
two new codes/constructors, `JOB_NOT_FOUND`/`job_not_found_error` (404)
and `JOB_ALREADY_RUNNING`/`job_already_running_error` (409) -- mirroring
how Step 184B added auth error constructors before Step 184D wired them
into a route. **Neither is raised by any route yet.**

**Tests**: `test_async_generation_config.py` (config defaults/overrides/
validation, plus an explicit proof that `REDIS_URL` never implies async
generation is active), `test_generation_job_models.py` (id prefix,
status/type enums, queued defaults, each `mark_job_*` transition,
progress-stage validation against `GENERATION_STAGE_KEYS`, no secret
fields, no percent/ETA field, serialization round-trip),
`test_job_repository.py` (CRUD, per-trip/per-status filtering, running-
jobs filtering, collection isolation from `trips`/`planning_states`/
`users`, deterministic oldest-first ordering, reload-from-disk, no Redis
import), extensions to `test_repository_factory.py` (`get_job_repository`
returns the local_json singleton both by default and under
`persistence_backend="postgres"`, import never connects), and a new
`test_async_job_foundation_noop.py` proving `/generate`/`/regenerate`
response shapes and behavior are byte-for-byte unchanged and that neither
route ever creates a `GenerationJob`. Full suite: 3152 passed + 10
skipped (up from 3106) -- every pre-existing test, including every
`test_generate_*`/`test_regenerate_*` guardrail test from Sections 163
and 174, passes completely unmodified.

No frontend file was touched. No `/trips/*` route, `PlanningOrchestrator`
method, provider adapter, or auth behavior changed. `docs/17_regeneration_
manual_qa.md`'s safety contract is unaffected. See `README.md`'s "Current
Status" section and `.env.example` for the corresponding user-facing
documentation of this step's scope, and section 6.6 of
`docs/CODEBASE_OVERVIEW.md` for the storage-layer summary.

## 117. Backend Async Generate/Regenerate Job Orchestration Behind ASYNC_GENERATION_ENABLED (Step 186C)

Step 186C wires Step 186B's inert job foundation into
`POST /trips/{trip_id}/generate` and `.../regenerate`, entirely behind
`Settings.async_generation_enabled` (default `False`, unchanged). With
the flag off -- the only state anyone deploying this app has today --
both routes are **byte-for-byte identical** to before this step: same
200/409 response shapes, same error codes, same synchronous blocking
behavior, confirmed by the full pre-186C test suite passing completely
unmodified (`test_generate_*`, `test_regenerate_*`,
`test_async_job_foundation_noop.py` from 186B). This section describes
what changes only when an operator explicitly opts in.

**Executor**: FastAPI's own `BackgroundTasks` (a route parameter,
`background_tasks: BackgroundTasks`) -- no Redis, Celery, RQ, or separate
worker process. Starlette runs a synchronous callable passed to
`background_tasks.add_task(...)` via its thread pool
(`anyio.to_thread.run_sync`), so a real ASGI server's event loop is never
blocked by the fully synchronous `PlanningOrchestrator`/regeneration-
mutation calls underneath. `TestClient` (used by every test in this
section) awaits the full ASGI response cycle -- including the background
task -- before returning control to the calling test, so a dispatched
job has *already run to completion* by the time `client.post(...)`
returns; no sleep/poll loop is needed anywhere in this step's tests to
observe a job's terminal state.

**Route changes** (`backend/app/api/routes/trips.py`):

- `POST /trips/{trip_id}/generate`: when `async_generation_enabled` is
  `True`, calls `generation_job_service.start_generate_job(...)` (owner
  check via `require_trip_owner` already ran before this point, as
  always) and returns `202 Accepted` with a `StartJobResponseData`
  envelope -- via a raw `JSONResponse` returned directly from the route,
  which bypasses FastAPI's `response_model` validation for just this one
  call so the decorator can keep declaring `ApiResponse[TripResponseData]`
  (today's real, default shape) without that declaration ever being
  applied to the differently-shaped async payload. The full
  `PlanningState` is not returned by this call in async mode -- callers
  poll `GET /trips/{trip_id}/jobs/{job_id}` (new) or the pre-existing
  `GET /trips/{trip_id}/generation-progress` instead.
- `POST /trips/{trip_id}/regenerate`: outcomes 1-4 (missing/false
  `confirm`, active locks, no pending feedback, no derivable affected
  stage) are **always synchronous**, completely unchanged, regardless of
  the flag -- `test_async_regenerate.py`'s refusal tests confirm the
  exact same error codes fire with the flag on. Only outcome 5 (Section
  174's real mutation) branches: with the flag on, after the same
  duplicate-job check `/generate` uses, a queued `GenerationJob` is
  created (`job_type=regenerate`, `progress_stage` seeded to the first
  derived affected stage -- a real `GENERATION_STAGE_KEYS` value, never a
  fabricated one) and `202 Accepted` is returned; the real mutation runs
  in the background.

**Extracted mutation helper**
(`backend/app/services/regeneration_mutation_service.py`, new file):
`apply_regeneration_mutation(planning_state, affected_stages,
pending_events) -> RegenerationMutationResult` is Step 174C/174D's
"outcome 5" body, relocated out of the route unchanged -- same calls, in
the same order (`PlanningOrchestrator.rerun_affected_stages` ->
itinerary narrator refresh -> `VersioningService.create_version_after_
feedback` -> mark feedback applied -> recompute `pending_feedback_
summary`/`plan_diff_preview`/`regeneration_readiness`), still raising
(never swallowing) via a new `RegenerationMutationError` when the rerun
itself fails. Both the synchronous route and the async job runner
(`generation_job_service.run_regenerate_job`) call this exact same
function -- no regeneration semantics were rewritten, only relocated so
both callers share one implementation. This works because every service
this function touches (`planning_orchestrator`, `versioning_service`,
`feedback_service`, `plan_diff_preview_service`, `regeneration_readiness_
service`, `itinerary_narrative_service`) is the same process-wide
singleton regardless of which module imports it -- confirmed by every
pre-existing `test_regenerate_guardrails.py` monkeypatch (which patches
attributes on those singleton instances, not on `trips.py`'s module
namespace) still passing unmodified after the relocation.

**Dispatcher** (`backend/app/services/generation_job_service.py`, new
file):

- `check_no_duplicate_running_job(trip_id)` -- raises
  `job_already_running_error(trip_id)` (409 `JOB_ALREADY_RUNNING`) when
  `JobRepository.list_running_by_trip_id(trip_id)` already has
  `>= Settings.generation_job_max_running_per_trip` (default `1`) queued/
  running jobs. Never blocks a different trip, never blocks on a terminal
  (succeeded/failed/cancelled) job, never leaks another user's jobs
  (scoped to `trip_id` only). Applies to both `/generate` and
  `/regenerate`.
- `start_generate_job(trip_id, owner_id, background_tasks)` /
  `start_regenerate_job(trip_id, owner_id, affected_stages,
  applied_feedback_event_ids, background_tasks)` -- run the duplicate
  check, create the queued `GenerationJob` via `JobRepository.create`,
  schedule `run_generate_job`/`run_regenerate_job` via
  `background_tasks.add_task`, and return the queued job so the route can
  build the `202` envelope from it.
- `run_generate_job(job_id)` -- marks the job `running`, calls the exact
  same `PlanningOrchestrator.generate_full_plan`/`generate_full_plan_
  via_langgraph` entry point the synchronous route already calls (no
  stage duplicated, no provider call added; `PlanningState.generation_
  progress` keeps being updated by the orchestrator itself, completely
  unaffected by this step), and marks the job `succeeded` (with
  `result_version`/`changed_sections` from the freshly-generated
  `PlanningState`) or `failed` (fixed, safe `error_code="STAGE_FAILED"`/
  `error_message` -- never the raw exception, never a stack trace) if the
  orchestrator raises. Always reloads its own repository references by id
  rather than trusting anything the dispatching request held, since on a
  real ASGI server this runs after that request has already returned.
- `run_regenerate_job(job_id, affected_stage_values,
  applied_feedback_event_ids)` -- receives plain strings, not live
  objects, and re-derives `affected_stages`/`pending_events` against a
  freshly-loaded `PlanningState` before calling `apply_regeneration_
  mutation`; if the named feedback events are no longer present (a
  defensive check, not expected to trigger in the current single-process
  local_json model where the duplicate-job guard already prevents a
  second regenerate job from racing this one), the job fails safely
  rather than acting on stale data. On success, marks the job `succeeded`
  with `result_version`/`changed_sections` and calls
  `regeneration_attempt_service.record_applied_attempt`, saving
  `planning_state`, exactly like the synchronous route does. On failure
  (`RegenerationMutationError`), records a failed `RegenerationAttempt`
  (`status="failed"`, the same `REGENERATION_NOT_AVAILABLE_MESSAGE`) and
  marks the job `failed` -- no version is created, no feedback is marked
  applied, matching the synchronous path's failure contract exactly.
- `get_job(trip_id, job_id)` / `list_jobs(trip_id)` -- trivial
  trip-scoped reads `backend/app/api/routes/trips.py`'s two new job
  endpoints call directly.

**New response schemas** (`backend/app/schemas/generation_job.py`, new
file): `JobResponseData` (mirrors `GenerationJob` field-for-field, with
one deliberate omission -- `owner_id`, since every job endpoint is
already owner-scoped via `require_trip_owner` before this schema is ever
built), `StartJobResponseData` (structurally identical, used for the
`202` moment), `JobListResponseData`. No secret, stack trace, or another
user's data is ever included.

**New endpoints** (`backend/app/api/routes/trips.py`):

- `GET /trips/{trip_id}/jobs` -- every `GenerationJob` ever created for
  `trip_id`, oldest first (`JobRepository`'s existing documented
  ordering). Always empty with the flag off, since nothing ever creates a
  job in that mode.
- `GET /trips/{trip_id}/jobs/{job_id}` -- one job's current status. A
  `job_id` that exists but belongs to a *different* trip returns
  `JOB_NOT_FOUND` (404), identically to a `job_id` that doesn't exist at
  all -- never leaking that the job exists elsewhere. Both routes use
  `Depends(require_trip_owner)` exactly like every other `/trips/*`
  route, added to `test_trip_route_auth_enforcement.py`'s full matrix
  (now 20 routes, up from 18): unauthenticated -> 401, wrong user -> 403,
  owner -> passes, legacy unowned trip -> 403.

**Tests**: `test_generation_job_service.py` (service-level, bypassing
FastAPI/BackgroundTasks entirely -- duplicate-guard behavior, both
runners' success/failure transitions, safe error messages with no leaked
exception text, `get_job`/`list_jobs` trip-scoping), `test_async_
generate.py` and `test_async_regenerate.py` (full API round-trips:
`202` + job envelope, job reaching `succeeded`/`failed`, `GET
/generation-progress` still working, duplicate-job `409`, terminal jobs
never blocking a new one, sync-mode default explicitly re-confirmed
unaffected), `test_job_endpoints.py` (owner/wrong-user/unauthenticated,
empty-list-before-any-job, cross-trip job isolation, `owner_id` never in
any response), and extensions to `test_trip_route_auth_enforcement.py`.
Every pre-186C regeneration/generation test (`test_regenerate_refusal.py`,
`test_regenerate_guardrails.py`, `test_generation_progress.py`,
`test_regeneration_readiness.py`,
`test_authenticated_trip_lifecycle_regression.py`) passes completely
unmodified. One pre-existing test file needed a small, mechanical update:
`test_itinerary_narrative_generation.py` monkeypatched
`app.api.routes.trips.itinerary_narrative_service` directly (the
regenerate route's own module-level reference, pre-186C) -- since that
call moved into `regeneration_mutation_service.py` unchanged, the test
now patches that module's reference instead. No behavior assertion in
that file changed; it still proves the exact same narrator-success/
narrator-failure/narrator-disabled outcomes for both generate and
regenerate.

No provider/API/scraping behavior changed. No Redis/Celery/RQ/worker
process was added. No Postgres jobs table/repository was added --
`get_job_repository()` still resolves to the local_json singleton
regardless of `Settings.persistence_backend` (Step 186F's job). Frontend
is completely untouched -- polling/loading UX for this new job API is
Step 186D's job, not this one.

## 118. Duplicate-Job, Failure, and Restart Hardening (Step 186E)

Step 186E hardens the async job path Step 186C wired in against four
real MVP gaps: a genuine request-level race around the duplicate-job
check, a background task left permanently "running" if it silently dies
without a process restart, a job left permanently "running" if the
process *does* restart (FastAPI's own `BackgroundTasks` hold no durable
state), and one unhandled-exception path in `run_regenerate_job` that
had escaped Step 186C's own safety contract. All of this is **single-
process** hardening -- true cross-process/multi-worker durable locking
(Redis, a database row lock, etc.) remains explicitly deferred, and
nothing here changes `ASYNC_GENERATION_ENABLED`'s default (`False`).

**1. Duplicate-job race hardening** -- FastAPI runs a sync `def` route
(both `/generate`/`/regenerate` handlers) in a real thread-pool thread
(via anyio), so two near-simultaneous requests for the same `trip_id`
can genuinely interleave between `check_no_duplicate_running_job`'s read
and `start_generate_job`/`start_regenerate_job`'s later write -- both
could see "nothing running" and both create a job. A new per-trip
`threading.Lock` registry (`generation_job_service._trip_lock_registry`,
guarded by its own meta-lock) now wraps the entire reconcile-check-
create sequence in both `start_*_job` functions, keyed by `trip_id` --
shared between generate and regenerate, so the two can't race each
other into double-creating a job for the same trip either. **Live-
verified against a real running `uvicorn` process** (not just
`TestClient`): two genuinely concurrent `POST /generate` requests fired
from separate threads against the same trip_id produced exactly one
`202` and one `409 JOB_ALREADY_RUNNING` with the backend's own existing
friendly message, every time. The lock registry grows by one entry per
distinct `trip_id` ever seen by the process and is never pruned --
acceptable at this local-dev-scale MVP, not a real leak concern.

**2. Stale/interrupted job model** (`backend/app/models/generation_job.py`):
`mark_job_interrupted(job, message=...)` -- reuses `mark_job_failed`'s
exact contract (`status=failed`, `finished_at` set) under a new, honest,
controlled `JOB_INTERRUPTED` error code and a default message ("This
background job was interrupted before it completed... Start generation
again.") that never claims a resumed run or a fabricated success.

**3. Startup/restart recovery** (`backend/app/main.py`, new `lifespan`
context manager; `backend/app/services/generation_job_service.
recover_interrupted_jobs()`): runs once, synchronously, before the app
serves its first request. By construction -- not as a "can't tell whose
job this is, so give up" fallback -- any job already persisted as
`queued`/`running` at that exact moment cannot belong to the process now
starting (which hasn't had a chance to create one yet), so every one of
them is unambiguously left over from a previous process; each is marked
`failed`/`JOB_INTERRUPTED`, never resumed, never faked as succeeded. A
new `JobRepository.list_non_terminal()` (and its
`GenerationJobRepositoryProtocol` counterpart) scans every trip's jobs
at once, since recovery isn't scoped to one trip. **Live-verified**: a
`queued`/`running` job was persisted to the local JSON store, the real
backend process was `kill -9`'d (a hard crash, not a graceful shutdown)
and restarted, and the very next `GET /trips/{trip_id}/jobs/{job_id}`
call showed `status=failed`/`error_code=JOB_INTERRUPTED` -- never
`succeeded`, no stack trace -- and a fresh `POST /generate` for that
same trip immediately succeeded (`202`) afterward. Because `TestClient`
only runs FastAPI's lifespan when used as `with TestClient(app):` (never
on a bare `TestClient(app)`, which this codebase's shared `client`
fixture uses), a dedicated pytest
(`test_lifespan_recovers_interrupted_jobs_on_real_app_startup`)
exercises the *real* `app.main` wiring end to end via that `with` form,
separately from the standalone `recover_interrupted_jobs()` unit tests.

**4. Stale guard before new job** -- `check_no_duplicate_running_job`
now calls a new `_reconcile_stale_jobs(trip_id)` *first*: any `queued`/
`running` job for that trip older than `Settings.generation_job_
stale_after_seconds` (new field, default 3600s/1 hour -- deliberately
separate from `generation_job_ttl_seconds`, which is about how long a
*finished* job stays worth displaying, not how long a job may run before
being considered abandoned) is marked interrupted before the "is
something already running" check runs. This is a narrower, always-on
complement to startup recovery: it catches the rarer case of a
background task that died without the *process* restarting (e.g. some
truly unexpected escape from this module's own try/except). A job
younger than the window, a terminal job, or a different trip's job is
never touched. `GET /trips/{trip_id}/jobs` and `GET /trips/{trip_id}/
jobs/{job_id}` (`generation_job_service.list_jobs`/`get_job`) call the
same reconciliation before returning, so an owner reading a job's status
right after a long-hung background task sees the honest, current
`failed`/`JOB_INTERRUPTED` state rather than a `running` status that
will never change on its own.

**5. Background failure hardening**
(`backend/app/services/generation_job_service.py`): two new helpers,
`safe_job_error_code(exc)`/`safe_job_error_message(exc, fallback=...)`,
exist as a single choke point that can never be handed a raw exception
string/repr/traceback by accident -- both deliberately never read
`exc`'s message/args (which could incidentally contain a file path or
other incidental detail), always returning a fixed, pre-written,
controlled value. `run_generate_job`'s existing `except Exception` now
routes through them (no behavior change, just a documented safety
choke point). `run_regenerate_job` gained a genuinely new top-level
`try/except Exception` wrapping its entire body (previously only
`apply_regeneration_mutation`'s own `RegenerationMutationError` was
caught) -- an unexpected exception from anywhere else in that function
(e.g. `regeneration_attempt_service.record_applied_attempt` failing
*after* an otherwise-successful mutation) would previously have escaped
uncaught, left the job `running` forever, and blocked every future
attempt for that trip until the staleness window or a restart cleared
it. Every failure path already guaranteed (and still guarantees):
`finished_at` set, a controlled `error_code`, no raw exception text, a
generate failure never fabricates `result_version`/a completed plan, and
a regenerate failure never marks feedback applied or creates a version
(`mark_job_failed`/`mark_job_interrupted` never touch `result_version`/
`changed_sections`, which stay at their fresh-job defaults).
`PlanningState.generation_progress` continues to be updated by the
orchestrator exactly as before -- this step adds no new progress
fabrication anywhere.

**6. Job endpoint hardening** -- `require_trip_owner` still gates both
job routes exactly as before (401/403/404 all unchanged); the only
addition is the stale-reconciliation call inside `list_jobs`/`get_job`
described above. `owner_id` remains omitted from the public
`JobResponseData` schema, unchanged.

**7-8. Frontend reload/resume + duplicate/failure UX**
(`frontend/app/page.tsx`): `useJobPolling` gained a `seedJob(job)`
function (sets `activeJob` without starting a new poll) alongside its
existing `waitForJob`/`clearJob`. A new `resumeExistingJobIfAny(tripId)`
helper -- called by both `handleSelectMyTrip` and
`handleLoadExistingTrip`, right after their existing `clearJob()` --
fetches `GET /trips/{tripId}/jobs` (owner-protected, like every other
trip call; a no-op empty list in sync mode) and, if the newest job is
still `queued`/`running`, resumes polling it via the same `waitForJob`
the generate/regenerate flows already use; a terminal newest job is
still seeded so its real status is visible. `loadPlanResult` is still
attempted regardless of the resumed job's outcome -- a failed/cancelled
*regenerate* job never invalidates an already-existing successful plan,
and a trip with no successful generation at all still gets the same
honest "not been generated yet" message it always has. This closes
Step 186D's original "no in-place resumption" MVP-scope note. The
decorative loading plane (`TravelGenerationLoading`) gained a
belt-and-suspenders `isCompleted` guard (`activeJob?.status !==
"failed"/"cancelled"`, alongside the pre-existing `backendProgress?.
status === "completed"` check, which already never reports "completed"
for a run the backend itself marked failed) so it can never show its
"landed" state for a failed/cancelled/interrupted job. `JobStatusCard`
gained one line of retry-affordance copy ("You can start generation
again using the form above.") shown only for a failed **generate** job
-- reusing the existing create-trip button/form rather than adding a new
endpoint or a per-trip retry action; a failed regenerate job has no
equivalent (regeneration needs fresh feedback), so it gets no such hint.
**Live-verified**: a queued job seeded directly into a freshly-restarted
process's store was picked up on page reload via `resumeExistingJobIfAny`
and correctly rendered through to its (in that test, staleness-
reconciled) terminal state; logging out ~200ms into a fresh generate
correctly returned to the login screen with zero leaked trip/job data
and zero new console errors; 375px/390px showed zero horizontal overflow
after a real generate completed.

**Tests**: `test_generation_job_hardening.py` (new, 19 tests --
mutual-exclusion proof of the per-trip lock via externally holding it
and confirming a concurrent `start_*_job` call blocks, shared-lock proof
between generate/regenerate, stale-job reconciliation, startup recovery
including the real-lifespan end-to-end test, `get_job`/`list_jobs`
surfacing reconciled state, the two new safe-error helpers, and the
`run_regenerate_job` top-level-guard regression), plus extensions to
`test_async_generation_config.py` (`generation_job_stale_after_seconds`),
`test_generation_job_models.py` (`mark_job_interrupted`),
`test_job_repository.py` (`list_non_terminal`), and
`test_job_endpoints.py` (owner/wrong-user/recovered-job read paths).
Full suite: 3230 passed + 10 skipped (up from 3197) -- every pre-186E
test, including every existing async job/generate/regenerate test, still
passes completely unmodified.

## 119. Opt-In Postgres Persistence for Generation Jobs (Step 186F)

Extends Step 183D/184C's opt-in Postgres persistence backend
(`Settings.persistence_backend`, still `"local_json"` by default) to
`GenerationJob` records -- the last of the four local-JSON collections
(`trips`, `planning_states`, `users`, now `jobs`) to gain a Postgres
equivalent. `local_json` remains the default in every environment;
`DATABASE_URL` alone still never switches persistence, only an explicit
`PERSISTENCE_BACKEND=postgres` does; `ASYNC_GENERATION_ENABLED` still
defaults `false`; no Redis/Celery/RQ/separate worker process is added --
async execution is still FastAPI's own `BackgroundTasks`, unchanged from
Step 186C.

**1. Migration** (`backend/alembic/versions/
6fabeaa6455e_create_generation_jobs.py`, `down_revision=835e5782d7a5`,
the third Postgres schema migration): creates `generation_jobs` mirroring
`GenerationJob` field-for-field -- `job_id TEXT PRIMARY KEY`, `trip_id`/
`owner_id`/`job_type`/`status`/`created_at TEXT|TIMESTAMPTZ NOT NULL`,
`progress_stage`/`message`/`error_code`/`error_message`/`started_at`/
`finished_at`/`result_version` all nullable, `changed_sections JSONB NOT
NULL DEFAULT '[]'`. Two deliberate deviations from a literal migration
spec, both documented in the migration's own docstring: `result_version`
is `TEXT`, not `INTEGER` -- it mirrors `GenerationJob.result_version:
str | None`, a version *label* like `"v2"`, never a numeric id; `owner_id`
is `NOT NULL` with `ON DELETE CASCADE` (unlike `trips.owner_id`, nullable
+ `SET NULL` for backward compatibility with pre-184C rows) -- a
`GenerationJob` always has a real, required `owner_id` from creation time
(Step 186C), so there is no legacy-row case to preserve, and a job record
has no independent value once its owner is gone. `trip_id` uses `ON
DELETE CASCADE` (matching `planning_states.trip_id`). Indexes on
`trip_id`, `owner_id`, `status`, plus a composite `(trip_id, status)`
index for the running-jobs-per-trip query path. `downgrade()` drops all
four indexes then the table, verified by actually running it against a
real Postgres (see item 8 below). No provider fact, no stack trace, no
secret/password/session-token/API-key column exists, and no change
touches `provider_cache` (stays SQLite, independent).

**2. SQLAlchemy model** (`backend/app/db/models.py`): new
`GenerationJobRow`, same `Base`/`Mapped[...]`/`mapped_column(...)`/
`Index` conventions as `TripRow`/`PlanningStateRow`/`UserRow`. Importing
`app.db.models` now registers four tables on `Base.metadata`
(`trips`, `planning_states`, `users`, `generation_jobs`) -- still no
`create_all` call and no import-time connection anywhere in the module.

**3. Postgres repository**
(`backend/app/repositories/postgres_job_repository.py`, new
`PostgresJobRepository`): implements the exact same
`GenerationJobRepositoryProtocol` surface as the local `JobRepository` --
`create`/`save`/`get_by_job_id`/`list_by_trip_id`/
`list_by_trip_id_and_status`/`list_running_by_trip_id`/
`list_non_terminal`. Opens a short-lived `Session` per call via the
injected/default `get_session_factory()`, never at import time. `create`
and `save` both upsert via `pg_insert(...).on_conflict_do_update(...)`
keyed on `job_id` -- matching `JobRepository.create`/`.save`'s shared
"replace by id" semantics exactly (the local repository's `create` and
`save` are byte-for-byte the same method); `save` additionally excludes
`created_at` from its update set so a job's creation time survives every
subsequent status transition, while `create` includes it (a duplicate
`create()` call for the same `job_id` is, by construction, describing the
same creation event). Every row is validated back through
`GenerationJob.model_validate` on read, so a corrupt/malformed row (e.g.
an invalid status enum value) surfaces a real `ValidationError` rather
than being silently coerced. List methods order by `(created_at,
job_id)` ascending, matching `JobRepository._jobs_for_trip`'s ordering
exactly. `changed_sections` round-trips through the `JSONB` column as a
plain `list[str]`.

**4. Repository factory**
(`backend/app/repositories/factory.py`): `get_job_repository()` now
branches on `Settings.persistence_backend` exactly like
`get_trip_repository()`/`get_planning_state_repository()`/
`get_user_repository()` already did -- `"local_json"` (default) returns
the existing `JobRepository` singleton unchanged; `"postgres"` lazily
constructs and `@lru_cache`s one `PostgresJobRepository`, first
connecting only on the first call made while that setting is active.
Resolved fresh on every call (never captured at import time), same
Step 183B-FIX-derived discipline as every other factory function here.

**5. Startup recovery with Postgres**: no code change was needed in
`generation_job_service.recover_interrupted_jobs()` or `app.main`'s
`lifespan` -- both already call `get_job_repository()` from the factory,
so once the factory resolves to `PostgresJobRepository`, recovery
transparently scans `generation_jobs` instead of the local JSON store.
With `local_json` (the default), behavior is exactly Step 186E's
unchanged. With `postgres` selected, `list_non_terminal()` queries every
`queued`/`running` row across all trips and marks each `failed`
(`JOB_INTERRUPTED`) -- jobs are never resumed, matching the local
behavior's own documented guarantee. If Postgres is selected but the
database is unreachable at startup, `recover_interrupted_jobs()` lets the
underlying SQLAlchemy connection error propagate and fail startup --
consistent with this codebase's existing convention for every other
opt-in Postgres code path (no repository anywhere adds special
down-database handling; a broken opt-in dependency failing loudly, not
silently degrading, is the established pattern). Confirmed end-to-end
against a real Postgres: two jobs (one `queued`, one `running`) seeded
directly via `PostgresJobRepository`, `recover_interrupted_jobs()` called
with `PERSISTENCE_BACKEND=postgres`, both jobs reloaded as `failed`/
`JOB_INTERRUPTED` from a completely fresh repository/session (see
`test_startup_recovery_marks_queued_and_running_postgres_jobs_interrupted`).

**6. Async service compatibility**: `generation_job_service.py` required
zero changes -- every one of its functions already goes through
`get_job_repository()`, never importing `JobRepository` directly, so the
duplicate-running-job guard, the per-trip `threading.Lock` registry, the
stale-job reconciliation pass, and both job runners all work unchanged
against `PostgresJobRepository`. Confirmed live: an async `POST
/trips/{id}/generate` with `PERSISTENCE_BACKEND=postgres` and
`ASYNC_GENERATION_ENABLED=true` creates a job row in `generation_jobs`,
runs it to `succeeded` in the same `BackgroundTasks` cycle
`TestClient` already awaits, and a completely fresh
`PostgresJobRepository` instance reads back the same terminal state.

**7. Tests** (all hermetic by default, matching every other Postgres
test file in this codebase):
- `test_migration_schema_generation_jobs.py` (new, static AST parsing,
  no live Postgres) -- migration file exists and chains after
  `835e5782d7a5`; exactly one new table; expected column list/
  nullability; `result_version` is `Text` not `Integer`;
  `changed_sections` is `JSONB` with an empty-list `server_default`;
  both foreign keys use `ON DELETE CASCADE`; exactly two foreign keys;
  primary key on `job_id`; all four expected indexes exist including the
  `(trip_id, status)` composite; no `provider_cache` table; no secret/
  password/stack-trace-shaped column; `downgrade()` drops all four
  indexes then the table.
- `test_db_models.py` (extended) -- importing `app.db.models` now
  registers exactly four tables; `GenerationJobRow`'s full column list/
  nullability; `changed_sections` is Postgres `JSONB`; `result_version`
  is `Text`; both foreign keys and their `ON DELETE CASCADE` behavior;
  `owner_id` is `NOT NULL` (the deliberate divergence from `TripRow`);
  all four indexes present; no secret/password column; the existing
  no-`create_all`/no-connection static-AST test still passes unmodified.
- `test_postgres_job_repository.py` (new, 13 tests, fake-session unit
  tests -- no live Postgres) -- `create`/`save` upsert statement shape
  and bound params; `save` excludes `created_at` from its update set;
  `get_by_job_id` returns `None` for a missing row and validates a real
  row back through Pydantic; a malformed row (invalid status) raises
  `ValidationError`; `list_by_trip_id`/`list_by_trip_id_and_status`/
  `list_running_by_trip_id`/`list_non_terminal` statement shape and
  ordering; `changed_sections` round-trips; failed/interrupted jobs
  round-trip their error fields; `created_at` is preserved on reload.
- `test_repository_factory.py` (updated) -- replaced the now-outdated
  `test_get_job_repository_returns_local_singleton_even_under_postgres`
  (which asserted the pre-186F "always local_json" behavior) with
  `test_factory_returns_postgres_job_repository_when_selected`, matching
  the existing trip/planning-state/user equivalents; the
  `DATABASE_URL`-alone and dotenv-ignored tests now also assert
  `get_job_repository()` stays local_json in both cases.
- Gated live-Postgres tests, skipped unless `TRAVELOB_RUN_POSTGRES_TESTS=1`
  (new `test_postgres_job_repository_integration.py`, 4 tests; new
  `test_postgres_async_job_api_integration.py`, 4 tests): create/get/
  save/list-running round trip against a real database; startup recovery
  marks queued/running Postgres jobs interrupted; async generate/
  regenerate against `PERSISTENCE_BACKEND=postgres` create and complete a
  real job end-to-end through the actual API; wrong-user job access still
  403; a job looked up under a different trip still 404.
  `test_postgres_opt_in_gating.py` gained two subprocess-based tests
  confirming both new gated files skip (never error, never require a
  database) under a clean environment, matching the existing convention
  for every other gated Postgres test file.
- Regression: default `pytest` remains fully hermetic -- 3269 passed +
  18 skipped (up from 3230 passed + 10 skipped; the 8 new skips are the
  two new gated files' tests, correctly skipped by default). Every
  pre-186F test, including every existing local_json job/generate/
  regenerate test, passes completely unmodified.

**8. Live Postgres verification** (Docker available in this
environment): `POSTGRES_HOST_PORT=15432 docker compose up -d postgres`
→ healthy; `alembic upgrade head` applied all three revisions cleanly
against a fresh database; the 8 gated job tests (both new files) passed
against the real database; the pre-existing gated trip/planning-state/
user/API-smoke suites (20 tests) were re-run against the same database
and still pass, confirming the new migration didn't disturb the earlier
two; `alembic downgrade base` dropped all three revisions' tables
cleanly (including `generation_jobs`); `docker compose down` removed the
container and network. No `docker compose config` was run, no `.env`
was modified or printed, and no secret/session-cookie value was printed
at any point.

**Safety confirmation**: no new/changed code or docs claim fake progress,
a fake ETA, fake provider success, that jobs resume across a restart,
that job success means the itinerary is guaranteed/final/travel-ready,
that Redis/RQ/Celery is active, that async behavior is on by default, or
any change to provider/API/scraping/auth/generation semantics.
`GenerationJobRow`/`generation_jobs` store job control/status metadata
only -- identity, ownership, lifecycle, and a short controlled error
code/message, exactly like the local_json `"jobs"` collection it mirrors.

## 120. Section 186G (final step of Section 186): Full-Stack Final Review and Live Verification

Pure verification and commit-readiness pass over the entire 186A-186F
stack -- no new feature, no `ASYNC_GENERATION_ENABLED` default change,
no Redis/RQ/Celery, no separate worker process, no generation/
regeneration/auth/provider semantics change. Zero backend source files
were edited this step (`compileall` recompiled nothing).

**Config/defaults re-confirmed by direct file read**: `async_generation_
enabled` defaults `False`; `generation_job_ttl_seconds` defaults `86400`
with `gt=0`; `generation_job_max_running_per_trip` defaults `1` with
`ge=1`; `generation_job_stale_after_seconds` defaults `3600` with `gt=0`;
`redis_url` is declared but read by no code path under `app/`
(`docker-compose.yml`'s `redis` service has no `depends_on` consumer in
app code); `database_url` alone never switches `persistence_backend`
(re-confirmed: `Settings._normalize_persistence_backend` only ever reads
the `PERSISTENCE_BACKEND` field, never `database_url`); `PERSISTENCE_
BACKEND=postgres` is required for every `Postgres*Repository`, confirmed
end-to-end again in this step's own live-Postgres run.

**Job model re-confirmed**: `GenerationJob`'s fields are exactly control/
status metadata (identity, ownership, lifecycle, error) -- no provider
fact, API key, session cookie, password, or stack trace field exists;
`new_job_id()` uses the stable `job_<uuid4 hex>` prefix matching every
other id in this codebase; `GenerationJobStatus`/`GenerationJobType`
vocabularies are exactly `queued/running/succeeded/failed/cancelled` and
`generate/regenerate`; `progress_stage`'s field validator rejects any
value outside `GENERATION_STAGE_KEYS` (real backend stages only, `None`
always allowed) -- there is no ETA/percent field on `GenerationJob` at
all, real or fabricated; `mark_job_failed`/`mark_job_interrupted` always
carry a short, pre-written `error_code`/`error_message`, never a raw
exception; `status="succeeded"` is documented, in the model's own
docstring, as pipeline completion only, never a travel-readiness claim.

**Backend async orchestration re-confirmed live** (real `uvicorn`, not
just `TestClient`): with `ASYNC_GENERATION_ENABLED` unset, `POST
/generate` returned the full synchronous `TripResponseData` (no
`job_id`), `POST /regenerate` returned the same synchronous `"applied"`/
refusal shapes as before Section 186, and `GET /trips/{id}/jobs` reads
back an empty list -- byte-for-byte the pre-186 contract. With it set to
`true`: `POST /generate` returned `202` with a queued job envelope; an
immediate duplicate `POST /generate` for the same trip returned `409
JOB_ALREADY_RUNNING`; the job reached `succeeded` with a real
`result_version`/`changed_sections`; `GET /trips/{id}/jobs/{job_id}` and
`GET /trips/{id}/jobs` both work and are owner-protected end to end (a
second signed-up user got `403` on both); a `job_id` looked up under a
different trip_id returned `404 JOB_NOT_FOUND` without revealing it
exists elsewhere; `POST /regenerate` after feedback returned `202`, and
the resulting job also reached `succeeded`. All confirmed via a live
Python HTTP client against a real running backend, not mocked.

**Failure/restart hardening re-confirmed live with a real crash**: a job
was created, then the backend process was sent `SIGKILL` at the earliest
possible moment after the `202` response (killed in-process, no extra
round trip, to catch it before the background task could finish) --
restarting with the *same* `SESSION_SECRET_KEY` (so the existing session
cookie kept working, matching real operator practice across a restart)
showed the job had been marked `failed`/`JOB_INTERRUPTED` by the new
process's startup recovery, with the existing safe, controlled message
(`"This background job was interrupted before it completed... Start
generation again."`) -- never `succeeded`, no raw exception text. A
fresh `POST /generate` for the same trip immediately afterward returned
`202`, confirming the interrupted job never blocks a future attempt.

**Postgres job persistence re-confirmed live**, repeating 186F's exact
verification end to end one more time: `POSTGRES_HOST_PORT=15432 docker
compose up -d postgres` → healthy → `alembic upgrade head` (all three
revisions) → the 8 gated job tests
(`test_postgres_job_repository_integration.py`,
`test_postgres_async_job_api_integration.py`) passed against the real
database → the pre-existing 20 gated trip/planning-state/user/API-smoke
tests were re-run against the same database and still pass → `alembic
downgrade base` dropped all three revisions cleanly → `docker compose
down` left zero containers/networks. No `docker compose config` was run.

**Frontend async UX re-confirmed live via Playwright** (isolated install
under a scratch directory, cached Chromium, never touching the project's
own `package.json`/lock file) -- see `docs/16_frontend_architecture.md`
section 48 for the full writeup, including one honest non-bug finding
(this demo pipeline often completes faster than the 1.5s poll interval,
so the visible `queued`/`running` window can be very brief) and two
pre-existing, out-of-scope stale UI copy strings noticed but
deliberately left unfixed.

**Safety/no-fabrication re-audited**: the full grep suite (identifiers,
banned-phrase safety language, secret patterns) was re-run across
`README.md`/`.env.example`/`frontend/`/`backend/app/`/`backend/alembic/`/
`docs/`/`docker-compose.yml`. Every "guaranteed"/"travel-ready"/
"production-ready"-shaped match is either a negation ("never claims..."),
a test guardrail asserting the word's *absence* from real output, or (one
instance, `docs/14_backend_architecture.md`'s existing "every failure
path already guaranteed" line) a plain-English description of this
codebase's own error-handling guarantee, not a claim about the itinerary
-- left unchanged as unambiguous in context. Every `*_KEY=`/`gsk_`/`sk-`
match is a docs placeholder, an explicit test-only fake token, or (one
new-to-this-audit case) a Playwright test signup email string that
happens to contain the substring `sk-`-adjacent characters purely by
coincidence of a random suffix -- not a credential. Zero real secrets
found anywhere. `git check-ignore -v` reconfirmed `.env`, `backend/.data/
travelobligator_state.json`, and `backend/.data/provider_cache.sqlite3`
all stay correctly gitignored.

**Transparency note, matching this codebase's own established
convention** (e.g. Step 183E's `docker compose config` incident): while
inspecting a live backend process's environment for this step's own
restart-recovery test, a `ps eww` command incidentally printed a
locally-generated, random, throwaway `SESSION_SECRET_KEY` value to this
session's own tool output -- never the real project `.env`'s secret,
never written to any file, and of no value outside that one ephemeral
verification process (which was killed and never reused). Recorded here
for the same reason 183E's note exists: so the record is honest about
exactly what happened, even though nothing sensitive was actually
exposed by it.

**Full suite**: 3269 passed + 18 skipped, unchanged from 186F.
`compileall`/`tsc`/`lint`/`build` all clean. `git check-ignore` clean.
**Section 186 (186A-186G) is verified ready to commit as a single
stack.** Nothing committed by this step.

## 121. Structured Logging Foundation (Step 187B)

Following Step 187A's read-only audit (which found no central logging
config anywhere, ~15 modules calling bare `logging.getLogger(__name__)`
with only `logger.warning(...)`, no request-id correlation, and no
APM/tracing dependency of any kind), this step adds a stdlib-only
structured-logging *foundation* -- it renders the codebase's existing
log calls as JSON, without adding a single new logging call site or
changing any request/response/provider/auth/persistence/async-job
behavior. **No OpenTelemetry, Elastic APM, Sentry, Datadog, or
structlog dependency was added** -- `backend/requirements.txt`/
`requirements-dev.txt` are untouched. **No external log shipping
exists** -- every log line still only ever reaches this process's own
stdout. **No request-id correlation exists yet** -- that is Step 187C's
job; `ResponseMetadata.request_id` is completely unchanged by this step.

**New module** (`backend/app/core/logging_config.py`):
- `JsonFormatter(logging.Formatter)` renders one JSON object per log
  line: always the safe default fields (`timestamp`/`level`/`logger`/
  `message`/`module`/`function`/`line`), plus any name present on the
  record from a fixed `ALLOWED_EXTRA_FIELDS` allowlist (`request_id`
  reserved for 187C, `trip_id`/`user_id`/`owner_id`/`job_id`/`job_type`/
  `status`/`stage`/`provider`/`error_code`/`duration_ms`), plus a
  bounded (4000-char-capped) rendering of `record.exc_info` *only* when
  an existing call site already passed `exc_info=True` -- this module
  never sets `exc_info` itself and adds no new exception-logging call
  site. Never touches `record.__dict__` wholesale; only the named
  allowlist fields are ever read off a record, so any other attribute
  (standard `LogRecord` internals like `process`/`thread`/`args`, or an
  arbitrary `extra=` key a call site might pass) is silently dropped.
- `SENSITIVE_LOG_FIELD_NAMES` (`password`, `password_hash`, `session`,
  `session_cookie`, `cookie`, `authorization`, `token`, `secret`,
  `api_key`, `request_body`, `response_body`, `provider_payload`,
  `planning_state`, `itinerary`) plus `_is_sensitive_field_name`'s
  `*_secret_key`/`*_api_key` suffix matching are a defense-in-depth
  denylist -- the primary guard is that `ALLOWED_EXTRA_FIELDS` simply
  never contains a sensitive name to begin with, enforced by a
  module-load-time `assert` that fails loudly (at import time, not
  silently at runtime) if the two sets ever overlap.
- `configure_logging(*, log_level=None, structured_logging_enabled=None)`
  attaches one `StreamHandler(stdout)` to the shared `"app"` logger
  namespace (every module's `logging.getLogger(__name__)` call, e.g.
  `"app.services.generation_job_service"`, is a dotted child of `"app"`,
  so this one handler captures all of them) with `propagate=False` --
  deliberately never touching the root logger or Uvicorn's own
  independently-configured `"uvicorn"`/`"uvicorn.error"`/
  `"uvicorn.access"` loggers, so Uvicorn's own startup/access logging is
  completely unaffected either way. Idempotent: a repeated call finds
  its own previously-attached handler by name (`_HANDLER_NAME`) and
  reconfigures it in place (level/formatter) rather than adding a
  second one, so no log record is ever emitted twice regardless of how
  many times this is called. With no arguments, reads
  `Settings.log_level`/`Settings.structured_logging_enabled` (imported
  lazily inside the function to avoid a config/logging import cycle).

**Config** (`backend/app/core/config.py`): `log_level` (default
`"INFO"`, alias `LOG_LEVEL`, normalizes any unrecognized value to
`"INFO"` rather than raising -- same fallback convention as
`persistence_backend`/`session_cookie_samesite` elsewhere in this file)
and `structured_logging_enabled` (default `True`, alias
`STRUCTURED_LOGGING_ENABLED` -- `False` swaps in a plain, single-line,
human-readable formatter instead of JSON; neither value changes *what*
is logged, only *how* it is rendered).

**Startup wiring** (`backend/app/main.py`): `configure_logging()` is
called once, at module import time, immediately after `settings =
get_settings()` and before the `FastAPI(...)` app object or any
exception handler/route is constructed -- so structured logging is
active before the app can ever handle a request. No route body, no
exception handler, and no async-job code path was edited to add a new
`logger.*` call; only the *rendering* of calls that already existed
before this step changed.

**Tests** (`backend/app/tests/core/test_logging_config.py`, 45 new):
`JsonFormatter` emits valid JSON with every default field present;
every `ALLOWED_EXTRA_FIELDS` name round-trips when present and is
absent (never crashes) when not; an arbitrary unallowlisted field name
is dropped; every `SENSITIVE_LOG_FIELD_NAMES` name plus
`session_secret_key`/`groq_api_key`/`anthropic_api_key` (suffix-matched)
is dropped, and the secret *value* never appears anywhere in the
rendered JSON either; `request_body`/`response_body`/`provider_payload`/
`planning_state`/`itinerary` are dropped; standard `LogRecord`
attributes (`process`/`thread`/`pathname`/`args`/`msg`) never leak
through; a real exception's `exc_info=True` rendering is bounded and
never exposes an unallowlisted extra field set alongside it on the same
record; the allowlist/denylist can never overlap (asserted both at
import time and re-checked by a dedicated test); `Settings.log_level`
defaults to `"INFO"`, accepts every valid level (case-insensitive), and
normalizes an invalid value to `"INFO"` without raising;
`Settings.structured_logging_enabled` defaults to `True`;
`configure_logging()` is idempotent (repeated calls never produce more
than one matching handler, and never emit a duplicate log line for the
same `logger.warning(...)` call, confirmed via `capsys`); the plain-text
fallback (`structured_logging_enabled=False`) is genuinely not valid
JSON; log-level filtering genuinely suppresses a below-threshold call;
and importing `app.main` (which now also runs `configure_logging()`)
prints no password/secret/cookie/API-key-shaped string to stdout or
stderr.

**Regression**: full suite 3314 passed + 18 skipped (up from 3269 + 18
-- the 45 new tests, zero change to any pre-existing test's outcome).
`compileall`/`tsc`/`lint`/`build` all clean. No frontend file touched.
`ResponseMetadata.request_id` generation, every provider adapter's
return value, every auth/persistence code path, and the entire
async-job model/service/repository stack are all byte-for-byte
unchanged -- confirmed by the full suite passing unmodified and by
`git diff --stat` touching only `backend/app/core/config.py` (two new
fields plus a validator), the new `backend/app/core/logging_config.py`,
`backend/app/main.py` (the one new `configure_logging()` call), this
test file, and docs/`.env.example`.

## 122. Request-Scoped Correlation ID (Step 187C)

Step 187A found `ResponseMetadata.request_id` generated fresh,
independently, at response-serialization time -- never threaded into
any log line. This step makes one id shared by the response body's
`metadata.request_id`, the response's `X-Request-Id` header, and every
structured log line emitted while that request's route ran. **No
OpenTelemetry/Elastic APM/Sentry/Datadog dependency was added**
(`requirements.txt` untouched); **no external log shipping exists**
(still only this process's own stdout); **a request id is a
correlation label for reading logs next to a response, never a
security token, never proof of identity, and an incoming header is
never trusted as an authorization signal** -- see the safety notes
below. Provider/gateway logging and auth-event logging remain
un-added -- still deferred to later steps.

**New module** (`backend/app/core/request_context.py`): one
`contextvars.ContextVar` (`_current_request_id`), holding exactly one
short id string and nothing else -- the module's own docstring is
explicit that it must never be extended to also carry a user id,
cookie, token, secret, or request body. `new_request_id()` generates
`req_<uuid4 hex>`, the exact format `ResponseMetadata.request_id`
already used before this step. `is_safe_request_id(value)` accepts
only a non-empty string, at most `MAX_REQUEST_ID_LENGTH` (128)
characters, matching `^[A-Za-z0-9_.:-]+$` -- no whitespace, no
newlines, no control characters, no characters that could inject an
extra field/line into a log line or corrupt the response header.
`normalize_request_id(value)` returns `value` unchanged if safe,
otherwise a fresh `new_request_id()` -- an unsafe or missing incoming
value is always silently replaced, never rejected with an error (a
malformed correlation header must never break a request). `get_
current_request_id()`/`set_current_request_id()`/`reset_current_
request_id()` mirror `ContextVar.get`/`.set`/`.reset` exactly;
`request_id_scope(value)` is a convenience context manager wrapping
the same set/reset pair with a guaranteed reset on exception.

**New middleware** (`backend/app/core/request_id_middleware.py`):
`RequestIdMiddleware.dispatch` reads the incoming `X-Request-Id`
header (nothing else -- never a cookie, never `Authorization`, never
the request body), calls `normalize_request_id` on it, sets the
context var for the duration of `call_next(request)` inside a
`try`/`finally` (so the context is always reset, even on an
exception, even though the exception itself is never swallowed), and
sets the same id as the response's `X-Request-Id` header (`Mutable
Headers.__setitem__` replaces rather than duplicates, so a response
never carries two `X-Request-Id` lines). Wired into `app.main` via
`app.add_middleware(RequestIdMiddleware)` **after**
`app.add_middleware(CORSMiddleware, ...)` -- Starlette's middleware
stack wraps in reverse-of-add order, so this ordering nests
`RequestIdMiddleware` inside CORS but *outside* Starlette's own
`ExceptionMiddleware`, meaning the same id is active while `AppError`/
`RequestValidationError`'s registered handlers run too, not just for a
fully successful route body. A truly unhandled exception (no
registered handler) still propagates past this middleware exactly as
before this step -- `RequestIdMiddleware` never catches or converts an
exception, only ensures the context resets either way.

**Logging integration** (`backend/app/core/logging_config.py`): a new
`RequestIdLogFilter(logging.Filter)` reads `get_current_request_id()`
and sets `record.request_id` *only* when the record doesn't already
carry one (an explicit `extra={"request_id": ...}` from a future call
site is never overridden) and *only* when a value exists (outside any
request -- a background job, a startup log, a plain script -- the
record is left alone, so `JsonFormatter`'s existing `hasattr` check
simply omits `request_id` from that line rather than inventing or
reusing a stale one). `configure_logging()` attaches this filter to
its own handler exactly once, idempotently, the same way it already
guards against attaching a duplicate handler. No existing `logger.
warning(...)` call site needed to change -- every one of them now
automatically carries the current request's id when one exists, with
zero code change at the call site itself.

**Response metadata integration**
(`backend/app/schemas/api_responses.py`): `ResponseMetadata.
request_id`'s `default_factory` is now `get_current_request_id() or
new_request_id()` -- inside a request, reuses the exact id
`RequestIdMiddleware` already set (and already echoed as the response
header); outside a request (a test/utility constructing
`ResponseMetadata`/`ApiResponse` directly, exactly as before this
step) falls back to generating a fresh one, so nothing that worked
before this step can break. `success_response()`/`error_response()`
(`core/response.py`) both construct `ApiResponse[...]` without passing
`metadata` explicitly, so this `default_factory` runs for every
response built through either path -- success, `AppError`, and
`RequestValidationError` responses alike all pick up the same
request-scoped id automatically, with no change needed in `app.main`'s
two exception handlers themselves.

**Tests**: `test_request_context.py` (new, 36 tests) -- id generation/
uniqueness/format; `is_safe_request_id` accepting valid ids and
rejecting `None`/empty/too-long/every tested whitespace-or-control-
character shape; `normalize_request_id` honoring a safe value and
replacing every unsafe one (always with a fresh, safe, `req_`-prefixed
id); get/set/reset round-tripping; `request_id_scope` setting,
resetting, resetting-on-exception, and correctly restoring an outer
value after a nested scope exits. `test_logging_config.py` (extended,
7 new tests) -- the filter injects the current id, leaves a record
alone outside any request, never overrides an explicit `extra` value;
`JsonFormatter` omits `request_id` entirely (never `null`, never a
stale value) when none is active; a real `configure_logging()` +
`capsys` round trip confirms the JSON line does/doesn't carry
`request_id` inside/outside a scope respectively; the filter is never
attached twice across repeated `configure_logging()` calls.
`test_request_id_correlation.py` (new, `backend/app/tests/api/`, 14
tests, real `TestClient` requests) -- no incoming header still yields
a matching generated id on both the header and (implicitly, via later
tests) metadata; a safe incoming header is echoed back verbatim;
every tested unsafe shape (spaces, newlines, null bytes, over-length)
is replaced, and the original unsafe value never reaches the response
header or body in any form; a response never carries two
`X-Request-Id` header lines; a real 200 success response, a real 404
`AppError` response, and a real 422 validation-error response all have
`metadata.request_id == X-Request-Id header`; a real pre-existing
`logger.warning` call's captured `LogRecord` carries the exact id of
the one request under test (isolated from the create/generate setup
calls before it, which each correctly carry their *own*, different
ids -- an early draft of this test conflated three separate requests'
ids and correctly failed on itself, since each request must always
get its own id); two sequential requests never share an id; an
`Authorization` header sent alongside a safe `X-Request-Id` never
leaks into the response.

**Regression**: full suite 3371 passed + 18 skipped (up from 3314 +
18 -- the 57 new tests, zero change to any pre-existing test's
outcome). `compileall`/`tsc`/`lint`/`build` all clean. No frontend
file touched. Every provider/auth/persistence/async-job code path is
completely unchanged -- confirmed by the full suite passing unmodified
and by `git diff --stat` touching only the two new core modules above,
`api_responses.py`'s one `default_factory` line, `main.py`'s one new
`add_middleware` call, the three test files, and docs/README/
`.env.example` (`.env.example` needed no change -- no new config
surface was added).

## 123. Structured Async-Job Lifecycle Logs (Step 187D)

Makes the async generate/regenerate job path (Step 186B-186F) fully
observable through the structured logging foundation (Step 187B) and
request-correlation (Step 187C) already in place -- no job/duplicate/
startup-recovery *behavior*, response shape, or frontend behavior
changed. Every log line added or enriched by this step uses only
`app.core.logging_config.ALLOWED_EXTRA_FIELDS` names already reserved
since Step 187B (`trip_id`/`owner_id`/`job_id`/`job_type`/`status`/
`stage`/`error_code`/`duration_ms`) -- the allowlist itself needed no
change. Nothing here logs a raw exception string into a structured
field, a `GenerationJob`/`PlanningState` model dump, a request/response
body, feedback text, or provider data.

**New helper** (`backend/app/services/generation_job_service.py`):
`_job_log_fields(job, *, status=None, error_code=None)` builds the
allowlisted `extra=` dict from a `GenerationJob`'s already-persisted
fields -- `status`/`error_code` default to `job.status.value`/
`job.error_code` (a caller only overrides them for the log line that
fires immediately before `mark_job_failed`/etc. would otherwise update
`job` in place); `stage` is included only when `job.progress_stage` is
set (never a fabricated phase); `duration_ms` is derived from
`job.started_at`/`job.finished_at` and included only once both are
real, persisted timestamps (i.e. only for a terminal job) -- never
estimated or fabricated. A field with no real value is omitted from
the dict entirely, never emitted as `null`, matching `JsonFormatter`'s
own `request_id`-omission convention from Step 187C.

**Lifecycle logs added** (all `logger.info`, all placed immediately
after the state transition they describe was already persisted):
`start_generate_job`/`start_regenerate_job` log `"...queued..."` right
after `get_job_repository().create(job)`; `run_generate_job`/
`run_regenerate_job` log `"...started..."` right after
`mark_job_running`+save, and `"...succeeded..."` right after
`mark_job_succeeded`+save. Two previously-silent clean-refusal paths in
`run_regenerate_job` (the trip no longer existing, or the trip's
pending feedback/affected stages having changed by the time the
background task ran -- both legitimate, expected outcomes, never an
exception) now also log `info` on failure, closing a real observability
gap Step 187A's audit found (these two paths previously logged
*nothing at all*).

**Failure logs preserved, enriched** (`logger.warning(...,
exc_info=True)`, unchanged level/message/semantics from Steps 186C/
186E): all three exception-triggered failure sites (`run_generate_job`'s
`except Exception`, `run_regenerate_job`'s `except
RegenerationMutationError` and its outer `except Exception`) were
reordered to call `mark_job_failed(...)` *before* logging, purely so
`_job_log_fields` can read the now-final `finished_at` and include a
real `duration_ms` -- every other side effect (which repository save
happens, what `error_code`/`error_message` get persisted, the response
the caller ultimately sees) is byte-for-byte unchanged; only the
relative order of an in-memory mutation and its own log line moved.
`safe_job_error_code`/`safe_job_error_message` (Step 186E, unchanged)
still guarantee the raw exception text never reaches `job.error_message`
-- and now, verified by a new test, never reaches the *structured* log
fields either (the real traceback still legitimately reaches
`exc_info`, server-side stdout only, exactly as before this step --
see `docs/14_backend_architecture.md` section 118's own note on this
being standard, intentional practice, not a leak).

**Duplicate-job rejection** (`check_no_duplicate_running_job`): gained
an optional `attempted_job_type: GenerationJobType | None = None`
keyword-only parameter (both callers now pass it; existing tests
calling it with just `trip_id` keep working unchanged) purely to enrich
the new `logger.warning` emitted on rejection -- never changes which
trip/jobs are checked or whether the call raises. The rejection log's
`job_id`/`status`/`owner_id` describe the *blocking* job (the one
already `queued`/`running`), which is always safe to log here because
`start_generate_job`/`start_regenerate_job` are only ever reached
through an owner-protected route, so that blocking job necessarily
belongs to the same trip/owner already gating this request -- never a
different user's job. `error_code="JOB_ALREADY_RUNNING"` on the log
line matches the response's own `ApiError.code` exactly.

**Interrupted/stale recovery, enriched, no new call site count
change**: `_reconcile_stale_jobs`'s existing warning (Step 186E)
gained structured `extra` fields describing the now-interrupted job.
`recover_interrupted_jobs`'s existing aggregate warning (a job *count*,
with no way to trace which specific job/trip it covered) is unchanged;
a new per-job `logger.info` inside its loop closes that gap with real
`job_id`/`trip_id`/`error_code="JOB_INTERRUPTED"` fields, without
touching the aggregate line's own text or level.

**Route-level failure log enriched**
(`backend/app/api/routes/trips.py`): the one pre-existing
`logger.warning(..., exc_info=True)` on the *synchronous* regenerate
failure path (Step 174C, reached only when
`Settings.async_generation_enabled=False` -- no `GenerationJob` exists
in this branch at all) gained `extra={"trip_id": ..., "status":
"failed", "error_code": "REGENERATION_NOT_AVAILABLE"}` -- same
message/level/`exc_info` behavior as before. `exc.planning_state` is
still read (unchanged) only to build the `RegenerationAttempt` audit
record, never logged itself.

**Request correlation, verified empirically, not assumed** (Step 187C
integration -- the task's own explicit instruction here was "test
honestly," so this was checked against a real running `uvicorn`
process, not just reasoned about): a request-time log (e.g. the
`"...queued..."` line, emitted synchronously inside the route) carries
the same `request_id` as the response's `X-Request-Id` header, as
expected. **More surprisingly, so does a background job's own log
line** (the `"...started..."`/`"...succeeded..."` lines, which run
inside `run_generate_job`/`run_regenerate_job` via
`BackgroundTasks.add_task`, scheduled on Starlette's own thread pool) --
confirmed both via `TestClient` and via a real `uvicorn` process
(request and matching log lines captured directly from stdout,
matching `request_id` values byte-for-byte). This works because
`RequestIdMiddleware`'s `set_current_request_id` call happens *before*
`BaseHTTPMiddleware` spawns the inner app as its own anyio task (which
copies the current `contextvars.Context`), and `anyio.to_thread.
run_sync` (what both a sync FastAPI route and a sync `BackgroundTask`
callable run through) itself copies that same context into the worker
thread -- not a guarantee this step invented, just an accurate
description of how Starlette/anyio's existing context-propagation
already behaves, verified rather than assumed. Startup recovery
(`recover_interrupted_jobs`, called from `app.main`'s `lifespan` before
any request) and stale-job reconciliation triggered by an unrelated
later request both correctly show *no* `request_id` field at all on
their log lines (never `null`, never a stale value from an earlier
request) -- confirmed live and by a dedicated test. The job model
itself was **not** expanded to carry a request id (the task's own
"prefer not to" default) -- it was never needed, since the existing
context-propagation mechanism already makes background logs correctly
correlated without it; every background log line remains independently
traceable via `job_id`/`trip_id`/`job_type`/`status` regardless.

**Tests** (`backend/app/tests/services/test_generation_job_logging.py`,
new, 11 tests, plus `trips.py`'s regenerate-refusal warning covered in
the same file): queued/running/succeeded logs for both job types carry
the expected fields (verified as real, allowlist-only JSON via
`JsonFormatter`); a triggered background failure carries `error_code=
"STAGE_FAILED"` and a real `duration_ms`, with the secret-looking
exception text confirmed absent from the *structured* field set
specifically (not from `exc_info`, which legitimately still carries it
server-side); duplicate rejection carries `JOB_ALREADY_RUNNING` plus
the blocking job's own identity; startup recovery and stale
reconciliation both carry `JOB_INTERRUPTED` with real job/trip
identity; the synchronous regenerate-route failure path carries
`REGENERATION_NOT_AVAILABLE` with no `job_id` (none exists in that
branch); the empirical request-correlation checks described above
(both the full HTTP-driven case and a minimal direct
`request_id_scope` case); and a dedicated check that none of
`password`/`session`/`cookie`/`authorization`/`token`/`secret`/
`api_key`/`request_body`/`response_body`/`provider_payload`/
`planning_state`/`itinerary` is ever set as an attribute on any
job-lifecycle `LogRecord` this step produces.

**Regression**: full suite 3382 passed + 18 skipped (up from 3371 + 18
-- the 11 new tests, zero change to any pre-existing test's outcome,
including every pre-existing async-job/duplicate/hardening/regenerate
test, confirming the `mark_job_failed`-before-`logger.warning`
reordering changed no observable job behavior). `compileall`/`tsc`/
`lint`/`build` all clean. No frontend file touched. No new dependency,
no `ALLOWED_EXTRA_FIELDS`/`SENSITIVE_LOG_FIELD_NAMES` change, no
request/response shape change, no duplicate-job/startup-recovery/
provider/auth/persistence behavior change -- confirmed by `git diff
--stat` touching only `generation_job_service.py`, `trips.py`'s one
warning call, `logging_config.py`'s docstring (no code change), the
one new test file, and docs.

## 124. Secret-Safe Auth Event Logs (Step 187E)

Makes signup/login/logout/session-verification observable through the
same structured logging foundation (Step 187B) and request-correlation
(Step 187C) already used for async jobs (Step 187D) -- no auth
behavior, password hashing, session signing, cookie setting, user
repository behavior, or response shape changed. Auth had almost no
deliberate logging before this step (confirmed by Step 187A's audit).

**New allowlisted field**: `app.core.logging_config.ALLOWED_EXTRA_FIELDS`
gained exactly one new name, `auth_event` (`"signup"`/`"login"`/
`"logout"`/`"session_verify"` only) -- never an email address; `email`
is deliberately never added to the allowlist anywhere in this codebase.
Every other field this step logs (`user_id`, `status`, `error_code`,
`request_id`) was already allowlisted since Step 187B/187C. `error_code`
values used here (`USER_ALREADY_EXISTS`, `INVALID_CREDENTIALS`,
`SESSION_INVALID`, `SESSION_EXPIRED`, `USER_NOT_FOUND`) are plain,
short, controlled strings -- not new members of the response-facing
`schemas.errors.ErrorCode` enum (mirrors Step 187D's own
`JOB_INTERRUPTED_ERROR_CODE` precedent: a log-level error code and an
API-response error code are deliberately separate vocabularies, so
adding or renaming one never risks changing the other).

**Signup** (`app.auth.service.signup`): `logger.info` on success
(`status="succeeded"`, `user_id` from the newly-created account) and
`logger.warning` on `UserAlreadyExistsError` (`status="rejected"`,
`error_code="USER_ALREADY_EXISTS"`, no `user_id` -- none is created).
Neither line ever includes `request.email`, `request.password`, or a
password hash; `email_already_registered_error()`'s own response
behavior (409, generic "already exists" message, field="email") is
completely unchanged.

**Login** (`app.auth.service.login`): `logger.info` on success
(`user_id` from the matched account) and `logger.warning` on rejection
(`error_code="INVALID_CREDENTIALS"`). The rejection log fires from the
exact same `if existing is None or not verify_password(...)` branch
`invalid_credentials_error()` already raised from before this step --
there is no separate code path for "unknown email" vs. "wrong
password" for a log line to accidentally diverge from, so the log
layer inherits the same non-distinguishing guarantee the response
already had, by construction, not by a separate check. Verified live
and by a dedicated test that both cases produce byte-identical
structured log field sets (minus `request_id`, which differs per
request as designed).

**Logout** (`app.api.routes.auth.logout_route`): `logger.info`,
`status="succeeded"`, deliberately **no** `user_id` -- this route takes
no `Depends(get_current_user)` by design (logout must always succeed
even with an invalid/expired/missing cookie or an unconfigured
secret), and adding a session-verification step purely to attach a
`user_id` to a log line would change that guarantee. No cookie value,
no session token, no signed payload logged.

**Session verification** (`app.auth.sessions.verify_session_token`,
`app.auth.dependencies.get_current_user`): `verify_session_token`'s
*return-value contract is completely unchanged* (`str | None`, `None`
for missing/malformed/tampered/expired alike -- the response a caller
sends back, via `authentication_required_error()`, still never
distinguishes any of these, exactly as that function's own docstring
requires). Internally, `itsdangerous.SignatureExpired` (a subclass of
`BadSignature`/`BadData`) is now caught before the broader `BadData`
catch, purely so a server-side log line can tell "expired"
(`status="expired"`, `error_code="SESSION_EXPIRED"`) apart from
"tampered/malformed" (`status="invalid"`,
`error_code="SESSION_INVALID"`) -- the token value and signed payload
are never logged either way. `get_current_user` additionally logs a
`status="invalid"`/`error_code="USER_NOT_FOUND"` warning (with
`user_id`, since it's the one piece of real information this case
reveals) for the rare case where a session verifies but the account no
longer exists (a deleted user's still-valid cookie) -- the response
stays the same generic 401 either way. Because `get_current_user`
backs `app.auth.ownership.require_trip_owner` (used by every
`/trips/*` route, Step 184D) as well as `GET /auth/me`, a
tampered/expired/phantom-user session is exactly as observable
regardless of which route triggered the check.

**Noise-level decision, documented explicitly in code**: a *missing*
cookie is never logged anywhere in this step -- it is the normal,
extremely common "not logged in yet" state (hit on every
unauthenticated page load, most visibly `GET /auth/me`'s own
once-per-page-load session check), and logging it would make that
completely ordinary case noisy for no operational benefit. This is a
deliberate choice, not an oversight -- `app.auth.dependencies`' own
module docstring and `verify_session_token`'s docstring both explain
it, and a dedicated test confirms zero log lines are produced for a
missing-cookie request.

**Tests** (`backend/app/tests/auth/test_auth_logging.py`, new, 11
tests; `test_logging_config.py` gained 1 more confirming `auth_event`
is allowlisted and `email` is not): signup success/duplicate-rejection
fields (via a real `TestClient` round trip, confirming `request_id`
matches the response's `X-Request-Id` header); login success/rejection
fields, including the dedicated no-distinction check described above;
logout fields, confirming no `user_id` and that the response's
`Set-Cookie` header value never appears anywhere in the rendered log
line; a tampered-token case (reusing `test_sessions.py`'s own
multi-character-corruption technique) confirming `SESSION_INVALID`; a
real-TTL-expiry case (short `session_ttl_seconds` + `time.sleep`,
matching `test_sessions.py`'s existing convention) confirming
`SESSION_EXPIRED`; a missing-cookie case confirming *zero* log records;
an unauthenticated `GET /auth/me` case (via a bare, never-signed-up
`TestClient` -- the shared `client` fixture used everywhere else in
this file already performs a real signup, so it can't exercise a
genuinely unauthenticated request) confirming both the 401 response
body and the zero-noise log outcome are unchanged; a deleted-user
session case confirming `USER_NOT_FOUND`; and a real-user/valid-session
case confirming *no* `session_verify` warning fires for the normal,
healthy path. Every test also asserts none of
`email`/`password`/`password_hash`/`session`/`session_cookie`/
`cookie`/`authorization`/`token`/`secret`/`api_key`/`request_body`/
`response_body`/`provider_payload`/`planning_state`/`itinerary` is
ever set as an attribute on any auth `LogRecord`.

**Regression**: full suite 3394 passed + 18 skipped (up from 3382 + 18
-- the 12 new tests, zero change to any pre-existing test's outcome,
including every pre-existing `test_auth_routes.py`/`test_service.py`/
`test_dependencies.py`/`test_sessions.py` test). `compileall`/`tsc`/
`lint`/`build` all clean. No frontend file touched. No new dependency;
`SENSITIVE_LOG_FIELD_NAMES`/the allowlist-denylist non-overlap
invariant both unchanged and re-verified; no response shape, cookie
setting, password-hashing, session-signing, or user-repository behavior
changed -- confirmed by the full suite passing unmodified and by live
verification against a real running backend (signup/duplicate-signup/
login/invalid-login/logout/tampered-session, log output inspected
directly, zero secrets found by grep across the entire log file).

## 125. Provider/Gateway Observability (Step 187F)

Makes provider calls observable from one central location using the
same structured logging foundation (Step 187B) and request-correlation
(Step 187C) already used for async jobs (Step 187D) and auth (Step
187E) -- no provider behavior, provider result models, response shapes,
or scraping/regeneration semantics changed anywhere in this step.

**Gateway dispatch methods** (`backend/app/providers/gateway.py`):
`ProviderGateway` has exactly three real *central dispatch* methods
that services actually call -- `get_route` (used by
`route_feasibility_service.py`/`travel_time_buffer_service.py`/
`route_aware_sequencing_service.py`), `search_accommodations` (used by
`accommodation_inventory_service.py`), and `search_flights` (used by
`flight_inventory_service.py`). All three now measure a
`time.monotonic()`-based `duration_ms` around the existing delegate
call and emit one structured log line via a new `_log_provider_call`
helper: `provider` (the underlying adapter's own `provider_name` class
attribute, e.g. `"osrm"`/`"scraped_accommodation_provider"`/
`"routing_provider"` for not_connected -- normalized to `"unknown"` by
`_safe_provider_name` if it's ever missing, over-length, or contains
anything outside `[A-Za-z0-9_:.-]`), `stage` (`"routing"`/
`"accommodations"`/`"flights"` -- a fixed, bounded string per method,
never derived from the request), `status` (the result's own `.status.
value`, via `_provider_status` -- `"returned"` as a fallback for any
shape without a recognizable `.status`), and `error_code` (via
`_provider_error_code`, mapping `"failed"`/`"not_connected"`/
`"unavailable"` to the *already-existing* `PROVIDER_FAILED`/
`PROVIDER_NOT_CONNECTED`/`DATA_UNAVAILABLE` `ErrorCode` values --
omitted, never invented, for a genuine success). `"success"`/
`"returned"` log at `info`; every other status (`not_connected`/
`unavailable`/`failed`/`partial`/`retrying`/`fallback_used`/
`not_requested`) logs at `warning` -- a real, honest non-success
outcome is never disguised as a completion. **No new exception boundary
was added**: none of the three methods caught exceptions before this
step, and none does now -- a raising provider still raises, completely
unchanged, unlogged by the gateway (confirmed by a dedicated test).
Never logs the request (destination/dates/traveler counts/coordinates),
the result's `offers`/`geometry`/`message`/`warnings`, a
`PlanningState`, or any file path.

**Deliberate, documented scope decision**: `places`/`weather`/
`holiday`/`currency` are reached via direct attribute access from
`destination_context_service.py` (e.g. `self.gateway.places.
search_attractions(...)`) -- there is no central dispatch *method* on
the gateway to wrap for these four, unlike the three above. Rather than
retrofitting new pass-through gateway methods nothing currently calls,
or touching `destination_context_service.py`'s six call sites (a much
larger surface for this one step), this step leaves them covered only
by their own pre-existing adapter-level `logger.warning(...)` calls
(OSM/Open-Meteo/Nager/Frankfurter, confirmed still present and
untouched -- see Step 187A's audit) -- an honest limitation, not an
oversight, and consistent with this step's own "preferably at the
central dispatch methods already used by services" instruction.

**LLM-backed subsystems** (item 4's own explicit fallback): neither
`ItineraryNarrativeService` nor `AICandidateDiscoveryService` calls
`ProviderGateway` at all -- both resolve their provider directly from
their own factory (`get_itinerary_narrator_provider()`/
`get_ai_candidate_proposal_provider()`) and call it directly. Per this
step's own scope, logging was added at each one's existing service
boundary instead, never inside a prompt/response:

- `ItineraryNarrativeService.generate`
  (`backend/app/services/itinerary_narrative_service.py`): logs
  `stage="itinerary_narrator"` around the existing `provider.narrate(...)`
  call -- `info` on `status="success"`, `warning` otherwise (including
  the existing `except Exception` branch, which already existed before
  this step and keeps its exact level/message/`exc_info` behavior,
  just gains structured `extra` fields). The disabled short-circuit
  (`ITINERARY_NARRATOR_ENABLED=false`, the default) is deliberately
  never logged -- no provider is even resolved in that branch, and
  logging "disabled" on every single generation would just repeat this
  app's own well-documented default, mirroring Step 187E's identical
  reasoning for a simply-missing auth cookie. Never logs the built
  request (which embeds real scheduled-experience/restaurant names),
  `report.message`, or the narrator's own generated prose.
- `PlanningOrchestrator._run_ai_candidate_discovery_shadow_stage`
  (`backend/app/services/planning_orchestrator.py`): logs
  `stage="ai_candidate_proposal"` around the existing `dry_run(...)`
  call, at the exact pre-existing `try`/`except Exception: return
  planning_state` boundary (Step 161B) -- the swallow-and-continue
  behavior is completely unchanged; a new `logger.warning(...,
  exc_info=True, ...)` line was added *inside* that already-existing
  except block, not a new boundary. On a non-exception return,
  `status="completed"` (`AICandidateProposalStatus.COMPLETED`) logs
  `info`; `"not_connected"`/`"skipped"`/`"rejected"` all log `warning`
  (mirroring the gateway's own info/warning split). Provider name is
  read defensively (`getattr(..., "proposal_provider", None)` before
  `getattr(..., "provider_name", ...)`) so an injected test double
  standing in for the whole service never crashes this log line. Never
  logs the built proposal request (which embeds real destination-context
  text), a proposal's own title/description, or the raw exception
  message.

**Existing adapter warnings preserved, untouched**: no adapter-level
`logger.warning(...)` call (OSM/Open-Meteo/Nager/Frankfurter/OSRM/
scraped accommodation/scraped flight/Kiwi MCP/scraped hotel-ratings)
was removed, renamed, or had its message/level changed by this step --
they remain the detailed, per-adapter failure diagnostics they always
were; the new gateway/service-boundary logs are a separate, central
*summary* layer on top, not a replacement.

**Tests**: `test_provider_gateway_logging.py`
(`backend/app/tests/providers/`, new, 13 tests) -- success logs `info`
with real `provider`/`duration_ms`/`request_id` (when inside a request
scope) and no `error_code`; non-success results (`not_connected`/
`unavailable`/`failed`, across all three methods) log `warning` with
the correct mapped `error_code`; an unsafe provider name (containing a
newline/control character) normalizes to `"unknown"`; no request
parameter (destination/check-in dates) or result content (offers,
`message`) ever appears in the rendered JSON; `duration_ms` is numeric
and non-negative; a call outside any request context carries no
`request_id`; a raising provider still raises unchanged, and the
gateway logs nothing for it. `test_provider_llm_logging.py`
(`backend/app/tests/services/`, new, 6 tests) -- the narrator's
disabled-by-default path produces no log; enabled success/failure both
log safe fields, with a dedicated check that a deliberately
secret-looking exception string never reaches the *structured* fields
(the real traceback still legitimately reaches `exc_info`, matching
Step 187D's precedent); the AI candidate proposal shadow stage's
disabled-by-default path produces no log; its default (`not_connected`)
outcome and an injected-exception outcome both log safe fields with no
secret leak. One genuine test-authoring bug was found and fixed while
writing these: an early draft constructed `Settings(_env_file=None,
ai_candidate_discovery_shadow_mode_enabled=True)` using the field name
rather than the alias (`AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED=True`)
-- a known, already-documented `pydantic-settings` precedence quirk
(`populate_by_name=True` means an ambient env var beats a field-name
kwarg but loses to an alias kwarg) silently left shadow mode `False`,
causing two tests to fail with zero log lines; fixed by switching to
the alias kwarg, matching `test_ai_candidate_discovery_shadow_mode.py`'s
own established convention. This was a test bug, not a product bug --
the underlying logging code was already correct, confirmed by a direct
script reproduction before the test fix.

**Regression**: full suite 3413 passed + 18 skipped (up from 3394 + 18
-- the 19 new tests, zero change to any pre-existing test's outcome,
including every pre-existing gateway/accommodation/flight/routing/
narrator/AI-candidate-discovery test). `compileall`/`tsc`/`lint`/
`build` all clean. No frontend file touched. Live-verified against a
real running backend: a real generate call produced two gateway log
lines (`accommodations`/`flights`, both honestly `unavailable` with the
default no-local-HTML-file state) sharing the response's own
`request_id`; a second run with the narrator and AI-candidate shadow
stage both explicitly enabled produced both LLM service-boundary logs,
correctly `not_connected`/`PROVIDER_NOT_CONNECTED`; a full grep across
both live log files found zero secrets, prompts, HTML, or destination
text. No provider/API/scraping/auth/persistence/async-job behavior
changed -- confirmed by the full suite passing unmodified and by `git
diff --stat` touching only `gateway.py`, `itinerary_narrative_service.py`,
`planning_orchestrator.py`'s one method, the two new test files, and
docs.

## 126. Frontend Request-ID Visibility & Safe Client Diagnostics (Step 187G, final Section 187 step)

Steps 187B-F built the entire backend-side structured-logging/
correlation-id foundation but never touched the frontend -- a failed
API call in the browser had no way to show its `request_id`, and
nothing recorded a caught error anywhere for later inspection. This
step closes exactly that one gap and nothing else: it is a **frontend
visibility layer over data the backend already produces**, not a new
logging system, not a new backend behavior, and not an external
error-reporting integration. **No OpenTelemetry/Elastic APM/Sentry/
Datadog/structlog dependency was added** (`package.json`/
`requirements.txt` both untouched); **no frontend error is ever sent
to any external service** -- every new frontend behavior described
below is 100% local to the browser tab; **no provider/API/scraping/
auth/persistence/async-job behavior changed** anywhere.

**Backend change (the only one this step makes): CORS header
exposure.** Browsers hide every response header from cross-origin
`fetch` code except a small always-safe allowlist -- `X-Request-Id` is
not on it. Without exposing it, the frontend could only ever read a
failed call's request_id from the JSON body's own
`metadata.request_id` (already present on every real response since
Step 187C), never from `response.headers`. `app.main` now passes
`expose_headers=["X-Request-Id"]` to its existing `CORSMiddleware`
call -- `allow_origins`/`allow_credentials`/`allow_methods`/
`allow_headers` are all unchanged, so no origin, credential mode, or
request method/header that was allowed before is allowed any
differently now. This exposes exactly one already-public,
non-sensitive response header (a correlation label, never a token,
cookie, or secret) -- confirmed by a new test that the exposed-headers
list contains `X-Request-Id` and nothing else.

**Frontend type/client changes** (`frontend/lib/types.ts`,
`frontend/lib/api.ts`): a new `ResponseMetadata` type mirrors the
backend's real `app.schemas.api_responses.ResponseMetadata` model
field-for-field (`request_id`/`timestamp`/`environment` -- no
invented field), and `ApiResponse<T>.metadata` is now typed as an
optional `ResponseMetadata` (optional only so a hand-built test
fixture without it still type-checks; every real response already
carries it). `ApiRequestError` gained a fourth constructor parameter,
`requestId: string | null`, populated in the shared `request()`
helper from `body.metadata?.request_id`, falling back to
`response.headers.get("X-Request-Id")` for the rare case of a parsed
body that happened to omit metadata. Every existing call site
constructing `ApiRequestError` directly (the two job-terminal-failure
synthetic errors in `frontend/app/page.tsx`) still compiles unchanged
-- the new parameter defaults to `null`. The success path is
completely untouched: `request<T>()` still resolves with exactly
`body.data`, so every one of the ~20 existing exported API functions
in `lib/api.ts` needed zero changes and every existing call site
across `page.tsx` keeps working exactly as before. `credentials:
"include"` is unchanged; no `Authorization` header was added; no
token is stored in `localStorage`.

**Frontend diagnostics ring buffer** (`frontend/app/page.tsx`, module
level, not React state): `recordApiError(operation, err)` appends an
`ApiErrorLogEntry` (`id`/`timestamp`/`requestId`/`status`/`code`/
`message`/`operation`) to an in-memory array capped at 20 entries,
newest first; `clearApiErrorLog()` empties it. Both are plain
module-level functions (not `useState`) specifically so a component
nested far from the top-level `Home` component -- an experience card's
"keep this place" button, a lock-removal row, the AI-candidate-
promotion refresh button -- can record an error without threading a
callback down through every intermediate layer; `useSyncExternalStore`
(called once, in `Home`) is the one place the buffer is actually read
for rendering, via `subscribeApiErrorLog`/`getApiErrorLogSnapshot`.
`entry.message` is never new content -- it is exactly the same
`ApiRequestError.message` this file already showed directly in
on-screen error banners throughout (lock/feedback/regenerate/auth
error text, all pre-existing) -- so this buffer introduces no new
value that wasn't already considered safe to show a signed-in user
about their own action. It never reads a request/response body,
cookie, `Authorization` header, password, email, provider payload, or
any `PlanningState`/itinerary content -- there is no code path by
which it could, since `ApiRequestError` itself never carries any of
those. Wired into every catch block in this file that already handled
an `ApiRequestError` (auth signup/login, "My trips" load, trip
load/select, generate, feedback, lock create/remove x3, regenerate
refusal, AI-candidate-promotion refresh, and a job-poll's *terminal*
failure only -- deliberately not its "keep polling, transient failure"
branch, which fires on every ordinary network blip and would flood the
buffer with noise rather than real errors) using the operation labels
`login`/`signup`/`create`/`generate`/`load`/`feedback`/`lock`/
`promote`/`regenerate`/`job_poll`. `checkAuth`'s own routine 401 (the
normal "not logged in yet" state hit on every page load by a
signed-out visitor) is deliberately never recorded -- recording it
would clutter Developer Mode with a routine, expected entry before the
visitor ever logs in, echoing Step 187E's own "never log a simply-
missing cookie" noise-reduction precedent. `clearApiErrorLog()` is
called from `handleLogout()` and from `handleAuthenticationRequired()`
(a session found to have expired/been revoked mid-use) -- either could
be followed by a different person signing in on the same browser tab,
so a previous user's caught errors must never still be visible to
them; nothing here is ever written to `localStorage`/`sessionStorage`,
and nothing is logged to the browser console by default.

**Developer Mode UI** (`ApiDiagnosticsPanel`, `frontend/app/page.tsx`):
a compact "Recent API errors (diagnostics)" card listing each entry's
operation/HTTP status/error code/safe message/timestamp, and, when
present, "Request ID: req_..." in a monospace, `break-all` line so a
long id wraps instead of causing horizontal overflow at narrow
viewports (375px/390px). Renders `null` (nothing at all) whenever the
buffer is empty, so a traveler with a clean session sees no trace of
it. Mounted once, in the signed-in shell right below the "Signed in
as / Log out" row, gated on `mode === "developer"` by its one caller --
it is structurally impossible to reach before `currentUser` is set,
so it never appears on the login screen, matching the existing
`if (!currentUser) { return ...login screen... }` early-return this
file already used before this step. It never shows a password, email,
request body, or session-cookie value -- none of those fields exist
anywhere in an `ApiErrorLogEntry`.

**Tests**: no frontend test framework exists in this repo (per
`CLAUDE.md`/`frontend/package.json` -- `npm run lint` is the only
frontend CI check), so per this step's own scope this relied on
`tsc --noEmit`/`lint`/`build` plus live manual verification instead of
new frontend test files -- exactly the documented fallback for a
frontend change here. One new backend test was added,
`test_cors_exposes_only_x_request_id_header_to_cross_origin_frontend`
(`backend/app/tests/api/test_request_id_correlation.py`): sends a
real cross-origin-shaped request (an `Origin` header) at
`GET /trips/{trip_id}`, asserts the `Access-Control-Expose-Headers`
response header is present and equals exactly `{"x-request-id"}` (no
other header exposed), and asserts the actual `X-Request-Id` header
value still matches the response body's `metadata.request_id` --
extending, not replacing, this file's existing same-origin
request-id/metadata-matching tests. Every pre-existing logging/
request-id/auth/job/provider-logging test in the suite still passes
unmodified.

**Regression**: full suite 3414 passed + 18 skipped (up from 3413 +
18 -- the one new CORS test, zero change to any pre-existing test's
outcome). `compileall` clean. `tsc --noEmit`/`lint`/`build` all clean
on the frontend. Manual verification against a real running backend
+ frontend confirmed: signup/login/create/generate behave identically
to before this step; a normal response's body `metadata.request_id`
still matches its own `X-Request-Id` header; a safe incoming
`X-Request-Id` is still honored and an unsafe one still replaced
(both pre-existing Step 187C behavior, re-confirmed unchanged); backend
JSON logs still share the same request_id as the response that
produced them; a deliberately-triggered failed frontend API call shows
up in the Developer Mode diagnostics panel with its real request_id,
status, error code, and safe message, and that panel disappears after
logout; no email/password/cookie/session-token/API-key/request-body/
full-itinerary/`PlanningState` content appeared anywhere in the UI or
browser console; provider/job/auth logs still emit the exact same safe
structured fields as Step 187F/187E/187D left them; unavailable
providers are still logged honestly as `unavailable`/`not_connected`,
never as a fabricated success; the diagnostics panel and its
monospace request-id text showed no horizontal overflow at 375px/
390px viewport widths. No provider/API/scraping/auth/persistence/
async-job behavior changed -- confirmed by the full suite passing
unmodified and by `git diff --stat` touching only
`backend/app/main.py` (one `expose_headers` line),
`backend/app/tests/api/test_request_id_correlation.py` (one new test),
`frontend/lib/types.ts`, `frontend/lib/api.ts`, `frontend/app/page.tsx`,
and docs. **This is the final step of Section 187** -- the full
187A-187G stack is now ready for a single combined review/commit.

## 127. Section 188B: `BACKEND_CORS_ORIGINS` and the Frontend URL Story (docs only, no code change)

Section 188A's Docker/deployment audit found that `BACKEND_CORS_ORIGINS`
(`Settings.backend_cors_origins`, read by `app.main`'s existing
`CORSMiddleware(allow_origins=cors_origins, ...)` call -- unchanged
since before Section 188, and unchanged by this step) was never
explained anywhere alongside the frontend's matching
`NEXT_PUBLIC_API_BASE_URL` value, even though the two only work
together. This step adds no code and no new config field -- it
documents an existing one, in README (new "Frontend ↔ backend URL and
CORS" section) and `docs/16_frontend_architecture.md` section 55.

The short version, restated here because it's a backend-side setting:
`BACKEND_CORS_ORIGINS` is a comma-separated allowlist of origins the
backend's browser-facing responses are allowed to be read from
cross-origin. The default, `http://localhost:3000`, matches the
frontend's own local default port -- so local dev and local
`docker compose up` both work with zero configuration on either side.
A real, non-local deployment (not set up, scripted, or verified by
Section 188) would need the deployed backend's `BACKEND_CORS_ORIGINS`
to include the deployed frontend's real origin, matching that
frontend's own `NEXT_PUBLIC_API_BASE_URL` pointing back at this
backend's real origin -- both sides have to agree, or the browser's
request is correctly rejected by CORS, which is intentional, safe
behavior, not a bug. No origin is added to `BACKEND_CORS_ORIGINS` by
this step, and `.env.example`'s existing default is unchanged.

No provider/API/scraping/auth/persistence/async-job/observability
behavior changed -- confirmed by the full suite passing unmodified and
by `git diff --stat` touching only `.env.example`, `README.md`,
`docs/16_frontend_architecture.md`, `docs/CODEBASE_OVERVIEW.md`, and
this file.

## 128. Section 188C: Backend Container Healthcheck, Compose Health Wiring, Python 3.13 Alignment

Section 188A's audit found two related backend-container gaps:
`backend/Dockerfile` still ran `python:3.11-slim` while
`.github/workflows/ci.yml`/`CLAUDE.md` both target Python 3.13 (so the
image's runtime was never actually the version the test suite ran
against), and neither `backend/Dockerfile` nor `docker-compose.yml`
had any healthcheck for the backend container -- `postgres` had a real
`pg_isready` healthcheck that nothing's `depends_on` ever referenced.
This step closes both gaps. It is a Docker/Compose/docs change only:
no backend application source file was modified, `/health`
(`backend/app/main.py`) is byte-for-byte the same route it was before
this step, and `backend/requirements.txt`/`requirements-dev.txt` are
unchanged.

**Python 3.13 alignment**: `backend/Dockerfile`'s `FROM` line changed
from `python:3.11-slim` to `python:3.13-slim`. Verified, not assumed:
built with `docker build --no-cache`, the image built cleanly (455MB),
and the full backend test suite (`python -m pytest`, run on the host
under 3.13 both immediately before and after this Dockerfile edit)
stayed at **3414 passed, 18 skipped** either way -- this change makes
the container's Python version match what was already being tested,
rather than introducing a new, unverified runtime. This is a version
*alignment*, not a "production-ready" claim by itself.

**Backend Dockerfile healthcheck**: a new `HEALTHCHECK` instruction
(`--interval=30s --timeout=5s --start-period=10s --retries=3`) runs
`python -c "import urllib.request; urllib.request.urlopen('http://
127.0.0.1:8000/health', timeout=3)"` inside the container -- stdlib
only, since neither `curl` nor `wget` is installed in
`python:3.13-slim` and adding one merely to shell out to it would be a
larger, riskier change than using what's already there. An unhandled
exception (connection refused during startup, timeout, a non-2xx
status once `.read()`/`getcode()` is inspected -- though `urlopen`
itself already raises `HTTPError` for any 4xx/5xx response) exits the
`python -c` process non-zero, which Docker's `HEALTHCHECK` reads as
"unhealthy" -- no extra error-handling code was needed for that
contract to work. **No new route was added; `/health` was not changed
to check anything new.** It remains exactly what it was before this
step: a liveness check confirming the FastAPI process itself is up and
answering HTTP requests -- **never** a readiness signal for Postgres,
Redis, or any external provider. Verified live: `docker inspect`
against a freshly built, freshly started container showed
`State.Health.Status` transition `starting` -> `healthy` with a
zero-exit-code, empty-output log entry, matching the healthcheck
succeeding exactly as designed.

**docker-compose.yml health wiring**: `backend`'s `depends_on` changed
from the old plain list form (`- postgres` / `- redis`, which only ever
waited for each container to *start*) to the long form:
`postgres: condition: service_healthy` (now actually consuming
`postgres`'s pre-existing `pg_isready` healthcheck, unused until this
step) and `redis: condition: service_started` (unchanged in effect --
`redis` still has no healthcheck, since nothing in `backend/app/`
reads `REDIS_URL` today; see `Settings.redis_url`'s own comment in
`backend/app/core/config.py`, unchanged). `frontend`'s `depends_on`
changed from `- backend` to `backend: condition: service_healthy`,
relying on `backend`'s own Dockerfile-defined `HEALTHCHECK` above --
Compose honors an image's built-in healthcheck for a `condition:
service_healthy` dependency even when the compose service definition
itself declares no separate `healthcheck:` block, which this step
verified live rather than assumed (see below). No port was added or
changed, no env var was added to either service, no volume changed,
and `backend`/`frontend`'s own `command:` overrides are untouched --
this is startup-ordering only.

**Why this is a "deployment cleanup," not a new runtime dependency**:
the app still defaults to `PERSISTENCE_BACKEND=local_json` (unchanged
by this step) and does not require Postgres to run at all; this
ordering only matters the moment a real deployment or opt-in local run
sets `PERSISTENCE_BACKEND=postgres` and would otherwise race the
backend process starting before Postgres could accept connections --
previously a latent gap despite `postgres`'s healthcheck already
existing.

**Verification, all done live, not assumed**: `docker compose config
--quiet` (with `POSTGRES_HOST_PORT=15432` set, never reading or
printing the real `.env`'s actual values -- `--quiet` prints nothing on
success) validated the compose file's syntax cleanly. `docker compose
build backend` succeeded. `POSTGRES_HOST_PORT=15432 docker compose up
-d postgres backend` produced, in order: `Container ...postgres
Waiting` -> `Container ...postgres Healthy` -> only then `Container
...backend Starting` -- direct proof the health-gated dependency
actually blocks backend startup, not just that the YAML parses.
`docker compose ps` afterward showed both `travelobligator_backend` and
`travelobligator_postgres` as `Up ... (healthy)`, and `GET /health`
through the published host port (`http://localhost:8000/health`)
returned `200` throughout. `docker compose down` cleanly tore
everything back down. The full backend test suite and frontend
`tsc`/`lint`/`build` were also re-run unmodified (see this step's own
final report for exact numbers) to confirm zero behavior change
alongside the Docker-level verification above.

**Safety notes, restated explicitly per this step's own constraints**:
this step does not claim the stack is production-ready; does not add
or claim automatic migrations (`alembic upgrade head` remains a manual
step -- see section 106); does not make `/health` check Postgres,
Redis, or any provider; does not claim Redis is used by the
application; and does not change or fix the frontend's Docker
production path (`frontend/Dockerfile` was not touched by this step --
that gap was closed later, by Step 188E; see section 130 below and
`docs/16_frontend_architecture.md` section 57). No
provider/API/scraping/auth/persistence/async-job/observability
behavior changed -- confirmed by the full suite passing unmodified and
by `git diff --stat` touching only `backend/Dockerfile`,
`docker-compose.yml`, `README.md`, `docs/16_frontend_architecture.md`,
`docs/CODEBASE_OVERVIEW.md`, and this file.

## 129. Section 188D: Backend Docker Build-Context Cleanup

Section 188A's audit found `backend/.dockerignore` excluded only
Python bytecode/cache directories, virtualenvs, and `.env`/`.env.*` --
nothing kept `backend/.data/` (the gitignored `LocalJsonStore` JSON
file, `provider_cache.sqlite3`, and any manual-scrape HTML fixtures) or
the test suite out of the Docker build context. This was not
theoretical: at the time this step was implemented, a real, populated
`backend/.data/` (3MB+, containing genuine local dev/test state from
prior manual verification passes in earlier 188-series steps) existed
on disk and would have been copied into any build run from that
checkout. This step is a `.dockerignore`-only change -- no backend
application source file was modified, `backend/Dockerfile`'s `COPY .
.` line is unchanged (the exclusion happens entirely via
`.dockerignore`, the standard, idiomatic mechanism for this -- not by
editing the `Dockerfile` itself), and `docker-compose.yml` was not
touched (its dev `backend` service bind-mounts `./backend:/app`
regardless of image contents, so this step has zero effect on that
mode either way).

**What's newly excluded**: `.data/` (see above -- the concrete, real
problem this step fixes); `**/__pycache__/`/`**/*.py[cod]`/
`.pytest_cache/`/`.mypy_cache/`/`.ruff_cache/`/`.coverage`/`htmlcov/`
(caches, some already excluded before this step in a less explicit
form, now consistent); `dist/`/`build/`/`*.egg-info/` (defensive --
nothing in this backend currently produces these); `.DS_Store`;
`logs/`/`*.log` (defensive -- this app logs to stdout only, per Step
187B, section 121); and `app/tests/` (see below). `.env`/`.env.*` and
`.venv/`/`venv/` were already excluded before this step and remain
excluded, unchanged.

**`app/tests/` exclusion, and why it's safe**: the backend test suite
is now excluded from both the build context and the built image.
Confirmed via grep across `app/` and `backend/scripts/` before making
this change that no non-test runtime code imports anything under
`app.tests` -- the exclusion cannot break an import at runtime.
`requirements-dev.txt` (the only place `pytest` itself is declared) was
already never installed into this image, unchanged since before
Section 188 -- so this runtime image never ran pytest anyway; excluding
the test *files* on top of the already-absent test *dependency* simply
makes that existing fact smaller and slightly harder to accidentally
rely on, not a new restriction. Tests continue to run exactly as
before: on the host (`pytest` from the repo root) and in CI
(`.github/workflows/ci.yml`) -- neither reads `.dockerignore` or runs
inside a container's built image. If a future step adds a CI job that
runs tests *inside* a container, that job would need to either build
without this exclusion (e.g. a separate `Dockerfile.test`) or rely on a
bind-mounted source tree the way `docker-compose.yml`'s existing dev
`backend` service already does (`./backend:/app`) -- neither of which
Section 188D adds.

**What's deliberately never excluded**: `alembic/`, `alembic.ini`, and
`requirements.txt` are not in `.dockerignore` and were never
considered for exclusion -- migrations remain fully available in the
image for the same manual, opt-in Postgres workflow documented in
section 106 (`alembic upgrade head`, run by a human, never
automatically). `backend/scripts/`'s manual smoke scripts (documented
in README's "Optional Provider-Backed Demos" section) are also left
untouched, since they're run from the host against `backend/` directly
and nothing here required excluding them.

**Verification, all done live, not assumed**: two sentinel files
(`backend/.data/188d_sentinel.txt`, `backend/app/tests/
188d_sentinel.txt`) were created before the build specifically so their
absence in the built image would be unambiguous proof of exclusion,
not a false negative from those directories happening to be empty --
both were deleted again after verification, leaving no trace in the
repo. `docker build --no-cache -t travelobligator-backend:188d
./backend` produced a smaller image than Section 188C's own build
(432MB vs. 455MB) -- direct evidence real content was excluded, not
just a config change with no observable effect. Running that image and
executing inside the live container confirmed: `/app/.data` absent,
`/app/app/tests` absent, `/app/alembic` present with all 3 migration
files, `/app/alembic.ini` present, `import app.main` succeeds,
`python -c "import pytest"` fails with `ModuleNotFoundError` (proving
pytest was never installed, unrelated to and unchanged by this step),
no `.env*` file present, `GET /health` returns `200`, and the
Dockerfile's own `HEALTHCHECK` (Step 188C) still reports `healthy`
against this newly built image. The image and container were removed
after verification.

No provider/API/scraping/auth/persistence/async-job/observability
behavior changed -- confirmed by the full suite passing unmodified and
by `git diff --stat` touching only `backend/.dockerignore`,
`README.md`, `docs/CODEBASE_OVERVIEW.md`, and this file.

## 130. Section 188E: Compose Frontend Target Pinning (backend-doc cross-reference)

Section 188E's full writeup is `docs/16_frontend_architecture.md`
section 57 -- this entry exists here only because that step's one
`docker-compose.yml` change is adjacent to the Compose startup-ordering
story section 128 already documents, and `backend/Dockerfile` was not
touched by 188E at all.

`frontend/Dockerfile` became multi-stage with a new `production`
target (real `next build` + `next start`) alongside its existing `dev`
target (unchanged `next dev` behavior). Because Docker builds a
multi-stage file's *last* stage by default when no `--target` is
given, and `production` is now that last stage, `docker-compose.yml`'s
`frontend` service gained one new line -- `build.target: dev` -- so
local `docker compose up`/`docker compose build frontend` keeps
building/running the exact same `dev` target it always did. Verified
live alongside this line's addition: `POSTGRES_HOST_PORT=15432 docker
compose up -d postgres backend frontend` reproduced the exact same
health-gated startup order Section 188C established (`postgres` ->
`healthy` -> `backend` -> `healthy` -> `frontend`), `docker compose
logs frontend` showed the real `next dev`/Turbopack banner, and both
`GET /` (frontend, port 3000) and `GET /health` (backend, port 8000)
returned `200` through their published host ports. `docker compose
ps` showed `backend`/`postgres` both `(healthy)` and `frontend` `Up`
(no healthcheck, unchanged). No backend service, port, environment
variable, or `depends_on` entry changed by 188E -- only the one new
`target: dev` line on the `frontend` service.

## 131. Section 188F: Explicit Manual Alembic Migration Workflow

Sections 183B-183E built a real, working, opt-in Postgres persistence
foundation (SQLAlchemy/Alembic dependencies, a real schema migration,
working `Postgres*Repository` implementations, Docker Compose port
hardening) -- but the only documented way to actually run that
migration was typing `alembic upgrade head` by hand from `backend/`,
with the right `DATABASE_URL` prefix. This step adds one small,
explicit, still-entirely-manual script wrapping that exact same
operation -- `backend/scripts/run_migrations.py` -- plus its own test
file and doc updates. **Postgres persistence is still entirely
opt-in** (`PERSISTENCE_BACKEND` still defaults to `"local_json"`,
unchanged), **and migrations are still never run automatically** by
anything -- this step adds a documented manual command, not an
automatic one.

**`backend/scripts/run_migrations.py`** mirrors this project's
existing `backend/scripts/manual_*.py` convention (a plain script, no
new dependency, a `main()` guarded by `if __name__ == "__main__":`,
never imported by any runtime module) with one difference: unlike
those scripts (which are genuinely dangerous/live-network by default
and so refuse to run without explicit env-var opt-in guardrails), this
one's only real "danger" is the same one typing `alembic upgrade head`
directly already carries, so it has no separate guardrail flag -- its
safety instead comes from never being invoked by anything but a human.
It calls Alembic's own Python API (`alembic.config.Config` +
`alembic.command.upgrade(config, "head")`), pointed at this project's
real, existing `backend/alembic.ini` -- **never** re-parsing
`DATABASE_URL` itself; `backend/alembic/env.py` (completely unchanged
by this step) remains the one place `Settings.database_url` is read,
exactly as it already was for a manually-typed `alembic upgrade head`.
This is deliberately the same "reuse the existing config path, never
duplicate it" discipline `alembic/env.py`'s own docstring already
established in Step 183B.

**Safety contract** (all enforced by what the script does *not* do,
not by a runtime check): only ever `upgrade` to `"head"` -- never
`downgrade`, never any other target; never calls SQLAlchemy's
`Base.metadata.create_all(...)` -- the schema is created exclusively
via the real Alembic migrations under `backend/alembic/versions/`;
never creates a database, role, or user -- Postgres and its
credentials must already exist (e.g. via `docker compose up -d
postgres`'s own `POSTGRES_DB`/`POSTGRES_USER`/`POSTGRES_PASSWORD`);
never prints `DATABASE_URL` or any other config value -- only a
handful of fixed, hardcoded status strings ever reach stdout/stderr,
and a real exception (a connection failure, a migration script error)
is deliberately never echoed verbatim, since a driver's own error text
could in principle embed connection details -- only Alembic's own
built-in revision-id logging (via `alembic.ini`'s pre-existing
`[loggers]` config, unchanged) may print revision ids/messages, never
a connection string; has no side effects on import -- only calling
`main()` runs anything; adds no new dependency (`alembic` has been a
pinned `requirements.txt` dependency since Step 183B, used here via
its public API rather than a `subprocess` call to its console script,
so this works identically inside the backend Docker image -- which
never installs `requirements-dev.txt` -- and on a developer's host).
**Never invoked automatically**: not by `backend/Dockerfile`'s `CMD`
(unchanged, still bare `uvicorn app.main:app ...`) or `HEALTHCHECK`
(unchanged, still liveness-only against `/health`), not by
`docker-compose.yml`'s `command:` for any service (unchanged), not by
app startup (`app.main`'s `lifespan` still only runs
`recover_interrupted_jobs()`, per Step 186E -- nothing else was
added). Running it is always a deliberate, manual operator action.

**Tests** (`backend/app/tests/scripts/test_run_migrations_script.py`,
new, 14 tests, none requiring a real Postgres): the script exists at
the documented path with `alembic.ini` alongside it (confirming the
same relative-path resolution works both in a real checkout and, by
the same logic, inside the backend Docker image, where `WORKDIR /app`
mirrors `backend/` exactly); importing it has zero side effects
(verified by patching `alembic.command.upgrade` before import and
confirming zero calls); `main()` genuinely invokes
`alembic.command.upgrade(config, "head")` with this project's own real
`alembic.ini`-backed `Config`, on success printing only a safe status
line; a simulated failure (a `RuntimeError` deliberately carrying a
fake connection string with a distinctive fake password in its
message, to make the "never leaked" assertion a real, specific check
rather than a vacuous one) exits `main()` with `1` and the captured
output contains neither that fake password, `DATABASE_URL`, nor any
other forbidden secret-shaped substring; source-level AST checks
(deliberately walking only `Name`/`Attribute`/import identifiers --
never a blunt raw-source substring search, which would have falsely
tripped on this script's own safety-explaining docstring prose)
confirm the script's actual *code* never references `downgrade`,
`create_all`, `PERSISTENCE_BACKEND`/`persistence_backend`,
`os.environ`, `database_url`, or `get_settings`, and imports nothing
beyond stdlib + `alembic`. One real subprocess test runs the script as
an actual process (`python backend/scripts/run_migrations.py`) against
`postgresql://fakeuser:fakepassword_dont_leak_me@127.0.0.1:65535/fakedb`
-- an address nothing listens on, so the connection is refused
immediately (loopback, no DNS delay) rather than requiring a real
running Postgres -- confirming a real end-to-end failure path exits
non-zero and leaks nothing, exactly like the mocked test above but
through the real CLI entrypoint.

**Live verification against a real Postgres, not just mocked** (
`POSTGRES_HOST_PORT=15432 docker compose up -d postgres`, waited for
`healthy`): `DATABASE_URL=postgresql://travelobligator_user:
change_me@localhost:15432/travelobligator python backend/scripts/
run_migrations.py` applied all 3 real migrations cleanly (`2fe86f95d81e`
-> `835e5782d7a5` -> `6fabeaa6455e`), printing only Alembic's own
revision-id log lines plus this script's two fixed status lines --
`DATABASE_URL` (with its real, though non-secret, local-only
`change_me` placeholder password) never appeared in the output.
`alembic current` independently confirmed `6fabeaa6455e (head)`; `psql
\dt` (schema listing only, no row data) confirmed exactly the expected
`trips`/`planning_states`/`users`/`generation_jobs`/`alembic_version`
tables. Running the script a second time against the now-already-
migrated database was a safe, clean no-op (exit `0`, same two status
lines, no error) -- `alembic upgrade head` is idempotent by design,
and this script inherits that property automatically since it performs
no custom state tracking of its own. `docker compose down` cleanly
tore everything back down afterward.

**Docker compatibility**: `backend/Dockerfile` was **not** modified --
its existing `COPY . .` already copies `backend/scripts/` (including
the new script) into the image exactly like every other file under
`backend/`, and `backend/alembic.ini`/`backend/alembic/versions/`
remain fully present (confirmed already, by Step 188D's own build-
context cleanup, which deliberately never excludes `alembic/`).
Verified live with a fresh `docker build --no-cache`: the built image
still starts with plain `uvicorn` (no migration attempted at startup),
still serves `GET /health` `200`, and the script itself is present and
importable inside the running container at its documented path.

No provider/API/scraping/auth/persistence/async-job/observability
behavior changed -- confirmed by the full suite passing unmodified and
by `git diff --stat` touching only `backend/scripts/run_migrations.py`
(new), `backend/app/tests/scripts/test_run_migrations_script.py`
(new), `README.md`, `docs/CODEBASE_OVERVIEW.md`, and this file.
`backend/Dockerfile`, `docker-compose.yml`, `backend/app/core/
config.py`, `backend/alembic/env.py`, and every existing repository/
route/service file are all byte-for-byte unchanged.

## 132. Section 188G: Final Docker/Deployment Verification, CI Build Gate, and Section 188 Closeout

The final step of Section 188. Adds one CI job (build-only), re-runs a
full live verification pass over everything 188A-188F built (nothing
new to fix was found -- every check below passed against the exact
same Dockerfiles/Compose file/script 188C-188F already produced), and
closes out the section's docs. No backend or frontend application
source file was touched by this step.

### CI Docker build gate

`.github/workflows/ci.yml` gained a third job, `docker-build`, running
independently alongside the existing `backend`/`frontend` test jobs
(both completely unchanged). Three steps, each a plain `docker build`
with no `--push`, no registry, no cloud credential, and no GitHub
secret referenced anywhere in the job:

```yaml
docker build -t travelobligator-backend:ci ./backend
docker build --target dev -t travelobligator-frontend:ci-dev ./frontend
docker build --target production -t travelobligator-frontend:ci-production ./frontend
```

This is a **build-only regression gate** -- it proves the three
Dockerfiles still produce an image on every push/PR, nothing more. No
container built by this job is ever run in CI, no migration is applied,
no real provider/API call is made (there is no running process to make
one), no image is uploaded/pushed/published anywhere, and no external
deployment platform is contacted. A green `docker-build` job is not a
claim of "production-ready" -- it answers exactly one question ("does
this still build?") and no other.

### Final live verification (all done for real, not assumed)

**Backend**: `docker build --no-cache -t travelobligator-backend:188g
./backend` succeeded. The running container: served `GET /health`
`200`; its own Docker `HEALTHCHECK` (Step 188C) transitioned to
`healthy`; `/app/.data` absent, `find /app -maxdepth 1 -name ".env*"`
empty, `/app/app/tests` absent (Step 188D's build-context exclusions,
re-confirmed still in effect); `/app/alembic.ini` present, all 3
migration files present under `/app/alembic/versions/`,
`/app/scripts/run_migrations.py` present (Step 188F's script, still
shipped); `python -c "import pytest"` still raises `ModuleNotFoundError`
(confirming `requirements-dev.txt` is still never installed in this
image); `import app.main` still succeeds cleanly.

**Frontend**: both `docker build --no-cache --target dev` and
`--target production` (Step 188E) succeeded. The `production`
container's own logs showed the real `next start` banner (never
`next dev`) and served `GET /` `200` with real page HTML. The `dev`
container's logs showed the real `next dev`/Turbopack banner and also
served `200` -- both targets independently re-confirmed working, not
just one.

**Compose**: `POSTGRES_HOST_PORT=15432 docker compose config --quiet`
validated cleanly with zero output (never printing the real `.env`).
`docker compose build` (all three application services) succeeded.
`POSTGRES_HOST_PORT=15432 docker compose up -d postgres backend
frontend` reproduced, once again, the exact health-gated startup order
Steps 188C/188E established: `postgres` reached `Healthy` before
`backend` even started; `backend` reached `Healthy` (Step 188C's own
`HEALTHCHECK`) before `frontend` started (Step 188E's `depends_on:
backend: condition: service_healthy`). `docker compose ps` showed
`backend`/`postgres` both `(healthy)`; `GET /health` (port 8000) and
`GET /` (port 3000) both returned `200` through their published host
ports. `docker compose down` cleanly tore everything back down
afterward, and every image/volume/container created during this
verification pass was removed once it finished.

**Migration helper, against that same live Compose Postgres**: `cd
backend && DATABASE_URL=postgresql://travelobligator_user:
change_me@localhost:15432/travelobligator python scripts/
run_migrations.py` applied all 3 migrations cleanly; `alembic current`
independently confirmed `6fabeaa6455e (head)`; running the script a
second time against the now-already-migrated database was a safe,
clean no-op (exit `0`, same two fixed status lines, no error).
`DATABASE_URL` was never printed by any of this -- consistent with
Step 188F's own safety contract, re-confirmed here rather than merely
assumed to still hold.

### Docs closeout

This step's own docs updates (this section; `README.md`'s "Running
with Docker Compose instead" and "Current Status" sections;
`docs/16_frontend_architecture.md`'s own no-frontend-source-change
note; `docs/CODEBASE_OVERVIEW.md`'s CI/CD-and-deploy paragraph) are the
last docs work Section 188 needs. Every doc updated across 188A-188G
consistently states the same honest scope: **Section 188 is Docker/
deployment cleanup and verification, not a production deployment.**
No Kubernetes/cloud/CD config was ever added; no image was ever
pushed anywhere; no external hosting platform is configured; Postgres
persistence remains entirely opt-in (`PERSISTENCE_BACKEND` still
defaults to `local_json`); Alembic migrations remain entirely manual/
operator-invoked (never run by the Dockerfile, Compose, CI, or app
startup); `/health` remains liveness-only, never a Postgres/Redis/
provider readiness signal; Redis remains genuinely unused by the
application; and `NEXT_PUBLIC_API_BASE_URL` remains public,
browser-visible build-time configuration, never a safe place for a
secret.

### Full Section 188 summary (188A-188G)

- **188A**: read-only Docker/deployment audit -- found the Python
  version mismatch, missing healthchecks, the un-narrowed backend
  build context, the missing frontend production path, and the
  undocumented `NEXT_PUBLIC_API_BASE_URL`/migration workflow. Zero
  files changed.
- **188B**: documented `NEXT_PUBLIC_API_BASE_URL` and its
  `BACKEND_CORS_ORIGINS` pairing in `.env.example`/README/docs. No
  code changed.
- **188C**: `backend/Dockerfile` -> `python:3.13-slim`; added a
  liveness-only `/health` `HEALTHCHECK`; `docker-compose.yml` gained
  real `condition: service_healthy`/`service_started` startup
  ordering.
- **188D**: narrowed `backend/.dockerignore` to exclude `.data/`/
  caches/bytecode/`.venv`/logs/`app/tests/` -- Alembic migrations
  deliberately never excluded.
- **188E**: `frontend/Dockerfile` became multi-stage with a real
  `dev`/`production` split; `docker-compose.yml` pinned
  `build.target: dev` so local Compose behavior never changed.
- **188F**: added `backend/scripts/run_migrations.py`, a manual,
  secret-safe wrapper around the pre-existing `alembic upgrade head`
  workflow, plus 14 tests.
- **188G**: this step -- a build-only CI Docker gate, one final full
  live re-verification pass (nothing new broken, nothing new fixed),
  and this closing summary.

No provider/API/scraping/auth/persistence/async-job/observability
behavior changed anywhere across Section 188 -- confirmed, at every
single step, by the full backend suite passing unmodified (3428
passed + 18 skipped as of 188F/188G, unchanged from before Section 188
began) and by every step's own `git diff --stat` touching only Docker/
CI/config/docs/test files, never `backend/app/`'s or `frontend/app/`'s
actual application logic. **Section 188 (188A-188G) is ready for a
single combined commit**, pending the user's own review.

## 133. Section 189C: Fix Backend-Sourced Stale Regeneration Copy

Section 189B's real browser smoke test surfaced a genuine, user-visible
bug: `"Feedback regeneration is not implemented yet."`, rendered
inside the pending-feedback/change-preview `blocked_by` list on every
feedback submission. Real, deterministic, feedback-driven regeneration
has existed since Step 174C/174D (`POST /trips/{trip_id}/regenerate`)
-- this string predates that and was never updated. This step is a
pure copy fix: **no `can_regenerate` condition, feedback
classification rule, `change_preview`/`pending_feedback_summary`
structure, `RegenerationReadiness`/`PlanDiffPreview` model field, or
API status code changed anywhere.** The frontend already renders every
`blocked_by` list generically (`.map((reason) => ...)` in four places
in `frontend/app/page.tsx`, none of which special-case any specific
string) -- so fixing the backend string required, and needed, zero
frontend code change.

**The same stale pattern existed in four places, not the two
originally suspected** -- found by reading every real call site, not
assumed:

1. `backend/app/services/feedback_service.py`'s `_BLOCKED_BY` tuple
   (used in both `apply_feedback`'s per-event `change_preview.
   blocked_by` and `_compute_pending_feedback_summary`'s
   `PendingFeedbackSummary.blocked_by`) -- `"Feedback regeneration is
   not implemented yet."` replaced with `"Submitting feedback does not
   itself trigger regeneration -- see regeneration readiness for
   whether a real regeneration can run now."` This wording is
   deliberately availability/state-based, not implementation-based,
   and is true regardless of the *current* value of `can_regenerate`
   (unlike a blanket "regeneration is unavailable" claim would be) --
   it only describes what this one endpoint itself does (nothing to
   the plan), which is unconditionally true.
2. `backend/app/services/regeneration_readiness_service.py`'s
   `recompute`, `not version_history` branch -- `"Regeneration engine
   is not implemented yet."` (the second of two `blocked_by` entries,
   redundant with and contradicting the first, already-accurate entry
   "No plan has been generated for this trip yet.") replaced with
   `"Regeneration is unavailable for the current planning state."`
3. `backend/app/services/plan_diff_preview_service.py`'s
   `_NO_PLAN_BLOCKED_BY` -- the sibling service `PlanDiffPreviewService`
   mirrors `RegenerationReadinessService`'s branch structure exactly
   and had the exact same bug (`"Regeneration engine is not
   implemented for an ungenerated trip."`), found by checking this
   service too even though it wasn't in this step's originally-named
   file list -- fixed with the same replacement wording as (2), for
   consistency.
4. `backend/app/models/planning_state.py` -- both `PlanDiffPreview.
   blocked_by` and `RegenerationReadiness.blocked_by`'s
   `default_factory` values (the model's own pre-recompute defaults,
   kept in sync with (2)/(3)'s real recompute branches by convention)
   carried the identical stale text and were fixed identically.
   `RegenerationReadiness`'s own class docstring also falsely claimed
   `status`/`can_regenerate` default to "blocked"/`False` "because no
   real regeneration engine is connected" -- reworded to correctly
   attribute that default to being the model's pre-recompute value,
   not a statement about the engine's existence (a real one has
   existed since Step 174C). `FeedbackService`'s own class docstring
   similarly overclaimed that "any regeneration" requires an
   `AIReasoningProvider` -- corrected to state plainly that real
   regeneration is deterministic and requires no AI provider at all;
   an AI provider would only ever improve the *interpretation* step
   this class already does with keyword matching.

**What deliberately did not change**: `required_inputs`/
`available_inputs`/`missing_capabilities` still use the symbolic
capability-name token `"regeneration_engine"` (alongside
`"generated_plan"`/`"pending_feedback"`/`"version_history"`/
`"plan_diff_preview"`) -- this is a machine-readable identifier list,
not English prose claiming something is unimplemented, and changing
its content would touch the readiness computation itself, out of this
step's pure-copy-fix scope. `_compute_pending_feedback_summary`'s own
`note` field ("Feedback has been captured and interpreted, but no plan
sections have been regenerated.") was already accurate (state-based,
never claims regeneration is impossible) and was left untouched.

**Tests**: updated the 4 existing tests that asserted the exact old
string (`backend/app/tests/repositories/test_persistence.py`, one
round-trip assertion; `backend/app/tests/api/test_trips_smoke.py`,
three assertions across the change-preview and pending-summary
endpoints) to the new wording -- confirmed via full-file grep that no
other existing test anywhere asserted any of the other three fixed
strings verbatim. Added a new, dedicated regression-guard file,
`backend/app/tests/services/test_regeneration_copy_accuracy.py` (8
tests): a static source-text scan across all four fixed files (catches
a reintroduced stale phrase even in a docstring no runtime test would
ever exercise) plus one runtime test per fixed call site confirming
the new wording is present, `blocked_by`'s length/existence is
unchanged, and every readiness/eligibility field
(`can_regenerate`/`status`/`preview_status`/`regeneration_available`/
`required_inputs`/`missing_capabilities`) is byte-for-byte identical
to before this step.

**Verification**: full suite **3436 passed + 18 skipped** (3428 + 8
new tests, zero change to any pre-existing test's outcome).
`compileall`/`tsc`/`lint`/`build` all clean. No frontend file changed.
No provider/API/scraping/auth/persistence/async-job/observability
behavior changed -- confirmed by the full suite passing unmodified and
by `git diff --stat` touching only the four backend source files named
above, two existing backend test files, one new backend test file,
and docs. This step does not claim regeneration always works, and
does not claim a generated or regenerated plan is final, guaranteed,
booking-ready, or travel-ready -- it only makes the copy describing
regeneration's *current availability* accurate, matching what Step
174C/174D actually built.

## 134. Section 190C: Redact Local Filesystem Paths From User-Facing Provider Warnings

Section 190B's real browser screenshots surfaced a real, portfolio-
visible cosmetic bug: `ScrapedAccommodationProvider`/
`ScrapedLocalFlightProvider`/`ScrapedLocalHotelRatingsProvider` (Steps
168C/169B/185E, extended to multi-source in 185C/185D/185E) each
included the real, absolute local machine filesystem path (e.g.
`/Users/apple/Project/travelobligator/backend/.data/manual_scrapes/
accommodations.html`) in their `message`/`warnings` output whenever a
configured local manual-scrape file didn't exist -- surfaced to the
frontend's Trust Dashboard, Validation Report, and Provider Coverage
sections verbatim. **Not a secret** (no credential, token, session
value, or `.env` content was ever involved), but unpolished and
unnecessary to expose; the safe `source_id` label each adapter already
used elsewhere (`success_labels`/`failed_labels`) says everything a
user or portfolio viewer needs to know.

**Exactly six leak points across three files**, found by reading every
`f"...{slot.path}..."`/`str(slot.path)`-into-a-user-facing-field call
site directly, not assumed: each of the three adapters had (1) a
per-slot "file does not exist" warning embedding the raw path, and (2)
a multi-source summary `message` joining the raw path list
(`checked_paths`) directly with `", ".join(...)`. Fixed identically in
all three:

- **Per-slot warning**: `f"{slot.source_id}: no local file found at
  {slot.path}."` (accommodation) / `f"{slot.source_id}: configured
  scraped-<type> HTML path does not exist: {slot.path}."` (flights,
  hotel-ratings) -> `f"{slot.source_id}: no local manual scrape file
  found for this source."` / `f"{slot.source_id}: configured local
  manual scrape file was not found."`
- **Multi-source summary**: `"No local scraped-<type> HTML files were
  found. Checked: " + ", ".join(checked_paths)` -> `"... Checked
  configured source(s): " + ", ".join(checked_source_ids)`. Each
  adapter gained one new parallel list, `checked_source_ids: list[str]`,
  populated alongside the pre-existing `checked_paths` in the same loop
  iteration -- `checked_paths` itself is **never removed and never
  changes its internal role** (still exactly what
  `if checked_paths:`/`len(checked_paths) == 1`/`if not checked_paths:`
  branch on), it simply stops being the thing joined into a
  user-facing string.

**What deliberately did not change**: the `"path": str(slot.path)`
entries inside `make_query_hash(...)`'s input dict (flights/hotel-
ratings adapters) -- these feed a cache-key hash, are never returned to
a caller or exposed via any API field, and changing them would touch
real cache-invalidation behavior, out of this step's pure-copy-fix
scope. No `AccommodationSearchResult`/`FlightSearchResult`/
`HotelRatingsResult` field was added, removed, or retyped -- `message`/
`warnings` remain exactly the same `str | None`/`list[str]` shapes.
`AccommodationSearchStatus.UNAVAILABLE`/`FlightSearchStatus.UNAVAILABLE`/
`HotelRatingsStatus.UNAVAILABLE` are still returned in exactly the same
branches as before -- **no provider ever reports available/success
when it isn't**, and no `not_connected`/`unavailable`/`failed` status
was hidden or softened anywhere. `backend/app/core/config.py`'s
resolved path fields (`resolved_scraped_accommodation_html_path()` and
siblings) are completely untouched -- the real configured local paths
still work exactly as before; only what gets *displayed* about them
changed.

**Tests**: two pre-existing tests
(`test_scraped_flight_provider.py::test_missing_file_message_is_honest`,
`test_scraped_hotel_ratings_provider.py::test_missing_file_message_is_honest`)
asserted the old `"does not exist"` substring -- updated to assert the
new `"was not found"` wording plus an explicit "the real tmp_path never
appears" guard. One pre-existing test
(`test_scraped_accommodation_multi_source.py::
test_all_missing_files_returns_unavailable_with_clear_message`) had an
`assert "booking.html" in result.message or len(result.warnings) >= 6`
that would have kept silently passing via its own `or` fallback even
after this fix (`"booking.html"` no longer appears) -- tightened to
assert the real absolute path is absent and the safe `"booking"`
source-id label is still present, so the test's own intent stays
accurate rather than accidentally-still-green. Added a new dedicated
regression-guard file,
`backend/app/tests/providers/test_scraped_adapters_no_path_leak.py` (7
tests): single-source and multi-source missing-file scenarios for all
three adapters, each using a real pytest `tmp_path` (a genuinely
absolute path, the same shape as a real machine's) so "the path never
appears in `message`/`warnings`" is a real, specific assertion rather
than a vacuous one, plus one test confirming a real, present file still
produces a real `SUCCESS` result (proving availability logic itself is
untouched).

**Verification**: full suite **3443 passed + 18 skipped** (3436 + 7
new tests, zero change to any pre-existing test's outcome).
`compileall`/`tsc`/`lint`/`build` all clean. No frontend file changed
-- confirmed unnecessary, since the frontend already renders every
provider `message`/`warnings` field generically (no path-specific
frontend logic ever existed to update). No provider/parsing/caching/
availability behavior changed anywhere -- confirmed by the full suite
passing unmodified and by `git diff --stat` touching only the three
adapter files, three existing test files, one new test file, and docs.
## 135. Section 191A: Live AI Candidate Discovery in LangGraph Generation

Section 190D established the exact current LangGraph node order and
found one concrete gap: the `ai_candidate` node was a pure no-op in the
LangGraph engine, and the legacy engine's Step 161B AI candidate
discovery shadow stage had no LangGraph equivalent at all -- shadow mode
only ever ran under `PLANNING_ENGINE_MODE=legacy`, regardless of its own
flag's value, because the LangGraph `destination_context`/
`candidate_quality` nodes call `DestinationContextService.run`/
`CandidateQualityService.build_report` directly, bypassing
`PlanningOrchestrator.run_destination_context_stage`'s wrapping (which is
where the shadow stage call lived) entirely. Section 191A closes that gap
for the LangGraph engine specifically, without touching the legacy
engine's own behavior.

**New, separate production flag.** `Settings.ai_candidate_discovery_enabled`
(alias `AI_CANDIDATE_DISCOVERY_ENABLED`, default `False`) is the real
production switch for the LangGraph `ai_candidate` node -- deliberately
not a reinterpretation of `Settings.ai_candidate_discovery_shadow_mode_enabled`
(Step 161B), which stays a separate, legacy-engine-only observation/
testing path, unread by the LangGraph node. Both flags default to
`False`; setting one never implicitly enables the other. Added to
`.env.example` alongside the existing shadow flag with a comment
explaining the distinction.

**Shared implementation, not a second pipeline.** Rather than duplicate
`PlanningOrchestrator._run_ai_candidate_discovery_shadow_stage`'s/
`_run_ai_candidate_promotion_stage`'s orchestration logic for the
LangGraph node, both were refactored to delegate to two new shared,
fail-safe, plain functions:

- `app.services.ai_candidate_discovery_service.apply_discovery_to_state(planning_state, discovery_service, *, stage_label=...)`
  -- extracted from the shadow stage's pre-191A body verbatim (same
  `destination_context is None` no-op guard, same try/except around
  `discovery_service.dry_run(...)`, same safe structured logging fields).
  `stage_label` defaults to `"ai_candidate_discovery"` for the new live
  call site; the legacy shadow stage passes `"ai_candidate_proposal"`
  explicitly to preserve its own pre-existing log-field contract exactly
  (`backend/app/tests/services/test_provider_llm_logging.py` asserts this
  literal string).
- `app.services.ai_candidate_promotion_service.apply_promotion_safely(planning_state, promotion_service)`
  -- extracted from the promotion stage's pre-191A try/except verbatim.

`PlanningOrchestrator._run_ai_candidate_discovery_shadow_stage`/
`_run_ai_candidate_promotion_stage` now call these two functions instead
of duplicating their bodies -- both methods' own observable behavior,
including every existing shadow-mode test's exact assertions (log fields,
fail-safe-on-raise, idempotency), is completely unchanged.

**The live `ai_candidate` node** (`build_ai_candidate_node` in
`backend/app/graphs/planning_graph_nodes.py`) reads
`Settings.ai_candidate_discovery_enabled` at call time (never at graph-
build time, so a config change takes effect on the next generation
without restarting the process):

```text
if not ai_candidate_discovery_enabled:
    if promotion_service was explicitly injected (test-only, DI ordering
      proofs): call apply_promotion on it -- unchanged pre-191A behavior.
    else: pure no-op, exactly as before Step 191A.
else:
    resolve real (or injected fake) AICandidateDiscoveryService/
      AICandidatePromotionService
    planning_state = apply_discovery_to_state(planning_state, discovery_service,
                                               stage_label="ai_candidate_discovery")
    if planning_state.ai_candidate_proposal_batch is not None:
        planning_state = apply_promotion_safely(planning_state, promotion_service)
```

The inner `if ai_candidate_proposal_batch is not None` guard mirrors
`_run_ai_candidate_promotion_stage`'s own guard exactly: a discovery
failure/no-op leaves `ai_candidate_promotion_report` at `None` rather
than storing a technically-honest-but-pointless empty report -- so both
engines report absence identically.

`AICandidateDiscoveryService`/`AICandidateProposalRequestBuilder`/
`CandidateGroundingRequestBuilder`/`CandidateGroundingService`/
`AICandidateReviewService`/`AICandidatePromotionEligibilityService`/
`AICandidatePromotionService` are all reused completely unmodified except
for the two new shared wrapper functions above -- no new AI-candidate
domain logic, no new grounding rule, no new quality tier, no new
eligibility rule was added or changed. `ai_candidate_discovery_service`
is threaded through `LangGraphPlanningService.__init__` ->
`PlanningGraphRunner.__init__` -> `build_planning_graph` -> the node,
mirroring exactly how every other stage service already flows through
that same chain; `PlanningOrchestrator.__init__` passes its own
already-constructed `self.ai_candidate_discovery_service` through (reused
identically by both engines, same as every other stage service),
`ai_candidate_promotion_service` is deliberately still left unpassed
(`None`) there so a default production LangGraph run with the live flag
off stays a byte-for-byte no-op.

**Grounding stays mandatory.** An AI proposal reaches
`ai_candidate_promotion_report.promoted_candidates` only through the
exact same unmodified `CandidateGroundingService.ground` deterministic
name-matching (exact/normalized, against real
`PlanningState.destination_context` provider candidates only) and the
exact same unmodified 8-rule `AICandidatePromotionEligibilityService`
gate (grounding required, accepted match type/confidence tier, accepted
quality tier via `CandidateQualityReport`, not an accommodation/transport-
category match). Nothing in Step 191A weakens, bypasses, or duplicates
any of these rules -- the live LangGraph stage is a new *caller* of this
machinery, not a new implementation of it.

**Reaching the itinerary required no experience-planner change.**
`ExperiencePlannerService` has read `planning_state.ai_candidate_promotion_report`
and merged any promoted candidates into scheduling since Step 170D,
completely independent of which engine produced the plan -- it is a pure
`PlanningState` read, not an engine-specific integration. Since the
`ai_candidate` node already runs before `trip_strategy`/
`accommodation_inventory`/`flight_inventory`/`experience_planning` in the
existing graph order (unchanged by this step), a promoted candidate is
already available in time; no wiring fix was needed here at all --
confirmed both by a dedicated node-level test
(`test_ai_candidate_node_live_enabled_calls_discovery_and_promotion` in
`backend/app/tests/graphs/test_langgraph_planning_nodes.py`) and by a
real end-to-end `/generate` test proving a promoted candidate reaches a
scheduled `ExperienceItem` with `promoted_from_ai=True`
(`test_live_discovery_enabled_promoted_candidate_reaches_experience_plan`
in the new `backend/app/tests/api/test_langgraph_live_ai_candidate_discovery.py`).

**Fail-safe end to end.** `apply_discovery_to_state`/
`apply_promotion_safely` never raise: a provider timeout, missing API
key, malformed/invalid structured output (a real
`pydantic.ValidationError` while building the result -- the same shape
`test_ai_candidate_discovery_safety.py`'s pre-existing
`_BrokenSchemaAICandidateProposalProvider` fixture already established),
or any other unexpected failure is caught and logged with safe,
secret-free structured fields (`provider`/`stage`/`status`/`error_code`/
`duration_ms` -- never a prompt, raw exception text, or an API key), and
`planning_state` is returned unchanged for that call. The node's own
`try`/`except` is defense-in-depth on top of that, mirroring every other
node in this file.

**Tests.** Import-ban tests in `test_langgraph_planning_graph.py`/
`test_langgraph_planning_nodes.py`/`test_langgraph_planning_service.py`
were updated: `ai_candidate_discovery_service` is now a legitimate,
asserted-present import in all three modules (it was previously banned,
back when the LangGraph engine had no path to it at all); Groq/Anthropic/
OpenAI/LangSmith/`app.providers.*`/`ai_candidate_proposal_provider`
direct imports remain fully banned in all three, preserving the "node/
graph/service -> AICandidateDiscoveryService -> AICandidateProposalProvider
-> adapter" layering (no direct LLM client import ever added to
orchestration code). `test_langgraph_mode_generate_does_not_call_anthropic_or_groq`/
`test_langgraph_mode_generate_does_not_call_ai_candidate_discovery_service`
in `test_langgraph_generate_mode.py` were renamed to make explicit that
they hold only while the live flag is at its default `False` (per this
step's own instruction, since that assertion is no longer universally
true). A new dedicated file,
`backend/app/tests/api/test_langgraph_live_ai_candidate_discovery.py` (10
tests), covers: proposal/grounding batches stored when enabled; a
grounded, quality-eligible candidate actually promoted; the promoted
candidate reaching a real scheduled `ExperienceItem`; an ungrounded
proposal recorded but never promoted or scheduled; a raising provider
never breaking generation; a `pydantic.ValidationError`-raising (invalid
schema) provider never breaking generation or fabricating a candidate;
the provider called exactly once per `/generate`; two structured-logging
assertions (safe fields present, no secret/prompt leak on both success
and failure); and the live flag having zero effect on the legacy engine.
3 new node-level tests were also added to
`test_langgraph_planning_nodes.py` (disabled-by-default, enabled-full-
pipeline, enabled-with-discovery-failure).

**Real local Groq verification.** Ran a genuine, non-mocked
`POST /trips/{id}/generate` against a fresh local backend with
`AI_CANDIDATE_DISCOVERY_ENABLED=true` passed as a process environment
override (never written to the real `.env`) and the already-configured
local `AI_CANDIDATE_PROPOSAL_PROVIDER=groq`/`GROQ_API_KEY` (value never
printed), for a real Lisbon, Portugal / 3-night / balanced-pace trip.
Groq was genuinely contacted (confirmed by `~6-14s` request latency and
`provider_name: "groq_ai_candidate_proposal_provider"` in the stored
`ai_candidate_proposal_batch`) on two separate real attempts. Both times,
the configured model (`GROQ_MODEL=openai/gpt-oss-20b`) failed to invoke
the required tool-call for structured output at the Groq API level
(`"Tool choice is required, but model did not call a tool"`, HTTP 400) --
a pre-existing, unmodified `GroqAICandidateProposalProvider` code path
(untouched by this step) caught this and returned an honest `rejected`
result with zero proposals, exactly per its own documented no-fabrication
contract. `ai_candidate_promotion_report` correctly stayed absent-in-
effect (`status="no_candidates_reviewed"`, `promoted_count=0`) rather
than promoting anything. This is a real, live-verified demonstration of
the fail-safe/no-fabrication half of the contract; the "successful
promotion reaches the itinerary" half of the contract is conclusively
proven instead by the deterministic fake-provider test suite above
(`test_live_discovery_enabled_promoted_candidate_reaches_experience_plan`),
since a real LLM's non-deterministic output is not a reliable basis for
an assertion. The rest of the same real response was independently
verified honest: `provider_coverage`/`accommodation_inventory_report`/
`flight_inventory_report` all matched the same honest `unavailable`/
`not_connected`/`success` values 190D already established for this local
config (routing/narrator both genuinely `success`, both pre-existing,
unaffected by this step); zero occurrences of any absolute filesystem
path anywhere in the response (190C's fix remains intact); zero
occurrences of any forbidden factual field name (`price`/`rating`/
`opening_hours`/`route_time`/`booking_url`/`review_count`/`safety_score`)
anywhere in the response.

**Verification**: full suite **3456 passed + 18 skipped** (3443 + 13 new
tests, zero change to any pre-existing test's outcome).
`compileall`/`pytest` clean. No frontend/shared-contract file changed --
frontend checks explicitly skipped for that reason.

## 136. Section 191A.1: Real Groq Structured-Output Compatibility Fix

Section 191A's own real local verification found the live Groq candidate-
proposal call failing with a genuine HTTP 400 in 2/2 attempts against the
configured `GROQ_MODEL=openai/gpt-oss-20b`. Section 191A.1 diagnosed and
fixed the real, underlying cause without touching any of Section 191A's
LangGraph wiring, grounding rules, promotion eligibility, quality
thresholds, or the Anthropic/narrator paths.

**Root cause 1 (fixed).** `GroqAICandidateProposalProvider._build_client`
called `chat.with_structured_output(_GroqProposalBatchSchema)` with no
explicit `method` -- `langchain_groq` (installed version 1.1.3) defaults
`with_structured_output`'s `method` parameter to `"function_calling"`,
which binds `_GroqProposalBatchSchema` as a fake tool and forces
`tool_choice` (`bind_tools([schema], tool_choice=tool_name, ...)`,
confirmed by reading the installed package's own source directly, not
assumed). The real Groq API rejected this with HTTP 400: `"Tool choice
is required, but model did not call a tool"` (`code: tool_use_failed`).
Per Groq's own Structured Outputs documentation, Structured Outputs and
tool use should never be combined in one request. **Fix:** pass
`method="json_schema", strict=True` explicitly. Reading `langchain_groq`'s
`json_schema` branch directly confirms it calls `.bind(response_format=...)`
-- never `.bind_tools(...)` -- so no `tools`/`tool_choice` field is ever
sent in this mode. `strict=True` is safe for the configured model:
`langchain_groq` maintains its own allowlist
(`_STRICT_STRUCTURED_OUTPUT_MODELS`), `openai/gpt-oss-20b` is on it, and
an unsupported `strict=True` for any other model is silently downgraded
by the library itself rather than raising.

**Root cause 2 (fixed).** After fixing root cause 1, a second, distinct
real HTTP 400 appeared: `code: json_validate_failed`,
`"Failed to validate JSON. Please adjust your prompt."`. Groq's own
Structured Outputs documentation lists only string/number/boolean/
integer/object/array/enum as supported schema primitives and does not
document `minItems`/`minimum`/`maximum` support for strict mode (checked
directly against console.groq.com/docs/structured-outputs, not
guessed) -- `_GroqProposalSchema.verification_requirements`
(`min_length=1` -> `minItems: 1`) and both `confidence` fields
(`ge=0.0, le=1.0` -> `minimum`/`maximum`) were the culprits. **Fix:**
removed these three constraints from the Groq-specific wire schema only.
Nothing about domain validation weakened: `AICandidateProposal.
verification_requirements` (`app/models/ai_candidate_proposal.py`)
already independently carries the same `min_length=1`, so a genuinely
empty list from Groq still fails `AICandidateProposal(**raw_proposal)`
construction and is still converted to an honest `rejected` result, same
as before; `_build_result_from_output`'s existing
`confidence = max(0.0, min(1.0, float(confidence)))` clamp (unchanged)
still independently bounds the batch-level value, and
`AICandidateProposal.confidence` (`ge=0.0, le=1.0`, unchanged) still
independently bounds each proposal's value.

**Root cause 3 (fixed).** After fixing roots 1-2, a third real response
showed the model producing genuinely valid, correctly-shaped, on-topic
JSON (visible in the error's own `failed_generation` field: real
Lisbon-relevant candidates like "Alfama district"/"Belém Tower") but
still hit HTTP 400 (`json_validate_failed`) because the response was
truncated by `max_tokens=1200` before the required trailing
`rejected_raw_items` key could be emitted. **Fix:** raised the adapter's
default `max_tokens` from 1200 to 4000 -- comfortably covering up to
`max_candidates=15` (the existing `dry_run` default) full proposals with
headroom, diagnosed from the real truncated payload rather than guessed.

**All three fixes are wire-schema/request-construction only.** The
domain contract (`AICandidateProposal`, `AICandidateProposalRequest`,
`AICandidateProposalResult`, `AICandidateProposalBatch`) is completely
untouched; `AICandidateProposalProvider.propose(request) ->
AICandidateProposalResult` stays the exact same interface every caller
already used. `_coerce_output`/`_build_result_from_output`'s downstream
parsing and validation logic is unchanged -- `PydanticOutputParser`
(used in `json_schema` mode, confirmed by reading the library source)
returns the same `_GroqProposalBatchSchema` Pydantic instance shape
`PydanticToolsParser` (the old `function_calling` mode) did, so
`_coerce_output`'s existing dict-or-`model_dump()` handling needed no
change. `CandidateGroundingService`/`AICandidatePromotionEligibilityService`/
`ExperiencePlannerService`/the itinerary narrator/OSRM routing/the
Anthropic adapter are all completely unmodified.

**Tests.** Two pre-existing tests were updated for the new method/schema
shape (`test_successful_response_via_structured_output_schema_instance`'s
fixture now includes the three previously-optional-but-now-always-
present keys; `test_explicit_api_key_forwarded_to_real_client_builder`'s
fake `ChatGroq.with_structured_output` now accepts `method`/`strict`
kwargs and asserts their values). Five new tests were added:
`test_build_client_uses_structured_outputs_not_tool_calling` (asserts
`method="json_schema"`/`strict=True` reach the real client-construction
call), `test_build_client_never_sends_tools_or_tool_choice` (asserts no
`tools`/`tool_choice` keyword anywhere in that call, with a defensive
`bind_tools`-raises fake to catch any future regression loudly),
`test_build_client_does_not_leak_api_key_into_structured_output_call`,
`test_real_groq_400_tool_choice_error_is_classified_and_sanitized`
(reproduces the exact real HTTP 400 body from live verification as a
fake-client exception, asserting an honest `rejected` result with no
secret/header leak), and
`test_valid_json_schema_response_still_goes_through_pydantic_validation`
(a schema-constrained-shaped response still fails the existing forbidden-
factual-claim guard, proving Groq's own schema constraint is never
trusted as a substitute for application-level validation). All Section
191A integration tests
(`backend/app/tests/api/test_langgraph_live_ai_candidate_discovery.py`,
the node/graph/service tests) continue to pass unmodified -- their fake-
provider scenarios never depended on the real adapter's internal request
mechanics.

**Real Groq verification (2 real runs, same local `.env` `GROQ_API_KEY`,
never printed).** Both runs used the same Lisbon/Portugal, 3-night,
balanced-pace, food/history/walking trip shape as Section 191A's. Run 1:
`proposal status: completed`, 10 real proposals (Alfama District, Belém
Tower, Chiado, LX Factory, Miradouro da Senhora do Monte, Museu Nacional
de Arte Antiga, Parque das Nações, Time Out Market Lisboa, Bairro Alto,
Fado Museum -- all genuine, relevant Lisbon points of interest, not
malformed or off-topic output). Run 2: `proposal status: completed`, 15
proposals (a similar, equally relevant set, including Oceanário de
Lisboa, Praça do Comércio, Santa Justa Lift). In both runs grounding
correctly rejected all proposals (`no_provider_match` -- this local dev
environment's real OSM-backed destination-context candidates for
Lisbon/Testville-style trips don't happen to share an exact/normalized
name with the LLM's free-text place names) and `ai_candidate_promotion_report`
correctly reported `no_eligible_candidates`/`promoted_count: 0` -- an
honest, expected outcome given this specific candidate pool, not a
defect; Section 191A's fake-provider integration tests already
conclusively prove the full grounding -> promotion -> scheduling chain
works once a name does match. Both runs: zero absolute filesystem paths,
zero forbidden factual field names, `route_feasibility_report.status`
and `itinerary_narrative_report.status` both `success` (both pre-
existing, confirming the narrator's separate Groq-backed path remains
fully unaffected by this adapter change), and a real, provider-backed
itinerary generated successfully both times.

**Verification**: full suite **3461 passed + 18 skipped** (3456 + 5 new
tests, zero change to any pre-existing test's outcome). `compileall`/
`pytest` clean. No frontend/shared-contract file changed -- frontend
checks explicitly skipped for that reason.

## 137. Section 191B: Provider-Search-Ready AI Candidate Proposals

Section 191A.1's real verification exposed the next architectural gap,
not a bug: real Groq proposals were reasonable Lisbon places, but
`CandidateGroundingService`'s deterministic exact/normalized name
matching had nothing to match them against, because free-text LLM names
essentially never share an exact/normalized string with the real
provider candidate pool (`grounded: 0`, `promoted: 0` even with 10-15
genuinely relevant proposals per run). Section 191B does **not** fix
that by weakening grounding or adding fuzzy "trust the AI" matching --
grounding is completely untouched. Instead it redesigns the *proposal
contract* so a future provider-search layer (Section 192, not built
here) has something real to search for.

**An `AICandidateProposal` is not a verified place -- and now it is
explicit about which of two things it is.**
`AICandidateProposal.proposal_type` (new field,
`app/models/ai_candidate_proposal.py`) is one of:

- `named_place`: the LLM names a specific place worth checking
  (`candidate_name` required, non-blank). Behaves exactly as every
  pre-191B proposal did -- `CandidateGroundingService`'s existing exact/
  normalized name matching still attempts to ground it, completely
  unmodified.
- `discovery_query`: the LLM expresses an experience/category need
  (`candidate_name` may be `None`) rather than inventing a specific
  establishment. `search_query` (new field, always present, provider-
  search-friendly) is the only thing Section 192's future provider
  search will have to go on. `CandidateGroundingService` now branches on
  `proposal_type` first (`candidate_grounding_service.py::_ground_one`)
  and rejects every `discovery_query` immediately with a new reason,
  `CandidateGroundingRejectReason.DISCOVERY_QUERY_AWAITING_PROVIDER_SEARCH`
  (`app/models/candidate_grounding.py`) -- proven even when a supplied
  provider candidate's name is identical to the search phrase (a real
  test case), so this is a `proposal_type` branch, not merely "was
  `candidate_name` set."

**Backward compatible by construction.** `proposal_type` defaults to
`named_place`; `search_query` defaults to `candidate_name` when a
`named_place` proposal doesn't set it explicitly
(`AICandidateProposal.validate_proposal_type_contract`). Every pre-191B
construction of this model (both LLM adapters' pre-191B raw output
shape, every existing test fixture) keeps validating and behaving
exactly as before with zero changes required.

**Both LLM adapters carry the same domain semantics.** Groq's
`_GroqProposalSchema` (`groq_adapter.py`) adds `proposal_type`/
`search_query` as required keys under Groq's strict `json_schema` mode
(unchanged: `response_format=json_schema`, `strict=True`, no
`tools`/`tool_choice` -- Section 191A.1 is untouched); `candidate_name`'s
*key* stays required (Groq strict mode requires every key present) but
its *value* is now nullable, with the conditional "required when
named_place" rule enforced one layer down by the domain model, not by
the wire schema. Anthropic's tool `input_schema`
(`anthropic_adapter.py`) mirrors the same two fields, but as ordinary
optional/required JSON Schema (`candidate_name` genuinely omittable,
`proposal_type`/`search_query` required) since Claude's tool use has no
strict-mode key-presence requirement. Both system prompts were rewritten
to explain the two proposal kinds, tell the model to prefer a
`discovery_query` over inventing an obscure/uncertain place, and ask for
provider-search-friendly `search_query` values, a mixture of high-
priority anchors and supporting ideas, and non-duplicate coverage --
without asking for any factual detail a provider must supply.

**Deterministic, proposal-level deduplication** (new module,
`app/providers/ai_candidate_proposal/proposal_dedup.py`,
`deduplicate_proposals`, shared by both adapters so this logic exists
exactly once). Collapses proposals whose normalized lookup key (accent-
stripped, lowercased, punctuation-stripped, leading-article-stripped
`candidate_name`/`search_query`) is identical, keeping the first
occurrence -- e.g. "Belém Tower" / "Belem Tower!" collapse, but a
translation like "Torre de Belém" deliberately does not (that is a
semantic judgment, explicitly out of scope; only deterministic
normalization is used). A `named_place` and a `discovery_query` sharing
the same words never collapse into each other. This is unrelated to,
and does not touch, `CandidateGroundingService`'s own matching.

**`AICandidateReviewItem` gained one additive field**,
`proposal_type: str` (`app/models/ai_candidate_review.py`, defaults to
`"named_place"` for backward compatibility), and its `name` now falls
back to `search_query` when `candidate_name` is `None`
(`ai_candidate_review_service.py`) -- purely a display concern; a
`discovery_query` item still can never be reported
`eligible_for_promotion=True` (it never reaches `build_report` with
`provider_grounded=True`, since grounding rejects it immediately).
Promotion (`AICandidatePromotionService`) is completely unmodified and
untouched by this step -- it already only ever promotes items the
review report already marked eligible.

**No new config.** `AI_CANDIDATE_DISCOVERY_ENABLED`,
`AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED`, and the LangGraph node
order (`traveler_profile -> destination_context -> candidate_quality ->
ai_candidate -> trip_strategy`) are all unchanged. Section 192, not this
step, will change how AI proposals drive provider discovery.

**Tests.** 33 new tests added across
`tests/models/test_ai_candidate_proposal_models.py` (named_place/
discovery_query contract, no-provider-fact-field serialization),
`tests/services/test_candidate_grounding_service.py` (discovery_query
always rejected with the new reason, even against a name-matching
provider candidate; mixed named_place+discovery_query batches),
`tests/services/test_ai_candidate_discovery_safety.py` (a
`discovery_query` can never be grounded or promoted merely because the
LLM emitted it, exercised through the real `AICandidateReviewService`/
`AICandidatePromotionService`), a new
`tests/providers/test_ai_candidate_proposal_dedup.py` (9 tests covering
exact/accent/article variants, translation non-collapse, distinct-type
non-collapse, order preservation), and both adapter test files (discovery-
query wire-shape round-trips, adapter-level dedup, schema
required/property assertions). One pre-existing Groq fixture
(`_valid_proposal_dict` in `test_groq_ai_candidate_proposal_provider.py`)
was updated to include the two new always-present strict-mode keys,
matching the same pattern Section 191A.1 already used for that file's
other always-present keys. Every Section 191A/191A.1 test continues to
pass unmodified.

**Real Groq verification (2 real runs, same local `.env`
`GROQ_API_KEY`, never printed, `GROQ_MODEL=openai/gpt-oss-20b`,
Lisbon/Portugal, 3-day, food/history/walking, mid-range budget).** Run
1: `completed`, 10 proposals (2 `named_place`: Time Out Market Lisboa,
LX Factory; 8 `discovery_query`: Alfama historic walk, Miradouro de
Santa Catarina sunset view, National Tile Museum, traditional food
market Lisbon, Parque Eduardo VII walk, Bairro Alto nightlife walk,
Miradouro da Senhora do Monte, MAAT contemporary art museum), categories
spanning neighborhood/viewpoint/museum/food_area/park/cultural_area, zero
duplicates, zero malformed proposals, zero provider-fact-field
violations. Run 2: `completed`, 10 proposals (6 `named_place`: Belém
Tower, Jerónimos Monastery, Mercado da Ribeira, LX Factory, São Jorge
Castle, Miradouro da Senhora do Monte; 4 `discovery_query`: historic
neighborhood walk Lisbon, traditional food market Lisbon, sunset
viewpoint Lisbon, historic neighborhood walk Alfama), same clean safety
result. Both runs graded against a small synthetic `destination_context`
(Belem Tower, Alfama) deliberately kept sparse/unrelated rather than
hand-fitted to the LLM's output: grounding correctly rejected all 10
proposals both times (`no_provider_match` for `named_place` proposals --
none happened to share an exact/normalized string with the synthetic
pool, including a real near-miss, "Belém Tower" vs "Belem Tower," that
normalized matching intentionally does not collapse since it does not
strip accents; `discovery_query_awaiting_provider_search` for every
`discovery_query`), exactly the honest, expected `grounded: 0` outcome
Section 191B's own scope explicitly allows -- Section 192 is what will
give `named_place`/`discovery_query` proposals a real provider pool to
resolve against.

**Quality assessment of the real output**: proposals clearly reflect the
stated interests (food/history/walking dominate both runs); every
`search_query` is short and directly usable as a provider search string;
no redundant near-duplicates survived (and the dedup helper is exercised
by unit tests even though these two particular real runs happened not to
produce raw duplicates); generic experience needs came back as
`discovery_query` search phrases rather than invented establishments
(more so in Run 1: 8 of 10); every `named_place` reads as a lookup hint,
never as a claimed-verified fact, in both its own `why_consider` prose
and its guaranteed `not_connected`/`rejected`-until-grounded status; both
runs stayed within the diversity-without-forcing-every-category
guidance (no accommodation/flight/transport proposals appeared, matching
the existing category-keyword promotion-eligibility floor this step
never touched).

**Verification**: full suite **3494 passed + 18 skipped** (3461 + 33 new
tests, zero change to any pre-existing test's outcome). `compileall`/
`pytest` clean. No frontend file or shared API response shape changed --
frontend checks explicitly skipped for that reason.

## 138. Section 192: AI-Directed Provider Discovery From Candidate Search Intents

Section 191B gave LLM #1 a structured `named_place`/`discovery_query`
contract, but real verification still showed `grounded: 0` -- the
deterministic exact/normalized grounding pool had nothing to match a
`discovery_query`'s free-text `search_query` against, and a `named_place`
whose exact wording didn't already exist in the broad `destination_context`
pool stayed rejected too. Section 192 closes that gap by giving both kinds
of proposal an actual, targeted, provider-backed lookup -- an
**additional** path alongside the existing broad `destination_context`
discovery, never a replacement for it.

**New flow** (inserted between AI proposal and grounding, inside
`AICandidateDiscoveryService.dry_run` -- no LangGraph node/order change):

```text
AICandidateProposal (named_place | discovery_query)
  -> AIDirectedProviderDiscoveryService.discover
       (ProviderGateway.places.search_must_visit_place, real provider call)
  -> AIProviderDiscoveryResult (proposal_id -> real ProviderCandidateForGrounding, or an honest non-match)
  -> CandidateGroundingRequest.ai_directed_matches
  -> CandidateGroundingService.ground (one call, unchanged public signature)
       named_place:  broad-pool exact/normalized match first, ai_directed_matches only as fallback
       discovery_query: ai_directed_matches only (never broad-pool name matching)
  -> review -> promotion (both completely unmodified)
```

**New service**: `AIDirectedProviderDiscoveryService`
(`app/services/ai_directed_provider_discovery_service.py`). Reaches
provider data only through `ProviderGateway.places.search_must_visit_place`
-- the exact same targeted single-result Nominatim lookup
`DestinationContextService._append_must_visit_candidates` already uses for
a traveler's explicit must-visit terms (Step 155C/section 10), reused here
for an AI-proposed search intent/name instead. Never a direct
OpenStreetMap/Nominatim/httpx import, matching the required `service ->
ProviderGateway -> provider adapter` layering. Never calls an LLM, never
calls `CandidateGroundingService` itself, never mutates `PlanningState`.

A proposal only gets a real lookup if it needs one:
`find_broad_pool_name_matches` (extracted, unchanged logic, from
`CandidateGroundingService._ground_one` into a shared, importable function
in `candidate_grounding_service.py`) decides whether a `named_place`
proposal already has exactly one clean broad-pool match; if so, it is
skipped entirely (no lookup, doesn't count against the search bound). A
`discovery_query` proposal always needs a lookup (it has no broad-pool name
to match by at all). This keeps the bound meaningful and avoids a wasted
provider call for a proposal broad matching would have handled anyway.

**Bound** (Task 8): `Settings.ai_directed_provider_discovery_max_searches`
(`AI_DIRECTED_PROVIDER_DISCOVERY_MAX_SEARCHES`, default `5`) caps how many
real lookups one `dry_run` call performs; a proposal beyond the bound is
recorded honestly as `not_searched`, never silently dropped. No separate
"max results per proposal" setting was needed --
`search_must_visit_place`'s own existing contract already returns at most
one result. `.env.example` still does not exist anywhere in this repo
(pre-existing gap, per this file's Core Rules note) -- this one new setting
was not used as the occasion to create one; it is documented here and in
`Settings` itself instead.

**New models** (`app/models/ai_provider_discovery.py`):
`AIProviderDiscoveryAttemptStatus` (`matched`/`not_found`/
`provider_failed`/`provider_not_connected`/`not_searched`),
`AIProviderDiscoveryAttempt` (one proposal's outcome; `match` is a real
`ProviderCandidateForGrounding` -- Step 159A's existing provider-evidence
model, reused rather than duplicated -- required exactly when
`status="matched"`, structurally forbidden otherwise),
`AIProviderDiscoveryResult` (the batch rollup, with a
`matches_by_proposal_id()` helper). No coordinate/price/rating/etc. is
ever asserted by these models except through a real `ProviderResponse`
returned from `ProviderGateway.places`.

**Grounding extension, not weakening** (`CandidateGroundingRequest` gains
`ai_directed_matches: dict[proposal_id, ProviderCandidateForGrounding]`,
default `{}` so every pre-Section-192 request/test is unaffected;
`CandidateGroundingService._ground_one` checks it only as a *fallback*):
a `discovery_query` proposal grounds via `ai_directed_matches` or stays
rejected with the existing (unremoved)
`DISCOVERY_QUERY_AWAITING_PROVIDER_SEARCH` reason -- now honestly reused
for "a provider search ran and found nothing," not just "no search ran
yet," per Task 6's own instruction. A `named_place` proposal still tries
broad-pool exact/normalized matching first (byte-for-byte the same
behavior as before this step: a clean broad match always wins and is
never overridden by a directed match, proven by a dedicated test), falling
back to `ai_directed_matches` only when broad matching found zero or
`>1` candidates. Both fallback paths use the existing
`CandidateGroundingMatchType.TARGETED_LOOKUP` enum member (defined since
Step 158A, never previously produced by any code path) -- already an
`_ACCEPTED_MATCH_TYPES` member in
`AICandidatePromotionEligibilityService` (Step 170B), so promotion
eligibility needed zero changes to accept a Section 192 match. The
`ground()` "no evidence at all -> not_connected" guard now checks both
`provider_candidates` and `ai_directed_matches` before reporting
`not_connected`, so a request with real directed evidence but an empty
broad pool is never misreported.

**PlanningState integration**: `ai_provider_discovery_result:
AIProviderDiscoveryResult | None` (new field, stays `None` until
`apply_discovery_to_state` runs, same gating as `ai_candidate_proposal_batch`),
stored *alongside*, not merged into, `candidate_grounding_batch` -- every
match this step actually contributed to grounding is already visible there
too (via `match_type=targeted_lookup`), so this field is purely additional,
inspectable detail for debugging/evaluation (Task 11's "distinguish broad
vs. AI-directed discovery"), never a second candidate pool competing with
`destination_context`.

**Known, honest limitation surfaced by real verification (not fixed in
this step, not asked for):** a proposal grounded only via Section 192
(`match_type=targeted_lookup`) was never scored by `CandidateQualityService`
(that stage only scores the pre-existing broad `destination_context` pool,
untouched by this step) -- so `AICandidatePromotionEligibilityService`
Rule 4 ("a quality score must exist") honestly keeps it un-promoted even
after a successful real grounding. This is a real architectural gap, not
a bug: grounding correctness was not weakened to paper over it, and it is
called out explicitly (with a passing test asserting the honest
`skipped_candidate_ids` outcome) rather than silently left undocumented.
Wiring `CandidateQualityService` to also score Section 192 matches is
future work, not attempted here.

**Real Groq + real OpenStreetMap/Nominatim verification (2 real runs,
same local `.env` `GROQ_API_KEY`/no key printed, `GROQ_MODEL=openai/gpt-oss-20b`,
`AIDirectedProviderDiscoveryService` backed by the real
`OpenStreetMapPlacesAdapter`, Lisbon/Portugal, 3-day, food/history/walking,
mid-range budget, graded against the same small synthetic
`destination_context` -- Belem Tower, Alfama -- Section 191B's
verification used, deliberately sparse/unrelated rather than hand-fitted).**
Run 1: 15 proposals (6 `named_place`, 9 `discovery_query`); all 15 needed a
targeted lookup (none matched the synthetic broad pool). 6/6 `named_place`
lookups matched a real provider place (Time Out Market Lisboa, a
"Portugal Essential" tour listing for "Alfama district", Miradouro de São
Pedro de Alcântara, Museu Nacional de Arte Antiga, Parque Eduardo VII,
Bairro Alto); 0/9 `discovery_query` lookups matched (generic category
phrases like "historic neighborhood walk"/"traditional food market" don't
geocode -- Nominatim is a place-name search engine, not a category/
semantic search engine, an honest, expected limitation this step's own
task description anticipated: "If OSM/provider search legitimately finds
no matching candidate, report it honestly"). Grounding: `partial`, 6
grounded (all `targeted_lookup`), 9 rejected
(`discovery_query_awaiting_provider_search`). Run 2 (after one real, known,
pre-existing intermittent Groq `json_validate_failed` HTTP 400 on the
first attempt -- Section 191A.1's own documented failure mode, retried):
10 proposals (5 `named_place`, 5 `discovery_query`); 4/10 lookups matched,
including **"Belém Tower Lisbon" correctly resolving to the real place
"Torre de Belém"** (way/24341353) -- exactly Section 192's own target-
architecture example, achieved with a real key against a real provider,
not simulated. Grounding: `partial`, 4 grounded, 6 rejected (mix of
`discovery_query_awaiting_provider_search` and, for the one `named_place`
whose own targeted lookup also came back empty, the pre-existing
`no_provider_match`).

**Quality/honesty note from both real runs**: not every targeted-lookup
match was a good one -- "Alfama district"/"Alfama Lisbon" both resolved to
an unrelated business listing ("Portugal Essential"/"Made In Portugal")
rather than the neighborhood itself, a real, pre-existing characteristic
of `search_must_visit_place`'s single-top-Nominatim-result design (it
already had this exact risk for a traveler's own must-visit terms before
this step; Section 192 did not change or worsen it). This was left exactly
as-is per this step's strict boundary ("do not add fuzzy provider
grounding") -- Nominatim's own top-result ranking is the provider's
answer, taken as real provider evidence and reported/grounded honestly
under `TARGETED_LOOKUP`/`matched_name`, never silently upgraded to a
higher-confidence tier or presented as more certain than a single
targeted-lookup result actually is.

**Tests**: 49 new tests, zero changes to any pre-existing test's
assertions or outcome --
`tests/models/test_ai_provider_discovery_models.py` (12: match-required-
iff-matched contract, count-consistency validators, no-forbidden-fields),
`tests/core/test_ai_directed_provider_discovery_config.py` (5: default,
env override, negative rejected, zero allowed),
`tests/services/test_ai_directed_provider_discovery_service.py` (15:
discovery_query resolves/zero-results/provider-failure/not-connected/
raises, named_place already-matched skips search, named_place fallback
succeeds/fails, ambiguous broad match still falls back, search bound
limits real calls including a zero-bound case, match fields provably
originate from the fake provider response not the proposal, mixed-
proposal independent outcomes), `tests/services/test_candidate_grounding_service.py`
(+8: directed-match grounding for both proposal types, a clean broad
match is never overridden, ambiguous-broad-match fallback, `not_connected`
correctly not reported when only directed evidence exists, the extracted
`find_broad_pool_name_matches` helper itself, unknown-proposal-id
rejected by the new request validator), `tests/services/test_ai_candidate_discovery_service.py`
(+7: `provider_discovery_result` present on every `dry_run`, end-to-end
discovery_query grounds via a fake targeted lookup, named_place with an
existing broad match never spends a lookup, zero-results and raised-
exception provider failures both leave `dry_run` honest and non-crashing,
default/injected `provider_discovery_service` wiring), and
`tests/api/test_langgraph_live_ai_candidate_discovery.py` (+2: a real
`/generate` call end-to-end grounds a `discovery_query` via a fake
targeted lookup and honestly reports the resulting quality-score gap
rather than asserting a false promotion; the pre-existing "ungrounded
proposal" API test now also asserts `ai_provider_discovery_result` is
present in the response envelope with the correct
`provider_not_connected` attempt, since the globally deterministic
`DeterministicTestPlacesProvider` test double -- `conftest.py`, autouse
for the whole suite -- never overrode `search_must_visit_place`, so it
already, correctly, safely defaults to the base `PlacesProvider` class's
honest `not_connected` response with no code change needed here).

**Strict boundaries honored**: LangGraph node order unchanged (verified by
every pre-existing `test_langgraph_planning_graph.py`/
`test_langgraph_planning_nodes.py` test passing unmodified); broad
`destination_context` discovery completely untouched (no edit to
`destination_context_service.py`); no fuzzy/trust-based grounding added
(`find_broad_pool_name_matches` is the exact pre-existing exact/normalized
predicate, `ai_directed_matches` is identity-keyed by `proposal_id`, never
a second text-similarity pass); no LLM coordinate/fact ever trusted (every
`AIProviderDiscoveryAttempt.match` traces to a real `NormalizedPlace` a
`PlacesProvider` call returned); routing/accommodation/flights/narrator/
feedback/regeneration/forking untouched; no new third-party dependency.

**Verification**: full suite **3543 passed + 18 skipped** (3494 + 49 new
tests, zero change to any pre-existing test's outcome). `compileall`/
`pytest` clean. No frontend file changed; the one new `PlanningState`
field (`ai_provider_discovery_result`) is additive to an already-partial,
hand-maintained frontend type mirror (`frontend/lib/types.ts` already
omits many backend fields by convention) -- frontend checks explicitly
skipped for that reason.

## 139. Section 192.1: AI-Directed Provider-Search Bound Verification

Section 192's own real-verification report showed 15 and 10 "targeted
searches" against a configured `AI_DIRECTED_PROVIDER_DISCOVERY_MAX_SEARCHES`
default of 5 -- an apparent contradiction. Root cause, traced end to end
(Task 1): **the manual verification script for Section 192, not
production code, explicitly passed `max_searches=15`/an unbounded value
into `AIDirectedProviderDiscoveryService.discover(...)` "so this manual
verification isn't artificially starved"** (that script's own comment).
The number reported was therefore a real count of real provider calls --
just made under a deliberately raised bound the script chose for its own
exploratory purposes, never the production default. Every actual
production call site (`AICandidateDiscoveryService.dry_run`, the only
caller) has never passed `max_searches` at all, always falling through to
`get_settings().ai_directed_provider_discovery_max_searches`; a local
`get_settings()` check confirms this repo's `.env` carries no override
and the effective value is the coded default, `5`. Production wiring was
never broken (Task 4: no fix needed there), and the enforcement logic
itself (`searches_used >= bound` before every real call) was already
correct and already covered by passing tests -- so `actual provider
lookup calls <= configured max` was true in production all along. This
step corrects the record (Task 10): "targeted searches: 15/10" in Section
192's report meant real calls under a raised verification-only bound, not
a production discrepancy.

**One real, intentionally scoped production change was still made**
(Task 6, not previously implemented): when more proposals need a lookup
than the bound allows, `AIDirectedProviderDiscoveryService.discover` now
selects deterministically by `priority_hint` (high, then medium, then
low, then unknown), tie-broken by original proposal order -- never
random, never an LLM call. Before this step, the naive first-come
proposal-list order was already deterministic but not priority-aware (a
`low`-priority proposal early in the LLM's own list could out-compete a
`high`-priority one later in it for a scarce search slot). `attempts` is
still reported in original proposal order for readability; only the
*selection* of which eligible proposals actually spend a real call is
priority-first. The existing `AIProviderDiscoveryAttemptStatus.NOT_SEARCHED`
member (already added in Section 192, matching this step's own suggested
`skipped_search_limit` concept) needed no new enum value -- it already
means exactly "the search bound was reached," and its message text was
reworded slightly to the more explicit phrasing this step's Task 7 asked
for ("Provider search was not executed because the targeted-search limit
for this generation was already reached").

**Count definitions, made explicit** (Task 2): `proposal_count` (every
proposal the LLM returned) is distinct from `eligible_lookup_count`
(proposals that actually need a lookup -- excludes any `named_place`
already cleanly matched by the broad `destination_context` pool, which
counts against neither the eligible count nor the search bound) is
distinct from `actual_provider_lookup_attempts` (`searched_count`, real
calls to `ProviderGateway.places.search_must_visit_place`, always `<=`
the configured bound) is distinct from `provider_results_found`
(`matched_count`, the subset of real calls that actually returned a
usable place). The cap only ever governs
`actual_provider_lookup_attempts` -- it was never applied to, and does
not affect, the existing broad `search_attractions`/`search_restaurants`/
`search_accommodation_pois` calls `DestinationContextService` already
makes independently.

**Tests** (7 new, `tests/services/test_ai_directed_provider_discovery_service.py`,
22 tests total in that file, zero changes to the 15 pre-existing ones):
exact-limit (15 eligible, `max_searches=5` -> exactly 5 real calls, 10
`not_searched`), under-limit (3 eligible, `max_searches=5` -> exactly 3
calls), shared budget (3 `named_place` fallback + 7 `discovery_query`
eligible proposals, `max_searches=5` -> exactly 5 total real calls across
both types combined, never 5 per type), deterministic priority selection
(high before medium before low before unknown, ties by original order --
asserted against a specific expected id set), identical selection across
two repeated calls with the same input, `NOT_SEARCHED` proven distinct
from `not_found`/`failed`/`not_connected` (a provider double that would
answer `unavailable` for anything actually asked, proving the skipped
ones were never asked at all), and skipped proposals never producing a
match (`matches_by_proposal_id()` disjoint from the skipped id set). The
existing zero-bound test (`test_search_bound_zero_means_no_lookups_at_all`,
Section 192) already covered `max_searches=0`; `Settings.
ai_directed_provider_discovery_max_searches` already permits `0` by
design (a legitimate "disable targeted lookups without touching
`AI_CANDIDATE_DISCOVERY_ENABLED`" configuration, `ge=0`) so no contract
change was needed there either.

**Real verification** (real Groq + real OpenStreetMap/Nominatim, same
Lisbon/3-day/food-history-walking shape, default `.env`-derived
`max_searches=5`, no override passed anywhere in this run). Two real
Groq attempts failed with the same known, pre-existing intermittent
`json_validate_failed`/truncation HTTP 400 already documented in Sections
191A.1/192 (unrelated to this step, not retried-away by any code change
here) before a completed response arrived; this is reported honestly, not
hidden. Successful run A: `proposal_count=10`, `eligible_lookup_count=10`,
`configured_max_searches=5`, `actual_provider_lookup_attempts=5`,
`provider_results_found=3`, `skipped_due_to_search_limit=5`; grounded 3,
rejected 7. Successful run B: `proposal_count=10`, `eligible_lookup_count=10`,
`configured_max_searches=5`, `actual_provider_lookup_attempts=5`,
`provider_results_found=5`, `skipped_due_to_search_limit=5` (real matches
included "Belém Tower" -> "Torre de Belém" way/24341353, "Jerónimos
Monastery" -> "Mosteiro dos Jerónimos" relation/3258300, and "Time Out
Market Lisboa" matching itself exactly); grounded 5, rejected 5. Both
real runs independently confirm `actual_provider_lookup_attempts <=
configured_max_searches` even though `eligible_lookup_count` was double
the bound in both cases -- the invariant this step set out to verify.

**Strict boundaries honored**: no change to AI proposal generation, Groq
prompts, grounding semantics/confidence rules, candidate-quality
behavior, promotion, experience planning, LangGraph node order, narrator,
routing, or regeneration; no new dependency; nothing committed.

**Verification**: full suite **3550 passed + 18 skipped** (3543 + 7 new
tests, zero change to any pre-existing test's outcome). `compileall`/
`pytest` clean. No frontend/shared-contract file changed -- frontend
checks explicitly skipped for that reason.

## 140. Section 192A: Quality-Scoring Provider-Backed AI-Directed Candidates

Section 192's own real verification produced real grounded candidates
(e.g. "Belém Tower Lisbon" -> real OpenStreetMap place "Torre de Belém",
`match_type=targeted_lookup`) that still never got promoted, because
`AICandidatePromotionEligibilityService`'s Rule 4 requires a
`CandidateQualityScore`, and `CandidateQualityService.build_report` only
ever scores `PlanningState.destination_context`'s own broad candidate
collections -- a Section 192 targeted-lookup result was never a member of
any of them.

```text
AI proposal -> provider lookup -> grounding -> CandidateQualityScore -> promotion
   OK             OK                OK              missing             blocked
```

**One quality policy, not two.** `CandidateQualityService` gained one new
public method, `score_provider_backed_candidate(place, candidate_type_hint,
user_interests=None, must_visit_names=None)` -- it adds only a
classification dispatch on top of the pre-existing `score_attraction`/
`score_restaurant`/`score_accommodation_poi` methods (already pure,
already reusable on any single `NormalizedPlace`, not structurally tied to
`destination_context` membership at all -- confirmed by inspection before
writing a single line, per this step's own Task 1). No new scoring
weight, threshold, tier, or heuristic exists anywhere in this step. A test
(`test_provider_backed_candidate_uses_same_scoring_math_as_score_attraction`)
asserts byte-identical output between the dispatcher and calling
`score_attraction` directly for an attraction-classified input.

**Classification prefers real provider category over the AI's own
label** (Task 3): a `matched_category` recognized by `score_restaurant`'s
own keyword sets, or by an accommodation keyword, routes there regardless
of what the AI proposed. Only when the provider category is uninformative
does the AI's `candidate_type` (passed as a plain string, e.g. `"food_area"`
-- never the enum itself, so this module gains no import dependency on
`app.models.ai_candidate_proposal`) get used, as a fallback signal for
restaurant classification only. Everything else defaults to
`score_attraction`, the safest existing generic path.
`AICandidateType` has no accommodation member today, so that branch here
is reachable only via a real provider category match -- documented, not
worked around.

**Only real, grounded, targeted-lookup candidates are ever scored**
(Task 4). `AICandidateDiscoveryService.dry_run` gained one step after
grounding: `_score_ai_directed_grounded_candidates` filters
`grounding_result.grounded_candidates` down to `match_type=targeted_lookup`
only (a broad-pool match is already scored by the separate
`build_report` pass, untouched) and scores each one from real
`GroundedCandidate.evidence` fields alone -- never `AICandidateProposal.
confidence` (a completely different, non-factual "the LLM thinks this
fits" signal; Task 16, regression-tested by
`test_ai_proposal_confidence_never_leaks_into_quality_score`, which
proves two proposals with confidence `0.01` and `0.99` produce identical
score `confidence`). An ungrounded proposal, a provider failure/not-
found/not-connected result, and a search-limit `not_searched` proposal
are all structurally excluded (they never appear in
`grounded_candidates` at all) -- each has its own passing test.

**Storage**: `CandidateQualityReport` gained one new field,
`ai_directed_scores: list[CandidateQualityScore]` (same model as every
other list on the report, no second score shape) -- kept separate from
`attraction_scores`/`restaurant_scores`/`accommodation_poi_scores` purely
so "scored from the broad pool" stays distinguishable from "scored from a
Section 192 targeted lookup," mirroring `ai_provider_discovery_result`'s
own separation from `candidate_grounding_batch`. `apply_discovery_to_state`
merges newly computed scores onto `planning_state.candidate_quality_report`
(creating a minimal one only if none exists yet, e.g. a direct `dry_run`
call outside the full pipeline) -- a no-op when nothing was scored.

**Promotion integration is one line, not an override** (Task 7):
`AICandidatePromotionEligibilityService.find_quality_score`'s `all_scores`
tuple now also includes `candidate_quality_report.ai_directed_scores` --
the exact same join-by-`provider_place_id`-then-normalized-name lookup
applies identically to either source. No new eligibility rule, no
relaxed threshold, no AI-specific code path.

**Duplicate identity is deduplicated, not fuzzy-matched** (Task 10):
before scoring a targeted-lookup candidate, its `provider_place_id` is
checked against every `candidate_id` already present in the broad
`attraction_scores`/`restaurant_scores`/`accommodation_poi_scores` (exact
string match only) and against every `provider_place_id` already scored
earlier in the same call -- either match skips scoring entirely, reusing
the existing score rather than creating a conflicting duplicate.

**ExperiencePlanner required zero changes** (Task 9). Its promoted-
candidate merge (`_build_promoted_candidate_pois`, Step 170D) already
reads `ai_candidate_promotion_report.promoted_candidates` completely
generically -- it does not re-check quality tier (eligibility already
enforced Rule 4) and does not care how the candidate was grounded. A real
`/generate` API test now proves the full chain end to end: a
`discovery_query` proposal resolved via a fake targeted lookup is
grounded, scored, promoted, and actually scheduled
(`promoted_from_ai: true` in `experience_plan`), with real coordinates
carried verbatim from the provider fixture.

**One real, pre-existing display-name bug surfaced and fixed along the
way**: `AICandidateReviewItem.name` (Step 191B) fell back to
`proposal.search_query` for a `discovery_query` proposal, which was
harmless while such a proposal could never ground (Section 191B) or get
promoted (pre-192A) -- but once Section 192A made promotion reachable,
this surfaced as a real bug: a promoted candidate's displayed name was
the AI's search phrase ("historic food market") instead of the real,
provider-verified place name ("Discovered Food Hall"). Fixed in
`ai_candidate_review_service.py`: `name` now prefers
`grounded.evidence.matched_name` (real provider fact) whenever a
candidate is grounded, falling back to `candidate_name`/`search_query`
only for an ungrounded proposal. This is a one-line, narrowly scoped
correctness fix directly caused by this step's own new integration test,
not a broader "improve display" change.

**Tests**: 18 new tests, zero changes to any pre-existing assertion or
outcome except the two the fix above required (both updated to assert
the now-correct, better behavior, not weakened) --
`tests/services/test_candidate_quality_service.py` (+8: classification
by provider category, classification fallback to the AI hint, default-
to-attraction, byte-identical output vs. `score_attraction` directly, and
a low-confidence/weak-category input still scoring `rejected` -- proving
no privileged treatment), `tests/services/test_ai_candidate_discovery_service.py`
(+7: targeted-lookup candidate gets scored, broad-pool match never
double-scored, ungrounded/provider-failure/search-limit-skipped never
scored, duplicate provider identity reuses the existing broad score, AI
confidence never leaks into score confidence),
`tests/services/test_ai_candidate_review_service.py` (+3: a targeted-
lookup grounded candidate resolves its score via `ai_directed_scores` and
becomes eligible, stays ineligible with no fabricated default score when
no `ai_directed_scores` entry exists, and stays ineligible when the
`ai_directed_scores` entry itself is low-quality/rejected -- proving Rule
4's threshold is not weakened for this new source). Every pre-existing
`CandidateQualityService`/`AICandidatePromotionEligibilityService`/
`ExperiencePlannerService` test remains green unmodified.

**Real Groq + real OpenStreetMap/Nominatim verification** (2 real runs,
same Lisbon/3-day/food-history-walking shape, default
`AI_DIRECTED_PROVIDER_DISCOVERY_MAX_SEARCHES=5`, no secrets printed).
Run 1: 15 proposals, 5 real targeted calls (bound-limited), 3 provider
matches, 3 grounded, all 3 quality-scored and all 3 eligible ("Belém
Tower" -> "Torre de Belém" `good_candidate` 0.73; "Alfama District" ->
"Made In Portugal" `secondary_candidate` 0.43; "Time Out Market Lisboa"
-> itself `secondary_candidate` 0.44, classified `restaurant`). Run 2: 10
proposals, 5 real targeted calls, 2 provider matches, 2 quality-scored
and both eligible ("Belém Tower" -> "Torre de Belém" again;
"Time Out Market" -> "Time Out Market Lisboa"). Both runs: `grounded > 0`
and `quality_scored > 0`, as expected; every real match happened to clear
the existing, unmodified promotion threshold in these two runs -- this
step's own deterministic unit tests (not the real runs) are what prove a
genuinely low-quality match still stays un-promoted, since real Lisbon
landmarks are simply good candidates by the existing rules. Thresholds
were not touched or tuned to produce this outcome.

**Example provenance trace** (from Run 1, and further confirmed
end-to-end -- including actual scheduling -- by the new deterministic API
test): AI intent `"Belém Tower Lisbon"` -> OpenStreetMap -> real place
`"Torre de Belém"` -> provider id `way/24341353` -> grounded
`targeted_lookup` -> quality score `0.73` -> tier `good_candidate` ->
promotion-eligible -> promoted -> (in the deterministic API test's fake-
provider equivalent) scheduled with `promoted_from_ai: true` and its real
provider coordinates preserved verbatim.

**Strict boundaries honored**: no change to LLM prompts, Groq structured-
output behavior, AI-directed provider lookup behavior, grounding rules
(no fuzzy matching added), promotion thresholds/tiers, or LangGraph node
order; no "AI override" path anywhere; `ExperiencePlannerService` source
untouched; no new dependency; nothing committed.

**Verification**: full suite **3568 passed + 18 skipped** (3550 + 18 new
tests, zero change to any pre-existing test's outcome beyond the two
updated for the display-name fix above). `compileall`/`pytest` clean. No
frontend/shared-contract file changed -- frontend checks explicitly
skipped for that reason.

## 141. Section 193A: Grounded Itinerary-Reasoning Contract for LLM #2

```text
LLM #1 (Sections 191A-191B):  decides what to investigate
Providers (Section 192):      establish factual candidates
CandidateQuality (Section 192A): applies deterministic quality policy
LLM #2 (Section 193A-onward): selects/groups/orders only provider-backed candidate IDs
Routing/Validator (existing): establish feasibility
```

**LLM #2 cannot introduce an itinerary place that does not already exist
in its allowed provider-backed candidate set.** Section 193A defines only
the contract and the request-building layer for this -- no Groq/Anthropic
call, no `ExperiencePlanner` change, no LangGraph order change.

**Existing reasoning infrastructure audited first, found unrelated.**
`app/models/ai_reasoning.py`/`app/services/ai_reasoning_contract_builder.py`
(Step 155A/155B) already exist, but are a *different* LLM responsibility
-- prose EXPLANATION of already-computed plan sections
(`trip_strategy_explanation`, `experience_plan_explanation`, etc.), never
candidate selection/grouping/ordering, and confirmed (by grep) imported
nowhere outside their own tests -- orphaned prototype infrastructure that
predates the provider-grounding architecture (no concept of
`GroundedCandidate`/`CandidateQualityScore`/candidate IDs at all). Not
reused, not modified, not removed -- a genuinely different contract was
needed, so a new, purpose-built module was added instead of bending that
one to a job it was never designed for.

**New models** (`app/models/ai_itinerary_reasoning.py`, a leaf contract
module like every other AI-facing model file in this repo -- it does not
import `planning_state.py`, which is what will import *from* it, avoiding
a circular import): `ItineraryCandidateReference` (Task 2 -- stable
`candidate_id`, real name/category/provider identity/coordinates/quality
score+tier/origin, nothing else), `AIItineraryReasoningRequest` (Task 4),
`ItineraryReasoningDayPlan`/`ItineraryReasoningCandidatePlacement`/
`ItineraryReasoningStrategy` (Task 5-6 -- coarse
morning/midday/afternoon/evening time windows only, never a clock time or
transfer duration; the forbidden-text pattern list rejects "09:"/"12:"
style text in `rationale`), `AIItineraryReasoningResult`/
`AIItineraryReasoningBatch` (status-consistency + Task 7's candidate-ID
safety validator).

**Stable candidate identity** (Task 3): `candidate_id =
f"{provider_name}:{provider_place_id}"` -- deterministic, never an array
index, identical every time the same real place is referenced.

**Candidate-ID safety validator** (Task 7):
`validate_result_against_request(request, result)` is a pure function
(independently testable, not only reachable through model construction)
checking every `candidate_id` referenced in `result.days` against
`request.allowed_candidate_ids()`, that no candidate appears on more than
one day (Task 8's default policy), that no `day_index` exceeds
`trip_duration_days`, and that no two days share a `day_index`.
`AIItineraryReasoningBatch`'s own model validator calls it automatically
for a `completed` result, raising `ValidationError` on any violation --
an unknown/duplicate/out-of-range candidate reference can never silently
become an itinerary place.

**Allowed candidate universe** (Task 1, built by the new
`AIItineraryReasoningRequestBuilder`,
`app/services/ai_itinerary_reasoning_request_builder.py` -- pure,
read-only, calls no provider/LLM): exactly two already-verified sources,
attraction and restaurant domains only (accommodation POIs are a stay-
area concept, never a schedulable "day candidate," matching how
`ExperiencePlannerService` itself treats them):

1. the broad `destination_context` pool, filtered to the same accepted
   quality tiers (`primary_anchor`/`good_candidate`/`secondary_candidate`)
   `ExperiencePlannerService`/`AICandidatePromotionEligibilityService`
   already use for scheduling eligibility.
2. `ai_candidate_promotion_report.promoted_candidates` (Sections
   192/192A) -- each candidate's real `CandidateQualityScore` is looked
   up via the exact same `find_quality_score` join
   `AICandidatePromotionEligibilityService` already uses, never
   re-derived or guessed.

A discovery_query with no provider match, a rejected/ungrounded
proposal, a `not_searched`/provider-failure attempt, and an ambiguous
grounding result are all structurally absent from both sources -- never
filtered out after the fact, never present to begin with. A duplicate
identity (same `provider_name`+`provider_place_id`) between the two
sources keeps the broad-pool entry and skips the AI-directed duplicate
(Task 10).

**Bounded, deterministic selection** (Task 9):
`Settings.ai_itinerary_reasoning_max_candidates`
(`AI_ITINERARY_REASONING_MAX_CANDIDATES`, default `40`) caps
`allowed_candidates`; selection sorts by quality tier first, then
`total_score` descending, then `candidate_id` (never AI proposal
confidence, never arbitrary truncation order) and keeps the top N.

**Factual context stays coarse and honest** (Task 11):
`FactualContextSummary` carries only per-source status strings and small
counts (`weather_status`, `holiday_count_in_range`, `accommodation_offer_count`,
etc.) -- never the full nested `WeatherContext`/`HolidayContext`/etc.
object, and never a placeholder offer/value for a genuinely absent
source; an absent accommodation/flight inventory report is represented as
`None`/`0`, exactly as it is, not invented.

**Reasoning instructions are centralized** (Task 12):
`DEFAULT_ITINERARY_REASONING_INSTRUCTIONS`, a fixed tuple of deterministic
instruction strings restating (never relaxing) the structural rules above
-- the single source Section 193B's future provider adapter will reuse
rather than writing its own prompt copy.

**PlanningState storage** (Task 14): `ai_itinerary_reasoning_result:
AIItineraryReasoningResult | None = None` -- stays `None` for every trip
today; nothing populates it yet. A `PlanningState` payload persisted
before this field existed still deserializes unchanged (tested directly).

**Current `/generate` behavior is completely unchanged**: nothing in
`PlanningOrchestrator`, the LangGraph nodes, `ExperiencePlannerService`,
or the API routes imports `ai_itinerary_reasoning_request_builder`
(asserted by dedicated import-absence tests, mirroring this repo's
existing convention for every other not-yet-wired AI module).

**Future 193C integration seam** (Task 16, inspected, not implemented):
`ExperiencePlannerService.run()` currently does its *own* deterministic
day-assignment (priority tiers, then geographic nearest-neighbor
grouping, then a pace-based per-day cap) entirely from
`destination_context.candidate_pois` -- it has no existing "accept an
external day plan" input at all. Section 193C will need to: (1) build a
`candidate_id -> real place dict` reverse index (the same
`provider_name:provider_place_id` scheme this contract already uses,
symmetric with how `AIItineraryReasoningRequestBuilder` built the forward
direction) so an accepted `AIItineraryReasoningResult.days` can be
translated into the same dict shape `_build_promoted_candidate_pois`
already produces for a promoted candidate; (2) add a branch in
`ExperiencePlannerService.run()` (or a new stage ahead of it) that, when
`planning_state.ai_itinerary_reasoning_result.status == completed`, uses
that translated day/candidate grouping instead of (or as a constraint
on) the current geographic algorithm; (3) confirm
`RouteAwareSequencingService`/`RouteFeasibilityService`/
`TravelTimeBufferService`/`PlanValidatorService` -- all of which already
operate deterministically over whatever schedule `ExperiencePlannerService`
produced -- need no changes, since they don't care who chose the
day/candidate grouping. None of this is implemented in 193A.

**Tests**: 66 new tests, zero changes to any pre-existing test --
`tests/models/test_ai_itinerary_reasoning_models.py` (48: candidate-
reference/request/day-plan/result construction and validation, the
candidate-ID safety validator's four violation classes plus its accept
path, batch construction success/failure, no-forbidden-field-names,
backward-compatible `PlanningState` round-trip, no-circular-import
hygiene), `tests/core/test_ai_itinerary_reasoning_config.py` (4),
`tests/services/test_ai_itinerary_reasoning_request_builder.py` (18:
backward-compatible minimal state, broad-pool accepted/excluded tiers,
restaurant-category mapping, accommodation POIs never included as day
candidates, a real Section 192/192A promoted candidate included via the
real `AICandidatePromotionService.apply_promotion` integration -- not
hand-constructed, an ungrounded/never-promoted proposal excluded, provider
identity preserved, AI proposal confidence provably not affecting
`quality_score`, duplicate-identity dedup preferring the broad pool,
bounded/deterministic tier-then-score selection, honest factual-context
absence, and five import-absence tests proving nothing in the runtime
pipeline is wired to this yet).

**Verification**: full suite **3634 passed + 18 skipped** (3568 + 66 new
tests, zero change to any pre-existing test's outcome). `compileall`/
`pytest` clean. No frontend/shared-contract file changed -- frontend
checks explicitly skipped for that reason.

## 142. Section 193B: Real Groq/Anthropic Provider for Grounded Itinerary Reasoning

```text
AIItineraryReasoningProvider (app/providers/ai_itinerary_reasoning/)
├── not_connected  (default; no network call)
├── groq           (Structured Outputs, json_schema/strict=True -- the Section 191A.1 lesson)
└── anthropic      (forced tool use, mirroring AnthropicAICandidateProposalProvider)
```

**193B does NOT alter the generated itinerary.** It only produces a
validated structured reasoning proposal. Section 193C will integrate
that proposal into itinerary generation. Nothing in `PlanningOrchestrator`,
any LangGraph node, `ExperiencePlannerService`, or the API routes imports
the new provider package or `AIItineraryReasoningService` -- proven by
dedicated import-absence tests and a real `/generate` behavioral
regression test showing `ai_itinerary_reasoning_result` stays `None`
even with `AI_ITINERARY_REASONING_ENABLED=true` set.

**Provider package** (`app/providers/ai_itinerary_reasoning/` --
`base.py`/`factory.py`/`groq_adapter.py`/`anthropic_adapter.py`/
`not_connected_adapter.py`, mirroring
`app.providers.ai_candidate_proposal`'s exact shape, audited first per
Task 1; deliberately *not* modeled on `app.providers.itinerary_narrator`,
whose Groq adapter still uses the pre-191A.1 `function_calling` method --
a stale pattern, not a convention to copy forward). `AIItineraryReasoningProvider.reason(request) -> AIItineraryReasoningResult`
is the only abstraction callers depend on; no LangChain/Groq/Anthropic
SDK type is ever exposed outside an adapter's own module (both SDK
imports stay deferred into `_build_client`, exactly like every other
Groq/Anthropic adapter in this repo).

**Configuration** (`AI_ITINERARY_REASONING_ENABLED` default `false`,
`AI_ITINERARY_REASONING_PROVIDER` default `"not_connected"`,
`AI_ITINERARY_REASONING_MODEL` default unset): no new API key setting --
both adapters reuse the existing `GROQ_API_KEY`/`ANTHROPIC_API_KEY`, and
each adapter's model resolves `explicit param -> AI_ITINERARY_REASONING_MODEL
-> GROQ_MODEL/ANTHROPIC_MODEL`, the exact three-level fallback
`GroqItineraryNarratorProvider`/`AnthropicItineraryNarratorProvider`
already use. Added to `.env.example` in the same "AI candidate proposal"
section as the closely related settings.

**Groq Structured Outputs** (Task 5-6): `method="json_schema"`,
`strict=True`, no `tools`/`tool_choice` -- verified by dedicated tests
capturing the real outgoing kwargs, mirroring
`GroqAICandidateProposalProvider`'s own Section 191A.1 tests exactly.
`_GroqItineraryReasoningSchema` is a wire-schema-only mirror of the 193A
domain contract with the same unsupported-constraint workarounds already
established (`min_length`/`ge`/`le` dropped from the wire schema;
`ItineraryReasoningDayPlan`/`AIItineraryReasoningResult` still enforce
them one layer down, on the parsed output) -- the domain model itself was
not changed to satisfy Groq. `max_tokens=6000` (headroom for a
multi-day response, following the same truncation lesson that raised the
AI-candidate-proposal adapter's own limit in 191A.1).

**Candidate-ID safety is enforced twice, never assumed away by
structured output** (Task 8-9): after building a domain-valid
`AIItineraryReasoningResult` from the parsed response, both adapters call
Section 193A's own `validate_result_against_request(request, result)`
before ever returning `completed` -- a hallucinated, duplicated, or
out-of-range candidate reference downgrades the result to `rejected`
(with the specific violation(s) in `blocked_reasons`), never silently
dropped while still reporting success, and never fuzzy-matched or
searched for.

**Anthropic adapter** (Task 10): same request/result models, same
`validate_result_against_request` safety check, same forced-tool-use
pattern as `AnthropicAICandidateProposalProvider` -- no substantially
different architecture was needed.

**Service layer** (`AIItineraryReasoningService`,
`app/services/ai_itinerary_reasoning_service.py`, Task 13): composes
`AIItineraryReasoningRequestBuilder` (193A) -> the configured provider ->
the validated result. `reason` is pure; `apply` is the only mutating
method and writes exactly `planning_state.ai_itinerary_reasoning_result`
-- `experience_plan`/`route_feasibility_report`/
`route_aware_sequencing_report`/`travel_time_buffer_report`/
`validation_report` are all untouched (tested directly, before/after
equality). `Settings.ai_itinerary_reasoning_enabled` gates the service
itself (mirroring `ItineraryNarrativeService`/`itinerary_narrator_enabled`
exactly) -- when disabled, no provider is even resolved, let alone
called. `apply_itinerary_reasoning_safely` (a fail-safe wrapper mirroring
`apply_discovery_to_state`) exists for Section 193C to reuse; nothing
calls it in this step.

**Real Groq verification (2 real runs, same local `.env` `GROQ_API_KEY`,
never printed, `GROQ_MODEL=openai/gpt-oss-20b`, Lisbon/3-day/
food-history-walking, 8 real provider-backed candidates -- 6 attraction,
2 restaurant -- built through the real `AIItineraryReasoningRequestBuilder`).**
Both runs: `completed`, 3 days, all 8 candidates scheduled exactly once
each, zero unknown candidate IDs, zero cross-day duplicates, zero
semantic-validation violations, only coarse time windows
(morning/midday/afternoon/evening), concise rationale with no
price/rating/hours/exact-route-time claims. Run 1 grouped Belém-area
monuments on day 1, Alfama/Oceanário/Time Out Market on day 2, the museum
and Mercado da Ribeira on day 3. Run 2 produced a different, equally
plausible grouping (same candidate set, different day assignment) --
expected non-determinism from a real LLM call, not a bug; both were
independently valid under the same deterministic safety check.

**Strict boundaries honored**: no LangGraph order change, no
`ExperiencePlanner`/route/validator change, no LLM #1/grounding/
CandidateQuality change, no repair loop, no new dependency; nothing
committed.

**Tests**: 78 new tests, zero changes to any pre-existing test --
config (8), factory (8, including "unknown provider never silently maps
to Groq"), Groq adapter (27: structured-output request shape, valid
response parsing, unknown/duplicate/invalid-day rejection, forbidden-
factual-claim rejection, HTTP-failure/malformed-JSON/missing-key
handling, API-key-secrecy, model-fallback priority), Anthropic adapter
(15, mirroring the Groq domain-safety tests with deterministic mocks),
service (11: disabled-by-default, full composition, `apply` touches only
its one field, fail-safe wrapper behavior, factory injection), and a
dedicated no-wiring suite (9: import-absence across every generation
entry point plus two real `/generate` behavioral regression tests).

**Verification**: full suite **3712 passed + 18 skipped** (3634 + 78 new
tests, zero change to any pre-existing test's outcome). `compileall`/
`pytest` clean. No frontend/shared-contract file changed -- frontend
checks explicitly skipped for that reason.

## 143. Section 193C: Wiring Grounded LLM #2 Reasoning into LangGraph Generation

```text
... flight_inventory -> ai_itinerary_reasoning -> experience_planning -> route_feasibility ...
```

**LangGraph order.** `build_ai_itinerary_reasoning_node` (Section 193B's
`AIItineraryReasoningService`, unchanged) is inserted immediately before
`experience_planning`, after `flight_inventory` -- the exact position the
193C spec required, so `ExperiencePlannerService.run` can read an
already-computed `planning_state.ai_itinerary_reasoning_result`. The node
always runs (same as `ItineraryNarrativeService`'s node): with
`AI_ITINERARY_REASONING_ENABLED=false` (the default) it stores an honest
`not_connected` result without resolving a provider or making a network
call; a real Groq/Anthropic call only happens when the flag is explicitly
enabled.

**ExperiencePlannerService integration is a single seam.** The only
change to `run()` is at the `day_groups = _group_candidates_into_days(...)`
call site: a new `_resolve_ai_guided_day_groups` helper is tried first,
and its result is used only when it resolves to a fully safe,
fully-referenced `(day_groups, restaurant_ids_by_day)` pair; otherwise
`run()` falls through to the pre-193C deterministic geographic grouping
completely unchanged. Every other line of `run()` -- `ExperienceItem`/
`DailyPlan`/`ExperiencePlan` construction, accommodation suggestions,
`stay_area_guidance`, `decision_summary`, `implementation_gaps`,
`readiness_checklist` -- is untouched.

**Candidate resolution never trusts a candidate_id blindly.** A
module-level `build_candidate_id(provider_name, provider_place_id)` in
`app.models.ai_itinerary_reasoning` is the single formula both Section
193A's request builder (forward direction: building the allowed-candidate
universe LLM #2 sees) and this step's `ExperiencePlannerService` reverse
index (backward direction: resolving a chosen candidate_id back to its
real provider record) use -- eliminating any chance of the two sides
silently drifting out of sync. `_resolve_ai_guided_day_groups` re-validates
independently of Section 193B's own `validate_result_against_request`
(defense-in-depth against a future caller/bug/test that populates
`ai_itinerary_reasoning_result` some other way): every `candidate_id`
must resolve to a real entry in the reverse index built from that
generation's own real `scheduling_candidate_pois`/tier-filtered
restaurants, `day_index` must be in `1..num_days` with no day repeated,
and no `candidate_id` may appear on more than one day. Any violation, or
a result that isn't `completed`, or a `completed` result that ends up
scheduling zero attractions trip-wide, falls back to the deterministic
path entirely -- never a partially-trusted AI result, never a fabricated
placeholder stop.

**Restaurant handling**: LLM #2's `allowed_candidates` include both
attraction and restaurant categories. When a day's resolved candidates
include one or more restaurant-category entries, those become that
day's `restaurant_suggestions` (via the existing `_build_restaurant_
suggestion`), overriding the geographic `_suggest_nearby_restaurants`
fallback for that day only; a day the AI didn't select any restaurant
for keeps the unchanged geographic suggestion. Accommodation is never
touched by AI reasoning (193A never included it in `allowed_candidates`).

**Coarse time semantics, deliberately not mapped further.**
`ExperienceItem.start_time`/`end_time`/`estimated_duration_minutes` are
not set anywhere in this codebase today, deterministic or AI-guided --
193C does not invent a coarse-time-to-clock-time convention that doesn't
already exist elsewhere. `approximate_structure`'s time windows
(morning/midday/afternoon/evening) stay on
`planning_state.ai_itinerary_reasoning_result` for inspection; only
`candidate_ids` order (already captured by the existing `stop_order`
loop, unchanged) drives within-day ordering. An AI-guided day keeps that
order verbatim -- `_order_day_by_distance` is skipped for it, since
re-sorting by geography would silently discard the one thing LLM #2 was
asked to decide.

**Factual safety preserved.** Every `ExperienceItem` field (`name`,
`coordinates`, `provider_place_id`, `provider_source`,
`original_ai_candidate_id`, `data_quality`) still comes from the same
real provider/promoted-candidate record the deterministic path already
used -- LLM #2's output is only ever a list of `candidate_id`s, `day_index`,
and a `rationale` string; it can select, group, and order, and it can
explain, but it cannot originate a fact. Verified directly against real
Groq output (see below): zero forbidden factual field names
(`price`/`rating`/`opening_hours`/`route_time`/`booking_url`/
`review_count`/`safety_score`/`availability`) anywhere in a real
generated `planning_state`.

**Routing/validation remain authoritative afterward.**
`RouteFeasibilityService`/`RouteAwareSequencingService`/
`TravelTimeBufferService`/`PlanValidatorService` are completely unaware
an AI-guided plan differs from a deterministic one -- they run
unconditionally, on whatever `experience_plan`
`ExperiencePlannerService` produced, exactly as before. Proven both by a
dedicated LangGraph-level test (real, unfaked route/sequencing/buffer/
validator services genuinely executing against an AI-guided plan) and by
the real end-to-end verification below (`route_feasibility`/
`route_aware_sequencing` both `success`, `validation_report` 0 issues).

**Observability**: `ExperiencePlannerService.run` logs one structured
`stage=ai_itinerary_reasoning` line per generation with `enabled`,
`provider`, `status`, `candidate_count`, `selected_candidate_count`,
`day_count`, and `fallback_used` -- in addition to Section 193B's own
service-level log (`stage`/`provider`/`status`/`duration_ms`/`day_count`)
from the earlier `reason()` call.

**Real Groq verification (2 real Lisbon/3-day/history-food-walking
generations via actual `POST /trips/{id}/generate`, same local `.env`
`GROQ_API_KEY` never printed, `GROQ_MODEL=openai/gpt-oss-20b`,
`AI_ITINERARY_REASONING_ENABLED=true`/`AI_ITINERARY_REASONING_PROVIDER=groq`
set only as process-level env overrides for the verification server --
`.env` itself never modified).** Both runs: LLM #1 `completed`, grounding
`partial` (5-6 grounded), promotion `promoted` (5 promoted candidates),
LLM #2 `completed` via `groq_ai_itinerary_reasoning_provider`, 3 days,
all candidate_ids resolved with zero unknown/duplicate/invalid-day
violations, `route_feasibility`/`route_aware_sequencing` both `success`,
`validation_report` 0 issues. Run 1 grouped Pelourinho/Miradouro/Pastéis
de Belém on day 1, Comboio do Zoo/Torre de São Lourenço on day 2, Alfama/
Mosteiro dos Jerónimos (both AI-directed-promoted) on day 3. Run 2
produced a different, equally valid grouping from a different LLM #1
proposal batch (6 grounded this run) -- expected non-determinism from a
real LLM call, not a bug; every group still passed the same deterministic
safety check.

**AI-enabled vs. deterministic comparison (Task 29)**: a third real
generation for the identical trip fixture with
`AI_ITINERARY_REASONING_ENABLED` left at its default (`false`) --
`ai_itinerary_reasoning_result.status` was `not_connected`, and
`ExperiencePlannerService` fell back to the pre-193C deterministic
geographic grouping. Same 5 promoted candidates entered the universe in
both modes (factual candidate identity unaffected by which path grouped
them), but selection/grouping/ordering differed: the deterministic run
anchored each day on its highest-priority unscheduled candidate and
filled by geographic proximity (e.g. day 1: Time Out Market Lisboa,
Pelourinho de Lisboa, Miradouro de São Pedro de Alcântara), while the
AI-guided runs grouped and explained days thematically (e.g. Run 1 day 1:
a historic-monument-then-pastry-then-viewpoint narrative). This
demonstrates LLM #2 actually changes selection/grouping/ordering without
ever altering a candidate's factual identity.

**Example end-to-end provenance (from Run 1, real values)**: user
interest `"history"` → LLM #1 (`ai_candidate_proposal_batch`) proposed a
discovery-query candidate → a real OpenStreetMap/Nominatim lookup
grounded it to `way/24341353` / "Alfama" → `CandidateQualityService`
scored it into an accepted tier → `AICandidatePromotionService` promoted
it (`ai_candidate_promotion_report.promoted_candidates`, `promoted=true`)
→ LLM #2 (`AIItineraryReasoningService`) selected
`candidate_id="openstreetmap_places:node/207597971"` for
`day_index=3` alongside `"openstreetmap_places:relation/3258300"` (Mosteiro
dos Jerónimos) → `ExperiencePlannerService` resolved both back through
the reverse index to their real provider records and scheduled them as
day 3's `ExperienceItem`s, each still carrying its real
`provider_place_id`/`provider_source`/`original_ai_candidate_id` →
`RouteFeasibilityService`/`RouteAwareSequencingService`/
`PlanValidatorService` ran unconditionally afterward and reported
`success`/`success`/0 issues.

**Failure-path verification**: node-level tests prove the disabled-by-
default path never calls the injected provider and still records an
honest `not_connected` result; `ExperiencePlannerService`-level tests
prove an unknown candidate_id, a duplicate candidate_id across days, an
out-of-range `day_index`, a `rejected`/`not_connected` reasoning result,
and a zero-attraction-trip-wide result all fall back to the deterministic
path rather than ever producing a partial/fabricated plan; a fail-safe
wrapper (`apply_itinerary_reasoning_safely`, from 193B) still swallows
any unexpected service exception, leaving `planning_state` unaffected for
that call.

**No-fabrication audit**: a real generated `planning_state` (Run 1) was
scanned for `price`/`rating`/`opening_hours`/`route_time`/`booking_url`/
`review_count`/`safety_score`/`availability` field names anywhere in the
full nested structure -- zero matches. LLM #2's own `rationale` text is
free prose describing sequencing/theme only (e.g. "Morning visit to
Pelourinho de Lisboa for historic ambience..."), never a price, rating,
opening hour, or exact route-time claim.

**Current narrator behavior**: unchanged -- `ItineraryNarrativeService`
was not touched by this step.

**Strict boundaries honored**: no repair loop, no LLM #2 place invention,
no routing/validator bypass, no change to CandidateQuality thresholds/
LLM #1/grounding/accommodation/flight/narrator/regeneration, no new
dependency, no secret exposed, nothing committed.

**Tests**: 19 new tests added, zero changes to any pre-existing test's
*expected outcome* (a small number of existing graph-order assertions in
193A/193B-era test files were updated to include the new
`ai_itinerary_reasoning` node/reflect the now-intentional live wiring --
documented as part of this step, not a regression). New coverage:
`test_experience_planner_ai_guided.py` (15: successful AI grouping/order,
factual-fields-from-provider, AI-directed-promoted-candidate end-to-end,
unknown/duplicate/invalid-day fallback, provider-failure fallback,
absent-result baseline, subset selection, pace-cap truncation,
transparent assumptions/rationale, no-forbidden-fields), 3 new node-level
tests (disabled-by-default never calls provider, enabled calls provider
and stores a real result, node-level failure is safe), 1 new full-graph
test (routing/sequencing/buffer/validation all genuinely execute, with
their own real services, for an AI-guided plan).

**Verification**: full suite **3731 passed + 18 skipped** (3712 + 19 new
tests, zero change to any pre-existing test's outcome). `compileall`/
`pytest` clean. No frontend/shared-contract file changed -- frontend
checks explicitly skipped for that reason.

## 144. Section 194A: Validator-Driven AI Itinerary Repair Contract and Service

```text
ExperiencePlan
  -> routing (RouteFeasibilityService/RouteAwareSequencingService/TravelTimeBufferService)
  -> validator (PlanValidatorService)
  -> repair request (AIItineraryRepairRequestBuilder -- ONLY when a real,
     structurally-sourced repairable issue exists)
  -> LLM repair (AIItineraryReasoningProvider.repair, same provider as `reason`)
  -> validated reasoning revision (validate_repair_result_against_request,
     then merge_repair_into_reasoning_result)
```

**194A does not yet run this automatically during `/generate`.** No
LangGraph node, `PlanningOrchestrator` stage, or API route constructs or
calls `AIItineraryRepairService`/`AIItineraryRepairRequestBuilder` --
proven by a dedicated import-absence test suite and a real `/generate`
regression test showing `ai_itinerary_repair_result` stays `None` even
with `AI_ITINERARY_REASONING_ENABLED=true` set. **Section 194B will
introduce the bounded execution loop** that actually calls this after
validation, decides whether to merge an accepted repair back into
`ai_itinerary_reasoning_result`, and (if so) reruns whatever downstream
steps that merge requires.

**Validator audit (Task 1/2)**: `ValidationIssue` has no structured
`code`/`affected_day`/`affected_candidate_id` field -- only `severity`,
a free-string `category`, `message`, and an optional free-string
`affected_section`. Of every `category` this app's `PlanValidatorService`
actually produces (`accommodation_inventory`/`budget`/`constraints`/
`feasibility`/`flight_inventory`/`geographic_spread`/`holidays`/
`hotel_ratings`/`movement_data`/`must_visit`/`provider_coverage`/
`provider_coverage_consistency`/`regeneration`/
`regeneration_state_consistency`/`route_aware_sequencing`/
`route_geometry`/`scheduling`/`weather`), only **`geographic_spread`**
names a specific day (`affected_section=f"experience_plan.daily_plans[{day_number}]"`)
and only ever warns, never blocks. Every route/sequencing/buffer/geometry
category above it is either a plan-wide aggregate status report (e.g.
"3 of 5 legs feasible") with no single actionable target, or -- for
`route_aware_sequencing`/`movement_data`/`route_geometry` specifically --
a pure data-availability restatement, never a "this is wrong" finding.
`scheduling`/`provider_coverage` are `CRITICAL` but describe an empty
plan with nothing scheduled at all, which a day-editing repair has no
mechanism to address. The remaining categories are genuinely external/
factual data repair must never be asked to "fix" (Task 2: "do not ask
the LLM to repair missing provider truth").

Two of this app's *other* real reports, inspected directly rather than
through `ValidationReport`, do carry leg-level identity: a
`RouteLegFeasibility` on `route_feasibility_report.legs` with
`feasibility_status == RouteFeasibilityStatus.NEEDS_REVIEW`, and a
`TravelTimeBuffer` on `travel_time_buffer_report.buffers` with
`buffer_status == BufferSufficiencyStatus.INSUFFICIENT` -- both carry
real `from_experience_id`/`to_experience_id` values, resolved to a day
via that experience's own `DailyPlan.day_number` (never the
denormalized, restatement-only `ExperienceItem.day_number`). Together
with `geographic_spread`, these three are the **only** members of this
app's own `RepairableIssueType` vocabulary
(`app.models.ai_itinerary_repair`) -- a deliberately narrow, real-field-
backed list, not a 1:1 mirror of `ValidationIssue.category`. Several
examples the 194A task spec itself named as "possibly repairable"
(day-count/pace overload, duplicate candidate, pacing violation) have no
corresponding validator/report finding anywhere in this codebase --
`ExperiencePlannerService` already prevents them at scheduling time, so
there is nothing to classify; they are deliberately not invented
(Task 2: "do not invent validator categories that do not exist").

**Repair request (Task 3/4/13)**: `AIItineraryRepairRequest`
(`app.models.ai_itinerary_repair`) mirrors `AIItineraryReasoningRequest`'s
trip-context/traveler-context/`allowed_candidates` shape exactly, plus
`original_days` (the full current reasoning result), `affected_days`
(derived from the classified issues' own day_index values),
`issues: list[AIItineraryRepairIssue]`, and `attempt_number` (supported,
never auto-incremented in this step). `AIItineraryRepairRequestBuilder`
composes the existing `AIItineraryReasoningRequestBuilder` (Section 193A)
to reconstruct the **exact same candidate universe** the original
reasoning call used -- safe because that builder performs zero provider
I/O of its own and 194A never mutates the `PlanningState` fields it
reads between the original reasoning call and a repair call. Returns
`None` (never calls the LLM) whenever there is no `completed` reasoning
result to repair, no repairable issue is classified, or the freshly-
rebuilt candidate universe is empty or inconsistent with the original
result.

**Repair result (Task 5)**: `AIItineraryRepairResult.repaired_days` holds
**only the day(s) actually changed** -- the smallest design compatible
with the existing reasoning architecture, never a full restated day
list. `merge_repair_into_reasoning_result` is the one deterministic place
that combines this with the original `AIItineraryReasoningResult`.

**Provider architecture (Task 9-11)**: `repair` was added directly to
the existing `AIItineraryReasoningProvider` ABC alongside `reason` --
one provider hierarchy, not two -- so `NotConnectedAIItineraryReasoningProvider`/
`GroqAIItineraryReasoningProvider`/`AnthropicAIItineraryReasoningProvider`
all reuse their existing client/API-key/model resolution exactly as-is.
Groq: `method="json_schema", strict=True`, no `tools`/`tool_choice` (same
191A.1 pattern), a narrower wire schema (`repaired_days`/`repair_summary`/
`addressed_issue_types`/`confidence` only). Anthropic: same forced
tool-use pattern, a narrower tool schema. Both call
`validate_repair_result_against_request` before ever reporting
`completed`, exactly mirroring how both `reason` implementations already
call `validate_result_against_request`.

**Configuration (Task 12)**: no new setting. Repair reuses
`AI_ITINERARY_REASONING_ENABLED`/`AI_ITINERARY_REASONING_PROVIDER`/
`AI_ITINERARY_REASONING_MODEL` exactly -- repairing a reasoning result
makes no sense when reasoning itself is disabled, and Section 194B is
what will need its own bounded-loop-control setting (attempt limits,
automatic wiring), not this step.

**Merge semantics (Task 16)**: `merge_repair_into_reasoning_result`
replaces each `affected_days` entry with its repaired version, carries
every other original day over unchanged, and always preserves
`strategy`/`overall_tradeoffs` from the original result (194A's repair
result has no strategy field of its own to adjust it with). Never
mutates the original `AIItineraryReasoningResult`. Defense-in-depth:
re-runs `validate_repair_result_against_request` before merging, then
re-runs the standard `validate_result_against_request` against the full
merged result (via `AIItineraryRepairRequest.to_reasoning_request()`,
which reconstructs an equivalent `AIItineraryReasoningRequest` from the
same fields the repair request already carries) -- "full result
validates under the standard reasoning contract."

**Semantic safety (Task 15)**: `validate_repair_result_against_request`
rejects an unknown candidate_id, a day_index outside `affected_days`
(Task 23 -- an unaffected day can never be "repaired"), a duplicate
day_index or candidate_id within the repair output itself, and a
candidate_id that collides with one already used by an *unaffected*
original day (would create a cross-day duplicate the instant the merge
combines both). All four are enforced identically whether the violation
comes from a real LLM or a directly-constructed, hand-crafted result
(the tests build both).

**Real Groq verification (2 real repair calls, same local `.env`
`GROQ_API_KEY` never printed, `GROQ_MODEL=openai/gpt-oss-20b`, a
realistic 6-candidate 2-day Lisbon `AIItineraryRepairRequest` built the
same shape `AIItineraryRepairRequestBuilder` itself would produce, with
one real `geographic_spread`-derived `AIItineraryRepairIssue` on day 2).**
Both runs: `completed`, `affected_days=[2]` honored exactly (day 1 never
touched by either run), zero unknown candidate IDs, zero semantic
violations, zero forbidden factual field names anywhere in the result.
Run 1 dropped one candidate from day 2 ("Removed Padrao dos
Descobrimentos from day 2 to reduce geographic spread"); Run 2 instead
kept all three and reordered them ("Reordered the experiences on day 2
to group geographically close sites together") -- expected non-
determinism from a real LLM call, not a bug; both independently passed
the same deterministic safety check, and both merged cleanly into the
original `AIItineraryReasoningResult` via `merge_repair_into_reasoning_result`
with day 1 preserved byte-for-byte and the original `strategy` intact.

**Strict boundaries honored**: no LangGraph wiring, no automatic retry,
no `ExperiencePlan` mutation, no automatic routing/validator rerun, no
new provider discovery, no new candidate invention, no change to
CandidateQuality/LLM #1/narrator/feedback/regeneration, no new
dependency, no secret exposed, nothing committed.

**Tests**: 78 new tests, zero changes to any pre-existing test's outcome
-- models (21: request/result structural safety, Tasks 21-25 exactly),
request builder (13: Task 1/2's classification for all three repairable
categories plus explicit non-repairable-category exclusion, Task
19/20's affected-day targeting, Task 13/18's "return None" cases), service
(8: Task 18's disabled path, Task 26's no-repairable-issues path, Task 27's
provider-failure safety, `apply`'s single-field mutation), Groq adapter
(17) and Anthropic adapter (8) repair-specific safety tests (structured-
output/tool-use shape, hallucinated-candidate/unaffected-day/forbidden-
claim rejection, API-key secrecy), and a dedicated no-wiring suite (11:
import-absence across every generation entry point plus three real
`/generate` behavioral regression tests, including one proving the
validator still runs exactly once).

**Verification**: full suite **3809 passed + 18 skipped** (3731 + 78 new
tests, zero change to any pre-existing test's outcome). `compileall`/
`pytest` clean. No frontend/shared-contract file changed -- frontend
checks explicitly skipped for that reason.

## 145. Section 194B: Bounded Automatic AI Repair Loop

```text
validation
  -> route_after_validation (conditional)
       |-- no repairable issue / disabled / no completed reasoning / budget exhausted -> provider_coverage
       `-- eligible -> ai_itinerary_repair
                          -> route_after_repair (conditional)
                               |-- completed -> experience_planning -> route_feasibility
                               |                 -> route_aware_sequencing -> travel_time_buffer
                               |                 -> validation (loops back to route_after_validation)
                               `-- rejected/not_connected/skipped -> provider_coverage
```

**Automatic repair is disabled by default** (`AI_ITINERARY_REPAIR_ENABLED=false`), and even when enabled it only ever fires on top of an already-`AI_ITINERARY_REASONING_ENABLED=true` completed LLM #2 result -- a normal deterministic or narrator-only generation is completely untouched by this section. `AI_ITINERARY_REPAIR_MAX_ATTEMPTS` (default `1`, hard-bounded `1..2`) is the loop's only stopping condition beyond "no repairable issue remains"; there is no unlimited-retry path anywhere in this graph.

**Reuses Section 194A entirely, unmodified in shape.** `AIItineraryRepairService`/`AIItineraryRepairRequestBuilder`/`classify_repairable_issues`/`merge_repair_into_reasoning_result`/the `AIItineraryReasoningProvider.repair` method are called exactly as 194A defined them -- the node adds zero new orchestration logic, only the loop-control wiring around them. The one real correction 194B's audit (Task 1) surfaced: `classify_repairable_issues`'s `ROUTE_NEEDS_REVIEW` check now additionally requires `RouteLegFeasibility.status == ProviderStatus.FAILED` (a routing call that was actually attempted and genuinely failed), not `feasibility_status == NEEDS_REVIEW` alone -- that status is *also* produced whenever no routing provider is connected at all (`ROUTING_PROVIDER` defaults to `not_connected` in this app), which is an absence of data, not a detected problem, and would otherwise have triggered repair on nearly every multi-stop day in any real deployment that hasn't configured OSRM.

**Configuration**: `AI_ITINERARY_REPAIR_ENABLED`/`AI_ITINERARY_REPAIR_MAX_ATTEMPTS` are the only new settings -- repair reuses `AI_ITINERARY_REASONING_PROVIDER`/`AI_ITINERARY_REASONING_MODEL` and the existing Groq/Anthropic API keys, since repair is the same LLM #2 job as reasoning, just scoped to a subset of days. Added to `.env.example` in the same itinerary-reasoning section; `.env` itself was never modified.

**`PlanningState.ai_itinerary_repair_attempt_count`** (new field, default `0`, backward compatible) tracks real invocations only. It is incremented by the `ai_itinerary_repair` node exactly when `Settings.ai_itinerary_reasoning_enabled` is `True`, a request could actually be built, and the call wasn't silently swallowed by `apply_repair_safely`'s own exception guard -- mirroring (never reimplementing as a second code path) the exact conditions `AIItineraryRepairService.repair` itself uses to decide whether to reach the provider at all, so the count stays correct even if this node is ever invoked without `route_after_validation`'s own pre-check having already guaranteed them. `ai_itinerary_repair_result` always holds the *latest* attempt's result -- never cleared once a later validation pass happens to come back clean, so a completed generation still shows exactly what happened during repair (Task 14: evidence for observability/evaluation).

**The `ai_itinerary_repair` node** (`build_ai_itinerary_repair_node`) computes `attempt_number`, builds one `AIItineraryRepairRequest` (via the service's own `request_builder` instance) purely so it has the exact object `merge_repair_into_reasoning_result` needs, calls `apply_repair_safely` (the one real, gated, logged call), and -- only on a `completed` result -- merges it into `ai_itinerary_reasoning_result`. A `completed` result whose merge fails defense-in-depth safety validation (Task 22: a hallucinated candidate, an unaffected-day mutation) is honestly downgraded in place to `rejected` before being stored, so a dishonest/buggy provider claim can never send `route_after_repair` back into the planning chain with a stale "completed" label sitting next to an untouched plan. Never rebuilds `ExperiencePlan` itself, never touches `route_feasibility_report`/`route_aware_sequencing_report`/`travel_time_buffer_report`/`validation_report` -- those are exactly the fields the existing downstream nodes already unconditionally overwrite on every call (Task 8's audit conclusion: every one of `RouteFeasibilityService`/`RouteAwareSequencingService`/`TravelTimeBufferService`/`PlanValidatorService`'s node wrappers assigns a fresh report object on every successful run, so a second pass never needs to see a stale value cleared first -- no explicit clearing was added anywhere).

**Two conditional edges, no new stage logic.** `route_after_validation` re-verifies every eligibility condition from scratch (repair enabled, reasoning enabled, a `completed` reasoning result exists, `classify_repairable_issues` finds something, the attempt budget isn't exhausted, a request can actually be built) using the real 194A classifier and request builder, never a reimplementation. `route_after_repair` is a one-line status check: `completed` re-enters at `experience_planning` (the full downstream chain -- route_feasibility, route_aware_sequencing, travel_time_buffer, validation -- always reruns in full, never a partial shortcut); everything else goes straight to `provider_coverage`, so a failed repair is never retried immediately -- only a later, independent pass through `route_after_validation` (itself only reachable after a *successful* repair's own re-validation) can ever trigger another attempt, which is what makes "no retry after failure" a property of the graph's topology rather than separate tracked state.

**Real Groq verification (2 real Lisbon/3-day/history-food-walking generations via actual `POST /trips/{id}/generate`, `AI_CANDIDATE_DISCOVERY_ENABLED=true`/`AI_ITINERARY_REASONING_ENABLED=true`/`AI_ITINERARY_REASONING_PROVIDER=groq`/`AI_ITINERARY_REPAIR_ENABLED=true`/`AI_ITINERARY_REPAIR_MAX_ATTEMPTS=1` set only as process-level env overrides for the verification server -- `.env` itself never modified, `GROQ_API_KEY` never printed).** Run 1 naturally produced a real `geographic_spread` finding on day 2 (5 candidates, one -- Torre de São Lourenço -- geographically far from the rest); the router correctly routed to repair, a real Groq call (`groq_ai_itinerary_reasoning_provider`, `openai/gpt-oss-20b`) returned a `completed` repair dropping exactly that one candidate, the merge preserved days 1 and 3 byte-for-byte, `experience_planning`/`route_feasibility`/`route_aware_sequencing`/`travel_time_buffer`/`validation` all genuinely reran, and the second validation pass showed zero remaining `geographic_spread` issues (route/sequencing both `success`). This one real run satisfies Task 30's "controlled" verification requirement too -- no synthetic validator schema or threshold change was needed; a real trip naturally produced the exact condition being tested. Run 2's LLM #2 call itself came back `rejected` (a real, honest reasoning-provider outcome, unrelated to repair), so `ai_itinerary_reasoning_result.status != completed` and the router correctly never attempted repair at all (`attempt_count=0`) -- the resulting plan fell back to the deterministic path and still carried a real, un-repaired `geographic_spread` finding in its final validation report, exactly as Task 13 requires ("do not repair non-AI itineraries").

**No-fabrication audit**: the full real Run 1 `planning_state` was scanned for `price`/`rating`/`opening_hours`/`route_time`/`route_distance`/`booking_url`/`review_count`/`availability`/`safety_score` field names anywhere in the nested structure -- zero matches. Every `repaired_days` candidate_id was confirmed to be a subset of the (unchanged) allowed candidate universe; three AI-directed promoted candidates (Castelo de São Jorge, Torre de Belém, Mosteiro dos Jerónimos) kept their exact `provider_place_id`/`provider_source`/coordinates across the repair, both in the untouched days (1 and 3) and structurally guaranteed for day 2 by `merge_repair_into_reasoning_result`'s own safety check.

**Strict boundaries honored**: no Section 195 narrator work, no new provider discovery during repair, no LLM #1/CandidateQuality change, no weakened/suppressed validation (a plan that exhausts its repair budget still reports every remaining issue honestly), no invented candidate ID, no accommodation/flight/feedback/regeneration change, no new dependency, no secret exposed, nothing committed (194A remains uncommitted, and 194B is built directly on top of it per this section's explicit instruction).

**Tests**: 25 new tests, zero changes to any pre-existing test's outcome (three 194A-era import-absence assertions in `test_ai_itinerary_repair_service_no_wiring.py` were updated to reflect the now-intentional live wiring, exactly like every prior section's own "no wiring yet" flip) -- a full-graph loop suite (17: valid-plan-needs-no-repair, non-repairable-findings-never-trigger, the critical successful-one-attempt full-rerun test, max-attempts-1/2 exhaustion in both outcomes, an explicit never-a-third-call bound test, provider-rejected/not_connected failure handling, semantic-invalid-repair rejection for both a hallucinated candidate and an unaffected-day mutation, stale-plan/second-pass consumption, unaffected-day preservation, and AI-directed-promoted-candidate provenance survival), 7 new node/router unit tests (disabled-never-calls-provider, node-failure-is-safe, both routers' eligibility branches), and 1 new request-builder regression test locking in the `ProviderStatus.FAILED` classification correction.

**Verification**: full suite **3834 passed + 18 skipped** (3809 + 25 new tests, zero change to any pre-existing test's outcome). `compileall`/`pytest` clean. No frontend/shared-contract file changed -- frontend checks explicitly skipped for that reason.

## 146. Section 195: Final Grounded Itinerary Narrator

```text
verified/provider-backed planning -> LLM #2 selection -> ExperiencePlanner
  -> routing -> validation -> optional bounded repair (Section 194B)
  -> FINAL planning state (result.planning_state from the LangGraph run)
  -> itinerary narrator (Step 182F, refined here)
```

**The narrator already ran on final, post-repair state -- no invocation-point change was needed.** Auditing `PlanningOrchestrator.generate_full_plan_via_langgraph` (Task 1) showed `self.itinerary_narrative_service.generate(new_state)` is called using `new_state = result.planning_state`, where `result` is the LangGraph run's own completed output -- and Section 194B's bounded repair loop is entirely contained *inside* that graph run (the graph only reaches `final_state`/`END` after any repair attempts have already resolved). By the time the narrator's request builder reads `planning_state.experience_plan`/`validation_report`/`ai_itinerary_reasoning_result`, these are already whatever the graph settled on -- there is only ever one current state, never a separate "give me the post-repair snapshot" step to add. This is proven directly (not just asserted) by `test_itinerary_narrative_final_state.py`, which builds a "day 2 already repaired down to one candidate" fixture and confirms the real request builder reflects exactly that. The legacy (non-LangGraph) `generate_full_plan` path has no LLM #2/repair stage at all, so it has nothing extra to reflect.

**Section 194B proposal-count sanity check (Task 0.1)**: the "LLM #1 proposals: 0, grounded: 3, promoted: 3" figure reported during 194B's own real verification was a bug in that turn's throwaway Python extraction script, not a system defect -- it read a nonexistent `proposal['result']['candidates']` key (silently defaulting to `[]`) instead of the real `proposal['result']['proposals']` key. Re-extracting the same two saved real responses with the correct key shows LLM #1 genuinely proposed 15 and 12 candidates in those two runs; only 3 of each were successfully grounded to a real provider place (an expected, honest narrowing -- not every AI-proposed named place has a matching Nominatim/OSM entry), and every grounded, quality-scored candidate was promoted. No production code changed as a result of this finding.

**The narrator's job stayed unchanged: explain, never decide.** No new place, price, rating, opening hour, route duration, or booking claim can reach `ItineraryNarrativeReport` -- the request model still has no such field (unchanged since Step 182F), and this step added the same forbidden-factual-claim-pattern check every other AI-facing contract in this repo already enforces (`_FORBIDDEN_TEXT_PATTERNS`, now including `optimal`/`verified`/`guaranteed`/`travel-ready`/`booking-ready`/`safest`/`the best`) directly onto `ItineraryNarrativeDayOutput.title`/`narrative`/`caveats` and `ItineraryNarrativeReport.summary`/`assumptions`/`warnings` -- previously this model had zero such guard, unlike `ai_candidate_proposal`/`ai_itinerary_reasoning`/`ai_itinerary_repair`.

**LLM #2 reasoning rationale and repair awareness are explanatory context only (Tasks 4-6).** `ItineraryNarrativeDayInput.reasoning_rationale`/`ItineraryNarrativeRequest.reasoning_strategy_summary` carry LLM #2's own already-generated prose, explicitly labeled "context only" in both adapters' prompts -- every specific factual claim in the narrator's own output must still independently pass the same forbidden-pattern/place-identity checks, so rationale prose can never smuggle an unsupported claim through. `was_adjusted_after_feasibility_checks` is a single plain boolean (`ai_itinerary_repair_attempt_count > 0` and the latest repair `status == completed`) -- never a provider name, model name, or attempt number -- and both adapters are instructed to mention it, when true, only in plain traveler-facing language, and never to mention it when false.

**Structural place-identity safety (Task 12), the one real architectural addition.** `ItineraryNarrativeExperienceInput.experience_id` (request side) and `ItineraryNarrativeDayOutput.referenced_experience_ids` (output side) are validated against each other by the new pure function `validate_narrative_against_request` -- every referenced id must belong to *that exact day's* real, current, post-repair scheduled experiences, never a different day's, never an invented one. Both adapters call it before ever reporting `success`, exactly mirroring how `validate_result_against_request`/`validate_repair_result_against_request` already gate `completed` in the reasoning/repair adapters. This is a structural check, not a claim that the free-text narrative mentions only allowed places by name -- deliberately chosen over fuzzy free-text place-name matching (Task 12's own explicit steer), consistent with how this codebase already trusts prompt-level instruction-following for rationale/tradeoff prose elsewhere.

**Real bug found and fixed via Task 29's real verification, invisible to unit tests.** The Groq narrator adapter had never adopted the Section 191A.1 Structured Outputs fix (`method="json_schema", strict=True`) -- Section 195 adopted it now that the output contract was changing anyway (Task 18). Doing so surfaced a genuine schema-construction bug: Groq's strict mode requires every property to appear in the schema's `required` array, but `Field(default_factory=list)` causes `langchain_groq`'s schema conversion to omit that field from `required`, and Groq then rejects the entire request (`400 invalid JSON schema for response_format`) before any completion is attempted -- a failure mode no fake-client unit test could ever observe, since it depends on the real schema Groq itself receives. Fixed by giving every field in `_NarratorDayOutputSchema`/`_NarratorBatchSchema` no Python default at all (always required, with the prompt instructed to use an empty list when there's nothing to report) -- the exact same convention `_GroqItineraryReasoningSchema`/`_GroqRepairSchema` already used correctly from the start.

**Real Groq verification (2 real Lisbon/3-day/history-food-walking generations via actual `POST /trips/{id}/generate`, `AI_CANDIDATE_DISCOVERY_ENABLED=true`/`AI_ITINERARY_REASONING_ENABLED=true`/`AI_ITINERARY_REASONING_PROVIDER=groq`/`AI_ITINERARY_REPAIR_ENABLED=true`/`AI_ITINERARY_REPAIR_MAX_ATTEMPTS=1`/`ITINERARY_NARRATOR_ENABLED=true`/`ITINERARY_NARRATOR_PROVIDER=groq` set only as process-level env overrides -- `.env` itself never modified, `GROQ_API_KEY` never printed).** Both runs: a real repair genuinely completed (attempt_count=1), the narrator succeeded (`groq_itinerary_narrator_provider`), and both summaries correctly, honestly mentioned the adjustment in plain language (e.g. "adjusted after feasibility checks to keep the days manageable") without naming any internal system/model/provider. Every `referenced_experience_ids` value across both runs (9 and 8 respectively) matched a real, currently-scheduled experience_id exactly -- zero unknown references, zero forbidden factual field names, zero overclaim words anywhere in either real report. Both real repairs happened to *reorder* day 2 rather than remove a candidate outright, so the specific "a removed candidate never appears in the narrator's request or output" guarantee (Task 11) is proven by the deterministic test suite (`test_itinerary_narrative_final_state.py`) rather than by these two particular real runs -- noted here explicitly rather than overstated.

**No-fabrication audit**: both real reports scanned for `price`/`rating`/`opening_hours`/`route_time`/`route_distance`/`booking_url`/`review_count`/`availability`/`safety_score` field names anywhere in the nested structure -- zero matches in either. Neither summary claimed the plan was resolved, verified, or complete -- but, as originally run, neither real report proactively surfaced the 8 real, still-unresolved validation issues either. Section 195.1 (below) closed that gap deterministically.

**Strict boundaries honored**: no LLM #1/CandidateQuality change, no new provider discovery, no repair-loop behavior change, no weakened `PlanValidator`, no routing change, no Section 196/197 work, no new dependency, no secret exposed, nothing committed (194A + 194B + 195 remain a combined uncommitted stack, exactly as instructed).

**Tests**: 26 new tests, zero changes to any pre-existing test's outcome (one 182F-era test renamed/updated to reflect `experience_id` as an intentional new allow-listed field, not a leak) -- `test_itinerary_narrative_final_state.py` (17: final-state-only reflection, removed-candidate/cross-day-reference rejection, resolved-vs-unresolved validation context, repaired/failed-repair/no-repair/deterministic-fallback narrator context, forbidden-claim/blank-text rejection, full read-only regression), 5 new Groq adapter tests (valid/hallucinated reference, forbidden-claim/overclaim rejection, the `json_schema`/`strict=True` structured-output regression), 4 new Anthropic adapter tests (the same reference/claim safety, mirrored).

**Verification**: full suite **3860 passed + 18 skipped** (3834 + 26 new tests, zero change to any pre-existing test's outcome). `compileall`/`pytest` clean. No frontend/shared-contract file changed -- frontend checks explicitly skipped for that reason.

### 146.1. Section 195.1: Deterministic Unresolved-Validation Disclosure

Section 195's real verification showed a real gap: a narrator that honestly avoids *claiming* a plan is resolved/verified still isn't the same as one that actually *surfaces* an unresolved limitation -- the LLM is free to simply not mention it, and both real Section 195 runs did exactly that (8 unresolved issues, zero caveats). Section 195.1 closes this with a small, deterministic, non-LLM post-processing step in `ItineraryNarrativeService.generate` (`_apply_final_validation_disclosure`), applied only to an already-`success` result, using the existing `ItineraryNarrativeReport.warnings` field -- no new model field, no provider/prompt change.

**Rule, driven entirely by the request's own (already final-state) `warning_count`/`critical_issue_count`:**
- zero unresolved issues (including a genuinely repair-resolved one): `report` returned completely unchanged.
- one or more unresolved issues and the narrator already returned at least one warning of its own: returned unchanged too (a deliberately simple structural rule -- trust an existing warning, never a fuzzy semantic duplicate check).
- one or more unresolved issues and `report.warnings` is empty: a new report (never a mutation) with exactly one fixed, pre-written sentence appended -- *"Some itinerary checks still need review; see the validation details before relying on the plan."* -- which never names the issue's category and carries none of `_FORBIDDEN_TEXT_PATTERNS`' overclaim language.

Never applied to a `failed`/`not_connected`/`unavailable` result (unchanged from Section 195). Never touches `experience_plan`/`route_feasibility_report`/`route_aware_sequencing_report`/`travel_time_buffer_report`/`validation_report`/`ai_itinerary_reasoning_result`/`ai_itinerary_repair_result` -- proven directly by a read-only regression test (full before/after deep-copy diff).

**Proposal-count sanity check re-confirmed (Task 9)**: unchanged conclusion from Section 195 -- the earlier "proposals=0, grounded/promoted=3" figure was a wrong-JSON-key bug in a throwaway verification script, not a product defect. No candidate-discovery code was touched.

**Combined worktree file count (Task 8)**: Section 195's own report undercounted by exactly one file. `git diff --stat` only shows tracked changes; `backend/app/tests/services/test_itinerary_narrative_final_state.py` was still genuinely untracked at the moment that count was taken, so it never appeared in that diff. `git status --porcelain`, which includes untracked files, correctly shows **33** changed/new paths for the full combined 194A + 194B + 195 (+ 195.1) working tree -- verified directly, no code changed because of this.

**Tests**: 7 new, all in `test_itinerary_narrative_final_state.py` -- unresolved-and-omitted (disclosure added), clean validation (no synthetic warning), resolved-after-repair (old issue never mentioned), exhausted-repair-with-remaining-issue (disclosure added), narrator-already-warns (no duplicate), failed-narrator (unaffected), and full read-only regression.

**Verification**: full suite **3867 passed + 18 skipped** (3860 + 7 new tests, zero change to any pre-existing test's outcome). `compileall`/`pytest` clean. No frontend/shared-contract file changed -- frontend checks explicitly skipped for that reason.

## 147. Section 196: Grounded Natural-Language Feedback Interpreter

```text
user free-text feedback -> final (post-repair, narrated) planning state context
  -> AIFeedbackInterpreterService.interpret() [pure, read-only]
  -> structured AIFeedbackInterpretationResult
  -> NO mutation, NO version, NO provider discovery, NO regeneration yet
  -> (future) Section 197: targeted provider lookup for new_place_requests
     -> targeted regeneration via rerun_affected_stages -> new version/diff
```

**Audited the existing feedback/regeneration/locks/versioning stack first, per Task 1's explicit instruction not to build a parallel subsystem blind.** `POST /trips/{id}/feedback` (`FeedbackService`) already exists and is untouched: it classifies free text with deterministic keyword rules only (no AI, no LLM call), stores a `FeedbackEvent` with `feedback_type`/`handling_status`/an `interpretation` dict whose `method` is literally `"deterministic_rule_based"` and whose `change_preview.blocked_by` already, honestly, lists `"No AI interpretation provider is connected."` -- a pre-existing disclosure this section leaves completely intact (proven by a real HTTP-level regression test, see below). `POST /trips/{id}/regenerate` (Section 174, `RegenerationMutationService`) is the only endpoint that can mutate a plan from feedback, and only when `confirm: true`, at least one feedback event is still pending, zero locks are active, and `rerun_affected_stages` can derive a real affected stage from the feedback's `feedback_type` -- every other case is a distinct, named refusal that changes nothing. Critically, `rerun_affected_stages` today only knows how to map a *feedback_type* (a coarse keyword-classification bucket, e.g. `remove_or_avoid`) to a *stage to rerun* -- it has no concept of a specific experience id, a target day, a pace change, or a new place request. This is exactly why Section 196's action vocabulary (`remove_experience`/`move_experience`/`change_pace`/`adjust_interest`/`regenerate_day`/`general_instruction`) was designed as the smallest useful step *toward* what a future, more targeted regeneration path could consume -- not as capabilities the current `rerun_affected_stages` can already act on. `FeedbackEvent.interpretation` was confirmed as the natural future home for a stored `AIFeedbackInterpretationResult` once Section 197 decides how/when to persist one; Section 196 defines the contract and a pure service that produces it, but writes to nothing.

**The interpreter never mutates, regenerates, or discovers.** `AIFeedbackInterpreterService.interpret(planning_state, feedback_text) -> AIFeedbackInterpretationResult` is the only method on the service -- deliberately no `apply()` and no fail-safe wrapper (unlike `AIItineraryReasoningService`/`AIItineraryRepairService`, which each own a `PlanningState` field to write and wrap that write in `apply_X_safely`). There is nothing for this service to own, so a provider exception propagates directly to the caller rather than being swallowed (`test_provider_exception_propagates_and_never_fabricates`). It gates on `Settings.ai_feedback_interpreter_enabled` (default `False`) and returns an honest `not_connected` result -- without building a request or resolving a provider at all -- when disabled.

**Existing-entity grounding is structural, not trust-based.** `AIFeedbackInterpretationRequestBuilder.build_request` reads the *current, final* `planning_state.experience_plan.daily_plans[*].experiences` (the same post-repair state the narrator reads) into `AIFeedbackItineraryItemContext` records keyed by the real, stable `experience_id`; `candidate_id` (`provider_name:provider_place_id`) is populated only when both fields are actually set on the source `ExperienceItem` -- true today only for AI-promoted candidates, confirmed by the same audit that grounded Section 195's narrator work, and proven by a dedicated test. `AIFeedbackInterpretationRequest.allowed_experience_ids()` is the single source of truth an action may reference. `validate_interpretation_against_request` (a pure function, not a prompt instruction) then structurally rejects any result referencing an experience id outside that set, any day index outside `1..trip_duration_days`, any experience both removed and moved, or any day/experience both preserved and independently targeted -- reused identically by both real provider adapters before either ever reports `completed`.

**New-place requests can never carry fabricated provider identity.** `NewPlaceRequest` has exactly three fields -- `query: str`, `requires_provider_lookup: Literal[True]`, `note: str | None` -- with no field for a coordinate, price, rating, or provider id, so there is structurally nothing for either adapter to invent even if a model tried; the real Groq/Anthropic wire schemas mirror this same narrow shape. A named place the model doesn't recognize as already-scheduled always becomes a `NewPlaceRequest`, never a guessed `experience_id`.

**Ambiguity produces `needs_clarification`, never a guess.** When the model reports genuine ambiguity (e.g. "remove the museum" against two scheduled museums), `AIFeedbackInterpretationResult.status = needs_clarification` is a whole-result state enforced by a `model_validator`: `actions`/`new_place_requests` must be completely empty and `clarification.reason`/`possible_experience_ids` must be populated -- a deliberately conservative "no partial trust" design chosen over letting unambiguous actions coexist alongside a clarification for the ambiguous remainder.

**Explicit "don't change"/"keep" constraints are structural, not prose.** `AIFeedbackPreserveScope` (`day_indices`, `experience_ids`) is a sibling of `actions`, not folded into it -- e.g. "remove Belem Tower but don't change day 1" produces `actions=[RemoveExperienceAction(...)]` plus `preserve.day_indices=[1]` as two independent fields, letting `validate_interpretation_against_request` catch a preserve/action conflict as a simple set-intersection check.

**User factual claims are rationale, never provider truth.** `_FORBIDDEN_TEXT_PATTERNS` (this module's own local copy, per this codebase's established per-leaf-module convention) extends the usual price/rating/route/booking terms with `"is closed"`/`"is unsafe"` specifically so a user's own claim (e.g. "remove it, it's closed now") can steer *which action* the model proposes but can never leak back out as an asserted fact in `summary`/`blocked_reasons`/a `GeneralInstructionAction.note`.

**Provider architecture mirrors every other AI stage in this repo**, and is deliberately kept on its own config surface rather than reusing `AI_ITINERARY_REASONING_*` (Task 17's explicit instruction, since interpreting free text into intent is a genuinely different job from selecting/ordering candidates): `AI_FEEDBACK_INTERPRETER_ENABLED` (default `false`) / `AI_FEEDBACK_INTERPRETER_PROVIDER` (default `not_connected`) / `AI_FEEDBACK_INTERPRETER_MODEL`, with `not_connected`/`anthropic`/`groq` adapters behind `get_ai_feedback_interpreter_provider()`, an unknown provider name falling back to `not_connected`, never a live default.

**The Groq strict-mode lesson (first hit painfully in Section 195, applied proactively here) held on the first try.** Rather than exposing the domain model's `AIFeedbackAction` discriminated union to Groq's structured-output schema directly (untested territory for `oneOf`/`discriminator` under strict mode), the Groq adapter uses one flat `_GroqActionSchema` (a `type` tag plus every possible action field as `X | None`, every field with **no Python default at all**, so nothing gets silently dropped from `required` the way Section 195's `default_factory=list` fields once were) and reconstructs the real typed domain objects afterward. All three real Groq calls below succeeded on the first attempt with zero schema-rejection errors.

**Real Groq verification (3 real calls, `AI_FEEDBACK_INTERPRETER_ENABLED=true`/`AI_FEEDBACK_INTERPRETER_PROVIDER=groq` set as process-level overrides only, `.env`/API key never modified or printed), against a realistic 9-item/3-day Lisbon context:**
1. *"Day 2 is too packed. Make it more relaxed."* -> `completed`, `scope=whole_itinerary`, `actions=[ChangePaceAction(pace="relaxed")]`, `confidence=1.0`. An honest, reasonable interpretation given the vocabulary has no day-specific pace action -- not a defect.
2. *"Remove Belem Tower but don't change day 1."* -> `completed`, `scope=single_experience`, `actions=[RemoveExperienceAction(experience_id="experience_way_24341353")]`, `preserve.day_indices=[1]`, `confidence=1.0`.
3. *"I also want to visit Sintra."* -> `completed`, `scope=new_place_request`, `new_place_requests=[{query: "Sintra", requires_provider_lookup: true, note: null}]`, `confidence=1.0`.

**No-fabrication audit**: all three real result dumps scanned for `price`/`rating`/`opening_hours`/`route_time`/`route_distance`/`booking_url`/`review_count`/`availability`/`safety_score`/`coordinates`/`provider_place_id`/`provider_source` -- zero matches. Zero unknown/hallucinated experience ids referenced across all three runs.

**Current `/feedback` and `/regenerate` behavior is provably unchanged.** Three real HTTP-level regression tests (`test_ai_feedback_interpreter_no_wiring.py`) exercise the actual endpoints end-to-end: `/feedback` still returns `interpretation.method == "deterministic_rule_based"` with the same honest `"No AI interpretation provider is connected."` disclosure; `/regenerate` without `confirm` still refuses with `REGENERATION_NOT_AVAILABLE`; `/regenerate` with `confirm: true` still applies, advances to `v2`, and updates `feedback_history`/`version_history` exactly as before. Five further import-absence tests confirm nothing in `FeedbackService`, `RegenerationMutationService`, `PlanningOrchestrator`, the LangGraph nodes module, or `app/api/routes/trips.py` imports any Section 196 symbol -- this subsystem is fully dormant in the runtime pipeline today, exactly as scoped. Wiring it in (deciding what Section 197's targeted regeneration path does with a `completed`/`needs_clarification`/`rejected` result) is explicitly out of scope here.

**Strict boundaries honored**: no PlanningState mutation, no provider discovery for new places, no call to ExperiencePlanner/routing/repair/narrator, no LangGraph node, no orchestrator stage, no route wiring, no weakened `PlanValidator`, no new dependency, no secret exposed, nothing committed.

**Tests**: 72 new -- `test_ai_feedback_interpretation_models.py` (24: request/result structural safety, every status-consistency branch, remove/move/preserve/preference/new-place/nonexistent/ambiguous/factual-claim/conflict-detection scenarios at the pure-function level), `test_ai_feedback_interpretation_request_builder.py` (7: final-plan reflection, promoted-only `candidate_id`, active-lock reflection, validation counts, empty-plan handling, traveler-context fallback, read-only proof), `test_ai_feedback_interpreter_service.py` (6: disabled-by-default, enabled-calls-provider, exception-propagates, rejected-is-honest, read-only proof, injected-provider-bypasses-factory), `test_groq_ai_feedback_interpreter_provider.py` (17), `test_anthropic_ai_feedback_interpreter_provider.py` (9), `test_ai_feedback_interpreter_no_wiring.py` (9: 5 import-absence + 1 disallowed-vendor-import + 3 real HTTP-level `/feedback`+`/regenerate` regression tests).

**Verification**: full suite **3939 passed + 18 skipped** (3867 + 72 new tests, zero change to any pre-existing test's outcome). `compileall`/`pytest` clean. No frontend/shared-contract file changed -- frontend checks explicitly skipped for that reason.

**Section 196.1 addendum**: real Groq verification surfaced one semantic gap -- "Day 2 is too packed, make it more relaxed" was interpreted as `scope=whole_itinerary` + a global `ChangePaceAction`, silently widening an explicitly day-scoped complaint into a traveler-profile-wide pace mutation. Fixed with two additive changes, no redesign: (1) `RegenerateDayAction` gained a required `instruction: str` field so a day-specific qualitative request is now structurally carried (`day_index=2, instruction="make it more relaxed"`) instead of only living in free text; (2) `validate_interpretation_against_request` gained a bounded, deterministic day-mention extractor (`day`/`days` + digits in a 20-character window -- not a general NLP parser) that rejects a `completed` result combining `scope=whole_itinerary` with a global `change_pace`/`adjust_interest` action when the feedback named a specific day and never said "whole trip"/"every day"/"overall". Both real adapters' prompts were updated with an explicit "explicit scope wins" instruction. Re-verified against real Groq (2 runs of the day-specific case + 1 global-control case): both day-specific runs correctly produced `scope=single_day` + `RegenerateDayAction`, the global control correctly stayed `scope=whole_itinerary` + `ChangePaceAction`. 10 new tests, full suite **3949 passed + 18 skipped**.

## 148. Section 197A: Targeted Regeneration Plan Compiler

```text
validated AIFeedbackInterpretationResult (Section 196/196.1)
  + current PlanningState (current experience_plan, locks, version)
  -> TargetedRegenerationPlanBuilder.build_plan() [pure, deterministic, read-only]
  -> TargetedRegenerationPlan
       - affected/preserved day + experience scope
       - deterministic removals/moves, day instructions, profile mutations
       - required PlanningStage dependencies + reasoning/provider/routing/
         validation/narrator flags
       - status: ready | needs_clarification | blocked | unsupported
  -> NO execution yet
  -> (future) Section 197B: executes a `ready` plan against PlanningState
```

**Audited the existing Section 174 regeneration system first (Task 1), rather than building a parallel one.** `apply_regeneration_mutation` (`app/services/regeneration_mutation_service.py`) is a plain module function -- not a class -- that reruns whatever `list[PlanningStage]` it's given via `PlanningOrchestrator.rerun_affected_stages`, creates one new `VersionHistoryItem`, and marks feedback events applied; it performs zero precondition checks itself (those live in the `/regenerate` route: `confirm`, active-lock count, pending-feedback presence, a derivable affected stage). `rerun_affected_stages` only ever consumes the coarse `PlanningStage` enum (`create_trip, traveler_profile, destination_context, trip_strategy, stay_transport, experience_plan, validation, feedback`) -- it has no concept of a specific experience id, target day, or new place, because that stage list is derived upstream by `FeedbackService`'s deterministic keyword-to-stage rule table (`_FEEDBACK_TYPE_RULES`), never from anything Section 196 produces. Trip locks (`UserLock`) block regeneration **globally** today -- any `is_active` lock refuses the entire `/regenerate` call outright; no stage service reads a lock to selectively preserve just that item. `PlanDiffPreview` is section-name-level only (`would_consider_sections: list[PlanningStage]`), never a before/after content diff. None of this existing infrastructure was replaced -- Section 197A is a new, additive compiler that produces a *different, more precise* plan a future Section 197B could execute through (or alongside) the same `rerun_affected_stages` mechanism, never a replacement for the Section 174 refusal contract.

**197A trusts Section 196's structured intent completely (Task 2) -- it never reparses `feedback_text`.** `TargetedRegenerationPlanBuilder.build_plan(planning_state, interpretation)` reads only the already-typed `AIFeedbackInterpretationResult` fields (`status`, `scope`, `actions`, `new_place_requests`, `preserve`, `clarification`) -- the compiler has no access to the original feedback string at all.

**The plan contract (`app.models.targeted_regeneration_plan.TargetedRegenerationPlan`) is the smallest model that captures Task 3's full list**: `source_version` (the *current* `PlanningMetadata.current_version` at compile time, for Section 197B to detect drift between compiling and executing -- Task 35), affected/preserved day indices and experience ids, typed `experience_removals`/`experience_moves`/`day_instructions`/`traveler_profile_mutation`/`new_place_lookups`, the real `PlanningStage` vocabulary in `required_stages` (never an invented stage name -- Task 18), and explicit booleans (`requires_ai_itinerary_reasoning`, `deterministic_edit_possible`, `requires_provider_discovery`, `requires_routing_rerun`, `requires_validation_rerun`, `requires_narrator_rerun`, `would_create_version`). Status is one of `ready | needs_clarification | blocked | unsupported` (Task 4) -- `ready` only when every action compiled without a stale reference, an out-of-range day, or a structural contradiction.

**Action compilation (Tasks 5-10, 23), using the real `TravelerProfile.pace`/`interests` fields only (Task 9's audit -- no invented profile field):**
- `RemoveExperienceAction` -> deterministic removal, `deterministic_edit_possible=True`, `requires_ai_itinerary_reasoning=False`, `required_stages={EXPERIENCE_PLAN, VALIDATION}` only -- no destination-context/stay-transport/provider rerun (Task 19). The user said remove it; nothing is silently added to replace it (Task 5).
- `MoveExperienceAction` -> deterministic move, both source and target days affected, `candidate_id`/`coordinates` copied verbatim from the item's current, already-provider-backed state (never reconstructed by name -- Task 6/17), same minimal `required_stages`.
- `RegenerateDayAction` -> only the named day affected, `instruction` (Section 196.1's own field) carried forward verbatim, `requires_ai_itinerary_reasoning=True`, no traveler-profile mutation.
- `ChangePaceAction`/`AdjustInterestAction` -> a genuine `TravelerProfileMutation` (`pace`, or `interests_to_add`/`interests_to_remove`), all current days affected (no more precise deterministic dependency exists -- Task 8), `TRAVELER_PROFILE` added to `required_stages` (plus `DESTINATION_CONTEXT` for an interest change, mirroring the legacy `interest_change` stage set).
- `NewPlaceRequest` -> `TargetedNewPlaceLookup(query, note)` only -- structurally cannot carry a candidate id, provider place id, coordinate, or quality score (Task 10); `requires_provider_discovery=True`.
- `GeneralInstructionAction` -> always compiles to `status=unsupported` (Task 23): the action has no structural day/scope field of its own, so there is nothing deterministic to target regardless of the interpretation's top-level `scope` -- honest refusal over a guessed stage mapping.

**Preservation semantics (Task 11/15) are structural, never prose.** `preserved_day_indices` is computed as *(all current days minus every affected day) union explicit `preserve.day_indices`* -- so an unaffected day is preserved by default even with no explicit user instruction (Task 32), and an explicit "keep day 1" is additionally guaranteed even if it happened to already be unaffected (Task 31). `preserved_experience_ids` is the explicit preserve set minus anything actually targeted.

**Trip locks and Section 196 `preserve` are two distinct mechanisms, and are not conflated (Task 12).** `preserve` is per-attempt user instruction carried on this one interpretation; `UserLock` is a persistent, cross-attempt guardrail. Existing global lock behavior is preserved exactly, not weakened: any active lock blocks a targeted plan outright (`status=blocked`) regardless of what the interpretation asked for, mirroring `/regenerate`'s own refusal today -- Section 197A does not attempt the more precise "only block the locked item" behavior the legacy system also doesn't have.

**197A independently re-validates every identity/day reference against the CURRENT plan (Task 13/16/17/34) -- it does not trust that Section 196's own validation ran against the same state.** An `experience_id` no longer present in the current `experience_plan` (e.g. the interpretation was built against an older version) produces `status=blocked` with an explicit stale-reference reason -- never a name-based re-match (version-safety, Task 34/35). A day index beyond the current actual day count is blocked the same way. Structural contradictions Section 196 already guards against (remove+preserve the same id, regenerate+preserve the same day, move-to-a-preserved-day) are independently re-checked here too, since 197A re-derives its own affected/removed/moved sets from the live state rather than reusing Section 196's internal check.

**No second LLM (Task 25).** `TargetedRegenerationPlanBuilder` is one class, one public method, zero provider/LLM imports -- the intelligence already happened in Section 196; this module is policy/compilation only.

**Strict boundaries honored**: no PlanningState mutation, no provider call, no LLM call, no ExperiencePlanner/routing/validator/repair/narrator call, no version/diff created, no route wiring, no frontend change, no secret exposed, nothing committed.

**Tests**: 24 new, all in `test_targeted_regeneration_plan_builder.py` -- remove/move/regenerate-day/global-pace/global-interest/new-place compilation, explicit and implicit preservation, three independent contradiction-defense cases, stale-experience-id and out-of-range-day blocking, source-version carry-through, needs_clarification/rejected/not_connected propagation, active-lock-still-blocks and inactive-lock-does-not, general_instruction and preserve-only unsupported cases, no-experience-plan blocking, multi-day regeneration, and a full read-only regression proving neither `planning_state` nor `interpretation` is ever mutated.

**Verification**: full suite **3973 passed + 18 skipped** (3949 + 24 new tests, zero change to any pre-existing test's outcome). `compileall`/`pytest` clean. No frontend/shared-contract file changed -- frontend checks explicitly skipped for that reason.

## 149. Section 197B: Targeted Regeneration Executor

```text
current PlanningState + a `ready` TargetedRegenerationPlan (Section 197A)
  -> version/lock TOCTOU recheck
  -> isolated deep-copy working state (source PlanningState never touched)
  -> deterministic edits (remove/move) and/or targeted new-place provider grounding
  -> scoped AI reasoning only where the plan requires it
  -> ExperiencePlanner materialization (provider-backed candidates only)
  -> splice every hard-preserved day back from a pre-execution snapshot
  -> route feasibility / route-aware sequencing (affected days only) / buffers
  -> validation (authoritative)
  -> bounded repair, constrained to affected, non-preserved days only
  -> narrator on the FINAL working state
  -> final preservation audit
  -> candidate TargetedRegenerationExecutionResult (resulting_planning_state set
     only when status == completed)
  -> NO persistence -- Section 197C will save/version/diff/consume feedback
```

**Audited every execution seam before writing anything (Task 1).** None of `ExperiencePlannerService.run`, `RouteFeasibilityService.build_report`, `RouteAwareSequencingService.build_report`, `TravelTimeBufferService.build_report`, `PlanValidatorService.run`, or `ItineraryNarrativeService.generate` accepts a day-scoping parameter -- every one of them is whole-trip only, and every one of them mutates/reads a plain in-memory `PlanningState` with zero repository/persistence coupling. This is the key architectural fact 197B is built around: day-scoping cannot be pushed down into these services without changing them, so it is achieved entirely at the executor's own boundary -- by restricting what's sent to AI reasoning, structurally rejecting any reasoning-result day outside the plan's own `affected_day_indices`, and splicing every hard-preserved day back from a snapshot immediately after whole-trip materialization and before any downstream report is computed (so the final itinerary and its routing/validation reports always agree, per Task 8). `RouteAwareSequencingService.apply_report` is the one exception with natural day-scoping: it only reorders the days named in the `report.suggestions` passed to it, so filtering those suggestions to non-preserved days before calling it is sufficient -- no service change needed there. `classify_repairable_issues`/`AIItineraryRepairRequestBuilder.build_request` gained one small, additive `allowed_day_indices: set[int] | None = None` parameter (Task 28) -- the smallest change needed so bounded repair can be constrained to the days execution actually touched.

**The source `PlanningState` is never mutated, under any outcome (Core invariant 1, Task 4).** `TargetedRegenerationExecutor.execute` immediately branches on `plan.status` (non-`ready` plans do zero work), then re-checks `plan.source_version == planning_state.metadata.current_version` (Task 5 -- a plan compiled against a since-changed state is blocked before any copy, provider call, or LLM call) and re-checks active locks (Task 6, TOCTOU defense -- a lock introduced after 197A compiled the plan still blocks execution, preserving the existing global-lock behavior exactly, never weakened). Only after both pass does `planning_state.model_copy(deep=True)` produce the working state every edit happens on. Every non-`completed` `TargetedRegenerationExecutionResult` carries `resulting_planning_state=None` by construction -- there is no code path that returns a partially-mutated state as if it were usable (Task 3/50).

**Hard preservation is a structural snapshot-and-splice, not a hope.** Before any edit, every day in `plan.preserved_day_indices` (and every experience in `plan.preserved_experience_ids`) is deep-copied from the *original* (pre-copy) state. After deterministic edits and/or scoped-reasoning-driven `ExperiencePlannerService.run()` materialization, those exact snapshots are spliced back onto `working_state.experience_plan.daily_plans` -- **before** route feasibility, sequencing, buffers, or validation ever run, so those reports are always computed against the actual final content, never a since-superseded ordering (Task 8's own explicit requirement: "the final itinerary and reports must agree"). One deliberate, disclosed exception: a plan whose *only* content is `new_place_lookups` (no removal/move/day-instruction/profile-mutation) has no hard-preserved days at all, since Section 196/197A never assigns a new place a target day -- the target day is inherently undetermined until scoped reasoning decides where it fits. `preserved_experience_ids` remain enforced regardless.

**Deterministic remove/move never touches provider discovery or the LLM (Task 9/10).** Both are applied as direct list edits on `ExperiencePlan.daily_plans[*].experiences`, re-stamping `stop_order` only -- no other `ExperienceItem` field changes, and a moved item's `provider_place_id`/`provider_source`/`coordinates` are never reconstructed, only carried over. Task 11's consistency requirement is also honored: `ai_itinerary_reasoning_result` (when a completed one already exists) has the removed/moved candidate_id deterministically dropped/relocated across its `days`, so nothing downstream can later claim a removed place is still selected -- never by asking an LLM to re-decide a user-explicit edit.

**Scoped AI reasoning (Task 12-14) restricts the candidate universe, never trusts a bare instruction.** The executor builds the real whole-trip `AIItineraryReasoningRequest` (via `AIItineraryReasoningRequestBuilder`, reused unchanged), then removes every candidate currently reserved by a hard-preserved day (computed directly from the current `experience_plan`'s own provider identity, not from the request) before ever calling the provider -- the model cannot "steal" what it never sees. Two independent structural checks then gate acceptance of the result: (1) every day the result names must be in `plan.affected_day_indices`, or the whole result is rejected (Task 39: a fake/misbehaving provider that returns changes to an untouched day never gets a chance to have those changes silently discarded -- the entire scoped reasoning attempt fails instead); (2) `validate_result_against_request` is re-run against the *restricted* request as defense-in-depth (Task 14/40: catches a provider that ignores the candidate universe it was given and returns a reserved candidate anyway -- rejected because that candidate was never in the filtered `allowed_candidates` at all).

**Global pace/interest mutations are real `TravelerProfile`/`TripRequest` field writes on the working state only (Task 15/16).** `ChangePaceAction`/`AdjustInterestAction` map onto exactly the fields Task 9's own audit confirmed exist (`pace`, `interests`) -- no invented profile concept. The source profile is never touched.

**User-requested new-place grounding never invents identity (Task 17-20).** `TargetedNewPlaceLookup.query` is resolved via `gateway.places.search_must_visit_place` -- the exact same targeted-lookup method `AIDirectedProviderDiscoveryService` already uses for AI-proposed must-visit terms -- then scored via `CandidateQualityService.score_provider_backed_candidate` (the same single-candidate quality entry point Section 192A already built). A `REJECTED`-tier or ungroundable result never becomes a trusted candidate just because the user named it (`status=provider_unavailable`, no mutation applied, honest failure). Provenance is never mislabeled: `CandidateOrigin` gained a third value, `USER_REQUESTED` (Task 17's explicit instruction -- this must never appear as `AI_DIRECTED_PROVIDER_DISCOVERY` just because it reuses the same gateway method; nothing reads `origin` for behavior, so this is additive and safe). A genuinely subtle materialization gap surfaced during implementation: `ExperiencePlannerService`'s AI-guided resolution only ever resolves a `candidate_id` against `destination_context`'s own broad pool -- never against whatever was sent to the reasoning provider -- so a grounded new-place candidate needed to also be registered via the existing "promoted AI candidate" pathway (Step 170D's `PromotedAICandidate`/`AICandidatePromotionReport`, reused as-is) for it to actually materialize into a real `ExperienceItem` with its true provider identity intact, rather than adding a second materialization mechanism.

**Every downstream report is genuinely recomputed, never reused stale (Task 24/26/27).** Route feasibility and travel-time buffers are rebuilt from the final, spliced plan every time. `PlanValidatorService` remains fully authoritative -- a targeted edit being *applied* is never conflated with the result being *valid*; `validation_status` in the execution result is always the validator's own fresh `readiness_status`. Route-aware sequencing (Task 25) is rebuilt whole-trip (report-only, side-effect-free) but its suggestions are filtered to non-preserved days before `apply_report` ever reorders anything -- a preserved day's stop order is provably never touched, verified directly by a test with an intentionally reorderable preserved day.

**Bounded repair is constrained to affected, non-preserved days only (Task 28/29).** `classify_repairable_issues`'s new `allowed_day_indices` filter is threaded through a small executor-local adapter around `AIItineraryRepairRequestBuilder`, so a repairable finding on a preserved day is never even considered -- it stays in the final `validation_report`, honestly reported, never silently fixed by "repairing around" a hard constraint. Repair is skipped entirely (never calls the LLM) whenever no repairable issue exists on an allowed day, matching initial generation's own "no repairable issue -> don't call the provider" rule. `ai_itinerary_repair_attempt_count` is explicitly reset to `0` on the working state before any repair consideration (Task 31) -- a new targeted-regeneration attempt always gets its own bounded budget, and the source state's own (possibly nonzero, stale) count is never read or touched.

**The narrator always runs last, on the final, fully-spliced, fully-validated working state (Task 30/32)** -- never on the source's stale narrative, and a removed item can never appear in it because the plan's content no longer contains it by the time the narrator runs.

**The final preservation audit is the last gate before `completed` (Task 33).** Every hard-preserved day and every explicitly preserved experience is byte-for-byte compared against its pre-execution snapshot; a removed item must be genuinely absent; a moved item must appear exactly once; no duplicate `experience_id`/candidate identity may exist anywhere in the final plan. Any violation returns `status=failed` with `resulting_planning_state=None` -- a preservation failure is never silently "fixed" by returning the source unchanged and claiming success; it is reported as an honest execution failure.

**Strict boundaries honored**: no wiring into `/feedback`/`/regenerate`, no `FeedbackEvent` mutation, no version created, no diff created, no feedback marked consumed, no weakened validator/locks, no LLM ever allowed to invent a place fact, no new dependency, no secret exposed, nothing committed. Section 197C is what will connect this executor to the runtime endpoint, persist a successful result transactionally, create the version/diff, and mark the triggering feedback applied.

**Tests**: 18 new, all in `test_targeted_regeneration_executor.py` -- deterministic removal/move preserving untouched days exactly, scoped day regeneration (success, reasoning-touches-preserved-day rejection, reasoning-steals-reserved-candidate rejection), route-aware-sequencing preservation, global pace mutation (working-state-only), new-place grounding success (real provider-response shape, no fabricated fields) and provider-unavailable honesty, stale-source-version blocking, lock-introduced-after-compile blocking, bounded repair respecting a preserved day's issue, repair-attempt-budget reset, narrator-reflects-final-state, two independent failure-atomicity cases (reasoning exception, materialization exception), and blocked/needs_clarification plans doing zero work.

**Real verification (Task 52-54)**: see this section's own real-run addendum below for the exact real Lisbon trip results (deterministic edit, real Groq scoped-day regeneration, real new-place grounding) -- run via direct `PlanningOrchestrator.create_trip`/`generate_full_plan_via_langgraph` calls (bypassing the HTTP/auth layer entirely, which is unrelated to this section's scope), never persisted, `.env`/API keys never modified or printed.

**Real-run addendum (Tasks 52-54, direct `PlanningOrchestrator.create_trip`/`generate_full_plan_via_langgraph` calls against a real 3-day Lisbon trip -- real OSM/Nominatim/Open-Meteo/Groq providers, `.env` never modified, nothing persisted by 197B itself):**
- **Task 52 (deterministic removal)**: `status=completed`, `affected_days=[1]`, `preserved_days=[2,3]`, `provider_lookup_status=not_required`, `reasoning_status=not_required`, `validation_status=needs_review` (expected -- `ROUTING_PROVIDER=not_connected` by default), `preservation_audit_passed=True`. Source state still had the removed item; the returned working state did not.
- **Task 53 (real Groq scoped-day regeneration)**: feedback "Day 1 is too packed. Make it more relaxed." -> Section 196 interpretation `scope=single_day`, Section 197A plan `status=ready` (`affected=[1]`, `preserved=[2,3]`), execution `status=completed`, `reasoning_status=completed` (real Groq call), days 2 and 3 verified byte-for-byte identical to the source, `narrator_status=success`, source state's day 1 unchanged.
- **Task 54 (real new-place grounding, "Sintra")**: Section 196 interpretation correctly produced a `NewPlaceRequest(query="Sintra")`; Section 197A plan `status=ready`; execution `status=provider_unavailable` -- the real gateway lookup could not ground "Sintra" to a sufficiently high-quality result this run. No fake candidate, no coordinate, no provider id was fabricated; `resulting_planning_state=None`; source state untouched. This is the honest-failure path Task 18 explicitly requires, not a defect (a successful grounding is separately proven by `test_new_place_success_grounds_via_real_provider_shape` using a controlled, injected real-response-shaped gateway).

**Verification**: full suite **3991 passed + 18 skipped** (3973 + 18 new tests, zero change to any pre-existing test's outcome). `compileall`/`pytest` clean. No frontend/shared-contract file changed -- frontend checks explicitly skipped for that reason.

### 149.1. Section 197B.1: Hard-Preserved Additive New-Place Insertion

Section 197B's own report disclosed a gap it left deliberately broad: "a pure new-place-request plan has no hard-preserved days," reasoning that 197A never assigns a target day to a `NewPlaceRequest`. That reasoning is correct about *days as a whole* but wrong about *existing content* -- "I also want to visit Sintra" is an explicit request to ADD something, never permission to let an LLM silently drop, move, reorder, or replace anything already scheduled. 197B.1 closes this without touching Section 197A at all (Task 1's audit confirmed 197A already computes the right signal: `affected_day_indices=[]`, `preserved_day_indices=`*every current day*, for a pure new-place plan -- the bug was entirely in 197B discarding that signal via its own `is_new_place_only -> hard_preserved_days=set()` override).

**A pure additive plan (new-place lookups only, no removal/move/day-instruction/profile-mutation) now takes a dedicated execution path, `_execute_pure_additive_new_place`, structurally separate from the general pipeline** (`_is_pure_additive_new_place_plan` decides which path a `ready` plan takes; every other plan shape -- including "redo day 2 and add Sintra," Task 18 -- is entirely unaffected and still uses the unchanged general pipeline). The key design choice: existing scheduled items are **never exposed to AI reasoning as selectable/regroupable candidates at all** -- most of them structurally couldn't be anyway (only an AI-promoted `ExperienceItem` carries real `provider_place_id`/`provider_source`; a plain broad-pool item's stay `None`, the same fact Section 196's request builder already relies on). Reasoning's only job is choosing **which single day** a newly grounded candidate should join, via a narrow request whose `allowed_candidates` contains exactly that one candidate and whose instructions list the existing day structure as read-only context. The executor then deterministically **appends** the new, provider-backed `ExperienceItem` to that day's existing (untouched) `experiences` list -- it never rebuilds, reorders, or re-stamps a pre-existing item's own fields. This makes Tasks 13/14/15 (drop/move/reorder an existing item) **immune by construction, not merely validated-and-rejected**: even a fake/misbehaving provider whose result nominally "claims" a day now contains only the new candidate cannot actually remove anything, because the executor never applies a day plan's content wholesale for this path -- it only ever reads a `day_index` and appends. Two structural checks still gate the day choice itself: the result must name exactly one day whose `candidate_ids` is exactly `[the one new candidate]` (catches Task 16's unrelated-candidate case), and `validate_result_against_request` is re-run against the same narrow request as defense-in-depth. Task 17 (the requested candidate referenced twice) turned out to be structurally impossible one layer further down -- `ItineraryReasoningDayPlan`'s own pre-existing model validator already forbids a duplicate `candidate_id` within one day, so there was nothing left for the executor to additionally reject.

**Route-aware sequencing never reorders any day during additive insertion, not even the insertion day itself (Task 7/19).** A generic reorder suggestion has no concept of "keep the pre-existing sub-order, only vary the new item's position," so `_rerun_routing_reports` is called with every current day passed as excluded from `apply_report` -- `build_report`'s feasibility data is still computed and attached honestly either way, just never applied. **Repair is skipped entirely for a pure additive execution (Task 9)** -- the existing repair contract has no mechanism to guarantee it would never touch a non-inserted day, so the safer, explicit choice is to leave the validator's findings honestly reported rather than risk "fixing" a new validation issue by rewriting something the user never asked to change (Task 20). **Multiple new-place requests fail atomically (Task 11)**: every request is grounded first, and if any one fails, the whole execution returns `provider_unavailable` before a single item is inserted -- never a silent partial success.

**The preservation audit for this path (Task 21) checks identity, day assignment, and relative order for every pre-existing item, excluding only the `stop_order` bookkeeping field** (which, per its own pre-existing docstring, is expected to be re-derived from real list position, not frozen -- the executor only ever assigns a fresh `stop_order` to the newly appended item itself, never touching a pre-existing item's own `stop_order` value, so this exclusion is a matter of what the audit *compares*, not a relaxation of what's *preserved*). New experience_ids anywhere in the final plan must resolve to a grounded, requested candidate, or the audit fails and no `completed` result is ever returned.

**Tests**: 10 new, all in `test_targeted_regeneration_executor.py` -- pure additive insertion preserving every original item and adding exactly one, existing-items-immune-to-reasoning-claims (Tasks 13-15), valid-insertion-position accepted, unrelated-candidate rejected, duplicate-candidate structural impossibility, explicit regenerate-day-plus-new-place using the unchanged general pipeline, additive-insertion-never-reorders-existing-items (route-aware scheduling explicitly enabled for this test), additive-validation-issue-never-removes-existing-items (repair confirmed skipped), one-failed-grounding-among-multiple fails the whole request, and a full read-only regression proving the source is never mutated. The one pre-existing new-place test (`test_new_place_success_grounds_via_real_provider_shape`) was updated to reflect 197A's real output for a pure-additive plan (`affected_day_indices=[]`, not `[1]`) and continues to pass unchanged in behavior.

**Frontend correction**: the Section 197B report incorrectly stated frontend checks were skipped -- `npm run lint` and `npm run build` did run successfully in that terminal session (no frontend/shared-contract file was touched then or now, so nothing needed rerunning, but the check itself was not skipped).

**Strict boundaries honored**: no wiring into `/feedback`/`/regenerate`, no Section 197C work, no weakened validator/locks, no secret exposed, nothing committed. All of remove/move/explicit-day-regeneration/global-pace/global-interest/stale-version/active-lock/provider-unavailable/narrator/failure-atomicity behavior is unchanged (Task 23), confirmed by the full pre-existing 197B test suite passing unmodified in outcome.

**Verification**: full suite **4001 passed + 18 skipped** (3991 + 10 new tests, zero change to any pre-existing test's outcome). `compileall`/`pytest` clean. No frontend/shared-contract file changed -- frontend checks explicitly skipped for this narrow follow-up for that reason.

## 151. Section 197C: Runtime Targeted Regeneration Integration

```text
user feedback (POST /trips/{trip_id}/feedback, unchanged)
  |
  v
POST /trips/{trip_id}/regenerate
  confirm / locks / pending-feedback checks (shared, unchanged)
  |
  +-- targeted_regeneration_enabled=False (default) --> legacy coarse
  |      stage-rerun path, byte-for-byte unchanged (apply_regeneration_mutation)
  |
  +-- targeted_regeneration_enabled=True
         |
         v
       TargetedRegenerationApplicationService.regenerate(trip_id)
         load current PlanningState
         -> pick oldest pending FeedbackEvent
         -> AIFeedbackInterpreterService.interpret() [fresh, against THIS state]
         -> persist structured interpretation onto FeedbackEvent.interpretation
         -> needs_clarification / rejected / not_connected -> honest refusal, no version
         -> TargetedRegenerationPlanBuilder.build_plan() -- require status=ready
         -> TargetedRegenerationExecutor.execute() -- the one 197A/197B boundary
         -> re-check current persisted version + locks immediately before commit
         -> versioning_service.create_version_after_feedback()
         -> mark the ONE consumed FeedbackEvent applied (same 3 fields legacy uses)
         -> recompute pending-feedback-summary / plan-diff-preview / readiness
         -> planning_state_repository.save()
         -> build_targeted_regeneration_diff() [pure, stable-id, before/after]
         v
       RegenerateResponseData (backward-compatible extension) / structured refusal
```

**Audited the entire existing runtime mutation path first (Task 1).** `POST /trips/{trip_id}/regenerate` (`app/api/routes/trips.py`) already had 5 ordered outcomes, all preserved unchanged: confirm/locks/pending-feedback checks, then (previously) legacy `derive_pending_affected_stages` + `apply_regeneration_mutation`. `apply_regeneration_mutation` (`regeneration_mutation_service.py`) is the one place `FeedbackEvent.applied_at`/`applied_in_version`/`handling_status="applied"` are set, and the one place `versioning_service.create_version_after_feedback` + the 3-recompute sequence (`feedback_service.recompute_pending_feedback_summary`, `plan_diff_preview_service.recompute`, `regeneration_readiness_service.recompute`) run. Async `/regenerate` (`generation_job_service.start_regenerate_job`/`run_regenerate_job`) calls the exact same `apply_regeneration_mutation` in the background, re-deriving eligibility from a freshly-reloaded state rather than trusting the request-time object. The audit also corrected a stale claim in this repo's own `CLAUDE.md`/`config.py` comments: real, functioning Postgres-backed repositories already exist (`app/repositories/postgres_*.py`, gated by `Settings.persistence_backend`, default still `local_json`) -- not "no code path reads a database" as previously documented. Because every repository (local JSON and Postgres alike) persists a `PlanningState` as one opaque `model_dump(mode="json")` blob, every new field this section adds is automatically compatible with both backends -- no migration, no schema change (Task 46).

**Decision: `apply_regeneration_mutation` is NOT reused for targeted regeneration.** Its step 2 (`rerun_affected_stages`) is a coarse, stage-level rerun -- fundamentally the wrong mechanism for an entity-level targeted edit. Instead, `TargetedRegenerationApplicationService._commit` replicates exactly steps 6-8 of `apply_regeneration_mutation` (version creation via the same `versioning_service.create_version_after_feedback` helper, the same 3-field feedback-applied marking, the same 3 recomputes in the same order) around `TargetedRegenerationExecutor`'s own result instead of `rerun_affected_stages`'s. This keeps `PlanningState` internally consistent regardless of which engine produced the new state, without a second `apply_*_mutation` definition drifting from the first.

**Feature gating (Task 2): a genuinely separate flag, `TARGETED_REGENERATION_ENABLED` (default `false`), not a reuse of `AI_FEEDBACK_INTERPRETER_ENABLED`.** They answer different questions -- the interpreter flag is "may the interpreter ever call a provider," the new flag is "should `/regenerate` route through the targeted pipeline at all." Both default off, so a fresh deployment's `/regenerate` is unchanged until an operator explicitly opts into both (plus a real interpreter provider). When targeted mode is on but the interpreter itself is unavailable, `/regenerate` returns an honest `REGENERATION_PROVIDER_UNAVAILABLE` refusal -- it never silently falls back to the legacy coarse path, which could execute changes far outside the scope the user actually asked for (Task 6).

**Interpretation timing (Task 3/4): always fresh, immediately before regeneration, never a reused stale interpretation.** `TargetedRegenerationApplicationService` calls `AIFeedbackInterpreterService.interpret` against the SAME `PlanningState` snapshot the plan gets compiled from, in the same call -- interpretation and plan-compilation can never disagree about which version they're targeting, sidestepping the "interpreted against v2, now v3" staleness class by construction rather than by detection. The structured result is persisted into the EXISTING `FeedbackEvent.interpretation` dict slot regardless of outcome (`{"method": "ai_interpreted", "status", "provider", "model", "source_version", "confidence", "scope", "structured_result", "interpreted_at"}`) -- never a schema/migration change, and never the raw prompt/response, only the already-validated structured result. An old event's `{"method": "deterministic_rule_based", ...}` shape is untouched and still loads (Task 33, proven directly by a dedicated test).

**Clarification/rejected/not_connected never execute, never version, never fall back (Task 5/6).** Each returns immediately after persisting the interpretation record, with an honest, distinct outcome: `NEEDS_CLARIFICATION` / `BLOCKED` (rejected) / `PROVIDER_UNAVAILABLE` (not_connected) -- mapped to three new `ErrorCode`s (`REGENERATION_NEEDS_CLARIFICATION`, `REGENERATION_CONFLICT`, `REGENERATION_PROVIDER_UNAVAILABLE`, all 409, mirroring the existing regeneration-refusal convention exactly). The full clarification detail (`reason`, `possible_experience_ids`) is always recoverable via the pre-existing `GET /trips/{trip_id}` -> `feedback_history[*].interpretation.structured_result.clarification`, without needing to overload the error envelope itself.

**Compile-then-execute through the two existing 197A/197B boundaries only (Task 7/8/9).** `TargetedRegenerationPlanBuilder.build_plan` must return `status=ready` before `TargetedRegenerationExecutor.execute` is ever called; neither deterministic-edit, provider-grounding, scoped-reasoning, preservation, routing, validation, repair, nor narration logic is reproduced in the application service or the route -- both stay thin orchestration.

**Commit boundary and atomicity (Task 10/11).** All AI/provider/executor work happens against an isolated in-memory candidate state (197B's own `model_copy(deep=True)`); persistent mutation happens ONLY after `execution_result.status == completed`. Immediately before committing, the application service reloads the CURRENT persisted state and re-checks both its version (against `plan.source_version`) and its active-lock count -- a concurrent regeneration or a lock introduced mid-flight is caught here and returns `CONFLICT`/`BLOCKED` rather than overwriting newer state (proven directly by two dedicated tests that inject a version/lock change between the service's own two `get_by_trip_id` calls). For the default `local_json` backend, the strongest atomicity actually available is what `LocalJsonStore` already provides (tempfile + `fsync` + atomic rename, single-process in-memory dict, one `RLock`) -- genuinely atomic per-write, but not a cross-request transaction; the version-recheck immediately before `save()` is what closes that gap for THIS specific race, not a database transaction. Documented honestly rather than claimed as ACID.

**Version increment and history (Task 12/13)** use the existing `versioning_service.create_version_after_feedback` helper unchanged -- never a manually concatenated `"v" + number`. `VersionHistoryItem.feedback_event_id` records the one consumed event; `changed_sections`/`preserved_sections` are derived from the plan's own `required_stages`/`preserved_day_indices`. No new version-history API was added (Task 43) -- the existing `GET /trips/{trip_id}` -> `version_history` already surfaces it, matching the pre-existing (and still only) way to read version history in this codebase.

**Feedback lifecycle (Task 15/16): the same 3-field "applied" marking, one pending event per call.** `applied_at`/`applied_in_version`/`handling_status="applied"` are set identically to the legacy path, but only on success, and only for the ONE event this call actually processed. Multiple pending events are handled by an explicit, deliberate strategy (Task 16's "option B," conflict-avoidant by construction): the oldest pending event (by `created_at`) is processed per call; every other pending event is left untouched for a subsequent call. This was chosen over combining/concatenating multiple structured interpretations, which risks silently merging two different user intents into one plan.

**Structured content diff (Task 18-25): a new, deliberately small `TargetedRegenerationDiff` contract**, separate from the pre-existing `PlanDiffPreview` (section-level only, no item identity). Built purely by comparing two real `PlanningState` snapshots (`build_targeted_regeneration_diff`) -- added/removed/moved experiences use stable `experience_id` identity only, never fuzzy name/coordinate matching (Task 19); a "reordered day" is reported only when the day's own experience_id SET is unchanged (never double-counted against an add/remove/move already reported); the traveler-profile diff only ever reports real, existing `pace`/`interests` fields; validation before/after is a factual status+count comparison, never an evaluative "improved" claim (Task 25).

**API response contract (Task 26): `RegenerateResponseData` extended backward-compatibly.** Every new field (`targeted`, `interpretation_status`, `execution_status`, `affected_day_indices`, `preserved_day_indices`, `diff`, `clarification_reason`, `clarification_possible_experience_ids`) defaults to `False`/`None`/empty, so a legacy (non-targeted) response's substance is unchanged -- no frontend migration forced.

**Sync and async share exactly one service boundary (Task 27/28).** The synchronous route calls `TargetedRegenerationApplicationService.regenerate(trip_id)` directly; the async job starter (`start_targeted_regenerate_job`/`run_targeted_regenerate_job`, mirroring the legacy `start_regenerate_job`/`run_regenerate_job` pair) calls the exact same method from a background task, reusing the identical duplicate-job guard/per-trip lock (`check_no_duplicate_running_job`/`_lock_for_trip`) the legacy async path already uses (Task 30's idempotency protection is therefore inherited, not reimplemented). Proven directly by an HTTP test asserting the async job's polled result matches the sync path's outcome for the same operation.

**Bounded scope, disclosed honestly.** Targeted-mode outcomes do not yet append a `RegenerationAttempt` audit-trail entry the way every legacy outcome does -- a real, disclosed gap, not a silent omission. Postgres compatibility is structural (generic JSONB blob persistence, no per-field schema) rather than proven by running a live Postgres-gated test in this environment.

**Strict boundaries honored**: no Section 198 UI, no true itinerary forks, legacy regeneration untouched and still the default, no weakened locks/validator, no data fabricated, no secret exposed, nothing committed.

**Tests**: 31 new -- `test_targeted_regeneration_application_service.py` (12: full success + version/feedback-marking, needs_clarification, rejected, not_connected, plan-blocked, execution-failure, version-conflict-before-commit, lock-before-interpretation, lock-after-execution-before-commit, no-pending-feedback, multiple-pending-events-oldest-only, legacy-interpretation-shape-still-loads), `test_targeted_regeneration_runtime_http.py` (10: real HTTP round-trip for remove/clarification/interpreter-unavailable/lock/day-scoped-success/day-scoped-honest-failure/new-place-provider-unavailable/new-place-success/stale-version-conflict/async-sync-parity, all against a REAL generated plan with only the AI interpretation and, where needed, the AI reasoning provider faked -- 197A's plan compiler and 197B's executor run for real), `test_ai_feedback_interpreter_no_wiring.py` (updated: the old `test_api_routes_do_not_import_interpreter` check is intentionally flipped to `test_api_routes_now_wire_targeted_regeneration_application_service`, mirroring the established Section 193C/194B "flip" convention -- the other four modules' no-wiring checks are untouched and still true).

**Real verification**: a real Lisbon trip (real OSM/Nominatim/Open-Meteo/Groq providers, `.env`/API keys never modified or printed, direct `PlanningOrchestrator.create_trip`/`generate_full_plan_via_langgraph` calls bypassing only the unrelated HTTP/auth layer) round-tripped through real feedback -> real Groq interpretation -> targeted regenerate: `"Remove Mercado da Ribeira please."` -> interpretation `completed` -> plan `ready` -> execution `completed` -> `v1 -> v2`, diff `removed=[the exact experience_id]`, `added=[]`, affected day `[1]`, preserved `[2, 3]`, validation `needs_review -> needs_review` (honest, unchanged), narrator `success`, feedback marked `applied` with `ai_interpreted` method, persisted reload confirmed `v2`. A deliberately vague follow-up ("Remove the place, I don't like it.") real-interpreted as `rejected` -> `blocked`, zero mutation, version unchanged. A real "I also want to visit Sintra." real-interpreted as `needs_clarification` on this run -> zero mutation, `new_version=None`, persisted version unchanged -- an honest, non-deterministic real LLM outcome (a clean grounding success is separately proven by the controlled real-response-shaped HTTP test), not a defect.

**Verification**: full suite **4023 passed + 18 skipped** (4001 + 22 new tests, zero change to any pre-existing test's outcome). `compileall`/`pytest` clean. No frontend/shared-contract file changed -- frontend checks explicitly skipped for that reason.

### 151.1. Section 197C.1: Regeneration Attempt Audit Parity

Section 197C's own report disclosed one gap: targeted regeneration never appended a `RegenerationAttempt` the way the legacy path does for every one of its outcomes. This closes it by reusing the existing model/service exactly as-is -- no second, targeted-only audit model, no new field on `RegenerationAttempt` (Task 1/2's audit confirmed the model is deliberately coarse: `attempt_id`, `status`, `requested_at`, `current_version`, `would_create_version`, `pending_feedback_count`, `active_lock_count`, `reason_code`, `message` -- no per-feedback-event linkage field exists, so `pending_feedback_count` stays the same whole-`feedback_history` count the legacy path already uses, unchanged).

```text
feedback -> interpretation -> plan
  |
  +-- every pre-executor refusal (no pending feedback, active lock, no
  |    experience plan, needs_clarification, interpreter rejected/
  |    not_connected, plan not ready)
  |    -> RegenerationAttempt(status="blocked") -- executor never invoked
  |
  +-- executor invoked, did not complete (reasoning_failed/
  |    provider_unavailable/failed/needs_clarification) OR completed but
  |    the pre-commit version/lock recheck refused to persist it
  |    -> RegenerationAttempt(status="failed") -- real work happened,
  |       nothing was saved
  |
  +-- executor completed AND committed
       -> version created -> feedback marked applied -> recomputes
       -> RegenerationAttempt(status="applied"), current_version ==
          the just-created version
```

**Audited legacy behavior first, and it directly overturned this task's own "preferred" framing (Task 3).** The legacy `/regenerate` route already records a `RegenerationAttempt` for confirm-missing, an active lock, no pending feedback, AND "no derivable affected stage" -- every refusal, not only a real mutation attempt. Real legacy behavior, not the narrower "only audit once the executor is about to run" framing, is what this section mirrors: every terminal branch of `TargetedRegenerationApplicationService._regenerate` now records exactly one attempt, via a small `_record_blocked` helper wrapping the existing `RegenerationAttemptService.record_blocked_attempt` (blocked/failed) and `record_applied_attempt` (applied, called only after version creation/feedback-marking/recomputes, exactly matching that method's own documented ordering requirement).

**The blocked/failed distinction is drawn honestly by whether the executor was ever invoked (Task 5/10/11), not by which error code fired.** `status="blocked"`: no pending feedback, an active lock, no experience plan, `needs_clarification` (from either the interpreter or the plan compiler), interpreter `rejected`/`not_connected`, plan not `ready` -- the executor never ran. `status="failed"`: the executor was actually invoked and its `TargetedRegenerationExecutionResult.status != completed` (this is what correctly distinguishes a new-place `provider_unavailable` reached mid-execution from the interpreter-level `provider_unavailable` above, which never reached the executor at all -- Task 11, proven by a dedicated test using each), or the executor completed in memory but the pre-commit version-conflict/lock recheck refused to persist it (real work happened, nothing was saved -- Task 7).

**Stale-version conflict (Task 7) records its attempt onto the freshest known state, not the stale one the call started from**, so the audit trail's own `current_version` reflects reality -- accepting the same small residual cross-request race section 151 already discloses (not a claim of perfect atomicity), rather than silently skipping the audit or overwriting the newer state to make room for it.

**Persistence failure after a completed execution (Task 6) never claims success.** If `planning_state_repository.save` itself raises after `record_applied_attempt` already appended the audit entry to the in-memory `resulting_state`, that in-memory object (attempt included) is simply never persisted -- the failure is logged and an honest `FAILED` result is returned, with no cascading retry attempt (matching this codebase's existing conventions elsewhere).

**Sync and async get identical attempt semantics for free (Task 8)** -- attempt recording lives inside the one shared `TargetedRegenerationApplicationService.regenerate` boundary, so neither the route nor `run_targeted_regenerate_job` needed any change; proven directly by an HTTP test asserting the async job's polled outcome and the persisted attempt are identical in shape to the sync path's.

**One call, one attempt (Task 12/13).** `regenerate()` returns exactly once through exactly one branch, so exactly one `RegenerationAttempt` is ever appended per call -- Section 194's own bounded AI repair loop stays entirely internal to `TargetedRegenerationExecutor` and never surfaces as a separate attempt. Multiple pending feedback events remain the existing Section 197C "one oldest event per call" strategy; the attempt's own `pending_feedback_count` is the same whole-history count legacy already computes, since the model has no field to represent anything narrower.

**Version consistency (Task 4/14/16) is proven directly, not assumed**: a dedicated test asserts `RegenerationAttempt.current_version == PlanningMetadata.current_version == VersionHistoryItem.version_label == FeedbackEvent.applied_in_version` after a successful regeneration, with no three-way disagreement -- confirmed again in real verification below.

**Strict boundaries honored**: no change to 197B's execution/preservation semantics, no diff-contract change, no change to LLM interpretation or provider grounding, no Section 198, no forks, no live Postgres required, no secret exposed, nothing committed.

**Tests**: 2 new (`test_provider_unavailable_during_executor_is_a_failed_attempt`, `test_targeted_async_duplicate_running_job_returns_409`), the version-conflict/lock-after-execution tests updated to assert the new save + attempt behavior instead of "never saved", and `RegenerationAttempt` assertions added directly into 9 further existing tests (successful sync/async, needs_clarification, rejected, not_connected, plan-blocked, execution-failure) across both the application-service and HTTP suites -- no new test file, since this narrowly extends already-existing coverage rather than requiring a parallel suite.

**Real verification**: a real Lisbon trip, real feedback ("Remove {a real scheduled experience} please."), real Groq interpretation -- `attempts before: 0` -> interpretation `completed` -> plan `ready` -> executor `completed` -> runtime `completed` -> `v1 -> v2` -> `attempts after: 1`, `attempt status: applied`, `attempt current_version: v2` -- agreeing exactly with `metadata.current_version`, `version_history[-1].version_label`, and the feedback event's `applied_in_version` (`three-way agreement: True`).

## 152. Section 199A: Revision Snapshots and Branch-Lineage Foundation

**Foundation only -- no user-created forks yet.** This section builds the persistence/domain layer TRUE itinerary forks (a future Section 199B) will need; it deliberately implements none of the fork-facing behavior itself:

```text
today (pre-199A):
  trip -> current PlanningState -> current_version (a label only)
                                 -> version_history (metadata: which
                                    sections changed, never content)

199A:
  trip -> ItineraryBranch ("Main", is_default=true)
            -> ItineraryRevision (immutable, full PlanningState snapshot)
                 -> ItineraryRevision (parent = the one above)
                      -> ItineraryRevision (branch.head_revision_id)

199B (NOT implemented by this section):
                     ┌-> Branch A -> ...
  revision N ---------┤
                     └-> Branch B -> ...
```

**Task 1's audit, not an assumption: before this section, NO historical full `PlanningState` was ever stored anywhere.** `PlanningStateRepository`/`PostgresPlanningStateRepository` keep only the CURRENT state per `trip_id` (`PlanningStateRepository.save`'s own docstring: "Stores only the latest PlanningState per trip_id"). `version_history`/`VersionHistoryItem` (`app.models.planning_state`, `VersioningService`) is metadata-only bookkeeping -- which sections changed/were preserved, a summary string, a `feedback_event_id` -- never a snapshot of content. Once a trip regenerates from v1 to v2, v1's actual content is gone; no code path could reconstruct it. `VersionHistoryItem.version_id` already existed as a stable opaque id per version-history entry (`_new_id("version")`) but was never used for anything beyond being that entry's own primary key -- `199A` reuses this fact by cross-referencing it (`ItineraryRevision.version_history_item_id`) rather than inventing a second, parallel version-identity scheme. `version_label` (`"v3"`) is a label only, never globally unique, and reused per-trip -- explicitly disqualified as a stable branch/fork identity by this section's own Core Invariant 3.

**Two new, deliberately minimal models (`app/models/itinerary_lineage.py`, Task 19/20).** `ItineraryBranch` (`branch_id`, `trip_id`, `display_name`, `is_default`, `base_revision_id`, `head_revision_id`, `created_at`) and `ItineraryRevision` (`revision_id`, `trip_id`, `branch_id`, `parent_revision_id`, `version_label`, `created_by`, `created_at`, `feedback_event_id`, `version_history_item_id`, `snapshot_available`, `snapshot`). Neither is nested inside `PlanningState` itself, and `ItineraryRevision.snapshot` never contains another revision/branch collection -- deliberately avoiding the recursive-snapshot anti-pattern (Task 7). Both use this codebase's existing `<prefix>_<uuid4 hex>` id convention (`app.models.planning_state._new_id`, `app.models.generation_job.new_job_id`).

**One canonical serialization boundary (Task 10, `app/services/revision_snapshot_service.py`)**: `serialize_planning_state_snapshot`/`deserialize_planning_state_snapshot`, both trivial wrappers around `PlanningState.model_dump(mode="json")`/`model_validate` (the same serialization `PlanningStateRepository`/`PlanningStateRow` already use) -- every other call site (the repository, `RevisionLineageService`, every test) goes through these two functions, never a second hand-built partial copy.

**`RevisionLineageService` (`app/services/revision_lineage_service.py`, Task 25) is the one orchestration boundary**, with a strict, explicitly documented scope: `ensure_default_branch` (idempotent, lazy, cheap -- safe from a read path since it never touches a snapshot), `record_current_revision` (idempotent on `(branch_id, version_label)`, best-effort -- catches and logs its own exceptions rather than ever turning an already-successful generation/regeneration into a reported failure, since the real `PlanningState` write has already succeeded by the time every call site reaches it), `list_branch_lineage`/`load_snapshot` (read-only), and `check_branch_head_consistency` (Task 28 -- reports a real mismatch rather than silently repairing one).

**Wired into all four real generation/regeneration success paths, additively (Tasks 12/13/14), each right after that path's own existing final `planning_state_repository.save(...)` call**: `PlanningOrchestrator.generate_full_plan`/`generate_full_plan_via_langgraph` (the first forkable revision, captured at the same "post_processing" boundary `create_initial_version` itself runs at -- never an empty pre-generation shell), the synchronous `/regenerate` route and `run_regenerate_job` (legacy regeneration, both calling the same `apply_regeneration_mutation`), and `TargetedRegenerationApplicationService._commit` (targeted regeneration, inside the same `try` block that already persists `resulting_state`). No regeneration/generation semantics were changed -- every insertion is a new line calling `revision_lineage_service.record_current_revision(...)` after an already-existing save, never before, never conditionally skipping the real work.

**Parent lineage is always the branch's CURRENT head at record time (Task 5/15), never derived from `version_label` arithmetic** -- proven by a dedicated sequential-versions test (`R1.parent=None`, `R2.parent=R1.revision_id`, `R3.parent=R2.revision_id`, `branch.head=R3.revision_id`) and by the old-trip-compatibility tests below.

**Old, pre-199A trips are handled honestly, never fabricated (Task 2/17/18).** A trip with real `v1`/`v2`/`v3` `VersionHistoryItem` metadata but zero recorded revisions (i.e. every trip that existed before this section shipped) gets: a lazily-created default branch with `head_revision_id=None`; when `record_current_revision` is next called for it (its next real regeneration, or an explicit capture), it snapshots whatever the CURRENT version is (honestly, e.g. `v3`) with `parent_revision_id=None` -- never a guessed/reconstructed parent from `v2`, and `v1`/`v2` never get a fabricated revision record of any kind (not even a `snapshot_available=False` placeholder invented for them). This is never done by reversing a diff or replaying `feedback_history` -- historical state absence remains absence, permanently, by design.

**Snapshot immutability is proven directly, including for mutable nested structures (Task 11/37).** A dedicated test stores R1, then mutates the LIVE `PlanningState`'s `experience_plan.daily_plans[...].experiences` list and `traveler_profile.interests` list in place (exactly the kind of in-place mutation a real regeneration performs) before recording R2 -- R1, reloaded, is unaffected; R2 correctly reflects the mutation. `model_dump(mode="json")` deep-copies by construction, so no stored snapshot ever shares a mutable reference with the live working state.

**Idempotency at two layers (Task 29/30).** The service checks `get_revision_by_branch_and_version` before creating a new revision (a duplicate call returns the existing one, never a second historical revision for the same real version); the Postgres schema additionally enforces this at the database level with a `UNIQUE(branch_id, version_label)` constraint (`create_revision`'s `ON CONFLICT DO NOTHING` + re-fetch is therefore atomically race-safe, not just "check then insert"), and a partial unique index enforces "at most one default branch per trip." 199A does not attempt full branching-on-conflict (Section 199B's job) -- a concurrent write is resolved by the SAME existing `PlanningState`-level conflict semantics (e.g. `REGENERATION_CONFLICT`) that already decide which regeneration "won" before `record_current_revision` is ever reached.

**Persistence: local JSON (Task 21/22) and Postgres (Task 23/24), both behind the existing `get_lineage_repository()` factory function** (mirroring `get_trip_repository()`/`get_job_repository()`'s exact per-call `Settings.persistence_backend` resolution). `ItineraryLineageRepository` (local JSON) adds two new `LocalJsonStore` collections (`itinerary_branches`, `itinerary_revisions`) to the same shared state file, following `JobRepository`'s exact pattern. `PostgresItineraryLineageRepository` adds two new tables via a new Alembic migration (`7cdda8ff3506`, chained after `6f678d2b3ed9` -- confirmed exactly one head). Circular reference handled deliberately: `itinerary_revisions.branch_id`/`trip_id`/`parent_revision_id` carry real foreign keys (the last one self-referential, `ON DELETE SET NULL`); `itinerary_branches.head_revision_id`/`base_revision_id` deliberately carry NO foreign key (a branch is always inserted, head `NULL`, before its first revision exists; the head only ever advances via `UPDATE` after that revision is already committed, so the ordering is safe at the application level without a circular constraint).

**A real gap found by the actual Postgres round-trip test itself, not by inspection (mirroring Section 198B's own discovery pattern), fixed before it shipped**: `parent_revision_id` was initially declared without an inline ORM-level `ForeignKey` (an inconsistent, mistaken deviation from this file's own established convention, where `trip_id`/`branch_id` and every comparable FK elsewhere already declare it inline) -- a dedicated column/FK test caught the missing constraint immediately; fixed to declare the self-referential FK inline, matching both the rest of this file and the migration's own already-correct `ForeignKeyConstraint`.

**Read-only API (Task 26/27/39, `app/api/routes/trips.py`)**: `GET /trips/{trip_id}/branches`, `GET /trips/{trip_id}/branches/{branch_id}/revisions`, `GET /trips/{trip_id}/revisions/{revision_id}` -- all reuse the existing `require_trip_owner` dependency, and additionally verify the fetched branch/revision's own `trip_id` actually matches the URL (never trusting an id alone, since a stable id existing at all reveals nothing about ownership -- proven by dedicated cross-user tests using `second_client`). The revision-list response never carries each revision's `snapshot` (a lightweight `ItineraryRevisionSummary`, Task 20); the revision-detail response reuses the exact same `PlanningState` shape `GET /trips/{trip_id}` already returns (Task 27 -- never a second, richer, internal-only representation), `None` exactly when `snapshot_available=False`.

**Observability (Task 42)**: every log call uses a safe, allowlisted `extra=` dict (`stage="revision_lineage"`, `trip_id`, `branch_id`, `revision_id`, `parent_revision_id`, `version_label`, `snapshot_available`, `operation`) -- never a full snapshot, never a raw prompt/model output/secret.

**Strict boundaries honored** (all explicitly re-verified, not just asserted): no `POST` fork endpoint exists; no code path switches which branch is "active"/editable; no regeneration reads from a historical revision as its starting state; no branch merge/compare exists anywhere, backend or frontend; targeted regeneration's/legacy regeneration's/AI feedback interpretation's own semantics are byte-for-byte unchanged (every existing test in those suites still passes unmodified); no historical snapshot is ever fabricated/reconstructed; no raw LLM reasoning or secret is ever logged or returned by the new endpoints; nothing was committed.

**Files changed**: new -- `app/models/itinerary_lineage.py`, `app/services/revision_snapshot_service.py`, `app/services/revision_lineage_service.py`, `app/repositories/itinerary_lineage_repository.py`, `app/repositories/postgres_itinerary_lineage_repository.py`, `app/schemas/itinerary_lineage.py`, `alembic/versions/7cdda8ff3506_create_itinerary_branches_and_revisions.py`, plus 6 new test files (`test_revision_lineage_service.py`, `test_revision_lineage_regeneration_consistency.py`, `test_revision_lineage_old_trip_compatibility.py`, `test_itinerary_lineage_repository.py`, `test_postgres_itinerary_lineage_repository_integration.py`, `test_migration_schema_itinerary_lineage.py`) and one API test file (`test_itinerary_lineage_api.py`). Modified -- `app/db/models.py` (`ItineraryBranchRow`/`ItineraryRevisionRow`), `app/repositories/protocols.py`/`factory.py` (`ItineraryLineageRepositoryProtocol`/`get_lineage_repository`), `app/core/errors.py`/`app/schemas/errors.py` (`BRANCH_NOT_FOUND`/`REVISION_NOT_FOUND`), `app/api/routes/trips.py` (3 new GET routes), `app/services/planning_orchestrator.py`/`generation_job_service.py`/`targeted_regeneration_application_service.py` (the 4 wiring call sites), `app/tests/conftest.py` (new repository reset), `app/tests/db/test_db_models.py` (table-count/column tests), `app/tests/services/test_generation_job_service.py`-adjacent suites unaffected.

**Tests**: 13 new service-level (`test_revision_lineage_service.py`: branch/revision creation, idempotency, sequential lineage, immutability, snapshot round trip, consistency check), 2 new regeneration-consistency (real `TargetedRegenerationApplicationService`/`apply_regeneration_mutation`, proving three-way version agreement), 4 new old-trip-compatibility, 8 new repository-level (local JSON, including a real restart-persistence test and a shared-file-does-not-clobber test), 5 new live-Postgres-gated integration, 13 new migration AST, 9 new API-level (fresh trip, never-generated trip, 404s, ownership) -- 54 new tests total, plus 2 existing `test_db_models.py` tests updated for the new table count.

**Verification**: `python -m compileall backend/app backend/scripts` clean; directly affected suites (targeted regeneration, generation job service, regenerate refusal) re-run clean before and after wiring; full `pytest` -- 4096 passed, 23 skipped (local JSON backend; the 23 skips are every live-Postgres-gated test, 18 pre-existing + 5 new, all separately run and passed against a real database below); `alembic heads` -- exactly one (`7cdda8ff3506`); a real, throwaway local Postgres container (`docker run postgres:16`, never `docker compose`, no repo file touched) verified a real `upgrade head` (all 5 migrations, including this one), the exact expected schema (partial unique index, unique constraint, all FKs) via `\d`, a manually-inserted legacy-shaped row still working, `downgrade -1` removing exactly the two new tables and nothing else, a clean re-`upgrade head`, and all 32 live-Postgres integration tests (27 pre-existing + 5 new) passing -- container removed afterward.

**Verification**: full suite **4025 passed + 18 skipped** (4023 + 2 new tests, zero change to any pre-existing test's outcome). `compileall`/`pytest` clean. No frontend/shared-contract file changed -- frontend checks explicitly skipped for that reason.

## 153. Section 199B: True Itinerary Forks -- Create, Activate, Branch-Aware Regeneration

**Real forking, on top of 199A's foundation -- still backend-only.** 199A could only ever have one branch per trip; this section adds the one genuinely new user-facing capability -- creating a second, independent branch and switching the editable workspace onto it -- while keeping every 199A invariant intact (a fork never mutates its source revision; historical snapshot absence is never fabricated; no merge, no branch deletion, no frontend -- Section 199C's job):

```text
Main:      R1 -> R2 -> R3M
Alternate:       R2 -> R3A -> R4A          (forked from R2, activated,
                                             regenerated twice)

fork:  new_branch.base_revision_id = source.revision_id
       new_branch.head_revision_id = source.revision_id   (Task 9 --
       shares immutable ancestry with its source; never a cloned new
       root revision)

activate: branch.head_revision_id -> snapshot -> deep PlanningState
          clone -> persisted as the trip's current editable state,
          carrying the new active_branch_id -- no new revision, no new
          VersionHistoryItem, no feedback auto-applied

regenerate (legacy or targeted, sync or async -- unchanged endpoints):
  resolves the CURRENTLY ACTIVE branch -> records the new revision onto
  it, parented on that branch's own current head (even when that head
  revision was inherited from a different branch at fork time) ->
  advances only that branch's head
```

**Task 1's audit, exactly as implemented (not assumed from the spec)**: 199A's `RevisionLineageService.record_current_revision` unconditionally recorded onto `ensure_default_branch(trip_id)` -- correct for 199A (only one branch could ever exist) but not branch-aware. `ItineraryBranch.base_revision_id`/`head_revision_id` already existed and needed no schema change for forking. No "active branch" concept existed anywhere. `check_branch_head_consistency` always checked the default branch. The read-only `list_itinerary_branches`/`list_itinerary_revisions` routes both silently assumed exactly one branch (the former literally returned only the default branch wrapped in a list; the latter rejected any `branch_id` that wasn't the default branch's) -- both were real, if latent, single-branch assumptions this section had to generalize, not just add to.

**Task 2's decision, deliberately NOT the literal "trip-level" suggestion**: active-branch identity lives on `PlanningState.metadata.active_branch_id` (`app/models/planning_state.py`), not a new `TripRecord`/`trips` column. `None` for every trip that has never had a branch explicitly activated (Task 3, including every 199A-era trip) -- always resolved via `RevisionLineageService.resolve_active_branch_id`, which lazily falls back to the default branch, never requiring a manual migration. Reasoning, in order of weight: (1) `PlanningState` is already the one object every generation/regeneration/consistency-check call site already loads, so this needed zero new repository round-trips anywhere; (2) `PlanningStateRow.state` already stores the entire model as one JSONB blob, so this needed **zero Postgres migration** at all; (3) activation (Task 18's atomicity requirement) becomes a single `planning_state_repository.save(...)` -- one record, one write, already atomic at both backends -- rather than two separate records (`TripRecord` + `PlanningState`) that could disagree if a process died between them.

**Task 4's consistency check, stable-identity by construction, not a bare label search**: `check_branch_head_consistency` (generalized from 199A, backward compatible -- `None` still resolves to default, so every 199A-era test for it still passes unchanged) resolves the STABLE `active_branch_id` first, reads THAT branch's STABLE `head_revision_id`, and only then compares that one specific revision's own `version_label` against the live state -- never a trip-wide "find a revision with this label" lookup, which is genuinely ambiguous once sibling branches can both legitimately reach "v3" (Task 26, proven directly by the central Task 40 test below, where Main's R3M and Alternate's R3A are BOTH labeled "v3" with different `revision_id`s).

**Fork creation (`ItineraryForkService.create_fork`, Task 6-12) never mutates or duplicates the source revision** -- proven directly (a dedicated test reloads the source revision after fork creation and asserts byte-for-byte equality). Rejects honestly, never reconstructing: unknown/cross-trip `source_revision_id` (`SOURCE_NOT_FOUND`/`REVISION_NOT_FOUND`), a real revision with `snapshot_available=False` (`SNAPSHOT_UNAVAILABLE` -- Task 34, an old pre-199A metadata-only version can never be forked), empty/oversized `display_name` (`VALIDATION_ERROR`), and a case-insensitive/trimmed duplicate name for the same trip (`BRANCH_NAME_CONFLICT`, Task 11) -- enforced at BOTH the service level (an immediate, friendly check) and the database level for Postgres (a real `UNIQUE(trip_id, lower(display_name))` index, Task 48 concurrency; local JSON gets the service-level check only, a disclosed residual race matching this section's own "no full distributed locking system" scope). `activate_after_create=true` (Task 33) applies the EXACT same guards a standalone activation call would -- never bypassed, and creation/activation are always reported separately in the response, never conflated (a blocked activation after a successful creation leaves the branch created but the workspace unswitched -- honestly documented as "not rolled back," since neither storage backend wraps the two steps in one transaction).

**Branch activation (`RevisionLineageService.activate_branch`, Task 13-18) applies four independent workspace-cleanliness guards against the CURRENT branch's workspace before touching anything**, all proven with dedicated tests: pending feedback (`BLOCKED_PENDING_FEEDBACK` -- unsnapshotted, would not carry over, Task 16), an active lock (`BLOCKED_ACTIVE_LOCK` -- extends this codebase's existing "locks block outright, never worked around" policy from regeneration to branch switching too, Task 17), a running generation/regeneration job (`BLOCKED_RUNNING_JOB` -- never auto-cancelled, Task 15), and the current branch's own head disagreeing with the live state (`BLOCKED_STATE_CONFLICT`, Task 4/39 -- never guesses which state should win). A blocked activation makes **zero** persisted change -- proven directly (state/feedback/lock assertions re-read after every blocked-activation test). A successful activation is the single atomic write described in Task 2's reasoning above; never creates a `VersionHistoryItem`, never auto-applies feedback.

**Cross-branch ancestry (Task 9/10) is a real, load-bearing case, not an edge case papered over**: a freshly forked branch's `head_revision_id` legitimately belongs to a DIFFERENT branch (its source) until its own first child is recorded -- `list_branch_lineage`/the `GET .../revisions` route stay strictly branch-local (Task 42's "smallest truthful contract": an inherited head is never relabeled as if it belonged to the fork), while `record_current_revision`'s existing 199A parent-lineage logic (`parent_revision_id = branch.head_revision_id`) needed **zero code change** to correctly parent a fork's first child onto a revision owned by a different branch -- proven directly by the central Task 40 test and the real HTTP-level targeted/legacy/async regeneration tests below.

**Branch-aware regeneration capture required changing exactly one thing**: `record_current_revision` now resolves `resolve_active_branch_id(planning_state)` instead of unconditionally `ensure_default_branch` (Task 24/25). Every one of the 4 real call sites from 199A (`PlanningOrchestrator.generate_full_plan`/`generate_full_plan_via_langgraph`, the sync `/regenerate` route, `run_regenerate_job`, `TargetedRegenerationApplicationService._commit`) needed **zero changes** -- they already just call `record_current_revision(final_state)` after their own save, and that function now transparently records onto whichever branch is active. Proven end-to-end through the REAL production HTTP routes (not simulated): a real legacy sync regeneration on an activated fork (Task 27/29), a real targeted regeneration on an activated fork with a controlled AI double (Task 28, mirroring Section 198B's own controlled-verification precedent), and a real async job (Task 30) -- all three independently confirm Main's head is byte-for-byte untouched while the fork's head advances with the correct cross-branch parent. Task 30's async safety is structural, not incidental: `activate_branch`'s own running-job guard (Task 15) makes "switch branches while a job from a different branch is in flight" unreachable through the real API, proven by a dedicated blocked-activation test.

**Task 31's audit of `/generate` re-entry**: `generate_full_plan`'s own `VersioningService.create_initial_version` call was already idempotent before this section (a no-op once `version_history` is non-empty) -- re-calling `/generate` re-runs the whole pipeline against the CURRENTLY ACTIVE branch's live working copy but never produces a second "v1" label, so `record_current_revision`'s own idempotency correctly treats it as already-recorded and does not capture a duplicate snapshot. This is `/generate`'s pre-existing, unchanged "initial-plan-only" meaning (Task 31's own "preserve that meaning" instruction), not a new 199B behavior. The one genuinely new invariant proven directly: `/generate` never touches any branch OTHER than the currently active one -- a dedicated test forks a branch, calls `/generate` again on Main, and asserts the fork's head/base/revision-list are completely unaffected.

**Read API extended, not replaced (Task 20-22, 26/27 continuity honored)**: `GET .../branches` now returns every branch for the trip (default first, then `created_at`, then `branch_id` as a final deterministic tie-breaker, Task 41) plus a top-level `active_branch_id` and a per-branch `is_active` flag (computed per-response, never a stored `is_active` field on `ItineraryBranch` itself -- 199A's own reasoning against ambiguous multiple-`true` rows still applies at the model level). Two new mutating routes: `POST .../branches` (create fork, `201`) and `POST .../branches/{branch_id}/activate` (`200`) -- both owner-protected via the same `require_trip_owner` dependency every route in this file already uses, and both verify the fetched branch's own `trip_id` before touching it (Task 39/49, proven by dedicated cross-user tests: a second user gets `403` for cross-trip fork/activation attempts exactly like every read route already did).

**Security/no-fabrication (Task 49/Strict boundaries)**: no raw LLM reasoning, prompt, or secret is ever logged (`app/services/itinerary_fork_service.py`/`revision_lineage_service.py`'s `activate_branch` both log only allowlisted ids/labels/statuses, Task 50); a metadata-only historical revision can never be forked (proven directly); ownership is enforced identically to every existing route. No merge, no branch deletion, no frontend, no regeneration reading from a historical revision as its starting state (regeneration always reads the CURRENT live `PlanningState`, exactly as before -- activation is the only way a historical snapshot ever becomes the live state, and it is never itself a regeneration).

**New error codes** (`REVISION_SNAPSHOT_UNAVAILABLE`, `BRANCH_NAME_CONFLICT`, `BRANCH_SWITCH_BLOCKED`, `BRANCH_STATE_CONFLICT`, all `409`) plus reuse of 199A's `BRANCH_NOT_FOUND`/`REVISION_NOT_FOUND` (`404`) and the existing `VALIDATION_ERROR` (`400`) -- no code invented for a case an existing one already covers (Task 44).

**Migration**: `cc7238a1bca3` (chained after 199A's `7cdda8ff3506`) adds exactly ONE database object -- a case-insensitive unique index `uq_itinerary_branches_trip_id_display_name_lower` on `itinerary_branches(trip_id, lower(display_name))` -- no new table, no new column (Task 46: `active_branch_id` needed none, per the Task 2 reasoning above). Verified against a real, throwaway local Postgres container exactly like every prior section: a clean `upgrade head` (all 6 migrations), the index confirmed via `\d`, a real `IntegrityError` on both a duplicate default-branch insert (199A's own index, re-confirmed) and a duplicate-display-name insert (this section's new one), a real fork + independent branch advancement through the real Postgres repository, `downgrade -1` removing exactly that one index, a clean re-`upgrade head`, and all 39 live-Postgres integration tests (32 pre-existing + 7 for this section) passing -- container removed afterward.

**Files changed**: new -- `app/services/itinerary_fork_service.py`, `alembic/versions/cc7238a1bca3_add_itinerary_branch_display_name_.py`, 9 new test files (`test_itinerary_fork_service.py`, `test_revision_lineage_branch_activation.py`, `test_itinerary_branch_independence.py`, `test_itinerary_branch_aware_targeted_regeneration.py`, `test_itinerary_fork_and_activation_api.py`, `test_itinerary_branch_aware_async_regeneration.py`, `test_itinerary_branch_generate_reentry.py`, `test_itinerary_branch_fork_persistence_restart.py`, `test_migration_schema_itinerary_branch_display_name.py`). Modified -- `app/models/planning_state.py` (`PlanningMetadata.active_branch_id`), `app/services/revision_lineage_service.py` (`resolve_active_branch_id`, branch-aware `record_current_revision`/`check_branch_head_consistency`, `activate_branch` + `BranchActivationStatus`/`BranchActivationResult`), `app/api/routes/trips.py` (branch list now multi-branch + `active_branch_id`/`is_active`, revision list now looks up any real branch instead of only the default, 2 new POST routes), `app/core/errors.py`/`app/schemas/errors.py` (4 new error codes/constructors), `app/schemas/itinerary_lineage.py` (fork/activation request/response shapes, `is_active`), `app/db/models.py` (the new index on `ItineraryBranchRow`), `app/tests/repositories/test_postgres_itinerary_lineage_repository_integration.py` (2 new tests appended).

**Tests**: 11 new fork-service, 10 new activation-guard, 1 central branch-independence (Task 40, the section's own acceptance test), 1 branch-aware targeted-regeneration, 9 new fork/activation API-level (including ownership and ordering), 1 async branch-awareness, 1 `/generate` re-entry safety, 1 local-restart, 5 new migration AST, 2 new live-Postgres -- 42 new tests total, all passing on first run except one (a test setup bug caught immediately -- a `PlanningState` never persisted before calling `activate_branch`, fixed in the test itself, not the source).

**Verification**: `python -m compileall backend/app backend/scripts` clean; directly affected 199A/198B suites re-run clean after every wiring change; `alembic heads` -- exactly one (`cc7238a1bca3`); live Postgres verified as described above, container removed. Full `pytest` (local JSON backend, run fresh after the observability pass) -- **4136 passed, 25 skipped** (25 = every live-Postgres-gated test, 23 pre-existing + 2 new for this section, all separately run and passed against a real database above).

**Observability (Task 50) tightened after an initial pass**: `create_fork`'s existing `stage="itinerary_branch"` log gained a `status` field; `activate_branch` (previously logging only its one success path under `revision_lineage_service.py`'s general `stage="revision_lineage"` tag) now logs EVERY outcome -- success and all four refusal kinds -- through one internal `_log_and_return` helper, consistently tagged `stage="itinerary_branch", operation="activate"` matching this task's own literal spec, with `status`/`branch_id`/`previous_branch_id`/`head_revision_id` and never a snapshot, prompt, or secret.

## 154. Section 199B.1: Canonical Branch-Head Workspace Consistency

**`active_branch_id` + `version_label` alone was never proof the live workspace equals a branch head -- this section closes that gap explicitly, not by assumption.** 199B's own `check_branch_head_consistency` resolved the active branch's stable `head_revision_id` (already correct -- never a trip-wide label search), but then only compared that head revision's `version_label` against the live state's `metadata.current_version`. A live `PlanningState` can carry `active_branch_id=B`, `current_version="v3"`, and STILL have itinerary/profile/provider-backed content that silently diverged from B's immutable R3 snapshot -- nothing before this section could have caught that, and Task 9-11's own tests (below) prove the OLD check would have incorrectly passed every one of them.

```text
OLD check:  active_branch_id -> branch.head_revision_id -> head_revision.version_label == current_version
            (proves label agreement only -- NOT content agreement)

NEW check:  active_branch_id -> branch.head_revision_id -> require snapshot_available
            -> deserialize head snapshot -> version_label still checked
            -> canonical REVISION-CONTENT projection (live) == canonical
               REVISION-CONTENT projection (head snapshot)
            -> BRANCH_STATE_CONFLICT on any disagreement, at any nesting depth
```

**Task 1's field-by-field audit (every `PlanningState` field individually classified, never a blanket "ignore metadata"/"ignore audit-like fields" rule -- recorded as code documentation directly above the new projection function, `app/services/revision_snapshot_service.py`, so the classification stays next to the code it governs):**

- **REVISION CONTENT** (must agree with the branch head snapshot; the WHOLE remainder of `PlanningState` after the two exclusion lists below -- a deliberate full-`model_dump` comparison with an explicit exclude-list, never a hand-picked allow-list, so a newly added `PlanningState` field is compared by default instead of silently skipped): `trip_request`, `traveler_profile`, `destination_context`, `weather_context`/`holiday_context`/`currency_context`, `trip_strategy`, `stay_transport`, `experience_plan`, every AI/candidate/routing/provider report (`candidate_quality_report`, `route_feasibility_report`, `route_aware_sequencing_report`, `travel_time_buffer_report`, `accommodation_inventory_report`, `flight_inventory_report`, `ai_candidate_proposal_batch`, `candidate_grounding_batch`, `ai_provider_discovery_result`, `ai_candidate_promotion_report`, `ai_itinerary_reasoning_result`, `ai_itinerary_repair_result`, `ai_itinerary_repair_attempt_count`), `validation_report`, `decision_cards`/`experience_cards`/`validation_cards`, `provider_status`/`provider_coverage`/`unavailable_data`/`data_sources_used`, `itinerary_narrative_report`, `version_history` (append-only lineage identity -- never legitimately differs under an unchanged label), `planning_state_id`/`trip_id`, `metadata.current_version`/`pipeline_status`/`created_at`.
- **GUARDED TRANSIENT USER STATE** (may legitimately differ; already separately guarded, so the content projection must NOT re-enforce it): `feedback_history`/`pending_feedback_summary` (pending feedback is the whole point of existing between revisions -- `activate_branch`'s own pending-feedback guard already blocks switching on it, Task 3/12), `user_locks` (active locks already separately block switching; an inactive lock -- already proven in 199B -- must not trip the new content check either, confirmed by a dedicated test).
- **AUDIT / DERIVED TRANSIENT STATE** (excluded because it legitimately changes independently of any specific revision's content): `regeneration_attempts` (the model's own docstring: "never a snapshot of plan content... purely bookkeeping" -- a blocked/failed attempt can append without ever creating a version, Task 13), `regeneration_readiness`/`plan_diff_preview` (both recomputed from scratch on nearly every write, confirmed by reading `RegenerationReadinessService.recompute`/`PlanDiffPreviewService.recompute`'s own docstrings -- derived, never independent content), `generation_progress` (real backend stage-progress bookkeeping, its own `is_real_backend_stage_progress` field exists specifically to mark it as never travel/plan content), `metadata.active_stage` (mid-pipeline-only, ephemeral like `generation_progress`), `metadata.updated_at` (confirmed, not assumed: `PlanningState.touch()` is called by every mutation helper -- `set_active_stage`, `set_pipeline_status`, both `VersioningService` methods -- so it changes constantly and carries no content meaning), `metadata.extra` (confirmed by a repo-wide search: written by no code path anywhere in this codebase).
- **`metadata.active_branch_id`** gets its own explicit call-out (Task 3/4) even though it is technically a `metadata` sub-field: a freshly forked branch's HEAD SNAPSHOT may be a revision Main recorded (so the stored snapshot's own `active_branch_id` reads "Main" or `None`), while the just-activated LIVE clone correctly has the fork's own id written onto it (`activate_branch` sets it explicitly at activation time). Comparing this field as content would make every fresh fork permanently, falsely "inconsistent" the moment it activates -- branch identity is proven separately, by resolving the active branch and reading ITS `head_revision_id`, never by comparing this pointer as itinerary content. Proven directly by a dedicated "inherited fork head" test (Task 15) that switches away from and back to a freshly activated fork with zero false conflicts.

**The canonical projection (Task 2, `revision_snapshot_service.build_workspace_consistency_projection`) is one function, one call site inside `check_branch_head_consistency` -- never scattered manual field comparisons.** Takes a `PlanningState`, returns its full `model_dump(mode="json")` with the two exclusion lists above popped out (top-level fields, then `metadata`'s own sub-fields) -- comparing two projections with plain dict `==` catches drift at ANY nesting depth (Task 11: a single experience's own `provider_place_id` changing several levels deep inside `experience_plan.daily_plans[...].experiences[...]` is caught exactly the same way a top-level field change would be, proven by a dedicated test), never relying on top-level identity alone.

**`check_branch_head_consistency` (Task 5) now runs 5 real steps in order** (resolve active branch -> resolve exact `head_revision_id` -> require the head revision's snapshot actually be available and deserializable, treating an unverifiable head as inconsistent rather than silently trusted -- defensive, since a real branch head should always carry one by construction -> verify `version_label` agreement, unchanged from 199B -> compare canonical projections) -- never silently repairs a mismatch, only reports `False` through the same `BranchActivationStatus.BLOCKED_STATE_CONFLICT`/`BRANCH_STATE_CONFLICT` vocabulary 199B already established.

**Activation (Task 6) required zero new guard-ordering logic** -- `activate_branch` already called `check_branch_head_consistency` as its fourth guard in 199B; strengthening that ONE method's own internal comparison automatically strengthens activation, with the exact same guard order preserved (pending feedback -> active lock -> running job -> state consistency -> snapshot load). Proven with 3 new dedicated tests (same-label `ExperiencePlan` drift, same-label `TravelerProfile` drift, nested provider-identity drift) -- all three would have passed the OLD label-only check and are now correctly refused with zero persisted change (Task 17, proven directly: active branch, both branch heads, revision counts, and feedback history all reloaded and compared before/after every blocked attempt).

**Regeneration itself is now ALSO guarded (Task 7/8) -- the one genuinely new code path this section adds**, at the one shared boundary both sync and async already call through for each engine, so no comparison is duplicated in either job runner:
- **Legacy**: `apply_regeneration_mutation` (`regeneration_mutation_service.py`) checks first, before any stage rerun, raising a new `BranchWorkspaceConflictError` (mirroring `RegenerationMutationError`'s own carry-the-planning-state-for-a-blocked-attempt convention) -- caught identically by the sync route and `run_regenerate_job`, both recording a blocked `RegenerationAttempt` with `reason_code=BRANCH_STATE_CONFLICT` and refusing with the same error code (`409`) / job failure (`error_code="BRANCH_STATE_CONFLICT"`).
- **Targeted**: `TargetedRegenerationApplicationService._regenerate` checks first, before the pending-feedback/lock/interpretation checks that already existed -- a NEW `TargetedRegenerationRuntimeStatus.WORKSPACE_CONFLICT` value (additive; `CONFLICT`'s own existing "stale-version race during commit" meaning is completely untouched, per this section's own "do not change targeted regeneration meaning" boundary) maps to the SAME `BRANCH_STATE_CONFLICT` error code in both the sync route and `generation_job_service.py`'s `error_code_by_status` table, so a caller sees one consistent code regardless of which regeneration engine handled the request.
- **Async**: both paths proven live through real HTTP -- a real `202`/job-poll for legacy async, and a direct `TargetedRegenerationApplicationService.regenerate` call proving the interpreter is never even reached once the workspace check refuses.
- **Pending feedback never false-conflicts either check** (Task 12): the canonical projection already excludes `feedback_history`, so submitting feedback and then regenerating normally continues to work exactly as before -- proven both directly (`check_branch_head_consistency` still returns `True` immediately after appending pending feedback) and through 3 full real regeneration flows (legacy sync, legacy async, targeted) that submit feedback and regenerate successfully with no drift introduced.

**Files changed**: modified only -- `app/services/revision_snapshot_service.py` (`build_workspace_consistency_projection` + the two exclusion sets), `app/services/revision_lineage_service.py` (`check_branch_head_consistency` strengthened, `_log_consistency_failure` helper consolidating Task 19's structured logging), `app/services/regeneration_mutation_service.py` (`BranchWorkspaceConflictError` + the new check), `app/services/targeted_regeneration_application_service.py` (the new check + `WORKSPACE_CONFLICT` handling), `app/services/generation_job_service.py` (both exception-handling branches), `app/api/routes/trips.py` (both exception-handling branches), `app/models/targeted_regeneration_runtime.py` (`WORKSPACE_CONFLICT` status). No model/schema/error-code additions beyond the one new enum value -- `ErrorCode.BRANCH_STATE_CONFLICT` already existed from 199B and is reused, not duplicated (Task 44 precedent honored again). **No new persistence schema, no new migration** (Task 18) -- the entire fix lives in comparison logic over data 199A/199B already store.

**Tests**: 10 new direct service-level (same-label content drift x3 -- experience plan, traveler profile, nested provider identity; pending-feedback non-conflict x2; regeneration-attempt-audit non-conflict; inactive-lock non-conflict; inherited-fork-head non-conflict; exact-state re-switching re-verified with the strengthened check; full failure-atomicity), 1 real HTTP legacy-regeneration-refused-on-drift, 1 real HTTP async-legacy-regeneration-refused-on-drift, 1 real targeted-regeneration-refused-on-drift (proving the interpreter is never reached) -- 13 new tests, zero regressions across the full 199A/199B/198B suite (every existing test for `check_branch_head_consistency`/`activate_branch`/fork creation/branch-aware regeneration re-run unchanged and still passes, since every pre-existing test's scenario is either lineage-uninitialized -- the early-return `True` path, unaffected -- or genuinely content-consistent).

**Verification**: `python -m compileall backend/app backend/scripts` clean; every directly affected 199A/199B/198B suite re-run clean after each wiring step; full `pytest` result recorded in this section's own "Return exactly" report below (this doc entry was written before that final run to avoid a stale number). No new Alembic migration; no live-Postgres re-verification performed (Task 18/Validation instructions: "no storage schema should change" -- confirmed true, since this section touches zero files under `app/db/`/`alembic/`) -- 199B's own live-Postgres verification remains fully valid.
