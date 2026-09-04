# LLM Reasoning Pipeline

## 1. Purpose

This document defines how TravelObligator uses LLMs safely and responsibly.

The LLM should help with reasoning, interpretation, explanation, and feedback understanding.

The LLM should not be treated as a source of factual travel data.

The goal is to make AI useful without allowing it to hallucinate:

- places
- restaurants
- accommodations
- prices
- ratings
- opening hours
- routes
- safety claims
- provider coverage
- availability
- booking links

TravelObligator should use AI as a reasoning layer, not as a data provider.

---

## 2. Core Rule

The LLM may reason from available evidence.

The LLM must not invent evidence.

Provider data, open data, user input, deterministic calculations, and explicit assumptions are the only valid inputs for factual reasoning.

---

## 3. What AI Is Allowed To Do

The LLM may be used for:

- interpreting free-text traveler preferences
- converting vague input into structured profile fields
- summarizing destination context
- explaining trip strategy tradeoffs
- explaining stay area recommendations
- explaining accommodation ranking
- explaining why experiences fit the traveler
- writing decision cards
- writing experience cards
- performing subjective validation reasoning
- interpreting user feedback
- identifying affected stages
- writing change summaries
- explaining unavailable provider coverage

The LLM should produce structured outputs that are validated before being accepted.

---

## 4. What AI Is Not Allowed To Do

The LLM must not invent:

- destination facts
- place names
- restaurant names
- accommodation names
- flight options
- ratings
- review counts
- prices
- availability
- opening hours
- ticket prices
- travel times
- walking distances
- transit lines
- stop names
- schedules
- booking links
- cancellation policies
- baggage rules
- safety ratings
- provider coverage
- unavailable provider results

If the data is missing, the LLM should say that it is missing.

Missing data should become:

```text
unavailable
low confidence
assumption
needs provider data
needs user confirmation
```

It should not become a confident recommendation.

---

## 5. AI Input Boundary

Every LLM call should receive a clear input package.

The input package should include only:

- relevant Planning State sections
- provider-backed facts
- open-data-backed facts
- deterministic calculations
- unavailable data fields
- provider coverage
- user input relevant to the stage
- explicit assumptions
- the expected output schema

The LLM should not receive unrelated planning state sections unless needed.

---

## 6. Standard LLM Request Shape

Every AI reasoning call should follow a standard request shape.

```json
{
  "task": "",
  "stage": "",
  "allowed_inputs": {
    "user_input": {},
    "traveler_profile": {},
    "destination_context": {},
    "trip_strategy": {},
    "stay_transport": {},
    "experience_plan": {},
    "validation_report": {},
    "provider_coverage": {},
    "unavailable_data": [],
    "deterministic_results": {},
    "assumptions": []
  },
  "rules": {
    "do_not_invent_facts": true,
    "use_only_provided_data": true,
    "mark_missing_data": true,
    "return_structured_json": true
  },
  "output_schema": {}
}
```

---

## 7. Standard LLM Response Shape

Every LLM response should return structured JSON.

```json
{
  "result": {},
  "reasoning_summary": "",
  "assumptions": [],
  "unavailable_data_referenced": [],
  "confidence": 0.0,
  "claim_sources": []
}
```

The backend should validate this response before accepting it.

If the response is invalid, the backend should reject it and retry or fail gracefully.

---

## 8. Claim Source Rules

Every explanation should distinguish facts from reasoning.

Allowed claim source types:

```text
provider_fact
open_data_fact
user_input
system_rule
ai_inference
assumption
unavailable_data
```

Example:

```json
{
  "claim": "This restaurant is near the previous activity.",
  "source_type": "provider_fact",
  "source": "places_provider"
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
    "activity count"
  ]
}
```

Example:

```json
{
  "claim": "Vacation-rental inventory is unavailable.",
  "source_type": "unavailable_data",
  "source": null
}
```

---

## 9. Stage-Level AI Responsibilities

## 9.1 Traveler Profile

AI may:

- interpret free-text preferences
- infer soft preferences
- identify missing information
- convert raw user language into structured profile fields

AI must not:

- invent constraints
- invent budget
- invent accessibility needs
- overstate confidence when the user was vague

Example:

User says:

```text
Traveling with parents, not too much walking.
```

Allowed AI inference:

```json
{
  "mobility_profile": {
    "walking_tolerance": "moderate_to_low",
    "confidence": 0.7
  },
  "decision_weights": {
    "comfort_weight": 0.9,
    "walkability_weight": 0.85
  }
}
```

---

## 9.2 Trip Strategy

AI may:

- explain destination suitability
- recommend trip style
- summarize tradeoffs
- create planning strategy
- create planning targets based on Traveler Profile and Destination Context

AI must not:

- select final attractions
- select final restaurants
- select final accommodations
- invent destination facts
- invent cost estimates not present in data

Trip Strategy should define planning direction, not the itinerary.

---

## 9.3 Stay + Transport

AI may:

- explain why a stay area fits
- explain transport tradeoffs
- explain accommodation ranking
- summarize provider coverage limitations

AI must not:

- invent accommodation options
- invent prices
- invent availability
- invent ratings
- claim a provider was searched when it was not connected
- label an area as safe or unsafe without authoritative data

Accommodation ranking should use provider/open-data facts and deterministic scoring.

AI may explain the ranking, but should not create unsupported options.

---

## 9.4 Experience Planner

AI may:

- explain why selected experiences fit
- help balance itinerary themes
- write day summaries
- explain why a day order makes sense
- generate experience card wording

AI must not:

- invent attractions
- invent restaurants
- invent opening hours
- invent ratings
- invent prices
- invent exact durations as facts
- invent route times
- invent walking distances

If restaurant data is unavailable, the planner should use meal areas instead of fake restaurant names.

---

## 9.5 Plan Validator

AI may:

- reason about subjective trip quality
- identify fatigue risk
- identify repetition
- evaluate whether the plan matches traveler intent
- explain why a warning matters

AI must not:

- modify the itinerary
- invent validation facts
- invent route problems
- invent closure issues
- invent safety ratings
- override deterministic validator results

Deterministic validation should run before AI validation.

The AI validator may only reason from:

- Traveler Profile
- Trip Strategy
- Stay + Transport
- Experience Plan
- provider data
- open data
- provider coverage
- deterministic validation results

---

## 9.6 Feedback Pipeline

AI may:

- interpret feedback
- classify feedback type
- identify affected stages
- summarize requested changes
- explain what changed
- explain what stayed the same

AI must not:

- regenerate unrelated sections
- ignore user locks
- remove must-visit items silently
- invent replacement options
- claim unavailable provider data exists

If feedback is vague, the system should ask a follow-up question.

Example:

```text
I want it better.
```

Should produce:

```text
Ask follow-up question.
```

Not:

```text
Regenerate entire itinerary.
```

---

## 10. Structured Output Requirement

All AI outputs should be structured JSON.

The backend should validate AI outputs against expected schemas.

Invalid outputs should be rejected.

Examples of invalid AI output:

- missing required fields
- malformed JSON
- unsupported enum values
- facts not present in inputs
- unsupported provider claims
- invented options
- confidence too high despite missing data

---

## 11. Confidence Rules

The LLM should return a confidence score.

Confidence should be reduced when:

- user input is vague
- provider data is missing
- only open data is available
- route data is unavailable
- restaurant ratings are unavailable
- accommodation prices are unavailable
- opening hours are unavailable
- assumptions are required

The LLM should not return high confidence when important data is unavailable.

---

## 12. Unavailable Data Handling

If data is unavailable, the AI should explicitly mention it.

Example:

```json
{
  "message": "Live accommodation prices are unavailable because no approved accommodation price provider is connected.",
  "source_type": "unavailable_data",
  "confidence": 0.0
}
```

The AI should not fill missing data with likely values.

---

## 13. AI Retry Policy

If an AI output fails validation, the backend may retry.

Retry should include:

- the schema error
- the invalid field
- a reminder to use only provided data
- a reminder to return valid JSON

Maximum retries should be limited.

Suggested policy:

```text
max_ai_retries = 2
```

If retry fails, return a controlled error or lower-confidence partial result.

---

## 14. AI Hallucination Checks

Before accepting AI output, the backend should check:

- Are all referenced places present in provider/open-data input?
- Are all restaurants present in provider/open-data input?
- Are all accommodations present in provider/open-data input?
- Are all prices present in provider data or marked unavailable?
- Are all ratings present in provider data or marked unavailable?
- Are all route times present in routing data or marked unavailable?
- Are all safety claims phrased as planning considerations?
- Are all provider coverage claims consistent with Planning State?

If not, reject the AI output.

---

## 15. Explanation Card Generation

AI can generate explanation card wording.

However, every card should include:

- title
- summary
- reasons
- tradeoffs
- alternatives when available
- confidence
- data_sources
- assumptions
- claim_sources

The frontend should be able to show why a recommendation exists and what data supports it.

---

## 16. Prompt Guardrails

Every prompt should include guardrails.

Required guardrail language:

```text
Use only the provided input data.
Do not invent factual travel data.
If a field is missing, mark it unavailable.
If a provider is not connected, do not imply it was searched.
Separate facts from reasoning.
Return valid JSON matching the schema.
```

---

## 17. Example Prompt Pattern

```text
You are the reasoning layer for TravelObligator.

Your task is to generate a trip strategy.

Use only:
- Traveler Profile
- Destination Context
- Provider Coverage
- Unavailable Data
- Explicit Assumptions

Do not:
- select final attractions
- invent destination facts
- invent restaurant names
- invent accommodation options
- invent prices
- invent route times
- claim unavailable providers were searched

Return JSON matching the schema.
```

---

## 18. Example Safe Output

```json
{
  "recommended_trip_style": "relaxed cultural and food-focused trip",
  "planning_strategy": [
    "Keep mornings for major sightseeing.",
    "Reserve meal breaks near planned activity clusters.",
    "Avoid late-night movement because the traveler prefers comfort and lower-friction planning."
  ],
  "assumptions": [
    "Restaurant ratings are unavailable from open data, so restaurant quality should not be ranked by rating unless a richer provider is connected."
  ],
  "confidence": 0.74,
  "claim_sources": [
    {
      "claim": "The traveler prefers a relaxed pace.",
      "source_type": "user_input",
      "source": "traveler_profile"
    },
    {
      "claim": "Restaurant ratings are unavailable.",
      "source_type": "unavailable_data",
      "source": "provider_coverage"
    }
  ]
}
```

---

## 19. Example Unsafe Output

```json
{
  "restaurant_name": "Best Bistro DC",
  "rating": 4.8,
  "reason": "This is one of the top restaurants in the city."
}
```

Why this is unsafe:

- restaurant was not provided by a legitimate source
- rating was invented
- “top restaurant” claim is unsupported
- no data source is provided

This output should be rejected.

---

## 20. AI and Provider Coverage

AI should be allowed to explain provider coverage.

Example:

```text
Accommodation results are based on OpenStreetMap accommodation locations. Live prices and availability are unavailable because no approved accommodation pricing provider is connected.
```

AI should not say:

```text
We searched Airbnb and Booking.com.
```

unless those providers are officially connected.

---

## 21. AI and Safety

AI should not generate direct safety ratings.

Allowed:

```text
This route may be less comfortable because it includes late-night walking and limited transit alignment.
```

Not allowed:

```text
This neighborhood is unsafe.
```

unless supported by authoritative data and the product is explicitly designed to handle that responsibly.

For MVP, use safety-related planning considerations only.

---

## 22. AI and Estimates

AI may help explain estimates, but should not convert estimates into facts.

Example:

```json
{
  "estimated_visit_duration_minutes": {
    "value": 75,
    "data_status": "estimated",
    "source": "system_default",
    "confidence": 0.65
  }
}
```

AI should not say:

```text
The attraction takes exactly 75 minutes.
```

It should say:

```text
The plan uses an estimated 75-minute visit duration.
```

---

## 23. AI Output Acceptance Rules

An AI output can be accepted only if:

- it matches the schema
- it uses allowed enum values
- it does not invent factual data
- it references only provided places, routes, restaurants, accommodations, or flights
- it marks missing fields unavailable
- it includes confidence
- it includes assumptions when needed
- it includes claim sources for important explanation claims

---

## 24. AI Output Rejection Rules

Reject AI output if it:

- invents a factual entity
- invents a price
- invents a rating
- invents availability
- invents a route time
- invents a safety rating
- ignores provider coverage
- claims a restricted provider was searched
- modifies unrelated sections
- violates user locks
- returns invalid JSON
- returns unsupported field values

---

## 25. Design Principles

The LLM reasoning pipeline should follow these principles:

- AI is a reasoning layer, not a data source.
- AI should use structured outputs.
- AI should be schema-validated.
- AI should never invent provider-backed facts.
- AI should expose assumptions.
- AI should expose unavailable data.
- AI should distinguish facts from reasoning.
- AI should respect provider coverage.
- AI should respect user locks.
- AI should reduce confidence when data is missing.
- AI should support explainability, not replace provider data.

---

## 26. AI Reasoning Contract Models (Step 155A)

`backend/app/models/ai_reasoning.py` defines the AI reasoning contract as
Pydantic models. This is a contract only:

- No LLM provider is connected yet.
- No LangGraph or LangSmith dependency has been added yet.
- Nothing in the app currently constructs or consumes these models.

Once a real AI reasoning provider is connected, its output must validate
through `AIReasoningResult` (or one of the task-specific result models)
before it can be accepted. The models enforce the rules already described
above in code:

- AI reasoning may explain and interpret existing PlanningState data, but
  must never create a travel fact -- `summary`/`reasoning` are rejected if
  they contain an obviously fabricated claim (rating, price, opening
  hours, booking/reservation link, route time, safety score, currency
  amount, or superlative marketing language).
- Every `EvidenceRef` must point back at a section/field that already
  exists in PlanningState; it can never introduce new data on its own.
- Missing provider fields must stay represented as
  `UnavailableConstraint` entries, not silently dropped or guessed at.
- A `completed` result requires real evidence and a passed guardrail
  check; a `rejected` result requires an explicit reason; a
  `not_connected` result always carries zero confidence.

---

## 27. AI Reasoning Contract Builder (Step 155B)

`backend/app/services/ai_reasoning_contract_builder.py` defines
`AIReasoningContractBuilder`, which converts existing `PlanningState`
metadata into `AIReasoningRequest` inputs. It does not call an LLM.

- It passes section/field references (`EvidenceRef`) and missing-data
  constraints (`UnavailableConstraint`) built from data that already
  exists on `PlanningState` -- never a full, unrestricted `PlanningState`
  dump and never a serialized prompt.
- `unavailable_constraints_from_state` merges `PlanningState.
  unavailable_data` and every `provider_status` entry's
  `unavailable_fields` into one deterministic, deduplicated list.
- `evidence_refs_for_task` only adds a reference when the underlying
  section/field is actually populated -- it never claims data is
  available when it isn't.
- `build_request` only assembles these into an `AIReasoningRequest`; it
  never reads/writes a provider, never mutates `PlanningState`, and relies
  on `AIReasoningRequest`'s own validation (e.g. non-empty
  `input_sections`) rather than duplicating those checks.

---

## 28. AI Candidate Proposal Contract Models (Step 157A)

`backend/app/models/ai_candidate_proposal.py` defines the AI-assisted
candidate discovery contract described in
`itinerary-generator-build-spec.md` Stage 5 (LLM Candidate Proposal), as
Pydantic models. This is a contract only:

- No LLM provider is connected yet.
- No LangGraph or LangSmith dependency has been added yet.
- Nothing in the app currently constructs or consumes these models, and
  nothing is wired into the generation pipeline.

The build spec's core principle applies directly here: **an AI candidate
proposal is not a fact.** `AICandidateProposal` may only carry a candidate
name, type, suggested area, and a one-line rationale (`why_consider`) --
it deliberately has no coordinate, provider-source, rating, price,
opening-hours, booking/availability, or route/timing field, so it can
never be mistaken for a grounded, schedulable place. Every proposal also
carries `verification_requirements` (e.g.
`must_ground_by_name_and_location`, `must_reject_if_not_found`) spelling
out what Stage 6 (Grounding & Verification) must still do before the idea
can be scheduled.

Key validation rules enforced in code:

- `candidate_name`, `suggested_area`, `why_consider`, and every entry of
  `fit_with_user_preferences` are rejected if they contain an obviously
  fabricated claim (rating, price, opening hours, route time, booking URL,
  review count, ticket price, availability, safety score, or superlative/
  marketing language implying a verified fact).
- `verification_requirements` must not be empty -- a proposal with no
  grounding requirement is invalid by construction.
- `AICandidateProposalResult.status="completed"` requires at least one
  proposal and a passed guardrail check; `"rejected"` requires a failed
  guardrail check with an explicit reason; `"not_connected"` and
  `"skipped"` always carry an empty proposal list, and `"not_connected"`
  always carries zero confidence.
- `AICandidateProposalBatch` requires `result.task` to match
  `request.task` when a result is present.

Once a real AI candidate proposal provider is connected, its output must
validate through `AICandidateProposalResult` before it can be accepted,
and every resulting `AICandidateProposal` must still pass through Stage 6
grounding/verification against provider/open data before it can appear in
`candidate_pool.grounded` or be scheduled.

---

## 29. AI Candidate Proposal Provider Boundary (Step 157B)

`backend/app/providers/ai_candidate_proposal/` adds the provider boundary
for the Step 157A contract models, following the same
provider-boundary pattern used everywhere else in the app: planning
services never call an LLM directly, only through a typed provider
interface (docs/14_backend_architecture.md sections 18 and 25). Still no
LLM, LangGraph, or LangSmith dependency is wired up.

- `AICandidateProposalProvider` (`base.py`) is an `abc.ABC` with one
  abstract method, `propose(request: AICandidateProposalRequest) ->
  AICandidateProposalResult`. Every future adapter (LLM-backed or
  otherwise) must implement this method explicitly -- there is no default
  implementation to silently fall back on.
- `NotConnectedAICandidateProposalProvider` (`not_connected_adapter.py`)
  is the default adapter. `propose` never calls a network service and
  never inspects `request` beyond `request.task` (which it preserves on
  the response); it always returns an honest
  `AICandidateProposalResult` with `status=not_connected`, an empty
  `proposals` list, `confidence=0.0`, and a `guardrail_report` explaining
  that no AI candidate proposal provider is connected yet.
- This is still not wired into `PlanningOrchestrator` or any stage
  service. Nothing in the generation pipeline constructs or calls
  `AICandidateProposalProvider` yet -- Stage 5 (LLM Candidate Proposal)
  remains unimplemented at runtime, and `destination_context.
  candidate_pool.llm_proposed` is not populated by this step.

---

## 30. Candidate Grounding Contract Models (Step 158A)

`backend/app/models/candidate_grounding.py` defines the contract for build
spec Stage 6, "Grounding & Verification (the trust firewall)" -- the step
that decides whether a Step 157A `AICandidateProposal` is real. This is a
contract only:

- No LLM, provider call, LangGraph, or LangSmith dependency is wired up
  yet.
- Nothing in this module calls a network service, and nothing mutates
  `PlanningState`.
- Nothing elsewhere in the app currently constructs or consumes these
  models, and none of it is wired into `PlanningOrchestrator`,
  scheduling, validation, or regeneration.

**Raw AI proposals are not facts.** An `AICandidateProposal` is only a
name and a one-line rationale (Step 157A) -- it carries no coordinate, no
provider source, no confirmed category. `candidate_grounding.py` defines
what happens next: every proposal must be independently matched against
real provider/open-data evidence before it can be treated as a real
place.

