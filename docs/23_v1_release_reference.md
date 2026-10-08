# 23. TravelObligator V1 Release Reference

This is the detailed companion to [`README.md`](../README.md). It records what V1 is, how it is built and
deployed, how it was evaluated, and what it does not do. It describes the **current** state of the system at
the V1 release. Earlier designs are covered by the numbered documents `docs/00`–`docs/21` and the running log
in `docs/CODEBASE_OVERVIEW.md`; where those disagree with this file about the released system, this file and
the code are authoritative.

No secret, connection string, session value or token appears in this document.

## 1. Release status

V1 is deployed and production-verified, with one operational check still open (section 12).

| Item | Value |
| --- | --- |
| Frontend | <https://travelobligator.vercel.app> |
| Backend | <https://travelobligator-backend.onrender.com> (reached through the frontend's `/api/*` rewrite) |
| Deployed backend commit | `f55401c` — "fix: accept short equivalent city spellings" |
| Database schema (Alembic head) | `d41a7e2c9b53` |
| Hosting | Free tiers: Vercel Hobby, Render Free, Neon Free, Upstash Free |

This is a portfolio / research / demo deployment. It is not an SLA-backed production service.

**Release tagging is paused; a final quality phase is open.** The V1 candidate described here passed its
infrastructure and factual-safety acceptance, and that result stands: this document remains the
factual-safety and infrastructure baseline. A user-facing comparison then exposed itinerary-composition
quality gaps (day grouping, a requested interest not represented, a severe walking leg left as a warning,
scarce slots spent on weak candidates). No `v1.0.0` tag has been created. A final phase, *Itinerary Quality
& Day Composition*, was opened; its acceptance contract, development scenarios and new frozen quality
holdout are in [`24_itinerary_quality_contract.md`](24_itinerary_quality_contract.md). The tuning, holdout
and stress results in sections 8–10 are historical and unchanged, and the original holdout is not rerun.

## 2. Product scope

V1 plans a trip to a **single city**. It is a travel *decision* system: the itinerary is the output, and the
staged pipeline that produces, grounds, validates and explains it is the product.

Implemented in the released system:

- Account signup, login and logout; a signed, HttpOnly session cookie; every trip belongs to one user and is
  invisible to others.
- Asynchronous generation: the request returns `202`, and the browser polls the job to completion.
- PostgreSQL persistence. A finished trip is reloaded and reopened without regenerating it.
- LangGraph orchestration of the planning stages (`PLANNING_ENGINE_MODE=langgraph`, the default).
- Factual provider grounding: destination resolution, a broad candidate pool, named-place lookups, routing.
- AI anchor proposals that are grounded through the geocoder before they can be scheduled.
- AI itinerary reasoning, followed by deterministic day planning, diversity and spatial checks.
- Route-aware day plans with provider-backed distance and duration, mixed walking/driving adaptation, maps
  and movement rows between stops.
- Nearby food suggestions for each day.
- Weather, public-holiday and currency context where the provider has data.
- Provider transparency: what was provider-backed, what was AI-proposed and then grounded, and what was
  unavailable.
- Validation and readiness reporting (`ready`, `needs_review`, `blocked`).
- Feedback capture and refusal-first regeneration, with versions and revisions.
- Redis provider-response caching.
- CI, hardened containers, health/readiness endpoints and structured, redacted logging.

Not in V1: booking, payment, live hotel prices or availability, multi-city trips, a public-transit planner.

## 3. Final architecture

The governing rule:

> **LLMs propose and reason. Providers establish facts. Deterministic code enforces feasibility, safety,
> scheduling, validation and persistence.**

Production itinerary pipeline:

```text
traveler request
  -> Geoapify Geocoding        destination resolution (city-level, country-checked)
  -> Geoapify Places           broad factual candidate pool inside the destination boundary
  -> LLM anchor proposals      hypotheses only: names worth looking for
  -> Geoapify grounding        each proposed anchor is looked up; ungrounded ones are dropped
  -> candidate quality         deterministic scoring, duplicate and category hygiene
  -> inventory sufficiency     is there enough verified material for this trip length and pace?
  -> LLM itinerary reasoning   chooses and orders from verified candidates only
  -> deterministic planning    diversity, geographic spread, day structure, top-up
  -> Geoapify Routing          real distance/time per day, bounded mixed-mode adaptation
  -> validation / usefulness   readiness, review and blocking findings
  -> narrator                  prose over the finished plan; adds no facts
  -> PostgreSQL                state, revision and branch head committed together
```

