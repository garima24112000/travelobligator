# Experience Planner

## 1. Purpose

The Experience Planner is responsible for transforming the traveler's preferences and the high-level trip strategy into a realistic and enjoyable day-wise travel plan.

This stage should **not** simply generate an itinerary by listing attractions.

Instead, it should answer three fundamental questions:

1. What experiences best match this traveler?
2. How should these experiences be distributed across the trip?
3. In what order should they be scheduled to maximize enjoyment while minimizing unnecessary travel and fatigue?

The output of this stage should be a complete itinerary proposal that is realistic, explainable, and ready for validation.

---

# 2. Inputs

The Experience Planner receives:

## Internal Inputs

- Traveler Profile
- Trip Strategy
- Stay & Transport Decisions

## External Production Inputs

### Places Provider

Provides:

- attractions
- landmarks
- museums
- restaurants
- parks
- viewpoints
- shopping areas
- beaches
- hiking trails
- entertainment venues

### Routes Provider

Provides:

- travel times
- distances
- walking routes
- driving routes
- public transport routes

### Attraction Metadata

For every attraction:

- opening hours
- closing hours
- estimated visit duration
- average ratings
- popularity
- entrance fees
- accessibility
- family suitability
- indoor/outdoor classification
- coordinates

---

# 3. Experience Selection

The planner should first determine **what deserves to be included**.

The goal is **not** to include every attraction.

The goal is to maximize traveler satisfaction.

Each attraction should receive a score.

Example scoring factors:

- traveler interests
- iconic importance
- uniqueness
- distance from stay area
- accessibility
- budget compatibility
- estimated crowd levels
- weather suitability
- family suitability
- walking requirements
- opening hours
- review quality

Example:

A traveler interested in food and culture may receive:

High Priority

- Local food market
- Historic district
- National museum

Medium Priority

- Observation deck

Low Priority

- Nightclub district

Excluded

- Amusement park

---

# 4. Experience Categorization

Experiences should be grouped into categories.

Examples:

- Cultural
- Historical
- Food
- Nature
- Adventure
- Shopping
- Scenic
- Religious
- Entertainment
- Family
- Relaxation
- Nightlife

Categorization helps balance each day.

---

# 5. Experience Scheduling

After selecting experiences, assign them to days.

The planner should consider:

- arrival day
- departure day
- opening hours
- travel time
- attraction proximity
- sunset time
- meal timing
- user energy level
- itinerary intensity
- weather (when available)

Example strategy:

Morning

- outdoor landmarks

Lunch

Afternoon

- museums

Evening

- local food area

Night

- optional activities

---

# 6. Daily Planning Philosophy

Each day should feel intentional.

Guidelines:

- group nearby attractions
- avoid unnecessary backtracking
- alternate indoor and outdoor experiences
- naturally include meal breaks
- include rest periods
- avoid excessive walking
- avoid excessive transit
- keep evenings flexible for relaxed travelers
- keep arrival and departure days lighter

Every day should have a clear theme.

Examples:

Day 1
Arrival + Local Exploration

Day 2
History and Culture

Day 3
Nature and Scenic Views

Day 4
Food and Shopping

---

# 7. Experience Cards

Every selected experience should generate an Experience Card.

Each card explains why it exists.

Fields:

- title
- category
- priority
- estimated duration
- best visiting time
- estimated walking
- estimated travel time
- included because
- possible tradeoffs
- nearby alternatives

Example:

Title

Lincoln Memorial

Included Because

- Matches cultural interests
- Iconic attraction
- Best visited during morning
- Close to nearby monuments

Priority

High

Duration

75 minutes

Walking

12 minutes

Tradeoff

Can become crowded after noon.

---

# 8. Day Summary

Before showing activities, every day should begin with a summary.

Example:

Day Theme

National Mall Exploration

Today's Goal

Cover the major monuments with minimal walking and transit.

Why This Order?

- attractions are geographically clustered
- museums open later
- monument lighting is better in the morning
- lunch naturally fits between sightseeing blocks

Expected Walking

6 km

Estimated Cost

$40

Energy Level

Moderate

---

# 9. Itinerary Assembly

The itinerary should be created by combining ordered Experience Cards.

Each day should contain:

- theme
- objectives
- activities
- meal suggestions
- transport recommendations
- estimated walking
- estimated travel time
- estimated expenses
- flexibility buffer

---

# 10. Decision Cards

The Experience Planner should also generate Decision Cards.

Examples:

Decision

Visit Georgetown on Day 3

Why

- close to sunset
- scenic
- restaurants nearby
- avoids backtracking

Tradeoff

Less shopping time.

Confidence

0.91

Alternative

Move Georgetown to Day 2.

---

# 11. Output Contract

The Experience Planner should return:

{
"trip_overview": {},
"daily_plan": [],
"experience_cards": [],
"decision_cards": [],
"estimated_budget": {},
"estimated_walking": {},
"planning_metadata": {}
}

---

# 12. Production Considerations

The Experience Planner should use production providers.

Places

- Google Places API
- OpenStreetMap fallback

Routing

- Google Routes API
- Mapbox Directions fallback

AI

- OpenAI Structured Outputs

Future integrations

- weather provider
- crowd prediction
- event calendars
- restaurant reservations

---

# 13. Edge Cases

The planner should handle:

- rainy weather
- attraction closures
- public holidays
- sold-out attractions
- delayed flights
- late hotel check-in
- early departure flights
- traveler fatigue
- accessibility limitations
- budget reductions

The planner should always produce the best feasible itinerary rather than failing completely.

---

# 14. Design Rules

The Experience Planner should follow these principles:

- Optimize for traveler satisfaction, not attraction count.
- Never overload a day just because free time exists.
- Every experience should have a clear reason for inclusion.
- Minimize unnecessary travel.
- Respect traveler energy levels.
- Prioritize realism over density.
- Generate explainable decisions rather than opaque recommendations.
- The itinerary is the result of intelligent planning, not the primary objective.

The Experience Planner is responsible for creating experiences that feel intentional, balanced, and personalized. The itinerary is simply the final representation of those decisions.
