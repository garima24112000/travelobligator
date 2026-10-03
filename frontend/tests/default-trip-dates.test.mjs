// Default create-trip dates. Run with `npm test` (Node's built-in test
// runner; no test framework dependency).
//
// The timezone test re-runs the module in child processes with different
// `TZ` values, because a process's timezone cannot be changed reliably once
// it has started.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

import {
  DEFAULT_TRIP_NIGHTS,
  DEFAULT_TRIP_START_OFFSET_DAYS,
  addLocalDays,
  defaultTripDates,
  formatLocalDate,
} from "../lib/default-trip-dates.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const page = readFileSync(join(root, "app", "page.tsx"), "utf8");
const moduleSource = readFileSync(join(root, "lib", "default-trip-dates.ts"), "utf8");

/** Whole calendar days between two `YYYY-MM-DD` strings (pure calendar arithmetic). */
function calendarDaysBetween(from, to) {
  const utc = (value) => {
    const [year, month, day] = value.split("-").map(Number);
    return Date.UTC(year, month - 1, day);
  };
  return Math.round((utc(to) - utc(from)) / 86_400_000);
}

test("defaults are in the future relative to a supplied today", () => {
  for (const today of [
    new Date(2026, 9, 3, 0, 0, 0),
    new Date(2026, 9, 3, 12, 0, 0),
    new Date(2026, 9, 3, 23, 59, 59),
    new Date(2031, 0, 1, 8, 30),
  ]) {
    const { start_date, end_date } = defaultTripDates(today);
    const todayText = formatLocalDate(today);
    assert.ok(start_date > todayText, `${start_date} is not after ${todayText}`);
    assert.ok(end_date > todayText);
    assert.equal(calendarDaysBetween(todayText, start_date), DEFAULT_TRIP_START_OFFSET_DAYS);
  }
  assert.deepEqual(defaultTripDates(new Date(2026, 9, 3, 15, 0)), {
    start_date: "2026-10-10",
    end_date: "2026-10-12",
  });
  // called without an argument it uses the real current date
  assert.ok(defaultTripDates().start_date > formatLocalDate(new Date()));
});

test("the end date is after the start date: 3 calendar days / 2 nights", () => {
  assert.equal(DEFAULT_TRIP_NIGHTS, 2);
  // every day of a leap year and a normal year
  for (const year of [2027, 2028]) {
    for (let dayOfYear = 0; dayOfYear < 366; dayOfYear += 1) {
      const { start_date, end_date } = defaultTripDates(new Date(year, 0, 1 + dayOfYear, 9));
      assert.ok(end_date > start_date);
      const nights = calendarDaysBetween(start_date, end_date);
      assert.equal(nights, 2, `${start_date} -> ${end_date}`);
      assert.equal(nights + 1, 3);
    }
  }
});

test("month and year boundaries roll over correctly", () => {
  const cases = [
    // [today, start, end]
    [new Date(2026, 9, 24), "2026-10-31", "2026-11-02"],
    [new Date(2026, 9, 28), "2026-11-04", "2026-11-06"],
    [new Date(2026, 11, 23), "2026-12-30", "2027-01-01"],
    [new Date(2026, 11, 28), "2027-01-04", "2027-01-06"],
    [new Date(2027, 1, 20), "2027-02-27", "2027-03-01"],
    // leap year: 29 February exists
    [new Date(2028, 1, 20), "2028-02-27", "2028-02-29"],
    [new Date(2028, 1, 22), "2028-02-29", "2028-03-02"],
  ];
  for (const [today, start, end] of cases) {
    assert.deepEqual(defaultTripDates(today), { start_date: start, end_date: end });
  }
  assert.equal(formatLocalDate(new Date(2027, 0, 5)), "2027-01-05");
  assert.equal(formatLocalDate(addLocalDays(new Date(2026, 11, 31), 1)), "2027-01-01");
});

test("a timezone offset cannot shift the date", () => {
  const moduleUrl = pathToFileURL(join(root, "lib", "default-trip-dates.ts")).href;
  // "Today" is given as local calendar fields, late in the evening and just
  // after midnight -- the two moments where a UTC conversion moves the date.
  const script = `
    const { defaultTripDates, formatLocalDate } = await import(${JSON.stringify(moduleUrl)});
    const out = [];
    for (const [hour, minute] of [[23, 30], [0, 15], [12, 0]]) {
      const today = new Date(2026, 9, 3, hour, minute);
      out.push({ today: formatLocalDate(today), ...defaultTripDates(today) });
    }
    console.log(JSON.stringify(out));
  `;
  const expected = { today: "2026-10-03", start_date: "2026-10-10", end_date: "2026-10-12" };
  for (const tz of [
    "UTC",
    "Pacific/Kiritimati", // UTC+14
    "Pacific/Pago_Pago", // UTC-11
    "Asia/Kolkata", // UTC+5:30
    "America/New_York",
    "Australia/Lord_Howe", // 30-minute daylight-saving shift
  ]) {
    const stdout = execFileSync(
      process.execPath,
      ["--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script],
      { env: { ...process.env, TZ: tz }, encoding: "utf8" },
    );
    for (const row of JSON.parse(stdout)) {
      assert.deepEqual(row, expected, `timezone ${tz}`);
    }
  }
  // a daylight-saving change inside the window does not lose or repeat a day
  const dst = execFileSync(
    process.execPath,
    [
      "--experimental-strip-types",
      "--no-warnings",
      "--input-type=module",
      "-e",
      `const { defaultTripDates } = await import(${JSON.stringify(moduleUrl)});
       console.log(JSON.stringify([
         defaultTripDates(new Date(2026, 9, 28, 23, 45)),
         defaultTripDates(new Date(2027, 2, 10, 0, 5)),
       ]));`,
    ],
    { env: { ...process.env, TZ: "America/New_York" }, encoding: "utf8" },
  );
  assert.deepEqual(JSON.parse(dst), [
    { start_date: "2026-11-04", end_date: "2026-11-06" },
    { start_date: "2027-03-17", end_date: "2027-03-19" },
  ]);
  // the implementation never converts through UTC
  assert.doesNotMatch(moduleSource.replace(/\/\/.*$/gm, ""), /toISOString|getUTC|Date\.UTC|toJSON/);
});

test("the form has no hardcoded dates and keeps what the user entered", () => {
  // no literal calendar date anywhere in the page
  assert.doesNotMatch(page, /["'`]\d{4}-\d{2}-\d{2}["'`]/);
  assert.doesNotMatch(page, /DEFAULT_TRIP_REQUEST/);
  assert.match(page, /\.\.\.defaultTripDates\(\),/);
  // initialised lazily, once per mount: a re-render never recomputes the dates
  assert.match(page, /useState<TripRequestInput>\(defaultTripRequest\);/);
  // defaults are only ever applied on mount and on logout's full reset
  assert.equal(page.split("defaultTripRequest()").length - 1, 2);
  const logout = page.slice(page.indexOf("async function handleLogout()"));
  assert.ok(logout.indexOf("setForm(defaultTripRequest());") > 0);
  assert.ok(
    logout.indexOf("setForm(defaultTripRequest());") < logout.indexOf("\n  async function "),
    "the only reset to defaults must be inside handleLogout",
  );
  // editing a date only spreads the existing form state
  assert.match(page, /setForm\(\{ \.\.\.form, start_date: event\.target\.value \}\)/);
  assert.match(page, /setForm\(\{ \.\.\.form, end_date: event\.target\.value \}\)/);
});
