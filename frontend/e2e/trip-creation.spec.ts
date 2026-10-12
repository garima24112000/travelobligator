import {
  DEFAULT_TRIP,
  createTripViaApi,
  expect,
  fillTripForm,
  generateViaApi,
  expectTripRows,
  signUp,
  signUpViaApi,
  submitTripButton,
  test,
} from "./support/app";

// B. Creating a trip and building its itinerary, through the real form, the real job and the real poll.

const ITINERARY = { name: "Your itinerary, day by day" };
const dayHeadings = (page: import("@playwright/test").Page) =>
  page.getByRole("heading", { level: 3, name: /^Day \d+ · / });

function countRequests(page: import("@playwright/test").Page) {
  const seen = { createTrip: 0, generate: 0 };
  page.on("request", (request) => {
    if (request.method() !== "POST") return;
    const path = new URL(request.url()).pathname;
    if (path === "/api/trips") seen.createTrip += 1;
    if (/^\/api\/trips\/[^/]+\/generate$/.test(path)) seen.generate += 1;
  });
  return seen;
}

test("a full trip request is submitted and its itinerary is shown", async ({ page }) => {
  await signUp(page);
  await fillTripForm(page, DEFAULT_TRIP);
  await page.getByLabel("Travelers").fill("3");
  await page.getByLabel("Pace").selectOption("relaxed");
  await page.getByLabel(/^Constraints/).fill("no early mornings");

  await submitTripButton(page).click();

  await expect(page.getByRole("heading", ITINERARY)).toBeVisible({ timeout: 30_000 });
  // what was typed is what was planned
  await expect(page.getByRole("heading", { level: 2, name: DEFAULT_TRIP.destination })).toBeVisible();
  await expect(page.getByText("2027-03-01 → 2027-03-03 · 3 travelers · relaxed pace · from Home City")).toBeVisible();
  await expect(page.getByText("Interests: food, art")).toBeVisible();
  await expect(dayHeadings(page)).toHaveText([/^Day 1 · 2027-03-01/, /^Day 2 · 2027-03-02/, /^Day 3 · 2027-03-03/]);
  // the trip is saved under My trips, once
  await expectTripRows(page, [DEFAULT_TRIP.destination]);
  // the form is usable again
  await expect(submitTripButton(page)).toBeEnabled();
  await expect(submitTripButton(page)).toHaveText("Create trip and generate plan");
});

test("progress is shown while the itinerary is being built, and the form cannot be submitted again", async ({ page }) => {
  await signUp(page);
  await fillTripForm(page, { ...DEFAULT_TRIP, destination: "Slowville, Testland" });
  const form = page.getByRole("form", { name: "Create a new trip" });

  await submitTripButton(page).click();

  // while the job is really running
  await expect(submitTripButton(page)).toBeDisabled();
  await expect(submitTripButton(page)).toHaveText(/Building your itinerary/);
  await expect(form).toHaveAttribute("aria-busy", "true");
  const progress = page.getByRole("status").filter({ hasText: /usually takes|Still working|Finding|Checking|Building|Finishing/ }).first();
  await expect(progress).toBeVisible();
  // it never claims a percentage it does not know
  await expect(progress).not.toContainText(/%/);
  await expect(page.getByRole("heading", ITINERARY)).toHaveCount(0);

  await expect(page.getByRole("heading", ITINERARY)).toBeVisible({ timeout: 40_000 });
  await expect(form).toHaveAttribute("aria-busy", "false");
});

test("submitting twice, by double click and by Enter, creates one trip and one job", async ({ page }) => {
  await signUp(page);
  const seen = countRequests(page);
  await fillTripForm(page, { ...DEFAULT_TRIP, destination: "Slowville, Testland" });

  await submitTripButton(page).dblclick();
  await page.getByLabel("Destination").press("Enter").catch(() => {});
  await page.getByLabel("Destination").press("Enter").catch(() => {});
  await expect(submitTripButton(page)).toBeDisabled();

  await expect(page.getByRole("heading", ITINERARY)).toBeVisible({ timeout: 40_000 });
  expect(seen).toEqual({ createTrip: 1, generate: 1 });
  await expectTripRows(page, ["Slowville, Testland"]);
  const listed = (await (await page.request.get("/api/trips")).json()).data.trips;
  expect(listed).toHaveLength(1);
});

