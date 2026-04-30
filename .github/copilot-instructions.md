# GitHub Copilot Instructions for TravelObligator

You are helping build Travelobligator, a full-stack AI-powered travel itinerary planning platform.

## Product Goal

TravelObligator creates personalized, realistic, route-aware travel itineraries based on user preferences, budget, trip style, accommodation needs, transport preferences, and feedback.

This is not a generic itinerary chatbot. It should behave like a travel planning copilot that helps users decide:
- where to stay
- what to do each day
- how to move around
- what to avoid
- how to adjust the plan when they dislike something

## Core Product Principles

1. Personalization first.
2. Route realism matters.
3. Use structured data before prose.
4. Prefer modular architecture.
5. Mock external providers first, but design adapters for real APIs later.
6. Feedback should update only affected itinerary sections where possible.
7. The app should feel like a polished travel dashboard, not a simple form app.

## Tech Stack

Frontend:
- Next.js
- React
- TypeScript
- Tailwind CSS
- shadcn/ui preferred
- Mapbox or Google Maps integration later

Backend:
- FastAPI or Node/Express depending on project setup
- PostgreSQL for persistence
- Redis optional for caching
- Provider adapter pattern for travel data

AI:
- Structured JSON itinerary generation
- Preference interpretation
- Feedback interpretation
- Explanation generation
- Do not rely on AI alone for route realism

## Coding Rules

- Use TypeScript strictly on the frontend.
- Keep files small and modular.
- Prefer reusable components.
- Avoid hardcoded business logic inside UI components.
- Separate provider adapters, scoring utilities, routing utilities, and AI workflows.
- Use clear names for types, services, and components.
- Add loading, empty, and error states for user-facing screens.
- Keep mock data clearly labeled as mock data.
- Never pretend mock data is live data.

## Architecture Rules

The system should be organized around these layers:

1. User preference capture
2. Traveler profile normalization
3. Travel data retrieval
4. Recommendation and scoring
5. Itinerary generation
6. Map and route visualization
7. Feedback interpretation
8. Selective regeneration
9. Version history

## Expected User Flow

1. User enters trip preferences.
2. System normalizes preferences into a structured traveler profile.
3. System retrieves or mocks destination, POI, accommodation, and transport data.
4. System ranks candidates.
5. System generates a structured itinerary JSON.
6. Frontend renders itinerary dashboard and map.
7. User gives feedback.
8. System updates the relevant part of the itinerary.
9. User can compare or continue refining.

## Important

When implementing features:
- First inspect existing files.
- Create a short implementation plan.
- Then make changes.
- Run lint/typecheck/tests when available.
- Summarize what changed.