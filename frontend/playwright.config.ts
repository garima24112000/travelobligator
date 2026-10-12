import { defineConfig, devices } from "@playwright/test";

// P1-C: real-browser end-to-end tests.
//
//   npm run e2e            build the app, start both servers, run every project
//   npm run e2e -- --project=desktop
//
// What runs (see e2e/README.md):
//   * the REAL backend, offline: backend/scripts/e2e_backend.py -- deterministic stand-in providers, no
//     model stage, a throwaway Local JSON store in a new temp directory. Never PostgreSQL, never a deployed
//     service, never a provider key.
//   * the REAL frontend as a PRODUCTION build (`next build` + `next start`), with the same same-origin
//     `/api/*` rewrite to the backend that production uses -- so the session cookie, the proxy and the
//     security headers are the ones a visitor gets.
//
// Nothing here can reach production: both servers are bound to 127.0.0.1 and `baseURL` is fixed below.

const BACKEND_PORT = Number(process.env.E2E_BACKEND_PORT ?? 8100);
const FRONTEND_PORT = Number(process.env.E2E_FRONTEND_PORT ?? 3100);
const BACKEND_ORIGIN = `http://127.0.0.1:${BACKEND_PORT}`;
const FRONTEND_ORIGIN = `http://127.0.0.1:${FRONTEND_PORT}`;
const PYTHON = process.env.E2E_PYTHON ?? "../.venv/bin/python";
const CI = Boolean(process.env.CI);

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./e2e-results/artifacts",
  // One account per test (a fresh signup), so tests are independent and can run in parallel.
  fullyParallel: true,
  forbidOnly: CI,
  // No retries: a test that passes only on a second try is a finding, not a pass.
  retries: 0,
  workers: CI ? 2 : 4,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: CI
    ? [["list"], ["html", { outputFolder: "e2e-results/report", open: "never" }], ["json", { outputFile: "e2e-results/results.json" }]]
    : [["list"], ["json", { outputFile: "e2e-results/results.json" }]],
  use: {
    baseURL: FRONTEND_ORIGIN,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
    // The app must work for someone who asked for less motion; screenshots are also stable this way.
    reducedMotion: "reduce",
  },
  projects: [
    // Every journey, at a laptop-sized window.
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    // The layout, accessibility and screenshot specs again at phone sizes and at 200% zoom.
    {
      name: "mobile-375",
      testMatch: /(layout|accessibility|journey-smoke)\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true },
    },
    {
      name: "mobile-320",
      testMatch: /layout\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], viewport: { width: 320, height: 640 }, isMobile: true, hasTouch: true },
    },
    {
      // 1440x900 at 200% browser zoom is a 720x450 CSS-pixel viewport at device scale 2: the same
      // reflow a visitor gets when they zoom the page to 200%.
      name: "zoom-200",
      testMatch: /layout\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], viewport: { width: 720, height: 450 }, deviceScaleFactor: 2 },
    },
  ],
  webServer: [
    {
      command: `${PYTHON} ../backend/scripts/e2e_backend.py`,
      url: `${BACKEND_ORIGIN}/health`,
      reuseExistingServer: !CI,
      timeout: 120_000,
      // SIGTERM (not the default SIGKILL), so the backend removes its temporary store on the way out
      gracefulShutdown: { signal: "SIGTERM", timeout: 5_000 },
      env: {
        E2E_BACKEND_PORT: String(BACKEND_PORT),
        E2E_FRONTEND_ORIGIN: FRONTEND_ORIGIN,
        E2E_SLOW_SECONDS: process.env.E2E_SLOW_SECONDS ?? "6",
      },
      stdout: "ignore",
      stderr: "pipe",
    },
    {
      // Production build with the same-origin API rewrite, then the production server.
      command: `npm run build && npx next start -p ${FRONTEND_PORT} -H 127.0.0.1`,
      url: FRONTEND_ORIGIN,
      reuseExistingServer: !CI,
      timeout: 240_000,
      env: {
        BACKEND_ORIGIN, // build time: creates the /api/* rewrite
        API_BASE_URL: "/api", // runtime: what the browser is told to call
        NEXT_TELEMETRY_DISABLED: "1",
        NODE_ENV: "production",
      },
      stdout: "ignore",
      stderr: "pipe",
    },
  ],
});
