# Traveler Profile

## 1. Purpose

The Traveler Profile is the structured representation of the user’s trip needs, preferences, constraints, and intent.

It is not the same as raw form input.

Raw input tells the system what the user typed or selected.  
The Traveler Profile tells the system how to make travel decisions for that user.

Every later stage should use this profile as its main input:

- Trip Strategy
- Stay + Transport
- Experience Planner
- Plan Validator
- Feedback Pipeline

## 2. Inputs

The profile should be created from:

### Explicit Form Inputs

- destination
- origin city
- trip dates
- number of travelers
- travel group type
- budget range
- accommodation preference
- transport preference
- interests
- must-visit places
- must-avoid places
- constraints
- free-text preferences

### Intent Inputs

The form should include an itinerary intensity scale:

1 = very relaxed  
2 = relaxed  
3 = balanced  
4 = active  
5 = maxed-out

This controls how dense the itinerary should be.

### Free Text Interpretation

Free text should be interpreted into structured signals.

Example:

User says:
“I’m traveling with my parents. We want scenic places, good food, not too much walking, and no nightlife.”

Derived signals:

- family/parents-friendly trip
- high comfort priority
- moderate or low walking tolerance
- nightlife should be deprioritized
- scenic and food experiences should be prioritized

## 3. Traveler Model

The Traveler Profile should include:

### Basic Context

- destination
- origin city
- start date
- end date
- duration
- number of travelers
- travel group type

### Travel Intent

- itinerary intensity scale
- trip goal
- desired pace
- comfort vs exploration preference
- iconic attractions vs hidden gems preference
- structured plan vs flexible plan preference

### Budget Profile

- minimum budget
- maximum budget
- budget tier
- cost sensitivity
- flexibility for splurge experiences

### Mobility Profile

- walking tolerance
- stairs tolerance
- uphill tolerance
- luggage handling needs
- accessibility needs
- elderly/children considerations

### Interest Profile

- food
- culture
- history
- nature
- shopping
- nightlife
- beaches
- adventure
- museums
- scenic views
- hidden gems
- family-friendly activities

### Stay Profile

- preferred accommodation type
- preferred neighborhood style
- safety priority
- proximity to public transport
- quiet vs lively area preference
- parking need
- work-friendly need

### Transport Profile

- preferred transport mode
- public transport comfort
- self-drive comfort
- taxi/rideshare comfort
- train preference
- flight preference
- luggage constraints

### Avoidance Profile

- places to avoid
- experiences to avoid
- unsafe timing
- excessive walking
- late nights
- crowds
- expensive activities
- tourist traps

## 4. Derived Attributes

The system should infer useful planning traits from raw input.

Examples:

If user is traveling with parents:

- increase comfort priority
- increase safety priority
- reduce nightlife weight
- reduce walking intensity
- prefer easy transport

If user selects maxed-out itinerary:

- allow more activities per day
- reduce buffer time
- allow longer sightseeing windows
- still validate feasibility

If user selects relaxed itinerary:

- fewer activities per day
- more rest time
- shorter walking routes
- more flexible evenings

If user has a low budget:

- prioritize free attractions
- prefer public transport
- recommend budget stays
- flag expensive experiences

If user mentions food:

- increase food experience weight
- include local food areas
- include meal planning context

## 5. Decision Weights

The profile should convert preferences into numeric weights.

Weights should range from 0.0 to 1.0.

Example weights:

- safety_weight
- budget_weight
- comfort_weight
- walkability_weight
- food_weight
- culture_weight
- nature_weight
- nightlife_weight
- hidden_gems_weight
- iconic_attractions_weight
- transport_convenience_weight
- family_friendliness_weight

Example:

```json
{
  "safety_weight": 1.0,
  "comfort_weight": 0.9,
  "walkability_weight": 0.85,
  "food_weight": 0.8,
  "nightlife_weight": 0.1,
  "budget_weight": 0.7
}
```

These weights should be used by later stages to rank stay areas, transport methods, and experiences.

## 6. Confidence Levels

The system should not assume certainty when the user did not provide enough information.

Each major profile area should include confidence.

Examples:

- budget confidence
- mobility confidence
- food preference confidence
- stay preference confidence
- transport preference confidence
- activity preference confidence

If confidence is low, the system can:

- make a conservative assumption
- explain the assumption
- ask a follow-up question later

Example:

```json
{
  "food_preference": "unknown",
  "confidence": 0.25,
  "assumption": "Include general local food suggestions but do not optimize heavily for cuisine."
}
```

## 7. Output Contract

The Traveler Profile should produce a structured object.

Example:

```json
{
  "basic_context": {
    "destination": "Washington DC",
    "origin_city": "New York",
    "duration_days": 3,
    "travel_group_type": "family",
    "travelers_count": 3
  },
  "travel_intent": {
    "intensity_scale": 2,
    "pace": "relaxed",
    "trip_goals": ["scenic", "culture", "food"],
    "plan_style": "structured_but_flexible"
  },
  "budget_profile": {
    "budget_min": 1000,
    "budget_max": 2500,
    "budget_tier": "mid_range",
    "cost_sensitivity": 0.65
  },
  "mobility_profile": {
    "walking_tolerance": "moderate",
    "stairs_tolerance": "low",
    "accessibility_needs": [],
    "elderly_consideration": true
  },
  "interest_profile": {
    "food": 0.8,
    "culture": 0.85,
    "scenic_views": 0.9,
    "nightlife": 0.1,
    "shopping": 0.4
  },
  "stay_profile": {
    "preferred_accommodation_type": "hotel",
    "safety_priority": 1.0,
    "public_transport_priority": 0.85,
    "quiet_area_preference": 0.75
  },
  "transport_profile": {
    "preferred_mode": "public_transport",
    "self_drive_comfort": 0.2,
    "rideshare_comfort": 0.7,
    "luggage_constraint": false
  },
  "avoidance_profile": {
    "avoid": ["nightlife", "long_hikes"],
    "crowd_sensitivity": 0.6,
    "tourist_trap_sensitivity": 0.7
  },
  "decision_weights": {
    "safety_weight": 1.0,
    "comfort_weight": 0.9,
    "walkability_weight": 0.85,
    "food_weight": 0.8,
    "budget_weight": 0.65,
    "nightlife_weight": 0.1
  },
  "confidence": {
    "budget": 0.9,
    "mobility": 0.7,
    "food": 0.8,
    "stay": 0.75,
    "transport": 0.7,
    "activities": 0.85
  }
}
```

## 8. Design Rule

No later stage should rely directly on raw form input if the same information exists in the Traveler Profile.

The Traveler Profile is the source of truth for planning decisions.
