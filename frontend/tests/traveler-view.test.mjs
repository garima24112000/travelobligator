// Section 2 (Traveler UX polish) frontend regression tests. Run with
// `npm test` (Node's built-in test runner; no test framework dependency).
//
// Two kinds of check, as in plan-insights.test.mjs:
//   1. unit tests of the pure view model in lib/traveler-view.ts and the
//      wording helpers in lib/display-labels.ts -- these produce the exact
//      strings Traveler view renders;
//   2. structure tests of app/page.tsx: that Traveler view is wired to those
//      helpers and does not print the raw backend values itself.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  hasPlannerWording,
  travelerProse,
  travelerText,
} from "../lib/display-labels.ts";
import {
  GENERIC_LOADING_STEPS,
  GETTING_AROUND_DISCLAIMER,
  GETTING_AROUND_HEADING,
  HOLIDAY_NOTICE,
  LOADING_STEP_ROTATION_SECONDS,
  WEATHER_NOTICE,
  formatTravelDistance,
  formatTravelDuration,
  genericLoadingStep,
  gettingAroundAdvisory,
  legMode,
  loadingPatienceNote,
  loadingStageLabel,
  movementModeLabel,
  travelerErrorMessage,
  travelerLegLine,
  travelerMovementLine,
  travelerReadiness,
  travelerReviewNotices,
  travelerRouteCoverageNote,
  tripSummarySourceNote,
} from "../lib/traveler-view.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const page = readFileSync(join(root, "app", "page.tsx"), "utf8");

const developerStart = page.indexOf('{mode === "developer" ? (');
const travelerStart = page.indexOf("{/* ---- Traveler view", developerStart);
assert.ok(developerStart > 0 && travelerStart > developerStart, "result view blocks not found in page.tsx");
const travelerBlock = page.slice(travelerStart, page.indexOf("</main>", travelerStart));

/** The source of one top-level `function Name(` in page.tsx. */
function componentSource(name) {
  const start = page.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `${name} not found in page.tsx`);
  const next = page.indexOf("\nfunction ", start + 1);
  return page.slice(start, next === -1 ? undefined : next);
}

// Wording that must never reach a traveler.
const INTERNAL_WORDING = [
  /AI itinerary reasoning/i,
  /\brepair/i,
  /fallback pass/i,
  /\bcandidate/i,
  /provider identity/i,
  /\bgrounding\b/i,
  /\breplacement/i,
  /route[- ]burden/i,
  /deterministic/i,
  /needs_review/,
  /provider-backed/i,
  /[a-z]+_[a-z_]+/,
  /\b[A-Z]{2,}_[A-Z_]+\b/,
];

function assertNoInternalWording(text, label = text) {
  for (const pattern of INTERNAL_WORDING) {
    assert.doesNotMatch(text, pattern, `${label} exposes internal wording (${pattern})`);
  }
}

const buffer = (overrides = {}) => ({
  from_experience_id: "a",
  to_experience_id: "b",
  status: "success",
  route_duration_seconds: 720,
  route_distance_meters: 900,
  ...overrides,
});

const routeReport = (mode, status = "success") => ({
  status: "success",
  legs: [{ from_experience_id: "a", to_experience_id: "b", status, mode }],
});

// -- movement mode -------------------------------------------------------------------

test("a walking leg renders as Walk with duration and distance", () => {
  assert.equal(travelerLegLine(buffer(), routeReport("walk")), "Walk · 12 min · 0.9 km");
  // a routed leg stored before modes existed was a walking route
  assert.equal(travelerLegLine(buffer(), routeReport(undefined)), "Walk · 12 min · 0.9 km");
  assert.equal(travelerLegLine(buffer(), routeReport(null)), "Walk · 12 min · 0.9 km");
});

test("a driving leg renders as Vehicle transfer, never as a named service", () => {
  const line = travelerLegLine(
    buffer({ route_duration_seconds: 1080, route_distance_meters: 8400 }),
    routeReport("drive"),
  );
  assert.equal(line, "Vehicle transfer · 18 min · 8.4 km");
  assert.doesNotMatch(line, /taxi|uber|rental|driver|\bcar\b|cab/i);
});

