// Final Batch 2: controls are offered only where they do something, and say what they do.
// Run with `npm test`.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { keepButtonLabel, keepControlAvailable, keptPlacesNote } from "../lib/traveler-view.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const page = readFileSync(join(root, "app", "page.tsx"), "utf8");
const types = readFileSync(join(root, "lib", "types.ts"), "utf8");
const backendReview = readFileSync(join(root, "..", "backend", "app", "schemas", "ai_candidate_review.py"), "utf8");

// -- "Keep this place" ------------------------------------------------------------------------

test("a new keep is offered only where requested changes can be applied", () => {
  assert.equal(keepControlAvailable({ change_requests_applicable: false }), false);
  assert.equal(keepControlAvailable({ change_requests_applicable: true }), true);
  // unknown (an older readiness, or none) keeps the previous behaviour
  assert.equal(keepControlAvailable({ change_requests_applicable: null }), true);
  assert.equal(keepControlAvailable({}), true);
  assert.equal(keepControlAvailable(null), true);
  assert.equal(keepControlAvailable(undefined), true);
});

test("the keep button's accessible name says what a keep does, and promises nothing more", () => {
  const label = keepButtonLabel("Fixture Museum");
  assert.equal(label, "Keep this place: Fixture Museum. While a place is kept, no requested change is applied");
  // the visible text starts the accessible name (WCAG 2.5.3, label in name)
  assert.ok(label.startsWith("Keep this place"));
  assert.match(page, />\s*\{isSubmittingLock \? "Saving\.\.\." : "Keep this place"\}/);
  assert.doesNotMatch(label, /future regeneration|preserv|guarantee|will be kept/i);
});

test("the card hides only the NEW keep control; a stored keep stays visible and removable", () => {
  assert.match(page, /canMarkKeep=\{keepControlAvailable\(result\.regenerationReadiness\)\}/);
  assert.match(page, /\{\(activeLock \|\| canMarkKeep\) && \(/);
  assert.match(page, /aria-label=\{keepButtonLabel\(experience\.name\)\}/);
  assert.match(page, /aria-label=\{`Remove keep marker for \$\{experience\.name\}`\}/);
  // the old wording promised a preservation that never existed (an active keep blocks changes)
  assert.doesNotMatch(page, /\} for future regeneration`|Kept for future regeneration|stored for future regeneration/);
  assert.match(page, /data-testid="scheduled-stop"/);
});

test("the list of kept places never tells a traveler that removing a keep would let changes apply", () => {
  const unavailable = keptPlacesNote(false);
  assert.match(unavailable, /not applied/);
  assert.match(unavailable, /changes nothing/);
  assert.doesNotMatch(unavailable, /remove the keep first|stay as they are/);
  assert.match(keptPlacesNote(true), /remove the keep first/);
  assert.equal((page.match(/changesApplicable=\{keepControlAvailable\(result\.regenerationReadiness\)\}/g) ?? []).length, 2);
});

// -- the development-only promotion refresh ------------------------------------------------------

test("the promotion refresh is offered only when the backend says it exists", () => {
  assert.match(backendReview, /promotion_refresh_available: bool = True/);
  assert.match(types, /promotion_refresh_available\?: boolean;/);
  // a missing field never advertises the action
  assert.match(page, /aiCandidatePromotionRefreshAvailable: aiCandidateReview\.promotion_refresh_available === true/);
  assert.match(page, /refreshAvailable=\{result\.aiCandidatePromotionRefreshAvailable\}/);
  assert.match(page, /\{refreshAvailable \? \(/);
});

test("the refresh says it records no version", () => {
  assert.match(page, /It records no\s+new version/);
});

// -- POST /generate builds a trip's first plan only ------------------------------------------------

test("a trip that already has its plan is loaded, not reported as a failure", async () => {
  const { PLAN_ALREADY_GENERATED } = await import("../lib/traveler-view.ts");
  const backendCodes = readFileSync(join(root, "..", "backend", "app", "schemas", "errors.py"), "utf8");
  assert.equal(PLAN_ALREADY_GENERATED, "PLAN_ALREADY_GENERATED");
  assert.match(backendCodes, /PLAN_ALREADY_GENERATED = "PLAN_ALREADY_GENERATED"/);
  // only that code is absorbed; every other error still propagates
  assert.match(
    page,
    /if \(!\(err instanceof ApiRequestError && err\.code === PLAN_ALREADY_GENERATED\)\) throw err;\s+response = null;/,
  );
  assert.match(page, /if \(response !== null && isStartJobResponseData\(response\)\)/);
});
