# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

TravelObligator is an AI travel **decision** platform, not a one-shot itinerary generator. The product is the staged decision pipeline that produces and explains a plan, not just the plan itself:

```text
Traveler Profile → Destination Context → Trip Strategy → Stay + Transport
  → Experience Planner → Plan Validator → Feedback Pipeline → Final Itinerary
```

Two services: a FastAPI/Python backend (`backend/`) and a Next.js/React frontend (`frontend/`), currently backed by a local JSON file rather than a database (see Architecture below).

Read `docs/00_product_vision.md` and `docs/09_planning_state.md` first if you haven't touched this repo before. Full recommended reading order (`docs/00`–`docs/18`, `docs/20`) is in `README.md`.

## Commands

**Backend** (from `backend/`, or via Docker):
```bash
pip install -r requirements.txt -r requirements-dev.txt   # requirements-dev adds pytest
uvicorn app.main:app --reload                              # dev server, port 8000
```

**Tests** (from repo root — `pytest.ini` sets `pythonpath=backend`, `testpaths=backend/app/tests`):
```bash
pytest                                                      # full backend suite
pytest backend/app/tests/services/test_feedback_service.py  # one file
pytest backend/app/tests/services/test_feedback_service.py::test_name  # one test
python -m compileall backend/app                            # compile check (CI also runs this)
```
There is no backend lint/type-check config (no ruff/mypy) in this repo.

**Frontend** (from `frontend/`):
```bash
npm run dev      # dev server, port 3000
npm run build    # next build
npm run lint     # eslint — the only frontend CI check; there is no test framework/test files
```

**Full stack**: `docker compose up` (root `docker-compose.yml`) starts `backend` (port 8000, `--reload`), `frontend` (port 3000, currently runs `npm run dev` even in the committed `frontend/Dockerfile`, not a production build), plus `postgres` and `redis` containers that nothing in the app code currently talks to (see Architecture).

CI (`.github/workflows/ci.yml`) runs two independent jobs: backend (`compileall` + `pytest`) and frontend (`lint` only) — no integration test, no deploy step.

## Architecture

**`PlanningState` is the single source of truth.** It's one Pydantic model (`backend/app/models/planning_state.py`) holding every section of a trip's plan (traveler profile, destination context, trip strategy, stay/transport, experience plan, validation report, feedback history, locks, version history, provider coverage, etc.). Each section is owned by exactly one backend stage service; the frontend renders directly from it and invents nothing itself.

**Backend layering** (`API → Orchestrator/Services → ProviderGateway/Repositories/Validators → Provider Adapters/Database`):
- `backend/app/api/routes/trips.py` — the single router (prefix `/trips`). Every response is wrapped in a standard envelope (`success`, `data`, `message`, `errors`, `metadata`) built in `core/response.py`; business errors are `AppError` subclasses from `core/errors.py`, converted to that envelope by a handler in `main.py`.
- `backend/app/services/planning_orchestrator.py` — `PlanningOrchestrator.generate_full_plan()` runs the 6 core stage services in a fixed order inside `POST /trips/{id}/generate`, saving `PlanningState` to the repository after every stage. This is the main write path; it's a single synchronous request (no job queue).
- Stage services (`backend/app/services/{traveler_profile,destination_context,trip_strategy,stay_transport,experience_planner,plan_validator}_service.py`) each own specific `PlanningState` fields and must not touch fields owned by another stage.
- `backend/app/providers/gateway.py` (`ProviderGateway`) is the *only* way stage services reach external data — never import a provider adapter directly. Real adapters exist for places (OpenStreetMap/Overpass + Nominatim), weather (Open-Meteo), holidays (Nager.Date), and currency (Frankfurter); routes/transit/accommodation/flights are stub interfaces that always report `not_connected` — there's no adapter for them yet.
- `backend/app/repositories/` (`PlanningStateRepository`, `TripRepository`) are module-level singletons, in-memory dicts persisted via `backend/app/storage/local_json_store.py` (`LocalJsonStore`) to a single gitignored JSON file (`backend/.data/travelobligator_state.json`), with atomic writes (`tempfile.mkstemp` + `fsync` + `os.replace`) under an in-process lock. **Despite `Settings.database_url`/`psycopg`/the compose `postgres` service existing, no code path in `app/` reads a database** — this local file is the real persistence layer today.
- No authentication/authorization exists anywhere; every endpoint is scoped only by knowledge of a `trip_id`.
- Services, the orchestrator, and the gateway are constructed as module-level singletons at import time, not via FastAPI `Depends()` — tests override behavior by monkeypatching these singletons directly (see `backend/app/tests/conftest.py`).