test("a trip longer than seven days is refused in the form, with nothing sent", async ({ page }) => {
  await signUp(page);
  const seen = countRequests(page);
  const form = page.getByRole("form", { name: "Create a new trip" });
  await fillTripForm(page, { ...DEFAULT_TRIP, startDate: "2027-03-01", endDate: "2027-03-07" });
  // the date picker itself stops at the last allowed day
  await expect(form.getByLabel("End date")).toHaveAttribute("max", "2027-03-07");
  await expect(form.getByLabel("End date")).toHaveAttribute("min", "2027-03-01");

  // one day more, typed in past the picker
  await form.getByLabel("End date").evaluate((input: HTMLInputElement) => input.removeAttribute("max"));
  await fillTripForm(page, { endDate: "2027-03-08" });
  await submitTripButton(page).click();

  const alert = page.getByRole("alert").filter({ hasText: "A trip can be at most 7 days long" });
  await expect(alert).toBeVisible();
  await expect(alert).toContainText("Choose an earlier end date.");
  await expect(page.getByRole("button", { name: "Try again" })).toHaveCount(0); // there is nothing to retry
  expect(seen).toEqual({ createTrip: 0, generate: 0 });
  await expect(submitTripButton(page)).toBeEnabled();

  // exactly seven days is accepted
  await form.getByLabel("End date").fill("2027-03-07");
  await submitTripButton(page).click();
  await expect(page.getByRole("heading", ITINERARY)).toBeVisible({ timeout: 30_000 });
  await expect(dayHeadings(page)).toHaveCount(7);
});

test("list sizes are bounded in the form, and the message says what to change", async ({ page }) => {
  await signUp(page);
  const seen = countRequests(page);
  const many = Array.from({ length: 21 }, (_, index) => `interest ${index + 1}`);

  await fillTripForm(page, { ...DEFAULT_TRIP, interests: many });
  await submitTripButton(page).click();
  await expect(page.getByRole("alert").filter({ hasText: "Please list at most 20 interests. You entered 21." })).toBeVisible();

  await fillTripForm(page, { interests: ["food"], mustVisit: ["x".repeat(121)] });
  await submitTripButton(page).click();
  await expect(
    page.getByRole("alert").filter({ hasText: "Each entry in must-visit places can be at most 120 characters long." }),
  ).toBeVisible();
  expect(seen).toEqual({ createTrip: 0, generate: 0 });
});

test("the browser's own validation stops an empty destination and an end before the start", async ({ page }) => {
  await signUp(page);
  const seen = countRequests(page);
  const form = page.getByRole("form", { name: "Create a new trip" });

  await form.getByLabel("Destination").fill("");
  await submitTripButton(page).click();
  expect(await form.getByLabel("Destination").evaluate((input: HTMLInputElement) => input.validity.valueMissing)).toBe(true);

  await fillTripForm(page, { destination: "Fixtureville", startDate: "2027-03-05", endDate: "2027-03-01" });
  await submitTripButton(page).click();
  expect(await form.getByLabel("End date").evaluate((input: HTMLInputElement) => input.validity.rangeUnderflow)).toBe(true);
  expect(seen).toEqual({ createTrip: 0, generate: 0 });
});

