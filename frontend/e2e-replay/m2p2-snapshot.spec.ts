import { readFileSync } from "node:fs";

import { expect, test, type Page, type Route } from "@playwright/test";

import { routeReportSummary, travelerFlightAirportNotes } from "../lib/traveler-view";
import type { FlightInventoryReport, RouteFeasibilityReport } from "../lib/types";

// M2 Phase 2: what ONE saved trip from the bounded live verification looks like in both views.
// The snapshot is the API responses the page loads (backend/scripts/verify_m2_phase2.py --ui-snapshot);
// every expectation below is computed from that snapshot, never typed in by hand.

type Snapshot = { trip_id: string; responses: Record<string, { status: number; body: unknown }> };

const SNAPSHOT_PATH = process.env.M2P2_UI_SNAPSHOT;
if (!SNAPSHOT_PATH) {
  throw new Error("Set M2P2_UI_SNAPSHOT to the file written by verify_m2_phase2.py --ui-snapshot.");
}
const snapshot = JSON.parse(readFileSync(SNAPSHOT_PATH, "utf8")) as Snapshot;
const tripId = snapshot.trip_id;
const state = (snapshot.responses[`/trips/${tripId}`]?.body as { data: { planning_state: Record<string, unknown> } })
  .data.planning_state;
const flight = state.flight_inventory_report as FlightInventoryReport;
const route = state.route_feasibility_report as RouteFeasibilityReport | null;
const accommodation = state.accommodation_inventory_report as { availability_reason: string | null; status: string };

const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==",
  "base64",
);

type Seen = { unrecorded: string[]; writes: string[]; outside: string[] };

async function replay(page: Page): Promise<Seen> {
  const seen: Seen = { unrecorded: [], writes: [], outside: [] };
  await page.route(/^https?:\/\/(?!127\.0\.0\.1[:/])/, async (route: Route) => {
    const url = new URL(route.request().url());
    if (url.hostname.endsWith("tile.openstreetmap.org")) {
      await route.fulfill({ status: 200, contentType: "image/png", body: PNG });
      return;
    }
    seen.outside.push(url.hostname);
    await route.abort();
  });
  await page.route("**/api/**", async (route: Route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/^\/api/, "");
    if (request.method() !== "GET") {
      seen.writes.push(`${request.method()} ${path}`);
      await route.abort();
      return;
    }
    const recorded = snapshot.responses[path];
    if (!recorded) {
      seen.unrecorded.push(path.replaceAll(tripId, "<trip>"));
      await route.fulfill({ status: 404, json: { success: false, data: null, message: "not recorded", errors: [] } });
      return;
    }
    await route.fulfill({ status: recorded.status, json: recorded.body });
  });
  return seen;
}

async function openTrip(page: Page): Promise<Seen> {
  const seen = await replay(page);
  await page.goto(`/?trip=${tripId}`);
  await expect(page.getByRole("heading", { name: "Your itinerary, day by day" })).toBeVisible({ timeout: 20_000 });
  return seen;
}

async function openSection(page: Page, id: string): Promise<void> {
  const button = page.locator(`section#${id} > h2 > button`);
  if ((await button.getAttribute("aria-expanded")) !== "true") await button.click();
}

test("Traveler view: usable Kiwi offers, the substituted airport, honest stay information", async ({ page }) => {
  const seen = await openTrip(page);
  expect(flight.offers.length, "the saved trip has flight offers").toBeGreaterThan(0);

  await openSection(page, "flights");
  const flights = page.locator("section#flights");
  for (const note of travelerFlightAirportNotes(flight)) await expect(flights.getByText(note)).toBeVisible();
  expect(travelerFlightAirportNotes(flight).length, "an airport was substituted").toBeGreaterThan(0);
  const first = flight.offers[0];
  await expect(flights.getByText(`${first.total_price_amount} ${first.currency}`).first()).toBeVisible();
  await expect(flights.getByText("Provider-supplied booking link -- not a booking confirmation").first()).toBeVisible();
  await expect(flights.getByText("Price not provided by the flight source.")).toHaveCount(0);

  await openSection(page, "where-to-stay");
  const stay = page.locator("section#where-to-stay");
  if (accommodation.status !== "success") {
    await expect(stay.getByText("Stay offers found for your dates.")).toHaveCount(0);
    await expect(
      stay.getByText(/These are not\s+live hotel offers|No lodging information is available for this trip yet\./),
    ).toBeVisible();
  }
  await expect(stay.getByText(/\bscrap(e|ed|ing)\b/i)).toHaveCount(0);

  expect(seen.unrecorded, "every request the page made was recorded").toEqual([]);
  expect(seen.writes, "the page wrote nothing").toEqual([]);
});

test("Developer view: reasons, airport evidence, request count, routes, booking links", async ({ page }) => {
  const seen = await openTrip(page);
  await page.getByRole("button", { name: "Developer view" }).click();
  await expect(page.getByRole("button", { name: "Developer view" })).toHaveAttribute("aria-pressed", "true");

  const flights = page.locator("#flight-inventory");
  await expect(flights.getByText(`Reason: ${flight.availability_reason}`)).toBeVisible();
  for (const entry of (flight.location_resolution ?? []).filter((e) => e.method === "nearby_airport")) {
    await expect(flights.getByText(`commercial-service evidence: ${entry.commercial_service_evidence}`).first()).toBeVisible();
    await expect(flights.getByText(new RegExp(`${entry.role}: "[^"]+" → ${entry.airport_code}`))).toBeVisible();
  }
  await expect(flights.getByText(`HTTP requests observed: ${flight.provider_http_requests}`)).toBeVisible();
  const link = flight.offers.find((offer) => offer.booking_url)?.booking_url;
  if (link) await expect(flights.getByText(link).first()).toBeVisible();

  // The exact line, in the Route feasibility section, whenever the saved trip has a route report --
  // also when the per-day route context is present (the M2 Phase 2 replay failure).
  const routeLine = routeReportSummary(route);
  if (route && route.status !== "not_connected") {
    expect(routeLine, "a route report yields a summary line").not.toBeNull();
    await expect(page.locator("#route-feasibility").getByTestId("route-report-summary")).toHaveText(routeLine!);
  }
  if (accommodation.availability_reason) {
    await expect(page.getByText(`Reason: ${accommodation.availability_reason}`).first()).toBeVisible();
  }

  expect(seen.unrecorded, "every request the page made was recorded").toEqual([]);
  expect(seen.writes, "the page wrote nothing").toEqual([]);
});
