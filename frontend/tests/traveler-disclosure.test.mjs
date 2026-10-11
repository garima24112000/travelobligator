// V1 disclosure contract regression tests. Run with `npm test` (Node's
// built-in test runner; no test framework dependency).
//
// Which validation findings are MATERIAL is decided by the backend
// (`backend/app/services/review_classification.py`, sent on every report as
// `review_code_classification`). These tests read that file, so the page can
// never drift from it, and check that Traveler view shows every material
// finding: in the banner, under Important limitations, on the affected day
// and on the affected leg. As in the other suites: unit tests of the pure
// helpers plus structure tests of the page source (no DOM renderer here).

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  FERRY_SENTENCE,
  GENERIC_MATERIAL_SENTENCE,
  GEOGRAPHIC_SPREAD_SENTENCE,
  affectedDayNumber,
  hasDiagnosticWording,
  isMaterialFinding,
  travelerDayFindings,
  travelerFinding,
  travelerFindings,
} from "../lib/display-labels.ts";
import {
  FERRY_DAY_NOTICE,
  FERRY_LEG_LABEL,
  OTHER_MATERIAL_NOTICE,
  UNVERIFIED_DAY_NOTICE,
  UNVERIFIED_LEG_LINE,
  legIncludesFerry,
  legIsUnverified,
  reviewSeverity,
  travelerDayNotices,
  travelerDayRouteNotices,
  travelerLegLine,
  travelerMovementLine,
  travelerReadiness,
  travelerReviewNotices,
} from "../lib/traveler-view.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const page = readFileSync(join(root, "app", "page.tsx"), "utf8");
const types = readFileSync(join(root, "lib", "types.ts"), "utf8");
const displayLabels = readFileSync(join(root, "lib", "display-labels.ts"), "utf8");
const travelerView = readFileSync(join(root, "lib", "traveler-view.ts"), "utf8");
const backend = readFileSync(
  join(root, "..", "backend", "app", "services", "review_classification.py"),
  "utf8",
);

function backendCodes(name) {
  const block = new RegExp(`${name}: frozenset\\[str\\] = frozenset\\(\\s*\\{([^}]*)\\}`).exec(backend);
  assert.ok(block, `${name} not found in the backend classification`);
  return [...block[1].matchAll(/"([A-Z_]+)"/g)].map((match) => match[1]);
}

const MATERIAL = backendCodes("MATERIAL_CODES");
const INFORMATIONAL = backendCodes("INFORMATIONAL_CODES");
/** What the backend would send for a report holding every known code. */
const CLASSIFICATION = Object.fromEntries([
  ...MATERIAL.map((code) => [code, "material"]),
  ...INFORMATIONAL.map((code) => [code, "informational"]),
]);

function componentSource(name) {
  const start = page.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `${name} not found in page.tsx`);
  const next = page.indexOf("\nfunction ", start + 1);
  return page.slice(start, next === -1 ? undefined : next);
}

const warning = (category, message, day = null) => ({
  issue_id: `${category}-${day}`,
  severity: "warning",
  category,
  message,
  affected_section: day === null ? "experience_plan" : `experience_plan.daily_plans[${day}]`,
  suggested_fix: null,
});

// The backend's own traveler wording (app/services/plan_validator_service.py).
const FERRY_FINDING = warning(
  "route_includes_ferry",
  "Day 3: getting between Liberty Landing and Harbour Island involves a ferry crossing. No ferry timetable, fare, ticket or availability is known to this plan. The time shown for this leg is a route estimate only: it does not include waiting for or boarding the ferry and is not a verified ferry schedule.",
  3,
);
const DISPERSION_FINDING = warning(
  "geographic_dispersion",
  "Day 4: this day's attractions are spread across a large area -- about 26.2 km in a straight line between consecutive stops. Review the travel required before following this schedule. Each transfer of the day has a checked route within the usual limits, so this is about how far apart the places are. At least one stop of this day is optional and lies far from the others.",
  4,
);
// The wording of reports stored BEFORE the correction (implementation prose).
const OLD_FERRY_FINDING = warning(
  "route_includes_ferry",
  "Day 3: the routing provider's route between A and B includes a ferry crossing. No ferry timetable, fare, ticket or availability is known to this plan, and the transfer time shown does not account for waiting.",
  3,
);
const OLD_DISPERSION_FINDING = warning(
  "geographic_dispersion",
  "Day 4 covers a wide area: about 26.2 km in a straight line between consecutive stops. Every transfer of the day has a verified route within the configured limits, so this is about how far apart the places are.",
  4,
);

