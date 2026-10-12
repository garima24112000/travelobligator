// Shared fixtures and helpers for the browser end-to-end tests (P1-C).
//
// Isolation: every test gets a fresh browser context and signs up its own throwaway account against the
// offline backend (backend/scripts/e2e_backend.py), whose whole store is a temp directory deleted when it
// stops. Nothing is shared between tests except that process; nothing leaves 127.0.0.1.

import { expect, test as base, type APIResponse, type Page, type Route } from "@playwright/test";

/** What a test learns about the page while it runs. */
export type Watch = {
  /** `securitypolicyviolation` events: what the Report-Only policy WOULD have blocked. */
  cspViolations: { directive: string; blocked: string; disposition: string; source: string }[];
  /** Console messages that mention the Content Security Policy. */
  cspConsole: string[];
  /** Uncaught exceptions in the page. */
  pageErrors: string[];
  /** Requests that failed at the network level (not HTTP error statuses), excluding deliberate aborts. */
  failedRequests: string[];
  /** Map tile requests the page made, by host. */
  tileRequests: string[];
  /** Call in a test that provokes a violation on purpose. */
  allowCspViolations(): void;
  /** Call in a test that provokes an uncaught page error or a failed request on purpose. */
  allowPageErrors(): void;
};

// A 1x1 PNG. Map tiles are answered with it: the tests must not depend on (or load) OpenStreetMap's
// servers, while the page still makes the real request, to the real host, under the real policy.
const TILE_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==",
  "base64",
);
export const TILE_HOST = /^https:\/\/[abc]\.tile\.openstreetmap\.org\//;

export const PASSWORD = "e2e-password-123";

export const test = base.extend<{ watch: Watch }>({
  watch: [
    async ({ page }, use, testInfo) => {
      let cspAllowed = false;
      let errorsAllowed = false;
      const watch: Watch = {
        cspViolations: [],
        cspConsole: [],
        pageErrors: [],
        failedRequests: [],
        tileRequests: [],
        allowCspViolations: () => {
          cspAllowed = true;
        },
        allowPageErrors: () => {
          errorsAllowed = true;
        },
      };

      await page.exposeBinding("__e2eCspViolation", (_source, violation) => {
        watch.cspViolations.push(violation);
      });
      await page.addInitScript(() => {
        document.addEventListener("securitypolicyviolation", (event) => {
          (window as unknown as { __e2eCspViolation: (v: unknown) => void }).__e2eCspViolation({
            directive: event.effectiveDirective,
            blocked: event.blockedURI,
            disposition: event.disposition,
            source: `${event.sourceFile ?? ""}:${event.lineNumber ?? ""}`,
          });
        });
      });
      page.on("console", (message) => {
        if (/content[- ]security[- ]policy/i.test(message.text())) watch.cspConsole.push(message.text());
      });
      page.on("pageerror", (error) => watch.pageErrors.push(String(error)));
      page.on("requestfailed", (request) => {
        const reason = request.failure()?.errorText ?? "";
        // navigations and reloads cancel whatever was in flight; that is not a failure of the app
        if (/ERR_ABORTED|NS_BINDING_ABORTED|cancelled/i.test(reason)) return;
        watch.failedRequests.push(`${request.method()} ${request.url()} ${reason}`);
      });
      await page.route(TILE_HOST, async (route) => {
        watch.tileRequests.push(new URL(route.request().url()).host);
        await route.fulfill({ status: 200, contentType: "image/png", body: TILE_PNG });
      });

      await use(watch);

      await testInfo.attach("page-watch.json", {
        body: JSON.stringify(watch, null, 2),
        contentType: "application/json",
      });
      // Every test is also a CSP test: unless it says otherwise, nothing it did may have tripped the
      // (Report-Only) policy, thrown in the page, or failed at the network level.
      if (!cspAllowed) {
        expect(watch.cspViolations, "Content Security Policy violations reported by the browser").toEqual([]);
        expect(watch.cspConsole, "Content Security Policy messages in the console").toEqual([]);
      }
      if (!errorsAllowed) {
        expect(watch.pageErrors, "uncaught errors in the page").toEqual([]);
        expect(watch.failedRequests, "requests that failed at the network level").toEqual([]);
      }
    },
    { auto: true },
  ],
});

export { expect };

// -- accounts -----------------------------------------------------------------------------------------

let counter = 0;
export function uniqueEmail(prefix = "e2e"): string {
  counter += 1;
  return `${prefix}-${Date.now()}-${process.pid}-${counter}-${Math.random().toString(36).slice(2, 8)}@example.com`;
}

