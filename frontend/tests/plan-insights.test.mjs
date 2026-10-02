// Section 203C.2B frontend regression tests. Run with `npm test`
// (Node's built-in test runner; no test framework dependency).
//
// Two kinds of check:
//   1. unit tests of the pure view model in lib/plan-insights.ts;
//   2. structure tests of app/page.tsx: which result sections each view
//      renders and in what order -- so a later edit cannot silently move
//      flight/lodging/provider diagnostics into Traveler view or drop them
//      from Developer view.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  DEVELOPER_ONLY_SECTIONS,
  INSUFFICIENT_VERIFIED_INVENTORY,
  TRAVELER_SECTION_ORDER,
  UNDERFILLED_PLAN,
  inventoryPanelRows,
  travelerInventoryNotice,
  usagePanelRows,
} from "../lib/plan-insights.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const page = readFileSync(join(root, "app", "page.tsx"), "utf8");
const panels = readFileSync(join(root, "app", "PlanInsightPanels.tsx"), "utf8");

// The result area is one ternary: `mode === "developer" ? (<>DEV</>) : (<>TRAVELER</>)`.
const developerStart = page.indexOf('{mode === "developer" ? (');
const travelerStart = page.indexOf("{/* ---- Traveler view", developerStart);
assert.ok(developerStart > 0 && travelerStart > developerStart, "result view blocks not found in page.tsx");
const developerBlock = page.slice(developerStart, travelerStart);
const travelerBlock = page.slice(travelerStart, page.indexOf("</main>", travelerStart));

const inventory = {
  status: "insufficient",
  trip_days: 3,
  pace: "balanced",
  target_stops: 9,
  minimum_useful: 8,
  healthy_buffer: 21,
  viable_candidates: 2,
  expansion_attempted: true,
  viable_before_expansion: 2,
  message: "Not enough verified places were found.",
};

// -- pure view model ---------------------------------------------------------------

test("insufficient inventory is explained to the traveler with the backend's own numbers", () => {
  const notice = travelerInventoryNotice(
    { blocking_codes: [INSUFFICIENT_VERIFIED_INVENTORY], review_codes: [] },
    inventory,
  );
  assert.equal(notice.tone, "blocked");
  assert.equal(notice.code, INSUFFICIENT_VERIFIED_INVENTORY);
  assert.match(notice.title, /Not enough verified places/);
  assert.match(notice.body, /2 verified places/);
  assert.match(notice.body, /3-day balanced trip needs at least 8/);
  assert.match(notice.body, /Nothing was added to fill the gaps/);
});

test("an underfilled plan is a review notice, and a useful plan has no notice", () => {
  const underfilled = travelerInventoryNotice(
    { blocking_codes: [], review_codes: [UNDERFILLED_PLAN] },
    { ...inventory, status: "healthy", viable_candidates: 24 },
  );
  assert.equal(underfilled.tone, "review");
  assert.equal(underfilled.code, UNDERFILLED_PLAN);

  assert.equal(travelerInventoryNotice({ blocking_codes: [], review_codes: [] }, inventory), null);
  // a plan stored before this section carries no codes and no report
  assert.equal(travelerInventoryNotice({}, null), null);
  assert.equal(travelerInventoryNotice(null, undefined), null);
});

test("developer panels restate the reports and stay empty without one", () => {
  const rows = Object.fromEntries(inventoryPanelRows(inventory).map((row) => [row.label, row.value]));
  assert.equal(rows["Status"], "insufficient");
  assert.equal(rows["Target stops (T)"], "9");
  assert.equal(rows["Minimum useful (R)"], "8");
  assert.equal(rows["Healthy buffer (H)"], "21");
  assert.equal(rows["Expansion round"], "tried (2 before)");
  assert.deepEqual(inventoryPanelRows(null), []);

  const usage = usagePanelRows({
    generation_id: "g1",
    budget: 100,
    credits_used: 37,
    credits_by_api: { routing: 6, places: 9, geocoding: 12, place_details: 10 },
    calls_by_api: { routing: 3, places: 6, geocoding: 12, place_details: 10 },
    refused_calls: 0,
  });
  assert.equal(usage[0].value, "37 of 100");
  assert.deepEqual(usage.slice(2).map((row) => row.label), ["geocoding", "place details", "places", "routing"]);
  assert.equal(usage.at(-1).value, "6 credit(s), 3 call(s)");
  assert.deepEqual(usagePanelRows(undefined), []);
});

// -- Traveler view ---------------------------------------------------------------------

test("Traveler view leads with the itinerary, in the documented order", () => {
  const positions = TRAVELER_SECTION_ORDER.map((id) =>
    id === "traveler-inventory-notice"
      ? travelerBlock.indexOf("<TravelerInventoryNotice")
      : travelerBlock.indexOf(`id="${id}"`),
  );
  positions.forEach((position, index) => assert.ok(position >= 0, `${TRAVELER_SECTION_ORDER[index]} is missing`));
  assert.deepEqual([...positions].sort((a, b) => a - b), positions, "Traveler sections are out of order");
  // the itinerary itself is inside the first content block
  const itinerary = travelerBlock.indexOf("{dayWiseItinerarySection}");
  assert.ok(itinerary > positions[1] && itinerary < positions[2]);
});

test("Traveler view does not foreground flight, bookable-lodging or provider diagnostics", () => {
  for (const section of DEVELOPER_ONLY_SECTIONS) {
    assert.ok(!travelerBlock.includes(`<${section}`), `${section} must not render in Traveler view`);
  }
  // flights and where-to-stay exist only as collapsed supporting detail, after the itinerary
  const supporting = travelerBlock.indexOf('id="supporting-detail"');
  for (const marker of ["<UserModeFlightSummary", "<TravelerWhereToStaySection"]) {
    const position = travelerBlock.indexOf(marker);
    assert.ok(position > supporting, `${marker} must stay under "Supporting detail"`);
    const disclosure = travelerBlock.lastIndexOf("<DisclosureSection", position);
    assert.ok(disclosure > supporting, `${marker} must be inside a DisclosureSection`);
  }
  assert.match(travelerBlock, /These are not bookable offers/);
});

test("material warnings stay visible to the traveler, outside any disclosure", () => {
  const supporting = travelerBlock.indexOf('id="supporting-detail"');
  for (const marker of ["<TravelerInventoryNotice", "<TravelerLimitationsSection"]) {
    const position = travelerBlock.indexOf(marker);
    assert.ok(position >= 0 && position < supporting, `${marker} must be shown before the disclosures`);
  }
  // the notice component itself is not a collapsible control
  assert.ok(!panels.slice(0, panels.indexOf("function RowsPanel")).includes("DisclosureSection"));
  assert.match(panels, /role="status"/);
});

// -- Developer view ----------------------------------------------------------------------

test("Developer view still exposes every diagnostic", () => {
  for (const section of DEVELOPER_ONLY_SECTIONS) {
    assert.ok(developerBlock.includes(`<${section}`), `${section} must render in Developer view`);
  }
  for (const section of [
    "ValidationSection",
    "RouteFeasibilitySection",
    "AICandidateReviewSection",
    "RegenerationReadinessSection",
    "BranchWorkspacePanel",
    "CandidatePoiSection",
  ]) {
    assert.ok(developerBlock.includes(`<${section}`), `${section} must render in Developer view`);
  }
});

test("both views show the same itinerary and feedback controls", () => {
  for (const block of [developerBlock, travelerBlock]) {
    assert.ok(block.includes("{dayWiseItinerarySection}"));
    assert.ok(block.includes("<FeedbackPanel"));
  }
});
