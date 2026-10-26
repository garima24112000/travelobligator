import { GEOGRAPHIC_SPREAD_SENTENCE } from "../lib/display-labels";
import {
  FERRY_DAY_NOTICE,
  FERRY_LEG_CAVEAT,
  FERRY_LEG_LABEL,
  UNVERIFIED_DAY_NOTICE,
  UNVERIFIED_LEG_LINE,
} from "../lib/traveler-view";
import {
  createTripViaApi,
  expect,
  generateViaApi,
  openGeneratedTrip,
  reshapeTrip,
  routeLegs,
  setReview,
  signUpViaApi,
  test,
} from "./support/app";

// E. How an itinerary is presented.
//
// The first group reads a REAL generated plan as the offline backend produced and validated it. The second
// group needs states that the backend's small fixed pool cannot produce (a ferry, an unverified leg, a
// blocked plan, ...): there the real response is fetched and only the fields the state is about are
// changed (support/app.ts `reshapeTrip`), so the page still receives a backend-shaped document.

const LONG_NAME =
  "The Extraordinarily Long Fixture Museum of Maritime History, Decorative Arts and Regional Archaeology (East Wing)";
const NON_LATIN_NAME = "フィクスチャ国立博物館 Fixture National Museum";

function dayCard(page: import("@playwright/test").Page, day: number) {
  // a day's heading and everything up to the next day's heading share one card
  return page.locator("div").filter({ has: page.getByRole("heading", { level: 3, name: new RegExp(`^Day ${day} · `) }) }).last();
}

async function openReshaped(
  page: import("@playwright/test").Page,
  change: Parameters<typeof reshapeTrip>[2],
): Promise<string> {
  await signUpViaApi(page);
  const tripId = await createTripViaApi(page);
  await generateViaApi(page, tripId);
  await reshapeTrip(page, tripId, change);
  await page.goto(`/?trip=${tripId}`);
  await expect(page.getByRole("heading", { name: "Your itinerary, day by day" })).toBeVisible({ timeout: 20_000 });
  return tripId;
}

// -- a real generated plan ---------------------------------------------------------------------------

