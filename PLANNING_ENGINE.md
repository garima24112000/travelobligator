# TravelObligator Planning Engine

The Planning Engine is the core intelligence layer of TravelObligator.

It does not simply ask an LLM to generate an itinerary.

It runs a staged decision pipeline that turns user input into an explainable, realistic, validated travel plan.

---

## 1. Core Idea

The Planning Engine follows this flow:

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

The itinerary is the final output.

The Planning Engine is responsible for the decisions that create it.

---

## 2. Central Object

The Planning Engine operates on one central object:

```text
PlanningState
```

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
- decision cards
- experience cards
- validation cards
- provider status
- provider coverage
- unavailable data
- data sources used
- version history

Each stage reads PlanningState, updates only the section it owns, and passes the updated state forward.

---

## 3. Stage 1: Traveler Profile

Purpose:

Convert raw user input into structured traveler preferences.

Consumes:

- trip request
- trip dates
- destination
- group type
- budget
- accommodation preference
- transport preference
- interests
- constraints
- free-text preferences
- intensity scale

Produces:

- traveler profile
- decision weights
- mobility profile
- budget profile
- stay profile
- transport profile
- interest profile
- avoidance profile
- confidence levels
- assumptions

AI may help interpret free text.

AI must not invent user constraints or overstate confidence.

---

## 4. Stage 2: Destination Context

Purpose:

Build a provider-backed or open-data-backed snapshot of the destination.

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
- data sources used

Destination Context is not the final itinerary.

It should not select:

- final attractions
- final restaurants
- final accommodation options
- final day plans

It only gives later stages reliable candidate data.

---

## 5. Stage 3: Trip Strategy

Purpose:

Define the planning direction before the itinerary is created.

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

Planning targets may include:

- preferred activities per day
- maximum activities per day
- maximum walking per day
- sightseeing start and end time
- meal break expectations
- buffer level
- experience mix targets

Trip Strategy should not choose final attractions, restaurants, or accommodation options.

---

## 6. Stage 4: Stay + Transport

Purpose:

Decide where the traveler should stay and how they should move around.

Consumes:

- traveler profile
- destination context
- trip strategy
- provider coverage

Produces:

- recommended stay area
- alternative stay areas
- transport strategy
- top accommodation options
- stay decision cards
- transport decision cards
- accommodation ranking explanations
- unavailable accommodation fields
- confidence

Important rule:

The system should recommend stay areas before recommending individual accommodation options.

Accommodation options may include:

- hotels
- motels
- hostels
- resorts
- serviced apartments
- guesthouses
- boutique stays
- vacation rentals
- Airbnb-style stays only when supported by approved access

The MVP recommends accommodation options.

It does not guarantee final booking, price, cancellation terms, or availability unless confirmed by a provider.

---

## 7. Stage 5: Experience Planner

Purpose:

Create the day-wise experience plan.

Consumes:

- traveler profile
- destination context
- trip strategy
- stay and transport decisions
- provider coverage
- unavailable data

Produces:

- trip overview
- daily plan
- selected experiences
- meal plan
- restaurant recommendations when provider-backed or open-data-backed
- meal-area fallback when restaurant data is insufficient
- experience cards
- decision cards
- estimated walking
- estimated travel time
- estimated cost
- confidence

The Experience Planner should optimize for traveler satisfaction, not attraction count.

It must not invent:

- attractions
- restaurants
- ratings
- review counts
- opening hours
- prices
- availability
- exact route times
- exact walking distances

Estimated values are allowed only when clearly marked as estimated.

---

## 8. Stage 6: Plan Validator

Purpose:

Review the itinerary before it is shown as final.

Consumes:

- traveler profile
- destination context
- trip strategy
- stay and transport decisions
- experience plan
- provider status
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

Validation should check:

- route feasibility
- walking burden
- timing realism
- budget alignment
- pace
- meal breaks
- experience variety
- safety-related planning considerations
- provider coverage limitations

The validator does not modify the itinerary directly.

It identifies problems and suggests fixes.

---

## 9. Stage 7: Feedback Pipeline

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

Feedback should trigger the smallest valid update path.

Examples:

```text
“Remove museums”
→ update Experience Planner and Plan Validator
```

```text
“Show cheaper accommodation options”
→ update Stay + Transport and Plan Validator
```

```text
“My parents cannot walk much”
→ update Traveler Profile and all downstream stages
```

The system should not regenerate everything by default.

---

## 10. User Locks

