# Step 188E (docs/16_frontend_architecture.md section 57): multi-stage
# build with two independent, explicitly-named targets:
#
#   `dev`        -- byte-for-byte the same behavior this file had before
#                   this step (`npm install` + `npm run dev`, i.e. real
#                   `next dev`). This is still exactly what
#                   `docker compose up frontend` builds/runs locally --
#                   docker-compose.yml now targets this stage
#                   explicitly (`build.target: dev`) specifically so
#                   local dev behavior does not silently change just
#                   because this Dockerfile gained a production path.
#   `production` -- new in this step: a real `next build` + `next
#                   start` path, never `next dev`. It is the LAST
#                   stage in this file, so a bare `docker build
#                   ./frontend` with no `--target` builds this one by
#                   default -- the opposite of `dev`, which must be
#                   targeted explicitly (see docker-compose.yml).
#
# Neither target is a claim that a full production deployment (reverse
# proxy, TLS termination, process supervision, log shipping, autoscaling,
# etc.) exists -- this step only verifies that a real, compiled Next.js
# production server builds and serves traffic. See
# docs/16_frontend_architecture.md section 57 and README's own note for
# the full, honest scope.

FROM node:20-alpine AS base
WORKDIR /app
COPY package*.json ./

# -----------------------------------------------------------------------
# `dev` -- unchanged from before this step. `npm install` (not `npm ci`)
# is kept deliberately here, exactly as it was, so this already-working
# path is not altered in any way by adding the new stages below.
# -----------------------------------------------------------------------
FROM base AS dev
RUN npm install
COPY . .
EXPOSE 3000
CMD ["npm", "run", "dev"]

# -----------------------------------------------------------------------
# `builder` -- compiles the real production build. Never run directly
# (no EXPOSE/CMD) -- `production` below only copies its output.
# -----------------------------------------------------------------------
FROM base AS builder
# `npm ci` (not `npm install`) here: package-lock.json exists and is
# committed, and a build stage is exactly where `ci`'s stricter,
# lockfile-exact, reproducible install is worth the (small) extra
# strictness -- unlike `dev` above, this is a new path with no existing
# behavior to preserve. Installs devDependencies too (the default for
# `npm ci` with no `--omit`), since `next build` needs `typescript`/
# `tailwindcss`/`@tailwindcss/postcss` from this project's own
# devDependencies to compile.
RUN npm ci
COPY . .

# Step 188E: NEXT_PUBLIC_API_BASE_URL is compiled directly into the
# client-side JavaScript bundle by `next build` (webpack/Turbopack
# statically replaces every `process.env.NEXT_PUBLIC_*` reference at
# build time) -- for this Docker path it is a BUILD-TIME value only,
# never a runtime one. Setting it as a container environment variable
# at `docker run`/`docker compose up` time (e.g. via `env_file`) has NO
# EFFECT on an already-built `production` image; only rebuilding with a
# different `--build-arg NEXT_PUBLIC_API_BASE_URL=...` changes what
# ships in the bundle. Defaults to the exact same safe local value
# `frontend/lib/api.ts`'s own fallback already uses (`http://
# localhost:8000`), so a plain `docker build ./frontend` with no
# build-arg behaves identically to today's default -- never a real
# deployed URL. This is public, browser-visible configuration either
# way, the same as every other `NEXT_PUBLIC_*` value (see
# `.env.example`'s own note) -- never a secret, and none is baked in
# here.
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}
RUN npm run build

# -----------------------------------------------------------------------
# `production` -- new in this step. Runs the real, compiled Next.js
# production server (`next start`), never `next dev`. A verified
# build/run path, not a "production-ready deployment" claim -- see this
# file's own top-of-file note.
# -----------------------------------------------------------------------
FROM node:20-alpine AS production
WORKDIR /app
ENV NODE_ENV=production
COPY package*.json ./
# Production-only dependencies: `next start` needs `next`/`react`/
# `react-dom`/`leaflet` (all in "dependencies"), never the
# devDependencies (`typescript`/`eslint`/`tailwindcss`/etc.) the
# `builder` stage needed only to compile the app.
RUN npm ci --omit=dev
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public
COPY --from=builder /app/next.config.ts ./next.config.ts

EXPOSE 3000
CMD ["npm", "run", "start"]
