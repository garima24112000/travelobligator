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