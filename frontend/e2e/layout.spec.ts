import { mkdirSync } from "node:fs";
import path from "node:path";

import type { Locator, Page, TestInfo } from "@playwright/test";

import {
  DEFAULT_TRIP,
  PASSWORD,
  createTripViaApi,
  expect,
  expectNoHorizontalScroll,
  fillTripForm,
  generateViaApi,
  openGeneratedTrip,
  reshapeTrip,
  routeLegs,
  setReview,
  signUp,
  signUpViaApi,
  submitTripButton,
  test,
  uniqueEmail,
} from "./support/app";

// Layout, at every viewport of the matrix (playwright.config.ts): desktop 1440 x 900, phone 375 x 812,
// small phone 320 x 640, and "zoom-200" -- a 720 x 450 CSS-pixel window at device scale 2, which is what a
// 1440 x 900 screen gives the page at 200 % browser zoom.
//
// What is measured: the document does not scroll sideways, nothing visible is cut off by the window's
// edge, separate controls do not sit on top of each other, and the things a person must see are on
// screen. What is NOT measured is whether it looks right. Every state is also saved as a screenshot under
// e2e-results/screenshots/<viewport>/ for a human to look at.

const LONG_NAME =
  "The Extraordinarily Long Fixture Museum of Maritime History, Decorative Arts and Regional Archaeology (East Wing)";
const NON_LATIN_NAME = "フィクスチャ国立博物館 Fixture National Museum";

async function shot(page: Page, testInfo: TestInfo, name: string, target?: Locator): Promise<void> {
  const directory = path.join(testInfo.config.rootDir, "..", "e2e-results", "screenshots", testInfo.project.name);
  mkdirSync(directory, { recursive: true });
  const file = path.join(directory, `${name}.png`);
  if (target) {
    await target.scrollIntoViewIfNeeded();
    await page.screenshot({ path: file }); // the window as the person sees it, with the target in view
  } else {
    await page.screenshot({ path: file, fullPage: true });
  }
}

/** Visible elements that reach past the window's left or right edge and are not inside a scroller. */
async function cutOff(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const width = document.documentElement.clientWidth;
    const clipped = (element: Element): boolean => {
      for (let parent = element.parentElement; parent; parent = parent.parentElement) {
        const overflow = getComputedStyle(parent).overflowX;
        if (overflow === "auto" || overflow === "scroll" || overflow === "hidden" || overflow === "clip") return true;
      }
      return false;
    };
    const found: string[] = [];
    for (const element of document.querySelectorAll("main *")) {
      const box = element.getBoundingClientRect();
      if (box.width === 0 || box.height === 0) continue;
      if (box.right <= width + 1 && box.left >= -1) continue;
      const style = getComputedStyle(element);
      if (style.visibility === "hidden" || style.display === "none") continue;
      if (element.closest(".leaflet-container") || clipped(element)) continue; // a map pans inside its own box
      found.push(`${element.tagName.toLowerCase()} "${(element.textContent ?? "").trim().slice(0, 40)}" left=${Math.round(box.left)} right=${Math.round(box.right)} window=${width}`);
    }
    return found.slice(0, 10);
  });
}

/** Pairs of separate, visible controls whose boxes overlap. */
async function overlappingControls(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const controls = [...document.querySelectorAll("main button, main a[href], main input, main select, main textarea, main summary")].filter(
      (element) => {
        const box = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        return box.width > 0 && box.height > 0 && style.visibility !== "hidden" && !element.closest(".leaflet-container");
      },
    );
    const found: string[] = [];
    const name = (element: Element) => (element.getAttribute("aria-label") || (element as HTMLElement).innerText || element.tagName).trim().slice(0, 30);
    for (let first = 0; first < controls.length; first += 1) {
      for (let second = first + 1; second < controls.length; second += 1) {
        const a = controls[first];
        const b = controls[second];
        if (a.contains(b) || b.contains(a)) continue;
        const one = a.getBoundingClientRect();
        const two = b.getBoundingClientRect();
        const overlapX = Math.min(one.right, two.right) - Math.max(one.left, two.left);
        const overlapY = Math.min(one.bottom, two.bottom) - Math.max(one.top, two.top);
        if (overlapX > 2 && overlapY > 2) found.push(`"${name(a)}" overlaps "${name(b)}" by ${Math.round(overlapX)}x${Math.round(overlapY)}`);
      }
    }
    return found.slice(0, 10);
  });
}

async function expectSoundLayout(page: Page): Promise<void> {
  await expectNoHorizontalScroll(page);
  expect(await cutOff(page), "elements cut off by the window edge").toEqual([]);
  expect(await overlappingControls(page), "controls on top of each other").toEqual([]);
}

