// Trip request size limits in the form, and the traveller wording for a request the backend refused
// (HTTP 422 validation, HTTP 429 limits). Run with `npm test` (Node's built-in test runner).

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  MAX_ITEM_LENGTH,
  MAX_LIST_ITEMS,
  MAX_TRIP_DAYS,
  latestEndDate,
  tripLengthDays,
  tripRequestLimitProblem,
} from "../lib/trip-request-limits.ts";
import { travelerErrorMessage } from "../lib/traveler-view.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const repo = join(root, "..");
const page = readFileSync(join(root, "app", "page.tsx"), "utf8");
const backendConfig = readFileSync(join(repo, "backend", "app", "core", "config.py"), "utf8");

const request = (overrides = {}) => ({
  start_date: "2027-03-01",
  end_date: "2027-03-03",
  interests: ["food"],
  must_visit: [],
  constraints: [],
  ...overrides,
});

// -- the limits are the backend's documented defaults ------------------------------------------------

test("the form's limits are the backend's default settings", () => {
  const backendDefault = (field) => {
    const match = new RegExp(`${field}: int = Field\\(\\s*default=(\\d+)`).exec(backendConfig);
    assert.ok(match, `backend setting ${field} not found`);
    return Number(match[1]);
  };
  assert.equal(MAX_TRIP_DAYS, backendDefault("max_trip_days"));
  assert.equal(MAX_LIST_ITEMS, backendDefault("trip_request_max_list_items"));
  assert.equal(MAX_ITEM_LENGTH, backendDefault("trip_request_max_item_length"));
});

// -- trip length ------------------------------------------------------------------------------------

test("trip length counts the first and the last day", () => {
  assert.equal(tripLengthDays("2027-03-01", "2027-03-01"), 1);
  assert.equal(tripLengthDays("2027-03-01", "2027-03-07"), 7);
  assert.equal(tripLengthDays("2027-03-01", "2027-03-08"), 8);
  // month, year and leap-day boundaries are plain calendar arithmetic
  assert.equal(tripLengthDays("2027-12-29", "2028-01-04"), 7);
  assert.equal(tripLengthDays("2028-02-27", "2028-03-01"), 4);
  // a daylight-saving change inside the trip never changes the count
  assert.equal(tripLengthDays("2027-03-12", "2027-03-15"), 4);
  assert.equal(tripLengthDays("", "2027-03-01"), null);
  assert.equal(tripLengthDays("2027-02-30", "2027-03-01"), null);
});

test("a trip of exactly the maximum is accepted and one day more is refused", () => {
  assert.equal(tripRequestLimitProblem(request({ end_date: "2027-03-07" })), null);
  const problem = tripRequestLimitProblem(request({ end_date: "2027-03-08" }));
  assert.match(problem, /at most 7 days/);
  assert.match(problem, /earlier end date/);
  assert.match(tripRequestLimitProblem(request({ end_date: "2027-02-28" })), /on or after the start date/);
});

test("the end-date picker stops at the last allowed day", () => {
  assert.equal(latestEndDate("2027-03-01"), "2027-03-07");
  assert.equal(latestEndDate("2027-12-29"), "2028-01-04");
  assert.equal(latestEndDate(""), undefined);
  assert.equal(tripLengthDays("2027-03-01", latestEndDate("2027-03-01")), MAX_TRIP_DAYS);
  assert.match(page, /max=\{latestEndDate\(form\.start_date\)\}/);
});

// -- lists ------------------------------------------------------------------------------------------

test("lists are bounded in count and in entry length, and the message says what to change", () => {
  const many = Array.from({ length: MAX_LIST_ITEMS + 1 }, (_, index) => `place ${index}`);
  assert.equal(tripRequestLimitProblem(request({ must_visit: many.slice(0, MAX_LIST_ITEMS) })), null);
  assert.match(tripRequestLimitProblem(request({ must_visit: many })), /at most 20 must-visit places.*entered 21/);
  assert.match(tripRequestLimitProblem(request({ interests: many })), /at most 20 interests/);
  assert.match(tripRequestLimitProblem(request({ constraints: many })), /at most 20 constraints/);

  assert.equal(tripRequestLimitProblem(request({ interests: ["x".repeat(MAX_ITEM_LENGTH)] })), null);
  assert.match(
    tripRequestLimitProblem(request({ interests: ["x".repeat(MAX_ITEM_LENGTH + 1)] })),
    /interests can be at most 120 characters/,
  );
});

