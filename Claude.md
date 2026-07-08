# Claude Code Instructions for TravelObligator

TravelObligator is an AI Travel Decision Platform.

It is not a simple itinerary generator.

Before implementation, read these architecture docs:

- docs/00_product_vision.md
- docs/07_production_data_sources.md
- docs/09_planning_state.md
- docs/10_data_model.md
- docs/11_api_contracts.md
- docs/12_provider_architecture.md
- docs/13_llm_reasoning_pipeline.md
- docs/14_backend_architecture.md
- docs/15_database_schema.md
- docs/16_frontend_architecture.md
- TASKS.md

## Core Rules

- PlanningState is the source of truth.
- Do not implement one large LLM-to-itinerary flow.
- Do not use mock travel facts as product data.
- Do not create fake hotels, fake restaurants, fake prices, fake ratings, fake routes, fake availability, or fake booking links.
- Do not scrape restricted providers like Airbnb, Booking.com, Expedia, Vrbo, Tripadvisor, or Google Flights.
- If data is unavailable, mark it as unavailable.
- If a provider is not connected, mark it as not_connected.
- Providers supply facts.
- AI supplies reasoning and explanation.
- AI must not invent factual travel data.
- Every major recommendation should be explainable.
- Provider coverage must be visible.
- Validation must run before the plan is shown as final.
- Feedback should update only affected sections where possible.
- User locks must be respected.

## Implementation Style

Work in small steps.

Prefer this order:

1. Backend Pydantic models
2. Standard API response wrapper
3. In-memory repositories
4. PlanningState creation
5. PlanningOrchestrator skeleton
6. Stage service skeletons
7. ProviderGateway skeleton
8. Real/open-data provider integrations
9. Frontend PlanningState dashboard

Do not rewrite the architecture unless asked.

Do not add large unrelated changes.

If unsure whether something is product logic or placeholder logic, ask before implementing.

## Environment Rules

Use `.env` for local secrets.

Never commit `.env`.

Use `.env.example` for safe environment variable examples.

Do not hardcode API keys or provider credentials.

## Frontend Rules

The frontend should render from PlanningState.

Do not hardcode demo trips, demo hotels, demo restaurants, demo ratings, demo prices, or demo itineraries.

Temporary placeholder UI is allowed only if it clearly does not present fake travel facts.

## Backend Rules

FastAPI backend should follow:

```text
API Layer
→ Orchestrator / Services
→ ProviderGateway / Repositories / Validators
→ Provider Adapters / Database