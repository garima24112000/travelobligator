// V1 disclosure: requested-interest coverage in Traveler view. Run with
// `npm test` (Node's built-in test runner; no test framework dependency).
//
// The backend says, per requested interest, whether the final itinerary
// covers it (`ValidationReport.interest_coverage`). Traveler view restates
// that: an uncovered interest is always shown, "no verified places found" is
// told apart from "places existed but none is scheduled", and unknown
// coverage is never worded as covered. Unit tests of the pure helpers plus
// structure tests of the page source (no DOM renderer in this repo).

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  COVERAGE_UNKNOWN_NOTICE,
  reviewSeverity,
  travelerInterestCoverage,
  travelerReadiness,
} from "../lib/traveler-view.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const page = readFileSync(join(root, "app", "page.tsx"), "utf8");
const types = readFileSync(join(root, "lib", "types.ts"), "utf8");
const backendFindings = readFileSync(
  join(root, "..", "backend", "app", "services", "plan_quality_findings.py"),
  "utf8",
);

function componentSource(name) {
  const start = page.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `${name} not found in page.tsx`);
  const next = page.indexOf("\nfunction ", start + 1);
  return page.slice(start, next === -1 ? undefined : next);
}

const entry = (interest, status, requested_as = [interest]) => ({ interest, status, requested_as });
const CLASSIFICATION = { WEATHER: "informational", LONG_TRAVEL_DAY: "material", INTEREST_UNDERCOVERAGE: "material" };
const report = (overrides = {}) => ({
  readiness_status: "needs_review",
  critical_issues: [],
  warnings: [],
  blocking_codes: [],
  review_codes: ["WEATHER"],
  review_code_classification: CLASSIFICATION,
  ...overrides,
});

const NO_COVERAGE_CLAIM = /no travel or coverage problem|covers everything you asked for\.$|everything you asked for is covered/i;

test("the statuses used here are exactly the backend's", () => {
  for (const status of ["covered", "not_covered_supply_available", "not_covered_no_supply", "not_tracked"]) {
    assert.match(backendFindings, new RegExp(`= "${status}"`), status);
  }
  assert.match(types, /interest_coverage\?: InterestCoverageEntry\[\] \| null;/);
});

test("all requested interests covered", () => {
  const coverage = travelerInterestCoverage(
    report({ interest_coverage: [entry("museum", "covered", ["museums"]), entry("food", "covered")] }),
  );
  assert.deepEqual(coverage, { state: "all_covered", notices: [] });
  const readiness = travelerReadiness("needs_review", "informational", coverage.state);
  assert.match(readiness.message, /No travel or coverage problem was flagged/);
  // a trip with no requested interest at all has nothing uncovered
  assert.equal(travelerInterestCoverage(report({ interest_coverage: [] })).state, "all_covered");
});

