# 22. Free-Tier Deployment Contract (Section 203B)

This document is the deployment **contract** for the portfolio release, and the record of carrying it out.

| Stage | Status |
| --- | --- |
| 203B — contract prepared and validated | Completed |
| 203C — deployment ([section 11](#11-deployment-checklist-executed-in-203c)) | Completed |
| 203D — production acceptance ([section 14](#14-production-verification-record)) | Completed, except cold-start recovery (pending) |

Live deployment URLs are recorded in `README.md` and `docs/23_v1_release_reference.md`. This contract uses
`<VERCEL_FRONTEND_ORIGIN>` and `<RENDER_BACKEND_ORIGIN>` throughout so it remains reusable and contains no
deployment-specific hosted URL.

It is a hobby/portfolio deployment on free tiers, **not** an SLA-backed production service. Target recurring
cost: $0/month within each provider's current free-tier limits. No step in this document needs a payment
method, and nothing in the application upgrades a plan or falls back to a paid resource.

No secret value appears in this file. Placeholders used throughout:

| Placeholder | Meaning |
| --- | --- |
| `<NEON_DATABASE_URL>` | Neon PostgreSQL connection string (secret) |
| `<UPSTASH_REDIS_URL>` | Upstash Redis TLS connection string (secret) |
| `<RENDER_BACKEND_ORIGIN>` | The backend's public `https://` origin on Render |
| `<VERCEL_FRONTEND_ORIGIN>` | The frontend's public `https://` origin on Vercel |

## 1. Topology

```text
Browser
  -> <VERCEL_FRONTEND_ORIGIN>            Next.js frontend (Vercel Hobby)
  -> <VERCEL_FRONTEND_ORIGIN>/api/*      same-origin; rewritten by the frontend host
  -> <RENDER_BACKEND_ORIGIN>/*           FastAPI, one Docker web service (Render Free)
       -> Neon PostgreSQL                authoritative state (TLS)
       -> Upstash Redis                  disposable provider-response cache (TLS)
```

The browser does not normally call the Render backend directly. Because every API call is same-origin with the page, the
session cookie is a first-party, host-only cookie and no cross-site cookie behaviour is relied on.

## 2. Invariants carried forward (unchanged by 203B)

- PostgreSQL is the durable, authoritative store. If it is unavailable, `/ready` is `not_ready` and persistence
  operations fail safely (sanitized HTTP 503). There is **no** SQLite or Local JSON production fallback.
- Redis is only the provider-response cache. If it is unavailable the backend keeps serving, uncached, and
  `/ready` is `degraded`. There is no SQLite stand-in for Redis.
- The web process never runs migrations. It verifies the Alembic head at startup and refuses to start on a
  mismatch.
- One backend container runs exactly one Uvicorn worker.
- `BackgroundTasks` remains the generation executor for V1. It is not a durable queue (see section 8).

## 3. Frontend on Vercel

| Setting | Where | Value |
| --- | --- | --- |
| Root Directory | Vercel project settings | `frontend` |
| Framework | auto-detected | Next.js (no `vercel.json` is needed or present) |
| `BACKEND_ORIGIN` | Vercel environment variable (build) | `<RENDER_BACKEND_ORIGIN>` |
| `API_BASE_URL` | Vercel environment variable (runtime) | `/api` |

- `frontend/next.config.ts` adds the rewrite `/api/:path*` -> `<BACKEND_ORIGIN>/:path*` **only** when
  `BACKEND_ORIGIN` is set. The value is read when the frontend is built, so changing it requires a redeploy.
  It must be a bare `http(s)` origin; a malformed value fails the build with a message that does not echo it.
- `BACKEND_ORIGIN` is not a `NEXT_PUBLIC_*` variable and is not compiled into browser JavaScript. It is not a
  secret either (the address is discoverable), but there is no reason to publish it.
- `API_BASE_URL=/api` tells the browser to use the same-origin prefix. `frontend/lib/runtime-config.ts` accepts
  either an absolute `http(s)` URL (local development, Docker) or a same-origin path prefix.
- With `BACKEND_ORIGIN` unset nothing changes: `npm run dev`, the Docker image and CI build without it and the
  browser calls the absolute `API_BASE_URL` (default `http://localhost:8000`) exactly as before.
- No backend credential exists in the frontend environment at all.

**Caching.** The backend sends `Cache-Control: no-store` on every response (section 6), and the frontend
config sets the same header on `/api/:path*`. API traffic is never cached at the CDN. Static frontend assets
keep normal Next.js/Vercel caching.

**Confirmed in 203C/203D.** The hosted rewrite forwards `Set-Cookie` and `Cache-Control` unchanged: the
session cookie arrives host-scoped to the Vercel origin and `/api/health` carries `no-store` (section 14). The
same rewrite had been exercised locally against a self-hosted Next.js server in 203B. The platform's time
limit for a proxied request was not measured; generation is a polled background job, so no single request
depends on it.

## 4. Sessions and cookies

The session cookie is set by `backend/app/auth/sessions.py` with no `Domain` attribute, so it is host-only: the
browser binds it to the host it called, which through the rewrite is the frontend origin.

| Attribute | Production value | Source |
| --- | --- | --- |
| `Secure` | true | `SESSION_COOKIE_SECURE=true` (required when `APP_ENV=production`) |
| `HttpOnly` | true | `SESSION_COOKIE_HTTPONLY=true` (required) |
| `SameSite` | `lax` | `SESSION_COOKIE_SAMESITE=lax` (`none` is rejected in production) |
| `Domain` | not set | host-only by construction |
| `Path` | `/` | so the cookie is sent to `/api/*` |

Signup, login, `/auth/me`, logout and authenticated trip requests all go through the same fetch helper
(`frontend/lib/api.ts`, `credentials: "include"`). Authentication stays a signed HttpOnly cookie; there is no
`localStorage` token.

## 5. CORS

Normal browser traffic is same-origin and does not use CORS. The backend is still configured narrowly:

- `BACKEND_CORS_ORIGINS` is an explicit comma-separated list. A wildcard, an entry with credentials, or an
  entry that is not a bare origin stops startup in every environment.
- With `APP_ENV=production` every entry must be `https`. The development default (`http://localhost:3000`) is
  therefore rejected in production and must be replaced explicitly with `<VERCEL_FRONTEND_ORIGIN>`.
- Local development keeps the `http://localhost:3000` default.
- CORS is not authentication: every route checks the session and trip ownership itself.

## 6. Backend on Render

`render.yaml` (repository root) describes exactly one free Docker web service built from `backend/Dockerfile`.
It defines no database, no Redis, no worker, no cron job, no disk and no pre-deploy command, and holds no
secret value.

| Contract | How it is met |
| --- | --- |
| Production image, non-root | `backend/Dockerfile` unchanged (uid 10001, no source volume) |
| One Uvicorn worker | `python -m app.serve` (`workers=1`, no reload) |
| Bind `0.0.0.0`, respect `PORT` | `app/serve.py` reads `HOST` (default `0.0.0.0`) and `PORT` |
| Platform liveness | `healthCheckPath: /health` (no database, cache or provider call) |
| No persistent filesystem | no local state is written; do not set `LOCAL_STORAGE_PATH` / `PROVIDER_CACHE_PATH` |
| No auto-migration | startup only verifies the schema head |
| Migrations before deploys | `autoDeploy: false`; deploys are started by hand after migrating |

Every backend response carries `Cache-Control: no-store` (`app/core/no_store_middleware.py`), including error
responses and CORS preflights.

**Ops endpoints** (`app/api/routes/ops.py`):

| Endpoint | Public production behaviour |
| --- | --- |
| `/health` | Public. Liveness only; reveals no configuration. |
| `/ready` | Requires `Authorization: Bearer <OPS_TOKEN>`; otherwise 404. With no `OPS_TOKEN` in production: always 404. |
| `/metrics` | Disabled (`METRICS_ENABLED=false`). If enabled, it also requires the `OPS_TOKEN` bearer. |

Outside production with no `OPS_TOKEN`, `/ready` and `/metrics` stay open so local Compose and CI probes work
unchanged. Neither endpoint returns a secret or a URL.

**Cold start.** A free Render service is stopped after a period without traffic and started again by the next
request, which then waits for the container (and possibly Neon) to wake. This is expected. The first request
after idle can take around a minute, or be answered by the proxy with an error page. The frontend reports
either case as "The server did not respond. It may be starting up after a period of inactivity..." and the
visitor retries; it never reports the service as permanently down, and job polling keeps retrying. There is
deliberately **no** keepalive ping, cron job or uptime monitor to defeat the sleep policy.

## 7. PostgreSQL on Neon

- `DATABASE_URL` accepts the standard Neon string, including `?sslmode=require&channel_binding=require`; the
  parameters are passed through to libpq (psycopg 3). `PERSISTENCE_BACKEND=postgres`.
- **Use Neon's direct connection string, not the pooled (`-pooler`) one.** The application sets
  `statement_timeout` and `lock_timeout` as connection startup options and relies on session-level behaviour
  (row locks, transaction-local settings); a transaction-pooling proxy in front of that is not part of the
  tested contract. The application's own pool is small and bounded, so the direct endpoint is sufficient.
- Preserved unchanged: `pool_pre_ping`, bounded pool (`DB_POOL_SIZE` + `DB_MAX_OVERFLOW`), connect timeout,
  statement timeout, lock timeout, READ COMMITTED transaction semantics, startup Alembic head verification.
- Neon suspends idle compute. A pooled connection that died while suspended is detected by the pre-ping and
  replaced transparently; the first connection after a suspend waits for the compute to resume, which is why
  `render.yaml` raises `DB_CONNECT_TIMEOUT_SECONDS` to 15. Nothing in the backend queries the database while
  idle (`/health` does not touch it), so the application does not keep Neon awake.
- No Neon-specific code exists.

## 8. Redis on Upstash, and what free hosting does to jobs

- `PROVIDER_CACHE_BACKEND=redis`, `REDIS_URL=<UPSTASH_REDIS_URL>` using the `rediss://` (TLS) endpoint over
  the normal Redis protocol. No Upstash REST API is used. For a `rediss://` URL the client verifies the
  certificate chain and the hostname against the image's system CA store.
- Preserved unchanged: Redis is optional for correctness, an outage means uncached provider calls, the
  connection-failure cooldown/bypass, the per-provider TTLs, and the single process-level pooled client.
- The cache needs no persistence. Losing every key only causes future cache misses.

**Interrupted generation.** Render may stop or restart the service at any time (sleep, redeploy, platform
maintenance), and there is no durable background worker. A generation that is running when the process stops
does not complete. On a graceful stop the job is closed as `JOB_INTERRUPTED`; on a hard stop its database lease
expires and the next startup closes it. State transitions are conditional and idempotent, so the trip is not
corrupted, and the user can start the generation again. This is not exactly-once execution and 203B does not
change that.

## 9. Variable manifest

Names only. Classification: **public** = non-sensitive configuration, **secret** = credential supplied by
you, **generated** = secret generated at deploy time, **provider** = third-party API credential.

Backend (Render):

| Variable | Class | Production value / note |
| --- | --- | --- |
| `APP_ENV` | public | `production` (turns on strict startup validation) |
| `APP_DEBUG` | public | `false` |
| `PERSISTENCE_BACKEND` | public | `postgres` |
| `DATABASE_URL` | secret | `<NEON_DATABASE_URL>` (direct endpoint) |
| `PROVIDER_CACHE_BACKEND` | public | `redis` |
| `REDIS_URL` | secret | `<UPSTASH_REDIS_URL>` (`rediss://`) |
| `SESSION_SECRET_KEY` | generated | at least 32 characters; generated by Render |
| `SESSION_COOKIE_SECURE` | public | `true` |
| `SESSION_COOKIE_HTTPONLY` | public | `true` |
| `SESSION_COOKIE_SAMESITE` | public | `lax` |
| `BACKEND_CORS_ORIGINS` | public | `<VERCEL_FRONTEND_ORIGIN>` |
| `METRICS_ENABLED` | public | `false` |
| `OPS_TOKEN` | generated | at least 24 characters; gates `/ready` (and `/metrics` if enabled) |
| `ASYNC_GENERATION_ENABLED` | public | `true` (generation as a polled background job) |
| `DB_POOL_SIZE` | public | `3` |
| `DB_MAX_OVERFLOW` | public | `2` |
| `DB_CONNECT_TIMEOUT_SECONDS` | public | `15` |
| `GROQ_API_KEY` | provider | preferred LLM provider for the AI stages |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | provider / public | secondary LLM provider; both are needed for failover (dashboard only, not in `render.yaml`) |
| `LLM_FAILOVER_ENABLED`, `LLM_PRIMARY_PROVIDER`, `LLM_SECONDARY_PROVIDER` | public | defaults `true` / `groq` / `gemini` |
| `GEOCODING_PROVIDER` | public | `geoapify` |
| `GEOAPIFY_API_KEY` | secret | `<GEOAPIFY_API_KEY>` (free plan); required when `GEOCODING_PROVIDER=geoapify` |

**Geocoder (Section 203C.1, historical context).** The first production generation was blocked by HTTP 429
from the public Nominatim search endpoint: destination geocoding and every named-place lookup went to it from a
shared hosting address. Geocoding is now a separate, configurable provider. Production uses Geoapify for
destination geocoding and named-place search. (At 203C.1 Overpass still did POI discovery; 203C.2B below
replaced it in production with Geoapify Places.) With `APP_ENV=production` the
backend refuses to start when `GEOCODING_PROVIDER=geoapify` has no `GEOAPIFY_API_KEY`, and when the geocoder is
the public Nominatim endpoint (unless `ALLOW_PUBLIC_NOMINATIM_IN_PRODUCTION=true` is set deliberately). There is
no automatic fallback from Geoapify to Nominatim. Geocode results are cached in Redis under their own
`geoapify_geocode` namespace for `OSM_GEOCODE_CACHE_TTL_SECONDS` (30 days); failures and "no match" are never
cached. A place found by Geoapify keeps a `geoapify/<id>` identity and carries no OpenStreetMap tags, so it
reaches candidate scoring with less structured evidence than a Nominatim/Overpass result. A geocoder outage is
reported as "Place geocoding provider (Geoapify) was unavailable.", distinct from an Overpass failure.

**Final V1 itinerary engine (Section 203C.2B).** Overpass is no longer release-critical: it stays selectable for
development (`PLACES_PROVIDER=openstreetmap`) and production refuses to start with it. Production variables
(all public, set in `render.yaml`):

| Variable | Value | Meaning |
|---|---|---|
| `PLACES_PROVIDER` | `geoapify` | broad factual pool (attractions, food, stay-area POIs) from Geoapify Places, inside the destination's boundary |
| `ROUTING_PROVIDER` | `geoapify` | real walking distance/time, one multi-waypoint request per itinerary day |
| `INVENTORY_SUFFICIENCY_GATE_ENABLED` | `true` | required in production |
| `GEOAPIFY_MAX_CREDITS_PER_GENERATION` | `100` | hard per-generation cap |
| `AI_CANDIDATE_PROPOSAL_PROVIDER` | `groq` | semantic anchor proposals (hypotheses only) |
| `AI_CANDIDATE_DISCOVERY_ENABLED` | `true` | every proposal is grounded through Geoapify before it is eligible |
| `AI_DIRECTED_PROVIDER_DISCOVERY_MAX_SEARCHES` / `..._MAX_EXTRA_SEARCHES` | `16` / `4` | bounded named lookups |

Itinerary reasoning, repair and the narrator (`AI_ITINERARY_REASONING_ENABLED`, `AI_ITINERARY_REPAIR_ENABLED`,
`ITINERARY_NARRATOR_ENABLED` and their `*_PROVIDER=groq` selectors) are part of the same engine and are set in the
Render dashboard alongside `GROQ_API_KEY`. With the Gemini variables also set, each of these stages fails over
from Groq to Gemini and then to its deterministic fallback.

*Verified provider facts (checked 2026-10-01 against Geoapify's documentation).* Category identifiers used are
listed in `backend/app/providers/places/geoapify_categories.py` and checked by a contract test against a snapshot
of the official taxonomy. Pricing: geocoding 1 credit per request; Places 1 credit up to 20 places, plus 1 per
further 20 returned; Place Details 1 credit; Routing 1 credit per waypoint pair. Free plan: 3,000 credits per day.

*Inventory and usefulness.* With `T = trip days × pace target` (relaxed 2, balanced 3, packed 4),
`R = ceil(0.8 × T)` and `H = ceil(2.25 × T)`, the verified, non-trivial candidate count `viable` is `healthy`
(≥ H), `sufficient` (≥ T), `thin_but_usable` (≥ R) or `insufficient` (< R). Whenever `viable ≥ R` a plan must
schedule at least R meaningful stops with no empty day; otherwise it gets one AI repair, then one deterministic
top-up from unused verified places, and finishes `needs_review` with `UNDERFILLED_PLAN` if it still falls short.
Only `viable < R` ends as `readiness=blocked` with `INSUFFICIENT_VERIFIED_INVENTORY` -- and that is a normally
completed generation (job `succeeded`, state persisted), not a failed one. Nothing is ever padded.

*Worst-case Geoapify credits per generation (cold cache).*

| Item | 1-day relaxed | 3-day balanced | 5-day packed |
|---|---|---|---|
| Destination geocode | 1 | 1 | 1 |
| Places (6 requests; 1 credit per 20 places reserved, settled to what is returned) | 9 | 9 | 9 |
| Anchor + must-visit named lookups | 12 | 20 | 20 |
| Place Details for off-pool anchors | 12 | 12 | 12 |
| Routing (requests × legs) | 2 × 1 = 2 | 10 × 2 = 20 | 14 × 3 = 42 |
| Expansion round | 8 | 8 | 8 |
| **Worst case** | **44** | **70** | **92** |

Routing is bounded and never quadratic: one request for each day's order, at most one alternative per day (only
when a straight-line check says a different order is clearly shorter), and a fixed allowance of four further
requests. Usage is tracked per generation (`provider_usage_report`); each generation, regeneration and targeted
regeneration has its own isolated budget, a call beyond it is refused locally, and nothing falls through to
another provider or a paid tier. Cache hits cost nothing. A successful but empty Places answer is cached for
`GEOAPIFY_EMPTY_RESULT_CACHE_TTL_SECONDS` (6 h); failures are never cached.

*Pre-release benchmark: target and outcome.* The target set before release for the fixed 28-city benchmark
(18 tuning cities and 10 unseen holdout cities) plus 8 stress scenarios (`backend/scripts/benchmark_cities.py`)
was: destination
resolution 100%, zero fabricated identities, duplicate scheduled places and unsupported claims, no empty day
and at least R meaningful stops whenever `viable ≥ R`, routing coverage ≥ 90%, persistence/reload 100%, and the
provider budget never exceeded.

The outcome did not meet every line of that target, and is recorded as it happened: tuning 15/18, unseen
holdout 9/10 (destination resolution 0.9), stress 8/8. Fabricated identities, duplicate scheduled places and
unsupported claims were zero in every set, and the credit cap was never exceeded. The holdout miss was a
generic destination-resolution defect, fixed and verified by a separate canary without rerunning the holdout.
V1 was released on that basis. Full figures: [`23_v1_release_reference.md`](23_v1_release_reference.md).

The AI feature switches (`AI_ITINERARY_REASONING_ENABLED`, `AI_FEEDBACK_INTERPRETER_ENABLED`,
`TARGETED_REGENERATION_ENABLED`, `ITINERARY_NARRATOR_ENABLED` and their `*_PROVIDER` selectors) are public
configuration and default to off / `not_connected`. The ones the release uses are set in the Render
dashboard, not in `render.yaml`. `PORT` is injected by Render and must not be set.

Frontend (Vercel):

| Variable | Class | Production value / note |
| --- | --- | --- |
| `BACKEND_ORIGIN` | public | `<RENDER_BACKEND_ORIGIN>` (build time, server side only) |
| `API_BASE_URL` | public | `/api` |

Local, migration step only: `DATABASE_URL` (secret) in the shell that runs the migration tool.

No value for any of these is committed to the repository.

## 10. Free-tier constraints and honest behaviour at a limit

Limits change; check each provider's current published free-tier terms rather than relying on numbers
written here.

| Provider | Constraint | What the application does when it bites |
| --- | --- | --- |
| Vercel Hobby | Personal, non-commercial use; usage quotas | Requests are refused by Vercel. Nothing is upgraded automatically. |
| Render Free web service | Sleeps after inactivity; ephemeral filesystem; single instance; monthly instance-hour allowance; no durable worker | Cold start on the next visit; an in-flight generation is interrupted and can be retried; if the allowance is exhausted the backend is unavailable until it resets. |
| Neon Free | Storage, compute-hour and connection limits; idle compute suspends | If the database is unavailable or out of quota: `/ready` is `not_ready`, requests get a sanitized 503, startup refuses to proceed. No fallback store. |
| Upstash Free | Command, storage and bandwidth limits | Cache commands fail; the backend serves uncached and reports the cache as degraded or erroring. Provider calls still run. |

No provider is upgraded automatically, there is no paid fallback, and none of the steps below adds a payment
method.

## 11. Deployment checklist (executed in 203C)

This checklist was carried out for the V1 release and is kept as the procedure for a fresh deployment or a
redeploy. Step G.7 (cold-start recovery) is the one item still pending. Order matters: the backend needs the database migrated first, the frontend build needs the backend origin, and
the backend's CORS value needs the frontend origin.

**A. Neon**

1. Create the project and database on the free plan.
2. Copy the **direct** connection string into a password manager as `<NEON_DATABASE_URL>`. Do not paste it
   into the repository, a document or chat.

**B. Migrate (local, explicit)**

1. In a fresh shell, load the URL without writing it to shell history and without echoing it:

   ```bash
   read -rs DATABASE_URL && export DATABASE_URL      # paste <NEON_DATABASE_URL>, press Enter
   ```

2. Run the existing one-shot tool from the repository root:

   ```bash
   python backend/scripts/run_migrations.py
   ```

   Expect `Migrations applied successfully (now at head).` The tool only runs `upgrade head` and never prints
   the URL.
3. Verify the head: `(cd backend && alembic current)` must print the single head revision marked `(head)`.
4. `unset DATABASE_URL` and close the shell.

For every later release that adds a migration: repeat B for the new commit first, then deploy the backend.

**C. Upstash**

1. Create one free Redis database, in a region close to the Render region. Enable eviction if offered.
2. Store its TLS (`rediss://`) URL as `<UPSTASH_REDIS_URL>`.

**D. Render backend**

1. Create the service from `render.yaml` (Blueprint), or create one free Docker web service by hand with the
   same settings. Decline anything that asks for a paid plan or a payment method.
2. In the dashboard set `DATABASE_URL`, `REDIS_URL`, `GROQ_API_KEY`, `GEMINI_API_KEY` and `GEMINI_MODEL` (for
   the AI stages and their failover), `GEOAPIFY_API_KEY` (create a free Geoapify project and copy its API key) and
   `BACKEND_CORS_ORIGINS`. The Vercel hostname does not exist yet: enter the intended
   `https://<project>.vercel.app` origin now and correct it in step F if it differs. Browser traffic does not
   depend on this value.
3. Let Render generate `SESSION_SECRET_KEY` and `OPS_TOKEN`.
4. Start the deploy manually. Note the resulting `<RENDER_BACKEND_ORIGIN>`.

**E. Verify the backend**

1. `GET <RENDER_BACKEND_ORIGIN>/health` returns 200.
2. `GET <RENDER_BACKEND_ORIGIN>/ready` without a token returns 404; with the bearer token (read from the
   Render dashboard into a shell variable, not typed into a saved command) it returns `ready`.
3. `GET <RENDER_BACKEND_ORIGIN>/metrics` returns 404.
4. The deploy log shows one `Application started.` line with `persistence_backend=postgres`,
   `provider_cache_backend=redis` and the expected `migration_head`, and no URL, key or token.

**F. Vercel frontend**

1. Import the repository, Root Directory `frontend`, Hobby plan.
2. Set `BACKEND_ORIGIN=<RENDER_BACKEND_ORIGIN>` and `API_BASE_URL=/api`, then deploy. Note
   `<VERCEL_FRONTEND_ORIGIN>`.
3. If it differs from the value entered in D.2, update `BACKEND_CORS_ORIGINS` on Render and redeploy the
   backend.

**G. Production auth / cookie / API smoke** (in a browser on `<VERCEL_FRONTEND_ORIGIN>`)

1. Network tab: every API request goes to `<VERCEL_FRONTEND_ORIGIN>/api/...`; none goes to the Render host.
2. Sign up: the response sets the session cookie with `Secure; HttpOnly; SameSite=lax`, no `Domain`, scoped to
   the Vercel host.
3. Reload: `/api/auth/me` returns the user. Log out: the cookie is cleared and `/api/auth/me` returns 401.
4. API responses carry `Cache-Control: no-store` and are not served from the CDN cache.
5. `<VERCEL_FRONTEND_ORIGIN>/api/ready` and `/api/metrics` return 404.
6. Create a trip and generate a plan; confirm the job is polled to completion.
7. Leave the site idle until the backend sleeps, revisit, and confirm the cold start is slow but recovers.
   **Pending for V1:** not yet verified after a genuine idle sleep.

**H. Section 203D acceptance** — the full acceptance run against the deployed system. Completed, apart from
G.7; the record is in [section 14](#14-production-verification-record).

## 12. Production configuration validation

With `APP_ENV=production`, `validate_runtime_configuration` (`app/core/operational_config.py`) stops startup,
with fixed messages that never echo a value, for: Local JSON persistence; a missing or non-PostgreSQL
`DATABASE_URL`; the SQLite provider cache; a malformed `REDIS_URL`; `APP_DEBUG=true`; a missing or short
`SESSION_SECRET_KEY`; a non-`Secure`, non-`HttpOnly` or `SameSite=none` session cookie; a non-`https` CORS
origin; a short `OPS_TOKEN`. A wildcard or credentialed CORS origin is rejected in every environment, and an
unknown `PROVIDER_CACHE_BACKEND` / `PERSISTENCE_BACKEND` is rejected when settings load.

Tests (no remote service needed): `backend/app/tests/core/test_production_config_203b.py`,
`backend/app/tests/api/test_public_exposure_203b.py`,
`backend/app/tests/core/test_deployment_artifacts_203b.py`.

## 13. Generation latency: concurrency limits and model-stage budgets (Sections 1A–1C)

Latency-only controls. None of them changes which plan is produced: concurrent batches apply their results
in the original order, and a model stage that runs out of time uses the same deterministic fallback as any
other model failure. All have safe defaults; none is a secret.

| Variable | Default | Meaning |
|---|---|---|
| `PROVIDER_IO_CONCURRENCY_ENABLED` | `true` | Independent provider requests of one generation run as bounded concurrent batches. `false` runs them serially, in the same order, with the same result. |
| `GEOAPIFY_MAX_CONCURRENT_REQUESTS` | `4` | Geoapify requests ONE generation may have in flight (geocoding, Places, Place Details, routing together). 1–4; the configuration rejects more than 4. |
| `GEOAPIFY_PROCESS_MAX_CONCURRENT_REQUESTS` | `6` | Geoapify requests the whole PROCESS may have in flight, across all generations in it. A request needs a slot of both limits (generation first, then process). |
| `GROQ_REQUEST_TIMEOUT_SECONDS` | `30` | Timeout of one request attempt of the anchor, reasoning and repair stages (narrator: `ITINERARY_NARRATOR_TIMEOUT_SECONDS`, 20). An attempt gets `min(this, stage budget left)`. |
| `GROQ_MAX_RETRIES` | `1` | Transport retries per stage (rate limit, timeout/network, 5xx), made by the application under the stage budget. The SDK's own retries are off. |
| `GROQ_ANCHOR_TOTAL_BUDGET_SECONDS` | `25` | Total wall-clock budget of the anchor-proposal stage. |
| `GROQ_REASONING_TOTAL_BUDGET_SECONDS` | `20` | Total wall-clock budget of the itinerary-reasoning stage. |
| `GROQ_REPAIR_TOTAL_BUDGET_SECONDS` | `20` | Total wall-clock budget of one itinerary-repair attempt. |
| `GROQ_NARRATOR_TOTAL_BUDGET_SECONDS` | `25` | Total wall-clock budget of the narrator stage. |
| `NAGER_DATE_NO_DATA_CACHE_TTL_SECONDS` | `21600` | How long a successful EMPTY Nager.Date answer (HTTP 204) is remembered. `0` disables it. |

**The process limit is per process, not per deployment.** The backend runs exactly one Uvicorn worker per
container, so `GEOAPIFY_PROCESS_MAX_CONCURRENT_REQUESTS` bounds one container. Two containers can together
have twice as many requests in flight. It is deliberately not a Redis or PostgreSQL lock: request
concurrency is not correctness state, and Redis must stay optional. On the single free Render instance the
process limit is the deployment limit.

**Model-stage request ceiling.** A stage's budget covers every attempt, the structural retry and the retry
backoff. A stage makes at most one recovery attempt, of one kind, so the most requests it can make is:

| Stage | Structural retry | Transport retry | Max requests | Budget | On `deadline_exceeded` |
|---|---|---|---|---|---|
| Anchor proposal | 1 (smaller batch) | 1 | 2 | 25 s | No anchors proposed; the broad provider pool is used alone. |
| Itinerary reasoning | 0 | 1 | 2 | 20 s | Deterministic planning. |
| Itinerary repair | 0 | 1 | 2 per repair attempt | 20 s | No repair; deterministic top-up / needs-review. |
| Narrator | 1 (format reminder) | 1 | 2 | 25 s | Deterministic narrative. |

A retry is not started with less than 3 s of budget left (after a 1 s pause for a transport retry). A
request already in flight is bounded by its own timeout, which is never longer than the budget that was
left when it started. That timeout is the HTTP client's per-operation (connect / read / write) timeout,
not a hard kill: for these non-streaming requests it bounds the wait for the answer, but a response that
trickles in slowly could still run somewhat past the budget.

The canary (`backend/scripts/canary_city.py`) prints, per run, the stage and provider wall time, the
concurrent batches and both Geoapify concurrency peaks, and for each model stage its attempts, retries,
whether its deadline was exceeded and how it ended. These are reported, never acceptance checks.

## 14. Production verification record

Checks performed against the deployed system for Sections 203C and 203D. No credential, connection string,
session value or token is recorded here.

| Area | Check | Result |
| --- | --- | --- |
| Backend | `/health` (public) | 200 |
| Backend | `/ready` and `/metrics` without the ops token | 404 |
| Backend | `/ready` with the ops token | 200, status `ready` |
| Backend | PostgreSQL backend | ready |
| Backend | Schema head, expected and current | `d41a7e2c9b53` for both |
| Backend | Redis provider cache | healthy |
| Proxy | `/api/health` on the Vercel origin | 200, `Cache-Control: no-store` |
| Proxy | `/api/ready` and `/api/metrics` | 404 |
| Proxy | Browser API traffic | goes to `<VERCEL_FRONTEND_ORIGIN>/api/*`, not to the Render host |
| Auth | Signup, login, logout | work; after logout `/api/auth/me` returns 401 |
| Auth | Session cookie | `Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/`, host-scoped to `<VERCEL_FRONTEND_ORIGIN>` |
| Auth | Reload with a valid session | session kept |
| Auth | `SESSION_SECRET_KEY` rotation | done after a session-cookie value was exposed during testing; old sessions invalidated, confirmed by a fresh authentication |
| Persistence | Trip creation | works |
| Persistence | Async generation | returns 202 and is polled to completion |
| Persistence | Reload of a completed itinerary | no regeneration |
| Persistence | Existing Lisbon trip after the secret rotation and a fresh login | still present |
| UI | Lisbon 3-day balanced itinerary | rendered: 9 scheduled places, maps, routes, movement rows |
| UI | Limitations and unavailable data | labelled as such |
| UI | Final production smoke | no uncaught application error observed |
| Render | Deployed commit | `f55401c` |
| Render | Cold-start recovery after a genuine idle sleep | **Pending — not yet verified** |
