import { DEFAULT_TRIP, PASSWORD, expect, expectNoHorizontalScroll, expectTripRows, fillTripForm, logIn, signUp, submitTripButton, test } from "./support/app";

// One person's whole visit, start to finish, through the user interface only. Runs on the desktop and the
// 375 px phone viewport: it is the test that says "the product works on a phone", where the other journey
// files (desktop only) say exactly how each part behaves.

test("sign up, plan a trip, read it, come back to it, delete it, delete the account", async ({ page, context, watch }) => {
  // -- sign up ---------------------------------------------------------------------------------------
  const email = await signUp(page);
  await expect(page.getByText(`Signed in as ${email}`)).toBeVisible();
  await expectNoHorizontalScroll(page);

  // -- plan a trip -----------------------------------------------------------------------------------
  await fillTripForm(page, DEFAULT_TRIP);
  await submitTripButton(page).click();
  await expect(page.getByRole("heading", { name: "Your itinerary, day by day" })).toBeVisible({ timeout: 40_000 });
  await expect(page.getByRole("heading", { level: 3, name: /^Day \d+ · / })).toHaveCount(3);
  await expect(page.getByRole("alert").filter({ hasText: "Review needed before you follow this" })).toBeVisible();
  const maps = page.locator(".leaflet-container");
  await expect(maps).toHaveCount(3);
  await maps.first().scrollIntoViewIfNeeded();
  await expect(maps.first().locator(".leaflet-marker-icon")).toHaveCount(3);
  await expectNoHorizontalScroll(page);
  await expectTripRows(page, [DEFAULT_TRIP.destination]);

  // -- come back later: a reload, then a fresh log-in ------------------------------------------------
  await page.reload();
  await expect(page.getByRole("heading", { name: "Your itinerary, day by day" })).toBeVisible({ timeout: 20_000 });
  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page.getByLabel("Email")).toBeVisible();
  await logIn(page, email, PASSWORD);
  await expect(page.getByRole("heading", { name: "My trips" })).toBeVisible();
  await expect(page.getByRole("button", { name: /^Delete trip: / })).toBeVisible();
  await expectTripRows(page, [DEFAULT_TRIP.destination]);

  // -- delete the trip -------------------------------------------------------------------------------
  await page.getByRole("button", { name: `Delete trip: ${DEFAULT_TRIP.destination}` }).click();
  await page.getByRole("group", { name: `Confirm deleting ${DEFAULT_TRIP.destination}` }).getByRole("button", { name: "Delete trip" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Trip deleted." })).toBeVisible();
  await expectTripRows(page, []);

  // -- delete the account ----------------------------------------------------------------------------
  const account = page.getByRole("region", { name: "Delete account" });
  await account.getByRole("button", { name: "Delete my account…" }).click();
  await account.getByLabel("Your password").fill(PASSWORD);
  await account.getByRole("checkbox").check();
  await account.getByRole("button", { name: "Permanently delete my account" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Your account and trips were deleted." })).toBeVisible();
  await expect(page.getByLabel("Email")).toBeVisible();
  expect((await context.cookies()).find((cookie) => cookie.name === "travelobligator_session")).toBeUndefined();

  // the whole visit, maps included, produced no policy report
  expect(watch.cspViolations).toEqual([]);
});