test("the raw mode value and planner terms are never exposed", () => {
  for (const mode of ["walk", "drive", "routing_drive", "DRIVE", "transit", "", null, undefined]) {
    const label = movementModeLabel(mode);
    assert.ok(["Walk", "Vehicle transfer", "Travel"].includes(label), `unexpected label ${label}`);
    const line = travelerMovementLine({ mode, durationSeconds: 600, distanceMeters: 1500 });
    assert.doesNotMatch(line, /routing_drive|\bdrive\b|\bwalk\b|mode|adapt/);
    assertNoInternalWording(line);
  }
  // an unknown mode is neutral, not guessed
  assert.equal(movementModeLabel("routing_drive"), "Travel");
  // no successfully routed leg in the route report: the mode is unknown
  assert.equal(legMode(routeReport("drive", "failed"), "a", "b"), null);
  assert.equal(legMode(null, "a", "b"), null);
  assert.equal(travelerLegLine(buffer(), null), "Travel · 12 min · 0.9 km");
});

test("durations and distances are traveler-friendly, never raw seconds or metres", () => {
  assert.equal(formatTravelDuration(720), "12 min");
  assert.equal(formatTravelDuration(3600), "1 h");
  assert.equal(formatTravelDuration(3900), "1 h 5 min");
  assert.equal(formatTravelDuration(20), "under 1 min");
  assert.equal(formatTravelDistance(900), "0.9 km");
  assert.equal(formatTravelDistance(8400), "8.4 km");
  assert.equal(formatTravelDistance(20), "under 0.1 km");
  assert.equal(formatTravelDistance(123456), "123 km");
  for (const bad of [null, undefined, Number.NaN, -5, Infinity]) {
    assert.equal(formatTravelDuration(bad), null);
    assert.equal(formatTravelDistance(bad), null);
  }
});

test("only the figures that exist are rendered", () => {
  assert.equal(
    travelerMovementLine({ mode: "walk", durationSeconds: 720, distanceMeters: null }),
    "Walk · 12 min",
  );
  assert.equal(
    travelerMovementLine({ mode: "drive", durationSeconds: null, distanceMeters: 8400 }),
    "Vehicle transfer · 8.4 km",
  );
  // nothing factual to show: no row at all
  assert.equal(travelerMovementLine({ mode: "walk", durationSeconds: null, distanceMeters: null }), null);
  for (const status of ["failed", "unavailable", "not_connected", "not_computable"]) {
    assert.equal(travelerLegLine(buffer({ status }), routeReport("walk")), null);
  }
  assert.equal(travelerLegLine(null, routeReport("walk")), null);
});

test("Traveler movement rows come from the view model, not from raw fields", () => {
  const row = componentSource("MovementRow");
  assert.match(row, /travelerLine/);
  assert.doesNotMatch(row, /\.mode\b|mode_adaptation|walking_distance/);
  assert.match(page, /travelerLegLine\(movement, result\.routeFeasibilityReport\)/);
  assert.doesNotMatch(page, /mode_adaptation_attempted|walking_duration_seconds/);
});

test("partial or missing route data is one calm sentence", () => {
  assert.equal(travelerRouteCoverageNote({ status: "success" }), null);
  assert.match(travelerRouteCoverageNote({ status: "partial" }), /shown where available/);
  for (const report of [null, { status: "failed" }, { status: "not_connected" }, { status: "unavailable" }]) {
    const note = travelerRouteCoverageNote(report);
    assert.match(note, /not available for this trip/);
    assertNoInternalWording(note);
    assert.doesNotMatch(note, /provider|not connected|failed/i);
  }
});

// -- getting around ----------------------------------------------------------------------

test("the Getting around advisory is shown once, with its heading and disclaimer", () => {
  assert.equal(GETTING_AROUND_HEADING, "Getting around");
  assert.equal(
    GETTING_AROUND_DISCLAIMER,
    "General AI guidance about the city, not based on this itinerary's route data.",
  );
  assert.equal(
    gettingAroundAdvisory({ getting_around_advisory: "  Most visitors use the metro and walk.  " }),
    "Most visitors use the metro and walk.",
  );
  assert.equal(travelerBlock.split("<GettingAroundSection").length - 1, 1);
  // next to the trip summary, before the day cards
  const advisory = travelerBlock.indexOf("<GettingAroundSection");
  assert.ok(advisory > travelerBlock.indexOf("<ItineraryNarrativeSummarySection"));
  assert.ok(advisory < travelerBlock.indexOf("{dayWiseItinerarySection}"));
  // the summary card no longer carries its own copy
  assert.doesNotMatch(componentSource("ItineraryNarrativeSummarySection"), /getting_around|GETTING_AROUND/);
});

