# 22. Free-Tier Deployment Contract (Section 203B)

This document is the deployment **contract** for the portfolio release. Section 203B prepared and validated
it; nothing here has been provisioned or deployed. Section 203C creates the real services by following
[section 11](#11-203c-deployment-checklist-not-yet-executed).

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

The browser never calls the Render backend directly. Because every API call is same-origin with the page, the
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

**To confirm in 203C** (cannot be verified without a real deployment): that the hosted rewrite forwards
`Set-Cookie` and `Cache-Control` unchanged, and what the platform's time limit for a proxied request is. The
same rewrite was exercised locally against a self-hosted Next.js server in 203B (signup, `/auth/me`, trip
list, logout: cookie and `no-store` preserved).

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
| `GROQ_API_KEY` | provider | only needed for the AI features you enable |

The AI feature switches (`AI_ITINERARY_REASONING_ENABLED`, `AI_FEEDBACK_INTERPRETER_ENABLED`,
`TARGETED_REGENERATION_ENABLED`, `ITINERARY_NARRATOR_ENABLED` and their `*_PROVIDER` selectors) are public
configuration and default to off / `not_connected`. Which of them the portfolio release turns on is a product
decision for 203C; they are not set in `render.yaml`. `PORT` is injected by Render and must not be set.

Frontend (Vercel):

| Variable | Class | Production value / note |
| --- | --- | --- |
| `BACKEND_ORIGIN` | public | `<RENDER_BACKEND_ORIGIN>` (build time, server side only) |
| `API_BASE_URL` | public | `/api` |

Local, migration step only: `DATABASE_URL` (secret) in the shell that runs the migration tool.

No value for any of these is committed to the repository.

## 10. Free-tier constraints and honest behaviour at a limit

Limits change; check each provider's current published free-tier terms in 203C rather than relying on numbers
written here.

| Provider | Constraint | What the application does when it bites |
| --- | --- | --- |
| Vercel Hobby | Personal, non-commercial use; usage quotas | Requests are refused by Vercel. Nothing is upgraded automatically. |
| Render Free web service | Sleeps after inactivity; ephemeral filesystem; single instance; monthly instance-hour allowance; no durable worker | Cold start on the next visit; an in-flight generation is interrupted and can be retried; if the allowance is exhausted the backend is unavailable until it resets. |
| Neon Free | Storage, compute-hour and connection limits; idle compute suspends | If the database is unavailable or out of quota: `/ready` is `not_ready`, requests get a sanitized 503, startup refuses to proceed. No fallback store. |
| Upstash Free | Command, storage and bandwidth limits | Cache commands fail; the backend serves uncached and reports the cache as degraded or erroring. Provider calls still run. |

No provider is upgraded automatically, there is no paid fallback, and none of the steps below adds a payment
method.

## 11. 203C deployment checklist (not yet executed)

Order matters: the backend needs the database migrated first, the frontend build needs the backend origin, and
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
2. In the dashboard set `DATABASE_URL`, `REDIS_URL`, `GROQ_API_KEY` (if AI features are enabled) and
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

**H. Section 203D acceptance** — the full acceptance run against the deployed system.

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