async function expectInsideWindow(page: Page, target: Locator, what: string): Promise<void> {
  await target.scrollIntoViewIfNeeded();
  const box = (await target.boundingBox())!;
  const width = page.viewportSize()!.width;
  expect(box.x, `${what}: left edge`).toBeGreaterThanOrEqual(-1);
  expect(box.x + box.width, `${what}: right edge`).toBeLessThanOrEqual(width + 1);
}

test.describe("signed out", () => {
  test("the sign-in screen", async ({ page }, testInfo) => {
    await page.goto("/");
    await expect(page.getByLabel("Email")).toBeVisible();
    await expectSoundLayout(page);
    await shot(page, testInfo, "01-sign-in");
  });

  test("the sign-up screen with an error and the privacy notice open", async ({ page }, testInfo) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Sign up" }).click();
    await page.getByLabel("Email").fill(uniqueEmail());
    await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
    await page.getByLabel("Confirm password").fill("something-else");
    await page.getByRole("button", { name: "Create account" }).click();
    const error = page.getByText("Passwords do not match.");
    await expect(error).toBeVisible();
    await page.locator("details").filter({ hasText: "Your data and privacy" }).locator("summary").click();

    await expectSoundLayout(page);
    await expectInsideWindow(page, error, "the error");
    await shot(page, testInfo, "02-sign-up-error-privacy-open");
  });
});

test.describe("creating a trip", () => {
  test("the form's controls are all usable", async ({ page }, testInfo) => {
    await signUp(page);
    const form = page.getByRole("form", { name: "Create a new trip" });
    await fillTripForm(page, {
      ...DEFAULT_TRIP,
      destination: "Llanfairpwllgwyngyllgogerychwyrndrobwllllantysiliogogogoch, Wales",
      interests: ["food", "contemporary art", "architecture", "waterfront walks", "local markets"],
      mustVisit: [LONG_NAME, NON_LATIN_NAME],
    });

    await expectSoundLayout(page);
    // every control is wide and tall enough to use and sits wholly inside the window
    const sizes = await form.locator("input, select, textarea, button").evaluateAll((controls) =>
      controls
        .filter((control) => (control as HTMLInputElement).type !== "checkbox")
        .map((control) => {
          const box = control.getBoundingClientRect();
          return { name: control.getAttribute("name") ?? control.tagName, width: Math.round(box.width), height: Math.round(box.height), right: Math.round(box.right) };
        }),
    );
    const width = page.viewportSize()!.width;
    expect(sizes.filter((control) => control.width < 100 || control.height < 24 || control.right > width + 1)).toEqual([]);
    await shot(page, testInfo, "03-create-trip-form-filled");
  });

  test("the progress state and an error state", async ({ page }, testInfo) => {
    await signUp(page);
    await fillTripForm(page, { ...DEFAULT_TRIP, destination: "Slowville, Testland" });
    await submitTripButton(page).click();
    await expect(submitTripButton(page)).toHaveText(/Building your itinerary/);
    const progress = page.getByRole("status").filter({ hasText: /itinerary|plan/i }).first();
    await expect(progress).toBeVisible();
    await expectSoundLayout(page);
    await expectInsideWindow(page, progress, "the progress message");
    await shot(page, testInfo, "04-generation-in-progress", progress);
    await expect(page.getByRole("heading", { name: "Your itinerary, day by day" })).toBeVisible({ timeout: 40_000 });

    // an error: one day past the limit, typed in past the date picker
    const form = page.getByRole("form", { name: "Create a new trip" });
    await fillTripForm(page, DEFAULT_TRIP);
    await form.getByLabel("End date").evaluate((input: HTMLInputElement) => input.removeAttribute("max"));
    await fillTripForm(page, { endDate: "2027-03-20" });
    await submitTripButton(page).click();
    const alert = page.getByRole("alert").filter({ hasText: "A trip can be at most 7 days long" });
    await expect(alert).toBeVisible();
    await expectSoundLayout(page);
    await expectInsideWindow(page, alert, "the error");
    await shot(page, testInfo, "05-create-trip-error", alert);
  });
});