- `CandidateGroundingEvidence` is the real provider/open-data fact that
  grounds one proposal -- `coordinates` and `data_status` are required,
  typed, provider-backed fields, never something free text can assert on
  its own.
- `GroundedCandidate` pairs a proposal with its `CandidateGroundingEvidence`
  and a `confidence_tier`/`confidence`. It is schedulable **only** because
  its coordinates come from real evidence, not from the AI's own wording
  -- unlike `AICandidateProposal`, it is allowed to carry a coordinate for
  exactly that reason. Being grounded is not the same as being scheduled;
  future scheduling/quality-scoring stages (7-8) still decide that.
- `RejectedCandidateProposal` records a proposal that could not be
  grounded, with a `CandidateGroundingRejectReason` and a plain-language
  `message` -- deliberately carrying no coordinate or provider field, so a
  rejected idea never looks like a real place. This is meant to later feed
  the frontend's "what the AI suggested but we didn't use" trust UI (build
  spec Stage 11), the same way `candidate_pool.rejected` does in the
  build spec's `PlanningState` sketch.
- `CandidateGroundingResult.status` follows the same
  `not_connected`/`skipped`/`completed`/`rejected` shape as the other
  contract results, plus `partial` for the common real-world case where
  some proposals ground and others don't: `partial` requires at least one
  `GroundedCandidate` *and* at least one `RejectedCandidateProposal`.
- `CandidateGroundingBatch` keeps a result honestly tied to the request it
  was run against: every `proposal_id` in `grounded_candidates`/
  `rejected_proposals` must trace back to a proposal actually in
  `request.proposals`, no `proposal_id` may appear in both lists or be
  duplicated within either list, and a `completed`/`partial` result must
  account for every request proposal -- nothing can be silently dropped.

This is still not wired into runtime. Once a real grounding step is
connected, its output must validate through `CandidateGroundingResult`
before any `GroundedCandidate` can be considered for future scheduling.

---

## 31. CandidateGroundingService NotConnected Skeleton (Step 158B)

`backend/app/services/candidate_grounding_service.py` defines
`CandidateGroundingService`, the service boundary for the Step 158A
contract models. This step adds the boundary only -- it does not
implement real grounding:

- `CandidateGroundingService.ground(request: CandidateGroundingRequest) ->
  CandidateGroundingResult` always returns a
  `status=not_connected` result -- empty `grounded_candidates`, empty
  `rejected_proposals`, `confidence=0.0`, and a `guardrail_report`
  explaining that no candidate grounding service is connected yet.
- It does not inspect `request.proposals` and does not attempt any name,
  location, or category matching. Passing proposals in the request has no
  effect on the output.
- No provider or open-data lookup is performed. No LLM, LangGraph, or
  LangSmith dependency is wired up.
- No `GroundedCandidate` can be produced by this step -- there is no
  matching logic to produce one from.
- This is still not wired into `PlanningOrchestrator`, scheduling,
  validation, or regeneration. Nothing in the generation pipeline
  constructs or calls `CandidateGroundingService` yet -- Stage 6
  (Grounding & Verification) remains unimplemented at runtime.

This service exists only so a future pipeline has a safe, honest boundary
to call before real grounding logic (fuzzy-match against provider data,
confidence tiering, ambiguity handling) is implemented.

---

## 32. Deterministic Supplied-Candidate Grounding (Step 159A)

`CandidateGroundingService.ground` now grounds `AICandidateProposal` ideas,
but only against `ProviderCandidateForGrounding` entries explicitly
supplied on `CandidateGroundingRequest.provider_candidates` -- the caller
must pass in the provider/open-data candidates to check against. This step
still does not perform any provider/open-data lookup or LLM call of its
own; it has no knowledge of any candidate that wasn't handed to it in the
request.

- `ProviderCandidateForGrounding` (Step 159A,
  `backend/app/models/candidate_grounding.py`) is the shape of one
  supplied provider/open-data candidate: `provider_name`,
  `provider_place_id`, `name`, an optional `category`, a required
  `coordinates` (`GeoPoint`), `data_status`, and `confidence`. Like every
  other model in this module, its text fields reject forbidden
  factual-claim patterns.
- Matching is deterministic and conservative: exact case-insensitive name
  match, or normalized name match (lowercase, punctuation stripped,
  whitespace collapsed, one leading article removed). No fuzzy matching
  and no substring matching are implemented at this step.
- A proposal grounds into a `GroundedCandidate` only if **exactly one**
  supplied provider candidate matches its name after normalization. Zero
  matches or more than one match both fail to ground the proposal.
- Unmatched and ambiguous proposals become a `RejectedCandidateProposal`
  instead of being silently dropped: `NO_PROVIDER_MATCH` for zero matches,
  `AMBIGUOUS_MATCH` for more than one match.
- `GroundedCandidate.evidence` is built entirely from the matched
  `ProviderCandidateForGrounding` fields (`provider_name`,
  `provider_place_id`, `coordinates`, `data_status`, category, confidence)
  -- never from the AI proposal's own wording. `GroundedCandidate.confidence`
  is `min(proposal.confidence, provider_candidate.confidence)`, and
  `confidence_tier` is `high` only for an exact match with provider
  confidence >= 0.75, `medium` for a normalized match or provider
  confidence >= 0.5, and `low` otherwise.
- `CandidateGroundingRequest.proposals` empty still returns `skipped`;
  proposals present but `provider_candidates` empty still returns
  `not_connected`. With both present, the result is `completed` (every
  proposal grounded), `partial` (a mix), or `rejected` (none grounded).

This is still not wired into `PlanningOrchestrator`, scheduling,
validation, or regeneration. Nothing in the generation pipeline constructs
or calls `CandidateGroundingService` yet -- callers must supply
`provider_candidates` themselves; this step does not fetch them.

---

## 33. CandidateGroundingRequestBuilder (Step 159B)

`backend/app/services/candidate_grounding_request_builder.py` defines
`CandidateGroundingRequestBuilder`, which answers the "callers must supply
`provider_candidates` themselves" gap left by Step 159A: it converts
provider/open-data candidates that already exist in
`PlanningState.destination_context` into `ProviderCandidateForGrounding`
entries, then assembles a `CandidateGroundingRequest` from them plus
caller-supplied `AICandidateProposal` objects.

- `build_request(planning_state, proposals) -> CandidateGroundingRequest`
  reads `planning_state.trip_id`, `planning_state.trip_request.
  primary_destination`, and `planning_state.destination_context.
  candidate_pois` / `candidate_restaurants` / `candidate_accommodation_pois`
  -- nothing else. It performs **no provider/open-data lookup and no LLM
  call** of its own; every `ProviderCandidateForGrounding` it produces
  traces back to a candidate dict already stored on `PlanningState`.
- A source candidate is converted only if it already has a non-blank name
  and usable coordinates; candidates missing either are skipped, never
  invented. `category` falls back to the source collection's default
  (`attraction`/`restaurant`/`accommodation`) only when the candidate has
  no existing category value.
- `provider_name` and `provider_place_id` prefer the candidate's own
  existing provider/source/id fields. When those are absent, the builder
  falls back to internal references only: `provider_name` becomes the
  literal label `"destination_context"`, and `provider_place_id` becomes a
  deterministic string like `"destination_context.candidate_pois[0]"`.
  Both are references to an existing `PlanningState` record's position,
  **not a claim that a new provider was searched or a new place ID was
  issued**.
- `data_status` and `confidence` prefer the candidate's own existing
  values. If a candidate dict has no explicit `data_status` (real
  candidates produced by `DestinationContextService` always do, since
  `NormalizedPlace.data_status` is required), the builder falls back to
  `DataStatus.UNAVAILABLE` rather than assuming `live` freshness it cannot
  confirm. If `confidence` is missing, it falls back to a deterministic
  `0.5` -- documented as a builder default for existing provider/open-data
  candidates, never a factual quality claim about the place itself.
- Candidates are deduplicated deterministically by (normalized
  `provider_name`, `provider_place_id`, normalized `name`, `coordinates`),
  preserving first-occurrence order. `provider_candidate_summary` counts
  only the candidates actually included, by source collection
  (`attraction`/`restaurant`/`accommodation`).
- `unavailable_data` on the request carries over the `field` names from
  `planning_state.unavailable_data`, deduplicated; it stays empty if none
  exist. `proposals` are included exactly as passed in, unchanged.
- The builder never calls `CandidateGroundingService.ground`, never
  creates a `GroundedCandidate` or `RejectedCandidateProposal`, and never
  mutates `PlanningState` -- it only reads it and returns a new request
  object.

This is still not wired into `PlanningOrchestrator`, scheduling,
validation, or regeneration. Nothing in the generation pipeline calls
`CandidateGroundingRequestBuilder` yet.

---

## 34. AICandidateProposalRequestBuilder (Step 160A)

`backend/app/services/ai_candidate_proposal_request_builder.py` defines
`AICandidateProposalRequestBuilder`, which prepares the future LLM
candidate proposal input package (Step 157A's `AICandidateProposalRequest`,
itinerary-generator-build-spec.md Stage 5) from existing `PlanningState`
data. This is still pre-LLM: it performs no LLM call, no provider call, no
proposal generation, no grounding, no scheduling, no validation, and no
orchestration wiring.

- `build_request(planning_state, task, max_candidates) ->
  AICandidateProposalRequest` reads only safe, already-existing
  `PlanningState` fields: trip metadata (`trip_id`, `trip_request.
  primary_destination`, an inclusive `trip_duration_days` calculated the
  same way `TripStrategyService`/`ExperiencePlannerService` already do --
  `(end_date - start_date).days + 1`, floored at 1), explicit user
  preference fields, provider candidate counts, and unavailable-data field
  names.
- `interests`/`must_visit`/`constraints` are read only from fields the
  user (or a prior deterministic stage) already populated on
  `trip_request` and, if it exists, `traveler_profile` -- concatenated
  (`trip_request` first, then `traveler_profile`) and deduplicated while
  preserving first-occurrence order. Nothing is inferred, and no default
  interest/must-visit/constraint (e.g. "museums", "food") is ever added
  when the field is genuinely empty.
- `provider_candidate_summary` is built entirely from
  `destination_context` candidate **counts** -- `len(candidate_pois)`,
  `len(candidate_restaurants)`, `len(candidate_accommodation_pois)` --
  never candidate names or raw place data, matching
  `AICandidateProposalRequest`'s own contract (section 28): the LLM is
  told what's already covered, not fed raw provider data to restate. If
  `destination_context` doesn't exist yet, every count is honestly `0`
  rather than omitted.
- `unavailable_data` carries over `field` names from `planning_state.
  unavailable_data`, then every `provider_status` entry's
  `unavailable_fields`, deduplicated while preserving first-occurrence
  order -- the same field-name-only shape `CandidateGroundingRequestBuilder`
  already uses (section 33), extended to also read `provider_status` since
  that data is just as easy to read here.
- `max_candidates` is passed straight through to `AICandidateProposalRequest`,
  which enforces its own `1..25` bound -- the builder does not duplicate
  that validation.
- The builder never calls `CandidateGroundingRequestBuilder`'s
  candidate-conversion logic (it builds its own local counts instead),
  never calls `CandidateGroundingService`, never calls
  `AICandidateProposalProvider`/`NotConnectedAICandidateProposalProvider`,
  never creates an `AICandidateProposal`, `GroundedCandidate`, or
  `RejectedCandidateProposal`, and never mutates `PlanningState`.

This is still not wired into `PlanningOrchestrator`, scheduling,
validation, or regeneration. Nothing in the generation pipeline calls
`AICandidateProposalRequestBuilder` yet.

---

## 35. AI Candidate Discovery Dry-Run Service (Step 160B)

`backend/app/services/ai_candidate_discovery_service.py` defines
`AICandidateDiscoveryService`, which composes the four safe pieces built in
Steps 157B/159A/159B/160A into one deterministic dry-run call of the
future candidate-discovery flow (itinerary-generator-build-spec.md Stages
5-6):

```text
AICandidateProposalRequestBuilder -> proposal provider
  -> CandidateGroundingRequestBuilder -> CandidateGroundingService
```

`dry_run(planning_state, task, max_candidates) ->
AICandidateDiscoveryDryRunResult` runs exactly those four steps in order
and returns all four intermediate objects (`proposal_request`,
`proposal_result`, `grounding_request`, `grounding_result`) bundled
together, each still validating through its own existing contract model.

- The default `proposal_provider` is `NotConnectedAICandidateProposalProvider`
  (Step 157B) -- it never calls a network service and always returns an
  honest `not_connected` result with an empty `proposals` list. Because
  `CandidateGroundingService.ground` returns `skipped` whenever `proposals`
  is empty (Step 159A), the **default** `dry_run` call therefore always
  produces `proposal_result.status=not_connected`,
  `proposal_result.proposals=[]`, `grounding_request.proposals=[]`, and
  `grounding_result.status=skipped` with no grounded or rejected
  candidates -- even when `planning_state.destination_context` has real
  provider candidates. This module never fabricates a fallback proposal or
  grounded candidate to compensate for the provider not being connected.
- `grounding_request.provider_candidates` is still built from
  `planning_state.destination_context` regardless of whether any proposals
  exist, since `CandidateGroundingRequestBuilder` (Step 159B) reads that
  independently of `proposals`.