test("a 422 from the server is shown in plain words, without the technical prefix", async ({ page }) => {
  await signUp(page);
  // The form's own limits are bypassed on purpose: this is the server refusing what the form let through.
  await page.route("**/api/trips", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    const body = route.request().postDataJSON();
    body.end_date = "2027-04-30"; // sixty-one days
    const response = await route.fetch({ postData: JSON.stringify(body) });
    await route.fulfill({ response });
  });
  await fillTripForm(page, DEFAULT_TRIP);

  await submitTripButton(page).click();

  const alert = page.getByRole("alert").filter({ hasText: "A trip can be at most 7 days long" });
  await expect(alert).toBeVisible();
  await expect(alert).not.toContainText(/Value error|end_date|VALIDATION_ERROR|422/);
  await expect(page.getByRole("button", { name: "Try again" })).toHaveCount(0);
  await expectTripRows(page, []); // nothing was stored
});

test("the real per-account limit is explained, and offers no pointless retry", async ({ page }) => {
  await signUpViaApi(page);
  // the offline backend allows three itineraries per account per day
  for (let index = 0; index < 3; index += 1) {
    await generateViaApi(page, await createTripViaApi(page));
  }
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "My trips" })).toBeVisible();
  await fillTripForm(page, { ...DEFAULT_TRIP, destination: "One Too Many, Testland" });

  await submitTripButton(page).click();

  const alert = page.getByRole("alert").filter({ hasText: "You have reached the limit of 3 itineraries per 24 hours." });
  await expect(alert).toBeVisible();
  await expect(alert).not.toContainText(/GENERATION_USER_LIMIT_REACHED|429/);
  await expect(page.getByRole("button", { name: "Try again" })).toHaveCount(0);
  await expect(page.getByRole("heading", ITINERARY)).toHaveCount(0);
  await expect(submitTripButton(page)).toBeEnabled();
});

test("when every slot is busy the visitor can try again, and the same trip is reused", async ({ page }) => {
  await signUp(page);
  const seen = countRequests(page);
  let refuse = true;
  await page.route("**/api/trips/*/generate", async (route) => {
    if (!refuse) return route.fallback();
    await route.fulfill({
      status: 429,
      contentType: "application/json",
      headers: { "Cache-Control": "no-store" },
      body: JSON.stringify({
        success: false,
        data: null,
        message: "Other itineraries are being built right now. Your trip is saved. Please try again in a minute.",
        errors: [
          {
            code: "GENERATION_CAPACITY_REACHED",
            field: null,
            message: "Other itineraries are being built right now. Your trip is saved. Please try again in a minute.",
          },
        ],
        metadata: { request_id: "req_e2e_capacity" },
      }),
    });
  });
  await fillTripForm(page, DEFAULT_TRIP);

  await submitTripButton(page).click();
  await expect(page.getByRole("alert").filter({ hasText: "Other itineraries are being built right now." })).toBeVisible();
  // the trip was saved (the list is refreshed in the background, so it is awaited, not read once)
  await expectTripRows(page, [DEFAULT_TRIP.destination]);

  refuse = false;
  await page.getByRole("button", { name: "Try again" }).click();

  await expect(page.getByRole("heading", ITINERARY)).toBeVisible({ timeout: 30_000 });
  // one trip, generated on the second attempt -- not a second, empty trip
  expect(seen).toEqual({ createTrip: 1, generate: 2 });
  await expectTripRows(page, [DEFAULT_TRIP.destination]);
});

test("a 503 while starting is reported as temporary and can be retried", async ({ page, watch }) => {
  watch.allowPageErrors(); // the 503 is deliberate
  await signUp(page);
  let failing = true;
  await page.route("**/api/trips/*/generate", async (route) => {
    if (!failing) return route.fallback();
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({
        success: false,
        data: null,
        message: "The database is temporarily unavailable. Please try again shortly.",
        errors: [
          { code: "PERSISTENCE_UNAVAILABLE", field: null, message: "The database is temporarily unavailable. Please try again shortly." },
        ],
        metadata: { request_id: "req_e2e_503" },
      }),
    });
  });
  await fillTripForm(page, DEFAULT_TRIP);

  await submitTripButton(page).click();

  const alert = page.getByRole("alert").filter({ hasText: /couldn't finish building this itinerary/ });
  await expect(alert).toBeVisible();
  await expect(alert).not.toContainText(/database|PERSISTENCE_UNAVAILABLE|503/); // no internals in Traveler view
  failing = false;
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("heading", ITINERARY)).toBeVisible({ timeout: 30_000 });
});

