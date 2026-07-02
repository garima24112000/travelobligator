# Pipeline Data Flow

## 1. Purpose

This document defines how data moves through the TravelObligator planning pipeline.

The goal is to make every planning stage clear, connected, and implementation-ready.

This document answers:

* What does each stage consume?
* What does each stage produce?
* Which outputs are passed forward?
* Which stages use AI?
* Which stages use deterministic rules?
* Which stages depend on external providers?
* Which object owns the current planning state?

The pipeline should avoid passing disconnected objects between services.

Instead, the system should carry one evolving `PlanningState` object through the planning process.

---

## 2. End-to-End Pipeline

The MVP planning flow is:

```text
User Input
→ Traveler Profile
→ Planning State
→ Trip Strategy
→ Stay + Transport
→ Experience Planner
→ Plan Validator
→ Feedback Pipeline
→ Updated Planning State
→ Final Itinerary
```

The itinerary is not generated directly from user input.

It is generated after the system has:

1. understood the traveler
2. created a trip strategy
3. decided stay and transport approach
4. selected and scheduled experiences
5. validated the plan
6. handled feedback when needed

---

## 3. Central Object: Planning State

`PlanningState` is the central object passed through the pipeline.

Each stage reads from it, updates its own section, and passes it forward.

Example structure:

```json
{
  "trip_request": {},
  "traveler_profile": {},
  "trip_strategy": {},
  "stay_transport": {},
  "experience_plan": {},
  "validation_report": {},
  "feedback_history": [],
  "decision_cards": [],
  "experience_cards": [],
  "validation_cards": [],
  "metadata": {},
  "version_history": []
}
```

The Planning State should prevent scattered data ownership.

Each stage should update only the section it owns.

---

## 4. Stage 1: User Input → Traveler Profile

### Consumes

Raw user input from the trip form:

* destination
* origin city
* dates
* number of travelers
* group type
* budget
* accommodation preference
* transport preference
* interests
* must-visit places
* must-avoid places
* constraints
* free-text preferences
* itinerary intensity scale

### Produces

`traveler_profile`

Includes:

* basic context
* travel intent
* budget profile
* mobility profile
* interest profile
* stay profile
* transport profile
* avoidance profile
* decision weights
* confidence levels

### Uses AI?

Yes.

AI may be used to interpret free-text preferences and convert them into structured signals.

Example:

User says:

> “I’m traveling with my parents and we don’t want too much walking.”

AI can infer:

* parent-friendly trip
* higher comfort priority
* lower walking tolerance
* lower nightlife weight

### Uses Deterministic Rules?

Yes.

Rules should be used for:

* calculating trip duration
* validating date order
* mapping intensity scale to pace
* normalizing budget tier
* trimming empty inputs

### External Providers?

No.

This stage should not call external travel providers.

### Passed Forward

The full `traveler_profile` is passed to all later stages.

---

## 5. Stage 2: Traveler Profile → Trip Strategy

### Consumes

From `traveler_profile`:

* destination
* trip duration
* travel group type
* budget profile
* mobility profile
* interest profile
* travel intent
* decision weights
* confidence levels

Optional provider data:

* destination overview
* average cost estimates
* attraction density
* transport quality
* seasonal context
* safety/neighborhood context

### Produces

`trip_strategy`

Includes:

* destination suitability
* duration assessment
* budget assessment
* recommended trip style
* planning strategy
* tradeoffs
* assumptions
* confidence score

### Uses AI?

Yes.

AI may be used to reason about destination suitability and trip style.

However, AI should not invent factual claims.

If destination data is unavailable, the strategy should include assumptions.

### Uses Deterministic Rules?

Yes.

Rules should be used for:

* comparing duration against minimum/ideal days
* comparing budget against estimated trip cost
* mapping intensity scale to daily planning density
* detecting obvious mismatch between trip length and expectations

### External Providers?

Optional.

Possible providers:

* Places provider for attraction density
* Accommodation provider for average hotel cost
* Routes provider for local transport feasibility
* Weather provider in future versions

### Passed Forward

The full `trip_strategy` is passed to:

* Stay + Transport
* Experience Planner
* Plan Validator
* Feedback Pipeline

---

## 6. Stage 3: Trip Strategy → Stay + Transport

### Consumes

From `traveler_profile`:

* budget profile
* stay profile
* transport profile
* mobility profile
* decision weights
* avoidance profile

From `trip_strategy`:

* recommended trip style
* planning strategy
* budget assessment
* destination suitability
* tradeoffs
* assumptions

Provider data:

* neighborhood data
* accommodation availability
* accommodation prices
* public transport access
* airport/train station access
* attraction clusters
* route/travel-time data

### Produces

`stay_transport`

