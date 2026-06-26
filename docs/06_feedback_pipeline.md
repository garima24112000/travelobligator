# Feedback Pipeline

## 1. Purpose

The Feedback Pipeline is responsible for updating a travel plan when the user dislikes, changes, or refines part of the itinerary.

This stage is one of the main differentiators of Travel Copilot.

Most itinerary generators regenerate the entire plan when feedback is given.

Travel Copilot should instead:
- understand what the feedback means
- identify which stages are affected
- preserve the parts that still work
- regenerate only the necessary sections
- explain what changed and why

The goal is controlled adaptation, not blind regeneration.

---

## 2. Inputs

The Feedback Pipeline receives:

### User Feedback

Examples:
- make it less hectic
- remove museums
- reduce walking
- make it cheaper
- add more food places
- avoid late nights
- change hotel area
- use public transport instead of taxis
- add more hidden gems

### Existing Planning State

The pipeline should have access to:
- Traveler Profile
- Trip Strategy
- Stay & Transport Decisions
- Experience Planner Output
- Plan Validator Report
- Current Itinerary Version

---

## 3. Feedback Interpretation

The system should convert raw feedback into structured feedback intent.

Example:

User says:

“Make this less tiring for my parents.”

Structured interpretation:

```json
{
  "feedback_type": "pace_adjustment",
  "affected_preferences": {
    "intensity_scale": 2,
    "walking_tolerance": "lower",
    "comfort_weight": "increase"
  },
  "affected_stages": [
    "traveler_profile",
    "experience_planner",
    "plan_validator"
  ]
}
```

## # Feedback Pipeline

## 1. Purpose

The Feedback Pipeline is responsible for updating a travel plan when the user dislikes, changes, or refines part of the itinerary.

This stage is one of the main differentiators of Travel Copilot.

Most itinerary generators regenerate the entire plan when feedback is given.

Travel Copilot should instead:
- understand what the feedback means
- identify which stages are affected
- preserve the parts that still work
- regenerate only the necessary sections
- explain what changed and why

The goal is controlled adaptation, not blind regeneration.

---

## 2. Inputs

The Feedback Pipeline receives:

### User Feedback

Examples:
- make it less hectic
- remove museums
- reduce walking
- make it cheaper
- add more food places
- avoid late nights
- change hotel area
- use public transport instead of taxis
- add more hidden gems

### Existing Planning State

The pipeline should have access to:
- Traveler Profile
- Trip Strategy
- Stay & Transport Decisions
- Experience Planner Output
- Plan Validator Report
- Current Itinerary Version

---

## 3. Feedback Interpretation

The system should convert raw feedback into structured feedback intent.

Example:

User says:

“Make this less tiring for my parents.”

Structured interpretation:

```json
{
  "feedback_type": "pace_adjustment",
  "affected_preferences": {
    "intensity_scale": 2,
    "walking_tolerance": "lower",
    "comfort_weight": "increase"
  },
  "affected_stages": [
    "traveler_profile",
    "experience_planner",
    "plan_validator"
  ]
}
```

## 9. Feedback Decision Card

Feedback updates should produce a Decision Card.

Example:

Title

Updated Day 2 to Reduce Fatigue

Recommendation

Replace one museum with a shorter scenic stop and add a longer rest break.

Why

User requested a less tiring plan.
Traveler Profile includes parent-friendly comfort.
Validator warned that Day 2 had high walking.

Tradeoff

The updated plan includes fewer indoor cultural attractions.

Confidence

0.89

10. Versioning

Every regenerated plan should create a new itinerary version.

The system should store:

original itinerary
feedback text
structured feedback intent
affected stages
new itinerary version
change summary
validation report

This allows users to compare versions later.

11. Output Contract

The Feedback Pipeline should return:

```json
{
  "feedback_interpretation": {},
  "affected_stages": [],
  "regeneration_strategy": "",
  "updated_traveler_profile": {},
  "updated_trip_strategy": {},
  "updated_stay_transport": {},
  "updated_experience_plan": {},
  "updated_validation_report": {},
  "change_summary": {},
  "decision_cards": [],
  "new_itinerary_version": {},
  "planning_metadata": {}
}
```

12. Production Considerations

The Feedback Pipeline should use:

AI

Used for:

interpreting natural language feedback
identifying affected stages
explaining changes
Deterministic Logic

Used for:

deciding when full regeneration is required
preserving unchanged sections
comparing old and new plans
versioning
Provider Data

May be needed when feedback affects:

routes
hotels
attractions
transport strategy
13. Edge Cases

The pipeline should handle:

vague feedback
contradictory feedback
feedback that violates constraints
feedback that conflicts with must-visit items
feedback that requires unavailable provider data
feedback that makes the trip impossible within budget/time

If feedback is unclear, the system should ask a follow-up question instead of guessing.

Example:

“I want it better.”

Response:

“What would you like improved: pace, cost, hotels, transport, food, or activities?”

14. Design Principles

The Feedback Pipeline should follow these principles:

Do not regenerate everything by default.
Preserve what the user already likes.
Explain every change.
Ask follow-up questions when feedback is ambiguous.
Treat feedback as a structured planning update.
Maintain version history.
Validate the updated plan before presenting it.
Never silently remove must-visit items.

