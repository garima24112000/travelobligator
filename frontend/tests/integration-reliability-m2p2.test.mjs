// M2 Phase 2 frontend regression tests. Run with `npm test`.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  travelerChangesUnavailable,
  travelerFlightAirportNotes,
  travelerFlightAvailabilityNotice,
} from "../lib/traveler-view.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const page = readFileSync(join(root, "app", "page.tsx"), "utf8");
const trust = readFileSync(join(root, "lib", "trust-dashboard.ts"), "utf8");

function componentSource(name) {
  const start = page.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `${name} not found in page.tsx`);
  const next = page.slice(start + 1).search(/\n(async )?function /);
  return page.slice(start, next === -1 ? undefined : start + 1 + next);
}

test("a paused generation switch is explained as temporary, in traveler words", () => {
  const paused = travelerChangesUnavailable({
    change_requests_applicable: false,
    change_requests_unavailable_reason: "generation_paused",
  });
  assert.equal(paused.title, "Applying changes is paused");
  assert.match(paused.detail, /saved/);
  assert.match(paused.detail, /try again later/);
  assert.doesNotMatch(`${paused.title} ${paused.detail}`, /GENERATION|switch|503|provider|regenerat/i);
});

test("a flight offer shows a price only in the currency the source stated", () => {
  const card = componentSource("TravelerFlightOfferCard");
  assert.match(card, /offer\.total_price_amount !== null && offer\.currency \?/);
  assert.match(card, /Price not provided by the flight source\./);
});

test("Developer inventory sections show the backend's reason", () => {
  assert.match(componentSource("FlightInventorySection"), /Reason: \{report\.availability_reason\}/);
  assert.match(componentSource("FlightInventorySection"), /report\.failure_kind/);
  assert.match(componentSource("AccommodationInventorySection"), /Reason: \{report\.availability_reason\}/);
});

test("local-file sources are never described as scraping", () => {
  assert.doesNotMatch(page, /"Rating from scraped\/manual source"/);
  assert.match(page, /"Rating from a manually supplied local file"/);
  assert.match(trust, /scraped_source: "Manually supplied local file"/);
});

test("an empty search by place name is never shown as 'no flights'", () => {
  const notice = travelerFlightAvailabilityNotice({
    status: "unavailable",
    offers: [],
    availability_reason: "location_unconfirmed",
  });
  assert.doesNotMatch(notice, /No flight offers were found for this trip's route/);
  assert.match(notice, /doesn't mean there are no flights/);
  const noAirport = travelerFlightAvailabilityNotice({
    status: "unavailable",
    offers: [],
    availability_reason: "no_searchable_airport",
  });
  assert.match(noAirport, /no airport could be found/);
  assert.doesNotMatch(`${notice} ${noAirport}`, /provider|configured|Kiwi|Geoapify/i);
});

test("a substituted airport is shown with where it is, and only from the backend's provenance", () => {
  const notes = travelerFlightAirportNotes({
    location_resolution: [
      { role: "origin", requested: "Jersey City, NJ", searched: "EWR", method: "nearby_airport",
        airport_name: "Newark Liberty International Airport", airport_code: "EWR", straight_line_km: 12.4,
        lookup_source: "ourairports", note: null, airport_type: "large_airport",
        commercial_service_evidence: "ourairports:scheduled_service=yes" },
      // an airport without commercial-service evidence is never presented as where flights depart
      { role: "origin", requested: "Elsewhere", searched: "NYS", method: "nearby_airport",
        airport_name: "Some Seaplane Base", airport_code: "NYS", straight_line_km: 6.5,
        lookup_source: "x", note: null, commercial_service_evidence: null },
      { role: "destination", requested: "Orlando, FL", searched: "Orlando", method: "place_name",
        airport_name: null, airport_code: null, straight_line_km: null, lookup_source: null, note: null },
    ],
  });
  assert.equal(notes.length, 1);
  assert.match(notes[0], /from Newark Liberty International Airport \(EWR\)/);
  assert.match(notes[0], /about 12\.4 km in a straight line/);
  assert.match(notes[0], /scheduled airline service/);
  assert.doesNotMatch(notes[0], /nearest/);
  assert.match(notes[0], /not in Jersey City, NJ itself/);
  assert.deepEqual(travelerFlightAirportNotes({ location_resolution: null }), []);
  assert.deepEqual(travelerFlightAirportNotes(undefined), []);
});

test("Traveler and Developer flight sections render the location provenance", () => {
  assert.match(componentSource("UserModeFlightSummary"), /travelerFlightAirportNotes\(report\)/);
  const dev = componentSource("FlightInventorySection");
  assert.match(dev, /report\?\.location_resolution/);
  assert.match(dev, /HTTP requests observed/);
});

test("Developer view states the route report's summary even when the per-day route context is present", () => {
  // The M2 Phase 2 replay failure: the summary was rendered only when the plan had NO per-day route
  // context, but the context is restated from the final route report, so a routed plan always has days.
  const section = componentSource("RouteFeasibilitySection");
  const summary = section.indexOf('data-testid="route-report-summary"');
  const days = section.indexOf("routeFeasibility.daily_route_feasibility.length === 0");
  assert.ok(summary >= 0, "the summary line is rendered");
  assert.ok(days >= 0 && summary < days, "the summary line does not depend on the per-day branch");
  assert.match(section, /\{reportSummary && \(/);
  // the "no results" fallback is never shown next to a real summary
  assert.match(section, /!reportSummary && \(/);
});
