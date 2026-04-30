# Backend Instructions

Apply these rules to backend files.

## Backend Rules

- Keep services modular.
- Separate routes, schemas, services, providers, and utilities.
- Validate incoming data.
- Use structured JSON for itinerary generation.
- Keep mock data clearly labeled as mock data.
- Do not pretend mock provider data is live data.

## Main Backend Modules

- trip request handling
- traveler profile normalization
- mock POI provider
- mock accommodation provider
- mock transport provider
- scoring utilities
- itinerary generation service
- feedback interpretation service
- regeneration service

## AI Rules

- Use AI for interpretation and explanation.
- Do not rely on AI alone for route feasibility.
- Generate structured itinerary JSON first.
- Validate schema before returning data.