Includes:

* recommended stay area
* alternative stay areas
* stay area decision card
* transport strategy
* transport decision card
* accommodation recommendations
* tradeoffs
* confidence score

### Uses AI?

Yes.

AI may be used to explain stay and transport tradeoffs.

Example:

> “Stay central because this traveler values comfort, safety, and lower daily travel time.”

### Uses Deterministic Rules?

Yes.

Rules should be used for:

* scoring neighborhoods
* filtering accommodations by budget
* calculating proximity to attraction clusters
* comparing travel times
* checking parking relevance
* checking public transport access

### External Providers?

Yes.

Possible providers:

* Google Places API
* Google Routes API
* Amadeus Hotels API
* Booking or Expedia provider later
* OpenStreetMap fallback

### Passed Forward

The `stay_transport` output is passed to:

* Experience Planner
* Plan Validator
* Feedback Pipeline

---

## 7. Stage 4: Stay + Transport → Experience Planner

### Consumes

From `traveler_profile`:

* interests
* travel intent
* mobility profile
* decision weights
* avoidance profile
* must-visit places
* must-avoid places

From `trip_strategy`:

* recommended trip style
* planning rules
* duration assessment
* budget assessment
* assumptions

From `stay_transport`:

* recommended stay area
* transport strategy
* accommodation location
* alternative stay areas if needed

Provider data:

* attractions
* restaurants/food areas
* coordinates
* ratings
* opening hours
* estimated visit duration
* route/travel times
* activity costs
* indoor/outdoor classification

### Produces

`experience_plan`

Includes:

* trip overview
* daily plan
* day summaries
* selected experiences
* experience cards
* decision cards
* estimated walking
* estimated travel time
* estimated cost
* planning metadata

### Uses AI?

Yes.

AI may be used for:

* selecting experiences that fit the traveler
* explaining why experiences were included
* creating day themes
* balancing variety and traveler intent

AI should not invent attraction details, opening hours, prices, or coordinates.

### Uses Deterministic Rules?

Yes.

Rules should be used for:

* grouping nearby attractions
* estimating daily walking
* limiting number of activities based on intensity scale
* respecting opening hours
* preventing must-avoid experiences
* preserving must-visit places
* estimating daily time windows

### External Providers?

Yes.

Possible providers:

* Google Places API
* Google Routes API
* Mapbox Directions fallback
* OpenStreetMap fallback
* Weather provider in future versions

### Passed Forward

The full `experience_plan` is passed to:

* Plan Validator
* Feedback Pipeline
* Final Itinerary renderer

---

## 8. Stage 5: Experience Planner → Plan Validator

### Consumes

From `traveler_profile`:

* mobility profile
* travel intent
* budget profile
* safety weight
* comfort weight
* decision weights
* constraints

From `trip_strategy`:

* planning strategy
* assumptions
* duration assessment
* budget assessment

From `stay_transport`:

* stay area
* transport strategy
* accommodation location

From `experience_plan`:

* daily plan
* activities
* timing
* route estimates
* walking estimates
* cost estimates
* experience cards

Provider data:

* route times
* walking distances
* attraction opening hours
* transit availability
* accessibility metadata

### Produces

`validation_report`

Includes:

* overall score
* category scores
* validation cards
* critical issues
* warnings
* suggestions
* validation summary
* planning metadata

### Uses AI?

Yes, but only after deterministic checks.

AI may be used for subjective travel-quality reasoning.

Examples:

* Does the day feel too exhausting?
* Are experiences too repetitive?
* Does the plan match the traveler’s intent?
* Is the arrival day too ambitious?

AI may only reason from existing planning state and provider data.

It should not invent facts.

### Uses Deterministic Rules?

Yes.

Rules should be used for:

* budget exceeded
* excessive walking
* attraction closed
* impossible timing
* excessive travel time
* too many activities
* missing meal breaks
* unsafe late-night movement where known

### External Providers?

Yes.

Possible providers:

* Google Routes API
* Google Places API
* Calendar/holiday provider later
* Weather provider later

### Passed Forward

The `validation_report` is passed to:

* Feedback Pipeline
* Dashboard
* Final Itinerary renderer

The validator does not modify the itinerary.

---

## 9. Stage 6: Plan Validator → Feedback Pipeline

### Consumes

The full `PlanningState`, including:

* traveler profile
* trip strategy
* stay transport decisions
* experience plan
* validation report
* decision cards
* experience cards
* validation cards
* current itinerary version

Also consumes:

* user feedback text
* quick action feedback
* user-approved sections
* user-rejected sections

### Produces

Updated `PlanningState`

May include:

