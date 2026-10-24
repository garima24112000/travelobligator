// M2 Phase 3: per-stop venue evidence in both views. Run with `npm test`.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { VENUE_HOURS_NOT_CHECKED, findVenueEvidence, travelerVenueLine } from "../lib/traveler-view.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const page = readFileSync(join(root, "app", "page.tsx"), "utf8");
const IMPLEMENTATION_WORDS = /provider|configured|inventory|OpenStreetMap|Geoapify|record status|venue_/i;

const entry = (status, extra = {}) => ({ status, hours_on_day: [], checked_on: null, ...extra });

test("listed hours are restated, never promised", () => {
  const line = travelerVenueLine(entry("listed_open_on_day", { hours_on_day: ["10:00–18:00"], checked_on: "2024-05" }));
  assert.equal(line.attention, false);
  assert.match(line.text, /Listed opening hours this day: 10:00–18:00/);
  assert.match(line.text, /Hours can change; check before you go/);
  assert.match(line.text, /Last confirmed in the place data: 2024-05/);
  assert.doesNotMatch(line.text, /\bis open\b|guaranteed|verified/i);
});

test("a stated closure or end of operation needs attention", () => {
  for (const status of ["listed_closed_on_day", "reported_not_operating", "reported_not_yet_open"]) {
    const line = travelerVenueLine(entry(status));
    assert.equal(line.attention, true, status);
    assert.doesNotMatch(line.text, IMPLEMENTATION_WORDS);
  }
});

test("missing evidence says the hours were not checked, never that the place is open", () => {
  for (const status of ["hours_not_listed", "hours_not_understood", "hours_need_holiday_data", "something_new"]) {
    const line = travelerVenueLine(entry(status));
    assert.deepEqual(line, { text: VENUE_HOURS_NOT_CHECKED, attention: false });
  }
  assert.equal(travelerVenueLine(null), null);
  assert.equal(findVenueEvidence({ venue_evidence: null }, "e1"), null);
  assert.equal(findVenueEvidence(undefined, "e1"), null);
});

test("both views render the stop's evidence row", () => {
  assert.match(page, /<VenueEvidenceRow\s+entry=\{findVenueEvidence\(result\.validationReport, experience\.experience_id\)\}/);
  assert.match(page, /Venue evidence: \{entry\.status\}/); // Developer view: the raw status and source
  assert.match(page, /travelerVenueLine\(entry\)/); // Traveler view: plain words
});
