// R2a: a must-visit the place data explicitly marks private / not open to the
// public is kept, and Traveler view must SHOW the backend's day-level finding.
// Run with `npm test` (Node's built-in test runner).

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

const CATEGORY = "must_visit_access_restricted";
const CODE = CATEGORY.toUpperCase();
const CLASSIFICATION = { [CODE]: "material", WEATHER: "informational" };

// The backend's own wording (`_inaccessible_must_visit_issues`).
const MESSAGE =
  "Day 2: Museum req is marked in the place data as private. It is kept because you asked for it, but " +
  "visitor access and admission are not established, so do not rely on being able to visit it.";

const finding = {
  issue_id: "w-1",
  severity: "warning",
  category: CATEGORY,
  message: MESSAGE,
  affected_section: "experience_plan.daily_plans[2]",
  suggested_fix: "Check whether and how this place can be visited before planning your day around it.",
};

test("the backend classifies the code as material and emits the category", () => {
  const material = /MATERIAL_CODES: frozenset\[str\] = frozenset\(\s*\{([^}]*)\}/.exec(backendClassification);
  assert.ok(material && material[1].includes(`"${CODE}"`));
  assert.ok(backendValidator.includes(`category="${CATEGORY}"`));
});

test("the finding's wording is shown as written, never replaced by a generic sentence", () => {
  assert.equal(hasDiagnosticWording(MESSAGE), false);
  assert.equal(isMaterialFinding(finding, CLASSIFICATION), true);
  assert.deepEqual(travelerFinding(finding, new Set(), CLASSIFICATION), finding);
  // also visible on a report that carries no classification at all
  assert.deepEqual(travelerFinding(finding, new Set(), null), finding);
});

test("it is shown on the affected day's card and only there", () => {
  const warnings = [finding];
  assert.deepEqual(travelerDayFindings(warnings, 2, CLASSIFICATION), [
    "Museum req is marked in the place data as private. It is kept because you asked for it, but " +
      "visitor access and admission are not established, so do not rely on being able to visit it.",
  ]);
  assert.deepEqual(travelerDayFindings(warnings, 1, CLASSIFICATION), []);
});

test("it is listed under Important limitations and makes the banner material", () => {
  const rows = travelerFindings([finding], new Set(), CLASSIFICATION);
  assert.equal(rows.length, 1);
  assert.match(rows[0].message, /do not rely on being able to visit it/);
  const report = { blocking_codes: [], review_codes: [CODE, "WEATHER"], review_code_classification: CLASSIFICATION };
  assert.equal(reviewSeverity(report), "material");
  assert.ok(travelerReviewNotices(report).includes(OTHER_MATERIAL_NOTICE));
});

test("the day card renders the day's findings in Traveler view", () => {
  const card = page.indexOf("<DayReviewNotices");
  assert.ok(card > 0);
  assert.ok(page.slice(card, card + 600).includes("travelerDayFindings("));
});