* updated traveler profile
* updated trip strategy
* updated stay transport
* updated experience plan
* updated validation report
* feedback interpretation
* affected stages
* regeneration strategy
* change summary
* new version entry

### Uses AI?

Yes.

AI may be used for:

* interpreting natural language feedback
* classifying feedback type
* identifying affected stages
* explaining what changed

### Uses Deterministic Rules?

Yes.

Rules should be used for:

* deciding whether full regeneration is needed
* preserving user-approved sections
* preventing removal of must-visit items without confirmation
* versioning
* comparing old vs new plan

### External Providers?

Only when feedback requires updated data.

Examples:

* “Find cheaper hotels” needs accommodation provider.
* “Reduce walking” needs routes provider.
* “Remove museums” may need places provider for alternatives.
* “Change transport to train” may need route/transit provider.

### Passed Forward

The updated `PlanningState` is passed back through the necessary affected stages.

---

## 10. Stage 7: Feedback Pipeline → Updated Plan

The Feedback Pipeline should not always restart the full pipeline.

It should choose the smallest valid regeneration path.

### Possible Paths

#### Explanation Only

Used when the user asks why something was recommended.

Example:

> “Why did you choose this area?”

No regeneration needed.

#### Section-Level Update

Used when one section changes.

Example:

> “Show cheaper hotels.”

Affected stage:

* Stay + Transport

#### Day-Level Update

Used when one day is problematic.

Example:

> “Day 2 is too packed.”

Affected stages:

* Experience Planner
* Plan Validator

#### Pipeline-Level Update

Used when the traveler profile changes.

Example:

> “Actually, this is a luxury trip.”

Affected stages:

* Traveler Profile
* Trip Strategy
* Stay + Transport
* Experience Planner
* Plan Validator

Full regeneration should be the last resort.

---

## 11. Shared Objects

The pipeline should use common object types across stages.

### PlanningState

The central state object.

### TravelerProfile

Structured understanding of the traveler.

### TripStrategy

High-level direction for the trip.

### StayTransportDecision

Stay area, accommodation, and movement strategy.

### ExperiencePlan

Selected and scheduled experiences.

### ValidationReport

Review of itinerary quality and feasibility.

### DecisionCard

Explains major recommendations.

Used by:

* Trip Strategy
* Stay + Transport
* Experience Planner
* Feedback Pipeline

### ExperienceCard

Explains why an activity was included.

Used by:

* Experience Planner

### ValidationCard

Explains a plan issue or warning.

Used by:

* Plan Validator

### PlanningMetadata

Tracks:

* data sources
* confidence
* assumptions
* generated timestamp
* provider status
* version number

---

## 12. Data Ownership Rules

Each stage owns only its own output.

### Traveler Profile Owns

* traveler intent
* preferences
* constraints
* decision weights
* confidence levels

### Trip Strategy Owns

* destination suitability
* duration assessment
* budget assessment
* planning strategy
* trip style

### Stay + Transport Owns

* recommended stay area
* accommodation ranking
* transport strategy

### Experience Planner Owns

* selected experiences
* daily plan
* experience cards
* trip overview

### Plan Validator Owns

* validation scores
* validation cards
* critical issues
* warnings
* suggestions

### Feedback Pipeline Owns

* feedback interpretation
* affected stages
* regeneration strategy
* change summary
* version history updates

No stage should overwrite another stage’s output unless it is explicitly part of a regeneration path.

---

## 13. AI vs Deterministic Responsibility

### AI Should Handle

* free-text interpretation
* preference inference
* explanation generation
* subjective trip-quality reasoning
* feedback interpretation
* decision card wording

### Deterministic Logic Should Handle

* date calculations
* budget comparisons
* distance and route calculations
* activity count limits
* provider data filtering
* schema validation
* versioning
* scoring aggregation

### Provider Data Should Handle

* places
* coordinates
* routes
* opening hours
* accommodation availability
* hotel prices
* transit feasibility where available

### The System Should Never Use AI To Invent

* opening hours
* ticket prices
* hotel availability
* live transport schedules
* exact walking distances
* real-time fare estimates
* safety claims without source data

---

## 14. Confidence and Assumptions

Every major stage should return:

* confidence score
* assumptions
* data sources used
* missing data warnings

If confidence is low, the system should:

* explain uncertainty
* avoid strong claims
* ask follow-up questions when needed
* continue with conservative assumptions only when safe

---

## 15. Design Principles

The pipeline should follow these principles:

* One central Planning State should flow through the system.
* Each stage should update only the section it owns.
* Every major recommendation should be explainable.
* Provider data supplies facts.
* AI supplies interpretation and explanation.
* Deterministic logic supplies validation and control.
* Feedback should trigger the smallest valid update path.
* The itinerary is the final artifact, not the system’s source of intelligence.