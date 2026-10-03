// Section 2C (final Traveler limitations cleanup) regression tests. Run with
// `npm test` (Node's built-in test runner; no test framework dependency).
//
// "Important limitations" in Traveler view is built by `travelerFindings`
// (lib/display-labels.ts); these tests feed it the backend's real finding
// sentences and check what comes out, plus source-structure checks that the
// page uses it and that Developer view still lists every finding.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  hasDiagnosticWording,
  travelerFinding,
  travelerFindings,
} from "../lib/display-labels.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const page = readFileSync(join(root, "app", "page.tsx"), "utf8");
const travelerSections = readFileSync(join(root, "app", "TravelerSections.tsx"), "utf8");

function componentSource(source, name) {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `${name} not found`);
  const next = source.indexOf("\nfunction ", start + 1);
  const nextExport = source.indexOf("\nexport function ", start + 1);
  const ends = [next, nextExport].filter((index) => index > 0);
  return source.slice(start, ends.length ? Math.min(...ends) : undefined);
}

const finding = (severity, category, message) => ({
  severity,
  category,
  message,
  affected_section: "experience_plan",
  suggested_fix: null,
});

// The backend's own sentences (backend/app/services/plan_validator_service.py).
const FEASIBILITY_ALL =
  "5 of 5 scheduled leg(s) have a provider-backed route (via geoapify_routing) with a real distance and duration. Route-aware sequencing may be applied, unavailable, failed, or not connected depending on provider data and configuration, and opening-hours/timing feasibility is not evaluated here -- this reports uncertainty, it does not calculate a missing route, so this plan still needs review before it can be considered ready.";
const DIAGNOSTIC_SUGGESTIONS = [
  finding("suggestion", "feasibility", FEASIBILITY_ALL),
  finding(
    "suggestion",
    "route_aware_sequencing",
    "This itinerary keeps its suggested order; route-aware reordering was not applied to any day.",
  ),
  finding(
    "suggestion",
    "route_aware_sequencing",
    "Provider-backed route-aware sequencing was applied to 2 of 3 day(s) with two or more scheduled experiences; any remaining day(s) keep their suggested order. This only reports that a provider-backed reorder happened -- it makes no claim about how good the resulting order is.",
  ),
  finding(
    "suggestion",
    "movement_data",
    "Provider-backed movement data was available for route-aware sequencing on all 3 day(s) with two or more scheduled experiences.",
  ),
  finding(
    "suggestion",
    "route_geometry",
    "Provider-backed route geometry is available for all 5 scheduled leg(s) in this itinerary.",
  ),
  finding(
    "suggestion",
    "accommodation_inventory",
    "The accommodation inventory provider was checked but is not connected. Configured sources: scraped_local (local scraped HTML file). Restricted sources such as Booking.com, Expedia, Vrbo and Airbnb are never scraped.",
  ),
  finding(
    "suggestion",
    "flight_inventory",
    "The flight inventory provider was checked and returned no offers. Configured source: scraped_local HTML; Skyscanner, Expedia and Google Flights are not integrated.",
  ),
  finding(
    "suggestion",
    "hotel_ratings",
    "No hotel ratings provider is connected, so accommodation offers were not checked for provider-backed rating data.",
  ),
  finding("suggestion", "regeneration", "Pending feedback is available for deterministic regeneration."),
];

const WEATHER_WARNING = finding(
  "warning",
  "weather",
  "Provider-backed weather data is available for this trip, but the itinerary has not been adjusted around weather yet -- review outdoor/long-walk days manually. High precipitation probability (>=60%) forecast on: 2026-10-11.",
);

const FORBIDDEN = [
  /route-aware sequencing/i,
  /scheduled leg\(s\)/i,
  /route geometry/i,
  /provider data and configuration/i,
  /inventory provider/i,
  /scraped/i,
  /configured source/i,
  /Movement data was available/i,
  /Skyscanner|Expedia|Vrbo|Booking\.com|Airbnb|Google Flights/i,
  /geoapify|openstreetmap|osrm|scraped_local|kiwi/i,
  /\b[a-z]+_[a-z_]+\b/,
];

function assertNoDiagnostics(rows) {
  for (const row of rows) {
    for (const pattern of FORBIDDEN) {
      assert.doesNotMatch(row.message, pattern, `Traveler row leaks ${pattern}: "${row.message}"`);
    }
  }
}

// -- policy ---------------------------------------------------------------------------

test("diagnostic suggestions from the live smoke are not shown to a traveler", () => {
  assert.deepEqual(travelerFindings(DIAGNOSTIC_SUGGESTIONS), []);
  for (const issue of DIAGNOSTIC_SUGGESTIONS) {
    assert.equal(travelerFinding(issue), null, issue.category);
  }
});