test("the getting-around profile is never displayed", () => {
  assert.doesNotMatch(page, /getting_around_profile/);
  const section = componentSource("GettingAroundSection");
  assert.doesNotMatch(section, /profile\b(?! the advisory)/);
  assert.match(section, /GETTING_AROUND_HEADING/);
  assert.match(section, /GETTING_AROUND_DISCLAIMER/);
});

test("an absent advisory renders nothing and creates no placeholder", () => {
  for (const report of [
    null,
    undefined,
    {},
    { getting_around_advisory: null },
    { getting_around_advisory: "" },
    { getting_around_advisory: "   " },
  ]) {
    assert.equal(gettingAroundAdvisory(report), null);
  }
  assert.match(componentSource("GettingAroundSection"), /if \(!advisory\) return null;/);
});

test("the advisory survives a fallback summary and a missing summary", () => {
  const advisory = "Most visitors use the metro and walk.";
  // narrator fell back: status describes the failed AI attempt
  assert.equal(
    gettingAroundAdvisory({
      status: "failed",
      narrative_source: "deterministic_fallback",
      summary: "A 3-day trip.",
      getting_around_advisory: advisory,
    }),
    advisory,
  );
  // no summary at all
  assert.equal(
    gettingAroundAdvisory({ status: "failed", summary: null, getting_around_advisory: advisory }),
    advisory,
  );
  // the section does not gate on the narrator's status / summary
  assert.doesNotMatch(componentSource("GettingAroundSection"), /narrativeIsRenderable|\.summary|\.status/);
});

// -- narrator fallback ----------------------------------------------------------------------

