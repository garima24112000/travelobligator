import {
  createTripViaApi,
  expect,
  generateViaApi,
  expectTripRows,
  openGeneratedTrip,
  signUpViaApi,
  test,
} from "./support/app";

// C. Saved trips: the list, reopening, surviving a refresh, and deleting.

const ITINERARY = { name: "Your itinerary, day by day" };

function deleteRequests(page: import("@playwright/test").Page): string[] {
  const seen: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "DELETE") seen.push(new URL(request.url()).pathname);
  });
  return seen;
}

test("My trips lists every saved trip and reopens the one that is chosen", async ({ page }) => {
  await signUpViaApi(page);
  const first = await createTripViaApi(page, { destination: "Alphaville, Testland", startDate: "2027-04-01", endDate: "2027-04-02" });
  const second = await createTripViaApi(page, { destination: "Betaville, Testland", origin: "Second Origin" });
  await generateViaApi(page, first);
  await generateViaApi(page, second);

  await page.goto("/");
  await expect(page.getByRole("button", { name: "Delete trip: Alphaville, Testland" })).toBeVisible();

  await expectTripRows(page, { inAnyOrder: ["Alphaville, Testland", "Betaville, Testland"] });
  const list = page.locator('ul:has(button[aria-label^="Delete trip:"])');
  await expect(list.getByText("From Home City · 2027-04-01 → 2027-04-02")).toBeVisible();

  await list.getByRole("button", { name: /^Betaville, Testland/ }).click();
  await expect(page.getByRole("heading", { level: 2, name: "Betaville, Testland" })).toBeVisible();
  await expect(page.getByText(/from Second Origin/)).toBeVisible();

  // and the other one, without a reload: nothing of the first trip is left on screen
  await list.getByRole("button", { name: /^Alphaville, Testland/ }).click();
  await expect(page.getByRole("heading", { level: 2, name: "Alphaville, Testland" })).toBeVisible();
  await expect(page.getByRole("heading", { level: 2, name: "Betaville, Testland" })).toHaveCount(0);
  await expect(page.getByRole("heading", { level: 3, name: /^Day \d+ · / })).toHaveCount(2);
});

test("a refresh keeps the same trip open, without building it again", async ({ page }) => {
  const tripId = await openGeneratedTrip(page, { destination: "Refreshville, Testland" });
  const generations: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().includes("/generate")) generations.push(request.url());
  });
  expect(new URL(page.url()).searchParams.get("trip")).toBe(tripId);

  await page.reload();

  await expect(page.getByRole("heading", { level: 2, name: "Refreshville, Testland" })).toBeVisible();
  await expect(page.getByRole("heading", ITINERARY)).toBeVisible();
  expect(new URL(page.url()).searchParams.get("trip")).toBe(tripId);
  expect(generations).toEqual([]);
  const jobs = (await (await page.request.get(`/api/trips/${tripId}/jobs`)).json()).data.jobs;
  expect(jobs).toHaveLength(1); // still the one job that built it
});

test("a link to someone else's trip shows nothing of it", async ({ page, browser }) => {
  const ownerContext = await browser.newContext();
  const owner = await ownerContext.newPage();
  await signUpViaApi(owner);
  const tripId = await createTripViaApi(owner, { destination: "Privateville, Testland" });
  await generateViaApi(owner, tripId);
  await ownerContext.close();

  await signUpViaApi(page);
  await page.goto(`/?trip=${tripId}`);

  await expect(page.getByRole("alert").filter({ hasText: "You do not have access to this trip." })).toBeVisible();
  await expect(page.getByText("Privateville")).toHaveCount(0);
  await expect(page.getByRole("heading", ITINERARY)).toHaveCount(0);
  await expectTripRows(page, []);
  // the dead link is forgotten rather than retried on every reload
  await page.reload();
  await expect(page.getByRole("heading", { name: "My trips" })).toBeVisible();
  expect(new URL(page.url()).searchParams.get("trip")).toBeNull();
});