test("routing and inventory diagnostics are hidden at warning severity too", () => {
  const warnings = [
    finding("warning", "route_aware_sequencing", "Route-aware sequencing could not be applied."),
    finding("warning", "route_geometry", "Provider-backed route geometry is available for 2 of 5 scheduled leg(s); the rest have no drawable path."),
    finding("warning", "hotel_ratings", "The hotel ratings provider request failed."),
    finding("warning", "provider_coverage_consistency", "provider_coverage.hotel_prices is 'not_connected' but an accommodation inventory provider returned offers."),
    finding("warning", "regeneration", "Active locks currently block feedback-driven regeneration."),
    finding("warning", "regeneration_state_consistency", "regeneration_readiness.can_regenerate (True) and plan_diff_preview.regeneration_available (False) disagree."),
    finding("warning", "accommodation_inventory", "The accommodation inventory provider failed."),
    finding("warning", "flight_inventory", "The flight inventory provider failed."),
  ];
  assert.deepEqual(travelerFindings(warnings), []);
});

test("an unknown suggestion or diagnostic-sounding warning defaults to hidden", () => {
  assert.equal(travelerFinding(finding("suggestion", "some_future_category", "A perfectly plain sentence.")), null);
  assert.equal(travelerFinding(finding("suggestion", "", "A perfectly plain sentence.")), null);
  // an unknown WARNING category is kept only when it is not diagnostic prose
  assert.equal(
    travelerFinding(finding("warning", "some_future_category", "The scheduler provider configuration was not checked.")),
    null,
  );
  assert.notEqual(
    travelerFinding(finding("warning", "some_future_category", "Day 2 ends late in the evening.")),
    null,
  );
  assert.equal(hasDiagnosticWording(FEASIBILITY_ALL), true);
  assert.equal(hasDiagnosticWording("Day 2 ends late in the evening."), false);
});

test("the weather warning still shows, in the backend's own words", () => {
  const rows = travelerFindings([WEATHER_WARNING, ...DIAGNOSTIC_SUGGESTIONS]);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].severity, "warning");
  assert.match(rows[0].message, /^Weather data is available for this trip, but the itinerary has not been adjusted around weather yet/);
  assert.match(rows[0].message, /High precipitation probability/);
  assertNoDiagnostics(rows);
});

test("critical findings and actionable warnings still show", () => {
  const issues = [
    finding("critical", "insufficient_verified_inventory", "Only 2 verified place(s) were found; a 3-day balanced trip needs at least 8."),
    finding("critical", "provider_coverage", "No experiences were scheduled because no places provider is connected."),
    finding("critical", "duplicate_experience", "'Belém Tower' (the same provider-backed place) is scheduled on Day 1 and Day 2."),
    WEATHER_WARNING,
    finding("warning", "holidays", "Provider-backed public holiday data is available for this trip, but the itinerary has not been checked against venue closures, opening hours, or crowd context yet -- review these dates manually: 2026-10-05 (Republic Day)."),
    finding("warning", "long_travel_day", "Day 2 involves about 9.4 km of walking between stops, more than a balanced day usually has."),
    finding("warning", "must_visit", "These must-visit places could not be included: Pena Palace. They were not replaced with unrelated attractions."),
    finding("warning", "budget", "Budget could not be checked against real prices."),
    finding("warning", "underfilled_plan", "Fewer places are scheduled than this trip's pace calls for."),
    finding("warning", "travel_time_buffer", "The gap between 'A' and 'B' is shorter than the travel time between them."),
    ...DIAGNOSTIC_SUGGESTIONS,
  ];
  const rows = travelerFindings(issues);
  assert.equal(rows.filter((row) => row.severity === "critical").length, 3);
  assert.equal(rows.filter((row) => row.severity === "warning").length, 7);
  assert.equal(rows.filter((row) => row.severity === "suggestion").length, 0);
  // a critical finding is never dropped, whatever it mentions
  assert.ok(rows.some((row) => /No experiences were scheduled/.test(row.message)));
  // severity is never changed by the policy
  for (const issue of issues) {
    const shown = travelerFinding(issue);
    if (shown) assert.equal(shown.severity, issue.severity);
  }
});

test("a route-coverage warning is one plain sentence, not the diagnostic", () => {
  const partial =
    "3 of 5 scheduled leg(s) have a provider-backed route (via geoapify_routing) with a real distance and duration. Route-aware sequencing may be applied, unavailable, failed, or not connected depending on provider data and configuration. 2 leg(s) could not be route-checked.";
  const rows = travelerFindings([
    finding("warning", "feasibility", partial),
    finding("warning", "feasibility", "Route feasibility between scheduled experiences has not been checked. No routing provider is connected."),
    finding(
      "warning",
      "movement_data",
      "Provider-backed travel-time buffer movement data is only partially available across this itinerary's legs; some legs could not be checked.",
    ),
    ...DIAGNOSTIC_SUGGESTIONS,
  ]);
  assert.deepEqual(
    rows.map((row) => [row.severity, row.message, row.count]),
    [
      ["warning", "Travel between some stops could not be fully checked.", 2],
      ["warning", "Travel times between some stops are not available.", 1],
    ],
  );
  assertNoDiagnostics(rows);
});