test("a narrator fallback still looks like a successful itinerary", () => {
  const note = tripSummarySourceNote({ narrative_source: "deterministic_fallback" });
  assert.doesNotMatch(note, /fail|unavailable|error|could not|couldn't|fallback|deterministic|\bAI\b/i);
  assertNoInternalWording(note);
  assert.match(tripSummarySourceNote({ narrative_source: "ai" }), /AI-written/);

  const summary = componentSource("ItineraryNarrativeSummarySection");
  // no error styling and no alert role on the summary card
  assert.doesNotMatch(summary, /role="alert"|border-red|text-red|bg-red/);
  // the failure sentence is Developer-view only
  assert.match(summary, /mode === "developer" && fallback/);
  // the narrator's own message / failure kind is never printed in Traveler view
  assert.doesNotMatch(summary, /report\.message|failure_kind/);
  assert.doesNotMatch(travelerBlock, /failure_kind|ItineraryNarrativeDiagnosticSection/);
});

// -- internal wording ------------------------------------------------------------------------

test("planner terminology is rewritten in backend sentences", () => {
  const samples = [
    "AI itinerary reasoning rationale for this day: A relaxed start in the old town.",
    "5 provider-backed candidate(s) were found, but none were quality-approved for scheduling.",
    "No remaining candidate attractions were available for this day.",
    "The route burden repair replaced one stop with a replacement place.",
    "A fallback pass filled the remaining slots after grounding.",
    "Each place keeps its provider identity from the deterministic path.",
    "Pending feedback is available for deterministic regeneration.",
    "Readiness is needs_review because of INSUFFICIENT_VERIFIED_INVENTORY.",
    "Provider-backed weather data is available for this trip.",
    "Narrative source: deterministic_fallback.",
  ];
  for (const sample of samples) {
    assertNoInternalWording(travelerText(sample), sample);
  }
  assert.equal(
    travelerText("Provider-backed weather data is available for this trip."),
    "Weather data is available for this trip.",
  );
  // a place name is left alone
  assert.match(travelerText("Bike Repair Café is near the Candidate Hall."), /Bike Repair Café/);
});

test("AI prose that describes planner operations is dropped, clean prose is kept", () => {
  for (const prose of [
    "Moved Belém Tower from day 2 to day 1 to reduce the route burden.",
    "A replacement was chosen after the repair.",
    "Selected by AI itinerary reasoning from the candidate pool.",
    "This day came from the fallback pass.",
    "Grounding kept each provider identity.",
    "The deterministic order was kept (needs_review).",
  ]) {
    assert.equal(hasPlannerWording(prose), true, prose);
    assert.equal(travelerProse(prose), null, prose);
  }
  const clean = "A relaxed morning in Alfama followed by an afternoon by the river.";
  assert.equal(hasPlannerWording(clean), false);
  assert.equal(travelerProse(clean), clean);
  assert.equal(travelerProse(""), null);
  assert.equal(travelerProse(null), null);

  // the day note falls back to the backend's factual day summary
  const note = componentSource("DailyNarrativeNote");
  assert.match(note, /travelerProse\(daily\.narrative\) \?\? \(factualSummary/);
  assert.match(page, /factualSummary=\{day\.goal \?\? null\}/);
  assert.match(componentSource("ItineraryNarrativeSummarySection"), /travelerProse\(report\.summary\)/);
});

// -- readiness ---------------------------------------------------------------------------------

test("raw readiness values and outcome codes are never shown", () => {
  for (const status of ["ready", "needs_review", "blocked", null, undefined, "something_new"]) {
    const readiness = travelerReadiness(status);
    for (const text of [readiness.label, readiness.message]) {
      assertNoInternalWording(text);
      assert.doesNotMatch(text, /needs review|\bblocked\b|\bready\b(?! to use)/i);
    }
  }
  assert.equal(travelerReadiness("ready").tone, "ok");
  assert.equal(travelerReadiness("needs_review").tone, "review");
  assert.equal(travelerReadiness("blocked").tone, "blocked");
  assert.equal(travelerReadiness(null).tone, "unknown");

  const notices = travelerReviewNotices({
    blocking_codes: ["INSUFFICIENT_VERIFIED_INVENTORY"],
    review_codes: ["FEASIBILITY", "LONG_TRAVEL_DAY", "UNDERFILLED_PLAN", "SOME_FUTURE_CODE", "WEATHER"],
  });
  assert.equal(notices.length, 3);
  notices.forEach((notice) => assertNoInternalWording(notice));

  const banner = componentSource("UserModeReadinessBanner");
  assert.match(banner, /travelerReadiness\(validationStatus\)/);
  assert.doesNotMatch(banner, /\{validationStatus\}|review_codes|blocking_codes/);
  // Traveler view never prints the codes; the inventory notice keeps its own explanation
  assert.doesNotMatch(travelerBlock, /review_codes|blocking_codes|readiness_status/);
  assert.match(travelerBlock, /<TravelerInventoryNotice/);
});

test("weather and holiday notices are traveler-friendly", () => {
  assert.equal(WEATHER_NOTICE, "Weather information may be unavailable for these dates.");
  assert.equal(HOLIDAY_NOTICE, "Holiday information could not be fully verified.");
  assert.deepEqual(travelerReviewNotices({ review_codes: ["WEATHER", "HOLIDAYS"] }), [
    WEATHER_NOTICE,
    HOLIDAY_NOTICE,
  ]);
  // derived from missing data as well, once each
  assert.deepEqual(
    travelerReviewNotices(
      { review_codes: ["WEATHER"] },
      { weather: null, holiday: { data_status: "unavailable" } },
    ),
    [WEATHER_NOTICE, HOLIDAY_NOTICE],
  );
  assert.deepEqual(
    travelerReviewNotices(null, {
      weather: { daily_weather: [], data_status: "live" },
      holiday: { data_status: "live" },
    }),
    [WEATHER_NOTICE],
  );
  // data that was returned produces no notice
  assert.deepEqual(
    travelerReviewNotices(
      { review_codes: [] },
      { weather: { daily_weather: [{}], data_status: "live" }, holiday: { data_status: "live" } },
    ),
    [],
  );
  assert.deepEqual(travelerReviewNotices(null), []);
});

// -- loading ---------------------------------------------------------------------------------------

test("loading copy is generic and never a fake percentage", () => {
  assert.deepEqual([...GENERIC_LOADING_STEPS], [
    "Finding verified places",
    "Checking your must-visits",
    "Building the day-by-day plan",
    "Checking travel between stops",
    "Finishing your itinerary",
  ]);
  assert.equal(genericLoadingStep(0), GENERIC_LOADING_STEPS[0]);
  assert.equal(genericLoadingStep(LOADING_STEP_ROTATION_SECONDS), GENERIC_LOADING_STEPS[1]);
  // it rotates rather than "completing"
  assert.equal(
    genericLoadingStep(LOADING_STEP_ROTATION_SECONDS * GENERIC_LOADING_STEPS.length),
    GENERIC_LOADING_STEPS[0],
  );
  for (const seconds of [0, 15, 40, 70, 120, 600]) {
    const copy = `${genericLoadingStep(seconds)} ${loadingPatienceNote(seconds)}`;
    assert.doesNotMatch(copy, /%|\bdone\b|complete|finished/i);
    assertNoInternalWording(copy);
  }
  assert.match(loadingPatienceNote(120), /still running/);

  // a stage label is only ever one the backend reported; unknown keys give none
  assert.equal(loadingStageLabel("destination_context"), "Finding verified places");
  assert.equal(loadingStageLabel("some_new_stage"), null);
  assert.equal(loadingStageLabel(null), null);

  const loading = componentSource("TravelGenerationLoading");
  // the generic line is labelled as generic and only shown without a real stage
  assert.match(loading, /!reportedStageLabel && \(/);
  assert.match(loading, /What goes into your plan:/);
  // announced politely; no percentage text is ever rendered
  assert.match(loading, /role="status"/);
  assert.doesNotMatch(loading, /\}%\s*</);
  // without a backend percentage the bar is indeterminate
  assert.match(loading, /displayProgress === null \?/);
});

test("a second generation cannot start while one is running", () => {
  const handler = page.slice(page.indexOf("async function handlePlanTrip()"));
  const guard = handler.indexOf("if (generationInFlightRef.current || isLoading || isLoadingExisting) return;");
  const firstRequest = handler.indexOf("await createTrip(");
  assert.ok(guard > 0 && guard < firstRequest, "the in-flight guard must run before any request");
  assert.ok(handler.indexOf("generationInFlightRef.current = true;") < firstRequest);
  // released when the attempt ends, success or failure
  const release = handler.indexOf("generationInFlightRef.current = false;");
  assert.ok(release > handler.indexOf("} finally {"), "the guard must be released in finally");
  // the submit button and every other trip-opening control are disabled meanwhile
  assert.match(page, /type="submit"\s+disabled=\{isLoading \|\| isLoadingExisting\}/);
  assert.match(page, /aria-busy=\{isLoading\}/);
  // generation is only ever started by the form or by an explicit "Try again"
  assert.equal(page.split("handlePlanTrip()").length - 1, 3);
});

// -- errors ------------------------------------------------------------------------------------------

test("request errors are actionable and free of internal detail", () => {
  const cases = [
    { code: "BACKEND_UNREACHABLE", status: 0, message: "The server did not respond.", operation: "generate" },
    { code: "STAGE_FAILED", status: 500, message: "Trip plan generation failed unexpectedly. Check GET /trips/{trip_id}/generation-progress for which stage was running when it failed.", operation: "generate" },
    { code: "JOB_INTERRUPTED", status: 500, message: "interrupted", operation: "generate" },
    { code: null, status: 500, message: "Traceback (most recent call last): psycopg.OperationalError", operation: "load" },
    { code: null, status: 502, message: "geoapify_places adapter failed: HTTP 429 failure_kind=rate_limited", operation: "generate" },
    { code: "PLAN_NOT_GENERATED", status: 409, message: "Trip 'trip_abc123' exists, but its plan has not been generated yet.", operation: "load" },
    { code: "TRIP_NOT_FOUND", status: 404, message: "Trip 'trip_abc123' was not found.", operation: "load" },
    { code: "CONCURRENT_UPDATE", status: 409, message: "changed by another request", operation: "feedback" },
    { code: "SOMETHING_ELSE", status: 400, message: "GROQ_API_KEY is not configured", operation: "generate" },
    { code: null, status: 429, message: "rate limited", operation: "generate" },
  ];
  for (const input of cases) {
    const { message } = travelerErrorMessage(input);
    assert.ok(message.length > 0 && message.length < 240);
    assert.doesNotMatch(message, /trip_abc123|Traceback|psycopg|geoapify|groq|failure_kind|API_KEY|\/trips\/|HTTP \d|stage/i);
    assert.doesNotMatch(message, /[A-Z]{3,}_[A-Z_]+|[a-z]+_[a-z_]+/);
  }
  // retry only where repeating the action is safe and may help
  assert.equal(travelerErrorMessage(cases[0]).retryable, true);
  assert.equal(travelerErrorMessage(cases[1]).retryable, true);
  assert.equal(travelerErrorMessage(cases[6]).retryable, false);
  assert.equal(
    travelerErrorMessage({ code: "FORBIDDEN", status: 403, message: "x", operation: "load" }).retryable,
    false,
  );
  // a cold start is described as temporary
  assert.match(travelerErrorMessage(cases[0]).message, /starting up|try again/i);
  // an expired session asks the traveler to log in again
  assert.match(
    travelerErrorMessage({ code: "AUTHENTICATION_REQUIRED", status: 401, message: "x", operation: "load" }).message,
    /log in again/,
  );
  // a plain, actionable backend sentence (a date problem) is kept
  assert.equal(
    travelerErrorMessage({
      code: "VALIDATION_ERROR",
      status: 422,
      message: "End date must be on or after the start date.",
      operation: "create",
    }).message,
    "End date must be on or after the start date.",
  );
  assert.match(
    travelerErrorMessage({ code: "DESTINATION_UNRESOLVED", status: 500, message: "x", operation: "generate" }).message,
    /city and country/,
  );
});

test("the trip error box is announced, retryable where safe, and reload shows a loading state", () => {
  assert.match(page, /\{error && \(\s*<div\s+role="alert"/);
  assert.match(page, /\{retryAction && \(/);
  assert.match(page, /Try again/);
  // the expired-session reason reaches the login screen
  assert.match(page, /setAuthError\(SESSION_ENDED_MESSAGE\)/);
  // a saved trip being fetched after a reload is never a blank area
  assert.match(page, /\{isLoadingExisting && !result && \(\s*<p\s+role="status"/);
  // only the trip id is persisted across reloads (never plan content)
  assert.doesNotMatch(page, /localStorage\.setItem\((?!VIEW_MODE_STORAGE_KEY)/);
  assert.doesNotMatch(page, /sessionStorage\.(setItem|getItem)/);
});

// -- long / local-script place names ---------------------------------------------------------------

test("long and local-script place names pass through unchanged and wrap safely", () => {
  const names = [
    "Mosteiro dos Jerónimos",
    "जंतर मंतर (Jantar Mantar), जयपुर",
    "مسجد الحسن الثاني Hassan II Mosque",
    "清水寺 Kiyomizu-dera",
    "Llanfairpwllgwyngyllgogerychwyrndrobwllllantysiliogogogoch",
    "Museu_Nacional do Azulejo",
  ];
  // names are rendered directly, never passed through the sentence rewriter
  const card = componentSource("ScheduledExperienceCard");
  assert.match(card, /<bdi>\{experience\.name\}<\/bdi>/);
  assert.doesNotMatch(card, /travelerText\(experience\.name\)|humanizeIdentifier\(experience\.name\)/);
  assert.match(card, /break-words[^"]*\[overflow-wrap:anywhere\]/);
  assert.match(card, /min-w-0 flex-1/);
  assert.match(componentSource("RestaurantSuggestionCard"), /<bdi>\{restaurant\.name\}<\/bdi>/);

  // a name inside a movement line or a notice keeps every character
  for (const name of names) {
    assert.equal(hasPlannerWording(name) && !name.includes("_"), false);
    const sentence = `Could not check the route to ${name} today.`;
    if (!name.includes("_")) assert.ok(travelerText(sentence).includes(name), name);
  }
  // movement lines carry no place name at all, so they can never overflow on one
  assert.equal(travelerLegLine(buffer(), routeReport("walk")).length < 40, true);
});