Users can lock parts of the plan they want to keep.

Lockable items:

- stay area
- accommodation
- experience
- restaurant
- day plan
- transport strategy

Locked items should not be changed unless:

- the user directly asks to change them
- the item becomes infeasible
- provider data shows the item is unavailable
- the lock conflicts with a higher-priority constraint

If a locked item must change, the system should explain why.

---

## 11. Explanation Cards

The Planning Engine should generate explanation cards.

Card types:

```text
DecisionCard
ExperienceCard
ValidationCard
```

Cards should explain:

- what is recommended
- why it fits
- what tradeoffs exist
- what alternatives exist
- what data supports it
- what assumptions were made
- how confident the system is

Every important claim should include a source type:

```text
provider_fact
open_data_fact
user_input
system_rule
ai_inference
assumption
unavailable_data
```

---

## 12. Provider Coverage

Provider coverage explains what data was actually available.

Example:

```json
{
  "places": "available",
  "routes": "available",
  "restaurants": "open_data_available",
  "accommodations": "open_poi_available",
  "hotel_prices": "provider_available",
  "vacation_rentals": "not_connected",
  "airbnb": "not_connected",
  "flights": "not_enabled",
  "weather": "available"
}
```

The Planning Engine must not imply that a provider was searched when it was not connected.

Unavailable data should be visible, not hidden.

---

## 13. Legit-Only Data Rule

The Planning Engine must not use:

- mock accommodation listings
- mock restaurant ratings
- mock prices
- mock availability
- scraped restricted provider data
- AI-invented factual travel data

If data is missing, mark it as:

```text
unavailable
not_connected
estimated
low confidence
```

Do not replace it with fake values.

Unavailable data is acceptable.

Fake data is not.

---

## 14. AI Role

AI is allowed to help with:

- free-text interpretation
- preference inference
- explanation writing
- trip strategy reasoning
- subjective validation reasoning
- feedback interpretation
- change summaries

AI is not allowed to invent factual travel data.

AI must not invent:

- places
- restaurants
- accommodations
- flights
- ratings
- review counts
- prices
- opening hours
- availability
- route times
- walking distances
- booking links
- provider coverage
- safety ratings

AI output should be structured and schema-validated.

---

## 15. Safety Policy

The Planning Engine does not generate direct safety scores in the MVP.

It should identify safety-related planning considerations such as:

- late-night travel
- long walking segments
- poor transit alignment
- remote or isolated movement
- weather exposure
- traveler-specific comfort constraints
- route uncertainty
- low provider confidence

Allowed wording:

```text
This route may require late-night walking and limited transit alignment.
```

Avoid:

```text
This area is unsafe.
```

unless supported by authoritative data and the product is explicitly designed for that use case.

---

## 16. Regeneration Strategy

Feedback regeneration should use one of these strategies:

```text
explanation_only
section_level_update
day_level_update
pipeline_level_update
full_regeneration
```

Full regeneration should be the last resort.

Use it only when:

- destination changes
- dates change significantly
- trip duration changes
- core traveler profile changes
- previous state is no longer valid

---

## 17. Validation Before Presentation

The Planning Engine should validate before presenting the plan as final.

If the plan is:

```text
ready
```

The frontend can show it confidently.

If the plan is:

```text
needs_review
```

The frontend should show warnings clearly.

If the plan is:

```text
blocked
```

The frontend should not present it as final until critical issues are resolved.

---

## 18. Planning Engine Output

The final Planning Engine output is the updated PlanningState.

The frontend should render from PlanningState instead of scattered endpoint responses.

Main frontend sections should come from:

```text
trip_request
trip_strategy
stay_transport
experience_plan
validation_report
decision_cards
experience_cards
validation_cards
provider_coverage
unavailable_data
feedback_history
version_history
```

---

## 19. Implementation Direction

The Planning Engine should be implemented through backend services:

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

The PlanningOrchestrator should control stage order.

Individual services should own stage-specific logic.

Providers should be accessed only through ProviderGateway.

---

## 20. Design Principles

- Decisions before itinerary.
- PlanningState is the source of truth.
- Each stage owns one section.
- Destination Context prevents circular dependency.
- Providers and open data supply facts.
- AI supplies reasoning and explanation.
- Missing data must be explicit.
- Provider coverage must be visible.
- Feedback should update only affected sections where possible.
- User locks should preserve approved items.
- Validation should happen before final presentation.
- The itinerary is the final artifact, not the planning engine itself.