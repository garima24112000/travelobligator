// Final Batch 1: estimated visit times, must-visit statuses, English names and
// internal planning notes in Traveler view. Run with `npm test`.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { hasPlannerWording } from "../lib/display-labels.ts";
import {
  travelerDayScheduleNote,
  travelerMustVisitLines,
  travelerPlaceName,
  travelerVisitTimeLine,
} from "../lib/traveler-view.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const page = readFileSync(join(root, "app", "page.tsx"), "utf8");
const IMPLEMENTATION_WORDS = /provider|configured|inventory|grounded|OpenStreetMap|Geoapify|_/i;

const stop = (status, extra = {}) => ({
  start_time: "09:30", end_time: "11:30", visit_time_status: status, arrival_wait_minutes: 0, ...extra,
});

test("a visit time is always worded as an estimate and never as 'open'", () => {
  const line = travelerVisitTimeLine(stop("fits_listed_hours"));
  assert.deepEqual(line, { text: "Estimated visit: about 09:30–11:30.", attention: false });
  assert.match(travelerVisitTimeLine(stop("fits_listed_hours", { arrival_wait_minutes: 30 })).text, /waiting about 30 min/);
  for (const status of ["fits_listed_hours", "hours_unknown", "outside_listed_hours", "not_timed"]) {
    const text = travelerVisitTimeLine(stop(status)).text;
    assert.doesNotMatch(text, /\bis open\b|guaranteed|verified/i, status);
    assert.doesNotMatch(text, IMPLEMENTATION_WORDS, status);
  }
});

test("hours that leave no time need attention; an untimed stop says why; older plans show nothing", () => {
  assert.equal(travelerVisitTimeLine(stop("outside_listed_hours")).attention, true);
  const untimed = travelerVisitTimeLine(stop("not_timed", { start_time: null, end_time: null }));
  assert.match(untimed.text, /No time estimated: the travel before this stop could not be checked/);
  assert.equal(travelerVisitTimeLine({ start_time: null, end_time: null, visit_time_status: null }), null);
});

test("the day note states the assumptions and a long day", () => {
  const note = travelerDayScheduleNote({
    window_start: "09:30", window_end: "18:00", status: "partly_timed", ends_at: "19:10",
    exceeds_window: true, untimed_reason: "travel_not_verified",
  });
  assert.match(note, /Times are estimates for a day from 09:30 to 18:00/);
  assert.match(note, /checked travel times only/);
  assert.match(note, /could not be checked/);
  assert.match(note, /until about 19:10/);
  assert.equal(travelerDayScheduleNote(null), null);
});

test("only must-visits that need attention are listed; a scheduled one never as missing", () => {
  const entry = (term, status, extra = {}) => ({
    term, status, place_name: null, day_number: 2, distance_km: null, drive_minutes: null, reason: null, ...extra,
  });
  const lines = travelerMustVisitLines([
    entry("On Plan", "scheduled"),
    entry("Far Site", "scheduled_excursion", { distance_km: 33.4, drive_minutes: 30 }),
    entry("Too Far", "outside_destination_not_feasible", { distance_km: 140 }),
    entry("Unseen", "outside_destination_unverified"),
    entry("Nowhere", "unresolved"),
  ]);
  const text = lines.map((line) => line.text).join(" | ");
  assert.doesNotMatch(text, /On Plan/);
  assert.match(text, /Far Site is outside the city, about 33 km away, on day 2; about 30 min each way by road/);
  assert.match(text, /not arranged or checked/);
  assert.match(text, /Too Far is outside the city, about 140 km away/);
  assert.match(text, /No matching place was found for Nowhere/);
  assert.doesNotMatch(text, IMPLEMENTATION_WORDS);
  assert.deepEqual(travelerMustVisitLines(undefined), []);
});

test("an English name from the record is shown first, with the original kept", () => {
  assert.equal(travelerPlaceName({ name: "Galleria Vecchia", display_name: "Old Gallery" }), "Old Gallery (Galleria Vecchia)");
  assert.equal(travelerPlaceName({ name: "Old Gallery", display_name: null }), "Old Gallery");
  assert.equal(travelerPlaceName({ name: "Old Gallery", display_name: "Old Gallery" }), "Old Gallery");
});

test("internal planning notes are planner wording", () => {
  assert.equal(hasPlannerWording("Expanded the schedule with a third stop to match the expected pace."), true);
  assert.equal(hasPlannerWording("A quiet morning among the old collections."), false);
});

test("Traveler view renders the new rows and filters planning notes", () => {
  assert.match(page, /<VisitTimeRow experience=\{experience\} mode=\{mode\} \/>/);
  assert.match(page, /<DayScheduleNote day=\{day\} \/>/);
  assert.match(page, /<MustVisitNotice report=\{result\.validationReport\} \/>/);
  assert.match(page, /\.filter\(\(warning\) => !hasPlannerWording\(warning\)\)/);
  assert.match(page, /travelerPlaceName\(experience\)/);
});