const leg = (from, to, overrides = {}) => ({
  from_experience_id: from,
  to_experience_id: to,
  status: "success",
  mode: "walk",
  includes_ferry: null,
  ...overrides,
});
const buffer = (from, to, overrides = {}) => ({
  from_experience_id: from,
  to_experience_id: to,
  status: "success",
  route_duration_seconds: 4463,
  route_distance_meters: 6569,
  ...overrides,
});

// ---------------------------------------------------------------------------
// The classification is the backend's
// ---------------------------------------------------------------------------

test("the page holds no classification of its own and reads the backend's", () => {
  assert.ok(MATERIAL.length >= 15 && INFORMATIONAL.length >= 7);
  assert.equal(MATERIAL.filter((code) => INFORMATIONAL.includes(code)).length, 0);
  assert.match(types, /review_code_classification\?: Record<string, "informational" \| "material">/);
  // no frontend file enumerates informational codes, and none of the helper
  // names a material code outside its WORDING tables
  for (const source of [displayLabels, travelerView, page]) {
    assert.doesNotMatch(source, /INFORMATIONAL_CODES|MATERIAL_CODES/);
  }
  assert.match(displayLabels, /classification\[category\.toUpperCase\(\)\] \?\? "material"/);
  assert.match(travelerView, /classification\[code\] \?\? "material"/);
});

test("every material warning passes the Traveler filter, whatever its text", () => {
  for (const code of MATERIAL) {
    const category = code.toLowerCase();
    for (const message of [
      `Day 2: something a traveler can read about ${category}.`,
      "3 of 5 scheduled leg(s) have provider route data; the adapter configuration needs review.",
    ]) {
      const shown = travelerFinding(warning(category, message, 2), undefined, CLASSIFICATION);
      assert.ok(shown, `${code} was hidden for: ${message}`);
      assert.equal(shown.severity, "warning");
      // wherever the helper words the finding itself, the wording is a traveler's
      // (a category whose backend text is already shown verbatim keeps that text)
      if (shown.message !== message) {
        assert.equal(hasDiagnosticWording(shown.message), false, `${code}: ${shown.message}`);
      }
    }
  }
  // ... and reaches the Important limitations list
  const rows = travelerFindings(
    MATERIAL.map((code) => warning(code.toLowerCase(), `A plain sentence about ${code}.`)),
    undefined,
    CLASSIFICATION,
  );
  assert.equal(rows.length, MATERIAL.length);
});

test("an unknown code stays visible; an informational diagnostic stays out", () => {
  const unknown = warning("a_finding_added_next_year", "The provider configuration changed.", 1);
  // the backend sends "material" for a code its list does not hold ...
  const sent = { A_FINDING_ADDED_NEXT_YEAR: "material", WEATHER: "informational" };
  assert.equal(isMaterialFinding(unknown, sent), true);
  assert.equal(travelerFinding(unknown, undefined, sent).message, `Day 1: ${GENERIC_MATERIAL_SENTENCE}`);
  // ... and a code missing from a classification that WAS sent is material too
  assert.equal(isMaterialFinding(unknown, { WEATHER: "informational" }), true);
  assert.ok(travelerFinding(unknown, undefined, { WEATHER: "informational" }));
  // informational bookkeeping is still not a traveler warning
  const bookkeeping = warning("provider_coverage_consistency", "Provider coverage is inconsistent.");
  assert.equal(travelerFinding(bookkeeping, undefined, CLASSIFICATION), null);
  assert.equal(isMaterialFinding(warning("weather", "x"), CLASSIFICATION), false);
  // severity is never changed, and a critical finding is always shown
  assert.equal(travelerFinding({ ...unknown, severity: "critical" }, undefined, sent).severity, "critical");
});

// ---------------------------------------------------------------------------
// Ferry
// ---------------------------------------------------------------------------

