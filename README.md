# TravelObligator

[![CI](https://github.com/garima24112000/travelobligator/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/garima24112000/travelobligator/actions/workflows/ci.yml)

**Live demo: <https://travelobligator.vercel.app>**

> A provider-grounded AI travel planner: LLMs propose and reason, real data providers establish the facts, and
> deterministic code decides what is feasible.

TravelObligator plans a trip to a single city and shows its work. It is not a prompt-to-itinerary wrapper.
Every scheduled place is grounded in provider data, every route shown comes from a routing provider, and
anything the system cannot verify is surfaced explicitly rather than filled in.

The demo runs on free hosting, so the first request after a quiet period can take up to a minute while the
backend wakes.

## What it does

- Builds a day-by-day itinerary for one city from your dates, pace and interests.
- Grounds every scheduled place in provider data. An AI suggestion is only a hypothesis until a geocoder
  confirms the place exists.
- Plans each day around real routes, with distance, duration, a map, and a switch from walking to driving
  when a leg is too long.
- Suggests food near each day's stops, and adds weather, public-holiday and currency context when available.
- Checks the plan before showing it, and reports it as ready, needing review, or blocked, with the reasons.
- Shows where each part of the plan came from and what data was missing.
- Takes feedback and regenerates only what the feedback affects, keeping versions. It refuses when it cannot
  do that safely.
- Supports accounts with private trips. Plans are saved and reopen without being regenerated.

## Architecture

```text
LLMs propose and reason  ->  providers establish facts  ->  deterministic code validates feasibility
```

```text
traveler request
  -> Geoapify geocoding      resolve the destination
  -> Geoapify Places         broad pool of real candidate places
  -> LLM anchor proposals    names worth looking for (hypotheses only)
  -> Geoapify grounding      keep only the proposals that really exist
  -> quality + sufficiency   is there enough verified material for this trip?
  -> LLM itinerary reasoning choose and order from verified candidates
  -> deterministic planning  diversity, geographic spread, day structure
  -> Geoapify Routing        real distance and time, walking or driving
  -> validation              readiness and review findings
  -> narrator                prose over the finished plan
  -> PostgreSQL
```

One model, `PlanningState`, holds the whole plan. Each section is written by one backend stage, and the
frontend renders from it without inventing anything.

| Layer | Technology |
| --- | --- |
| Frontend | Next.js, React, TypeScript, Leaflet maps |
| Backend | FastAPI, Pydantic, LangGraph orchestration |
| Facts | Geoapify (geocoding, places, routing), Open-Meteo, Nager.Date, Frankfurter |
| Reasoning | Groq (primary), Gemini (secondary), deterministic fallback |
| Data | PostgreSQL (authoritative), Redis (disposable provider cache) |
| Delivery | Docker, GitHub Actions, Vercel, Render |

If Groq is rate-limited or down, the same stage runs on Gemini under the same time budget. If neither
answers in time, a deterministic planner finishes the job. Provider health is tracked with a circuit breaker,
and each model stage makes at most two requests.

## Safety / no-fabrication design

An LLM here can suggest, choose and explain. It cannot be the source of a place identity, coordinates, a
route, a price, a rating, availability or a booking link.

- A proposed place is scheduled only after a provider confirms it. Unconfirmed proposals are dropped.
- Every scheduled place carries its provider source and provider id.
- Missing data is marked `unavailable`, and a provider that is not wired in is marked `not_connected`.
- When there is not enough verified inventory, the plan is reported as blocked. It is never padded.
- A provider outage degrades the plan honestly and never produces invented facts.
- Restricted travel platforms are never scraped.

## Evaluation

The engine was evaluated on a fixed 28-city benchmark — 18 tuning cities and 10 unseen holdout cities — plus
8 stress scenarios.

| Evaluation | Result | Fabricated identities | Duplicate places | Unsupported claims |
| --- | --- | --- | --- | --- |
| Tuning (18 cities, final freeze) | 15 / 18 pass | 0 | 0 | 0 |
| Unseen holdout (10 cities, initial run) | 9 / 10 pass | 0 | 0 | 0 |
| Post-holdout Cusco canary (generic fix) | Pass | 0 | 0 | 0 |
| Stress (8 scenarios) | 8 / 8 pass | 0 | 0 | 0 |
| Backend test suite | 5938 passed, 98 skipped | | | |

The three tuning failures were ordinary itinerary-quality misses, not safety failures.

The holdout failure was real and useful. Geoapify returned "Cuzco" for "Cusco, Peru", and a generic rule for
equivalent city spellings rejected it because it required six characters. The rule was fixed generically to
accept five-character names that are one edit apart, still requiring a city-level result and a matching
country. The fix was verified with a separate live run. The holdout was **not** rerun, so it stays 9/10.

Geoapify usage stayed well inside the 100-credit cap per generation (holdout median 40.5, max 47).

Method, full metrics and the production acceptance record are in
[docs/23_v1_release_reference.md](docs/23_v1_release_reference.md).

## Production deployment

```text
Browser -> Vercel (Next.js) -> /api/* rewrite -> Render (FastAPI) -> Neon PostgreSQL + Upstash Redis
```

The browser talks only to the Vercel origin, so the session cookie is first-party: `Secure`, `HttpOnly`,
`SameSite=Lax`. PostgreSQL is authoritative. Redis contents are disposable: Redis only caches provider
responses, and the backend does NOT wait for it to start, so losing the cache only causes future provider
calls. Generation runs as a background job that the browser polls.

Live at <https://travelobligator.vercel.app>. Deployment details are in
[docs/22_free_tier_deployment.md](docs/22_free_tier_deployment.md).

## Quick start

Requirements: Docker, or Python 3.11 and Node 22.

**Full stack with Docker Compose**

```bash
git clone https://github.com/garima24112000/travelobligator.git
cd travelobligator
cp .env.example .env        # then set POSTGRES_PASSWORD and SESSION_SECRET_KEY
docker compose up --build
```

Open <http://localhost:3000>. The backend is at <http://localhost:8000>. This starts PostgreSQL, runs the
migrations once, then starts the backend and frontend from the production images.

For hot reload, add the dev override:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

**Tests**

```bash
pip install -r backend/requirements.txt -r backend/requirements-dev.txt
pytest                      # backend suite, from the repo root; needs no database, cache or API key
cd frontend && npm ci && npm test
```

The local defaults use open development providers and need no API key. The production pipeline needs
`GEOAPIFY_API_KEY` and at least one of `GROQ_API_KEY` or `GEMINI_API_KEY`; every setting is documented in
`.env.example`.

## Limitations

- Single city per trip.
- Free-tier hosting: cold starts, and no uptime guarantee.
- No booking and no payment.
- No durable distributed job queue. An interrupted generation has to be retried.
- Availability of flights, accommodation detail, weather and holidays depends on the provider.

The full list is in [docs/23_v1_release_reference.md](docs/23_v1_release_reference.md#13-known-limitations).

## Documentation

- [docs/23_v1_release_reference.md](docs/23_v1_release_reference.md) — V1 scope, architecture, evaluation,
  production evidence, limitations.
- [docs/22_free_tier_deployment.md](docs/22_free_tier_deployment.md) — deployment contract and procedure.
- [ARCHITECTURE.md](ARCHITECTURE.md) and [PLANNING_ENGINE.md](PLANNING_ENGINE.md) — design background and
  history; the release reference above is the authoritative V1 current-state document.
- [docs/14_backend_architecture.md](docs/14_backend_architecture.md) — backend internals.
- [docs/CODEBASE_OVERVIEW.md](docs/CODEBASE_OVERVIEW.md) — file map and implementation history.

## License

[MIT](LICENSE)
