# Browser end-to-end tests

Playwright tests that drive the real application in a real browser (Chromium): the production frontend
build, talking through the same-origin `/api` rewrite to the real backend running offline.

```bash
cd frontend
npx playwright install chromium     # once
npm run e2e                         # build, start both servers, run every viewport
npm run e2e -- --project=desktop    # one viewport
npm run e2e -- e2e/auth.spec.ts     # one file
node e2e/support/report-counts.mjs  # passed / failed / expected failure / skipped, per viewport
```

Playwright starts and stops both servers itself. Locally it reuses servers already listening on
`127.0.0.1:8100` and `:3100`; in CI it always starts its own. The Python interpreter is `../.venv/bin/python`
unless `E2E_PYTHON` says otherwise.

## What runs, and what cannot be reached

| Part | What it is |
| --- | --- |
| Frontend | `next build` + `next start` on `127.0.0.1:3100`, built with `BACKEND_ORIGIN` so the `/api/*` rewrite exists, run with `API_BASE_URL=/api`. The proxy, the session cookie and the security headers are the ones a visitor gets. |
| Backend | `backend/scripts/e2e_backend.py` on `127.0.0.1:8100`: the real FastAPI application, real authentication, quotas, async jobs and validation. |
| Places, weather, holidays, currency | Fixed stand-ins installed by that script. The places are invented fixture places with invented names, never presented as real. |
| Model stages (anchors, reasoning, repair, narrator) | Off. The deterministic planner runs. |
| Storage | A Local JSON file in a new temporary directory, removed when the backend stops. |
| Map tiles | The page asks `tile.openstreetmap.org` as in production; the test answers with a one-pixel image, so no request leaves the machine. |

The script removes `DATABASE_URL`, `REDIS_URL` and every provider key from its own environment and refuses
to start as anything other than a development, Local JSON, keyless backend. It reads no `.env` file. There
is no way to point these tests at PostgreSQL, a deployed service, Geoapify, Groq or Gemini.

**Isolation.** Every test signs up its own account with a random address and creates its own trips. Tests
share nothing, so they run in parallel and in any order. Nothing needs cleaning up: the store is deleted
with the backend.

**Special destinations of the offline backend.** `Slowville` makes generation take `E2E_SLOW_SECONDS`
(default 6) so a running job can be observed; `Failville` makes the places lookup raise.

**Itinerary states the small fixed pool cannot produce** (a ferry leg, an unverified leg, a blocked plan,
a long travel day, …) are made by fetching the backend's REAL response for a real generated trip and
changing only the fields that state is about (`support/app.ts`, `reshapeTrip`). The shape always comes
from the backend. No stored benchmark file is read or written.

## Files

| File | Covers | Viewports |
| --- | --- | --- |
| `auth.spec.ts` | sign up, log in, log out, wrong credentials, session restore, ended session | desktop |
| `trip-creation.spec.ts` | fields, 7-day limit and list bounds, progress, success, 422 / 429 / 503 / failed job, duplicate submit | desktop |
| `saved-trips.spec.ts` | list, reopen, refresh, delete with confirmation, cancel, refusal while a job runs | desktop |
| `account.spec.ts` | privacy notice, account deletion: confirmation, wrong password, success, session cleared | desktop |
| `itinerary.spec.ts` | day cards, maps and markers, legs, must-visits, interest coverage, review banners, ferry / unverified legs, long travel day, dispersion | desktop |
| `security.spec.ts` | response headers, Report-Only policy, nonces, proxy, session cookie, framing | desktop |
| `accessibility.spec.ts` | axe-core scans, headings and landmarks, labels, keyboard, focus, announcements | desktop, 375 px |
| `layout.spec.ts` | no sideways scroll, nothing cut off or overlapping, wrapping, maps, banners, confirmations; saves screenshots | desktop, 375 px, 320 px, 200 % zoom |
| `journey-smoke.spec.ts` | one whole visit through the interface | desktop, 375 px |

"200 % zoom" is a 720 × 450 CSS-pixel window at device scale 2, which is what a 1440 × 900 screen gives
the page at 200 % browser zoom.

## Output (`frontend/e2e-results/`, not committed)

- `screenshots/<viewport>/NN-state.png`: the important states at each viewport, written by
  `layout.spec.ts` on every run, **for a person to look at**. The tests measure geometry; they do not
  judge appearance.
- `artifacts/`: for a failed test only, a trace (`npx playwright show-trace <trace.zip>`), a screenshot
  and `page-watch.json`.
- `results.json`: the machine-readable result; `report/` is the HTML report (CI only).
- `backend.log`: the offline backend's own structured log for the run (fixture data only), including
  the warning and traceback of any generation job that failed unexpectedly. A test that waited for a job
  also prints the job's last status and error code when it fails. It passes through the application's
  own redaction (no password, cookie, token or email is written; ids of the throwaway test accounts are),
  is created with mode 600, and if it cannot be written the backend runs without it. In CI it is part of
  the `browser-e2e-results` artifact: kept 14 days and, because this repository is public, downloadable by
  any signed-in GitHub user -- so never point the e2e backend at real data.

## Content Security Policy

Every test in every file fails if the browser raises a `securitypolicyviolation` event, logs a policy
message, throws an uncaught error or has a failed request (`support/app.ts`, the `watch` fixture), unless
the test explicitly allows it. `security.spec.ts` also proves the check works: a deliberately disallowed
image is reported, and still loads, because the policy is **Report-Only**.

These tests show that ordinary use produces no report. They do not show that an enforcing policy is safe
to switch on; that stays a human decision.

## Expected failures are reported defects

There are none at present: the five defects this suite first found (P1C-1 to P1C-5) are fixed, and their
tests, named `P1C-n`, are ordinary passing tests.

The convention for a future one: a test named `DEFECT <id>` and marked `test.fail()` reproduces an
application defect that has been reported and not yet fixed. It runs every time and is counted separately
as an *expected failure*. When
the defect is fixed the test starts passing, Playwright reports that as a failure, and the `test.fail()`
line is removed, after which the test guards the fix. `KNOWN_ISSUES` in `accessibility.spec.ts` works the
same way for axe rules: a listed rule that stops occurring fails the scan until it is taken off the list.

Retries are off. A test that passes only on a second attempt is a finding, not a pass.

## What an automated run does not establish

Passing these tests is **not** a statement of WCAG conformance. axe-core finds only the kinds of problem
a program can detect. Still to be done by a person:

- Read each screen with a screen reader (VoiceOver with Safari, NVDA with Firefox or Chrome): the sign-in
  form and its errors, the create-trip form, generation progress and completion, the review banner, a day
  card with its travel rows, the delete confirmations.
- Check that the announcements are heard at the right moment and are not repeated or cut off.
- Judge whether the place links beside each map are a sufficient alternative to the map itself.
- Use the application on a real phone (touch targets, the on-screen keyboard over the form, the map's
  touch gestures against page scrolling) and in Safari and Firefox; only Chromium is automated.
- Look at the screenshots for anything that measures correctly and still looks wrong.
- Check contrast and readability in forced-colours / high-contrast mode, and at 400 % zoom.