test("limit messages are plain sentences without field names or codes", () => {
  const problems = [
    tripRequestLimitProblem(request({ end_date: "2027-04-01" })),
    tripRequestLimitProblem(request({ must_visit: Array(30).fill("a") })),
    tripRequestLimitProblem(request({ constraints: ["y".repeat(500)] })),
  ];
  for (const problem of problems) {
    assert.ok(problem.length > 0 && problem.length < 240);
    assert.doesNotMatch(problem, /[a-z]+_[a-z_]+|[A-Z]{3,}_[A-Z_]+|422|validation/i);
  }
});

test("an over-limit request is refused in the form before anything is sent", () => {
  const handler = page.slice(page.indexOf("async function handlePlanTrip()"));
  const check = handler.indexOf("tripRequestLimitProblem(requestBody)");
  assert.ok(check > 0, "the form must check the limits");
  assert.ok(check < handler.indexOf("generationInFlightRef.current = true;"));
  assert.ok(check < handler.indexOf("createTrip(requestBody)"));
});

// -- wording for a refused request ------------------------------------------------------------------

test("a 422 from the backend is shown as the backend worded it, without its technical prefix", () => {
  const view = travelerErrorMessage({
    code: "VALIDATION_ERROR",
    status: 422,
    message:
      "Value error, A trip can be at most 7 days long, counting the first and the last day. Choose an earlier end date.",
    operation: "create",
  });
  assert.equal(
    view.message,
    "A trip can be at most 7 days long, counting the first and the last day. Choose an earlier end date.",
  );
  assert.equal(view.retryable, false);

  // a validation message that names internals is replaced, never shown
  const internal = travelerErrorMessage({
    code: "VALIDATION_ERROR",
    status: 422,
    message: "Value error, end_date must be on or after start_date",
    operation: "create",
  });
  assert.doesNotMatch(internal.message, /end_date|Value error/);
  assert.match(internal.message, /check the dates/);
  assert.equal(internal.retryable, false);
});

test("each 429 limit has its own wording and only a busy moment offers a retry", () => {
  const view = (code, message = "x") =>
    travelerErrorMessage({ code, status: 429, message, operation: "generate" });

  const user = view(
    "GENERATION_USER_LIMIT_REACHED",
    "You have reached the limit of 5 itineraries per 24 hours. Please try again later.",
  );
  assert.match(user.message, /limit of 5 itineraries per 24 hours/);
  assert.equal(user.retryable, false);
  // an unexpected backend sentence is never passed through
  assert.match(view("GENERATION_USER_LIMIT_REACHED", "owner_id user_123 over quota").message, /try again later/);

  const global = view("GENERATION_GLOBAL_LIMIT_REACHED");
  assert.match(global.message, /trip is saved/);
  assert.equal(global.retryable, false);

  const busy = view("GENERATION_CAPACITY_REACHED");
  assert.match(busy.message, /try again in a minute/);
  assert.equal(busy.retryable, true);

  const trips = view("TRIP_LIMIT_REACHED", "You have reached the limit of 20 saved trips for one account.");
  assert.match(trips.message, /20 saved trips/);
  assert.equal(trips.retryable, false);

  assert.equal(view("SIGNUP_LIMIT_REACHED").retryable, false);
  assert.match(view("TOO_MANY_ATTEMPTS").message, /wait a few minutes/);

  for (const code of [
    "GENERATION_USER_LIMIT_REACHED",
    "GENERATION_GLOBAL_LIMIT_REACHED",
    "GENERATION_CAPACITY_REACHED",
    "TRIP_LIMIT_REACHED",
    "SIGNUP_LIMIT_REACHED",
    "TOO_MANY_ATTEMPTS",
  ]) {
    const { message } = view(code);
    assert.ok(message.length > 0 && message.length < 240);
    assert.doesNotMatch(message, /[A-Z]{3,}_[A-Z_]+|[a-z]+_[a-z_]+|429|quota|provider/i);
  }
});

test("trying again reuses the trip that was already saved", () => {
  const handler = page.slice(page.indexOf("async function handlePlanTrip()"));
  assert.match(handler, /saved !== null && saved\.requestKey === requestKey/);
  assert.match(handler, /ungeneratedTripRef\.current = \{ tripId, requestKey \}/);
  // cleared once an itinerary exists, and whenever the session ends
  assert.ok(handler.indexOf("ungeneratedTripRef.current = null;") < handler.indexOf("setResult(await loadPlanResult(tripId));"));
  assert.ok(page.split("ungeneratedTripRef.current = null;").length - 1 >= 4);
});