test.describe("a real generated itinerary", () => {
  test("shows a card per day with its stops in visit order", async ({ page }) => {
    await openGeneratedTrip(page);

    await expect(page.getByRole("heading", { level: 3, name: /^Day \d+ · / })).toHaveText([
      "Day 1 · 2027-03-01",
      "Day 2 · 2027-03-02",
      "Day 3 · 2027-03-03",
    ]);
    await expect(page.getByText("9 scheduled places")).toBeVisible();
    for (const day of [1, 2, 3]) {
      await expect(dayCard(page, day).getByTestId("scheduled-stop")).toHaveCount(3);
    }
    // every scheduled place appears exactly once across the trip
    const names = await page
      .getByTestId("scheduled-stop")
      .evaluateAll((stops) => stops.map((stop) => stop.getAttribute("data-stop-name") ?? ""));
    expect(names).toHaveLength(9);
    expect(new Set(names).size).toBe(9);
    // nothing the providers did not supply is claimed
    await expect(page.getByText("Places come from map data. Ratings, prices and opening hours are not included.")).toBeVisible();
    await expect(page.getByText(/\$\d|★|rating:|open until/i)).toHaveCount(0);
  });

  test("marks the must-visit place and the stops that match an interest", async ({ page }) => {
    await openGeneratedTrip(page);
    const mustVisit = page.getByRole("listitem").filter({ hasText: "Fixture Cathedral (attraction)" });
    await expect(mustVisit).toContainText("One of your must-visit places.");
    // and only that one
    await expect(page.getByText("One of your must-visit places.")).toHaveCount(1);
    await expect(page.getByText("Matches your interests.").first()).toBeVisible();
  });

  test("says which requested interest is not covered, above the itinerary", async ({ page }) => {
    await openGeneratedTrip(page);
    const notice = page.getByRole("status", { name: "Not everything you asked for is covered" });
    await expect(notice).toBeVisible();
    await expect(notice).toContainText('Nothing in this itinerary covers "art".');
    await expect(notice).toContainText("That does not mean there are none in this destination.");
    // the covered interest is not listed as missing ...
    await expect(notice).not.toContainText('Nothing in this itinerary covers "food"');
    // ... but food met only by nearby suggestions is not called a food experience (Phase 2)
    await expect(notice).toContainText(
      '"food" is covered only by restaurant suggestions near your stops. No food experience, such as a food market, is scheduled.',
    );
    // it comes before the first day, not buried under it
    const noticeBox = await notice.boundingBox();
    const firstDayBox = await page.getByRole("heading", { level: 3, name: /^Day 1 · / }).boundingBox();
    expect(noticeBox && firstDayBox && noticeBox.y + noticeBox.height <= firstDayBox.y).toBeTruthy();
  });

  test("raises a material review banner that is announced, and says travel was not checked", async ({ page }) => {
    await openGeneratedTrip(page);
    const banner = page.getByRole("alert").filter({ hasText: "Review needed before you follow this" });
    await expect(banner).toBeVisible();
    await expect(banner).toContainText("Travel between some stops could not be fully checked.");
    await expect(page.getByText("Travel times between stops are not available for this trip.")).toBeVisible();
    // no leg has a route, so no movement row pretends otherwise
    await expect(page.getByTestId("movement-row")).toHaveCount(0);
    await expect(page.getByText(/^Walk · |^Drive · /)).toHaveCount(0);
    await expect(page.getByText("Checks passed")).toHaveCount(0);
  });

  test("shows a long place name and a non-Latin place name in full", async ({ page }) => {
    await openGeneratedTrip(page);
    await expect(page.getByText(`${LONG_NAME} (attraction)`)).toBeVisible();
    await expect(page.getByText(`${NON_LATIN_NAME} (attraction)`)).toBeVisible();
    // the characters arrive intact (no replacement characters, no escaped entities)
    const shown = await page.getByText(`${NON_LATIN_NAME} (attraction)`).innerText();
    expect(shown).toContain("フィクスチャ国立博物館");
    expect(shown).not.toMatch(/�|&#|\\u/);
  });

  test("draws a map per day with a numbered marker for every stop", async ({ page, watch }) => {
    await openGeneratedTrip(page);

    const maps = page.locator(".leaflet-container");
    await expect(maps).toHaveCount(3);
    for (let index = 0; index < 3; index += 1) {
      const map = maps.nth(index);
      await map.scrollIntoViewIfNeeded();
      await expect(map.locator(".leaflet-marker-icon")).toHaveCount(3);
      await expect(map.locator(".leaflet-marker-icon")).toHaveText(["1", "2", "3"]);
      // tiles were requested from OpenStreetMap's tile hosts and drawn
      await expect(map.locator("img.leaflet-tile").first()).toBeVisible();
      const box = await map.boundingBox();
      expect(box && box.width > 200 && box.height > 100).toBeTruthy();
      // the required attribution is on the map
      await expect(map.getByRole("link", { name: "OpenStreetMap" })).toHaveAttribute("href", "https://www.openstreetmap.org/copyright");
    }
    expect(watch.tileRequests.length).toBeGreaterThan(0);
    expect(new Set(watch.tileRequests.map((host) => host.replace(/^[abc]\./, "")))).toEqual(new Set(["tile.openstreetmap.org"]));
    // the map never implies a route that was not provided
    await expect(page.getByText("Route line not available for this day")).toHaveCount(3);
    await expect(page.locator(".leaflet-overlay-pane path")).toHaveCount(0);
  });

  test("the map is not the only way to learn where the stops are", async ({ page }) => {
    await openGeneratedTrip(page);
    // every stop is in the ordered list with its number, and has plain links to open its location
    await expect(page.getByRole("link", { name: "Open Fixture Cathedral in OpenStreetMap" })).toHaveAttribute(
      "href",
      /^https:\/\/www\.openstreetmap\.org\/\?mlat=38\.7098&mlon=-9\.133/,
    );
    await expect(page.getByRole("link", { name: "Open Fixture Cathedral in Google Maps" })).toHaveAttribute(
      "href",
      /^https:\/\/www\.google\.com\/maps\/search\/\?api=1&query=38\.7098,-9\.133/,
    );
    await expect(page.getByText("Numbers show the visit order.").first()).toBeVisible();
    await expect(page.getByText("Map links open the scheduled place coordinates only.")).toBeVisible();
  });

  // Phase 3: the map tile server is down. Two kinds of outage: it answers with an error, or the request
  // fails at the network level. Either way the itinerary is still readable without the map, and the page
  // throws nothing.
  for (const outage of ["answers 503", "is unreachable"] as const) {
    test(`with the map tile server down (${outage}) the itinerary stays usable`, async ({ page, watch }) => {
      if (outage === "is unreachable") watch.allowPageErrors(); // the failed tile requests are the point
      // registered after the fixture's stub, so it answers the tile requests instead
      await page.route(/^https:\/\/[abc]\.tile\.openstreetmap\.org\//, (route) =>
        outage === "answers 503" ? route.fulfill({ status: 503, body: "" }) : route.abort("failed"),
      );
      await openGeneratedTrip(page);

      const map = page.locator(".leaflet-container").first();
      await map.scrollIntoViewIfNeeded();
      await expect(map).toBeVisible(); // the map area is still laid out
      // every stop can still be found without a single tile
      await expect(page.getByText("Numbers show the visit order.").first()).toBeVisible();
      await expect(page.getByRole("link", { name: "Open Fixture Cathedral in OpenStreetMap" })).toBeVisible();
      await expect(page.getByRole("heading", { level: 3, name: /^Day 1 · / })).toBeVisible();
      // and the map still answers the keyboard and its controls
      await map.getByRole("button", { name: "Zoom in" }).click();
      await page.waitForTimeout(500);
      expect(watch.pageErrors, "uncaught errors in the page").toEqual([]);
    });
  }

  test("lists nearby food as ideas, not as bookings or recommendations", async ({ page }) => {
    await openGeneratedTrip(page);
    await expect(page.getByText("Nearby food ideas").first()).toBeVisible();
    await expect(page.getByText("These are not reservations, ratings or recommendations.").first()).toBeVisible();
    await expect(page.getByText("Fixture Corner Bistro (restaurant)").first()).toBeVisible();
  });
});

// -- marking a place to keep (Final Batch 2) ----------------------------------------------------------

const MARK_KEPT_BUTTON = /^Keep this place: .*\. While a place is kept, no requested change is applied$/;

async function firstScheduledStop(page: import("@playwright/test").Page, tripId: string) {
  const response = await page.request.get(`/api/trips/${tripId}`);
  expect(response.ok()).toBe(true);
  const plans = (await response.json()).data.planning_state.experience_plan.daily_plans;
  const stop = plans.flatMap((day: { experiences: { experience_id: string; name: string }[] }) => day.experiences)[0];
  return stop as { experience_id: string; name: string };
}

test.describe("keeping a place", () => {
  test("no keep control is offered where requested changes cannot be applied", async ({ page }) => {
    // The offline backend runs the release configuration: saved requests are not applied.
    await openGeneratedTrip(page);
    await expect(page.getByTestId("scheduled-stop")).toHaveCount(9);
    await expect(page.getByRole("button", { name: MARK_KEPT_BUTTON })).toHaveCount(0);
    await expect(page.getByRole("button", { name: /Keep this place|future regeneration/ })).toHaveCount(0);
    await expect(page.getByText("Keep this place")).toHaveCount(0);
  });

  test("a keep that is already stored is still shown and can be removed", async ({ page }) => {
    await signUpViaApi(page);
    const tripId = await createTripViaApi(page);
    await generateViaApi(page, tripId);
    const stop = await firstScheduledStop(page, tripId);
    const locked = await page.request.post(`/api/trips/${tripId}/locks`, {
      data: { locked_item_type: "experience", locked_item_id: stop.experience_id, reason: "user_requested_keep" },
    });
    expect(locked.ok()).toBe(true);

    await page.goto(`/?trip=${tripId}`);
    await expect(page.getByRole("heading", { name: "Your itinerary, day by day" })).toBeVisible({ timeout: 20_000 });
    const card = page.getByTestId("scheduled-stop").filter({ has: page.getByText(stop.name, { exact: false }) }).first();
    await expect(card.getByText("Kept", { exact: true })).toBeVisible();
    const remove = card.getByRole("button", { name: `Remove keep marker for ${stop.name}` });
    await remove.click();
    await expect(card.getByText("Keep marker removed.")).toBeVisible();
    await expect(card.getByText("Kept", { exact: true })).toHaveCount(0);
    // and, applying still being unavailable, no new keep is offered in its place
    await expect(card.getByRole("button", { name: MARK_KEPT_BUTTON })).toHaveCount(0);
  });

  test("where changes can be applied, the control says what a keep does", async ({ page }) => {
    await signUpViaApi(page);
    const tripId = await createTripViaApi(page);
    await generateViaApi(page, tripId);
    await page.route(`**/api/trips/${tripId}/regeneration-readiness`, async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      if (response.ok()) {
        body.data.regeneration_readiness.change_requests_applicable = true;
        body.data.regeneration_readiness.change_requests_unavailable_reason = null;
      }
      await route.fulfill({ response, json: body });
    });
    await page.goto(`/?trip=${tripId}`);
    await expect(page.getByRole("heading", { name: "Your itinerary, day by day" })).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole("button", { name: MARK_KEPT_BUTTON })).toHaveCount(9);
    await expect(page.getByRole("button", { name: /future regeneration/ })).toHaveCount(0);
  });
});

