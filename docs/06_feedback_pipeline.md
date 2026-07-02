# Feedback Pipeline

## 1. Purpose

The Feedback Pipeline is responsible for updating a travel plan when the user dislikes, changes, or refines part of the itinerary.

This stage is one of the main differentiators of TravelObligator.

Most itinerary generators regenerate the entire plan when feedback is given.

TravelObligator should instead:

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
- change accommodation area
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

## 4. Feedback Categories

Feedback should be classified into categories.

### Profile Feedback

Changes the Traveler Profile.

Examples:

- actually we want a relaxed trip
- my parents cannot walk much
- we prefer vegetarian food
- we do not want nightlife

### Stay Feedback

Changes stay area or accommodation logic.

Examples:

- cheaper accommodation
- safer area
- closer to metro
- avoid downtown
- need parking

### Transport Feedback

Changes transport strategy.

Examples:

- avoid driving
- use trains
- reduce Uber
- prefer walking
- do not use public transport at night

### Experience Feedback

Changes selected activities.

Examples:

- remove museums
- add hidden gems
- more food spots
- more nature
- less shopping

### Schedule Feedback

Changes timing and density.

Examples:

- make days lighter
- start later
- keep evenings free
- add rest breaks

### Budget Feedback

Changes cost decisions.

Examples:

- make it cheaper
- allow luxury options
- avoid paid attractions

## 5. Affected Stage Detection

The pipeline should determine which parts of the planning state need to be updated.

Example:

Feedback:

“Remove museums.”

Affected:

Experience Planner
Plan Validator

Not affected:

Stay Area
Transport Strategy
Budget Profile unless museum tickets were a major cost

Example:

Feedback:

“Stay somewhere cheaper.”

Affected:

Stay & Transport Decisions
Budget Assessment
Plan Validator

Not affected:

Most experience selections unless travel time changes significantly

## User Locks

User-approved sections should be stored as locks.

Locked items should not be changed unless the user directly asks for it or the lock conflicts with a higher-priority constraint.

Examples:
- locked stay area
- locked acccommodation
- locked experience
- locked day plan

## 6. Regeneration Strategy

The system should support partial regeneration.

Possible strategies:

No Regeneration Needed

Used when feedback can be handled with explanation only.

Example:
“Why did you choose this area?”

Section-Level Regeneration

Used when one part changes.

Example:
“Replace this accommodation.”

Day-Level Regeneration

Used when one day is problematic.

Example:
“Day 2 is too packed.”

Pipeline-Level Regeneration

Used when core traveler profile changes.

Example:
“Actually this is a luxury trip, not budget.”

Full regeneration should be the last resort.

## 7. Change Preservation

The system should preserve valid parts of the itinerary.

It should not discard:

- accepted days
- selected stay area
- transport strategy
- must-visit activities
- user-approved recommendations

Unless the feedback directly conflicts with them.

## 8. Change Explanation

Every feedback response should explain:

- what changed
- why it changed
- what stayed the same
- which stages were affected
- whether the validation score improved or declined

Example:

```json
{
  "change_summary": {
    "changed": [
      "Removed two museum activities.",
      "Added a food market and scenic viewpoint.",
      "Reduced Day 2 walking distance."
    ],
    "unchanged": [
      "Stay area remained Dupont Circle.",
      "Transport strategy remained Metro + rideshare."
    ],
    "reason": "The user asked for fewer museums and a less tiring plan."
  }
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

## 10. Versioning

Every regenerated plan should create a new itinerary version.

The system should store:

- original itinerary
- feedback text
- structured feedback intent
- affected stages
- new itinerary version
- change summary
- validation report

This allows users to compare versions later.

## 11. Output Contract

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

## 12. Production Considerations

The Feedback Pipeline should use:

### AI

Used for:

- interpreting natural language feedback
- identifying affected stages
- explaining changes

### Deterministic Logic

Used for:

- deciding when full regeneration is required
- preserving unchanged sections
- comparing old and new plans
- versioning

### Provider Data

May be needed when feedback affects:

- routes
- accommodation
- attractions
- transport strategy

## 13. Edge Cases

The pipeline should handle:

- vague feedback
- contradictory feedback
- feedback that violates constraints
- feedback that conflicts with must-visit items
- feedback that requires unavailable provider data
- feedback that makes the trip impossible within budget/time

If feedback is unclear, the system should ask a follow-up question instead of guessing.

Example:

“I want it better.”

Response:

“What would you like improved: pace, cost, accommodations, transport, food, or activities?”

## 14. Design Principles

The Feedback Pipeline should follow these principles:

- Do not regenerate everything by default.
- Preserve what the user already likes.
- Explain every change.
- Ask follow-up questions when feedback is ambiguous.
- Treat feedback as a structured planning update.
- Maintain version history.
- Validate the updated plan before presenting it.
- Never silently remove must-visit items.