test("a gateway error page instead of JSON is treated as the server waking up", async ({ page, watch }) => {
  watch.allowPageErrors();
  await signUp(page);
  await page.route("**/api/trips", (route) =>
    route.request().method() === "POST"
      ? route.fulfill({ status: 502, contentType: "text/html", body: "<html><body>Bad Gateway</body></html>" })
      : route.fallback(),
  );
  await fillTripForm(page, DEFAULT_TRIP);

  await submitTripButton(page).click();

  await expect(page.getByRole("alert").filter({ hasText: /couldn't reach the server|starting up/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();
});

// -- when the provider behind a stage fails ----------------------------------------------------------

async function failedJob(page: import("@playwright/test").Page, errorCode: string, message: string) {
  // The job really runs and succeeds; what the page is TOLD is that it failed, with the backend's own
  // envelope shape, so the failed-job presentation can be seen without breaking the backend.
  await page.route("**/api/trips/*/jobs/*", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    if (["succeeded", "failed"].includes(body.data?.status)) {
      Object.assign(body.data, { status: "failed", error_code: errorCode, error_message: message, result_version: null });
    }
    await route.fulfill({ response, json: body });
  });
}

test("a job that ends failed is reported in plain words, with a retry on the same trip", async ({ page }) => {
  await signUp(page);
  const seen = countRequests(page);
  await failedJob(
    page,
    "STAGE_FAILED",
    "Trip plan generation failed unexpectedly. Check GET /trips/{trip_id}/generation-progress for which stage was running when it failed.",
  );
  await fillTripForm(page, DEFAULT_TRIP);

  await submitTripButton(page).click();

  await expect(page.getByText("We couldn't finish building this itinerary. Please try again in a moment.")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("heading", ITINERARY)).toHaveCount(0);
  // the backend's developer-facing message never reaches Traveler view
  await expect(page.getByText(/generation-progress|STAGE_FAILED|GET \/trips/)).toHaveCount(0);
  await expectTripRows(page, [DEFAULT_TRIP.destination]);
  await expect(submitTripButton(page)).toBeEnabled();
  expect(seen.createTrip).toBe(1);
});

test("an interrupted job says it was interrupted", async ({ page }) => {
  await signUp(page);
  await failedJob(page, "JOB_INTERRUPTED", "This background job was interrupted before it completed. Start generation again.");
  await fillTripForm(page, DEFAULT_TRIP);

  await submitTripButton(page).click();

  await expect(page.getByText("Building this itinerary was interrupted before it finished. Please try again.")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("heading", ITINERARY)).toHaveCount(0);
});

test("when the places lookup itself fails nothing is shown as an itinerary and nothing internal leaks", async ({ page }) => {
  await signUp(page);
  await fillTripForm(page, { ...DEFAULT_TRIP, destination: "Failville, Testland" });

  await submitTripButton(page).click();

  await expect(page.getByRole("alert").filter({ hasText: /\S/ }).first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("heading", ITINERARY)).toHaveCount(0);
  await expect(page.getByText(/RuntimeError|Traceback|simulated provider failure|destination_context/)).toHaveCount(0);
  await expectTripRows(page, ["Failville, Testland"]);
  await expect(submitTripButton(page)).toBeEnabled();
});

// P1C-2 (fixed): when the destination stage raises, the backend ends the job `succeeded` with the plan
// `blocked`, nothing scheduled and no destination context. The page used to announce "Your itinerary is
// ready" and then "This trip doesn't have an itinerary yet... it may still be in progress".
test("P1C-2: a generation whose first stage failed is not announced as ready or as still in progress", async ({ page }) => {
  await signUp(page);
  const seen = countRequests(page);
  // every word shown while the page works and afterwards is collected, so a "ready" that only flashes is caught
  await page.evaluate(() => {
    const log: string[] = ((window as unknown as { __shown: string[] }).__shown = []);
    new MutationObserver(() => log.push(document.body.innerText)).observe(document.body, { childList: true, subtree: true, characterData: true });
  });
  await fillTripForm(page, { ...DEFAULT_TRIP, destination: "Failville, Testland" });
  await submitTripButton(page).click();
  await expect(page.getByRole("alert").filter({ hasText: /\S/ }).first()).toBeVisible({ timeout: 30_000 });

  await expect(page.getByText("Your itinerary is ready")).toHaveCount(0, { timeout: 2000 });
  await expect(page.getByText(/may still be in progress/)).toHaveCount(0, { timeout: 2000 });

  // what it says instead: no itinerary, the trip is kept, a retry that promises nothing
  const alert = page.getByRole("alert").filter({ hasText: "We couldn't build an itinerary for this trip" });
  await expect(alert).toBeVisible();
  await expect(alert).toContainText("no places could be scheduled, so there is no plan to follow");
  await expect(alert).toContainText("You can try again, but it may not work");
  await expect(alert).not.toContainText(/provider|blocked|validation|destination_context/i);
  // nothing that reads as success or as work in progress is left beside it
  await expect(page.getByText("Itinerary generation finished")).toHaveCount(0);
  await expect(page.getByText(/Finished\. Opening the result/)).toHaveCount(0);
  await expect(dayHeadings(page)).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Your itinerary, day by day" })).toHaveCount(0);
  await expect(submitTripButton(page)).toBeEnabled();
  const shown: string[] = await page.evaluate(() => (window as unknown as { __shown: string[] }).__shown);
  expect(shown.filter((text) => /itinerary is ready|Itinerary ready|may still be in progress/i.test(text))).toEqual([]);

  // "Try again" builds the SAME trip again -- no second trip -- and the honest outcome is reported again
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(alert).toBeVisible({ timeout: 30_000 });
  expect(seen).toEqual({ createTrip: 1, generate: 2 });
  await expectTripRows(page, ["Failville, Testland"]);

  // reopening it from My trips says the same thing, without a retry that could only reload it
  await page.reload();
  await expect(page.getByRole("heading", { name: "My trips" })).toBeVisible();
  await page.getByRole("button", { name: /Failville, Testland/ }).first().click();
  const reopened = page.getByRole("alert").filter({ hasText: "This trip has no usable itinerary" });
  await expect(reopened).toBeVisible();
  await expect(reopened).toContainText("You can delete it under My trips and create it again.");
  await expect(page.getByText(/may still be in progress|itinerary is ready/i)).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Try again" })).toHaveCount(0);
});

// P1C-2: the neighbouring outcomes keep their own, different presentation.
test("P1C-2: a finished generation is never called ready before its checks say so", async ({ page }) => {
  await signUp(page);
  await page.evaluate(() => {
    const log: string[] = ((window as unknown as { __shown: string[] }).__shown = []);
    new MutationObserver(() => log.push(document.body.innerText)).observe(document.body, { childList: true, subtree: true, characterData: true });
  });
  await fillTripForm(page, DEFAULT_TRIP);
  await submitTripButton(page).click();
  await expect(page.getByRole("heading", { name: "Your itinerary, day by day" })).toBeVisible({ timeout: 40_000 });

  // this plan needs review: it is shown, with its material warning, and was never announced as ready
  await expect(page.getByRole("alert").filter({ hasText: "Review needed before you follow this" })).toBeVisible();
  await expect(dayHeadings(page)).toHaveCount(3);
  const shown: string[] = await page.evaluate(() => (window as unknown as { __shown: string[] }).__shown);
  expect(shown.length).toBeGreaterThan(0);
  expect(shown.filter((text) => /itinerary is ready|Itinerary ready/i.test(text))).toEqual([]);
});