test("a provider-confirmed ferry leg is never shown as Walk alone", () => {
  const report = { status: "success", legs: [leg("a", "b", { includes_ferry: true }), leg("b", "c")] };
  const line = travelerLegLine(buffer("a", "b"), report);
  assert.ok(line.startsWith(FERRY_LEG_LABEL));
  assert.doesNotMatch(line, /^Walk\b|\bWalk ·/);
  assert.match(line, /route estimate 1 h 14 min · 6\.6 km/);
  assert.match(line, /excludes waiting; ferry times, tickets and availability not verified/);
  // a vehicle leg with a ferry is not "Vehicle transfer" alone either
  const drive = travelerMovementLine({ mode: "drive", durationSeconds: 600, distanceMeters: 5000, includesFerry: true });
  assert.ok(drive.startsWith(FERRY_LEG_LABEL));
  // the ordinary leg is unchanged
  assert.equal(travelerLegLine(buffer("b", "c", { route_duration_seconds: 720, route_distance_meters: 900 }), report), "Walk · 12 min · 0.9 km");
  // nothing is stated without figures, with or without a ferry
  assert.equal(travelerMovementLine({ mode: "walk", durationSeconds: null, distanceMeters: null, includesFerry: true }), null);
});

test("a ferry is never inferred: only the provider's positive flag counts", () => {
  for (const flag of [null, undefined, false, "true", 1]) {
    const report = { status: "success", legs: [leg("a", "b", { includes_ferry: flag })] };
    assert.equal(legIncludesFerry(report, "a", "b"), false, String(flag));
    assert.equal(travelerLegLine(buffer("a", "b"), report), "Walk · 1 h 14 min · 6.6 km");
    assert.deepEqual(travelerDayRouteNotices(["a", "b"], report), { ferry: false, unverified: false });
  }
  // a long over-water looking leg, a "ferry" in a place name: still no ferry
  assert.equal(legIncludesFerry({ status: "success", legs: [leg("ferry-terminal", "island")] }, "ferry-terminal", "island"), false);
  assert.equal(legIncludesFerry(null, "a", "b"), false);
  assert.match(types, /includes_ferry\?: boolean \| null;/);
});

test("the ferry disclosure is shown on the affected day, once", () => {
  const report = { status: "success", legs: [leg("a", "b", { includes_ferry: true }), leg("c", "d")] };
  const route = travelerDayRouteNotices(["a", "b"], report);
  assert.deepEqual(route, { ferry: true, unverified: false });
  // with the backend's finding for the day: its sentence, not a second one
  const findings = travelerDayFindings([FERRY_FINDING, DISPERSION_FINDING], 3, CLASSIFICATION);
  assert.equal(findings.length, 1);
  assert.match(findings[0], /^getting between Liberty Landing and Harbour Island involves a ferry crossing\./);
  assert.match(findings[0], /No ferry timetable, fare, ticket or availability is known/);
  assert.match(findings[0], /not a verified ferry schedule/);
  assert.deepEqual(travelerDayNotices(findings, route), findings);
  // without one (an old report): the fixed notice
  assert.deepEqual(travelerDayNotices([], route), [FERRY_DAY_NOTICE]);
  assert.match(FERRY_DAY_NOTICE, /schedules, waiting times, tickets and operating availability are not verified/);
  // another day says nothing about it
  assert.deepEqual(travelerDayNotices(travelerDayFindings([FERRY_FINDING], 1, CLASSIFICATION), travelerDayRouteNotices(["c", "d"], report)), []);
  // an old report's implementation prose is replaced, never hidden
  assert.deepEqual(travelerDayFindings([OLD_FERRY_FINDING], 3, undefined), [FERRY_SENTENCE]);
  assert.equal(travelerFinding(OLD_FERRY_FINDING).message, `Day 3: ${FERRY_SENTENCE}`);
});

// ---------------------------------------------------------------------------
// Geographic dispersion
// ---------------------------------------------------------------------------

