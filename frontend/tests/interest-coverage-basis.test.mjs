// Phase 2: a "covered" interest whose coverage is weaker than the word is
// never shown as plainly covered. The backend states the basis
// (`InterestCoverageEntry.basis`); Traveler view restates it. Run with
// `npm test` (Node's built-in test runner).

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { travelerInterestCoverage, travelerReadiness } from "../lib/traveler-view.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const backendCoverage = readFileSync(join(root, "..", "backend", "app", "services", "interest_coverage.py"), "utf8");

const entry = (interest, status, basis, requested_as = [interest]) => ({ interest, requested_as, status, basis });
const report = (entries) => ({ interest_coverage: entries, warnings: [] });

test("the basis values are the backend's own", () => {
  for (const value of ["scheduled_place", "nearby_suggestions_only", "museum_subject_unrecorded"]) {
    assert.ok(backendCoverage.includes(`"${value}"`), value);
  }
});

test("food covered only by nearby suggestions is said to be so", () => {
  const coverage = travelerInterestCoverage(report([entry("food", "covered", "nearby_suggestions_only", ["local food"])]));
  assert.equal(coverage.state, "some_uncovered");
  assert.deepEqual(coverage.notices, [
    '"local food" is covered only by restaurant suggestions near your stops. No food experience, such as a food market, is scheduled.',
  ]);
});

test("history covered only by museums of unrecorded subject is said to be uncertain", () => {
  const coverage = travelerInterestCoverage(report([entry("history", "covered", "museum_subject_unrecorded")]));
  assert.deepEqual(coverage.notices, [
    '"history" is covered only by museums whose subject our place data does not record, so they may not be about "history".',
  ]);
});

test("a real scheduled match, an unknown basis and an older entry stay plainly covered", () => {
  for (const basis of ["scheduled_place", "something_new", null, undefined]) {
    const coverage = travelerInterestCoverage(report([entry("art", "covered", basis)]));
    assert.deepEqual(coverage, { state: "all_covered", notices: [] }, String(basis));
  }
});

test("the reassuring banner is never given when coverage rests on a weaker basis", () => {
  const weak = travelerInterestCoverage(report([entry("food", "covered", "nearby_suggestions_only")]));
  const strong = travelerInterestCoverage(report([entry("food", "covered", "scheduled_place")]));
  assert.notDeepEqual(
    travelerReadiness("needs_review", "informational", weak.state),
    travelerReadiness("needs_review", "informational", strong.state),
  );
});

test("the no-supply sentence speaks of what the place data identifies, never of the destination", () => {
  const coverage = travelerInterestCoverage(report([entry("waterfront", "not_covered_no_supply", null)]));
  assert.match(coverage.notices[0], /identified by our place data as fitting it/);
  assert.match(coverage.notices[0], /does not mean there are none in this destination/);
});

test("no notice claims a place is closed, missing or of a particular subject", () => {
  const coverage = travelerInterestCoverage(
    report([entry("food", "covered", "nearby_suggestions_only"), entry("art", "covered", "museum_subject_unrecorded")]),
  );
  for (const notice of coverage.notices) {
    assert.doesNotMatch(notice, /provider|closed|does not exist|is about|is not about|candidate/i);
  }
});
