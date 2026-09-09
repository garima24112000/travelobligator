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

Holiday and currency context are inferred deterministically from the
destination string — a small, exact-match table of country names plus
(as of Step 182B) all 50 US states/abbreviations and Washington D.C., so
a domestic US destination written the ordinary way ("Orlando, FL",
"Jersey City, NJ") resolves correctly instead of reporting `unavailable`.
A single ambiguous word (e.g. "Georgia," which is both a US state and a
country) still stays unresolved on purpose, rather than guessing.

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

### D. OSRM routing demo (no API key)

Route feasibility, movement rows, and map paths default to `not_connected`
(`ROUTING_PROVIDER=not_connected`). For a local/demo run with real route
data and **no API key or signup**, point `OSRM_BASE_URL` at the public
OSRM demo server:

```bash
ROUTING_PROVIDER=osrm
OSRM_BASE_URL=https://router.project-osrm.org
```

That demo server is free and requires no credentials, but it's a shared
public instance with no uptime or rate-limit guarantee — fine for local
development/demos, not something to point production traffic at. Leaving
`OSRM_BASE_URL` unset (the default) keeps routing honestly
`not_connected` even if `ROUTING_PROVIDER=osrm` is set.

### E. Itinerary narrator demo (Anthropic / Groq, Step 182F)

A completely separate feature from the AI-candidate-proposal demo above
(C) — don't confuse `ITINERARY_NARRATOR_*` with `AI_CANDIDATE_*`. This
narrator only ever reads an already-finished `PlanningState` (destination,
dates, scheduled place names/reasons, a plain weather range, offer
counts, validation status) and writes polished, traveler-facing prose —
it can never create a hotel, flight, price, rating, route, or booking,
and it never changes provider coverage, validation readiness, route
feasibility, or any other factual field. Off by default
(`ITINERARY_NARRATOR_ENABLED=false`); generation and regeneration always
succeed whether it's disabled or its provider call fails.