test("one uncovered interest with no verified supply", () => {
  // the holdout shape: three interests asked for, two covered, one without any supply
  const coverage = travelerInterestCoverage(
    report({
      interest_coverage: [
        entry("outdoors", "covered", ["gardens"]),
        entry("food", "covered"),
        entry("waterfront", "not_covered_no_supply"),
      ],
    }),
  );
  assert.equal(coverage.state, "some_uncovered");
  assert.deepEqual(coverage.notices, [
    'Nothing in this itinerary covers "waterfront". The verified places we searched included none that fit it. That does not mean there are none in this destination.',
  ]);
  // it is about what was SEARCHED, never a claim that the destination has no such place,
  // and it says nothing about why supply was missing
  assert.match(coverage.notices[0], /places we searched included none/);
  assert.match(coverage.notices[0], /does not mean there are none in this destination/);
  assert.doesNotMatch(coverage.notices[0], /provider|closed|does not exist|no such|couldn't find .* in this destination|there (is|are) no /i);
});

test("one uncovered interest although supply was available", () => {
  const coverage = travelerInterestCoverage(
    report({ interest_coverage: [entry("shopping", "not_covered_supply_available", ["markets"]), entry("food", "covered")] }),
  );
  assert.equal(coverage.state, "some_uncovered");
  assert.equal(
    coverage.notices[0],
    'Nothing in this itinerary covers "markets", although the places we searched included some that fit it. You can request a change, but one may not fit this schedule.',
  );
  // finding matching places is not a promise that one can be scheduled
  assert.match(coverage.notices[0], /one may not fit this schedule/);
  assert.doesNotMatch(coverage.notices[0], /to include one|will be added|can be added|we will|guarantee/i);
  // the two cases are worded differently
  const none = travelerInterestCoverage(report({ interest_coverage: [entry("shopping", "not_covered_no_supply", ["markets"])] }));
  assert.notEqual(none.notices[0], coverage.notices[0]);
  // the traveler's own words are used; several terms for one interest are all shown
  const several = travelerInterestCoverage(
    report({ interest_coverage: [entry("waterfront", "not_covered_no_supply", ["piers", "harbour"])] }),
  );
  assert.match(several.notices[0], /"piers \/ harbour"/);
});

test("missing or unknown coverage evidence is never presented as covered", () => {
  for (const missing of [undefined, null]) {
    const coverage = travelerInterestCoverage(report({ interest_coverage: missing }));
    assert.equal(coverage.state, "unknown");
    const readiness = travelerReadiness("needs_review", "informational", coverage.state);
    assert.doesNotMatch(readiness.message, NO_COVERAGE_CLAIM);
    assert.match(readiness.message, /could not be confirmed/);
  }
  assert.equal(travelerInterestCoverage(null).state, "unknown");
  assert.equal(travelerInterestCoverage(undefined).state, "unknown");
  // a term the backend could not track, and a status this page does not know
  for (const status of ["not_tracked", "some_future_status", "", null]) {
    const coverage = travelerInterestCoverage(report({ interest_coverage: [entry("zorbing", status), entry("food", "covered")] }));
    assert.equal(coverage.state, "some_uncovered", String(status));
    assert.equal(coverage.notices[0], 'We couldn\'t check whether this itinerary covers "zorbing".');
  }
  // a malformed entry: unknown, with a notice, never "all covered"
  const malformed = travelerInterestCoverage(report({ interest_coverage: [null, entry("food", "covered")] }));
  assert.deepEqual(malformed, { state: "unknown", notices: [COVERAGE_UNKNOWN_NOTICE] });
  // the readiness wording without any coverage knowledge passed in
  assert.doesNotMatch(travelerReadiness("needs_review", "informational").message, NO_COVERAGE_CLAIM);
});

test("a report created before the disclosure patch", () => {
  const limited = {
    severity: "suggestion",
    category: "interest_supply_limited",
    message:
      "No quality-approved provider-backed place matching the requested interest 'waterfront' was available for this destination, so none could be scheduled.",
    affected_section: "experience_plan",
    suggested_fix: null,
  };
  const undercovered = {
    severity: "warning",
    category: "interest_undercoverage",
    message:
      "Requested interest 'shopping' has 3 quality-approved provider-backed candidate(s) available, but none is scheduled in this itinerary.",
    affected_section: "experience_plan",
    suggested_fix: null,
  };
  // no entries and no classification: the report's own findings are the evidence
  const old = { readiness_status: "needs_review", critical_issues: [], warnings: [limited, undercovered], review_codes: ["WEATHER"] };
  const coverage = travelerInterestCoverage(old);
  assert.equal(coverage.state, "some_uncovered");
  assert.deepEqual(coverage.notices, [
    'Nothing in this itinerary covers "waterfront". The verified places we searched included none that fit it. That does not mean there are none in this destination.',
    'Nothing in this itinerary covers "shopping", although the places we searched included some that fit it. You can request a change, but one may not fit this schedule.',
  ]);
  coverage.notices.forEach((notice) => assert.doesNotMatch(notice, /provider|quality-approved|candidate/i));
  // a finding whose text names no interest is still disclosed
  const unnamed = travelerInterestCoverage({ warnings: [{ ...limited, message: "Something else entirely." }] });
  assert.match(unnamed.notices[0], /covers something you asked for/);
  // an old report with no such finding: coverage is unknown, not confirmed
  assert.deepEqual(travelerInterestCoverage({ warnings: [], review_codes: ["WEATHER"] }), { state: "unknown", notices: [] });
  // and an old report is presented as material review, as before
  assert.equal(reviewSeverity(old), "material");
});

test("informational-only readiness with an uncovered interest", () => {
  const validation = report({ interest_coverage: [entry("waterfront", "not_covered_no_supply"), entry("food", "covered")] });
  const coverage = travelerInterestCoverage(validation);
  assert.equal(reviewSeverity(validation), "informational"); // the backend raised no material code
  const readiness = travelerReadiness("needs_review", reviewSeverity(validation), coverage.state);
  assert.equal(readiness.tone, "review");
  assert.match(readiness.label, /not everything you asked for is covered/);
  assert.match(readiness.message, /does not cover everything you asked for/);
  assert.doesNotMatch(readiness.message, /No travel or coverage problem/);
  assert.notEqual(readiness.tone, "ok");
  // the status is still what the backend said: nothing here made it "ready" or "blocked"
  assert.equal(validation.readiness_status, "needs_review");
  // a `ready` plan keeps its banner, and the notice is still produced for it
  assert.equal(travelerReadiness("ready", "informational", coverage.state).tone, "ok");
  assert.equal(coverage.notices.length, 1);
});

test("material review together with uncovered interests", () => {
  const validation = report({
    review_codes: ["LONG_TRAVEL_DAY", "INTEREST_UNDERCOVERAGE", "WEATHER"],
    interest_coverage: [entry("shopping", "not_covered_supply_available", ["markets"]), entry("waterfront", "not_covered_no_supply")],
  });
  const coverage = travelerInterestCoverage(validation);
  assert.equal(reviewSeverity(validation), "material");
  const readiness = travelerReadiness("needs_review", reviewSeverity(validation), coverage.state);
  assert.equal(readiness.tone, "review_material"); // the stronger banner is kept
  assert.equal(coverage.notices.length, 2); // and both missing interests are listed
  assert.equal(new Set(coverage.notices).size, 2);
  // a blocked plan stays clearly unusable whatever the coverage
  assert.equal(travelerReadiness("blocked", "material", coverage.state).tone, "blocked");
});

test("the notice is wired above the itinerary with an accessible, wrapping structure", () => {
  const notice = componentSource("InterestCoverageNotice");
  assert.match(notice, /if \(notices\.length === 0\) return null;/);
  assert.match(notice, /<section/);
  assert.match(notice, /role="status"/);
  assert.match(notice, /aria-labelledby="interest-coverage-heading"/);
  assert.match(notice, /<h2 id="interest-coverage-heading"/);
  assert.match(notice, /<ul[^>]*break-words/); // long sentences wrap on a phone
  assert.doesNotMatch(notice, /whitespace-nowrap|min-w-\[|w-\[\d|overflow-x/);
  assert.doesNotMatch(notice, /<details|DisclosureSection/); // never tucked away

  // Traveler view only, above the itinerary, from the helper
  const developerStart = page.indexOf('{mode === "developer" ? (');
  const travelerStart = page.indexOf("{/* ---- Traveler view", developerStart);
  const travelerBlock = page.slice(travelerStart, page.indexOf("</main>", travelerStart));
  const at = travelerBlock.indexOf("<InterestCoverageNotice");
  assert.ok(at > 0 && at < travelerBlock.indexOf('id="draft-itinerary"'));
  assert.match(travelerBlock, /<InterestCoverageNotice\s+notices=\{travelerInterestCoverage\(result\.validationReport\)\.notices\}/);
  assert.equal(page.slice(developerStart, travelerStart).includes("<InterestCoverageNotice"), false);

  // the banner is told what is known about coverage
  assert.match(page, /coverage=\{travelerInterestCoverage\(result\.validationReport\)\.state\}/);
  assert.match(componentSource("UserModeReadinessBanner"), /travelerReadiness\(validationStatus, severity, coverage\)/);
});