test.describe("development-only actions in Developer view", () => {
  const REFRESH = { name: "Refresh AI promotion report" };

  async function openDeveloperView(page: import("@playwright/test").Page, refreshAvailable?: boolean) {
    await signUpViaApi(page);
    const tripId = await createTripViaApi(page);
    await generateViaApi(page, tripId);
    // The offline backend runs no AI discovery, and the promotion controls are shown only for a review
    // that has candidate data: mark the real review as having some (still no item). The availability
    // flag is the backend's own unless a test sets what a production backend answers (which also
    // serves no such route).
    await page.route(`**/api/trips/${tripId}/ai-candidate-review`, async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      if (response.ok()) {
        body.data.ai_candidate_review_report.status = "success";
        if (refreshAvailable !== undefined) body.data.promotion_refresh_available = refreshAvailable;
      }
      await route.fulfill({ response, json: body });
    });
    await page.goto(`/?trip=${tripId}`);
    await expect(page.getByRole("heading", { name: "Your itinerary, day by day" })).toBeVisible({ timeout: 20_000 });
    await page.getByRole("button", { name: "Developer view" }).click();
    await expect(page.getByRole("heading", { name: "AI candidate review" })).toBeVisible();
  }

  test("the promotion refresh is offered only where the backend has it", async ({ page }) => {
    await openDeveloperView(page); // the offline backend runs as development
    await expect(page.getByRole("button", REFRESH)).toBeVisible();
    await expect(page.getByText(/Development only\. Refreshing only recomputes/)).toBeVisible();
  });

  test("where the refresh does not exist, no button is offered", async ({ page }) => {
    await openDeveloperView(page, false);
    await expect(page.getByRole("button", REFRESH)).toHaveCount(0);
    await expect(page.getByText("The report above is the one recorded when this itinerary was built.")).toBeVisible();
  });
});