**Groq:**
1. Create a key in the [GroqCloud Console](https://console.groq.com).
2. Put it in `GROQ_API_KEY` in your local `.env`.
3. Set:
   ```bash
   GROQ_API_KEY=...
   ITINERARY_NARRATOR_PROVIDER=groq
   ITINERARY_NARRATOR_ENABLED=true
   ```

**Anthropic:**
1. Create a key in the Claude/Anthropic Console, under Settings > API keys.
2. Put it in `ANTHROPIC_API_KEY` in your local `.env`.
3. Set:
   ```bash
   ANTHROPIC_API_KEY=...
   ITINERARY_NARRATOR_PROVIDER=anthropic
   ITINERARY_NARRATOR_ENABLED=true
   ```

Restart the backend, then generate a trip: Traveler view shows a short
trip summary near the top of the itinerary and a short narrative inside
each day card; Developer view's "Itinerary narrative (AI)" section shows
the exact status/provider/model and which `PlanningState` fields were
used. With no key or `ITINERARY_NARRATOR_ENABLED=false` (the default),
Traveler view shows nothing extra (no fake summary), and Developer view
shows a calm `not_connected`/disabled reason — never a fabricated
narrative either way. Remove the key (or restore your `.env`) after
testing, same as the other optional demos above.

---

## Demo Walkthrough

The result page has a **Traveler view / Developer view** toggle (Traveler
view is the default). Traveler view prioritizes the actual itinerary —
a concise trip-context summary, the day-by-day plan, one trip-level
"Where to stay" section, one trip-level "Flights" section, and
feedback/regeneration — in that order. Developer view exposes the full
diagnostic experience described below — Trust Dashboard, full Validation
Report, Provider Coverage, Regeneration Readiness/Audit, and the raw
candidate inventories. Nothing is deleted in Traveler view, only hidden;
switch to Developer view to see it. The steps below describe Developer
view.

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
9. If the itinerary narrator is enabled (see "Optional Provider-Backed
   Demos" E above), check the **Itinerary narrative (AI)** section —
   status/provider/model and which `PlanningState` fields were used.
   Disabled (the default) shows a calm `not_connected`/disabled reason,
   never a fabricated narrative.

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
| `scraped_local` / `manual_html` (accommodation) | Lodging inventory | Local/manual static-HTML file parsing only — never live scraping. Enabled by default; honestly reports data as unavailable with no local file present. Labeled experimental/fragile, never official-provider data. `manual_html` (Step 182E) is a non-breaking alias for `scraped_local` — same adapter. Optional `ACCOMMODATION_MANUAL_HTML_SOURCE` cosmetic label (e.g. `booking`/`expedia`) relabels displayed provenance only. |
| `scraped_local` / `manual_html` (flights) | Flight inventory | Same local/manual static-HTML parsing as above, for flights. Never live scraping. Optional `FLIGHT_MANUAL_HTML_SOURCE` cosmetic label (e.g. `skyscanner`) relabels displayed provenance only — never confused with Kiwi MCP below. |
| Kiwi (via MCP) | Live flight inventory | Real, live, third-party data. Explicit opt-in only — both `FLIGHT_PROVIDER=kiwi_mcp` and `KIWI_MCP_ENABLED=true` required. Off by default. Labeled distinctly from `scraped_local`/`manual_html` everywhere it appears; a booking link is always labeled provider-supplied, not a booking confirmation. |
| Hotel-ratings provider layer | Lodging rating enrichment | Provider-backed rating snapshot when a real ratings provider is configured. No live rating provider connected today (`HOTEL_RATINGS_PROVIDER` defaults to `not_connected`). Never a quality guarantee even when connected. |
| Anthropic (primary) / Groq (dev-only) | AI-suggested candidate proposals, reasoning/explanation | Proposal and reasoning only — never a source of factual travel data. A suggestion must be independently grounded against real provider/open data before it can be anything more than a rejected/unpromoted candidate. |
| Anthropic / Groq (itinerary narrator, Step 182F) | Read-only, presentation-prose summary of an already-finished plan | Completely separate feature/config surface from the AI-candidate-proposal row above (`ITINERARY_NARRATOR_*`, never `AI_CANDIDATE_*`). Off by default. Reads `PlanningState` only — never creates a hotel, flight, price, rating, route, or booking, and never changes provider coverage, validation readiness, or route feasibility. Generation/regeneration always succeed whether it's disabled or fails. |
| Booking.com Demand API, Expedia Rapid API, Hotelbeds, Hostelworld, Vrbo, Airbnb, Skyscanner Travel API, Tripadvisor Content API | Future lodging/flight/rating coverage | Not connected — partner/paid-access options, credential placeholders declared but not wired into any adapter. See "Provider Activation" below for access steps and manual-fallback guidance for each. |
| Amadeus, Google Places/Routes, Mapbox | Future accommodation/flight/routing coverage | Not connected — approved-access options for later, not implemented today |
| OpenTripPlanner + GTFS | Transit-specific routing | Planned/deferred — not implemented today |

Restricted providers — Airbnb, Booking.com, Expedia, Vrbo, Tripadvisor,
and Google Flights — are never scraped and never treated as connected.
They appear in this document only as a policy note, not as data sources.

---

## Provider Activation: APIs and Manual Fallback

For every real or aspirational data source this app can use, this section
states: what it's for, its current MVP status, whether it needs a key,
how to get access at a high level, which env variable(s) it reads, its
fallback path when access is paid/partner-blocked/unavailable, and what
the app shows when it's missing. This app never fabricates data — a
missing key or file always produces an honest `not_connected`/
`unavailable` result, never a fake one.

**Kiwi MCP** (flights, real/live)
- Status: implemented, off by default.
- Key needed: no normal API key in this implementation — it talks to
  Kiwi's hosted MCP server directly.
- Access: none to arrange; it's a public MCP endpoint.
- Env: `FLIGHT_PROVIDER=kiwi_mcp` and `KIWI_MCP_ENABLED=true` (both
  required — either alone does nothing live). Endpoint defaults to
  `KIWI_MCP_ENDPOINT=https://mcp.kiwi.com`.
- Fallback: the manual/local HTML flight fallback below.
- Missing: `flight_inventory_report.status` stays `not_connected` and
  `FLIGHT_PROVIDER` stays at its default (`scraped_local`).

**Groq** (AI candidate proposal/reasoning only — never a factual data
source)
- Status: implemented, off by default.
- Key needed: yes.
- Access: get a key from the GroqCloud console.
- Env: `GROQ_API_KEY=<your key>`, `AI_CANDIDATE_PROPOSAL_PROVIDER=groq`,
  and `AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED=true` to actually run
  it during generation.
- Fallback: none needed — this is a proposal/reasoning aid, not a
  required data source; the app works fully without it.
- Missing: `AnthropicAICandidateProposalProvider`/
  `GroqAICandidateProposalProvider.propose` return an honest
  `not_connected` result; no candidate is proposed, and nothing in the
  itinerary changes.

**Anthropic** (AI candidate proposal/reasoning only — same rule as Groq;
this is the primary wired base)
- Status: implemented, off by default.
- Key needed: yes.
- Access: create a key in the Claude/Anthropic Console, under API keys.
- Env: `ANTHROPIC_API_KEY=<your key>`,
  `AI_CANDIDATE_PROPOSAL_PROVIDER=anthropic`, and
  `AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED=true`.
- Fallback: none needed, same reasoning as Groq.
- Missing: same honest `not_connected` behavior as Groq.

**Booking.com Demand API** (lodging)
- Status: not implemented — no adapter exists.
- Key needed: yes, if ever implemented — a managed affiliate/Demand API
  partner relationship with Booking.com, not a self-serve signup.
- Access: apply through Booking.com's partner/affiliate program; a real
  adapter would only be added once real credentials and a documented
  request/response schema exist.
- Env: `BOOKING_DEMAND_API_KEY`/`BOOKING_DEMAND_API_BASE_URL` are
  declared as placeholders only — not read by any adapter today.
- Fallback: the manual/local HTML accommodation fallback below (label it
  `ACCOMMODATION_MANUAL_HTML_SOURCE=booking` for clearer provenance —
  still never official Booking.com data).
- Missing (always, today): `accommodation_provider` never resolves to a
  Booking adapter — selecting `"booking"` explicitly falls back to
  `not_connected` (see `get_accommodation_provider`'s "unsupported name"
  behavior).

**Expedia Rapid API** (lodging)
- Status: not implemented.
- Key needed: yes, if ever implemented — Rapid API / partner access via
  Expedia Partner Solutions, not self-serve.
- Access: apply through Expedia Partner Solutions.
- Env: `EXPEDIA_RAPID_API_KEY`/`EXPEDIA_RAPID_API_SECRET`/
  `EXPEDIA_RAPID_API_BASE_URL` — placeholders only.
- Fallback: manual/local HTML accommodation fallback
  (`ACCOMMODATION_MANUAL_HTML_SOURCE=expedia`).
- Missing (always, today): same `not_connected` fallback as Booking.com.

**Hotelbeds API** (lodging)
- Status: not implemented.
- Key needed: yes, if ever implemented — an API key and secret issued
  after registering as a Hotelbeds API partner.
- Access: register at the Hotelbeds APIs developer portal.
- Env: `HOTELBEDS_API_KEY`/`HOTELBEDS_SECRET`/`HOTELBEDS_API_BASE_URL` —
  placeholders only.
- Fallback: manual/local HTML accommodation fallback
  (`ACCOMMODATION_MANUAL_HTML_SOURCE=hotelbeds`).
- Missing (always, today): same `not_connected` fallback.

**Hostelworld Partner/Affiliate API** (lodging)
- Status: not implemented.
- Key needed: yes, if ever implemented — partner/affiliate API access.
- Access: apply through Hostelworld's partner program.
- Env: `HOSTELWORLD_API_KEY`/`HOSTELWORLD_API_BASE_URL` — placeholders
  only.
- Fallback: manual/local HTML accommodation fallback
  (`ACCOMMODATION_MANUAL_HTML_SOURCE=hostelworld`).
- Missing (always, today): same `not_connected` fallback.

**Vrbo** (vacation rentals — partner/connectivity, effectively an Expedia
Group path)
- Status: not implemented.
- Key needed: yes, if ever implemented — partner/connectivity access,
  routed through the same Expedia Group partner program as Expedia Rapid
  API above.
- Access: apply through Expedia Group's partner/connectivity program.
- Env: `VRBO_PARTNER_API_KEY`/`VRBO_PARTNER_API_BASE_URL` — placeholders
  only.
- Fallback: the generic accommodation parser's manual/local HTML file
  handles vacation-rental-style listings the same as hotel listings (it
  has no lodging-type distinction) — label it
  `ACCOMMODATION_MANUAL_HTML_SOURCE=vrbo`. If you'd rather not label it
  at all, it's just `not_connected`/manual review.
- Missing (always, today): same `not_connected` fallback.

**Airbnb** (lodging — partner/API program only; a restricted provider,
never scraped)
- Status: not implemented, and not scraped under any circumstance —
  Airbnb is on this app's restricted-provider list.
- Key needed: yes, if ever implemented — Airbnb's official partner/API
  program, which is invite/approval-based, not self-serve.
- Access: apply through Airbnb's partner program (no guarantee of
  acceptance).
- Env: `AIRBNB_PARTNER_API_KEY`/`AIRBNB_PARTNER_API_BASE_URL` —
  placeholders only, useful only if a real partnership is ever arranged.
- Fallback: manual/local HTML accommodation file only
  (`ACCOMMODATION_MANUAL_HTML_SOURCE=airbnb`) — and the label always
  reads "not official Airbnb data"; this app never implies an official
  Airbnb connection under any configuration.
- Missing (always, today): same `not_connected` fallback.

**Skyscanner Travel API** (flights)
- Status: not implemented.
- Key needed: yes, if ever implemented — Travel API access is
  approval/commercial, not self-serve signup.
- Access: apply through Skyscanner's Travel API partner program.
- Env: `SKYSCANNER_API_KEY`/`SKYSCANNER_API_BASE_URL` — placeholders
  only.
- Fallback: manual/local HTML flight fallback
  (`FLIGHT_MANUAL_HTML_SOURCE=skyscanner`).
- Missing (always, today): `flight_provider` never resolves to a
  Skyscanner adapter — selecting `"skyscanner"` explicitly falls back to
  `not_connected`.

**Tripadvisor Content API** (hotel ratings/reviews)
- Status: not implemented — `hotel_ratings_provider` only supports
  `not_connected` today; no automated Tripadvisor scraping is implemented
  or planned anywhere in this codebase.
- Key needed: yes, if ever implemented — a Content API key issued through
  Tripadvisor's developer/API access path.
- Access: apply through the Tripadvisor Content API developer portal.
- Env: `TRIPADVISOR_API_KEY`/`TRIPADVISOR_API_BASE_URL` — placeholders
  only.
- Fallback: none implemented in this step. A manual/local
  review-fallback file is not currently modeled — rather than force one
  in, hotel ratings simply stay `not_connected` until a real,
  credentialed adapter (or a deliberately-designed manual fallback) is
  built with its own tests.
- Missing (always, today): `provider_coverage.hotel_ratings` stays
  `None` (enrichment was never attempted) whenever no accommodation
  offers exist to enrich, or reports `not_connected` otherwise — never a
  fabricated rating.

**Manual/local HTML accommodation fallback**
- Used for: lodging inventory when no official API is connected — the
  default path today.
- Status: implemented (`ScrapedAccommodationProvider`).
- Key needed: no.
- Access: none — you supply a local HTML file yourself (e.g. saved from
  a browser you're legally permitted to view), at the path
  `SCRAPED_ACCOMMODATION_HTML_PATH` (default
  `.data/manual_scrapes/accommodations.html`). This app never fetches
  that page itself.
- Env: `ACCOMMODATION_PROVIDER=scraped_local` (or the `manual_html`
  alias, Step 182E — same adapter either name), plus
  `SCRAPED_ACCOMMODATION_HTML_PATH`/`SCRAPED_ACCOMMODATION_SOURCE_ID`/
  `SCRAPED_ACCOMMODATION_SOURCE_NAME`/`ACCOMMODATION_MANUAL_HTML_SOURCE`.
- Fallback: this *is* the fallback for every partner-blocked lodging
  source above.
- Missing: with no file at the configured path, `accommodation_inventory_report.status`
  is honestly `unavailable`, `offers` stays empty — never a fabricated
  offer.

**Manual/local HTML flight fallback**
- Used for: flight inventory when no official/live API is connected —
  the default path today (Kiwi MCP is the one real, live exception, and
  is off by default).
- Status: implemented (`ScrapedLocalFlightProvider`).
- Key needed: no.
- Access: same as the accommodation fallback — you supply the local file
  yourself, at `SCRAPED_FLIGHT_HTML_PATH` (default
  `.data/manual_scrapes/flights.html`).
- Env: `FLIGHT_PROVIDER=scraped_local` (or the `manual_html` alias, Step
  182E), plus `SCRAPED_FLIGHT_HTML_PATH`/`SCRAPED_FLIGHT_SOURCE_ID`/
  `SCRAPED_FLIGHT_SOURCE_NAME`/`FLIGHT_MANUAL_HTML_SOURCE`.
- Fallback: this *is* the fallback for Skyscanner above (and for Kiwi
  when Kiwi MCP is disabled).
- Missing: with no file at the configured path,
  `flight_inventory_report.status` is honestly `unavailable` — never a
  fabricated offer, flight number, airline, or schedule.

**Manual/local review/rating fallback**
- Not implemented in this step. `hotel_ratings_provider` only supports
  `not_connected` — there is no manual/local HTML parser for ratings or
  reviews today, and Step 182E deliberately does not force one into
  product behavior. If one is built later, it will follow the exact same
  pattern as the accommodation/flight fallbacks above: a local file the
  user supplies, parsed by a static parser, never a live fetch.

For every manual/local HTML fallback above: a `*_MANUAL_HTML_SOURCE`
label (e.g. `booking`, `skyscanner`) only changes the *displayed*
`source_id`/`source_name` on parsed offers — never a claim that the named
provider's real API was used. The generated label always reads "labeled
by user" / "not official `<Name>` data." Setting a
`*_MANUAL_HTML_SOURCE` value never enables scraping, never bypasses a
login/paywall/CAPTCHA/bot-protection/rate-limit, and never calls the
named provider's website in any way — it only relabels a file you
already supplied yourself.

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

The backend has an automated test suite currently at 2436 passing tests
(`pytest`, run locally, confirmed to run completely hermetically with no
`.env` file present — see `.github/workflows/ci.yml` for what actually
runs in CI). The Kiwi MCP integration, the itinerary narrator, and the
Section 179/182 frontend polish passes were additionally checked with
manual, dev-only smoke scripts and manual browser verification
(`backend/scripts/manual_kiwi_mcp_*.py`,
`backend/scripts/manual_anthropic_shadow_smoke.py`) — these are one-time,
local verification runs, not a permanent, CI-enforced guarantee that stays
true after every future change. Section 182's own final verification
(Step 182G) additionally ran one real generated trip with every optional
no-key/real-key provider active at once (OSRM, Kiwi MCP, manual/local
HTML lodging, and the Groq itinerary narrator with a real, never-committed
key) — see `docs/CODEBASE_OVERVIEW.md`'s Step 182G entry for the full
account, including one small real Traveler-view copy bug it found and
fixed.

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