/** Sign up through the real form. Ends on the signed-in page. */
export async function signUp(page: Page, email = uniqueEmail()): Promise<string> {
  await page.goto("/");
  await page.getByRole("button", { name: "Sign up" }).click();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByLabel("Confirm password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("heading", { name: "My trips" })).toBeVisible();
  await expect(page.getByText(email)).toBeVisible();
  return email;
}

export async function logIn(page: Page, email: string, password = PASSWORD): Promise<void> {
  await page.goto("/");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.locator("form").getByRole("button", { name: "Log in" }).click();
}

/**
 * Create an account through the API instead of the form: the browser context then carries the session
 * cookie exactly as if the form had been used. For tests whose subject is not the sign-up form.
 */
export async function signUpViaApi(page: Page, email = uniqueEmail()): Promise<string> {
  const response = await page.request.post("/api/auth/signup", { data: { email, password: PASSWORD } });
  expect(response.status(), await response.text()).toBe(201);
  return email;
}

// -- trips --------------------------------------------------------------------------------------------

export type TripInput = {
  destination?: string;
  origin?: string;
  startDate?: string;
  endDate?: string;
  interests?: string[];
  mustVisit?: string[];
};

export const DEFAULT_TRIP: Required<TripInput> = {
  destination: "Fixtureville, Testland",
  origin: "Home City",
  startDate: "2027-03-01",
  endDate: "2027-03-03",
  interests: ["food", "art"],
  mustVisit: ["Fixture Cathedral"],
};

function tripPayload(input: TripInput) {
  const trip = { ...DEFAULT_TRIP, ...input };
  return {
    primary_destination: trip.destination,
    origin_city: trip.origin,
    start_date: trip.startDate,
    end_date: trip.endDate,
    travelers_count: 2,
    travel_group_type: "couple",
    interests: trip.interests,
    must_visit: trip.mustVisit,
  };
}

async function json(response: APIResponse) {
  return (await response.json()) as { data: Record<string, unknown>; errors?: { code: string }[] };
}

/** Create a trip through the API (no itinerary yet). Returns its id. */
export async function createTripViaApi(page: Page, input: TripInput = {}): Promise<string> {
  const response = await page.request.post("/api/trips", { data: tripPayload(input) });
  expect(response.status(), await response.text()).toBe(201);
  return (await json(response)).data.trip_id as string;
}

/** Start generation through the API and, by default, wait for the job to finish. */
export async function generateViaApi(page: Page, tripId: string, options: { wait?: boolean } = {}): Promise<string> {
  const started = await page.request.post(`/api/trips/${tripId}/generate`);
  expect(started.status(), await started.text()).toBe(202);
  const jobId = (await json(started)).data.job_id as string;
  if (options.wait === false) return jobId;
  await expect
    .poll(
      async () => (await json(await page.request.get(`/api/trips/${tripId}/jobs/${jobId}`))).data.status,
      { timeout: 30_000, intervals: [250] },
    )
    .toBe("succeeded");
  return jobId;
}

/** A signed-in account with one finished itinerary, opened in the page. Returns the trip id. */
export async function openGeneratedTrip(page: Page, input: TripInput = {}): Promise<string> {
  await signUpViaApi(page);
  const tripId = await createTripViaApi(page, input);
  await generateViaApi(page, tripId);
  await page.goto(`/?trip=${tripId}`);
  await expect(page.getByRole("heading", { name: "Your itinerary, day by day" })).toBeVisible({ timeout: 20_000 });
  return tripId;
}

/** Fill the create-trip form. Only the fields given are changed. */
export async function fillTripForm(page: Page, input: TripInput): Promise<void> {
  const form = page.getByRole("form", { name: "Create a new trip" });
  if (input.destination !== undefined) await form.getByLabel("Destination").fill(input.destination);
  if (input.origin !== undefined) await form.getByLabel("Origin city").fill(input.origin);
  if (input.startDate !== undefined) await form.getByLabel("Start date").fill(input.startDate);
  if (input.endDate !== undefined) await form.getByLabel("End date").fill(input.endDate);
  if (input.interests !== undefined) await form.getByLabel(/^Interests/).fill(input.interests.join(", "));
  if (input.mustVisit !== undefined) await form.getByLabel(/^Must-visit places/).fill(input.mustVisit.join(", "));
}

export function submitTripButton(page: Page) {
  return page.getByRole("form", { name: "Create a new trip" }).locator('button[type="submit"]');
}

export async function myTripRows(page: Page): Promise<string[]> {
  const list = page.locator('ul:has(button[aria-label^="Delete trip:"])');
  if ((await list.count()) === 0) return [];
  return list.locator("li p.font-semibold").allInnerTexts();
}

// -- shaping what the page is shown ------------------------------------------------------------------
//
// The itinerary presentation states (a ferry leg, an unverified leg, a blocked plan, ...) cannot be
// produced by the offline backend's tiny fixed pool. They are made by taking the backend's REAL response
// for a real generated trip and changing only the fields a state is about, so every other field keeps the
// exact shape the backend sends. The original response is never invented wholesale.

type Json = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

/**
 * Rewrite the real responses of `GET /api/trips/{id}` (the planning state) and
 * `GET /api/trips/{id}/validation-report` before the page receives them.
 */
export async function reshapeTrip(
  page: Page,
  tripId: string,
  change: {
    planningState?: (state: Json) => void;
    validation?: (report: Json) => void;
    /** The readiness the trip summary reports; the backend derives it from the validation report. */
    readiness?: "ready" | "needs_review" | "blocked";
  },
): Promise<void> {
  const rewrite = async (route: Route, apply: (body: Json) => void) => {
    const response = await route.fetch();
    const body = (await response.json()) as Json;
    if (response.ok()) apply(body);
    await route.fulfill({ response, json: body });
  };
  await page.route(`**/api/trips/${tripId}`, (route) =>
    route.request().method() === "GET"
      ? rewrite(route, (body) => {
          change.planningState?.(body.data.planning_state);
          // the validation report is also embedded in the planning state
          if (body.data.planning_state.validation_report) change.validation?.(body.data.planning_state.validation_report);
        })
      : route.fallback(),
  );
  await page.route(`**/api/trips/${tripId}/validation-report`, (route) =>
    rewrite(route, (body) => change.validation?.(body.data.validation_report)),
  );
  if (change.readiness) {
    // kept consistent with the report above, as the backend would
    await page.route(`**/api/trips/${tripId}/summary`, (route) =>
      rewrite(route, (body) => {
        body.data.validation_status = change.readiness;
        if (change.readiness === "blocked") body.data.pipeline_status = "blocked";
      }),
    );
  }
}

/** Mark legs of the route report (and their travel-time buffers) as successfully routed. */
export function routeLegs(
  state: Json,
  describe: (leg: Json, index: number) => { routed: boolean; mode?: string; seconds?: number; meters?: number; ferry?: boolean | null },
): void {
  const legs: Json[] = state.route_feasibility_report.legs;
  const buffers: Json[] = state.travel_time_buffer_report.buffers;
  legs.forEach((leg, index) => {
    const spec = describe(leg, index);
    const buffer = buffers.find(
      (candidate) =>
        candidate.from_experience_id === leg.from_experience_id && candidate.to_experience_id === leg.to_experience_id,
    );
    if (!spec.routed) return; // left exactly as the backend reported it: no verified route
    Object.assign(leg, {
      status: "success",
      feasibility_status: "feasible",
      mode: spec.mode ?? "walk",
      duration_seconds: spec.seconds ?? 720,
      distance_meters: spec.meters ?? 900,
      includes_ferry: spec.ferry ?? false,
      message: null,
    });
    if (buffer) {
      Object.assign(buffer, {
        status: "success",
        route_duration_seconds: spec.seconds ?? 720,
        route_distance_meters: spec.meters ?? 900,
        buffer_status: "comfortable",
        message: null,
      });
    }
  });
  state.route_feasibility_report.status = "success";
  state.travel_time_buffer_report.status = "success";
}

/** Replace the review outcome of a validation report, keeping every other field as the backend sent it. */
export function setReview(
  report: Json,
  review: {
    readiness: "ready" | "needs_review" | "blocked";
    reviewCodes?: string[];
    blockingCodes?: string[];
    classification?: Record<string, "material" | "informational">;
    warnings?: { category: string; message: string; day?: number; severity?: string }[];
  },
): void {
  report.readiness_status = review.readiness;
  report.review_codes = review.reviewCodes ?? [];
  report.blocking_codes = review.blockingCodes ?? [];
  report.review_code_classification = review.classification ?? {};
  report.critical_issues = [];
  report.warnings = (review.warnings ?? []).map((warning, index) => ({
    issue_id: `validation_issue_e2e_${index}`,
    severity: warning.severity ?? "warning",
    category: warning.category,
    message: warning.message,
    affected_section: warning.day ? `experience_plan.daily_plans[${warning.day}]` : "experience_plan",
    suggested_fix: null,
    claim_sources: [],
  }));
}

/** The whole document fits its window: nothing scrolls sideways. */
export async function expectNoHorizontalScroll(page: Page): Promise<void> {
  const overflow = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  expect(overflow.scrollWidth, "the page scrolls horizontally").toBeLessThanOrEqual(overflow.clientWidth + 1);
}