test.describe("an itinerary", () => {
  test("the whole page, with long and non-Latin place names", async ({ page }, testInfo) => {
    await openGeneratedTrip(page);
    await expect(page.locator(".leaflet-container").first().locator(".leaflet-marker-icon").first()).toBeVisible();

    await expectSoundLayout(page);
    for (const name of [LONG_NAME, NON_LATIN_NAME]) {
      const place = page.getByText(name, { exact: true }).first();
      await expect(place).toBeVisible();
      await expectInsideWindow(page, place, name);
    }
    // the long name wrapped instead of being cut: it takes more than one line on a narrow window
    if (page.viewportSize()!.width <= 400) {
      const lines = await page.getByText(LONG_NAME, { exact: true }).first().evaluate((element) => {
        const style = getComputedStyle(element);
        return element.getBoundingClientRect().height / parseFloat(style.lineHeight);
      });
      expect(lines).toBeGreaterThan(1.5);
    }
    await shot(page, testInfo, "06-itinerary-full-page");
  });

  test("every map fits the window", async ({ page }, testInfo) => {
    await openGeneratedTrip(page);
    const maps = page.locator(".leaflet-container");
    await expect(maps).toHaveCount(3);
    const window = page.viewportSize()!;
    for (let index = 0; index < 3; index += 1) {
      const map = maps.nth(index);
      await expectInsideWindow(page, map, `map ${index + 1}`);
      const box = (await map.boundingBox())!;
      expect(box.width).toBeGreaterThan(180); // 194 px on a 320 px window
      expect(box.height).toBeGreaterThan(120);
      // a map never takes the whole window, so the page can still be scrolled past it by touch
      expect(box.height).toBeLessThan(window.height);
      await expect(map.locator(".leaflet-marker-icon")).toHaveCount(3);
      // every marker is inside the map's own box
      for (const marker of await map.locator(".leaflet-marker-icon").all()) {
        const point = (await marker.boundingBox())!;
        expect(point.x).toBeGreaterThanOrEqual(box.x - 1);
        expect(point.x + point.width).toBeLessThanOrEqual(box.x + box.width + 1);
      }
    }
    await shot(page, testInfo, "07-day-map", maps.first());
  });

  test("a material warning, a ferry leg and an unverified leg stay visible", async ({ page }, testInfo) => {
    const sentence = "Day 2: Getting between this day's stops takes far longer than the rest of the trip. Consider moving a stop.";
    await signUpViaApi(page);
    const tripId = await createTripViaApi(page);
    await generateViaApi(page, tripId);
    await reshapeTrip(page, tripId, {
      readiness: "needs_review",
      // first leg a ferry, second leg left without a verified route, the rest long drives
      planningState: (state) =>
        routeLegs(state, (_leg, index) =>
          index === 1 ? { routed: false } : { routed: true, mode: index === 0 ? "walk" : "drive", seconds: 4200, meters: 31000, ferry: index === 0 },
        ),
      validation: (report) =>
        setReview(report, {
          readiness: "needs_review",
          reviewCodes: ["LONG_TRAVEL_DAY", "ROUTE_INCLUDES_FERRY"],
          classification: { LONG_TRAVEL_DAY: "material", ROUTE_INCLUDES_FERRY: "material" },
          warnings: [{ category: "long_travel_day", message: sentence, day: 2 }],
        }),
    });
    await page.goto(`/?trip=${tripId}`);
    await expect(page.getByRole("heading", { name: "Your itinerary, day by day" })).toBeVisible({ timeout: 20_000 });

    const banner = page.getByRole("alert").filter({ hasText: "Review needed before you follow this" });
    await expect(banner).toBeVisible();
    await expectInsideWindow(page, banner, "the review banner");
    await shot(page, testInfo, "08-material-review-banner", banner);

    const rows = page.getByTestId("movement-row");
    await expectInsideWindow(page, rows.nth(0), "the ferry leg");
    await expectInsideWindow(page, rows.nth(1), "the unverified leg");
    await shot(page, testInfo, "09-ferry-and-unverified-legs", rows.nth(0));
    const dayWarning = page.getByText("Getting between this day's stops takes far longer than the rest of the trip.").first();
    await expectInsideWindow(page, dayWarning, "the day's warning");
    await expectSoundLayout(page);
    await shot(page, testInfo, "10-itinerary-with-warnings-full-page");
  });

  test("a blocked plan", async ({ page }, testInfo) => {
    await signUpViaApi(page);
    const tripId = await createTripViaApi(page);
    await generateViaApi(page, tripId);
    await reshapeTrip(page, tripId, {
      readiness: "blocked",
      validation: (report) =>
        setReview(report, {
          readiness: "blocked",
          blockingCodes: ["INSUFFICIENT_VERIFIED_INVENTORY"],
          classification: { INSUFFICIENT_VERIFIED_INVENTORY: "material" },
        }),
    });
    await page.goto(`/?trip=${tripId}`);
    const banner = page.getByRole("alert").filter({ hasText: "Not ready to use" });
    await expect(banner).toBeVisible({ timeout: 20_000 });
    await expectInsideWindow(page, banner, "the blocked banner");
    await expectSoundLayout(page);
    await shot(page, testInfo, "11-blocked-plan", banner);
  });

  test("Developer view", async ({ page }, testInfo) => {
    await openGeneratedTrip(page);
    await page.getByRole("button", { name: "Developer view" }).click();
    await expect(page.getByRole("button", { name: "Developer view" })).toHaveAttribute("aria-pressed", "true");
    await expectNoHorizontalScroll(page);
    await shot(page, testInfo, "12-developer-view-full-page");
  });
});