`PlanningState` (`backend/app/models/planning_state.py`) is the single source of truth. Each section is owned
by one backend stage, and the frontend renders from it without inventing anything.

Inventory and usefulness: with `T = days × pace target` (relaxed 2, balanced 3, packed 4),
`R = ceil(0.8·T)` and `H = ceil(2.25·T)`, the count of verified, non-trivial candidates is `healthy` (≥ H),
`sufficient` (≥ T), `thin_but_usable` (≥ R) or `insufficient` (< R). When there is enough inventory the plan
must schedule at least R meaningful stops with no empty day. When there is not, the generation completes
honestly as `readiness=blocked` with `INSUFFICIENT_VERIFIED_INVENTORY`. A plan is never padded.

Layering: `API → orchestrator / stage services → ProviderGateway, repositories, validators → provider
adapters, database`. Stage services reach external data only through the gateway. More detail is in
[`docs/14_backend_architecture.md`](14_backend_architecture.md), [`ARCHITECTURE.md`](../ARCHITECTURE.md) and
[`PLANNING_ENGINE.md`](../PLANNING_ENGINE.md).

## 4. Production infrastructure

```text
Browser
  -> Vercel            Next.js frontend
  -> /api/*            same-origin rewrite on the frontend host
  -> Render            FastAPI backend, one Docker web service, one Uvicorn worker
       -> Neon         PostgreSQL, authoritative persistence
       -> Upstash      Redis, disposable provider-response cache
```

The browser does not normally call the Render backend directly.

**Frontend (Vercel).** Next.js standalone build. `BACKEND_ORIGIN` (build time, server side) creates the
`/api/:path*` rewrite; the runtime `API_BASE_URL=/api` tells the browser to use it. No backend credential
exists in the frontend environment.

**Backend (Render).** Built from `backend/Dockerfile` (`python:3.11.17-slim-bookworm`, multi-stage, non-root
uid 10001, `python -m app.serve`). `render.yaml` describes one free web service and holds no secret value.
`APP_ENV=production` turns on strict startup validation: PostgreSQL only, no SQLite cache, debug off, a
Secure/HttpOnly session cookie, a long session secret, https-only explicit CORS origins. Every response is
`Cache-Control: no-store`.

**Auth and sessions.** A signed session cookie: `Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/`, no `Domain`,
so it is host-scoped to the frontend origin. There is no token in `localStorage`. Every `/trips/*` route
checks the session and trip ownership.

**Persistence.** PostgreSQL is authoritative for users, trips, planning states, jobs, branches and revisions.
Migrations are an explicit step run before a deploy; the web process only verifies the Alembic head and
refuses to start on a mismatch. There is no fallback store. `PlanningState` writes are optimistic
(`lock_version` compare-and-set; a stale writer gets HTTP 409). Isolation is READ COMMITTED.

**Async jobs.** Generation runs as a FastAPI `BackgroundTasks` task owned by a database lease
(claim / heartbeat / conditional terminal transition). A partial unique index allows one active job per trip.
This is not a durable queue: see section 13.

**Caching.** Redis caches provider responses only, with TTLs by volatility. It is never authoritative and
never a lock. If Redis is down, provider calls run uncached and `/ready` reports `degraded`. AI output and
transient failures are never cached.

**Operations.** `/health` is public liveness only. `/ready` and `/metrics` require the ops bearer token and
answer 404 without it in production. Logs go through an allowlisted, redacting pipeline and never contain
feedback text, itinerary text, prompts, provider payloads, tokens or URLs.

Deployment contract and procedure: [`docs/22_free_tier_deployment.md`](22_free_tier_deployment.md).

## 5. Provider architecture

**Production (factual sources):**

| Provider | Supplies |
| --- | --- |
| Geoapify Geocoding | Destination resolution; grounding of named places |
| Geoapify Places | Broad factual candidate pool (attractions, food, stay-area points of interest) |
| Geoapify Routing | Distance and duration per day, walking and driving |
| Open-Meteo | Weather context |
| Nager.Date | Public holidays (an empty answer is reported as `unavailable`, not "no holidays") |
| Frankfurter | Currency rates, where applicable |

