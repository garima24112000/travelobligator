# Trip Strategy

## 1. Purpose

The Trip Strategy stage decides the overall direction of the trip before building the itinerary.

It answers:
- Is this destination suitable for the traveler?
- Is the trip duration realistic?
- Is the budget realistic?
- What kind of trip should this become?
- What major planning assumptions should guide the itinerary?

This stage should not create the day-wise itinerary yet.

## 2. Input

Primary input:
- Traveler Profile

Optional production data:
- destination overview
- average hotel cost
- average food cost
- seasonal/weather context
- local transport quality
- safety/neighborhood context
- major attraction density

## 3. Outputs

The Trip Strategy should produce:

- destination suitability score
- recommended trip style
- duration assessment
- budget assessment
- high-level planning strategy
- key tradeoffs
- assumptions
- confidence score

## 4. Destination Suitability

The system should evaluate whether the destination matches the traveler profile.

Example:

A destination is highly suitable if:
- it has activities matching user interests
- it fits the travel group type
- it supports the preferred pace
- it has reasonable transport options
- it fits budget expectations

Example output:

```json
{
  "destination_suitability": {
    "score": 0.88,
    "label": "high",
    "reasons": [
      "The destination has strong culture and food options.",
      "Public transport is suitable for a parent-friendly trip.",
      "Major attractions can be grouped without excessive travel time."
    ]
  }
}
```

## 5. Duration Assessment

The system should decide whether the trip length is too short, enough, or excessive.

Example logic:

If trip is too short, recommend prioritization.
If trip is enough, continue normally.
If trip is long, include slower days or day trips.

Example:

```json
{
  "duration_assessment": {
    "label": "enough",
    "reason": "Three days is enough for the main monuments, museums, food areas, and one flexible evening."
  }
}
```

## 6. Budget Assessment

The system should evaluate if the user’s budget is realistic.

Example logic:

Compare budget with estimated stay, food, transport, and activity costs.
If budget is tight, prioritize free/low-cost attractions.
If budget is flexible, allow premium experiences.

Example:

```json
{
  "budget_assessment": {
    "label": "realistic",
    "risk_level": "low",
    "reason": "The budget supports mid-range hotels, public transport, and a few paid attractions."
  }
}
```

## 7. Recommended Trip Style

The strategy should convert profile + destination into a clear planning style.

Examples:

relaxed cultural trip
parent-friendly scenic trip
fast-paced first-time tourist trip
budget food and public transport trip
luxury comfort-focused trip

Example:

```json
{
  "recommended_trip_style": "parent-friendly scenic cultural trip"
}
```
## 8. Planning Strategy

The system should define broad planning rules.

Example:

```json 
{
  "planning_strategy": [
    "Keep mornings for major attractions.",
    "Group nearby sights together.",
    "Avoid late-night activities.",
    "Use public transport for city travel and rideshare when walking distance is high.",
    "Include rest breaks between sightseeing blocks."
  ]
}
```

## 9. Tradeoffs

The system should explain tradeoffs clearly.

Example:

```json
{
  "tradeoffs": [
    {
      "decision": "Do not rent a car inside the city.",
      "why": "Parking and traffic add complexity without saving much time."
    },
    {
      "decision": "Prioritize fewer attractions per day.",
      "why": "The traveler selected a relaxed intensity and parent-friendly comfort."
    }
  ]
}
```

## 10. Assumptions

If information is missing, assumptions must be explicit.

Example:

```json
{
  "assumptions": [
    "Assuming the travelers are comfortable with moderate walking.",
    "Assuming hotel budget is part of the total trip budget.",
    "Assuming no accessibility restrictions beyond the provided constraints."
  ]
}
```

## 11. Output Contract

Example Trip Strategy object:

```json
{
  "destination_suitability": {
    "score": 0.88,
    "label": "high",
    "reasons": [
      "Strong match with food, culture, and scenic interests.",
      "Good public transport supports a relaxed itinerary."
    ]
  },
  "duration_assessment": {
    "label": "enough",
    "recommended_days": 3,
    "reason": "Three days can cover major highlights at a balanced pace."
  },
  "budget_assessment": {
    "label": "realistic",
    "risk_level": "low",
    "reason": "Budget appears sufficient for mid-range stay and local transport."
  },
  "recommended_trip_style": "relaxed scenic cultural trip",
  "planning_strategy": [
    "Group attractions geographically.",
    "Avoid overloading each day.",
    "Use public transport where convenient and rideshare for comfort."
  ],
  "tradeoffs": [
    {
      "decision": "Skip nightlife-heavy areas.",
      "why": "The profile deprioritizes nightlife and prioritizes comfort."
    }
  ],
  "assumptions": [
    "Weather is assumed suitable unless real weather data says otherwise."
  ],
  "confidence": 0.82
}
```

## 12. Design Rule

Trip Strategy should guide the itinerary, not replace it.

It should explain the planning direction before the itinerary is generated.