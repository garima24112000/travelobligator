// R4a: a must-visit on a leg the routing provider definitively failed to
// route is kept, and Traveler view must SHOW the backend's day-level finding
// in full. Run with `npm test` (Node's built-in test runner).

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
  travelerFindings,
} from "../lib/display-labels.ts";
import { OTHER_MATERIAL_NOTICE, reviewSeverity, travelerReviewNotices } from "../lib/traveler-view.ts";

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

const CATEGORY = "must_visit_route_unverified";
const CODE = CATEGORY.toUpperCase();
const CLASSIFICATION = { [CODE]: "material", FEASIBILITY: "material", WEATHER: "informational" };

// The backend's own wording (`_unrouted_must_visit_leg_issues`).
const BODY =
  "A verified route between Museum req and Museum m3 could not be found. Museum req is kept because you " +
  "requested it, but travel between these stops is not verified. Check the appropriate way to reach it " +
  "before relying on this day's plan.";
const MESSAGE = `Day 3: ${BODY}`;

const finding = {
  issue_id: "w-1",
  severity: "warning",
  category: CATEGORY,
  message: MESSAGE,
  affected_section: "experience_plan.daily_plans[3]",
  suggested_fix: "Check how to get between these stops yourself before following this day.",
};

test("the backend classifies the code as material and emits the category", () => {
  const material = /MATERIAL_CODES: frozenset\[str\] = frozenset\(\s*\{([^}]*)\}/.exec(backendClassification);
  assert.ok(material && material[1].includes(`"${CODE}"`));
  assert.ok(backendValidator.includes(`category="${CATEGORY}"`));
});

test("the full wording is shown as written, never replaced by a generic sentence", () => {
  assert.equal(hasDiagnosticWording(MESSAGE), false);
  assert.equal(isMaterialFinding(finding, CLASSIFICATION), true);
  assert.deepEqual(travelerFinding(finding, new Set(), CLASSIFICATION), finding);
  assert.deepEqual(travelerFinding(finding, new Set(), null), finding);
});

test("it is shown on the affected day's card only", () => {
  assert.deepEqual(travelerDayFindings([finding], 3, CLASSIFICATION), [BODY]);
  assert.deepEqual(travelerDayFindings([finding], 2, CLASSIFICATION), []);
});

test("it sits beside the existing generic feasibility warning, which is unchanged", () => {
  const feasibility = {
    issue_id: "w-2",
    severity: "warning",
    category: "feasibility",
    message: "2 of 3 scheduled leg(s) have a provider-backed route. 1 leg(s) could not be route-checked.",
    affected_section: "experience_plan",
    suggested_fix: null,
  };
  const rows = travelerFindings([feasibility, finding], new Set(), CLASSIFICATION).map((row) => row.message);
  assert.ok(rows.includes(MESSAGE));
  assert.ok(rows.includes("Travel between some stops could not be fully checked."));
  const report = { blocking_codes: [], review_codes: [CODE, "FEASIBILITY"], review_code_classification: CLASSIFICATION };
  assert.equal(reviewSeverity(report), "material");
  assert.ok(travelerReviewNotices(report).includes(OTHER_MATERIAL_NOTICE));
});

test("no time, distance, mode or access claim is shown", () => {
  for (const word of [/minute/i, /\bkm\b/i, /walk/i, /drive/i, /ferry/i, /closed/i, /inaccessible/i, /impossible/i]) {
    assert.doesNotMatch(MESSAGE, word);
  }
});

test("the day card renders the day's findings in Traveler view", () => {
  const card = page.indexOf("<DayReviewNotices");
  assert.ok(card > 0 && page.slice(card, card + 600).includes("travelerDayFindings("));
});