Geoapify usage is accounted per generation and capped at 100 credits; a call past the cap is refused locally.
There is no automatic fallback between factual data providers; for example, a Geoapify failure does not
silently switch to Nominatim, Overpass, or OSRM. LLM provider failover is a separate resilience mechanism
described in section 6. Provider result order is never treated as a prominence
signal.

**Development / experimental (not the production pipeline):**

| Provider | Status |
| --- | --- |
| OpenStreetMap / Overpass | Development-only places source; rejected at startup with `APP_ENV=production` |
| Nominatim | Development and test geocoder; the public endpoint is rejected in production |
| OSRM | Optional development routing adapter |
| `scraped_local` | Local, manual static-HTML parser for accommodation and flights; `unavailable` with no file |
| Kiwi via MCP | Opt-in live flight search; off by default |
| Anthropic adapters | Present for the shadow candidate-proposal path and feedback tooling; not the production LLM route |
| Transit, hotel ratings | No live source; reported as `not_connected` |

Restricted platforms (Airbnb, Booking.com, Expedia, Vrbo, Tripadvisor, Google Flights) are never scraped.

## 6. LLM resilience

Order of preference for every routed model stage (anchor proposal, itinerary reasoning, repair, narrator):

1. **Groq** — preferred primary.
2. **Gemini** — secondary.
3. **Deterministic fallback** — always available; never fabricates.

- Both providers receive the same grounded input and return the same wire schema, so everything after the
  call (reference resolution, validation, grounding, sanitizing) is one code path. Gemini is given no search
  or maps grounding tool and is never a factual source.
- Each stage has one wall-clock budget covering every attempt, and makes at most two provider requests.
- Provider health is tracked in process (healthy / draining / open / half-open). A rate limit opens the
  circuit until the provider's own reset; a 5xx, timeout or network error opens it for a short cooldown.
  A stage does not retry the provider that just failed.
- An answer that parsed but was rejected by validation or grounding never triggers a second request.
- Running out of budget is a latency outcome, not a factual one: the deterministic fallback takes over.

Failover was exercised during evaluation. In the final tuning run every one of the 18 generations used
provider failover, and Gemini completed most model stages (52 successful stage calls against 3 for Groq)
while Groq was rate-limited. Provider identity is reported for observability only and is never an acceptance
input.

## 7. Factual safety / no-fabrication invariants

An LLM **may**:

- propose names of places worth looking up,
- choose and order among candidates that a provider has already verified,
- explain a decision and write prose about a finished plan.

An LLM **may not** be the source of:

- a place identity,
- coordinates,
- a route, distance or travel time,
- a price,
- a rating,
- availability,
- a booking link.

Enforcement is structural, not a matter of prompting:

- A proposed anchor is scheduled only if the geocoder grounds it; otherwise it is dropped.
- Every scheduled place carries a provider source and provider place id.
- Data that a provider did not supply is marked `unavailable`; a provider that is not wired in is marked
  `not_connected`. Nothing is silently omitted or guessed.
- A provider outage degrades the plan honestly. It never produces invented travel facts.
- Validation runs before a plan is shown as final.
- An active user lock blocks regeneration outright.

## 8. Evaluation methodology

The benchmark is a fixed set of 28 cities defined in `backend/scripts/benchmark/cities.json`. That data is
never imported by application code, and no city or landmark name appears in production logic.

| Set | Size | Purpose |
| --- | --- | --- |
| Tuning | 18 cities | Used while improving the generic engine. Results on it are not an unbiased estimate. |
| Unseen holdout | 10 cities | Fixed in advance and run once, after the tuning freeze. |
| Stress | 8 scenarios | Holdout cities at varied trip lengths and paces (1-day relaxed to 5-day packed). |

Acceptance for a run: destination resolved; no fabricated or unverified scheduled identity; no duplicate
scheduled place; no unsupported factual claim; no empty day and at least R meaningful stops when inventory
allows; routing coverage ≥ 90%; persistence and reload succeed; the provider credit cap is not exceeded.

**Stopping rule.** Tuning stopped at a chosen freeze. The holdout was then run once and its result recorded
as it came out.