test("nothing Traveler view shows contains the diagnostic phrases", () => {
  const everything = [
    ...DIAGNOSTIC_SUGGESTIONS,
    ...DIAGNOSTIC_SUGGESTIONS.map((issue) => ({ ...issue, severity: "warning" })),
    ...DIAGNOSTIC_SUGGESTIONS.map((issue) => ({ ...issue, severity: "warning", category: "unknown_category" })),
    WEATHER_WARNING,
  ];
  const rows = travelerFindings(everything);
  assertNoDiagnostics(rows);
  // only the feasibility / movement sentences and the weather warning remain
  assert.deepEqual(rows.map((row) => row.message.slice(0, 28)).sort(), [
    "Travel between some stops co",
    "Travel times between some st",
    "Weather data is available fo",
  ]);
});

// -- optional-suggestion control ---------------------------------------------------------

test("the optional-suggestion count covers only traveler-visible suggestions", () => {
  const eligible = new Set(["packing_tip"]);
  const issues = [
    finding("suggestion", "packing_tip", "Evenings are cool in October; bring a light jacket."),
    finding("suggestion", "packing_tip", "Several stops involve stairs; wear comfortable shoes."),
    // listed category, but diagnostic prose: still hidden
    finding("suggestion", "packing_tip", "The inventory provider configuration was checked."),
    ...DIAGNOSTIC_SUGGESTIONS,
    WEATHER_WARNING,
  ];
  const rows = travelerFindings(issues, eligible);
  const suggestions = rows.filter((row) => row.severity === "suggestion");
  assert.equal(suggestions.length, 2);
  assertNoDiagnostics(rows);

  // the control's count is the length of exactly that filtered list
  const section = componentSource(travelerSections, "TravelerLimitationsSection");
  assert.match(section, /const findings = travelerFindings\(\[\.\.\.report\.critical_issues, \.\.\.report\.warnings\]\);/);
  assert.match(section, /const suggestions = bySeverity\("suggestion"\);/);
  assert.match(section, /<SuggestionsDisclosure count=\{suggestions\.length\}>\{renderRows\(suggestions\)\}<\/SuggestionsDisclosure>/);
  // no other path renders a raw report finding in this section
  assert.doesNotMatch(section, /aggregateIssues|report\.warnings\.map|report\.critical_issues\.map/);
});

test("the suggestions button is not rendered when no suggestion is traveler-visible", () => {
  // with today's policy every backend suggestion is a diagnostic
  const rows = travelerFindings([WEATHER_WARNING, ...DIAGNOSTIC_SUGGESTIONS]);
  assert.equal(rows.filter((row) => row.severity === "suggestion").length, 0);

  const section = componentSource(travelerSections, "TravelerLimitationsSection");
  // the control exists only behind a non-empty check, so there is no button
  // and no empty expanded area
  assert.match(section, /\{suggestions\.length > 0 && \(\s*<SuggestionsDisclosure/);
  assert.equal(section.split("<SuggestionsDisclosure").length - 1, 1);
  // the intro line stops mentioning suggestions when there are none
  assert.match(section, /suggestions\.length > 0 \? ", suggestions are optional\." : "\."/);
  // the disclosure only mounts its rows while open
  assert.match(componentSource(travelerSections, "SuggestionsDisclosure"), /\{open \? children : null\}/);
});

// -- Developer view -------------------------------------------------------------------------

test("Developer view still lists every finding, diagnostics included", () => {
  const developerStart = page.indexOf('{mode === "developer" ? (');
  const travelerStart = page.indexOf("{/* ---- Traveler view", developerStart);
  const developerBlock = page.slice(developerStart, travelerStart);
  const travelerBlock = page.slice(travelerStart, page.indexOf("</main>", travelerStart));

  assert.match(developerBlock, /<ValidationSection report=\{result\.validationReport\} \/>/);
  assert.ok(!travelerBlock.includes("<ValidationSection"));
  assert.match(travelerBlock, /<TravelerLimitationsSection report=\{result\.validationReport\} \/>/);

  // the Developer section reads the raw report and applies no Traveler filter
  const validation = componentSource(page, "ValidationSection");
  assert.match(validation, /report\.warnings/);
  assert.match(validation, /report\.critical_issues/);
  assert.doesNotMatch(validation, /travelerFinding|travelerFindings|travelerText|hasDiagnosticWording/);
  assert.doesNotMatch(page, /travelerFindings|travelerFinding\(/);
  // its per-finding card shows the backend message and suggested fix verbatim
  const card = componentSource(page, "ValidationIssueCard");
  assert.match(card, /issue\.message/);
  assert.match(card, /issue\.suggested_fix/);
  // route / inventory diagnostics keep their own Developer sections as well
  for (const marker of ["<RouteFeasibilitySection", "<ProviderCoverageSection", "<AccommodationInventorySection", "<FlightInventorySection"]) {
    assert.ok(developerBlock.includes(marker), `${marker} must stay in Developer view`);
  }
});
