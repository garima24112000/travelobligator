import { defineConfig, devices } from "@playwright/test";

// M2 Phase 2: the OFFLINE browser replay of one saved trip (frontend/e2e-replay/README.md).
//
//   M2P2_UI_SNAPSHOT=/tmp/m2p2_ui.json npm run e2e:replay
//
// The REAL frontend production build is served on 127.0.0.1; every /api request the page makes is
// answered from the snapshot the bounded verification wrote (backend/scripts/verify_m2_phase2.py
// --ui-snapshot), so no backend, provider, key or database is involved. Map tiles are answered locally
// and any other outside request is aborted. Not part of CI (its own config and test directory).

const FRONTEND_PORT = Number(process.env.M2P2_REPLAY_PORT ?? 3200);
const FRONTEND_ORIGIN = `http://127.0.0.1:${FRONTEND_PORT}`;

export default defineConfig({
  testDir: "./e2e-replay",
  outputDir: "./e2e-results/replay-artifacts",
  retries: 0,
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [["list"]],
  use: { baseURL: FRONTEND_ORIGIN, trace: "retain-on-failure", screenshot: "only-on-failure", reducedMotion: "reduce" },
  projects: [{ name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } }],
  webServer: {
    command: `npm run build && npx next start -p ${FRONTEND_PORT} -H 127.0.0.1`,
    url: FRONTEND_ORIGIN,
    reuseExistingServer: false,
    timeout: 240_000,
    env: {
      // build time: the same-origin /api rewrite exists, to an address nothing listens on -- every /api
      // request is answered by the test before it could reach it
      BACKEND_ORIGIN: "http://127.0.0.1:9",
      API_BASE_URL: "/api",
      NEXT_TELEMETRY_DISABLED: "1",
      NODE_ENV: "production",
    },
    stdout: "ignore",
    stderr: "pipe",
  },
});
