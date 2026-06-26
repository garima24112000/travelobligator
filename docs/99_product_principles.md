# Product Principles

These principles define how TravelObligator should behave as it grows.

## 1. Decisions Before Itinerary

TravelObligator should not start by generating a day-wise itinerary.

It should first understand the traveler, define the trip strategy, decide where they should stay, decide how they should move around, and only then compose the itinerary.

## 2. Explain Every Major Recommendation

Every important recommendation should answer:

- What is recommended?
- Why does it fit this traveler?
- What are the tradeoffs?
- What alternatives exist?
- How confident is the system?

## 3. Traveler Profile Is the Source of Truth

Downstream stages should use the structured Traveler Profile instead of raw form inputs whenever possible.

Raw input is what the user said.  
Traveler Profile is what the system understands.

## 4. Recommend Areas Before Properties

For accommodation, the system should recommend the best area or neighborhood first.

Hotels, hostels, Airbnbs, or resorts should be ranked only after the stay area decision is made.

## 5. Route Realism Matters

A plan that looks good in text but fails geographically is a bad plan.

TravelObligator should consider distance, travel time, walking burden, transport mode, and attraction grouping.

## 6. Validate Before Presenting

The plan should pass through validation before being shown as final.

Validation should check feasibility, comfort, safety, budget, pace, route quality, and alignment with traveler intent.

## 7. Do Not Silently Fix Problems

If the system detects a problem, it should explain the issue and suggest a fix.

The user should understand why the plan changed.

## 8. Partial Regeneration Over Full Regeneration

When feedback is given, the system should identify affected stages and update only those parts where possible.

Do not regenerate the entire trip unless the core traveler profile or trip strategy changes.

## 9. Preserve What the User Likes

If a user approves part of the trip, the system should preserve it unless later feedback directly conflicts with it.

## 10. Use Real Provider Data Where Possible

TravelObligator should use production data providers for places, routes, accommodations, and AI reasoning.

Mock data should not be treated as production behavior.

## 11. Separate Facts From Reasoning

Provider data should supply facts.

The reasoning pipeline should interpret those facts in the context of the traveler.

The system should not invent factual information.

## 12. Show Uncertainty

When information is missing or confidence is low, the system should explain assumptions instead of pretending certainty.

## 13. Optimize for Traveler Satisfaction

The goal is not to maximize the number of attractions.

The goal is to create a trip that feels enjoyable, realistic, and aligned with the traveler’s expectations.

## 14. Every Output Should Be Useful

The system should avoid generic filler.

Every recommendation, warning, card, and explanation should help the traveler make a better decision.

## 15. The Itinerary Is the Final Artifact, Not the Product

The real product is the decision pipeline that creates and explains the itinerary.