// -- states made from a real plan --------------------------------------------------------------------

test.describe("presentation states", () => {
  test("verified legs show their mode, time and distance between the stops", async ({ page }) => {
    await openReshaped(page, {
      planningState: (state) => routeLegs(state, (_leg, index) => ({ routed: true, mode: index % 2 ? "drive" : "walk", seconds: 720, meters: 900 })),
    });

    const rows = page.getByTestId("movement-row");
    await expect(rows).toHaveCount(6); // two legs on each of three days
    await expect(rows.first()).toHaveText("Walk · 12 min · 0.9 km");
    // a driven leg is a "vehicle transfer": the app names no vehicle, fare or service
    await expect(rows.nth(1)).toHaveText("Vehicle transfer · 12 min · 0.9 km");
    await expect(page.getByText("Travel times between stops are not available for this trip.")).toHaveCount(0);
    await expect(page.getByText(UNVERIFIED_LEG_LINE)).toHaveCount(0);
  });

  test("a leg the provider flagged as a ferry is labelled as one, never as a walk", async ({ page }) => {
    await openReshaped(page, {
      planningState: (state) => routeLegs(state, (_leg, index) => ({ routed: true, mode: "walk", seconds: 1500, meters: 4200, ferry: index === 0 })),
    });

    const ferry = page.getByTestId("movement-row").first();
    await expect(ferry).toContainText(FERRY_LEG_LABEL);
    await expect(ferry).toContainText(FERRY_LEG_CAVEAT);
    await expect(ferry).toHaveAttribute("data-attention", "true");
    await expect(ferry).not.toHaveText(/^Walk/);
    // the day says so too, and only that day
    await expect(dayCard(page, 1)).toContainText(FERRY_DAY_NOTICE);
    await expect(page.getByText(FERRY_DAY_NOTICE)).toHaveCount(1);
    // no timetable, fare or operator is invented
    await expect(page.getByText(/departs|timetable|fare \$|operator:/i)).toHaveCount(0);
    // the other legs are ordinary
    await expect(page.getByTestId("movement-row").nth(1)).toHaveText("Walk · 25 min · 4.2 km");
  });

  test("a ferry is never inferred when the provider said nothing", async ({ page }) => {
    await openReshaped(page, {
      planningState: (state) => routeLegs(state, () => ({ routed: true, mode: "walk", seconds: 600, meters: 700, ferry: null })),
    });
    await expect(page.getByText(FERRY_LEG_LABEL)).toHaveCount(0);
    await expect(page.getByText(FERRY_DAY_NOTICE)).toHaveCount(0);
    await expect(page.getByTestId("movement-row").first()).toHaveText("Walk · 10 min · 0.7 km");
  });

  test("a leg with no verified route gets a row that states no mode, time or distance", async ({ page }) => {
    await openReshaped(page, {
      // every leg is routed except the first: a specific, visible gap
      planningState: (state) => {
        routeLegs(state, (_leg, index) => ({ routed: index !== 0, mode: "walk", seconds: 600, meters: 700 }));
        state.route_feasibility_report.status = "partial";
      },
    });

    const gap = dayCard(page, 1).getByText(UNVERIFIED_LEG_LINE);
    await expect(gap).toBeVisible();
    await expect(page.getByText(UNVERIFIED_LEG_LINE)).toHaveCount(1);
    await expect(dayCard(page, 1)).toContainText(UNVERIFIED_DAY_NOTICE);
    await expect(dayCard(page, 2)).not.toContainText(UNVERIFIED_DAY_NOTICE);
    // the gap row carries no figures
    expect(await gap.innerText()).not.toMatch(/\d+ ?(min|km|m)\b|Walk|Drive/);
    await expect(page.getByText("Travel times are shown where available. Some legs between stops could not be estimated.")).toBeVisible();
  });

  test("a plan with only informational findings is a draft, not an alarm", async ({ page }) => {
    await openReshaped(page, {
      validation: (report) => {
        setReview(report, {
          readiness: "needs_review",
          reviewCodes: ["FLIGHT_INVENTORY", "ACCOMMODATION_INVENTORY"],
          classification: { FLIGHT_INVENTORY: "informational", ACCOMMODATION_INVENTORY: "informational" },
        });
        report.interest_coverage = [
          { interest: "food", requested_as: ["food"], status: "covered" },
          { interest: "art", requested_as: ["art"], status: "covered" },
        ];
      },
    });

    const banner = page.getByRole("status").filter({ hasText: "Planning draft — some details not checked" });
    await expect(banner).toBeVisible();
    await expect(banner).toContainText("No travel or coverage problem was flagged for this schedule");
    await expect(page.getByText("Review needed before you follow this")).toHaveCount(0);
    await expect(page.getByRole("status", { name: "Not everything you asked for is covered" })).toHaveCount(0);
  });

  test("coverage that is unknown is never described as fine", async ({ page }) => {
    await openReshaped(page, {
      validation: (report) => {
        setReview(report, {
          readiness: "needs_review",
          reviewCodes: ["FLIGHT_INVENTORY"],
          classification: { FLIGHT_INVENTORY: "informational" },
        });
        report.interest_coverage = null; // an older report: nothing is known about coverage
      },
    });

    const banner = page.getByRole("status").filter({ hasText: "Planning draft — some details not checked" });
    await expect(banner).toContainText("Whether it covers everything you asked for could not be confirmed");
    await expect(banner).not.toContainText("No travel or coverage problem");
  });

  test("a report without a classification is treated as material", async ({ page }) => {
    await openReshaped(page, {
      validation: (report) => {
        setReview(report, { readiness: "needs_review", reviewCodes: ["SOME_FUTURE_CODE"] });
        delete report.review_code_classification;
      },
    });
    await expect(page.getByRole("alert").filter({ hasText: "Review needed before you follow this" })).toBeVisible();
  });

  test("a plan that passed its checks says so, with a reminder to confirm the details", async ({ page }) => {
    await openReshaped(page, {
      readiness: "ready",
      planningState: (state) => routeLegs(state, () => ({ routed: true })),
      validation: (report) => {
        setReview(report, { readiness: "ready" });
        report.interest_coverage = [{ interest: "food", requested_as: ["food"], status: "covered" }];
      },
    });
    const banner = page.getByRole("status").filter({ hasText: "Checks passed" });
    await expect(banner).toBeVisible();
    await expect(banner).toContainText("Still confirm opening hours, prices and bookings yourself before you travel.");
    await expect(page.getByRole("alert").filter({ hasText: /Review needed|Not ready/ })).toHaveCount(0);
  });

  test("a blocked plan is announced as not ready to use", async ({ page }) => {
    await openReshaped(page, {
      readiness: "blocked",
      validation: (report) =>
        setReview(report, {
          readiness: "blocked",
          blockingCodes: ["INSUFFICIENT_VERIFIED_INVENTORY"],
          classification: { INSUFFICIENT_VERIFIED_INVENTORY: "material" },
        }),
    });
    const banner = page.getByRole("alert").filter({ hasText: "Not ready to use" });
    await expect(banner).toBeVisible();
    await expect(banner).toContainText("This itinerary is incomplete and should not be relied on yet.");
    await expect(page.getByText("Checks passed")).toHaveCount(0);
    await expect(page.getByText(/INSUFFICIENT_VERIFIED_INVENTORY/)).toHaveCount(0); // no code in Traveler view
    // P1C-2: nothing on the page calls a blocked plan ready -- not even the note about the finished job
    await expect(page.getByText(/itinerary is ready|Itinerary ready/i)).toHaveCount(0);
    await expect(page.getByText("Itinerary generation finished")).toBeVisible();
  });

  test("a long travel day is flagged on that day's card", async ({ page }) => {
    const sentence = "Day 2: Getting between this day's stops takes far longer than the rest of the trip. Consider moving a stop.";
    await openReshaped(page, {
      planningState: (state) => routeLegs(state, () => ({ routed: true, mode: "drive", seconds: 4200, meters: 31000 })),
      validation: (report) =>
        setReview(report, {
          readiness: "needs_review",
          reviewCodes: ["LONG_TRAVEL_DAY"],
          classification: { LONG_TRAVEL_DAY: "material" },
          warnings: [{ category: "long_travel_day", message: sentence, day: 2 }],
        }),
    });

    await expect(page.getByRole("alert").filter({ hasText: "Review needed before you follow this" })).toBeVisible();
    const day2 = dayCard(page, 2);
    await expect(day2).toContainText("Getting between this day's stops takes far longer than the rest of the trip.");
    await expect(dayCard(page, 1)).not.toContainText("takes far longer");
    await expect(dayCard(page, 3)).not.toContainText("takes far longer");
    await expect(page.getByTestId("movement-row").first()).toHaveText("Vehicle transfer · 1 h 10 min · 31.0 km");
  });

  test("geographic dispersion is flagged on that day's card", async ({ page }) => {
    await openReshaped(page, {
      planningState: (state) => routeLegs(state, () => ({ routed: true })),
      validation: (report) =>
        setReview(report, {
          readiness: "needs_review",
          reviewCodes: ["GEOGRAPHIC_DISPERSION"],
          classification: { GEOGRAPHIC_DISPERSION: "material" },
          warnings: [{ category: "geographic_dispersion", message: `Day 3: ${GEOGRAPHIC_SPREAD_SENTENCE}`, day: 3 }],
        }),
    });

    await expect(dayCard(page, 3)).toContainText(GEOGRAPHIC_SPREAD_SENTENCE);
    // on that day's card and nowhere else among the days (it is also listed once under the limitations)
    await expect(dayCard(page, 1)).not.toContainText(GEOGRAPHIC_SPREAD_SENTENCE);
    await expect(dayCard(page, 2)).not.toContainText(GEOGRAPHIC_SPREAD_SENTENCE);
    await expect(page.getByRole("alert").filter({ hasText: "Review needed before you follow this" })).toBeVisible();
  });

  test("a finding worded for developers is never shown to a traveler as written", async ({ page }) => {
    await openReshaped(page, {
      validation: (report) =>
        setReview(report, {
          readiness: "needs_review",
          reviewCodes: ["GEOGRAPHIC_DISPERSION"],
          classification: { GEOGRAPHIC_DISPERSION: "material" },
          warnings: [
            {
              category: "geographic_dispersion",
              message: "Day 1: geoapify_routing provider adapter returned day_spread_km=14.2 (threshold GEOGRAPHIC_SPREAD_THRESHOLD_KM)",
              day: 1,
            },
          ],
        }),
    });
    await expect(page.getByText(/geoapify|adapter|day_spread_km|THRESHOLD/)).toHaveCount(0);
    // the finding is still shown, in the app's own words
    await expect(dayCard(page, 1)).toContainText(GEOGRAPHIC_SPREAD_SENTENCE);
  });

  test("an interest that had supply but was not scheduled is reported differently from one with none", async ({ page }) => {
    await openReshaped(page, {
      validation: (report) => {
        report.interest_coverage = [
          { interest: "food", requested_as: ["food"], status: "covered" },
          { interest: "art", requested_as: ["art"], status: "not_covered_supply_available" },
          { interest: "hiking", requested_as: ["hiking"], status: "not_covered_no_supply" },
        ];
      },
    });
    const items = page.getByRole("status", { name: "Not everything you asked for is covered" }).getByRole("listitem");
    await expect(items).toHaveCount(2);
    const texts = await items.allInnerTexts();
    expect(texts.find((text) => text.includes('"art"'))).not.toEqual(texts.find((text) => text.includes('"hiking"'))?.replace("hiking", "art"));
    expect(texts.join(" ")).not.toContain('"food"');
  });
});