test("deleting a trip asks first, and Cancel leaves everything as it was", async ({ page }) => {
  await openGeneratedTrip(page, { destination: "Keepville, Testland" });
  const deletes = deleteRequests(page);

  await page.getByRole("button", { name: "Delete trip: Keepville, Testland" }).click();

  const confirmation = page.getByRole("group", { name: "Confirm deleting Keepville, Testland" });
  await expect(confirmation).toContainText(
    "Delete Keepville, Testland? Its itinerary, earlier versions and change requests will be removed. This cannot be undone.",
  );
  await expect(page.getByRole("button", { name: "Delete trip: Keepville, Testland" })).toHaveAttribute("aria-expanded", "true");
  expect(deletes).toEqual([]); // asking is not deleting

  await confirmation.getByRole("button", { name: "Cancel" }).click();

  await expect(confirmation).toHaveCount(0);
  expect(deletes).toEqual([]);
  await expectTripRows(page, ["Keepville, Testland"]);
  await expect(page.getByRole("heading", ITINERARY)).toBeVisible(); // the open itinerary is untouched
  // the Delete button toggles: a second click closes the question again
  await page.getByRole("button", { name: "Delete trip: Keepville, Testland" }).click();
  await page.getByRole("button", { name: "Delete trip: Keepville, Testland" }).click();
  await expect(confirmation).toHaveCount(0);
});

test("confirming deletes the trip, closes it if it was open, and frees its place", async ({ page }) => {
  await signUpViaApi(page);
  const kept = await createTripViaApi(page, { destination: "Staysville, Testland" });
  const removed = await createTripViaApi(page, { destination: "Goneville, Testland" });
  await generateViaApi(page, kept);
  await generateViaApi(page, removed);
  await page.goto(`/?trip=${removed}`);
  await expect(page.getByRole("heading", { level: 2, name: "Goneville, Testland" })).toBeVisible();
  const deletes = deleteRequests(page);

  await page.getByRole("button", { name: "Delete trip: Goneville, Testland" }).click();
  await page.getByRole("group", { name: "Confirm deleting Goneville, Testland" }).getByRole("button", { name: "Delete trip" }).click();

  await expect(page.getByRole("status").filter({ hasText: "Trip deleted." })).toBeVisible();
  await expectTripRows(page, ["Staysville, Testland"]);
  expect(deletes).toEqual([`/api/trips/${removed}`]);
  // the deleted trip was the one on screen: it is closed, and a reload does not try to reopen it
  await expect(page.getByRole("heading", { level: 2, name: "Goneville, Testland" })).toHaveCount(0);
  await expect(page.getByRole("heading", ITINERARY)).toHaveCount(0);
  expect(new URL(page.url()).searchParams.get("trip")).toBeNull();
  await page.reload();
  await expect(page.getByRole("heading", { name: "My trips" })).toBeVisible();
  await expectTripRows(page, ["Staysville, Testland"]);
  await expect(page.getByRole("alert").filter({ hasText: /\S/ })).toHaveCount(0);
  // gone on the server too, and the other trip is intact
  expect((await page.request.get(`/api/trips/${removed}`)).status()).toBe(404);
  expect((await page.request.get(`/api/trips/${kept}`)).status()).toBe(200);
});

test("deleting a trip that is not the open one leaves the open itinerary alone", async ({ page }) => {
  await signUpViaApi(page);
  const open = await createTripViaApi(page, { destination: "Openville, Testland" });
  const other = await createTripViaApi(page, { destination: "Otherville, Testland" });
  await generateViaApi(page, open);
  await page.goto(`/?trip=${open}`);
  await expect(page.getByRole("heading", ITINERARY)).toBeVisible();

  await page.getByRole("button", { name: "Delete trip: Otherville, Testland" }).click();
  await page.getByRole("group", { name: "Confirm deleting Otherville, Testland" }).getByRole("button", { name: "Delete trip" }).click();

  await expect(page.getByRole("status").filter({ hasText: "Trip deleted." })).toBeVisible();
  await expect(page.getByRole("heading", { level: 2, name: "Openville, Testland" })).toBeVisible();
  await expect(page.getByRole("heading", ITINERARY)).toBeVisible();
  expect(new URL(page.url()).searchParams.get("trip")).toBe(open);
  expect((await page.request.get(`/api/trips/${other}`)).status()).toBe(404);
});

