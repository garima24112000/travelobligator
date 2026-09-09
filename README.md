# TravelObligator

> An AI travel decision platform with LangGraph orchestration,
> provider-backed data boundaries, a real Kiwi MCP flight integration,
> a validation/trust dashboard, and automated no-fabrication safety checks.

TravelObligator is an **AI travel decision platform** — not a
prompt-to-itinerary wrapper. Ask it to plan a trip and the response isn't
"here's a list of places an LLM thought sounded nice." It's a staged
decision pipeline that tracks what's actually known, what's missing, what
came from a real provider versus an AI suggestion, and whether the
resulting plan is even safe to treat as a real itinerary.

The itinerary is the artifact. The decision pipeline — provider coverage,
validation, route-aware scheduling, regeneration, and source transparency
— is the actual technical product.

Concretely, TravelObligator:

- runs trip planning as an explicit, staged pipeline (traveler profile →
  destination context → strategy → stay/transport → experience planning →
  validation → feedback/regeneration), not one LLM call
- treats an LLM as a **proposer, never a source of truth** — every
  AI-suggested place must be independently grounded against real
  provider/open data before it can appear in a plan
- surfaces exactly what backs each part of the plan — provider-backed,
  open-data, AI-suggested-then-grounded, or explicitly unavailable —
  through a frontend "trust dashboard," not buried in fine print
