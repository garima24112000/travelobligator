# TravelObligator

TravelObligator is an AI travel decision platform that helps travelers plan realistic, personalized, and explainable trips.

It does not only generate an itinerary.

It helps explain:

- where to stay
- how to move around
- what to do
- what to skip
- whether the plan is realistic
- what data was available
- what data was unavailable
- why each recommendation fits the traveler

The itinerary is the final artifact.  
The real product is the decision pipeline that creates and explains the itinerary.

---

## Core Idea

Most travel planning tools produce lists of places.

TravelObligator focuses on decisions first:

```text
Traveler Profile
→ Destination Context
→ Trip Strategy
→ Stay + Transport
→ Experience Planner
→ Plan Validator
→ Feedback Pipeline
→ Final Itinerary
```

Each stage produces structured output, explanations, assumptions, confidence, and provider coverage.

---

## MVP Scope

The MVP supports single-city trip planning.

It is designed so future multi-city support can be added without a full redesign.

The MVP includes:

- traveler profile creation
- destination context building
- destination suitability reasoning
- trip strategy generation
- stay area recommendation
- transport strategy recommendation
- top accommodation option recommendations
- day-wise experience planning
- restaurant or meal-area recommendations
- validation before final presentation
- feedback-based partial regeneration
- user locks for approved items
- provider coverage transparency
- unavailable data labeling

---

## What Makes TravelObligator Different

TravelObligator is not just:

```text
input → AI → itinerary
```

It is a staged planning system:

```text
User Input
→ Structured Traveler Profile
→ Provider/Open-Data Destination Context
→ Strategy
→ Stay + Transport Decisions
→ Experience Plan
→ Validation
→ Feedback Updates
```

Every major recommendation should explain:

- what is recommended
- why it fits
- what tradeoffs exist
- what data supports it
- what assumptions were made
- how confident the system is

---

## Data Policy

TravelObligator uses legit-only data.

The system must not use:

- mock accommodation listings
- mock restaurant ratings
- mock prices
- mock availability
- scraped restricted provider data
- AI-invented factual travel data

If data is unavailable, the system should say it is unavailable.

Unavailable data is acceptable.  
Fake data is not.

---

## Data Sources

The MVP can use legitimate sources such as:

- OpenStreetMap / Overpass for places, restaurants, attractions, and accommodation POIs
- Nominatim or GeoNames for location resolution
- OpenTripPlanner + GTFS + OpenStreetMap for routing and transit feasibility
- Open-Meteo for weather
- Nager.Date for public holidays
- Frankfurter for currency conversion
- Amadeus APIs where production access is available
- Google Places, Google Routes, Mapbox, or other approved providers where available
- OpenAI Structured Outputs for reasoning and explanation only

Restricted providers such as Airbnb, Booking.com, Expedia, Vrbo, Tripadvisor, and Google Flights should not be scraped or treated as connected unless approved access exists.

---

## Safety Policy

The MVP does not generate direct safety scores.

Instead, it identifies safety-related planning considerations such as:

- late-night travel
- long walking segments
- poor transit alignment
- remote or isolated movement
- weather exposure
- traveler-specific comfort constraints
- low provider confidence

The system should not label places as safe or unsafe without authoritative data.

---

## Architecture Documents

The detailed architecture lives in `docs/`.

Recommended reading order:

```text
docs/00_product_vision.md
docs/01_traveler_profile.md
docs/02_trip_strategy.md
docs/03_stay_transport.md
docs/04_experience_planner.md
docs/05_plan_validator.md
docs/06_feedback_pipeline.md
docs/07_production_data_sources.md
docs/08_pipeline_data_flow.md
docs/09_planning_state.md
docs/10_data_model.md
docs/11_api_contracts.md
docs/12_provider_architecture.md
docs/13_llm_reasoning_pipeline.md
docs/14_backend_architecture.md
docs/15_database_schema.md
docs/16_frontend_architecture.md
docs/99_product_principles.md
```

---

## Planned Tech Stack

Frontend:

```text
Next.js
React
TypeScript
Tailwind CSS
```

Backend:

```text
FastAPI
Python
Pydantic
PostgreSQL
Redis later for cache
```

AI:

```text
OpenAI Structured Outputs
Schema-validated reasoning
Explanation generation
Feedback interpretation
```

Provider Layer:

```text
Replaceable provider adapters
ProviderGateway
Provider status tracking
Provider coverage tracking
Unavailable data handling
```

---

## Current Status

Architecture V1 is finalized.

Implementation phase begins with:

1. shared TypeScript types
2. backend Pydantic models
3. standard API response wrapper
4. in-memory PlanningState repository
5. PlanningOrchestrator skeleton
6. provider interfaces
7. stage service skeletons
8. real/open-data provider integration
9. validation and feedback loop

---

## Design Principles

- Decisions before itinerary.
- PlanningState is the source of truth.
- Providers supply facts.
- AI supplies reasoning and explanation.
- Missing data must be explicit.
- Mock data must not be treated as production truth.
- Recommendations must be explainable.
- Feedback should update only affected sections where possible.
- The user should know what the system knows, assumes, and could not verify.