// P1C-3 (fixed): zooming a day map and then removing it before the zoom animation ended (switching view,
// opening another trip, deleting the trip) threw an uncaught TypeError in the page: "Cannot read properties
// of undefined (reading '_leaflet_pos')". Leaflet's end-of-zoom timer ran after the map was removed.
test("P1C-3: removing a map while it is zooming throws no error in the page", async ({ page, watch }) => {
  watch.allowPageErrors(); // asserted explicitly below instead
  await openGeneratedTrip(page);
  const map = page.locator(".leaflet-container").first();
  await map.scrollIntoViewIfNeeded();
  await expect(map.locator("img.leaflet-tile").first()).toBeVisible();

  await map.getByRole("button", { name: "Zoom in" }).click();
  await page.getByRole("button", { name: "Developer view" }).click(); // the maps are replaced mid-animation
  await page.waitForTimeout(1200);

  expect(watch.pageErrors).toEqual([]);
});

/** How many Leaflet maps the page holds, and whether any container holds more than one. */
async function mapCensus(page: import("@playwright/test").Page) {
  return page.evaluate(() => {
    const maps = [...document.querySelectorAll(".leaflet-container")];
    return {
      maps: maps.length,
      // a container React owns holds exactly one map element; a leftover would be a second child
      crowded: maps.filter((map) => map.parentElement!.children.length !== 1).length,
      nested: maps.filter((map) => map.parentElement!.closest(".leaflet-container")).length,
      panes: document.querySelectorAll(".leaflet-map-pane").length,
    };
  });
}