- integrates real external data sources (OpenStreetMap, Open-Meteo,
  Nager.Date, Frankfurter, OSRM, and a live third-party flight provider via
  Kiwi's MCP server) behind one replaceable provider layer
- supports real, narrowly-scoped, feedback-driven regeneration: it reruns
  only the plan section your feedback actually affects, and refuses
  outright rather than guessing when it can't do that safely

It helps explain:

- where to stay
- how to move around
- what to do
- what to skip
- whether the plan is realistic
- what data was available
- what data was unavailable
- why each recommendation fits the traveler

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

## Getting Started

Prerequisites: Python 3.11+, Node.js 20+ with npm, and git.

Clone and enter the repo, then set up the backend:

```bash
git clone <this-repo-url>
cd travelobligator/backend
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
```

Copy the environment template (every default is already safe to run
as-is — see "Optional Provider-Backed Demos" below if you want to opt
into anything extra):

```bash
cd ..
cp .env.example .env
```

Run the backend (from `backend/`, virtualenv active):

```bash
cd backend
uvicorn app.main:app --reload
```

The backend runs at `http://localhost:8000`.

In a second terminal, set up and run the frontend (from the repo root):

```bash
cd frontend
npm install
npm run dev
```

The frontend runs at `http://localhost:3000` — open that URL and use the
form to create a trip.

Run the backend test suite (from the repo root, virtualenv active):

```bash
pytest
```

### Running with Docker Compose instead

`docker compose up` (from the repo root) starts a `backend` container
(port 8000, `--reload`), a `frontend` container (port 3000, running
`npm run dev` — not a production build), and `postgres`/`redis`
containers. **Nothing in the application code currently talks to
Postgres or Redis** — they exist in `docker-compose.yml` for future use,
not because the app needs them today (see "Current Status" below). This
is a convenience for running both services together locally, not a
production deployment path.

---

## Expected Default Behavior

Out of the box, with no configuration changes, most external providers
default to `not_connected` or are limited to local/manual data. **This is
the expected, honest default state — not a bug.** The whole point of this
project is that it never fabricates a fact to paper over a missing
provider.

Status vocabulary you'll see throughout the app:

- `success` — a real provider (or local file) actually returned usable data
- `unavailable` — the source was checked but had no usable data for this
  request (e.g. no local scraped file present)
- `failed` — the provider was called and returned an error
- `not_connected` — no provider is configured for this data type at all
- `blocked` — the plan-level readiness status shown when required data is
  missing and the plan should not be treated as usable yet

With no configuration changes, a freshly generated trip will typically
show overall `blocked` or `needs_review`, with accommodation/flight
inventory reporting `unavailable` and routing reporting `not_connected`.
Places, weather, holidays, and currency are connected by default and
should return real data. See "Optional Provider-Backed Demos" below for
how to see non-empty accommodation/flight inventory locally.

---

## Optional Provider-Backed Demos

Everything below is opt-in. None of it is required to run the app, and
none of it involves committing sample data to this repository.

### A. Local scraped HTML demo (accommodation / flights)

`ACCOMMODATION_PROVIDER`/`FLIGHT_PROVIDER` both default to `scraped_local`
— a parser that reads one local, developer-supplied static HTML file per
data type. It never fetches a live website, never automates a browser,
and never scrapes anything on its own.

- Accommodation: place a file at `backend/.data/manual_scrapes/accommodations.html`
- Flights: place a file at `backend/.data/manual_scrapes/flights.html`

Both paths are configurable (`SCRAPED_ACCOMMODATION_HTML_PATH`/
`SCRAPED_FLIGHT_HTML_PATH` in `.env.example`). With no file at either
path — the default state of a fresh clone — the corresponding inventory
honestly reports `unavailable`; nothing is fabricated. This repository
does not include or commit any sample HTML fixture. If you want to see a
non-empty inventory locally, you would need to supply your own local file
in the shape the parser expects (see
`backend/app/providers/accommodation/scraped_parser.py` and
`backend/app/providers/flights/scraped_parser.py`, and their test files,
for the expected structure).

### B. Kiwi MCP flight demo (live, third-party)

Kiwi's real flight-search MCP server can be queried live, but only with
two independent, explicit opt-ins — both required:

```bash
FLIGHT_PROVIDER=kiwi_mcp
KIWI_MCP_ENABLED=true
```

With both set, flight inventory is fetched live from Kiwi via the Model
Context Protocol. Any booking link this returns is a **provider-supplied
third-party link, never a booking confirmation**.

Manual smoke scripts (dev-only; never run in CI or by `pytest`; each
makes a real network call once its guard variables are set):

```bash
FLIGHT_PROVIDER=kiwi_mcp KIWI_MCP_ENABLED=true \
  python backend/scripts/manual_kiwi_mcp_tool_discovery_smoke.py

FLIGHT_PROVIDER=kiwi_mcp KIWI_MCP_ENABLED=true \
  python backend/scripts/manual_kiwi_mcp_flight_search_smoke.py
```

### C. AI-candidate-proposal demo (Anthropic / Groq)

`AI_CANDIDATE_PROPOSAL_PROVIDER` (default `not_connected`) can be set to
`anthropic` or `groq` so the AI-candidate-discovery shadow path can
propose place suggestions. This is reasoning/proposal only — the LLM's
output is never treated as factual travel data, and a proposal reaches
the itinerary only after independent provider-grounding and explicit
promotion (see "What It Does Today").

```bash
AI_CANDIDATE_PROPOSAL_PROVIDER=anthropic   # or: groq
AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED=true
ANTHROPIC_API_KEY=...                       # required if provider=anthropic
GROQ_API_KEY=...                            # required if provider=groq
```

Manual smoke script for the Anthropic path (dev-only; makes a real,
billable Anthropic API call once its guard variables are set — see
`docs/20_manual_anthropic_shadow_smoke.md`):

```bash
ANTHROPIC_API_KEY=sk-ant-... \
AI_CANDIDATE_PROPOSAL_PROVIDER=anthropic \
AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED=true \
python backend/scripts/manual_anthropic_shadow_smoke.py
```

---

## Demo Walkthrough

With the backend and frontend both running:

1. Open `http://localhost:3000` and fill in the trip form (destination,
   dates, travelers, pace) to create and generate a trip.
2. Read the **Trust Dashboard** first — it summarizes what's available,
   missing, or needs review across the whole plan.
3. Check the **Validation Report** for the plan's readiness status
   (`ready`/`needs_review`/`blocked`) and any critical issues or warnings.
4. Check **Provider Coverage** to see exactly which data sources were
   connected, not connected, or returned nothing for this trip.
5. Scroll the day-by-day **itinerary**: numbered stops, movement rows
   (shown only when provider-backed movement data exists), and the map
   view (a route path renders only when real route geometry exists for
   that leg).
6. If accommodation/flight providers are connected (see "Optional
   Provider-Backed Demos" above), check the **Accommodation/Flight
   Inventory** sections for their labeled source (bookable-provider,
   scraped/manual, or Kiwi MCP).
7. Submit feedback in the **Request changes** box.
8. Check **Regeneration Readiness** — it only allows regeneration when
   your feedback maps to a real affected stage and no lock is active;
   otherwise it explains exactly why not.

Real output depends entirely on which providers you've connected — a
default, unconfigured run will show mostly `not_connected`/`unavailable`
states, which is the expected, honest behavior described above, not a
failure of the demo.

### Screenshots/GIFs to capture

This repository does not yet include screenshots. If you're preparing a
demo or portfolio writeup, useful captures would be:

- [ ] the create-trip form
- [ ] the trust dashboard
- [ ] the validation report
- [ ] the day-by-day itinerary with the map/route-path view
- [ ] Kiwi MCP flight inventory (with `FLIGHT_PROVIDER=kiwi_mcp` enabled)
- [ ] the regeneration diff preview / version history

Capture these from your own real local run once you have one going —
never fabricate or mock up a screenshot to stand in for one.

---

## Troubleshooting

- **Backend fails with an import error / "No module named app"** — run
  `uvicorn` from inside `backend/` (or ensure `backend/` is on
  `PYTHONPATH`), and confirm the virtualenv with
  `requirements.txt`/`requirements-dev.txt` installed is active.
- **Frontend can't reach the backend** — confirm the backend is running
  on port 8000; the frontend defaults to `http://localhost:8000` via
  `NEXT_PUBLIC_API_BASE_URL`.
- **A trip shows `blocked`/`not_connected`/`unavailable` everywhere** —
  expected with no provider configuration changes; see "Expected Default
  Behavior" above.
- **Accommodation/flight inventory is always `unavailable`** — no local
  file exists at `SCRAPED_ACCOMMODATION_HTML_PATH`/
  `SCRAPED_FLIGHT_HTML_PATH`; see "Optional Provider-Backed Demos A."
  above.
- **Kiwi MCP flights don't return anything** — confirm both
  `FLIGHT_PROVIDER=kiwi_mcp` and `KIWI_MCP_ENABLED=true` are set (both
  are required); a network/tool failure and a legitimate zero-result
  search both report honestly rather than falling back to fake data.
- **AI candidate proposals don't run** — confirm
  `AI_CANDIDATE_PROPOSAL_PROVIDER` is `anthropic` or `groq`,
  `AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED=true`, and the matching
  `ANTHROPIC_API_KEY`/`GROQ_API_KEY` is set — a missing key returns an
  honest `not_connected` result rather than an error.

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

### Current Regeneration Status

Feedback-based regeneration is implemented, but as a narrow, MVP-scoped
workflow — not the full "regenerate anything" design the diagram above
implies:

- `POST /trips/{trip_id}/feedback` captures feedback and a preliminary,
  deterministic (keyword-based, non-AI) interpretation; it does not change
  the itinerary by itself.
- `GET /trips/{trip_id}/regeneration-readiness` explains whether
  regeneration can run right now, and exactly why not if it can't.
- `POST /trips/{trip_id}/regenerate` reruns only the affected stage(s) —
  and only when the request confirms intent (`confirm: true`), at least
  one feedback event is still pending, zero locks are active, and a real
  affected stage can be derived from that feedback. When it runs, it
  creates a new plan version and marks the feedback used as applied.
- Any active lock blocks regeneration entirely. The system does not work
  around a lock, merge changes past it, or "preserve" it by skipping just
  that item — a locked plan must be unlocked before regeneration can
  proceed at all.
- Every other case (missing/false `confirm`, no pending feedback, or
  feedback that doesn't map to a real stage) is a distinct, named refusal.
  No plan version is created and no itinerary content changes.
- `GET /trips/{trip_id}/regeneration-attempts` returns a full audit trail
  of every attempt, successful or refused.

A successful regeneration is not a claim that the plan improved, and not a
claim that the resulting plan is ready for real-world use without review —
validation still governs that separately, and still runs on its own terms.

Before touching regeneration code, run through
`docs/17_regeneration_manual_qa.md` to verify this safety contract still
holds.

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

## What It Does Today

Everything below is implemented and running today, not a roadmap item.

**Planning pipeline**
- A staged backend pipeline (`PlanningOrchestrator`) runs traveler profile
  → destination context → trip strategy → stay/transport → experience
  planning → validation as one deterministic sequence, with `PlanningState`
  as the single object every stage reads from and writes one owned section
  of.
- A parallel **LangGraph orchestration layer** wraps the same deterministic
  stages as an explicit graph, alongside the original synchronous pipeline
  — it changes how the stages are invoked, not what any stage is allowed
  to decide or invent.

**Trust and provider transparency**
- A frontend **trust dashboard** summarizes, per category, whether data is
  available, needs review, not connected, unavailable, failed, or
  partially available — reading only fields the backend already returned,
  never computing a new fact of its own.
- Provider coverage is tracked and shown per data source with an explicit
  status vocabulary (`success`, `unavailable`, `failed`, `not_connected`,
  and several more specific variants) instead of one ambiguous "no data"
  state.
- A validation report grades every generated plan `ready`, `needs_review`,
  or `blocked`, with critical issues, warnings, and suggestions grouped by
  category. Passing validation is not a claim that the plan is ready for
  real-world use without review.

**Scheduling and itinerary**
- Route-aware experience scheduling orders each day's stops using
  provider-backed movement data when it's available, with a documented,
  deterministic fallback order when it isn't.
- Scheduled stops are numbered in their actual schedule order, with
  movement rows between consecutive stops showing provider-backed
  duration/distance only when the backend actually has it.
- A map view renders a real, provider-backed route path between stops when
  route geometry exists for that leg, and draws no line at all — never an
  invented straight-line stand-in — when it doesn't.

**AI-suggested candidates**
- An AI-candidate-proposal pipeline (Anthropic primary, Groq a
  cheaper/dev-only alternative) can suggest places to consider, using a
  forced tool-use schema that structurally cannot include a price, rating,
  or coordinate. A suggestion only becomes eligible for scheduling after
  independent grounding against real provider/open data and an explicit
  promotion step — an AI suggestion alone is never enough.

**Regeneration, diffs, and versions**
- Feedback-driven regeneration (see "Current Regeneration Status" above)
  reruns only the affected plan section when it safely can, records a new
  version, and shows a diff preview and full version history — refusing by
  name, rather than guessing, whenever it can't safely proceed.

**Inventory and third-party data**
- Lodging inventory renders distinct states depending on its actual
  source: bookable-provider inventory, local/manual scraped data (labeled
  experimental/fragile and explicitly not official-provider data), or
  open-data location candidates (never presented as bookable).
- A hotel-ratings provider layer can enrich a lodging offer with a
  provider-backed rating snapshot when a ratings provider is configured —
  no live rating provider is connected today, and a rating is never
  presented as a quality guarantee even when one is connected.
- A real, live third-party flight data integration calls Kiwi through the
  Model Context Protocol (MCP) — gated behind two independent, explicit
  opt-in flags, off by default, and labeled distinctly from local/manual
  scraped flight data everywhere it appears.

**Frontend**
- A Section 179 polish pass reorganized the result page's visual
  hierarchy, fixed mobile text-overflow issues, and added keyboard-focus
  and screen-reader labeling — without hiding, collapsing, or removing any
  trust/validation/provider/inventory detail by default.

None of the above claims a hotel or flight is booked, a booking link is a
confirmation, scraped or open-data listings are official, a rating implies
quality, a route exists where the backend has none, or that a plan is
final just because it validated.

---

## Architecture Overview

**Backend** — a FastAPI service (`backend/app/`) layered as
`API → Orchestrator/Services → ProviderGateway → Provider Adapters`. Stage
services each own one section of `PlanningState` and never call an
external API directly.

**`PlanningState`** is the canonical object: one Pydantic model holding
every section of a trip's plan (traveler profile, destination context,
strategy, stay/transport, experience plan, validation report, feedback
history, locks, version history, provider coverage, and more). The
frontend renders directly from it and invents nothing client-side.

**`ProviderGateway` and provider adapters** are the *only* path to
external data. Real adapters exist for places (OpenStreetMap/Overpass +
Nominatim), weather (Open-Meteo), holidays (Nager.Date), currency
(Frankfurter), routing (OSRM), accommodation/flights (a local/manual
scraped-HTML parser, plus a live Kiwi MCP flight adapter), and a
hotel-ratings provider slot — see the provider matrix below for exact
status per source.

**LangGraph orchestration** wraps the same deterministic planning stages
in an explicit graph, run alongside the original synchronous pipeline —
it's a different way of invoking the stages, not a different set of rules
for what a stage may decide or invent.

**LLM providers (Anthropic / Groq)** are a reasoning and candidate-proposal
layer only. They can suggest a name and a rationale under a schema that
structurally forbids factual fields; they are never treated as a source of
price, rating, availability, route, or booking data, and a suggestion
never reaches the itinerary without independent provider-grounding and
explicit promotion.

**Validation and the trust dashboard** are a separate, user-visible
quality layer on top of the pipeline, not part of it: they read the
already-generated plan and provider coverage and report readiness/gaps —
they never generate or alter a travel fact themselves.

**Regeneration** is an MVP-scoped, narrow workflow: a confirmed request
with pending feedback and zero active locks can rerun the one plan section
that feedback maps to and create a new version; any active lock blocks
regeneration entirely rather than being worked around.

**Frontend** — a Next.js/React single-page result experience
(`frontend/app/page.tsx`) rendering the trust dashboard, validation
report, provider coverage, accommodation/flight inventory, the day-by-day
itinerary with route-aware scheduling and map paths, and the
feedback/regeneration/version-history workflow, all directly from
`PlanningState`.

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

Provider transparency is visible in the frontend through grouped provider
status, unavailable data, and data-source summaries.

---

## Data Honesty (No Fabrication)

This is the rule the rest of the product exists to protect:

- Unavailable data is always labeled `unavailable`, `not_connected`, or
  `failed` — never silently omitted, and never replaced with a guess.
- No fake hotels, flights, prices, ratings, routes, or booking links are
  ever generated, by the backend or by the frontend.
- An LLM never becomes a source of travel facts. It can propose a
  candidate and a rationale; every fact that reaches the user must trace
  back to real provider/open data, or to an AI proposal that was
  independently grounded against that data.
- A booking link, when present, is always a provider-supplied third-party
  link — never presented as a booking confirmation.
- Local/manual scraped (`scraped_local`) data is explicitly labeled
  experimental/fragile and source-limited. It is never presented as
  official-provider data.
- An open-data lodging or POI candidate is a location candidate only. It
  is never presented as bookable inventory unless a real, connected
  lodging-inventory provider actually backs it.

---

## Provider & Data Source Matrix

| Source | Used for | Status today |
|---|---|---|
| OpenStreetMap / Overpass + Nominatim | Places, restaurants, attractions, accommodation POIs, location resolution | Implemented, connected by default |
| Open-Meteo | Weather context | Implemented, connected by default |
| Nager.Date | Public holidays | Implemented, connected by default |
| Frankfurter | Currency conversion | Implemented, connected by default |
| OSRM | Route feasibility / route geometry | Implemented adapter. `ROUTING_PROVIDER` defaults to `not_connected`; `osrm` when explicitly configured. No route time or path is ever shown when not connected. |
| `scraped_local` (accommodation) | Lodging inventory | Local/manual static-HTML file parsing only — never live scraping. Enabled by default; honestly reports data as unavailable with no local file present. Labeled experimental/fragile, never official-provider data. |
| `scraped_local` (flights) | Flight inventory | Same local/manual static-HTML parsing as above, for flights. Never live scraping. |
| Kiwi (via MCP) | Live flight inventory | Real, live, third-party data. Explicit opt-in only — both `FLIGHT_PROVIDER=kiwi_mcp` and `KIWI_MCP_ENABLED=true` required. Off by default. Labeled distinctly from `scraped_local` everywhere it appears; a booking link is always labeled provider-supplied, not a booking confirmation. |
| Hotel-ratings provider layer | Lodging rating enrichment | Provider-backed rating snapshot when a real ratings provider is configured. No live rating provider connected today (`HOTEL_RATINGS_PROVIDER` defaults to `not_connected`). Never a quality guarantee even when connected. |
| Anthropic (primary) / Groq (dev-only) | AI-suggested candidate proposals, reasoning/explanation | Proposal and reasoning only — never a source of factual travel data. A suggestion must be independently grounded against real provider/open data before it can be anything more than a rejected/unpromoted candidate. |
| Amadeus, Google Places/Routes, Mapbox | Future accommodation/flight/routing coverage | Not connected — approved-access options for later, not implemented today |
| OpenTripPlanner + GTFS | Transit-specific routing | Planned/deferred — not implemented today |

Restricted providers — Airbnb, Booking.com, Expedia, Vrbo, Tripadvisor,
and Google Flights — are never scraped and never treated as connected.
They appear in this document only as a policy note, not as data sources.

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

Root-level docs (start here for the current-state summary):

```text
ARCHITECTURE.md      -- architecture reference, corrected to current implementation status
PLANNING_ENGINE.md   -- the staged PlanningState pipeline, stage by stage
TASKS.md             -- implementation history; see its own status note at the top
```

`itinerary-generator-build-spec.md` is an early, historical concept spec —
see its own banner. Where it disagrees with the files above or with actual
code, the files above (and the code) are authoritative.

The detailed architecture lives in `docs/`. Recommended reading order:

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
docs/17_regeneration_manual_qa.md
docs/18_candidate_quality.md
docs/20_manual_anthropic_shadow_smoke.md
docs/21_manual_provider_cache_smoke.md
docs/99_product_principles.md
docs/CODEBASE_OVERVIEW.md
```

`docs/CODEBASE_OVERVIEW.md` also carries a running, dated log of every
implementation section from 170 onward — it's the most current single
source for what actually changed and when.

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
Anthropic (Claude) -- primary AI-candidate-proposal LLM base
Groq -- cheaper/dev-only AI-candidate-proposal alternative
Schema-validated, tool-forced structured output
Candidate proposal and reasoning only -- never a factual data source
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

Architecture V1 is finalized, and the MVP is implemented and demo-ready as
of Sections 170-179 (see "What It Does Today" above, and
`docs/CODEBASE_OVERVIEW.md` for the full section-by-section log): a
working FastAPI backend and Next.js frontend, the full planning pipeline,
real provider integrations, and a real (if MVP-scoped — see "Current
Regeneration Status" above) feedback-driven regeneration workflow. This is
well past an "implementation begins with shared types" stage.

The backend has an automated test suite currently at 2296 passing tests
(`pytest`, run locally — see `.github/workflows/ci.yml` for what actually
runs in CI). The Kiwi MCP integration and the Section 179 frontend polish
pass were additionally checked with manual, dev-only smoke scripts and
manual browser verification (`backend/scripts/manual_kiwi_mcp_*.py`,
`backend/scripts/manual_anthropic_shadow_smoke.py`) — these are one-time,
local verification runs, not a permanent, CI-enforced guarantee that stays
true after every future change.

Deferred / not yet implemented (production-hardening work, tracked
separately from MVP feature work):

- async/background job processing for plan generation (today: a single
  synchronous request per generation call)
- PostgreSQL persistence (today: a local, gitignored JSON file — see
  `ARCHITECTURE.md` section 12a)
- authentication and per-user trip isolation (today: any caller who knows
  a `trip_id` can read or modify it)
- Docker/deployment hardening (the committed frontend Dockerfile runs
  `npm run dev`, not a production build; the compose `postgres`/`redis`
  services exist but nothing in the app talks to them yet)
- observability / structured logging

This is a working MVP with real integrations, not a finished production
system — the list above is what that gap actually consists of.

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