- Every dependency (`proposal_request_builder`, `proposal_provider`,
  `grounding_request_builder`, `grounding_service`) can be injected via the
  constructor. Tests can supply a deterministic fake
  `AICandidateProposalProvider` to exercise the full composition path end
  to end (e.g. a fake proposal that exactly matches a supplied provider
  candidate produces a `completed` `grounding_result`, and one that
  doesn't produces a `rejected` `grounding_result`) -- this proves the
  wiring works without adding any real LLM/provider call to the runtime
  default.
- `dry_run` never mutates `planning_state`, never persists anything, and
  never schedules anything. It is not called by `PlanningOrchestrator`,
  and it does not affect scheduling, validation, regeneration, or the
  frontend.

---

## 36. PlanningState Storage Fields for AI Candidate Discovery (Step 160C)

`backend/app/models/planning_state.py` now defines two optional storage
fields on `PlanningState` that give a future runtime/shadow-mode
integration a validated place to persist what Steps 157A-160B's
candidate-discovery flow produced:

```python
ai_candidate_proposal_batch: AICandidateProposalBatch | None = None
candidate_grounding_batch: CandidateGroundingBatch | None = None
```

This is storage-contract only:

- Both fields reuse the existing, already-validated batch models
  (`AICandidateProposalBatch` from Step 157A, `CandidateGroundingBatch`
  from Step 158A) -- no new model, no new validation rule, and no field
  that could hold raw prompt text, raw/unvalidated LLM output, or an
  unvalidated provider response. Each batch still enforces its own
  existing invariants (e.g. a `completed`/`partial`
  `CandidateGroundingResult` must account for every proposal in its
  paired request) exactly as it did before this step.
- Both fields default to `None` and every existing `PlanningState`
  construction, API response, and persistence round-trip continues to
  work unchanged -- a `PlanningState` built before this step (or missing
  these keys entirely in an older persisted JSON record) still loads
  correctly, with both fields honestly defaulting to `None` rather than
  fabricating a batch.
- **Nothing populates these fields yet.** `PlanningOrchestrator`, every
  stage service, and `AICandidateDiscoveryService` (Step 160B) are
  unchanged by this step -- `generate_full_plan` still leaves both fields
  `None` on every generated `PlanningState`, exactly as before. This
  prepares the storage shape for a future shadow-mode integration (running
  `AICandidateDiscoveryService.dry_run` alongside generation and recording
  its result here for inspection, without it affecting the plan) -- it
  does not itself wire that integration in, and it does not affect
  scheduling, validation, or regeneration.

---

## 37. AI Candidate Discovery Safety End-to-End Tests (Step 160D)

`backend/app/tests/services/test_ai_candidate_discovery_safety.py` adds
safety end-to-end tests for the full candidate-discovery composition
(Steps 157A-160C) **before any real LLM-backed adapter is connected**.
This step is test-only -- it adds no production code, because none of
these tests uncovered a real safety gap in the existing models/services.
Every scenario uses only in-file deterministic fake
`AICandidateProposalProvider` test doubles; no real LLM, LangGraph,
LangSmith, or provider adapter is called.

- **Unsafe AI-like output fails schema validation before grounding.** A
  fake provider that tries to construct an `AICandidateProposal` with a
  forbidden factual claim (rating, price, opening hours, route time,
  booking URL, review count, ticket price, availability, safety score,
  "book now", "highly rated", the "guaran" + "teed" pattern, or "exact
  travel time") in `candidate_name`, `suggested_area`,
  `why_consider`, or `fit_with_user_preferences` raises
  `pydantic.ValidationError` while building the proposal object itself --
  it can never reach `AICandidateDiscoveryService.dry_run`'s grounding
  step. A patched `CandidateGroundingService.ground` spy confirms it is
  never called in any of these cases. The same is true for structurally
  invalid `AICandidateProposalResult` output (empty
  `verification_requirements`, a blank `candidate_name`, a `completed`
  result with no proposals or a failed guardrail, a `not_connected` result
  that still carries proposals, or a request/result task mismatch on
  `AICandidateProposalBatch`) -- all rejected by the existing Step 157A
  model validators before grounding runs.
- **Valid but unsupported proposals become `RejectedCandidateProposal`
  through grounding, never silently dropped.** A schema-valid proposal
  naming a place with no matching supplied provider candidate produces
  `grounding_result.status=rejected` with a `NO_PROVIDER_MATCH` reason. A
  schema-valid proposal whose name matches two supplied provider
  candidates (same normalized name, different `provider_place_id`/
  coordinates) produces `rejected` with an `AMBIGUOUS_MATCH` reason. In
  both cases, `grounded_candidates` stays empty.
- **Matching proposals ground only from supplied provider/open-data
  evidence.** A schema-valid proposal that matches exactly one supplied
  `destination_context` candidate produces a `completed` result whose
  `GroundedCandidate.evidence` (`provider_name`, `provider_place_id`,
  `matched_name`, `data_status`, `coordinates`) traces back verbatim to
  that supplied candidate -- confirmed field-by-field, plus a direct check
  that `AICandidateProposal` itself has no `coordinates`/`provider_name`/
  `provider_place_id` fields at all, so no such evidence could ever have
  come from the proposal's own wording.
- **`dry_run` remains storage-neutral and runtime-neutral.** It never
  populates `planning_state.ai_candidate_proposal_batch` or
  `.candidate_grounding_batch` (Step 160C), never mutates `planning_state`
  otherwise, and a patched `PlanningStateRepository.save` on the real
  singleton instance confirms `dry_run` never reaches persistence at all
  (its constructor also takes no repository dependency). Separate
  import-based checks confirm `PlanningOrchestrator`, `app/api/routes/
  trips.py`, `ExperiencePlannerService`, `PlanValidatorService`, and the
  regeneration/feedback/versioning service modules still do not import
  `AICandidateDiscoveryService`.

This step is a precondition, not an integration: it hardens confidence in
the existing contract-only pipeline before a real LLM-backed
`AICandidateProposalProvider` adapter is ever connected. It does not wire
`AICandidateDiscoveryService` into `PlanningOrchestrator`, change
scheduling, validation, or regeneration, or touch the frontend.

---

## 38. Config-Gated AI Candidate Proposal Provider Factory (Step 160E)

`backend/app/providers/ai_candidate_proposal/factory.py` defines
`get_ai_candidate_proposal_provider(provider_name: str | None = None) ->
AICandidateProposalProvider`, a provider-selection boundary so a future
real LLM-backed adapter can be config-gated in later without changing any
calling code. **This step still does not add a real LLM adapter** -- no
LangGraph, LangSmith, or OpenAI/Anthropic/Gemini client code is added
here.

- `"not_connected"` (mapping to the Step 157B
  `NotConnectedAICandidateProposalProvider`) is the only supported
  provider name today.
- When `provider_name` is omitted, the factory reads
  `Settings.ai_candidate_proposal_provider` (`AI_CANDIDATE_PROPOSAL_PROVIDER`
  env var), which defaults to `"not_connected"`.
- **An unsupported/unrecognized provider name can never silently create
  fake proposals.** The factory falls back to the same honest
  `NotConnectedAICandidateProposalProvider` used when nothing is
  configured, rather than raising or guessing -- unknown configuration is
  treated as functionally identical to "not connected."
- `AICandidateDiscoveryService` (Step 160B) now resolves its default
  `proposal_provider` through this factory instead of constructing
  `NotConnectedAICandidateProposalProvider` directly. Dependency injection
  is unchanged: an explicitly passed `proposal_provider` still bypasses
  the factory entirely, and the default `dry_run` behavior (`not_connected`
  proposal result, empty proposals, `skipped` grounding result) is
  unaffected by this change.
- This is still not wired into `PlanningOrchestrator`, scheduling,
  validation, or regeneration, and no provider adapter (places, routes,
  weather, holidays, currency) is imported by the factory.

---

## 39. Anthropic (Claude) AI Candidate Proposal Provider Adapter (Step 161A)

**Claude/Anthropic is the selected LLM base for AI candidate proposals.**
`backend/app/providers/ai_candidate_proposal/anthropic_adapter.py` defines
`AnthropicAICandidateProposalProvider`, the first real (non-`not_connected`)
`AICandidateProposalProvider` implementation. It is still not wired into
`PlanningOrchestrator`, scheduling, validation, regeneration, or normal
trip generation -- it is only reachable by explicitly injecting it or by
setting `AI_CANDIDATE_PROPOSAL_PROVIDER=anthropic`.

- **Uses the Anthropic API boundary, not the Claude Code CLI.** The
  adapter calls Claude through the official `anthropic` Python SDK's
  Messages API (`client.messages.create`) -- never by shelling out to a
  local coding-agent CLI or any other runtime dependency on Claude Code.
- **Default app behavior remains `not_connected`** unless both
  `AI_CANDIDATE_PROPOSAL_PROVIDER=anthropic` (Step 160E's factory gate)
  and a real `ANTHROPIC_API_KEY` are configured. With no API key (the
  default), `propose` returns an honest `not_connected` result -- it
  never calls the network and never crashes the app or the test suite.
- **The `anthropic` package import is deferred**, kept inside a
  `_build_client` helper rather than a module-level import, so the rest
  of the app -- and every test that injects a fake client or exercises
  the no-key path -- keeps working whether or not the package is
  installed.
- **Structured output via forced tool use.** The adapter defines one tool,
  `submit_ai_candidate_proposals`, whose JSON schema mirrors
  `AICandidateProposal` field-for-field (`proposal_id`, `candidate_name`,
  `candidate_type`, `priority_hint`, `suggested_area`, `why_consider`,
  `fit_with_user_preferences`, `verification_requirements`, `confidence`)
  and deliberately has no coordinate, provider-id, price, rating,
  opening-hours, route-time, review-count, ticket-price, availability,
  booking-link, or safety-score field. `tool_choice={"type": "tool",
  "name": "submit_ai_candidate_proposals"}` forces Claude to respond
  through that schema. The system/user prompt explicitly instructs Claude
  that every idea is a proposal, not a fact, and that verification
  requirements are still needed before any idea can be used.
- **This adapter only ever creates `AICandidateProposal` objects, which
  are not facts.** Every parsed proposal still has to pass
  `AICandidateProposal`'s own validation (forbidden-claim text patterns,
  non-blank fields, non-empty `verification_requirements`) before it can
  appear in a `completed` result -- a proposal that fails validation, or a
  tool-use response Claude never actually produced, never becomes a
  `completed` result.
- **All outputs validate through `AICandidateProposalResult`.** Parsing
  and validating Claude's tool output happens before any status is
  returned:
  - Missing package, missing/unset API key, or client-construction
    failure -> `not_connected` (no proposals, zero confidence, failed
    guardrail explaining why).
  - The API call itself raising, no usable `tool_use` block in the
    response, or the parsed tool input failing
    `AICandidateProposal`/`AICandidateProposalResult` validation ->
    `rejected` (no proposals, failed guardrail explaining why).
  - A valid, schema-conforming tool response -> `completed`, with
    validated proposals, a passed guardrail, `provider_name`, and
    `model_name`.
  - No case ever fabricates a proposal to compensate for a failure.
- **Proposals still require grounding through `CandidateGroundingService`
  before scheduling.** This adapter never creates a `GroundedCandidate` or
  `RejectedCandidateProposal` itself, never calls
  `CandidateGroundingService`, never calls a provider adapter, and never
  mutates `PlanningState` -- grounding against real supplied
  `destination_context` evidence (Step 159A) remains the only path from a
  proposal to something that could ever be scheduled.
- **Factory support.** `get_ai_candidate_proposal_provider` (Step 160E)
  now supports `"anthropic"` -> `AnthropicAICandidateProposalProvider()`
  alongside `"not_connected"`. The default remains `"not_connected"`, and
  an unsupported/unrecognized config value still falls back to
  `NotConnectedAICandidateProposalProvider` -- never to this adapter, and
  never to a fabricated result.
- **No orchestration/runtime generation wiring.** Tests prove the full
  composition path works end to end (an injected fake Anthropic client ->
  a validated `AICandidateProposal` -> `CandidateGroundingService.ground`
  producing a `completed` result against a matching supplied
  `destination_context` candidate) using only in-file fake clients -- no
  network call is made, and no real `ANTHROPIC_API_KEY` is required for
  the test suite. `AICandidateDiscoveryService`'s default `dry_run` call
  is unaffected and still returns `not_connected`/`skipped`.

---

## 40. Config-Gated AI Candidate Discovery Shadow Mode (Step 161B)

Step 161B gives `AICandidateDiscoveryService.dry_run` (Steps 157A-160E,
safety-hardened in Step 160D, extended with a real Claude/Anthropic-backed
adapter in Step 161A) its first runtime caller. `PlanningOrchestrator` now
defines a private helper,
`_run_ai_candidate_discovery_shadow_stage(planning_state: PlanningState) ->
PlanningState`, called once per generation after the destination context
stage runs.

- **Disabled by default.** The helper is gated by
  `Settings.ai_candidate_discovery_shadow_mode_enabled`
  (`AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED`, default `False`). When
  disabled -- the default for every existing deployment and test -- the
  helper returns `planning_state` completely untouched: `POST
  /trips/{trip_id}/generate` behaves exactly as it did before this step,
  and `planning_state.ai_candidate_proposal_batch` /
  `.candidate_grounding_batch` (Step 160C's storage fields) stay `None`.
- **What "enabled" actually does.** When the flag is on and
  `planning_state.destination_context` already exists, the helper calls
  `AICandidateDiscoveryService().dry_run(planning_state)` and stores the
  validated request/result pairs it returns:
  `AICandidateProposalBatch(request=result.proposal_request,
  result=result.proposal_result)` into `ai_candidate_proposal_batch`, and
  `CandidateGroundingBatch(request=result.grounding_request,
  result=result.grounding_result)` into `candidate_grounding_batch`. Both
  fields still validate through their existing Step 157A/158A contract
  models -- no new validation rule, no raw prompt text, no unvalidated
  provider response.
- **This is "shadow mode," not integration.** Storing these batches is
  purely for later inspection. The helper never mutates
  `destination_context.candidate_pois` / `candidate_restaurants` /
  `candidate_accommodation_pois`, never passes any `AICandidateProposal`
  or `GroundedCandidate` into `ExperiencePlannerService` (scheduling) or
  `CandidateQualityService` (pre-ranking), never changes
  `validation_report.readiness_status`, never affects
  `RegenerationReadinessService`/`PlanDiffPreviewService`, and never adds
  an AI-related name to `provider_coverage` or `data_sources_used`. A
  Claude/Anthropic-backed proposal (Step 161A) is exactly as unscheduled
  as a `not_connected` one -- every proposal, from any provider, still has
  to pass through `CandidateGroundingService` against real supplied
  `destination_context` evidence before it is even a `GroundedCandidate`,
  and nothing downstream of this helper consumes a `GroundedCandidate`
  yet.
- **Fails safe.** If `AICandidateDiscoveryService.dry_run` raises for any
  reason (a misbehaving injected provider, a network-level exception from
  a real adapter, anything), the helper catches it, stores nothing, and
  generation continues exactly as if shadow mode were disabled for that
  request -- it never crashes `POST /trips/{trip_id}/generate` and never
  fabricates a proposal or grounded candidate to compensate.
- **Test coverage
  (`backend/app/tests/services/test_ai_candidate_discovery_shadow_mode.py`)**
  proves, using only in-file deterministic fake proposal providers/discovery
  services (never a real network call, never a required
  `ANTHROPIC_API_KEY`): the config default is `False`; default generation
  leaves both batch fields `None`; enabling shadow mode with the default
  `not_connected` provider still succeeds and stores a `not_connected`
  proposal batch and a `skipped` grounding batch; running the same trip
  request through the full pipeline with shadow mode disabled vs. enabled
  produces an identical `experience_plan`, identical
  `validation_report.readiness_status`, and identical
  `destination_context` candidates; both batch fields round-trip through
  the real planning-state repository; an `AnthropicAICandidateProposalProvider`
  with no API key configured does not crash generation; an injected fake
  discovery service returning a `completed` proposal/grounding result still
  is not scheduled by `ExperiencePlannerService` and is not consumed by
  `CandidateQualityService`; a raising fake discovery service is swallowed
  fail-safe; and no forbidden factual field (price, rating, opening hours,
  route time, booking URL, review count, ticket price, availability, safety
  score) ever appears in a stored batch dump.
- **No frontend, scheduling, or regeneration change.** This step touches
  only `backend/app/core/config.py` and
  `backend/app/services/planning_orchestrator.py`; no frontend file, no
  scheduling logic, and no regeneration logic changed.

---

## 41. Groq AI Candidate Proposal Provider Adapter (Step 162A)

**Groq is a second, cheap/dev-iteration LLM base for AI candidate
proposals, alongside (not replacing) the Anthropic/Claude adapter (Step
161A).** `backend/app/providers/ai_candidate_proposal/groq_adapter.py`
defines `GroqAICandidateProposalProvider`, a second real (non-
`not_connected`) `AICandidateProposalProvider` implementation. It is still
not wired into `PlanningOrchestrator`'s default path, scheduling,
validation, or regeneration -- it is only reachable by explicitly
injecting it or by setting `AI_CANDIDATE_PROPOSAL_PROVIDER=groq`. The
shadow-mode stage (Step 161B) is provider-agnostic: it calls whichever
`AICandidateProposalProvider` the factory resolves, so Groq gets exactly
the same shadow-mode-only treatment Anthropic does.

- **Default app behavior remains `not_connected`.** The factory default
  (`Settings.ai_candidate_proposal_provider`) is unchanged by this step --
  still `"not_connected"`. Even with `AI_CANDIDATE_PROPOSAL_PROVIDER=groq`
  explicitly set, a missing `GROQ_API_KEY` (the default) makes `propose`
  return an honest `not_connected` result -- it never calls the network and
  never crashes the app or the test suite.
- **Uses `langchain_groq.ChatGroq`'s structured output, not a raw HTTP
  call.** The adapter builds a `ChatGroq` client bound to
  `with_structured_output(_GroqProposalBatchSchema)`, where
  `_GroqProposalBatchSchema` (and its nested `_GroqProposalSchema`) mirror
  `AICandidateProposal` field-for-field (`proposal_id`, `candidate_name`,
  `candidate_type`, `priority_hint`, `suggested_area`, `why_consider`,
  `fit_with_user_preferences`, `verification_requirements`, `confidence`)
  and deliberately have no coordinate, provider-id, price, rating,
  opening-hours, route-time, review-count, ticket-price, availability,
  booking-link, or safety-score field.
- **The `langchain_groq` package import is deferred**, kept inside a
  `_build_client` method rather than a module-level import, exactly like
  the Anthropic adapter's deferred `anthropic` import -- so the rest of the
  app, and every test that injects a fake client or exercises the no-key
  path, keeps working whether or not the package is installed.
- **Every output still validates through `AICandidateProposalResult`/
  `AICandidateProposal` before acceptance.** `client.invoke(prompt)`'s
  return value (a `_GroqProposalBatchSchema` instance, or a plain dict from
  a simpler injected fake) is normalized to a dict, then each proposal
  entry is parsed through `AICandidateProposal`'s own validation
  (forbidden-claim text patterns, non-blank fields, non-empty
  `verification_requirements`) before it can appear in a `completed`
  result:
  - Missing package, missing/unset `GROQ_API_KEY`, or client-construction
    failure -> `not_connected` (no proposals, zero confidence, failed
    guardrail explaining why).
  - The `invoke` call itself raising, an unparseable/non-structured
    response, or a parsed proposal failing
    `AICandidateProposal`/`AICandidateProposalResult` validation ->
    `rejected` (no proposals, failed guardrail explaining why).
  - A valid, schema-conforming response -> `completed`, with validated
    proposals, a passed guardrail, `provider_name`, and `model_name`.
  - No case ever fabricates a proposal to compensate for a failure.
- **Proposals still require grounding through `CandidateGroundingService`
  before scheduling**, exactly like every other proposal provider. This
  adapter never creates a `GroundedCandidate` or `RejectedCandidateProposal`
  itself, never calls `CandidateGroundingService`, never calls a provider
  adapter, and never mutates `PlanningState`.
- **Factory support.** `get_ai_candidate_proposal_provider` (Step 160E) now
  supports `"groq"` -> `GroqAICandidateProposalProvider()`, alongside
  `"not_connected"` and `"anthropic"`. The default remains
  `"not_connected"`, and an unsupported/unrecognized config value still
  falls back to `NotConnectedAICandidateProposalProvider`.
- **No real Groq API call in automated tests.**
  `backend/app/tests/providers/test_groq_ai_candidate_proposal_provider.py`
  proves the full adapter contract -- no-key path, fake-client success/
  rejection/exception paths, explicit `api_key`/`model` forwarding into a
  faked `langchain_groq` module, no forbidden factual field in any result,
  and no `CandidateGroundingService` call -- using only in-file fake
  clients. No test in this suite requires a real `GROQ_API_KEY`, and no
  test imports the real `langchain_groq`/`groq` packages.
- **No LangGraph in this step.** This adapter calls `ChatGroq` directly; no
  LangGraph graph/node is added here or anywhere else in this step.
- **Config.** `backend/app/core/config.py` adds `groq_api_key`
  (`GROQ_API_KEY`, default `None`) and `groq_model` (`GROQ_MODEL`, default
  `"openai/gpt-oss-20b"`), following the exact same optional/safe-default
  pattern as `anthropic_api_key`/`anthropic_model`.

---

## 42. LangGraph Skeleton Around the Deterministic Planning Pipeline (Step 162B)

**This is a LangGraph skeleton only -- architecture/resume foundation, not
a runtime change.** `backend/app/graphs/planning_graph.py` defines a
LangGraph `StateGraph` that mirrors `PlanningOrchestrator`'s existing
deterministic stage order, but it is not wired into
`PlanningOrchestrator.generate_full_plan`, is not imported by any API
route, and does not change `POST /trips/{trip_id}/generate` behavior,
scheduling, validation, or regeneration in any way.

- **No LLM call of any kind.** This step calls no LLM -- not Groq (Step
  162A), not Anthropic (Step 161A), not any other provider. No Kiwi/MCP
  integration and no scraping are added. The graph nodes call only the
  existing deterministic stage services, exactly as `PlanningOrchestrator`
  already does.
- **`PlanningState` remains the single source of truth.** `PlanningGraphState`
  (a `TypedDict`) carries `planning_state: PlanningState` through the graph
  unchanged in shape; `executed_nodes` and `errors` are a test/debug-only
  trace, not new plan data. No node reconstructs, duplicates, or bypasses
  `PlanningState`.
- **No stage logic is duplicated.** Every node calls exactly one existing
  service method and nothing else:
  - `traveler_profile_node` -> `TravelerProfileService.run`
  - `destination_context_node` -> `DestinationContextService.run`
  - `candidate_quality_node` -> `CandidateQualityService.build_report`,
    stored onto `planning_state.candidate_quality_report` exactly like
    `PlanningOrchestrator.run_destination_context_stage` already does (Step
    156B)
  - `ai_candidate_shadow_placeholder_node` -> **a deliberate no-op**. It
    does not call `AICandidateDiscoveryService`, Groq, or Anthropic, and it
    leaves `ai_candidate_proposal_batch`/`candidate_grounding_batch`
    untouched -- it only records that the node executed. **Step 162C is
    expected to wire the real config-gated shadow-mode call into this
    node.**
  - `trip_strategy_node` -> `TripStrategyService.run`
  - `stay_transport_node` -> `StayTransportService.run`
  - `experience_plan_node` -> `ExperiencePlannerService.run`
  - `validation_node` -> `PlanValidatorService.run`
- **Node order matches the documented pipeline order** (docs/14_backend_
  architecture.md section 7), with `candidate_quality` and
  `ai_candidate_shadow_placeholder` inserted at the same points
  `PlanningOrchestrator` already runs them (inline inside
  `run_destination_context_stage`):
  `START -> traveler_profile -> destination_context -> candidate_quality ->
  ai_candidate_shadow_placeholder -> trip_strategy -> stay_transport ->
  experience_plan -> validation -> END`.
- **Dependency injection throughout, no module-level singleton.**
  `PlanningGraphRunner.__init__` accepts every stage service and defaults
  to constructing the real ones (mirroring `PlanningOrchestrator.__init__`'s
  own pattern) only when not injected. Tests inject fake stage-service
  doubles so no real provider/network call is ever made. Unlike
  `planning_orchestrator` (a module-level singleton `PlanningOrchestrator()`
  instance), no `PlanningGraphRunner`/graph singleton is constructed at
  import time or imported by any API route.
- **Never persists anything.** `PlanningGraphRunner.run` never calls
  `PlanningStateRepository`/`TripRepository` -- persistence stays the
  caller's responsibility, exactly as it already is for
  `PlanningOrchestrator`'s individual stage-runner methods.
- **No real LLM/provider call in tests.**
  `backend/app/tests/graphs/test_planning_graph.py` proves the graph
  compiles, runs nodes in the exact documented order, returns a
  `PlanningState`, calls only injected fake services, never saves to either
  repository, is not imported by `PlanningOrchestrator` or
  `app/api/routes/trips.py`, and that `POST /trips/{trip_id}/generate`
  behaves exactly as before this step -- all using only in-file fake
  service doubles, never a real provider or LLM call.
- **No LangSmith runtime configuration added.** `langgraph` depends on
  `langchain-core`, which (as already noted in Step 162A) transitively
  pulls in the `langsmith` package -- this module does not import
  `langsmith` directly, and does not set any LangSmith tracing environment
  variable or configuration.

---

## 43. AI Candidate Shadow Node Wired Into the LangGraph Skeleton (Step 162C)

Step 162C replaces `backend/app/graphs/planning_graph.py`'s Step 162B
placeholder (`ai_candidate_shadow_placeholder_node`, always a no-op) with a
real node, `ai_candidate_shadow_node`, that calls the existing
`AICandidateDiscoveryService.dry_run` -- but only when shadow mode is
explicitly enabled. **The graph is still not wired into `/generate` or any
other runtime path**; this step only gives the graph's shadow node the same
real (config-gated, off-by-default) behavior
`PlanningOrchestrator._run_ai_candidate_discovery_shadow_stage` (Step 161B)
already has.

- **Same safe-default gate as the orchestrator's shadow stage.** The node
  reads `Settings.ai_candidate_discovery_shadow_mode_enabled`
  (`AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED`, default `False`) live via
  `get_settings()` on every run, exactly like
  `PlanningOrchestrator._run_ai_candidate_discovery_shadow_stage` does --
  `build_planning_graph`'s new `shadow_mode_enabled` parameter only exists
  so tests can force an explicit override; it changes nothing about the
  live-settings default path. `Settings.ai_candidate_proposal_provider`
  (`AI_CANDIDATE_PROPOSAL_PROVIDER`, default `"not_connected"`) is
  similarly untouched by this step -- whichever provider
  `AICandidateDiscoveryService`'s underlying factory call resolves (Groq,
  Anthropic, or the default not_connected) is exactly as config-gated as
  it already was.
- **No stage logic duplicated.** The node calls exactly one method,
  `ai_candidate_discovery_service.dry_run(planning_state)` -- the same
  `AICandidateDiscoveryService` (Steps 157A-161B) `PlanningOrchestrator`
  already uses, injected via `PlanningGraphRunner`'s constructor (mirroring
  every other stage service's DI pattern in this module). No new
  discovery/grounding logic is written in the graph module itself.
- **Three-way gate, matching the orchestrator's shadow stage exactly:**
  - Disabled (the default) -> no-op: `executed_nodes` still records
    `"ai_candidate_shadow"` ran, but no batch field is touched.
  - Enabled but `planning_state.destination_context` is still `None` ->
    no-op: the discovery service is never called without real candidate
    data to ground against (grounding needs `destination_context`'s
    candidate lists).
  - Enabled and `destination_context` exists -> calls `dry_run`, then
    stores `AICandidateProposalBatch(request=..., result=...)` and
    `CandidateGroundingBatch(request=..., result=...)` onto
    `planning_state`, reusing the exact same batch models
    `PlanningOrchestrator`'s shadow stage already constructs -- no new
    model, no new validation rule.
- **Fails safe on any exception.** If `dry_run` raises for any reason, the
  node stores nothing and appends one generic, secret-free marker string
  to the graph's `errors` list (never the raw exception message, a prompt,
  an API key, or a raw LLM response) -- and the graph continues to run
  every remaining node exactly as if shadow mode were disabled for that
  run. This never raises out of the node itself.
- **Still shadow-only -- nothing downstream consumes a proposal or grounded
  candidate.** `candidate_quality_node` (which runs *before*
  `ai_candidate_shadow_node`) and `experience_plan_node`,
  `stay_transport_node`, `trip_strategy_node`, `validation_node` (which all
  run through their existing service `run(planning_state)` method) have no
  parameter through which an AI proposal or `GroundedCandidate` could ever
  be passed in -- `CandidateQualityService.build_report` and
  `ExperiencePlannerService.run` both take only `planning_state` as their
  sole argument, structurally identical to how
  `PlanningOrchestrator`/`ExperiencePlannerService` already guarantee this
  (docs/14_backend_architecture.md section 25, Step 156C/156E). Neither
  `provider_coverage` nor `data_sources_used` ever gains a Groq/Anthropic/
  AI-related source name from this node.
- **Never persists.** Exactly like every other node, storing the batches
  onto `planning_state` is an in-memory mutation only -- `PlanningGraphRunner.run`
  still never calls `PlanningStateRepository`/`TripRepository`.
- **No real Groq/Anthropic API call in automated tests.**
  `backend/app/tests/graphs/test_planning_graph.py` adds a
  `_FakeAICandidateDiscoveryService` test double that returns a canned
  `AICandidateDiscoveryDryRunResult` built entirely from existing, already-
  validated models (`AICandidateProposalRequest`/`Result`,
  `CandidateGroundingRequest`/`Result`, `GroundedCandidate`,
  `RejectedCandidateProposal`) -- never a dict, never a real provider call.
  Tests prove: the node is a no-op with batches untouched when disabled;
  a no-op when `destination_context` is missing even with shadow mode
  forced on; the fake discovery service is called exactly once when
  enabled with `destination_context` present; not_connected/skipped,
  completed, and partial fake results all store correctly; a raising fake
  discovery service is swallowed fail-safe with only a generic marker
  appended to `errors`, and every other observable field (data sources
  used, candidate quality report) stays identical whether shadow mode is
  disabled or enabled-but-failing; node execution order stays exactly
  `traveler_profile -> destination_context -> candidate_quality ->
  ai_candidate_shadow -> trip_strategy -> stay_transport ->
  experience_plan -> validation`; the graph runner still never saves to
  either repository; neither `PlanningOrchestrator` nor
  `app/api/routes/trips.py` import the graph module;
  `PlanningOrchestrator.generate_full_plan`'s own source has no reference
  to a graph; and `POST /trips/{trip_id}/generate` behaves exactly as
  before this step.
- **No scheduling, validation, or regeneration behavior changes.** This
  step touches only `backend/app/graphs/planning_graph.py` and its test
  file -- no frontend file, no `ExperiencePlannerService`/
  `PlanValidatorService` logic, and no regeneration logic changed.

---

## 44. Backend Generation Stage-Progress Model (Step 163B)

Step 163B adds `GenerationProgress`
(`backend/app/models/planning_state.py`), a dedicated model recording real
`PlanningOrchestrator` pipeline stage progress during `POST
/trips/{trip_id}/generate`, plus a read-only `GET
/trips/{trip_id}/generation-progress` endpoint (docs/11_api_contracts.md
section 29). **This is preparation for future frontend progress
polling/animation only -- the frontend is not wired to it in this step.**
The Step 163A decorative loading animation (frontend/app/page.tsx's
`TravelGenerationLoading`) continues to run entirely off local UI timer
state; it does not read `generation_progress` and this step does not
change that.

- **Real backend stage progress, nothing else.** `GenerationProgress`
  tracks which named pipeline stage `PlanningOrchestrator.generate_full_plan`
  is running or has run: `status` (`idle`/`generating`/`completed`/
  `failed`), `current_stage`/`current_stage_label`, `completed_stages`,
  `total_stages`, `progress_percent`, `message`, `updated_at`, and a fixed
  `is_real_backend_stage_progress: true` marker. It is never flight
  tracking, a real flight route, a real route/travel time, a booking
  status, a flight number, a price, a rating, or an availability claim --
  no such field exists on this model, and the boolean marker exists
  specifically so nothing downstream can confuse backend pipeline
  bookkeeping with real-world travel movement.
- **Allowed stage keys** (`GENERATION_STAGE_KEYS`, in this fixed order):
  `traveler_profile`, `destination_context`, `candidate_quality`,
  `ai_candidate_shadow`, `trip_strategy`, `stay_transport`,
  `experience_plan`, `validation`, `post_processing`. The first eight mirror
  the LangGraph skeleton's node names (Step 162B/162C section 42/43) for
  consistency; `post_processing` is new, covering the
  versioning/plan-diff-preview/regeneration-readiness bookkeeping
  `generate_full_plan` runs after the last stage. **This list is
  independent of `app.graphs` -- the LangGraph module remains not wired
  into `/generate`; this step does not change that** (re-verified by this
  step's own tests, in addition to the existing Step 162B/162C tests).
- **`PlanningOrchestrator.create_trip`** now sets
  `planning_state.generation_progress = GenerationProgress()` (idle,
  `progress_percent=0`, empty `completed_stages`) on every new trip,
  instead of leaving the field `None`, so `GET
  /trips/{trip_id}/generation-progress` always has real state to read
  immediately after trip creation.
- **`PlanningOrchestrator.generate_full_plan`** wraps its existing,
  unmodified stage calls with progress bookkeeping only -- stage order and
  stage outputs are unchanged, and the existing save-after-each-stage
  cadence is unchanged (progress fields are updated on the same
  `planning_state` object already being saved at each existing save point;
  no new save call was added on the success path). Before the stage loop
  starts, progress resets to `generating`/0%/empty `completed_stages` for
  that run (so re-generating an already-generated trip reports that run's
  progress, never stale counts appended on top of a previous run).
  `candidate_quality` and `ai_candidate_shadow` are recorded as their own
  completed stage keys immediately after `run_destination_context_stage`
  returns (both are real sub-steps it already performs internally) --
  without any change to `run_destination_context_stage` or
  `_run_ai_candidate_discovery_shadow_stage` themselves. After the last
  stage plus the post-processing recomputes, `status` becomes `completed`
  and `progress_percent` is forced to exactly `100`.
- **Failure path**: if any stage raises, `generation_progress.status`
  becomes `failed` (via a `try`/`except`/`raise` around the stage loop)
  before the original exception is re-raised unchanged -- the exception is
  never swallowed or replaced, and no other error-handling behavior
  changes. `current_stage` is deliberately left pointing at whichever
  stage was running when the failure happened, rather than cleared, since
  that's more useful for diagnosis.
- **No scheduling, validation, or regeneration behavior changes.**
  `ExperiencePlannerService`, `PlanValidatorService`, and every
  regeneration-safety service/endpoint (`POST /regenerate` still always
  refuses with `REGENERATION_NOT_AVAILABLE`) are untouched. Shadow-mode
  default behavior is untouched: with
  `AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED` at its default `false`,
  `ai_candidate_proposal_batch`/`candidate_grounding_batch` still stay
  `None` after `/generate`, re-verified by this step's own tests alongside
  the new `generation_progress` assertions.
- **No Groq/Anthropic/Kiwi/MCP/scraping call anywhere in this step.** No
  provider adapter, AI candidate proposal provider, or LangGraph node is
  imported or called by `GenerationProgress`, the new stage-progress
  helper methods on `PlanningOrchestrator`, or the new endpoint --
  confirmed by this step's static source-inspection tests (no `"graph"`
  reference in `generate_full_plan`'s source, no `app.graphs` import
  anywhere in `planning_orchestrator.py`) plus the existing
  `conftest.py` autouse env-isolation fixture
  (`_isolate_ai_candidate_proposal_env`) that already prevents any real
  LLM key from leaking into the test suite.
- **Persists like every other `PlanningState` field.** `generation_progress`
  round-trips through `LocalJsonStore` unchanged; a planning state
  persisted before this step (no `generation_progress` key at all) still
  loads, with the field honestly defaulting to `None` rather than raising
  or fabricating a progress record.

---

## 45. Frontend Wired to Backend Generation Progress (Step 163C)

Step 163C is frontend/API-client wiring only -- **no AI, provider,
planning, scheduling, validation, or regeneration behavior changes**. It
connects the Step 163A decorative loading animation to the real Step 163B
`GET /trips/{trip_id}/generation-progress` endpoint, purely as a loading-UI
data source.

- **What changed**: `frontend/lib/api.ts` gained `getGenerationProgress`
  (same `request()`/envelope helper every other client function already
  uses); `frontend/lib/types.ts` gained a `GenerationProgress` type
  mirroring the backend response field-for-field; `frontend/app/page.tsx`'s
  `handlePlanTrip()` now polls that endpoint on a ~700ms interval while
  `POST /trips/{trip_id}/generate` is in flight, and
  `TravelGenerationLoading` gained four optional props
  (`progressPercent`, `stageLabel`, `progressMessage`,
  `isRealBackendStageProgress`) it uses in preference to its local timer
  loop when present (docs/16_frontend_architecture.md section 29.1).
- **The frontend only ever reads this data for loading-UI display.**
  Nothing in `loadPlanResult`, itinerary rendering, validation display, or
  any other section reads `generation_progress` -- it is consumed
  exclusively inside `TravelGenerationLoading` while `isLoading` is true,
  and discarded (`setBackendProgress(null)`) once generation finishes or
  fails.
- **`loadPlanResult` is still called exactly once** per successful
  `handlePlanTrip()` run, after generation completes and after one final
  progress read plus a brief pause to show the completed/100% state --
  never on every poll tick. **`POST /generate` is still called exactly
  once** per submission; the polling loop only ever calls the read-only
  `GET /generation-progress` endpoint.
- **Poll failures are non-fatal by design.** Each poll tick's promise has
  its own `.catch()` that silently ignores the failure (never calls
  `setError`); if the final post-generation read also fails, the result
  still renders. A transient progress-polling hiccup can never surface as
  a user-facing error or block the actual generation flow, which does not
  depend on polling succeeding at all.
- **No backend file was touched by this step.** `PlanningOrchestrator`,
  the `GenerationProgress` model, the `/generation-progress` endpoint,
  `app/graphs/planning_graph.py`, every provider adapter, and every
  Groq/Anthropic-backed code path are exactly as Step 163B left them --
  this step only adds a frontend consumer of an endpoint that already
  existed and was already read-only/side-effect-free.

---

## 46. Provider Cache Foundation (Step 164A)

Step 164A adds `ProviderCacheStore`
(`backend/app/storage/provider_cache_store.py`,
docs/12_provider_architecture.md section 25) -- a small local SQLite cache
store keyed by `(source, query_hash)`. **This is deterministic
infrastructure, not AI.** It contains no LLM call, no prompt, no model
inference, and no reasoning of any kind -- `make_query_hash` is a plain
SHA-256 hash of canonical JSON, and `get`/`set`/`delete`/`prune_expired`/
`clear_source` are ordinary SQLite reads/writes. It sits in the same
category as `LocalJsonStore` (Python stdlib only), not alongside the
AI candidate-proposal/grounding subsystem (sections 28-41) or the LangGraph
skeleton (sections 42-43).

- **No provider behavior changes yet.** `OpenStreetMapPlacesAdapter`,
  `OpenMeteoWeatherAdapter`, `NagerDateHolidaysAdapter`,
  `FrankfurterCurrencyAdapter`, `ProviderGateway`, and
  `PlanningOrchestrator` do not import or call this module -- confirmed by
  this step's own static source-inspection tests, the same pattern used to
  confirm LangGraph isn't wired into `/generate` (section 43/45). Every
  provider call still goes out live (or reports `not_connected`/
  `unavailable` honestly) exactly as before this step; no response is
  read from or written to a cache anywhere in the current request path.
- **Cache miss is always honest.** `get` returns `None` for a missing row
  or an expired row -- it never fabricates, guesses, or backfills a
  payload. This matters even though nothing calls `get` yet: it's the
  contract a future wiring step will rely on to preserve the "if data is
  unavailable, mark it `unavailable`" rule (CLAUDE.md Core Rules) rather
  than accidentally serving stale-but-plausible-looking data as if it
  were fresh.
- **Never stores raw query text, secrets, or user-private trip data.**
  Only an opaque `query_hash` is persisted per row (never the query dict/
  string that produced it); `payload`/`metadata` must be JSON-serializable
  and are documented as provider-response data only -- never an API key,
  token, prompt, raw LLM response, or `PlanningState`/trip-specific
  content.
- **Config declared, not read.** `Settings.provider_cache_path`
  (`PROVIDER_CACHE_PATH`) and `Settings.provider_cache_enabled`
  (`PROVIDER_CACHE_ENABLED`, default `true`) exist so a later step can
  wire a provider without a config change first; no code path reads
  `provider_cache_enabled` yet.
- **No real network/Groq/Anthropic/Kiwi/MCP/scraping call anywhere in
  this step or its tests.** `ProviderCacheStore` imports only Python
  stdlib (`sqlite3`, `json`, `hashlib`, `dataclasses`, `datetime`,
  `pathlib`, `threading`) -- confirmed by a static import-check test
  mirroring the one already used for the AI candidate-proposal adapters
  and the LangGraph module. Every test in
  `backend/app/tests/repositories/test_provider_cache_store.py` and
  `backend/app/tests/core/test_provider_cache_config.py` uses only
  in-file payloads and a `tmp_path`-backed SQLite file -- no HTTP call,
  no live provider, no API key required.

---

## 47. Open-Meteo Weather Provider Cache Wiring (Step 164B)

Step 164B wires the Step 164A `ProviderCacheStore` foundation into exactly
one provider adapter, `OpenMeteoWeatherAdapter`
(`backend/app/providers/weather/open_meteo_adapter.py`,
docs/12_provider_architecture.md section 26). **This is deterministic
provider infrastructure, not AI reasoning** -- it contains no LLM call, no
prompt, and no model inference; a cache hit and a cache miss both return
data that already came from the real Open-Meteo API on some earlier call,
never anything invented or inferred.

- **Only Open-Meteo is wired.** `OpenStreetMapPlacesAdapter`,
  `NagerDateHolidaysAdapter`, `FrankfurterCurrencyAdapter`,
  `ProviderGateway`, and `PlanningOrchestrator` are unchanged by this step
  and still do not import `ProviderCacheStore` -- confirmed by this step's
  own static source-inspection tests
  (`backend/app/tests/core/test_provider_cache_config.py`).
- **Cache key is `source="open_meteo"` + a hash of the normalized
  request** (latitude, longitude, start/end date, timezone) -- never the
  raw destination string, trip ID, or any other `PlanningState`/trip-private
  field. The cached payload is the normalized `NormalizedDailyWeather[]`
  response Open-Meteo itself already returned on a prior live call, not
  anything reasoned about or reformatted by an AI step.
- **Cache hit/miss never fabricates data.** A hit returns the exact same
  `ProviderResponse` shape a live call would, relabeled
  `data_status="cached"`; a miss (including an expired entry, which is
  treated exactly like a miss) runs the existing live HTTP path unchanged
  and normalizes it exactly as before. Only a successful, usable forecast
  is ever cached -- `unavailable`/`failed` responses are not.
- **Cache failure is non-fatal.** A broken cache read falls back to the
  live request; a broken cache write still returns the already-computed
  live result. Neither failure path logs the query or payload contents.
- **Config**: `Settings.open_meteo_cache_ttl_seconds`
  (`OPEN_METEO_CACHE_TTL_SECONDS`, default `3600`, must be non-negative)
  controls TTL; the existing `Settings.provider_cache_enabled`
  (`PROVIDER_CACHE_ENABLED`, default `true`) gates whether the cache is
  used at all -- when `false`, every call goes live even if a cache store
  was explicitly injected.
- **No real network/Groq/Anthropic/Kiwi/MCP/scraping call in this step's
  tests.** Every test in
  `backend/app/tests/providers/test_open_meteo_adapter.py` uses the
  existing in-file `_FakeClient`/`_FakeResponse` test doubles and an
  injected or `tmp_path`-backed `ProviderCacheStore` -- no real HTTP call
  to Open-Meteo or any other network service.

---

## 48. Nager.Date Holiday Provider Cache Wiring (Step 164C)

Step 164C wires the Step 164A `ProviderCacheStore` foundation into a
second provider adapter, `NagerDateHolidaysAdapter`
(`backend/app/providers/holidays/nager_date_adapter.py`,
docs/12_provider_architecture.md section 27), alongside Open-Meteo (Step
164B, section 47). **This is deterministic provider infrastructure, not AI
reasoning** -- it contains no LLM call, no prompt, and no model inference;
a cache hit and a cache miss both return data that already came from the
real Nager.Date API on some earlier call, never anything invented or
inferred.

- **Open-Meteo and Nager.Date are now the two cache consumers.**
  `OpenStreetMapPlacesAdapter`, `FrankfurterCurrencyAdapter`,
  `ProviderGateway`, and `PlanningOrchestrator` are unchanged by this step
  and still do not import `ProviderCacheStore` -- confirmed by this step's
  own static source-inspection tests
  (`backend/app/tests/core/test_provider_cache_config.py`), which also
  re-confirm Open-Meteo's Step 164B wiring is unaffected.
- **Cache key is `source="nager_date"` + a hash of the normalized
  request** (`country_code`, `year`) -- never the raw destination string,
  trip ID, trip date range, or any other `PlanningState`/trip-private
  field. Caching is per calendar year rather than per trip date range,
  matching Nager.Date's own per-year API shape and letting a different
  trip in the same country/year reuse the same cache entry. The cached
  payload is the normalized `NormalizedHoliday[]` list Nager.Date itself
  already returned for that year on a prior live call, not anything
  reasoned about or reformatted by an AI step.
- **Cache hit/miss never fabricates data.** A hit for a given year returns
  the exact same `NormalizedHoliday` shape a live call for that year would,
  relabeled `data_status="cached"`; a miss (including an expired entry,
  treated exactly like a miss) runs the existing live HTTP path for that
  year unchanged and normalizes it exactly as before. A year is only
  cached if its live fetch produced at least one usable holiday; a
  malformed/empty year, and any overall `unavailable`/`failed` response,
  is never cached.
- **Cache failure is non-fatal.** A broken cache read for a year falls
  back to the live request for that year; a broken cache write still
  returns the already-computed live result. Neither failure path logs the
  query or payload contents.
- **Config**: `Settings.nager_date_cache_ttl_seconds`
  (`NAGER_DATE_CACHE_TTL_SECONDS`, default `2592000` = 30 days, must be
  non-negative) controls TTL -- 30 days is acceptable because public
  holiday calendars change slowly once published for a given year/country,
  but it stays configurable; the existing `Settings.provider_cache_enabled`
  (`PROVIDER_CACHE_ENABLED`, default `true`) gates whether the cache is
  used at all -- when `false`, every call goes live even if a cache store
  was explicitly injected.
- **No real network/Groq/Anthropic/Kiwi/MCP/scraping call in this step's
  tests.** Every test in
  `backend/app/tests/providers/test_nager_date_adapter.py` uses the
  existing in-file `_FakeClient`/`_FakeResponse` test doubles and an
  injected or `tmp_path`-backed `ProviderCacheStore` -- no real HTTP call
  to Nager.Date or any other network service. Open-Meteo's own cache tests
  (`backend/app/tests/providers/test_open_meteo_adapter.py`) are re-run
  unchanged and still pass, confirming this step didn't disturb Step 164B.

---

## 49. Frankfurter Currency Provider Cache Wiring (Step 164D)

Step 164D wires the Step 164A `ProviderCacheStore` foundation into a third
provider adapter, `FrankfurterCurrencyAdapter`
(`backend/app/providers/currency/frankfurter_adapter.py`,
docs/12_provider_architecture.md section 28), alongside Open-Meteo (Step
164B, section 47) and Nager.Date (Step 164C, section 48). **This is
deterministic provider infrastructure, not AI reasoning** -- it contains
no LLM call, no prompt, and no model inference; a cache hit and a cache
miss both return a rate that already came from the real Frankfurter API on
some earlier call, never anything invented or inferred.

- **Open-Meteo, Nager.Date, and Frankfurter are now the three cache
  consumers.** `OpenStreetMapPlacesAdapter`, `ProviderGateway`, and
  `PlanningOrchestrator` are unchanged by this step and still do not
  import `ProviderCacheStore` -- confirmed by this step's own static
  source-inspection tests (`backend/app/tests/core/test_provider_cache_config.py`),
  which also re-confirm Open-Meteo's and Nager.Date's earlier wiring is
  unaffected.
- **Cache key is `source="frankfurter"` + a hash of the normalized
  request** (`base_currency`, `destination_currency`, a fixed `"latest"`
  marker) -- never the raw destination string, trip ID, or any other
  `PlanningState`/trip-private field. `amount` is not part of the key,
  since this adapter always requests a single-unit rate and the
  normalized result doesn't depend on it. The cached payload is the
  normalized `NormalizedExchangeRate` Frankfurter itself already returned
  on a prior live call, not anything reasoned about or reformatted by an
  AI step.
- **Cache hit/miss never fabricates data.** A hit returns the exact same
  `ProviderResponse` shape a live call would, relabeled
  `data_status="cached"`; a miss (including an expired entry, treated
  exactly like a miss) runs the existing live HTTP path unchanged and
  normalizes it exactly as before. Only a successful rate fetched over the
  network is ever cached -- `unavailable`/`failed` responses are not, and
  neither is the same-currency identity result (which never makes an HTTP
  call in the first place).
- **Cache failure is non-fatal.** A broken cache read falls back to the
  live request; a broken cache write still returns the already-computed
  live result. Neither failure path logs the query or payload contents.
- **Config**: `Settings.frankfurter_cache_ttl_seconds`
  (`FRANKFURTER_CACHE_TTL_SECONDS`, default `21600` = 6 hours, must be
  non-negative) controls TTL -- 6 hours is acceptable because this app's
  currency data can change but not minute-by-minute, but it stays
  configurable; the existing `Settings.provider_cache_enabled`
  (`PROVIDER_CACHE_ENABLED`, default `true`) gates whether the cache is
  used at all -- when `false`, every call goes live even if a cache store
  was explicitly injected.
- **No real network/Groq/Anthropic/Kiwi/MCP/scraping call in this step's
  tests.** Every test in
  `backend/app/tests/providers/test_frankfurter_adapter.py` uses the
  existing in-file `_FakeClient`/`_FakeResponse` test doubles and an
  injected or `tmp_path`-backed `ProviderCacheStore` -- no real HTTP call
  to Frankfurter or any other network service. Open-Meteo's and
  Nager.Date's own cache tests are re-run unchanged and still pass,
  confirming this step didn't disturb Steps 164B/164C.

---

## 50. OpenStreetMap Geocoding Cache Wiring (Step 164E)

Step 164E wires the Step 164A `ProviderCacheStore` foundation into a
fourth provider adapter, `OpenStreetMapPlacesAdapter`
(`backend/app/providers/places/openstreetmap_adapter.py`,
docs/12_provider_architecture.md section 29), alongside Open-Meteo (Step
164B, section 47), Nager.Date (Step 164C, section 48), and Frankfurter
(Step 164D, section 49). **This is deterministic provider infrastructure,
not AI reasoning** -- it contains no LLM call, no prompt, and no model
inference; a cache hit and a cache miss both return a geocode result that
already came from the real Nominatim API on some earlier call, never
anything invented or inferred.

- **Geocoding only -- Overpass POI search is untouched.** Only
  `_resolve_destination` (the Nominatim destination lookup used by
  `_search`, `resolve_coordinates`, and `search_must_visit_place`) is
  cache-wired. Attraction/restaurant/accommodation-POI search via Overpass
  still goes out live on every call, exactly as before this step --
  confirmed by every pre-existing Overpass-related test in
  `backend/app/tests/providers/test_openstreetmap_adapter.py` still
  passing unchanged, plus a dedicated source-inspection test
  (`backend/app/tests/core/test_provider_cache_config.py`) confirming
  `_query_overpass` itself references neither `cache_store` nor
  `ProviderCacheStore`.
- **Open-Meteo, Nager.Date, Frankfurter, and now OSM geocoding are the
  four cache consumers.** No other provider (routes, transit,
  accommodation, flights) is wired, and `ProviderGateway`/
  `PlanningOrchestrator` still do not import `ProviderCacheStore` --
  confirmed by this step's own static source-inspection tests, which also
  re-confirm the three earlier adapters' wiring is unaffected.
- **Cache key is `source="openstreetmap_geocode"` + a hash of the
  normalized search text** (plus the fixed `format`/`limit` Nominatim
  params) -- never the raw trip destination stored as its own field, a
  trip ID, or any other `PlanningState`/trip-private data. The cached
  payload is the geocode result (`lat`, `lng`, `bounding_box`,
  `display_name`) Nominatim itself already returned on a prior live call,
  not anything reasoned about or reformatted by an AI step.
- **Cache hit/miss never fabricates a coordinate, place name, or OSM
  ID.** A hit returns the exact same internal `_ResolvedDestination` shape
  a live geocode would; a miss (including an expired entry, treated
  exactly like a miss) runs the existing live Nominatim request unchanged
  and applies the exact same plausibility check as before. Only a
  successfully resolved, plausibility-checked destination is ever cached
  -- an unresolved destination, a rejected implausible match, or a request
  failure is not.
- **Sits underneath the existing per-instance in-memory
  `self._destination_cache` dict**, which is unchanged and still checked
  first; the persistent cache only extends reuse across separate adapter
  instances and process restarts, which the in-memory dict alone cannot
  do.
- **Cache failure is non-fatal.** A broken cache read falls back to the
  live request; a broken cache write still returns the already-computed
  live result. Neither failure path logs the query or payload contents.
- **Config**: `Settings.osm_geocode_cache_ttl_seconds`
  (`OSM_GEOCODE_CACHE_TTL_SECONDS`, default `2592000` = 30 days, must be
  non-negative) controls TTL -- 30 days is acceptable because geocoding a
  given destination string changes slowly, but it stays configurable; the
  existing `Settings.provider_cache_enabled` (`PROVIDER_CACHE_ENABLED`,
  default `true`) gates whether the persistent cache is used at all --
  when `false`, every call goes live even if a cache store was explicitly
  injected (only the in-memory per-instance dict still applies).
- **No real network/Groq/Anthropic/Kiwi/MCP/scraping call in this step's
  tests.** Every test in
  `backend/app/tests/providers/test_openstreetmap_adapter.py` uses the
  existing in-file `_FakeClient`/`_FakeResponse` test doubles and an
  injected or `tmp_path`-backed `ProviderCacheStore` -- no real HTTP call
  to Nominatim, Overpass, or any other network service. Open-Meteo's,
  Nager.Date's, and Frankfurter's own cache tests are re-run unchanged and
  still pass, confirming this step didn't disturb Steps 164B/164C/164D.

---

## 51. Manual Live Smoke Coverage for OSM Geocoding Cache (Step 164F)

Step 164F extends the existing Step 164D.1 manual smoke script
(`backend/scripts/manual_provider_cache_smoke.py`,
docs/21_manual_provider_cache_smoke.md) to also exercise Step 164E's OSM
geocoding cache wiring against the real Nominatim API. **This is manual
live provider verification, not AI reasoning** -- it contains no LLM call,
no prompt, and no model inference; it only proves that a real, already-
built HTTP integration and its cache wiring still work together, the same
way the script already did for Open-Meteo/Nager.Date/Frankfurter.

- **Geocoding only, added to the existing script -- no new script, no CI,
  no pytest integration.** The script still requires
  `RUN_LIVE_PROVIDER_CACHE_SMOKE=true` and still exits immediately with no
  network call when that's missing/falsy. It now calls
  `OpenStreetMapPlacesAdapter.resolve_coordinates` -- never
  `search_attractions`/`search_restaurants`/`search_accommodation_pois`/
  `search_must_visit_place`, all of which call Overpass, not just
  Nominatim, and Overpass is not cache-wired (Step 164E's own scope).
- **A network-call counter, not a fake response, proves the cache path.**
  `resolve_coordinates` returns a plain coordinate, not a
  `ProviderResponse` with a `data_status` field, so unlike the other three
  providers' `data_status="cached"` check, this step wraps the adapter's
  own `httpx.Client` in a thin, transparent counter (delegating every call
  to the real client, changing no header/timeout/User-Agent) to prove a
  second, fresh-instance lookup made no additional live HTTP request. This
  is still a real live network call underneath -- nothing is faked.
- **Still only structural assertions.** No exact coordinate, OSM ID, or
  display name is ever asserted -- only that a real point was resolved
  (`status=success`-equivalent), that the second call needed no new
  network request, that a cache row exists for source
  `"openstreetmap_geocode"`, and that no secret marker or raw destination
  text is stored in `query_hash`/`payload_json`/`metadata_json`.
- **No provider behavior changed.** No file under
  `backend/app/providers/` or `backend/app/core/config.py` was touched by
  this step -- only the manual script, its test suite, and docs.
- **No real network/Groq/Anthropic/Kiwi/MCP/scraping call in this step's
  own tests.** Every test in
  `backend/app/tests/scripts/test_manual_provider_cache_smoke_script.py`
  still only inspects the script's source and runs it as a subprocess with
  the guardrail env var deliberately missing/falsy -- no real HTTP call to
  Nominatim or any other network service, and no `RUN_LIVE_PROVIDER_CACHE_SMOKE`
  requirement anywhere in the automated suite.

---

## 52. OSM/Overpass POI Search Cache Wiring (Step 164G)

Step 164G extends `OpenStreetMapPlacesAdapter`'s Step 164E geocode cache
wiring (section 50) to also cover Overpass POI search
(`backend/app/providers/places/openstreetmap_adapter.py`'s `_try_query`,
docs/12_provider_architecture.md section 30). **This is deterministic
provider infrastructure, not AI reasoning** -- it contains no LLM call, no
prompt, and no model inference; a cache hit and a cache miss both return
places that already came from the real Overpass API on some earlier call,
never anything invented or inferred.

- **Overpass POI search only, not the must-visit lookup.** Only
  `_try_query` (used by `search_attractions`, `search_restaurants`, and
  `search_accommodation_pois`, for both the primary and every fallback tag
  query) is cache-wired. `search_must_visit_place`'s Nominatim named-place
  lookup (`_lookup_named_place`) is untouched -- it was never Overpass to
  begin with.
- **Cache key is `source="openstreetmap_poi"` + a hash of the normalized
  request** (`lat`, `lon`, `radius_meters`, sorted `tags`, `limit`) --
  never the raw destination string, trip ID, or any other
  `PlanningState`/trip-private field, and the Overpass query string itself
  is never persisted as raw metadata. One row per individual Overpass
  query (primary or one fallback tag), matching how the live path already
  issues separate requests.
- **Cache hit/miss never fabricates a place, coordinate, OSM ID, rating,
  price, opening hours, availability, booking link, or route time.** A hit
  returns the exact same `NormalizedPlace` list a live query would, each
  place relabeled `data_status="cached"`; a miss (including an expired
  entry, treated exactly like a miss) runs the existing live Overpass path
  unchanged and applies the exact same containment filter as before. Only
  a query with at least one named, contained result is cached --
  empty/unusable results and request failures are not.
- **The overall `ProviderResponse` envelope is unaffected.** `status`,
  `data_status`, `fallback_used`, and `fallback_provider` on
  `search_attractions`/`search_restaurants`/`search_accommodation_pois`
  still reflect only whether fallback was needed, exactly as before this
  step -- confirmed by a dedicated test
  (`test_poi_cache_hit_does_not_change_provider_coverage_relevant_fields`)
  and by the fact that nothing in `CandidateQualityService`,
  `ExperiencePlannerService`, `destination_context_service.py`'s
  candidate-list construction, or `provider_coverage`/`data_sources_used`
  reads or filters on an individual place's `data_status`. Candidate
  quality, scheduling, validation, and provider coverage logic are
  unchanged by this step.
- **Cache failure is non-fatal.** A broken cache read falls back to the
  live request; a broken cache write still returns the already-computed
  live result. Neither failure path logs the query or payload contents.
- **Config**: `Settings.osm_poi_cache_ttl_seconds`
  (`OSM_POI_CACHE_TTL_SECONDS`, default `604800` = 7 days, must be
  non-negative) controls TTL -- shorter than the 30-day geocode TTL since
  POI data changes more often than geocoding but not every minute, but it
  stays configurable; the existing `Settings.provider_cache_enabled`
  (`PROVIDER_CACHE_ENABLED`, default `true`) gates whether the cache is
  used at all -- when `false`, every call goes live even if a cache store
  was explicitly injected.
- **A latent test-isolation gap was found and fixed, not a provider
  behavior change.** Because POI search now also reads the same lazily-
  resolved, process-wide cache singleton geocoding already used, an
  existing API test (`test_destination_grounding.py`) that monkeypatches
  `provider_gateway.places` with a real `OpenStreetMapPlacesAdapter()` (no
  injected `cache_store`) started intermittently reusing a different
  test's cached POI result instead of exercising its own fake HTTP client.
  `backend/app/tests/conftest.py` gained an autouse
  `_isolate_provider_cache_store` fixture (mirroring the pre-existing
  `_reset_in_memory_repositories` fixture) that points every cache-wired
  adapter module at a fresh, throwaway, per-test store. This is a test
  suite fix only -- no production code path changed.
- **No real network/Groq/Anthropic/Kiwi/MCP/scraping call in this step's
  tests.** Every test in
  `backend/app/tests/providers/test_openstreetmap_adapter.py` uses the
  existing in-file `_FakeClient`/`_FakeResponse` test doubles and an
  injected or `tmp_path`-backed `ProviderCacheStore` -- no real HTTP call
  to Overpass, Nominatim, or any other network service. Open-Meteo's,
  Nager.Date's, and Frankfurter's own cache tests, and OSM's own geocode
  cache tests, are re-run unchanged and still pass, confirming this step
  didn't disturb Steps 164B/164C/164D/164E. No live smoke coverage was
  added for Overpass in this step (docs/21_manual_provider_cache_smoke.md
  still only covers OSM geocoding).

---

## 53. Manual Live Smoke Coverage for OSM/Overpass POI Cache (Step 164H)

Step 164H extends the Step 164D.1/164F manual smoke script
(`backend/scripts/manual_provider_cache_smoke.py`,
docs/21_manual_provider_cache_smoke.md) to also exercise Step 164G's
OSM/Overpass POI search cache wiring against the real Overpass API. **This
is manual live provider verification, not AI reasoning** -- it contains no
LLM call, no prompt, and no model inference; it only proves that a real,
already-built HTTP integration and its cache wiring still work together,
the same way the script already did for the other four providers.

- **One small, respectful POI call, added to the existing script -- no new
  script, no CI, no pytest integration.** The script still requires
  `RUN_LIVE_PROVIDER_CACHE_SMOKE=true` and still exits immediately with no
  network call when that's missing/falsy. It now additionally calls
  `OpenStreetMapPlacesAdapter.search_attractions` -- never
  `search_restaurants`, `search_accommodation_pois`, or
  `search_must_visit_place` -- for the same known destination ("Lisbon,
  Portugal") already used for every other provider in this script, whose
  geocoding was already cached by the existing OSM geocoding check earlier
  in the same run, so no additional Nominatim request is made either.
- **A network-call counter, not a fake response, proves the cache path,**
  the same technique already used for OSM geocoding.
  `search_attractions`'s overall `ProviderResponse.status`/`data_status`
  reflects only whether Overpass fallback was needed (Step 164G design),
  not whether the result came from cache, so this wraps the adapter's own
  `httpx.Client` in a thin, transparent counter (delegating every call to
  the real client, changing no header/timeout/User-Agent) to prove a
  second, fresh-instance search made no additional live Overpass request.
  This is still a real live network call underneath -- nothing is faked.
- **Still only structural assertions.** No exact POI name, OSM ID, or
  coordinate is ever asserted -- only that real, named places were
  returned (`status` in `success`/`partial`/`fallback_used` and a non-empty
  result), that the second call needed no new Overpass request, that a
  cache row exists for source `"openstreetmap_poi"`, that no secret marker
  or raw destination text is stored in
  `query_hash`/`payload_json`/`metadata_json`, and that the cached payload
  itself contains no rating/price/opening-hours/availability/booking/
  route-time marker -- checked directly against the stored bytes, since
  `NormalizedPlace` never carries any of those fields to begin with.
- **No provider behavior changed.** No file under
  `backend/app/providers/` or `backend/app/core/config.py` was touched by
  this step -- only the manual script, its test suite, and docs.
- **No real network/Groq/Anthropic/Kiwi/MCP/scraping call in this step's
  own tests.** Every test in
  `backend/app/tests/scripts/test_manual_provider_cache_smoke_script.py`
  still only inspects the script's source and runs it as a subprocess with
  the guardrail env var deliberately missing/falsy -- no real HTTP call to
  Overpass or any other network service, and no
  `RUN_LIVE_PROVIDER_CACHE_SMOKE` requirement anywhere in the automated
  suite.

---

## 54. OSRM Routing Provider Skeleton (Step 165A)

Step 165A adds a routing provider contract (`backend/app/models/routing.py`)
and a first skeleton adapter (`backend/app/providers/routing/`,
docs/12_provider_architecture.md section 31) for point-to-point route data
(distance, duration) between two coordinates. **This is deterministic
provider infrastructure, not AI reasoning** -- it contains no LLM call, no
prompt, and no model inference; `OSRMRoutingAdapter` either returns a real
distance/duration OSRM itself already returned, or an honest
`not_connected`/`unavailable`/`failed` result. Nothing here invents a
route.

- **Not wired into planning.** No file under `backend/app/services/`
  (`PlanningOrchestrator`, `ExperiencePlannerService`,
  `PlanValidatorService`, or any other) imports or calls this subsystem.
  `ProviderGateway.routes` is untouched -- still the generic,
  always-`not_connected` `RoutesProvider()` base interface from
  `app.providers.base`, exactly as before this step, confirmed by a
  dedicated test that the gateway's source never mentions "routing" or
  "osrm."
- **Mirrors the AI candidate-proposal provider-boundary pattern** (section
  28 above), not the generic `ProviderResponse[T]` envelope every *other*
  real provider adapter in this codebase returns: `RoutingProvider` is an
  `abc.ABC` with one abstract method, `get_route`, and `RouteResult`
  carries its own `status` field directly (`not_connected`/`unavailable`/
  `failed`/`success`) rather than being wrapped.
- **`NotConnectedRoutingProvider` is the default**, exactly analogous to
  `NotConnectedAICandidateProposalProvider` (section 29): deterministic,
  no network call, always an honest `not_connected` `RouteResult` with no
  distance/duration and zero confidence.
- **`OSRMRoutingAdapter` is conservative by default.** With no
  `OSRM_BASE_URL` configured -- the default -- it returns `not_connected`
  without any network call, even if `ROUTING_PROVIDER=osrm` is also set.
  Only `code == "Ok"` with a usable route is treated as `success`;
  `NoRoute`/malformed/empty-route responses are `unavailable`; a
  request-level failure is `failed`. Distance/duration are read directly
  from OSRM's own `distance`/`duration` fields (already meters/seconds) --
  never invented, and never backfilled from
  `app.utils.geo.haversine_distance_km` (a straight-line estimate, clearly
  documented elsewhere in this codebase as "not a route, walking, or
  travel-time distance" -- this adapter never calls it).
- **Config-gated factory** (`get_routing_provider`, Step 165A) mirrors
  `get_ai_candidate_proposal_provider` (section 38): `"not_connected"`
  (default) and `"osrm"` are the only supported names; an unsupported/
  unrecognized name falls back to `NotConnectedRoutingProvider` rather
  than raising or guessing.
- **No cache.** `ProviderCacheStore` (Step 164A) is not imported or called
  anywhere in this subsystem -- confirmed by a dedicated test.
- **No real network/Groq/Anthropic/Kiwi/MCP/scraping call in this step's
  tests.** Every test in
  `backend/app/tests/providers/test_routing_provider.py`,
  `test_osrm_adapter.py`, and `test_routing_factory.py` uses only in-file
  fake HTTP doubles (for the OSRM adapter) or no network access at all
  (for the contract models and the not-connected/factory paths) -- no real
  HTTP call to any OSRM instance, public or self-hosted. Open-Meteo's,
  Nager.Date's, Frankfurter's, and OSM's own provider/cache tests are
  re-run unchanged and still pass, confirming this step didn't disturb any
  earlier provider work.

---

## 55. Routing Exposed Through ProviderGateway (Step 165B)

Step 165B wires the Step 165A routing provider factory into
`ProviderGateway` (`backend/app/providers/gateway.py`'s new `routing`
attribute and `get_route` method,
docs/12_provider_architecture.md section 32). **This is deterministic
provider infrastructure, not AI reasoning** -- it contains no LLM call, no
prompt, and no model inference; `ProviderGateway.get_route` is a pure
delegation to whichever `RoutingProvider` the gateway holds, returning
exactly what that provider itself already produces (an honest
`not_connected` result by default, or a real OSRM-backed distance/duration
if explicitly configured) -- never anything invented or reasoned about.

- **Exposure only, not consumption.** `PlanningOrchestrator`,
  `ExperiencePlannerService`, and `PlanValidatorService` do not call
  `ProviderGateway.get_route` or reference `ProviderGateway.routing`
  anywhere -- confirmed by dedicated static source-inspection tests on all
  three modules. Scheduling and validation behavior are unchanged by this
  step.
- **`ProviderGateway.__init__` gained one new, backward-compatible
  parameter** (`routing: RoutingProvider | None = None`), defaulting to
  `get_routing_provider()` -- the exact Step 165A factory, unmodified.
  Every existing `ProviderGateway(...)` construction, including the
  pre-existing, unrelated `routes=` parameter for the generic
  `RoutesProvider` stub, is unaffected.
- **The gateway adds no route data of its own.** `get_route` never
  fabricates a distance or duration, and never falls back to
  `app.utils.geo.haversine_distance_km` (a straight-line estimate, not a
  route) when the underlying provider reports the route as
  `not_connected`/`unavailable`/`failed`.
- **No provider coverage change.** `ProviderGateway.default_provider_coverage()`
  and `ProviderCoverage`'s existing `routes` field are untouched -- no
  active or inactive routing coverage metadata is added, since nothing in
  the coverage-tracking path reads the new `routing`/`get_route` surface
  yet.
- **No cache.** `ProviderGateway.get_route` never reads from or writes to
  `ProviderCacheStore`.
- **Dependency injection for tests.** `ProviderGateway(routing=<fake>)`
  lets a test substitute an in-memory `RoutingProvider` double and assert
  the gateway delegates to it correctly -- no real or fake HTTP layer
  needed for that class of test at all.
- **No real network/Groq/Anthropic/Kiwi/MCP/scraping call in this step's
  tests.** Every test in
  `backend/app/tests/providers/test_provider_gateway_routing.py` uses
  either the default `not_connected` routing provider or an injected fake
  -- no real HTTP call to any OSRM instance. One test explicitly
  monkeypatches `httpx.Client` to raise if ever called, proving
  `ProviderGateway()`'s default `get_route` path opens no network
  connection at all. Open-Meteo's, Nager.Date's, Frankfurter's, and OSM's
  own provider/cache tests, and Step 165A's own routing tests, are re-run
  unchanged and still pass.

## 56. OSRM Route Cache Wiring (Step 165C)

Step 165C wires `OSRMRoutingAdapter` (section 54) to the Step 164A
`ProviderCacheStore` foundation (docs/12_provider_architecture.md section
33). **This is deterministic provider infrastructure, not AI reasoning**
-- it contains no LLM call, no prompt, and no model inference. Caching an
OSRM response is a pure key-value lookup keyed by a SHA-256 hash of the
normalized route request (origin/destination coordinates and profile);
nothing about *what* the adapter returns changes, only whether a repeated,
identical route lookup re-fetches from OSRM or reads the same normalized
result back from local SQLite.

- **Only `osrm_adapter.py` changed.** `ProviderGateway` needed no update --
  it already delegates `get_route` straight through to whichever
  `RoutingProvider` it holds (section 55), so it automatically benefits
  from the new cache wiring without any gateway-level code change.
- **Cache hit and cache miss return the same normalized shape.** A cached
  `RouteResult` has identical `distance_meters`/`duration_seconds`/
  `geometry`/`confidence` fields to what a live OSRM call would produce --
  no route time or distance is ever invented, whether the result came from
  cache or from a live request.
- **Never cached:** `not_connected`, `unavailable`, `failed`, `NoRoute`,
  and malformed OSRM responses. Only a successful, usable route is
  written to the cache.
- **Cache failure is always non-fatal.** A broken cache read falls back to
  the live OSRM HTTP call; a broken cache write still returns the
  already-computed live result. Routing never fails *because* the cache
  failed.
- **No secrets, no user trip data.** The cached payload holds only
  normalized route fields; no API key, prompt, raw LLM response, or
  user-private trip payload is ever stored, and the raw coordinate query
  text / request URL is never persisted -- only the opaque query hash.
- **Routing is still not consumed by scheduling or validation.** This step
  changes only how `OSRMRoutingAdapter` answers a repeated lookup -- it
  does not change whether, when, or how often `PlanningOrchestrator`,
  `ExperiencePlannerService`, or `PlanValidatorService` call routing (they
  still don't).
- **No real network/Groq/Anthropic/Kiwi/MCP/scraping call in this step's
  tests.** Every new cache test either injects a `ProviderCacheStore`
  pointed at a throwaway SQLite file, or a fake in-memory cache double that
  raises on `get`/`set` to prove failure-fallback behavior -- no real HTTP
  call to any OSRM instance. Open-Meteo's, Nager.Date's, Frankfurter's,
  OSM's, and Step 165A/165B's own routing tests are re-run unchanged and
  still pass.

## 57. Manual Live Smoke Coverage for OSRM Route Cache (Step 165D)

Step 165D extends `backend/scripts/manual_provider_cache_smoke.py`
(docs/21_manual_provider_cache_smoke.md, previously covering Open-Meteo,
Nager.Date, Frankfurter, and OSM) to also call the real `OSRMRoutingAdapter`
(sections 54-56) against a live OSRM instance. **This is manual live
provider verification, not AI reasoning** -- it contains no LLM call, no
prompt, and no model inference; it is a human-run script that calls
`OSRMRoutingAdapter.get_route` directly and checks the response structure
and cache behavior, nothing more.

- **One tiny, fixed route, called twice.** The script uses one known,
  stable origin/destination pair near Lisbon, Portugal (consistent with
  every other provider this script already covers) against a real OSRM
  instance -- the public OSRM demo server by default, or `OSRM_BASE_URL`
  if a developer sets it.
- **Structural checks only.** The script confirms `status=success` with a
  positive numeric `distance_meters`/`duration_seconds` on the first call,
  and that the second, identical call returns the same values (proving
  cache reuse) with a `"osrm_route"` cache row present -- it never asserts
  an exact distance, duration, or geometry value.
- **No secrets, no raw coordinates/URLs in the cache or in output.** The
  script checks that no cache row for `osrm_route` contains a secret-like
  marker, a raw coordinate value, or a route URL fragment, and its
  `print()` calls never include a base URL, a route URL, or raw
  coordinates -- only the same safe summary line format
  (`provider=... live_path_ok=... cache_path_ok=... status=...
  cache_row_count=...`) every other provider in this script already uses.
- **Manual-only, never CI.** Like the rest of this script, the OSRM check
  only runs when a human sets `RUN_LIVE_PROVIDER_CACHE_SMOKE=true` and
  executes the file directly -- never during `pytest`, `python -m
  compileall`, or any CI job.
- **Does not mean routing is used in scheduling or validation.** A PASS
  here only confirms `OSRMRoutingAdapter` and its cache work when called
  directly through this manual script. (As of Step 165E below,
  `PlanningOrchestrator` does call `ProviderGateway.get_route` for route
  *feasibility reporting* -- but this manual script's own PASS/FAIL is
  still unrelated to that; it only exercises the adapter directly.)
- **No real network/Groq/Anthropic/Kiwi/MCP/scraping call in this step's
  own tests.** `backend/app/tests/scripts/test_manual_provider_cache_smoke_script.py`
  never sets `RUN_LIVE_PROVIDER_CACHE_SMOKE` and never calls a real
  provider -- it only inspects the script's source and runs it with the
  guard deliberately missing, which must exit immediately with no network
  call at all.

## 58. Route Feasibility for Scheduled Experiences (Step 165E)

Step 165E adds `RouteFeasibilityService` (`backend/app/services/route_feasibility_service.py`,
docs/12_provider_architecture.md section 35), run by `PlanningOrchestrator`
between `ExperiencePlannerService` and `PlanValidatorService`. **This is
deterministic provider infrastructure, not AI reasoning** -- it contains no
LLM call, no prompt, and no model inference. It is a plain loop over
consecutive scheduled experience pairs that calls
`ProviderGateway.get_route` (Step 165B, cache-backed via Step 165C when
configured) and maps each `RouteResult` onto a `RouteLegFeasibility` using
fixed, deterministic rules -- success maps to `feasible`, a missing
coordinate maps to `unavailable` without ever calling the provider, and
`not_connected`/`unavailable`/`failed` maps to `needs_review`.

- **Not route-aware scheduling.** This step never reorders, adds, or drops
  a scheduled experience. `ExperiencePlannerService`'s straight-line/
  haversine-based scheduling (Step 156C) is completely untouched --
  feasibility is reported *about* the existing schedule, never used to
  change it. Full route-aware scheduling is Section 166's job.
- **No fabricated route data, ever.** A leg is only `feasible` when the
  real routing provider returned `status=success`; `distance_meters`/
  `duration_seconds` come directly from that provider response, never
  invented, and never backfilled from a straight-line/haversine estimate.
- **`PlanValidatorService` consumes the resulting report** (Step 165E) to
  replace its previous blanket "not implemented yet" feasibility warning
  with one that names what was actually found -- still always a
  `WARNING`, never upgrading `readiness_status` to `ready` by itself.
- **Default behavior is unchanged in spirit.** With the default
  `Settings.routing_provider="not_connected"`, every leg (and the report as
  a whole) honestly reports `not_connected` with no network call --
  `/generate` behaves exactly as before this step for a default
  deployment, just with an additional, honestly-empty-of-real-data report
  attached.
- **No real network/Groq/Anthropic/Kiwi/MCP/scraping call in this step's
  tests.** Every test either uses the default `NotConnectedRoutingProvider`
  or injects a deterministic in-memory fake `RoutingProvider` double --
  never a real OSRM/network call, matching every other routing-related
  test in this codebase (Steps 165A-165D).

## 59. Route-Aware Day Sequencing, Shadow/Report-Only (Step 166A)

Step 166A adds `RouteAwareSequencingService`
(`backend/app/services/route_aware_sequencing_service.py`,
docs/12_provider_architecture.md, docs/14_backend_architecture.md), run by
`PlanningOrchestrator` immediately after `RouteFeasibilityService` (Step
165E, section 58 above) and before `PlanValidatorService`. **This is
deterministic provider infrastructure, not AI reasoning** -- no LLM call,
no prompt, no model inference, no LangGraph node. It reuses the exact same
`ProviderGateway.get_route` call path Step 165B/165C already exposes and
cache-backs, never a provider adapter directly.

- **Shadow/report-only, strictly.** `PlanningState.route_aware_sequencing_
  report` is a `RouteAwareSequencingReport` with `is_shadow_only=True` and
  `applied_to_itinerary=False`, always. This step never reorders, adds, or
  drops a scheduled experience -- `ExperiencePlannerService`'s straight-
  line/haversine scheduling (Step 156C) is completely untouched, and the
  suggested order is never fed back into `ExperiencePlannerService`,
  `validation_report`, or `provider_coverage`.
- **What it computes.** For each scheduled day with two or more
  experiences, it sums real route durations along the day's *current*
  order (coordinate-backed experiences only) and separately computes a
  candidate reordering using a conservative nearest-next-by-real-route-
  duration walk, starting from the day's first coordinate-backed
  experience. Both totals, and the `improvement_seconds` between them, are
  populated only when every route lookup they depend on returned
  `RouteResult.status == success` -- never a partial sum, never a
  straight-line (haversine) estimate presented as a route duration or
  distance, and never claimed to be optimal.
- **Honest degradation.** A day with fewer than two coordinate-backed
  experiences is `unavailable` without ever calling the routing provider
  for it. A day where some, but not all, needed route lookups succeeded
  (including a day where some, but not all, scheduled experiences are
  missing coordinates) is `partial`, with no duration/distance total
  reported. With the default `Settings.routing_provider="not_connected"`,
  every day (and the report as a whole) honestly reports `not_connected`
  with no network call -- `/generate` behaves exactly as before this step
  for a default deployment, just with an additional, honestly-empty-of-
  real-data shadow report attached.
- **No validator or provider-coverage change.** `PlanValidatorService` and
  `ProviderCoverage.routes` are untouched by this step -- both remain
  driven solely by `route_feasibility_report`, exactly as they were after
  Step 165E.
- **No real network/Groq/Anthropic/Kiwi/MCP/scraping call in this step's
  tests.** Every test either uses the default `NotConnectedRoutingProvider`
  or injects a deterministic in-memory fake `RoutingProvider` double --
  never a real OSRM/network call, matching every other routing-related
  test in this codebase (Steps 165A-165E).

## 60. Config-Gated Route-Aware Scheduling Application (Step 166B)

Step 166B lets Step 166A's shadow-only sequencing suggestion actually
change the scheduled itinerary order -- but only behind an explicit
config gate, `Settings.route_aware_scheduling_enabled` (default `False`,
`ROUTE_AWARE_SCHEDULING_ENABLED`). **The default behavior is completely
unchanged**: with the gate off, `PlanningOrchestrator` never calls
`RouteAwareSequencingService.apply_report`, and `route_aware_sequencing_
report` stays exactly as Step 166A left it -- `is_shadow_only=True`,
`applied_to_itinerary=False`, and the scheduled itinerary order untouched.

- **Only provider-backed, successful-enough suggestions are ever
  applied.** `apply_report` reorders a day's real scheduled experiences
  only when every one of these holds: the day's suggestion `status ==
  success` (never `partial`/`unavailable`/`not_connected`/`failed`); its
  real `improvement_seconds` exceeds the configured minimum
  (`Settings.route_aware_scheduling_min_improvement_seconds`, default
  `0.0`, so only a genuinely positive improvement ever applies); the
  day's real, current scheduled experience IDs still exactly match the
  suggestion's `original_order` (guards against a stale suggestion); and
  `suggested_order` is verified to be an exact permutation of those same
  IDs (same multiset, same length) -- no experience is ever added,
  removed, or duplicated by applying it, and no experience's own fields
  (name, coordinates, dates, etc.) are ever changed, only schedule order.
- **No fabricated route duration/distance, ever, in the application
  path either.** Everything `apply_report` reads
  (`route_duration_seconds`/`route_distance_meters`/`improvement_seconds`)
  was already computed by Step 166A's `build_report` from real,
  successful `RouteResult`s only -- `apply_report` itself makes no new
  provider call and never substitutes a straight-line (haversine)
  estimate for a route duration.
- **When applied, the report records it honestly.** At least one day
  actually being reordered flips `route_aware_sequencing_report.
  is_shadow_only` to `False` and `applied_to_itinerary` to `True`, and
  each affected day's own `RouteAwareSequenceSuggestion.applied` flips to
  `True`. When nothing was safe to apply (including the gate being off),
  both flags stay exactly as Step 166A set them.
- **Route feasibility stays fresh after a reorder.** If `apply_report`
  changes at least one day's order, `PlanningOrchestrator` recomputes
  `route_feasibility_report` (and the derived `ProviderCoverage.routes`)
  against the new order immediately afterward, so
  `PlanValidatorService`'s feasibility warning never describes a stale
  schedule.
- **No LangGraph, no LLM.** This remains deterministic provider
  infrastructure -- no LangGraph node, no LLM call, no prompt. `/generate`
  behaves exactly as before this step whenever the config gate is off
  (the default), and PlanValidatorService/regeneration-refusal behavior
  are otherwise unaffected either way.
- **No real network/Groq/Anthropic/Kiwi/MCP/scraping call in this step's
  tests.** Every test either uses the default `NotConnectedRoutingProvider`
  or injects a deterministic in-memory fake `RoutingProvider` double --
  never a real OSRM/network call.

## 61. Provider-Backed Travel-Time Buffers (Step 166C)

Step 166C adds `TravelTimeBufferService`
(`backend/app/services/travel_time_buffer_service.py`,
docs/12_provider_architecture.md, docs/14_backend_architecture.md), run by
`PlanningOrchestrator` immediately after `route_feasibility_report` and
after any Step 166B config-gated route-aware-scheduling application, so
`travel_time_buffer_report` always reflects this run's *final* scheduled
order. **This is deterministic provider infrastructure, not AI
reasoning** -- no LLM call, no prompt, no model inference. For every
consecutive pair of scheduled experiences within a day, it calls
`ProviderGateway.get_route` (the same Step 165B/165C call path) and
builds a `TravelTimeBuffer` (`backend/app/models/routing.py`).

- **Travel-time buffers use provider-backed route duration only.**
  `recommended_buffer_seconds` is never anything but an exact restatement
  of a real, successful `RouteResult.duration_seconds` -- this step never
  adds an invented padding percentage or safety margin on top of it, and
  never substitutes a straight-line (haversine) estimate for a route
  duration. A leg only gets `status=success` when the routing provider
  actually returned a usable route.
- **Unavailable routing stays unavailable/needs_review, honestly.** A leg
  with a missing coordinate is `status=not_computable` without ever
  calling the routing provider for it. A leg whose routing provider call
  is `not_connected`/`unavailable`/`failed` mirrors that status exactly,
  with no duration/distance/buffer populated. In every one of these
  cases, `buffer_status=unavailable` -- sufficiency is never guessed when
  no real duration exists.
- **Schedule-gap comparison, only when real timestamps exist.** This
  app's scheduling does not currently populate
  `ExperienceItem.start_time`/`end_time` at all (Step 156-era
  scheduling), so `available_gap_seconds` is `None` and
  `buffer_status=not_computable` for essentially every plan generated
  today, even when a real successful duration exists. If a future step
  does populate real schedule timestamps, this same logic (mirroring
  `RouteFeasibilityService`'s own gap-parsing) compares the real gap
  against the real duration and reports `sufficient`/`insufficient`
  honestly -- never invented either way.
- **`PlanValidatorService` consumes the resulting report** (Step 166C) to
  add one `WARNING` per leg whose `buffer_status == insufficient` --
  never a critical issue, and never claiming a `sufficient`/
  `not_computable`/`unavailable` leg needs no review beyond what the
  existing feasibility warning already says.
- **Step 166B's config default is unchanged.** This step does not alter
  `Settings.route_aware_scheduling_enabled`'s default (`False`) or
  `apply_report`'s own safety contract in any way.
- **No real network/Groq/Anthropic/Kiwi/MCP/scraping call in this step's
  tests.** Every test either uses the default `NotConnectedRoutingProvider`
  or injects a deterministic in-memory fake `RoutingProvider` double --
  never a real OSRM/network call.

## 62. Hardened Unavailable-Routing Fallback Behavior (Step 166D)

Step 166D does not add a new report or feature -- it hardens the
fallback behavior across everything Steps 165E/166A-166C already built,
so route-dependent planning/reporting stays safe and consistent no
matter how routing data is missing, partial, or fails.

**Normalized fallback vocabulary.** Every one of the three Section 166
reports (`route_feasibility_report`, `route_aware_sequencing_report`,
`travel_time_buffer_report`) already shared the same conceptual states
before this step -- `success` only when a provider-backed duration/
distance actually exists, `partial` when some legs/days succeeded and
others didn't, `unavailable` when the provider responded but returned no
usable route, `not_connected` when no routing provider is configured,
and (for `travel_time_buffer_report` specifically)
`not_computable` when a required input, like coordinates, is missing
before the provider is ever called. This step keeps that vocabulary
exactly as-is (no enum renamed, no existing test's status assertion
changed) and hardens the code paths that produce it:

- **Route-dependent scheduling is applied only when provider-backed
  route data is complete and successful.** `RouteAwareSequencingService.
  apply_report` already required `suggestion.status == success` for
  every safety check to even be considered; this step adds no new
  scenario where a `partial`/`unavailable`/`not_connected`/`failed`
  suggestion can be applied -- it never could, and this step locks that
  in with cross-service regression tests (missing coordinates, partial
  route data) proving the scheduled order stays exactly as
  `ExperiencePlannerService` left it whenever routing data is anything
  less than fully successful.
- **An unexpected exception from the routing provider is contained per
  leg, in every one of the three route-dependent services.** Every real
  routing adapter already converts its own failure modes into an honest
  `RouteResult(status=failed/unavailable/not_connected)` without
  raising; `_safe_get_route` (duplicated, self-contained, in each of
  `RouteFeasibilityService`, `RouteAwareSequencingService`, and
  `TravelTimeBufferService`) is a second line of defense for a
  genuinely unexpected bug, converting any exception into an honest
  `status=failed` result with a generic, safe message -- never the raw
  exception text, never a provider payload, and never a straight-line/
  haversine estimate substituted in its place. `RouteAwareSequencingService.
  apply_report` additionally contains an unexpected error applying *one*
  day's suggestion without aborting other, still-safe days.
- **`PlanningOrchestrator` fails safe at the report level, too.** If
  `RouteFeasibilityService.build_report`, `RouteAwareSequencingService.
  build_report`/`apply_report`, or `TravelTimeBufferService.build_report`
  ever raises unexpectedly (beyond what per-leg containment already
  catches), the orchestrator stores an honest, empty `status=failed`
  report instead and generation continues -- `/generate` never fails
  just because route-dependent reporting did. No raw exception text or
  provider payload is ever stored in any user-facing field; the
  exception itself is only ever logged server-side.
- **`PlanValidatorService` never produces duplicate/conflicting warnings
  for the same leg**, and never claims route timing was checked when it
  wasn't. The blanket feasibility warning is a single aggregate `WARNING`
  regardless of how many legs are non-feasible; the travel-time-buffer
  warning path is now deduplicated by `(from_experience_id,
  to_experience_id)` as a defensive guard. Unavailable/not-connected
  routing always stays `needs_review`, never a blocking critical issue.

## 63. Movement-Data Provenance and Final Section 166 Integration (Step 166E)

Step 166E is the final Section 166 step. It is integration hardening,
validation clarity, and documentation only -- no new report, no new
endpoint, no change to any existing status field's values.

**Movement-data provenance.** `MovementDataProvenance`
(`backend/app/models/routing.py`) is a shared, six-value label --
`provider_backed`/`not_connected`/`unavailable`/`not_computable`/
`failed`/`not_applied` -- added as an *additional* field
(`movement_data_provenance`) on every `RouteLegFeasibility`/
`RouteAwareSequenceSuggestion`/`TravelTimeBuffer` and their three parent
reports, sitting alongside (never replacing) each one's own existing,
finer-grained status field. A shared helper,
`movement_data_provenance_from_status`, maps any of this subsystem's
existing statuses onto this vocabulary (`success` -> `provider_backed`,
`not_connected` -> `not_connected`, `failed` -> `failed`,
`not_computable` -> `not_computable`, `partial`/`unavailable` -> the
coarser shared `unavailable`), so a caller can tell at a glance whether a
duration/distance/reorder/buffer figure is real without first learning
each report's own status vocabulary.

**`not_applied` only ever appears on `RouteAwareSequenceSuggestion`.** A
`success` suggestion is `not_applied`, not `provider_backed`, from the
moment `RouteAwareSequencingService.build_report` creates it -- real,
provider-backed data can exist and still never have touched the actual
schedule, whether because `Settings.route_aware_scheduling_enabled` is
`False` (the default), the improvement didn't clear the configured
minimum, or the suggestion had otherwise gone stale. Only when
`apply_report` actually reorders that specific day does its provenance
flip to `provider_backed` (mirroring the existing `message`-update
pattern from Step 166D) -- so route-aware scheduling only ever affects
ordering when provider-backed route data is both complete *and*
successful *and* actually applied; it is never implied that routing was
applied just because favorable data existed.

**Unavailable routing remains `needs_review`, never fabricated.** Every
non-`provider_backed` provenance value stays exactly that: missing
coordinates are always `not_computable`, a not-connected provider is
always `not_connected`, a provider that responded with no usable route
is always `unavailable`, and a request (or an unexpected exception, Step
166D) that failed is always `failed`. None of these ever becomes
`provider_backed`, and `PlanValidatorService` never claims route timing
was checked when the underlying data says otherwise -- `needs_review` is
the ceiling for any plan whose movement data is anything other than
fully `provider_backed`.

---

## 64. Accommodation Provider Contract Foundation (Step 167A)

Step 167A adds an accommodation inventory provider contract
(`backend/app/models/accommodation.py`) and a first interface skeleton
(`backend/app/providers/accommodation/`,
docs/12_provider_architecture.md section 41) for bookable lodging
search results (property, price, availability, rating, booking link).
**This is deterministic provider infrastructure, not AI reasoning** --
it contains no LLM call, no prompt, and no model inference. It is the
accommodation-domain counterpart to the OSRM routing contract (Step
165A, section 54 above): a standalone model/interface foundation added
several steps before any concrete adapter or wiring.

- **Not wired into planning.** No file under `backend/app/services/`
  (`PlanningOrchestrator`, `StayTransportService`, `PlanValidatorService`,
  or any other) imports or calls this subsystem. `ProviderGateway.
  accommodation` is untouched -- still the generic, always-`not_connected`
  `AccommodationProvider()` base interface from `app.providers.base`,
  exactly as before this step, confirmed by a dedicated test that the
  gateway's and orchestrator's source never mentions this new contract.
- **Mirrors the routing provider-boundary pattern** (section 54 above),
  not the generic `ProviderResponse[T]` envelope every other real
  provider adapter in this codebase returns: `AccommodationInventoryProvider`
  is an `abc.ABC` with one abstract method, `search_accommodations`, and
  `AccommodationSearchResult` carries its own `status` field directly
  (`not_connected`/`unavailable`/`failed`/`success`) rather than being
  wrapped.
- **No concrete adapter yet** -- not a not-connected default, not an
  OSM-backed adapter, and no real Booking/Expedia/Hotelbeds/Hostelworld/
  Amadeus/Vrbo/Airbnb integration. This step is the contract only, one
  step earlier in the pattern than routing was after Step 165A (which at
  least shipped `NotConnectedRoutingProvider`).
- **Bookable inventory stays separate from OSM accommodation POIs.**
  `AccommodationOffer` (this step) is never merged with, or presented
  as, `DestinationContext.candidate_accommodation_pois` -- the existing
  open-data OSM location candidates already used for day-level
  `AccommodationSuggestion`/plan-level `StayAreaGuidance`. An OSM POI is
  a real place; it is never a price, availability window, rating, or
  booking link, and this step does not change that.
- **No fake hotel, price, availability, rating, amenity, cancellation
  policy, or booking link is ever produced.** Every optional fact field
  on `AccommodationOffer` stays at its honest empty/`None` default
  unless a real adapter sets it. `AccommodationSearchResult` enforces
  by validation that `offers` can only be non-empty when
  `status == success`.
- **No cache.** `ProviderCacheStore` (Step 164A) is not imported or
  called anywhere in this subsystem.
- **No real network/Groq/Anthropic/Kiwi/MCP/scraping call in this
  step's tests.** Every test in
  `backend/app/tests/models/test_accommodation_models.py` and
  `backend/app/tests/providers/test_accommodation_provider.py` uses only
  in-process pydantic validation and an in-file fake provider subclass --
  no network access at all. The full existing suite (routing, weather,
  holiday, currency, OSM provider tests, and the Section 165/166
  route-aware scheduling tests) is re-run unchanged and still passes,
  confirming this step didn't disturb any earlier provider work.

## 65. Accommodation Not-Connected Provider and Factory (Step 167B)

Step 167B adds `NotConnectedAccommodationProvider` and
`get_accommodation_provider` (`backend/app/providers/accommodation/
not_connected_adapter.py` and `factory.py`, docs/12_provider_
architecture.md section 42) plus one new config field,
`Settings.accommodation_provider` (default `"not_connected"` at the
time; changed to `"scraped_local"` by Step 168F, section 73). **This
is deterministic provider infrastructure, not AI reasoning** -- no LLM
call, no prompt, no model inference, exactly like the Step 165A/165B
routing factory pair it mirrors (section 54).

- `NotConnectedAccommodationProvider.search_accommodations` is a pure,
  deterministic function of its `AccommodationSearchResult` return
  value: it never inspects the request's destination/dates/party size
  to invent anything, and always reports `status=not_connected` with
  an empty `offers` list.
- `get_accommodation_provider` only chooses between already-existing
  `AccommodationInventoryProvider` subclasses; it never fabricates
  data itself and never falls back to anything other than the same
  not-connected provider for an unrecognized configuration value.
- **Still not wired into planning.** Neither `ProviderGateway` nor
  `PlanningOrchestrator` references this factory or provider class --
  confirmed by a dedicated source-inspection test, matching the
  equivalent Step 165A/165B-era checks for the routing factory.
- **No new AI-adjacent surface area.** This step touches no prompt,
  no `ai_candidate_proposal` code, and no Groq/Anthropic/Kiwi/MCP
  dependency -- confirmed by the same import-safety test style used
  for `app.providers.routing.factory`.

## 66. Accommodation Lookup Exposed Through ProviderGateway (Step 167C)

Step 167C adds `ProviderGateway.search_accommodations` and an
`accommodation_inventory` constructor slot, wiring the Step 167B
factory into the gateway the same way `get_route`/`routing` were wired
in for the routing subsystem (Step 165B, section 54/65 above). **This
is deterministic provider infrastructure, not AI reasoning** -- no LLM
call, no prompt, no model inference.

- `search_accommodations` is a pure delegation: it calls
  `self.accommodation_inventory.search_accommodations(request)` and
  returns whatever that provider returns, unmodified. It never inspects
  `request.destination`/dates/party size to invent a property, price,
  availability, rating, amenity, cancellation policy, or booking link.
- **Still not wired into planning.** Neither `PlanningOrchestrator`,
  `StayTransportService`, nor `PlanValidatorService` calls
  `search_accommodations` or references `accommodation_inventory` --
  confirmed by dedicated source-inspection tests, matching the
  equivalent Step 165B-era checks that `ExperiencePlannerService`/
  `PlanValidatorService` didn't call `gateway.get_route` yet.
- **Validation and coverage reporting are unchanged.**
  `PlanValidatorService` and `ProviderCoverageService` are both
  untouched by this step, and `ProviderGateway.default_provider_
  coverage()` still reports `accommodations: "not_connected"` exactly
  as before.
- **No new AI-adjacent surface area.** This step touches no prompt,
  no `ai_candidate_proposal` code, and no Groq/Anthropic/Kiwi/MCP
  dependency.

## 67. Accommodation Inventory Report, Coverage, and Validation Clarity (Step 167D)

Step 167D wires `AccommodationInventoryService`
(`backend/app/services/accommodation_inventory_service.py`) into
`PlanningOrchestrator.run_stay_transport_stage`, plus a new
`category="accommodation_inventory"` warning in `PlanValidatorService`
(docs/12_provider_architecture.md section 44,
docs/14_backend_architecture.md section 44). **This is deterministic
provider infrastructure, not AI reasoning** -- no LLM call, no prompt,
no model inference, and no LangGraph node is added or touched.

- `AccommodationInventoryService.build_report` is a pure function of
  `PlanningState.trip_request` and `ProviderGateway.
  search_accommodations` -- it never reads `destination_context`, so
  the OSM-backed `candidate_accommodation_pois` location candidates
  are structurally unreachable from this path and can never become a
  fabricated bookable offer.
- **Missing lodging inventory stays honestly `not_connected`/
  `unavailable`/`failed`.** With the default `not_connected`
  accommodation provider (Step 167B), every generation run stores
  `accommodation_inventory_report.status == "not_connected"` with an
  empty `offers` list; `PlanValidatorService`'s new warning states
  explicitly that price/availability/rating/booking-link data "could
  not be checked" -- it never claims lodging was checked when it
  wasn't, and it never blocks generation (`critical_issues` is never
  affected by this).
- **No hotel recommendation logic, no itinerary scheduling.** This
  step only records and surfaces an honest inventory status; it never
  populates `StayTransportDecision.accommodation_recommendations`,
  never adds a day-plan item, and never changes `ExperiencePlan`
  scheduling, route-aware sequencing (Section 165/166), or
  regeneration behavior (`POST /trips/{id}/regenerate` still always
  returns `409 REGENERATION_NOT_AVAILABLE`, confirmed unchanged by the
  existing regeneration-refusal test suite continuing to pass).

## 68. Frontend Wording and Final Section 167 Integration (Step 167E)

Step 167E (the final Section 167 step) adds frontend wording only --
`AccommodationInventorySection` in `frontend/app/page.tsx`, plus
`AccommodationInventoryReport`/`AccommodationOffer` types in
`frontend/lib/types.ts` (docs/16_frontend_architecture.md section 39).
**This is deterministic UI rendering of already-backend-computed data,
not AI reasoning** -- no LLM call, no prompt, no model inference, and no
client-side invention of any kind.

- The panel reads `trip.planning_state.accommodation_inventory_report`
  from the `GET /trips/{trip_id}` response already fetched by
  `loadPlanResult` -- no new API endpoint or extra network call is
  added.
- **Missing lodging inventory stays honestly `not_connected`/
  `unavailable`/`failed` in the UI, exactly as the backend reports it.**
  The frontend never upgrades a `not_connected`/`unavailable`/`failed`
  status, and a `success` status with zero offers is treated the same
  as not-connected for display purposes (no property/price/rating/
  availability/amenity/booking-link section is rendered) -- matching
  the backend's own `hotel_prices` coverage mapping (Step 167D, section
  44/67) treating a zero-offer success as `"unavailable"`.
- The `ProviderCoverageSection` field-label mapping added in this step
  (`accommodations` -> "Accommodation-like location candidates (open
  data)", `hotel_prices` -> "Bookable lodging inventory") is purely
  cosmetic -- it changes no coverage value, only how the existing raw
  key is labeled, with the raw key always shown alongside it.
- This closes out Section 167 (167A-167E, docs/12_provider_
  architecture.md section 45): a complete contract-to-frontend
  accommodation inventory foundation with no real lodging provider
  connected, no fabricated travel data anywhere in the stack, and OSM
  accommodation-like location candidates kept visibly and structurally
  separate from bookable lodging inventory at every layer.

## 69. Scraping Policy and Provenance Foundation (Step 168A)

Step 168A adds `backend/app/models/scraping.py`
(`ScrapingSourcePolicy`/`ScrapedDataProvenance`/`ScrapingSourceRegistry`,
docs/12_provider_architecture.md section 46) -- the first step of
Section 168, a separate contract-before-adapter foundation for scraping
missing travel data from explicitly-approved public pages. **This is
deterministic provider infrastructure, not AI reasoning** -- no LLM
call, no prompt, no model inference, and no live scraper exists yet.

- **Scraping was disabled by default when this step was written.**
  `Settings.scraping_enabled` and `Settings.scraped_accommodation_
  provider_enabled` both defaulted `False` here; Step 168F (section 73)
  later flipped both to `True`. `ScrapingSourceRegistry`'s default
  instance still starts with zero registered sources, so
  `active_sources()` still returns an empty list out of the box --
  registering an approved source is a separate, still-manual step from
  the app-wide `scraping_enabled` flag. No source becomes active without
  an explicit, per-source `enabled=True` that also passes every safety
  check (not login-required, not paywalled, not captcha-expected, and
  `approved_for_personal_use=True`).
- **LLMs must not invent a missing scraped field, now or later.** This
  step defines no scraped-item model with actual travel-fact fields
  (price, rating, availability, amenities, booking link, opening hours,
  route time, safety claim) -- only provenance metadata. When a future
  step adds such a model, the same rule that already governs
  `AccommodationOffer` applies: every optional fact field stays `None`/
  empty unless a real, approved scrape actually returned it, and no AI
  candidate-proposal or reasoning path may fill one in.
- **Scraped data can never masquerade as official-provider data.**
  `ScrapedDataProvenance.official_provider` is typed `Literal[False]` --
  a structural guarantee, not just a default, matching the "no fake
  official facts" rule this pipeline already enforces for provider data
  everywhere else.
- Not wired into `ProviderGateway`, `PlanningOrchestrator`, any stage
  service, or the frontend -- confirmed by dedicated source-inspection
  tests, mirroring the equivalent Step 167A-era checks for the
  accommodation contract.

## 70. Static HTML Parser Framework for Scraped Accommodation Data (Step 168B)

Step 168B adds `parse_scraped_accommodation_html`
(`backend/app/providers/accommodation/scraped_parser.py`,
docs/12_provider_architecture.md section 47) -- a pure function
transforming an already-provided HTML string into a normalized
`AccommodationSearchResult`. **This is deterministic provider
infrastructure, not AI reasoning** -- no LLM call, no prompt, no model
inference, and no live scraper exists yet.

- **LLMs must not fill a missing scraped field.** The parser itself
  already enforces this structurally (a missing price/rating/
  availability/booking-url/amenity stays `None`/`unknown`/empty), and no
  AI candidate-proposal or reasoning path reads or writes anything this
  module produces -- confirmed by dedicated source-inspection tests
  showing `ProviderGateway`/`PlanningOrchestrator` don't reference this
  module at all.
- **Refusal-before-parsing is itself deterministic**, not a judgment
  call: a `ScrapingSourcePolicy` that is unsafe, not enabled, or doesn't
  allow lodging data is refused before a single byte of HTML is
  examined, using the same `is_unsafe`/`enabled`/`allows_lodging` checks
  Step 168A's registry already uses.
- Uses only the Python standard library `html.parser.HTMLParser` -- no
  new dependency, no `httpx`/`requests` call (the project's existing
  `httpx` dependency, used by real provider adapters elsewhere, is not
  imported here), no browser automation.

## 71. Config-Gated Local/Manual Scraped Accommodation Provider (Step 168C)

Step 168C adds `ScrapedAccommodationProvider`
(`backend/app/providers/accommodation/scraped_adapter.py`,
docs/12_provider_architecture.md section 48), the first concrete adapter
to call the Step 168B parser. **This is deterministic provider
infrastructure, not AI reasoning** -- no LLM call, no prompt, no model
inference, and it reads only a manually-supplied local HTML file, never
a live website.

- **LLMs must not fill a missing scraped field here either.** The
  adapter passes the file's raw contents straight to
  `parse_scraped_accommodation_html` unmodified and returns whatever
  that function returns unmodified -- no post-processing step exists
  that could fill in a missing price/rating/availability/amenity, and
  none may ever be added.
- **Disabled by default at three independent layers**
  (`scraping_enabled`, `scraped_accommodation_provider_enabled`,
  `accommodation_provider="scraped_local"`) -- confirmed by tests
  showing each layer alone, without the others, still yields
  `not_connected`.
- Not wired into `ProviderGateway`'s default construction or
  `PlanningOrchestrator` -- selecting `"scraped_local"` through
  `get_accommodation_provider` and injecting the result into
  `ProviderGateway`/`AccommodationInventoryService` works today (both
  already just delegate to whatever provider they're given), but no
  default code path does so.

## 72. Scraped Accommodation Cache, Rate-Limit Guard, and Provenance Hardening (Step 168D)

Step 168D adds a cache layer to `ScrapedAccommodationProvider` and a
standalone `ScrapingRateLimitGuard` (docs/12_provider_architecture.md
section 49). **This is deterministic provider infrastructure, not AI
reasoning** -- no LLM call, no prompt, no model inference, and no live
fetching of any kind.

- **LLMs must not fill a missing scraped field on a cache hit either.**
  A cache hit reconstructs the exact `AccommodationSearchResult` the
  original parse produced, byte-for-byte in field values -- there is no
  post-cache-read processing step that could fill in a missing price/
  rating/availability/amenity, and none may ever be added.
- **The rate-limit guard makes no reasoning judgment either** -- it is a
  pure timing utility (elapsed time vs. `rate_limit_seconds`), with no
  branch that could be swapped for an AI-driven "should I wait" decision,
  and it has no concept of source safety at all.
- Confirmed by dedicated source-inspection tests that neither the cache
  code nor the rate-limit guard imports `httpx`/`requests`/a browser
  automation library, mirroring every prior Section 168 step's own
  import-safety checks.

## 73. End-to-End Scraped Accommodation Integration (Step 168E)

Step 168E proves the `scraped_local` path
end to end through `PlanningOrchestrator`/`ProviderCoverage`/
`PlanValidatorService`/the API/the frontend (docs/12_provider_
architecture.md section 50). **This is still deterministic provider
infrastructure, not AI reasoning** -- no LLM call, no prompt, no model
inference anywhere in this path, and no live website fetching is added.

- **LLMs must not fill a missing scraped field anywhere in this
  end-to-end chain** -- from the parser (Step 168B) through the cache
  (Step 168D) through the API response through the frontend, a missing
  price/rating/availability/amenity/booking-link stays `None`/`unknown`/
  empty at every hop; nothing in this chain is an AI reasoning step that
  could be tempted to fill one in.
- **LLMs (and any future AI-adjacent reasoning path) must treat
  `scraped_public_page` data as non-official and review-worthy, never
  as a verified fact.** `PlanValidatorService`'s warning now says so
  explicitly for scraped inventory ("not official-provider data...has
  not been verified"); any future AI reasoning step that ever reads
  `PlanningState.accommodation_inventory_report` must honor the same
  distinction -- a `scraped_provenance`-carrying offer is never
  equivalent to an official-provider-backed one, and must never be
  presented, summarized, or reasoned about as if it were.
- Confirmed unchanged: route-aware scheduling (Section 165/166),
  regeneration refusal (`409 REGENERATION_NOT_AVAILABLE`), and
  LangGraph's continued absence from `/generate` (existing import-level
  tests in `test_generation_progress.py`/`test_planning_graph.py`) all
  still hold with `scraped_local` explicitly enabled.

## 74. Scraped Accommodation Made the Default Provider (Step 168F)

Step 168F flips `Settings.accommodation_provider`'s default from
`"not_connected"` to `"scraped_local"`, and `Settings.scraping_enabled`/
`scraped_accommodation_provider_enabled` from `False` to `True`
(docs/12_provider_architecture.md section 51). **This is still
deterministic provider infrastructure, not AI reasoning** -- no LLM
call, no prompt, no model inference, and still no live website fetching
anywhere in this codebase.

- **LLMs must not fill the gap when the default file is absent.** With
  no file at the new default local path
  (`.data/manual_scrapes/accommodations.html`), `ScrapedAccommodationProvider`
  reports `status=unavailable` with `offers=[]` -- honest and empty, not
  a prompt for any reasoning step to "helpfully" propose a plausible
  hotel or price. No AI candidate-proposal or reasoning path reads or
  writes this provider's output.
- **LLMs must still treat any resulting `scraped_public_page` data as
  non-official and review-worthy**, exactly as section 73 already
  established -- this step changes only which provider is selected by
  default, not the honesty contract on what it returns.
- Confirmed unchanged: route-aware scheduling, regeneration refusal, and
  LangGraph's continued absence from `/generate`.

## 75. Flight Provider Contract Foundation (Step 169A)

Step 169A adds `backend/app/models/flight.py` (`FlightSearchRequest`/
`FlightSegment`/`FlightOffer`/`FlightSearchResult`) and
`backend/app/providers/flights/base.py` (`FlightInventoryProvider`)
(docs/12_provider_architecture.md section 52). **These are deterministic
provider infrastructure -- a data contract and an interface -- not AI
reasoning.** No LLM call, no prompt, no model inference exists in either
module, and no live flight provider or scraper is connected.

- **LLMs must not invent any missing flight field.** `FlightSegment`'s
  airports, carrier name/code, flight number, departure/arrival time, and
  duration, and `FlightOffer`'s price, currency, booking URL, availability
  status, baggage policy, and cancellation policy all stay `None` unless a
  real future adapter supplies them -- exactly the same rule already
  applied to `AccommodationOffer` (section 68) and to every other
  provider-fact field in this document (section 4). No AI reasoning step
  exists that reads or writes these models yet, so there is nothing to
  guard against calling them today, but the rule is stated here so a
  future flight-aware reasoning stage inherits it explicitly rather than
  needing to rediscover it.
- **A future scraped flight offer must be labeled the same way a scraped
  accommodation offer already is** -- `scraped_public_page`/
  `experimental`/`fragile` via `FlightOffer.scraped_provenance`, never
  presented as official-provider data, and any future AI reasoning step
  that ever reads flight inventory must treat a
  `scraped_provenance`-carrying offer as non-official and review-worthy,
  mirroring section 73's rule for scraped accommodation data.
- Not wired into `ProviderGateway`, `PlanningOrchestrator`,
  `TripStrategyService`, `StayTransportService`, `PlanValidatorService`,
  the AI candidate-proposal subsystem, or any AI reasoning contract model
  (`ai_reasoning.py`, `ai_candidate_proposal.py`) -- nothing in the app's
  AI-adjacent code currently constructs a `FlightSearchRequest` or
  consumes a `FlightSearchResult`.
- Confirmed unchanged: route-aware scheduling, regeneration refusal, and
  LangGraph's continued absence from `/generate`; no Groq/Anthropic/
  Kiwi/MCP call exists anywhere in the new modules.

## 76. Flight Provider Config, Not-Connected Adapter, Scraped-Local Stub, and Factory (Step 169B)

Step 169B adds flight provider config (`Settings.flight_provider`,
default `"scraped_local"`), `NotConnectedFlightProvider`,
`ScrapedLocalFlightProvider` (a stub -- no parser yet), and
`get_flight_provider` (docs/12_provider_architecture.md section 53).
**Flight provider selection is deterministic provider infrastructure,
not AI reasoning.** No LLM call, no prompt, no model inference exists in
any of these modules.

- **LLMs must not invent a missing flight field here either.**
  `NotConnectedFlightProvider` always returns `offers=[]`; `Scraped
  LocalFlightProvider`, even when a local file exists, returns
  `offers=[]` with an honest "parsing is not implemented yet" message
  rather than a filled-in guess -- there is no post-lookup step in
  either adapter that could fill in a missing airline/flight number/
  airport/time/duration/price/availability/baggage policy/cancellation
  policy/booking link, and none may ever be added.
- **The factory's fallback-to-`not_connected` behavior is not an AI
  judgment call either** -- `get_flight_provider` is a plain dict lookup
  (`_SUPPORTED_PROVIDERS.get(name, NotConnectedFlightProvider)`), with no
  branch that could be swapped for an AI-driven provider choice.
- Not wired into `ProviderGateway`, `PlanningOrchestrator`,
  `TripStrategyService`, `StayTransportService`, `PlanValidatorService`,
  or any AI reasoning contract model -- confirmed by dedicated tests
  (`test_provider_gateway_does_not_reference_flight_factory`,
  `test_planning_orchestrator_does_not_reference_flight_factory` in
  `backend/app/tests/providers/test_flight_factory.py`).
- Confirmed by dedicated import-safety tests that none of
  `backend/app/providers/flights/{factory,not_connected_adapter,
  scraped_adapter}.py` imports `httpx`/`requests`/a browser-automation
  library/Groq/Anthropic/Kiwi/MCP -- mirroring every prior Section 167/168
  provider-selection step's own import-safety checks.

## 77. Static Flight HTML Parser (Step 169C)

Step 169C adds `backend/app/providers/flights/scraped_parser.py`'s
`parse_scraped_flight_html`, the flight equivalent of the Step 168B
accommodation parser (docs/12_provider_architecture.md section 54).
**This is deterministic provider infrastructure -- a stdlib HTML walk
plus pydantic validation -- not AI reasoning.** No LLM call, no prompt,
no model inference exists anywhere in this module, and it never fetches
a live website; it only transforms an already-provided HTML string.

- **LLMs must not fill any missing scraped flight field.** A field class
  absent from the HTML (origin/destination airport, departure/arrival
  time, carrier name/code, flight number, duration, price, currency,
  availability status, baggage policy, cancellation policy, booking URL)
  stays `None`/empty on the resulting `FlightSegment`/`FlightOffer` --
  there is no post-parse step in this module, or anywhere downstream
  today, that could fill one in. This mirrors section 68's rule for
  scraped accommodation data exactly, extended to flights.
- **Parsed flight data must be treated as non-official and
  review-worthy**, the same way section 73 already established for
  scraped accommodation data -- every parsed `FlightOffer` carries
  `data_status=scraped_public_page` and `scraped_provenance.
  official_provider=False`; any future AI reasoning step that ever reads
  flight inventory must never present, summarize, or reason about a
  scraped flight offer as if it were official-provider data.
- Not wired into `ScrapedLocalFlightProvider`, `ProviderGateway`,
  `PlanningOrchestrator`, or any AI reasoning/candidate-proposal contract
  model -- nothing in the app's AI-adjacent code, or the flight provider
  itself, currently calls `parse_scraped_flight_html` outside this
  subsystem's own tests (wiring the provider is Step 169D).
- Confirmed by dedicated import-safety tests that
  `scraped_parser.py` imports no `httpx`/`requests`/a browser-automation
  library/Groq/Anthropic/Kiwi/MCP.

## 78. Scraped Local Flight Provider Wired to the Parser and Cache (Step 169D)

Step 169D wires `ScrapedLocalFlightProvider`
(`backend/app/providers/flights/scraped_adapter.py`) to
`parse_scraped_flight_html` (section 77) and a `ProviderCacheStore`-backed
cache (docs/12_provider_architecture.md section 55). **Scraped flight
parsing and caching are deterministic provider infrastructure -- a local
file read, a stdlib HTML walk, pydantic validation, and a SQLite
key/value cache -- not AI reasoning.** No LLM call, no prompt, no model
inference exists anywhere in this flow.

- **LLMs must not fill any missing scraped flight field, on a fresh
  parse or a cache hit.** A field absent from the source HTML stays
  `None`/empty at every hop -- through the parser, through the cache
  round-trip (`FlightSearchResult.model_validate(entry.payload)`), and
  out of `ScrapedLocalFlightProvider.search_flights`'s return value.
  There is no post-parse or post-cache-read step anywhere in this
  provider that could fill one in, mirroring section 71's rule for
  cached scraped accommodation data exactly.
- **LLMs must treat `scraped_public_page` flight data as non-official
  and review-worthy**, on a fresh parse or a cache hit alike -- every
  returned `FlightOffer` carries `data_status=scraped_public_page` and
  `scraped_provenance.official_provider=False` regardless of whether it
  came from a fresh parse or the cache; any future AI reasoning step
  that ever reads flight inventory must never present, summarize, or
  reason about it as if it were official-provider data, mirroring
  section 73's rule for scraped accommodation data.
- Not wired into `ProviderGateway`, `PlanningOrchestrator`, or any AI
  reasoning/candidate-proposal contract model -- nothing in the app's
  AI-adjacent code calls `ScrapedLocalFlightProvider.search_flights`
  outside this subsystem's own tests.
- Confirmed by a dedicated import-safety test that `scraped_adapter.py`
  imports no `requests`/a browser-automation library/Groq/Anthropic/
  Kiwi/MCP (it does import the stdlib-only `scraped_parser` and the
  existing `ProviderCacheStore`, both already covered by their own
  import-safety tests).

## 79. Flight Inventory Wired Through the Full App (Step 169E, final Section 169 step)

Step 169E wires `ProviderGateway.search_flights`,
`FlightInventoryService`, `PlanningState.flight_inventory_report`,
`ProviderCoverage.flights`, `PlanValidatorService`'s non-blocking
`flight_inventory` warning, and frontend display
(docs/12_provider_architecture.md section 56). **Every piece of this is
deterministic provider infrastructure -- gateway delegation, request
mapping from existing trip fields, pydantic-validated reporting, string
coverage mapping, template-based warning text, and React rendering of
already-validated backend fields -- not AI reasoning.** No LLM call, no
prompt, no model inference exists anywhere in this flow.

- **LLMs must not fill any missing flight field, at any point in this
  flow.** `FlightInventoryService` never invents an `origin` when
  `TripRequest.origin_city` is unset; `FlightInventoryProvider`
  implementations never invent an airline/flight number/airport/time/
  duration/price/availability/baggage policy/cancellation policy/
  booking link; and the frontend never renders a missing field as if
  present. There is no post-lookup, post-report, or post-render step
  anywhere in this chain that could fill one in, mirroring every prior
  Section 167/168/169 step's rule.
- **LLMs must treat `scraped_public_page` flight data as non-official
  and review-worthy**, exactly as sections 73/78 already established for
  scraped accommodation/flight data -- `PlanValidatorService`'s
  `flight_inventory` warning says so explicitly for any offer carrying
  `scraped_provenance`, and any future AI reasoning step that ever reads
  `PlanningState.flight_inventory_report` must honor the same
  distinction. This is also enforced client-side:
  `ScrapedFlightProvenanceBadge` always renders the "not
  official-provider data...not verified" sentence alongside any scraped
  offer, and a flight offer is never scheduled into a day card in the
  itinerary.
- Confirmed unchanged: route-aware scheduling (Section 165/166),
  regeneration refusal (`409 REGENERATION_NOT_AVAILABLE`), and
  LangGraph's continued absence from `/generate` all still hold with
  `scraped_local` flight inventory explicitly enabled and populated.