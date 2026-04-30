# Architecture Overview

TravelObligator is built using a modular architecture.

## Core Layers

1. User Input Layer
- Collect trip preferences
- Normalize into traveler profile

2. Data Layer
- Destination info
- Points of interest (POIs)
- Accommodation data
- Transport data

3. Recommendation Layer
- Rank attractions
- Rank accommodations
- Rank transport options

4. Itinerary Engine
- Generate structured itinerary JSON
- Assign activities to days
- Maintain route feasibility

5. Map Layer
- Plot POIs
- Show routes
- Visualize travel time

6. AI Layer
- Interpret preferences
- Generate itinerary
- Explain decisions
- Process feedback

7. Regeneration Layer
- Update only affected sections
- Maintain version history

## Key Design Principles

- Structured data first, UI later
- Mock providers first, real APIs later
- Modular services
- Clear separation of concerns