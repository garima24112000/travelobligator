import { BETA_LABEL, BETA_NOTICE_HEADING, BETA_NOTICE_POINTS, BETA_SUMMARY, PASSWORD_LOSS_WARNING } from "../lib/beta-notice";
import { createTripViaApi, expect, generateViaApi, openGeneratedTrip, reshapeTrip, setReview, signUp, signUpViaApi, test } from "./support/app";

// Release-copy alignment: the limited-beta labelling and the password-loss warning, as a visitor sees them.
// The labelling is general; what is said about one itinerary still comes from its own validation banner,
// and these tests check that the two stand side by side without the first softening the second.
// (Layout at every viewport and the axe scans cover these screens in layout.spec.ts / accessibility.spec.ts.)

// A claim that results in general are finished or checked. Two denials are the opposite of such a claim
// and are not matched: "could not be fully verified" (the holiday notice) and "are not independently
// guaranteed" (the beta notice itself).
const POSITIVE_CLAIM = /itinerary is ready|ready to follow|(?<!not be )fully verified|(?<!not independently )guaranteed/i;

function betaNotice(page: import("@playwright/test").Page) {
  return page.getByRole("region", { name: BETA_NOTICE_HEADING });
}

test("a visitor sees the beta label and what it means before signing in", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "Log in to your account" })).toBeVisible();

  // the label, as words, beside the product name
  await expect(page.getByText(BETA_LABEL, { exact: true })).toBeVisible();
  await expect(page.locator("p", { hasText: "TravelObligator" }).first()).toContainText(BETA_LABEL);
  await expect(page.getByText(BETA_SUMMARY)).toBeVisible();

  // the five points: visible without opening anything, under a real heading
  const notice = betaNotice(page);
  await expect(notice.getByRole("heading", { level: 2, name: BETA_NOTICE_HEADING })).toBeVisible();
  await expect(notice.getByRole("listitem")).toHaveText(BETA_NOTICE_POINTS);
  for (const point of BETA_NOTICE_POINTS) await expect(notice.getByText(point)).toBeVisible();
  await expect(notice.locator("xpath=ancestor::details")).toHaveCount(0);

  // it is not a claim about results
  await expect(page.locator("main")).not.toContainText(POSITIVE_CLAIM);
});

test("the password-loss warning is shown when registering, and only then", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByLabel("Email")).toBeVisible();
  // logging in: no such warning in the form (the account, and its password, already exist). The sentence
  // is still in the collapsed privacy notice, so the form's own element is what is checked.
  await expect(page.locator("#password-loss-warning")).toHaveCount(0);
  await expect(page.locator("form").getByText(PASSWORD_LOSS_WARNING)).toHaveCount(0);

  await page.getByRole("button", { name: "Sign up" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Create your account" })).toBeVisible();
  const warning = page.locator("#password-loss-warning");
  await expect(warning).toBeVisible();
  await expect(warning).toHaveText(PASSWORD_LOSS_WARNING);

  // inside the form, above the button that creates the account
  const form = page.locator("form").filter({ has: page.getByLabel("Confirm password") });
  await expect(form.locator("#password-loss-warning")).toHaveCount(1);
  const create = form.getByRole("button", { name: "Create account" });
  expect((await warning.boundingBox())!.y).toBeLessThan((await create.boundingBox())!.y);
  // and read out with the password field, after the length hint
  await expect(page.getByLabel("Password", { exact: true })).toHaveAccessibleDescription(`At least 8 characters. ${PASSWORD_LOSS_WARNING}`);

  // the privacy notice on the same screen says it in the same words
  const privacy = page.locator("details").filter({ hasText: "Your data and privacy" });
  await privacy.locator("summary").click();
  await expect(privacy).toContainText(PASSWORD_LOSS_WARNING);

  // back on log-in, the form's warning is gone again
  await page.getByRole("button", { name: "Log in" }).first().click();
  await expect(page.locator("#password-loss-warning")).toHaveCount(0);
});

test("a signed-in traveller sees the beta notice above their trips", async ({ page }) => {
  await signUp(page);
  await expect(page.getByText(BETA_LABEL, { exact: true })).toBeVisible();
  const notice = betaNotice(page);
  await expect(notice.getByRole("listitem")).toHaveText(BETA_NOTICE_POINTS);
  const trips = page.getByRole("heading", { level: 2, name: "My trips" });
  expect((await notice.boundingBox())!.y).toBeLessThan((await trips.boundingBox())!.y);
  // no registration warning here: there is nothing to register
  await expect(page.locator("#password-loss-warning")).toHaveCount(0);
});

test("the beta notice stands beside an itinerary's own review banner without replacing it", async ({ page }) => {
  await openGeneratedTrip(page);
  await expect(betaNotice(page)).toBeVisible();

  // this plan needs review: the material banner is still an alert, with its own wording
  const review = page.getByRole("alert").filter({ hasText: "Review needed before you follow this" });
  await expect(review).toBeVisible();
  await expect(review).toContainText("Parts of this itinerary need your attention before you rely on it.");
  // the uncovered interest is still disclosed above the itinerary
  await expect(page.getByRole("status", { name: "Not everything you asked for is covered" })).toBeVisible();
  // the general notice is not itself an alert, and does not repeat or soften the banner
  await expect(betaNotice(page)).not.toHaveAttribute("role", "alert");
  await expect(betaNotice(page).getByRole("alert")).toHaveCount(0);
  await expect(betaNotice(page)).not.toContainText(/Review needed|Not ready|Checks passed/);
  await expect(page.locator("main")).not.toContainText(POSITIVE_CLAIM);
});

test("a blocked plan and a plan that passed its checks keep their own wording under the beta notice", async ({ page }) => {
  await signUpViaApi(page);
  const blocked = await createTripViaApi(page, { destination: "Blockedville, Testland" });
  await generateViaApi(page, blocked);
  await reshapeTrip(page, blocked, {
    readiness: "blocked",
    validation: (report) =>
      setReview(report, { readiness: "blocked", blockingCodes: ["INSUFFICIENT_VERIFIED_INVENTORY"], classification: { INSUFFICIENT_VERIFIED_INVENTORY: "material" } }),
  });
  await page.goto(`/?trip=${blocked}`);
  await expect(page.getByRole("alert").filter({ hasText: "Not ready to use" })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("alert").filter({ hasText: "Not ready to use" })).toContainText("This itinerary is incomplete and should not be relied on yet.");
  await expect(betaNotice(page)).toBeVisible();

  const passed = await createTripViaApi(page, { destination: "Passedville, Testland" });
  await generateViaApi(page, passed);
  await reshapeTrip(page, passed, { readiness: "ready", validation: (report) => setReview(report, { readiness: "ready" }) });
  await page.goto(`/?trip=${passed}`);
  const banner = page.getByRole("status").filter({ hasText: "Checks passed" });
  await expect(banner).toBeVisible({ timeout: 20_000 });
  // even the best outcome is worded as "passed our automated checks", with the reminder to confirm
  await expect(banner).toContainText("Still confirm opening hours, prices and bookings yourself before you travel.");
  await expect(betaNotice(page)).toBeVisible();
  await expect(page.locator("main")).not.toContainText(POSITIVE_CLAIM);
});