test("a dispersed day is visible on that day and in the limitations list", () => {
  const onDay = travelerDayFindings([DISPERSION_FINDING, FERRY_FINDING], 4, CLASSIFICATION);
  assert.equal(onDay.length, 1);
  assert.match(onDay[0], /^this day's attractions are spread across a large area/);
  assert.match(onDay[0], /Review the travel required before following this schedule\./);
  assert.deepEqual(travelerDayFindings([DISPERSION_FINDING], 2, CLASSIFICATION), []);
  assert.equal(travelerFindings([DISPERSION_FINDING], undefined, CLASSIFICATION).length, 1);
  // geographic spread (the unverified / over-limit finding) the same way
  const spread = warning("geographic_spread", "Day 2: stops are far apart and the route could not be checked.", 2);
  assert.equal(travelerDayFindings([spread], 2, CLASSIFICATION).length, 1);
  // old implementation prose is replaced by the traveler sentence, never hidden
  assert.equal(travelerFinding(OLD_DISPERSION_FINDING).message, `Day 4: ${GEOGRAPHIC_SPREAD_SENTENCE}`);
  assert.deepEqual(travelerDayFindings([OLD_DISPERSION_FINDING], 4, undefined), [GEOGRAPHIC_SPREAD_SENTENCE]);
  assert.equal(affectedDayNumber(DISPERSION_FINDING), 4);
  assert.equal(affectedDayNumber(warning("weather", "x")), null);
  // an informational finding about a day is not put on the day card
  assert.deepEqual(travelerDayFindings([warning("weather", "Day 4: rain is forecast.", 4)], 4, CLASSIFICATION), []);
});

// ---------------------------------------------------------------------------
// Unverified legs
// ---------------------------------------------------------------------------

test("a leg without a verified route gets an explicit row and a day notice", () => {
  const report = {
    status: "partial",
    legs: [leg("a", "b"), leg("c", "d", { status: "failed", mode: null })],
  };
  assert.equal(legIsUnverified(report, "c", "d"), true);
  assert.equal(legIsUnverified(report, "a", "b"), false);
  assert.equal(legIsUnverified(report, "x", "y"), true); // no stored leg at all
  assert.equal(travelerLegLine(buffer("c", "d", { status: "failed", route_duration_seconds: null, route_distance_meters: null }), report), null);
  assert.deepEqual(travelerDayNotices([], travelerDayRouteNotices(["c", "d"], report)), [UNVERIFIED_DAY_NOTICE]);
  // the row states that nothing is verified, and no mode, time or service
  assert.match(UNVERIFIED_LEG_LINE, /could not be verified/);
  for (const text of [UNVERIFIED_LEG_LINE, UNVERIFIED_DAY_NOTICE]) {
    assert.doesNotMatch(text, /\d|walk|drive|vehicle|ferry|bus|train|taxi|transit|hike|min\b|km\b/i);
  }
  // a trip with no routed leg at all keeps its ONE trip-level note; no row per leg
  const none = { status: "unavailable", legs: [leg("a", "b", { status: "unavailable" })] };
  assert.equal(legIsUnverified(none, "a", "b"), false);
  assert.equal(legIsUnverified(null, "a", "b"), false);
  assert.deepEqual(travelerDayRouteNotices(["a", "b"], none), { ferry: false, unverified: false });
  // a single-stop day has no leg to report
  assert.deepEqual(travelerDayRouteNotices(["a"], report), { ferry: false, unverified: false });
});

// ---------------------------------------------------------------------------
// Readiness presentation
// ---------------------------------------------------------------------------

test("material and informational review are presented differently, and neither as ready", () => {
  const informational = { blocking_codes: [], review_codes: ["WEATHER", "HOLIDAYS"], review_code_classification: CLASSIFICATION };
  const material = { blocking_codes: [], review_codes: ["WEATHER", "GEOGRAPHIC_DISPERSION"], review_code_classification: CLASSIFICATION };
  assert.equal(reviewSeverity(informational), "informational");
  assert.equal(reviewSeverity(material), "material");
  const calm = travelerReadiness("needs_review", reviewSeverity(informational));
  const strong = travelerReadiness("needs_review", reviewSeverity(material));
  assert.equal(calm.tone, "review");
  assert.equal(strong.tone, "review_material");
  assert.notEqual(calm.label, strong.label);
  assert.notEqual(calm.message, strong.message);
  for (const readiness of [calm, strong]) {
    assert.notEqual(readiness.tone, "ok");
    assert.doesNotMatch(`${readiness.label} ${readiness.message}`, /checks passed|verified|ready to (follow|use)/i);
  }
  // only the backend's `ready` is "Checks passed"; severity never upgrades anything
  assert.equal(travelerReadiness("ready", "material").tone, "ok");
  assert.equal(travelerReadiness("blocked", "informational").tone, "blocked");
  assert.match(travelerReadiness("blocked", "informational").label, /Not ready to use/);
  // every material code makes the plan material; unknown and unclassified codes too
  for (const code of MATERIAL) {
    assert.equal(reviewSeverity({ review_codes: [code], review_code_classification: CLASSIFICATION }), "material", code);
  }
  for (const code of INFORMATIONAL) {
    assert.equal(reviewSeverity({ review_codes: [code], review_code_classification: CLASSIFICATION }), "informational", code);
  }
  assert.equal(reviewSeverity({ review_codes: ["BRAND_NEW_CODE"], review_code_classification: CLASSIFICATION }), "material");
  assert.equal(reviewSeverity({ review_codes: ["WEATHER"] }), "material"); // an old report: never played down
  assert.equal(reviewSeverity({ blocking_codes: ["INSUFFICIENT_VERIFIED_INVENTORY"], review_code_classification: CLASSIFICATION }), "material");
  assert.equal(reviewSeverity(null), "informational"); // nothing to review
});

test("every material code is announced in the banner", () => {
  for (const code of MATERIAL) {
    if (code === "INSUFFICIENT_VERIFIED_INVENTORY" || code === "UNDERFILLED_PLAN") continue; // their own notice above the itinerary
    const notices = travelerReviewNotices({ review_codes: [code], review_code_classification: CLASSIFICATION });
    assert.equal(notices.length, 1, code);
    assert.equal(hasDiagnosticWording(notices[0]), false, notices[0]);
  }
  assert.deepEqual(
    travelerReviewNotices({ review_codes: ["BRAND_NEW_CODE"], review_code_classification: { BRAND_NEW_CODE: "material" } }),
    [OTHER_MATERIAL_NOTICE],
  );
  // an informational code without a sentence adds nothing
  assert.deepEqual(travelerReviewNotices({ review_codes: ["HOTEL_RATINGS"], review_code_classification: CLASSIFICATION }), []);
  const ferry = travelerReviewNotices({ review_codes: ["ROUTE_INCLUDES_FERRY"], review_code_classification: CLASSIFICATION })[0];
  assert.match(ferry, /ferry crossing/);
  assert.match(ferry, /not verified/);
});

// ---------------------------------------------------------------------------
// The page is wired to the helpers
// ---------------------------------------------------------------------------

test("the day card, the leg rows and the banner use the disclosure helpers", () => {
  const dayNotices = componentSource("DayReviewNotices");
  assert.match(dayNotices, /if \(notices\.length === 0\) return null;/);
  assert.match(dayNotices, /role="note"/);
  assert.match(dayNotices, /aria-label=\{`Needs review on day \$\{dayNumber\}`\}/);
  assert.match(dayNotices, /break-words/); // long sentences wrap on a narrow screen
  assert.doesNotMatch(dayNotices, /whitespace-nowrap|min-w-\[|w-\[\d/);

  assert.match(page, /<DayReviewNotices\s+dayNumber=\{day\.day_number\}/);
  assert.match(page, /travelerDayFindings\(\s*result\.validationReport\.warnings,\s*day\.day_number,\s*result\.validationReport\.review_code_classification,/);
  assert.match(page, /travelerDayRouteNotices\(/);
  assert.match(page, /\{legUnverified && <UnverifiedLegRow \/>\}/);
  assert.match(page, /attention=\{legHasFerry\}/);

  const row = componentSource("UnverifiedLegRow");
  assert.match(row, /\{UNVERIFIED_LEG_LINE\}/);
  assert.doesNotMatch(row, /duration|distance|mode/i); // it states no figure and no mode

  const banner = componentSource("UserModeReadinessBanner");
  assert.match(banner, /readiness\.tone === "review_material"/);
  assert.match(banner, /role=\{readiness\.tone === "review_material" \|\| readiness\.tone === "blocked" \? "alert" : "status"\}/);
  assert.match(page, /severity=\{reviewSeverity\(result\.validationReport\)\}/);

  // Developer view's validation report is untouched: the raw findings, no Traveler filter
  const validation = componentSource("ValidationSection");
  assert.match(validation, /report\.warnings/);
  assert.doesNotMatch(validation, /travelerDayFindings|isMaterialFinding|review_code_classification/);
});
