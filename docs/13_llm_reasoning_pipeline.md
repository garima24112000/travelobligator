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