**Why the holdout was not rerun.** The holdout exposed a generic defect (section 10). The fix was validated
with a separate single-city canary. Rerunning the ten cities after fixing a bug they revealed would turn the
holdout into a second tuning set and inflate the score, so the original result stands as **9/10**.

## 9. Evaluation results

**Tuning freeze: 15 / 18 PASS.**

- The three failures were ordinary itinerary-quality limitations. None was a fabrication or safety failure.
- Across all 18: destination resolution 100%, factual-safety pass rate 100%, routing ≥ 90% in 100%,
  persistence 100%, no empty day, no city below R.

**Unseen holdout: 9 / 10 PASS (initial, unbiased, not rerun).**

| Metric | Value |
| --- | --- |
| Destination resolution rate | 0.9 |
| Viable ≥ R rate | 0.9 |
| Median target attainment | 1.0 |
| Fabricated identities | 0 |
| Duplicate scheduled places | 0 |
| Unsupported claims | 0 |
| Geoapify credits, median / max / total | 40.5 / 47 / 375 |
| Per-generation credit cap | 100 |

The single failure was Cusco, Peru (section 10).

**Post-fix Cusco canary: PASS.**

- Destination resolved; healthy inventory.
- 9 meaningful scheduled stops; no empty day; no duplicate.
- 100% routing coverage.
- No fabricated or unverified scheduled identity; no unsupported factual claim.
- Persistence and reload succeeded.
- 34 of 100 Geoapify credits.

**Stress suite: 8 / 8 PASS.**

| Metric | Value |
| --- | --- |
| Destination resolution rate | 1.0 |
| Viable ≥ R rate | 1.0 |
| Median target attainment | 0.95 |
| Fabricated identities | 0 |
| Duplicate scheduled places | 0 |
| Unsupported claims | 0 |
| Geoapify credits, median / max / total | 36.5 / 67 / 306 |
| Per-generation credit cap | 100 |

Degradation observed during the stress runs, from the terminal and runtime logs of those runs (the benchmark
result file itself does not record the routing events):

- S3 had narrator / model-stage degradation and used the deterministic narrative.
- S5 and S6 logged transient "Geoapify routing provider unavailable" events.
- All eight scenarios still passed. A degraded provider is reported as degraded; it is not counted as a
  success it did not deliver.

The raw result files are local evaluation output and are not tracked in the repository
(`benchmark_results/` is gitignored), so the figures above are the record.

## 10. Cusco holdout defect

**What happened.** For the request "Cusco, Peru", Geoapify returned "Cuzco, Cusco, Peru". The destination
resolver rejected it as `locality_mismatch`.

**Cause.** The resolver accepts a provider city whose name is one edit away from the requested name, as an
equivalent spelling. That rule required names of at least six characters. "Cusco" / "Cuzco" has five.

**Fix.** The minimum length for the one-edit equivalent-spelling rule is now five characters. The other
conditions are unchanged:

- the result must be city-level,
- the names must be exactly one edit apart,
- the country must agree strongly.

This is a generic rule about name length. There is no city-specific logic in production code. A regression
test was added (`backend/app/tests/providers/test_destination_plausibility_3c1.py`), and the change is commit
`f55401c`.

**Record.** The holdout stays at 9/10. The fix was confirmed by the separate canary in section 9.

## 11. Automated testing / CI

Latest clean local backend suite, after the Cusco regression test: **5938 passed, 98 skipped**. The skips are
tests gated on a real PostgreSQL or Redis instance; the CI integration job runs them and enforces zero skips.

GitHub checks passing on the release commit:

| Check | What it proves |
| --- | --- |
| `Backend / Hermetic` | `compileall` and the suite with no database or cache |
| `Backend / Integration` | One Alembic head, empty PostgreSQL migrated to head, full suite with both gates on, zero skips |
| `Frontend / Build` | Lockfile install, type check, lint, unit tests, production build |
| `Containers / Build & Smoke` | Both production images build; image audit; Trivy image gate; production-style Compose smoke |
| `Security / Scan` | Trivy secret scan and dependency vulnerability scan |
| Vercel deployment | The frontend builds and deploys |

CI runs on Python 3.11, Node 22, PostgreSQL 16 and Redis 7.4. It has no deploy step, no registry push and no
secret.

