// Phase 1 (travel feasibility): two disclosures Traveler view must show as
// the backend states them. (1) A day whose stops all lie away from where the
// trip's places are: travel there and back is not in the plan. (2) A walk
// beyond the usual walking limit with no faster way verified: its leg row
// never reads as an ordinary "Walk". Run with `npm test`.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  hasDiagnosticWording,
  isMaterialFinding,
  travelerDayFindings,
  travelerFinding,
} from "../lib/display-labels.ts";
import {
  FERRY_LEG_LABEL,
  OVER_LIMIT_WALK_CAVEAT,
  OVER_LIMIT_WALK_LABEL,
  legIsOverLimitWalk,
  travelerLegLine,
  travelerMovementLine,
} from "../lib/traveler-view.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const page = readFileSync(join(root, "app", "page.tsx"), "utf8");
const backendClassification = readFileSync(
  join(root, "..", "backend", "app", "services", "review_classification.py"),
  "utf8",
);
const backendValidator = readFileSync(
  join(root, "..", "backend", "app", "services", "plan_validator_service.py"),
  "utf8",
);

// -- a day away from the trip's main area ------------------------------------------------

const REMOTE = "remote_day_access";
const REMOTE_CODE = REMOTE.toUpperCase();
const CLASSIFICATION = { [REMOTE_CODE]: "material", WEATHER: "informational" };
// The backend's own wording (`_remote_day_issues`).
const REMOTE_BODY =
  "All of this day's stops lie away from the area where most of this trip's places are. The travel times " +
  "shown cover only the moves between this day's stops: getting to this area and back is not included and " +
  "has not been checked. Museum Far is on this day because you asked for it.";
const remote = {
  issue_id: "w-1",
  severity: "warning",
  category: REMOTE,
  message: `Day 2: ${REMOTE_BODY}`,
  affected_section: "experience_plan.daily_plans[2]",
  suggested_fix: "Check how you will get to this area and back, and allow time for it, before following this day.",
};

test("the backend classifies the remote-day code as material and emits the category", () => {
  const material = /MATERIAL_CODES: frozenset\[str\] = frozenset\(\s*\{([^}]*)\}/.exec(backendClassification);
  assert.ok(material && material[1].includes(`"${REMOTE_CODE}"`));
  assert.ok(backendValidator.includes(`category="${REMOTE}"`));
});

test("the remote-day finding is shown in full on its own day only", () => {
  assert.equal(hasDiagnosticWording(remote.message), false);
  assert.equal(isMaterialFinding(remote, CLASSIFICATION), true);
  assert.deepEqual(travelerFinding(remote, new Set(), CLASSIFICATION), remote);
  assert.deepEqual(travelerFinding(remote, new Set(), null), remote);
  assert.deepEqual(travelerDayFindings([remote], 2, CLASSIFICATION), [REMOTE_BODY]);
  assert.deepEqual(travelerDayFindings([remote], 1, CLASSIFICATION), []);
});

test("the remote-day finding states no time, distance, mode or place to stay", () => {
  for (const word of [/minute/i, /\bkm\b/i, /\bhour/i, /drive/i, /transit/i, /bus\b/i, /train/i, /hotel/i, /accommodation/i]) {
    assert.doesNotMatch(remote.message, word);
  }
});

// -- a walk beyond the usual walking limit ----------------------------------------------

const report = {
  over_limit_walking_legs: [{ day_number: 3, from_experience_id: "e1", to_experience_id: "e2" }],
};
const buffer = {
  from_experience_id: "e1",
  to_experience_id: "e2",
  status: "success",
  route_duration_seconds: 9960,
  route_distance_meters: 6300,
};
const routes = {
  legs: [{ from_experience_id: "e1", to_experience_id: "e2", status: "success", mode: "walk", includes_ferry: null }],
};

test("only a leg the backend listed is a long walk", () => {
  assert.equal(legIsOverLimitWalk(report, "e1", "e2"), true);
  assert.equal(legIsOverLimitWalk(report, "e2", "e1"), false);
  assert.equal(legIsOverLimitWalk({ over_limit_walking_legs: null }, "e1", "e2"), false);
  assert.equal(legIsOverLimitWalk({}, "e1", "e2"), false);
  assert.equal(legIsOverLimitWalk(null, "e1", "e2"), false);
});

test("a long walk is never shown as an ordinary walk, and no other mode is claimed", () => {
  const line = travelerLegLine(buffer, routes, report);
  assert.equal(line, `${OVER_LIMIT_WALK_LABEL} · 2 h 46 min · 6.3 km · ${OVER_LIMIT_WALK_CAVEAT}`);
  assert.doesNotMatch(line, /^Walk ·/);
  for (const word of [/vehicle/i, /drive/i, /taxi/i, /transit/i, /bus\b/i, /ferry/i, /cable/i, /funicular/i]) {
    assert.doesNotMatch(line, word);
  }
  // the same leg without the backend's list is the ordinary line, figures unchanged
  assert.equal(travelerLegLine(buffer, routes), "Walk · 2 h 46 min · 6.3 km");
  assert.equal(travelerLegLine(buffer, routes, { over_limit_walking_legs: [] }), "Walk · 2 h 46 min · 6.3 km");
});

test("a provider-flagged ferry keeps its own line", () => {
  const ferry = travelerMovementLine({
    mode: "walk", durationSeconds: 9960, distanceMeters: 6300, includesFerry: true, overLimitWalk: true,
  });
  assert.ok(ferry.startsWith(FERRY_LEG_LABEL));
});

test("with no figures there is still nothing to say", () => {
  assert.equal(travelerMovementLine({ mode: "walk", durationSeconds: null, distanceMeters: null, overLimitWalk: true }), null);
});

test("the day card passes the report to the leg line and marks the leg", () => {
  assert.match(page, /travelerLegLine\(\s*movement,\s*result\.routeFeasibilityReport,\s*result\.validationReport,?\s*\)/);
  assert.ok(page.includes("attention={legHasFerry || legIsLongWalk}"));
  assert.ok(page.includes("legIsOverLimitWalk("));
});

test("the backend builds the list and names the long walk on the day's finding", () => {
  assert.ok(backendValidator.includes("over_limit_walking_legs="));
  assert.ok(backendValidator.includes("is longer than a usual walk between stops, "));
  assert.ok(backendValidator.includes('"and no faster way of making it was verified."'));
});
