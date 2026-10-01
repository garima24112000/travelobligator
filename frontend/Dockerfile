# Frontend image (Step 188E, hardened in Section 200E).
#
# Two independent targets:
#   `dev`        -- `next dev` with dependencies installed for local hot-reload (used by docker-compose.dev.yml).
#   `production` -- the LAST stage, so a bare `docker build ./frontend` builds it: a multi-stage build that
#                   compiles with `output: "standalone"` and runs the traced server with `node server.js` as an
#                   unprivileged user. No dev dependencies, no source tree, no `.env`, no `next dev`.
#
# Node 22 (LTS) is pinned by exact version; Next.js 16.2.4 supports Node >= 20.9 (Node 20 reached end of life in
# April 2026). CI uses Node 22 as well (Section 203A).
#
# API URL: the browser is told which backend to call at RUNTIME via the container's `API_BASE_URL` environment
# variable (injected by the root layout, see frontend/lib/runtime-config.ts) -- one image works in any
# environment. `NEXT_PUBLIC_API_BASE_URL` (a build-time ARG) is only the fallback when `API_BASE_URL` is unset.
# Both are public, browser-visible values -- never secrets, never internal service names.

ARG NODE_IMAGE=node:22.23.3-alpine3.24

FROM ${NODE_IMAGE} AS base
WORKDIR /app
COPY package*.json ./

# ---------------------------------------------------------------- dev
FROM base AS dev
RUN npm install
COPY . .
EXPOSE 3000
CMD ["npm", "run", "dev"]

# ---------------------------------------------------------------- builder
FROM base AS builder
ENV NEXT_TELEMETRY_DISABLED=1
# `npm ci`: lockfile-exact, reproducible. devDependencies are needed only here, to compile.
RUN npm ci
COPY . .
# Build-time FALLBACK only (see header). Defaults to the local-development address.
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}
RUN npm run build

# ---------------------------------------------------------------- production
FROM ${NODE_IMAGE} AS production
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
# The runtime only ever executes `node server.js`: the package managers shipped in the base image (npm/npx,
# corepack, yarn) are removed, so their bundled dependencies (with known CVEs) are not part of the shipped image.
RUN rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack /opt/yarn-v* \
        /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack /usr/local/bin/yarn /usr/local/bin/yarnpkg
WORKDIR /app
# The standalone output is a self-contained server (traced node_modules subset + server.js). Files are root-owned
# and read-only to the runtime user.
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public
USER node
EXPOSE 3000
# Local liveness of the Next.js server only (no backend, database or provider dependency). Node 22 has fetch.
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:'+(process.env.PORT||3000)+'/').then(r=>process.exit(r.status<500?0:1)).catch(()=>process.exit(1))"]
CMD ["node", "server.js"]