**Regeneration is intentionally not implemented.** `POST /trips/{id}/regenerate` always returns `409 REGENERATION_NOT_AVAILABLE` and only appends to an audit trail — it never reruns a stage. `POST /trips/{id}/feedback` classifies feedback with deterministic keyword rules (no AI, no plan mutation) and reports what *would* need to change. Before touching any regeneration-adjacent code, read `docs/17_regeneration_manual_qa.md` and run its checklist — this refusal behavior is a safety contract, not a placeholder to "finish."

**AI candidate-proposal subsystem** (`backend/app/providers/ai_candidate_proposal/`) is a separate, fully-built shadow-mode path (Anthropic-backed, forced tool-use schema that structurally forbids factual fields like price/rating/coordinates) gated by `AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED` (default off). When enabled it populates `ai_candidate_proposal_batch`/`candidate_grounding_batch` for inspection only — it never feeds scheduling, `validation_report`, or `provider_coverage`. Don't wire it into the real pipeline unless explicitly asked to.

**Frontend** is a single route (`frontend/app/page.tsx`, one large file — no `components/`/`hooks/`/dynamic routing). `frontend/lib/api.ts` is a thin fetch client, one function per backend endpoint; `frontend/lib/types.ts` is a hand-maintained partial mirror of backend response shapes (not generated — keep both in sync manually when changing API contracts). State is plain `useState`, no store library. Loading a trip fires `GET /summary` then 7 parallel calls assembled client-side into one `PlanResult` — this deviates from `docs/16_frontend_architecture.md`'s single-fetch design; that's a known, accepted divergence, not a bug to silently "fix."

**Next.js version note**: `frontend/AGENTS.md` (pulled in via `frontend/CLAUDE.md`) warns this is a bleeding-edge Next.js version with breaking API changes from what training data assumes — check `node_modules/next/dist/docs/` before writing frontend code that touches Next.js APIs/conventions.

For a full file-by-file map, traced request/response flows with file:line references, and a list of known gaps, see `docs/CODEBASE_OVERVIEW.md`.

## Core Rules

- `PlanningState` is the source of truth; render the frontend from it, don't invent client-side.
- Providers supply facts, AI supplies reasoning/explanation only — never let AI invent a price, rating, availability, route, or booking link. Never fabricate mock hotels/restaurants/prices/ratings/routes as product data.
- Do not scrape restricted providers (Airbnb, Booking.com, Expedia, Vrbo, Tripadvisor, Google Flights).
- If data is unavailable, mark it `unavailable`; if a provider isn't connected, mark it `not_connected` — never silently omit or guess.
- Validation must run before a plan is shown as final; user locks must be respected; feedback should update only affected sections where possible (once regeneration exists to do so).
- Work in small steps; don't rewrite the staged architecture or bundle large unrelated changes. If unsure whether something is real product logic vs. placeholder, ask before implementing.
- Use `.env` for local secrets (never commit it); there is currently no `.env.example` in the repo despite this being the intended pattern — reconstruct the expected variable list from `backend/app/core/config.py` if you need to add one.

**Note on `.github/` instructions**: `.github/copilot-instructions.md` and `.github/instructions/*.md` describe an earlier, looser design (mock providers, mock data allowed if labeled) that predates and conflicts with the no-mock-data policy above, which is what's actually enforced in code today. Treat this file as authoritative over those.