## 12. Production acceptance evidence

All checks below were performed against the deployed system.

**Backend**

- `/health` is public and returns 200.
- `/ready` and `/metrics` without the ops token return 404.
- Authenticated `/ready` returns 200 with status `ready`: PostgreSQL ready, schema expected head and current
  head both `d41a7e2c9b53`, Redis provider cache healthy.

**Vercel proxy**

- `/api/health` returns 200 with `Cache-Control: no-store`.
- `/api/ready` and `/api/metrics` return 404.
- Browser API traffic goes to `travelobligator.vercel.app/api/*`, not to the Render host.

**Authentication**

- Signup, login and logout work. After logout, `/api/auth/me` returns 401.
- The cookie is `Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/`, host-scoped to `travelobligator.vercel.app`.
- The session survives a reload while valid.
- `SESSION_SECRET_KEY` was rotated after a session-cookie value was exposed during testing. Old sessions
  were invalidated, confirmed by a fresh authentication.

**Persistence**

- Trip creation works in production.
- Asynchronous generation returns 202 and is polled to completion.
- A completed itinerary reloads without regeneration.
- An existing Lisbon trip was still present after the secret rotation and a fresh login.

**Production UI**

- A Lisbon 3-day balanced itinerary rendered with 9 scheduled places, maps, routes and movement rows.
- Limitations and unavailable data were labelled as such.
- No uncaught application error appeared during the final smoke.

**Render**

- The latest production deployment is on `f55401c`.

**Pending**

- Free-tier cold-start recovery after a genuine idle sleep has **not yet been verified**. The expected
  behaviour is a slow first request that recovers; it is recorded here as pending, not as passed.

## 13. Known limitations

- **Single city only.**
- **Hobby / free-tier deployment.** No SLA. Render stops the backend after inactivity, so the first request
  after idle is slow or briefly fails, and Neon suspends idle compute.
- **No durable job queue.** Generation runs as a `BackgroundTasks` task with database leases. A restart
  interrupts a running generation; the trip stays consistent, and the user retries.
- **No exactly-once distributed execution.** Process-local limits and health state describe one container.
- **Weather is advisory.** It is shown as context and does not reschedule the itinerary.
- **No public-transit planner.** There is no GTFS routing; movement is walking and driving.
- **Accommodation is guidance.** No live price or availability is implied unless a factual inventory
  provider supplied it.
- **Flights may be unavailable**, depending on provider and configuration.
- **No booking and no payment.**
- **Validation is not a real-world guarantee.** A plan that passes validation is internally consistent with
  the data available; opening hours, closures and conditions on the day can still differ.
- **Provider outages** produce honest `unavailable` or fallback behaviour, never invented travel facts.

## 14. Deferred / future work

These are roadmap items, not V1 blockers:

- Multi-city trips.
- A durable worker and queue for generation.
- Transit-specific planning (GTFS / OpenTripPlanner).
- Richer official inventory APIs for accommodation and flights.
- Booking and payment execution.
- A paid, high-availability deployment.
- Tracked screenshots for the README. None is in the repository yet; the intended location is
  `docs/assets/v1/`.

## 15. Release conclusion

TravelObligator V1 is complete for portfolio, research and demonstration purposes. It is deployed, its
core production acceptance checks have passed, and its evaluation is recorded as it happened: 15/18 on tuning, 9/10
on the unseen holdout with zero fabricated identities, duplicates or unsupported claims, a generic defect
found by the holdout and fixed without rerunning it, and 8/8 on stress.

One operational check remains open: cold-start recovery after a genuine idle sleep on the free Render
instance (section 12).

That conclusion covers infrastructure and factual safety. Itinerary-composition quality is being addressed
in the phase described in [`24_itinerary_quality_contract.md`](24_itinerary_quality_contract.md), and the
release tag waits for it (section 1).

The README before this release cleanup was a much longer implementation history. Its last version is in git
history at commit `f55401c`; the step-by-step detail it carried remains in
[`docs/14_backend_architecture.md`](14_backend_architecture.md),
[`docs/12_provider_architecture.md`](12_provider_architecture.md) and
[`docs/CODEBASE_OVERVIEW.md`](CODEBASE_OVERVIEW.md).