test("P1C-3: rapid zooming, view switching and remounting leave no error and no leftover map", async ({ page, watch }) => {
  watch.allowPageErrors(); // asserted explicitly below instead
  await openGeneratedTrip(page);
  const maps = page.locator(".leaflet-container");
  const toggle = page.getByRole("group", { name: "Result view mode" });
  await expect(maps).toHaveCount(3);
  await maps.first().scrollIntoViewIfNeeded();
  await expect(maps.first().locator("img.leaflet-tile").first()).toBeVisible();

  // rapid zoom on one map: clicks faster than one animation
  for (const name of ["Zoom in", "Zoom in", "Zoom out", "Zoom in", "Zoom out"]) {
    await maps.first().getByRole("button", { name }).click({ delay: 0 });
  }
  // then every map zooming at once, and the view switched in the middle of it, several times over
  for (let round = 0; round < 4; round += 1) {
    for (let index = 0; index < 3; index += 1) {
      await maps.nth(index).getByRole("button", { name: round % 2 ? "Zoom out" : "Zoom in" }).click();
    }
    await toggle.getByRole("button", { name: "Developer view" }).click();
    await expect(toggle.getByRole("button", { name: "Developer view" })).toHaveAttribute("aria-pressed", "true");
    await toggle.getByRole("button", { name: "Traveler view" }).click();
    await expect(maps).toHaveCount(3);
    await expect(maps.first().locator(".leaflet-marker-icon").first()).toBeVisible();
  }
  // a zoom still running when the whole trip is closed and opened again
  await maps.nth(1).getByRole("button", { name: "Zoom in" }).click();
  await page.getByRole("button", { name: /Fixtureville, Testland/ }).first().click();
  await expect(maps).toHaveCount(3);
  await maps.nth(2).scrollIntoViewIfNeeded();
  await expect(maps.nth(2).locator(".leaflet-marker-icon")).toHaveCount(3);
  await page.waitForTimeout(1500); // longer than the zoom animation and the removal backstop

  expect(watch.pageErrors).toEqual([]);
  expect(await mapCensus(page)).toEqual({ maps: 3, crowded: 0, nested: 0, panes: 3 });
  // the remounted maps are whole and still work
  for (let index = 0; index < 3; index += 1) {
    await expect(maps.nth(index).locator(".leaflet-marker-icon")).toHaveCount(3);
    await expect(maps.nth(index).locator(".leaflet-control-zoom")).toHaveCount(1);
  }
  await maps.first().scrollIntoViewIfNeeded();
  await maps.first().getByRole("button", { name: "Zoom out" }).click();
  await expect(maps.first().locator(".leaflet-zoom-anim")).toHaveCount(0);

  // and when the trip is deleted mid-zoom, every map is gone -- none is left behind, attached or not
  await maps.first().getByRole("button", { name: "Zoom in" }).click();
  await page.getByRole("button", { name: /^Delete trip: / }).click();
  await page.getByRole("group", { name: /^Confirm deleting / }).getByRole("button", { name: "Delete trip" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Trip deleted." })).toBeVisible();
  await page.waitForTimeout(1500);
  expect(await mapCensus(page)).toEqual({ maps: 0, crowded: 0, nested: 0, panes: 0 });
  expect(watch.pageErrors).toEqual([]);
});

test("Developer view shows the technical detail and is clearly separate", async ({ page }) => {
  await openGeneratedTrip(page);
  const toggle = page.getByRole("group", { name: "Result view mode" });
  await expect(toggle.getByRole("button", { name: "Traveler view" })).toHaveAttribute("aria-pressed", "true");

  await toggle.getByRole("button", { name: "Developer view" }).click();

  await expect(toggle.getByRole("button", { name: "Developer view" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("heading", { name: "Day-wise experiences" })).toBeVisible();
  // the choice is remembered for this browser only
  await page.reload();
  await expect(page.getByRole("heading", { name: "Day-wise experiences" })).toBeVisible({ timeout: 20_000 });
});