test("a trip whose itinerary is being built cannot be deleted until it has finished", async ({ page }) => {
  await signUpViaApi(page);
  const tripId = await createTripViaApi(page, { destination: "Slowville, Testland" });
  await generateViaApi(page, tripId, { wait: false }); // the real job is now running for several seconds
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Delete trip: Slowville, Testland" })).toBeVisible();

  await page.getByRole("button", { name: "Delete trip: Slowville, Testland" }).click();
  const confirmation = page.getByRole("group", { name: "Confirm deleting Slowville, Testland" });
  await confirmation.getByRole("button", { name: "Delete trip" }).click();

  const refusal = page.getByRole("alert").filter({ hasText: "This trip's itinerary is being built right now, so it cannot be deleted yet." });
  await expect(refusal).toBeVisible();
  await expect(refusal).not.toContainText(/TRIP_DELETION_BLOCKED|409/);
  await expectTripRows(page, ["Slowville, Testland"]); // still there
  expect((await page.request.get(`/api/trips/${tripId}`)).status()).toBe(200);

  // once the job has finished, the same confirmation deletes it
  await expect
    .poll(async () => (await (await page.request.get(`/api/trips/${tripId}/jobs`)).json()).data.jobs[0].status, { timeout: 30_000 })
    .toBe("succeeded");
  await confirmation.getByRole("button", { name: "Delete trip" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Trip deleted." })).toBeVisible();
  await expectTripRows(page, []);
});

test("a deletion that fails on the server keeps the trip and says nothing was removed", async ({ page, watch }) => {
  watch.allowPageErrors(); // the 503 is deliberate
  const tripId = await openGeneratedTrip(page, { destination: "Sturdyville, Testland" });
  await page.route(`**/api/trips/${tripId}`, (route) =>
    route.request().method() === "DELETE"
      ? route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({
            success: false,
            data: null,
            message: "The database is temporarily unavailable. Please try again shortly.",
            errors: [{ code: "PERSISTENCE_UNAVAILABLE", field: null, message: "The database is temporarily unavailable. Please try again shortly." }],
            metadata: { request_id: "req_e2e_delete_503" },
          }),
        })
      : route.fallback(),
  );

  await page.getByRole("button", { name: "Delete trip: Sturdyville, Testland" }).click();
  await page.getByRole("group", { name: "Confirm deleting Sturdyville, Testland" }).getByRole("button", { name: "Delete trip" }).click();

  await expect(page.getByRole("alert").filter({ hasText: /try again/i }).first()).toBeVisible();
  await expectTripRows(page, ["Sturdyville, Testland"]);
  await expect(page.getByRole("heading", ITINERARY)).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "Trip deleted." })).toHaveCount(0);
});

test("the place a deleted trip took can be used again", async ({ page }) => {
  await signUpViaApi(page);
  const ids: string[] = [];
  for (let index = 0; index < 20; index += 1) ids.push(await createTripViaApi(page, { destination: `Quota ${index + 1}, Testland` }));
  const refused = await page.request.post("/api/trips", {
    data: { primary_destination: "One Too Many", start_date: "2027-03-01", end_date: "2027-03-02", travelers_count: 1, travel_group_type: "solo" },
  });
  expect(refused.status()).toBe(429);
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Delete trip: Quota 7, Testland" })).toBeVisible();

  await page.getByRole("button", { name: "Delete trip: Quota 7, Testland" }).click();
  await page.getByRole("group", { name: "Confirm deleting Quota 7, Testland" }).getByRole("button", { name: "Delete trip" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Trip deleted." })).toBeVisible();

  await expectTripRows(page, { count: 19 });
  const accepted = await page.request.post("/api/trips", {
    data: { primary_destination: "Fits Again", start_date: "2027-03-01", end_date: "2027-03-02", travelers_count: 1, travel_group_type: "solo" },
  });
  expect(accepted.status()).toBe(201);
});
