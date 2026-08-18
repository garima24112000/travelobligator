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