test.describe("generation outcomes", () => {
  test("a generation that produced no usable itinerary", async ({ page }, testInfo) => {
    await signUp(page);
    await fillTripForm(page, { ...DEFAULT_TRIP, destination: "Failville, Testland" });
    await submitTripButton(page).click();
    const alert = page.getByRole("alert").filter({ hasText: "We couldn't build an itinerary for this trip" });
    await expect(alert).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();

    await expectInsideWindow(page, alert, "the outcome message");
    await expectInsideWindow(page, page.getByRole("button", { name: "Try again" }), "Try again");
    await expectSoundLayout(page);
    await shot(page, testInfo, "15-no-usable-itinerary", alert);
  });

  test("a failed generation job", async ({ page }, testInfo) => {
    await signUp(page);
    // the job really runs; the page is told it failed, in the backend's own envelope (as in trip-creation.spec.ts)
    await page.route("**/api/trips/*/jobs/*", async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      if (["succeeded", "failed"].includes(body.data?.status)) {
        Object.assign(body.data, { status: "failed", error_code: "STAGE_FAILED", error_message: "Trip plan generation failed unexpectedly.", result_version: null });
      }
      await route.fulfill({ response, json: body });
    });
    await fillTripForm(page, DEFAULT_TRIP);
    await submitTripButton(page).click();
    const failure = page.getByRole("alert").filter({ hasText: "We couldn't build this itinerary" });
    await expect(failure).toBeVisible({ timeout: 30_000 });

    await expectInsideWindow(page, failure, "the failure card");
    await expectSoundLayout(page);
    await shot(page, testInfo, "16-failed-generation-job", failure);
  });

  test("a plan that passed its checks, and the notice about an interest that is not covered", async ({ page }, testInfo) => {
    await signUpViaApi(page);
    const tripId = await createTripViaApi(page);
    await generateViaApi(page, tripId);
    await reshapeTrip(page, tripId, {
      readiness: "ready",
      planningState: (state) => routeLegs(state, () => ({ routed: true })),
      // the readiness alone is changed; the real report's interest coverage (art is not covered) is kept
      validation: (report) => setReview(report, { readiness: "ready" }),
    });
    await page.goto(`/?trip=${tripId}`);
    const passed = page.getByRole("status").filter({ hasText: "Checks passed" });
    await expect(passed).toBeVisible({ timeout: 20_000 });
    await expectInsideWindow(page, passed, "the checks-passed banner");
    await shot(page, testInfo, "17-ready-itinerary-banner", passed);

    const coverage = page.getByRole("status", { name: "Not everything you asked for is covered" });
    await expect(coverage).toBeVisible();
    await expectInsideWindow(page, coverage, "the interest notice");
    await expectSoundLayout(page);
    await shot(page, testInfo, "18-interest-not-covered-notice", coverage);
    await shot(page, testInfo, "19-ready-itinerary-full-page");
  });
});

test.describe("deleting", () => {
  test("the delete-trip confirmation fits, with a long trip name", async ({ page }, testInfo) => {
    const destination = "Llanfairpwllgwyngyllgogerychwyrndrobwllllantysiliogogogoch, Wales";
    await openGeneratedTrip(page, { destination });
    await page.getByRole("button", { name: `Delete trip: ${destination}` }).click();
    const confirmation = page.getByRole("group", { name: `Confirm deleting ${destination}` });
    await expect(confirmation).toBeVisible();

    await expectInsideWindow(page, confirmation, "the confirmation");
    for (const name of ["Delete trip", "Cancel"]) {
      const button = confirmation.getByRole("button", { name });
      await expectInsideWindow(page, button, name);
      const box = (await button.boundingBox())!;
      expect(box.height, `${name} is tall enough to tap`).toBeGreaterThanOrEqual(24);
    }
    await expectSoundLayout(page);
    await shot(page, testInfo, "13-delete-trip-confirmation", confirmation);
  });

  test("the delete-account form fits, with its error", async ({ page }, testInfo) => {
    await signUp(page);
    const account = page.getByRole("region", { name: "Delete account" });
    await account.getByRole("button", { name: "Delete my account…" }).click();
    await account.getByLabel("Your password").fill("not-the-password");
    await account.getByRole("checkbox").check();
    await account.getByRole("button", { name: "Permanently delete my account" }).click();
    const error = account.getByRole("alert");
    await expect(error).toContainText("That password is not correct.");

    await expectInsideWindow(page, account, "the delete-account form");
    await expectInsideWindow(page, error, "the error");
    await expectInsideWindow(page, account.getByRole("button", { name: "Permanently delete my account" }), "the delete button");
    await expectInsideWindow(page, account.getByRole("button", { name: "Cancel" }), "the cancel button");
    await expectSoundLayout(page);
    await shot(page, testInfo, "14-delete-account-error", account);
  });
});
