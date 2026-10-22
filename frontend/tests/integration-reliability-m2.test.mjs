// M2 Phase 1 (core integration reliability) frontend regression tests. Run
// with `npm test` (Node's built-in test runner; no test framework dependency).
//
// Unit tests of the pure wording helpers in lib/traveler-view.ts plus
// structure tests of app/page.tsx (there is no DOM renderer in this repo).

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  CURRENCY_NOTICE,
  FLIGHTS_NOT_SHOWN,
  FLIGHTS_SEARCHED_NO_OFFERS,
  HOLIDAY_NOTICE,
  WEATHER_NOTICE,
  currencyMissingNotice,
  holidayMissingNotice,
  routeReportSummary,
  travelerChangeRefusal,
  travelerChangesUnavailable,
  travelerFlightAvailabilityNotice,
  travelerReviewNotices,
  weatherMissingNotice,
} from "../lib/traveler-view.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const page = readFileSync(join(root, "app", "page.tsx"), "utf8");
const backendReasons = readFileSync(
  join(root, "..", "backend", "app", "providers", "missing_data_reasons.py"),
  "utf8",
);
const backendFlightModel = readFileSync(join(root, "..", "backend", "app", "models", "flight.py"), "utf8");

/** Source with comments removed, so a comment naming a phrase is not a false positive. */
function withoutComments(source) {
  return source
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, "")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "");
}

function componentSource(name) {
  const start = page.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `${name} not found in page.tsx`);
  const next = page.slice(start + 1).search(/\n(async )?function /);
  return withoutComments(page.slice(start, next === -1 ? undefined : start + 1 + next));
}

const travelerStart = page.indexOf("{/* ---- Traveler view");
assert.ok(travelerStart > 0, "Traveler view block not found in page.tsx");
const travelerBlock = page.slice(travelerStart, page.indexOf("</main>", travelerStart));

// Implementation vocabulary that must never reach a traveler.
const OPERATOR_WORDS = /provider|configur|connect|enable|inventory|scrap|MCP|Kiwi|Geoapify|Frankfurter|Nager|Open-Meteo|API|regenerat|\bAI\b|version|deterministic/i;

function backendReasonValues() {
  return [...backendReasons.matchAll(/^[A-Z_]+ = "([a-z_]+)"$/gm)].map((match) => match[1]);
}

// -- weather / holidays / currency -------------------------------------------------------

test("every backend missing-data reason has traveler wording (no raw codes, no operator words)", () => {
  const reasons = backendReasonValues();
  assert.ok(reasons.length >= 12, "reason constants not found in the backend module");
  for (const reason of reasons) {
    for (const notice of [
      weatherMissingNotice({ missing_reason: reason }),
      holidayMissingNotice({ missing_reason: reason }),
      currencyMissingNotice({ missing_reason: reason }),
    ]) {
      assert.ok(notice.length > 0);
      assert.doesNotMatch(notice, /_/, `${reason} -> "${notice}" shows a raw code`);
      assert.doesNotMatch(notice, OPERATOR_WORDS, `${reason} -> "${notice}"`);
    }
  }
});

test("each cause says what happened", () => {
  assert.match(weatherMissingNotice({ missing_reason: "forecast_not_yet_available" }), /next two weeks/);
  assert.match(holidayMissingNotice({ missing_reason: "country_not_supported" }), /isn't available for this country/);
  assert.match(holidayMissingNotice({ missing_reason: "country_unresolved" }), /country couldn't be determined/);
  assert.match(currencyMissingNotice({ missing_reason: "currency_ambiguous" }), /More than one currency/);
  const unsupported = currencyMissingNotice({ missing_reason: "currency_not_supported" });
  assert.match(unsupported, /isn't available in this app/);
  // either side of the pair may be the unsupported one: neither is blamed
  assert.doesNotMatch(unsupported, /destination's currency/);
  for (const reason of ["request_failed", "request_timed_out", "malformed_response", "provider_reported_error"]) {
    assert.match(weatherMissingNotice({ missing_reason: reason }), /couldn't be reached/);
    assert.match(holidayMissingNotice({ missing_reason: reason }), /couldn't be reached/);
    assert.match(currencyMissingNotice({ missing_reason: reason }), /couldn't be reached/);
  }
});

test("a service failure is never worded as a fact about the destination", () => {
  for (const reason of ["request_failed", "request_timed_out", "malformed_response"]) {
    const holiday = holidayMissingNotice({ missing_reason: reason });
    assert.doesNotMatch(holiday, /no (public )?holidays|isn't available for this country/i);
    const currency = currencyMissingNotice({ missing_reason: reason });
    assert.doesNotMatch(currency, /not available for this destination|available in this app/i);
  }
});

test("an older trip without a reason keeps the general notices", () => {
  for (const context of [null, undefined, {}, { missing_reason: null }, { missing_reason: "something_new" }]) {
    assert.equal(weatherMissingNotice(context), WEATHER_NOTICE);
    assert.equal(holidayMissingNotice(context), HOLIDAY_NOTICE);
    assert.equal(currencyMissingNotice(context), CURRENCY_NOTICE);
  }
});

test("the review banner uses the specific reason when the data is absent", () => {
  const notices = travelerReviewNotices(
    { review_codes: [] },
    {
      weather: { daily_weather: [], data_status: "unavailable", missing_reason: "forecast_not_yet_available" },
      holiday: { data_status: "unavailable", missing_reason: "country_not_supported" },
    },
  );
  assert.deepEqual(notices, [
    weatherMissingNotice({ missing_reason: "forecast_not_yet_available" }),
    holidayMissingNotice({ missing_reason: "country_not_supported" }),
  ]);
});

test("Traveler travel-context summaries are wired to the reason helpers", () => {
  assert.match(componentSource("summarizeWeatherForTravelerView"), /weatherMissingNotice\(weather\)/);
  assert.match(componentSource("summarizeCurrencyForTravelerView"), /currencyMissingNotice\(currency\)/);
  assert.doesNotMatch(
    componentSource("summarizeCurrencyForTravelerView"),
    /"Currency exchange rate is not available for this destination\."/,
  );
});

// -- flights -------------------------------------------------------------------------------

test('"No flight offers were found" is said only for a completed search with zero offers', () => {
  assert.equal(
    travelerFlightAvailabilityNotice({ status: "unavailable", offers: [], availability_reason: "search_completed_no_offers" }),
    FLIGHTS_SEARCHED_NO_OFFERS,
  );
  for (const report of [
    // production today: no flight source at all
    { status: "unavailable", offers: [], availability_reason: "no_source_configured" },
    { status: "not_connected", offers: [], availability_reason: "no_source_configured" },
    { status: "unavailable", offers: [], availability_reason: "search_not_possible" },
    { status: "unavailable", offers: [], availability_reason: "local_source_no_matching_offers" },
    { status: "unavailable", offers: [], availability_reason: "response_unusable" },
    { status: "failed", offers: [], availability_reason: "provider_failed" },
    // an older report without a reason, and a missing report
    { status: "unavailable", offers: [] },
    { status: "failed", offers: [] },
    null,
  ]) {
    const notice = travelerFlightAvailabilityNotice(report);
    assert.notEqual(notice, FLIGHTS_SEARCHED_NO_OFFERS, JSON.stringify(report));
    assert.doesNotMatch(notice, /were found/, JSON.stringify(report));
  }
  assert.equal(travelerFlightAvailabilityNotice({ status: "unavailable", offers: [] }), FLIGHTS_NOT_SHOWN);
});

test("every flight reason the backend defines is worded for a traveler", () => {
  const reasons = [...backendFlightModel.matchAll(/^    [A-Z_]+ = "([a-z_]+)"$/gm)].map((match) => match[1]);
  for (const reason of ["no_source_configured", "search_completed_no_offers", "provider_failed"]) {
    assert.ok(reasons.includes(reason), `${reason} missing from the backend enum`);
  }
  for (const reason of reasons) {
    const notice = travelerFlightAvailabilityNotice({ status: "unavailable", offers: [], availability_reason: reason });
    if (reason === "offers_found") continue;
    assert.doesNotMatch(notice, OPERATOR_WORDS, `${reason} -> "${notice}"`);
  }
});

test("a report with offers shows the offers, not a notice", () => {
  assert.equal(
    travelerFlightAvailabilityNotice({ status: "success", offers: [{}], availability_reason: "offers_found" }),
    null,
  );
});

test("the Traveler flight card has no operator wording and uses the helper", () => {
  const card = componentSource("UserModeFlightSummary");
  assert.match(card, /travelerFlightAvailabilityNotice\(report\)/);
  assert.doesNotMatch(card, /Connect or enable/);
  assert.doesNotMatch(card, /"No flight offers were found/);
  assert.doesNotMatch(card, /official-provider/);
  assert.doesNotMatch(travelerBlock, /Only shown if a flight source returned offers/);
});

// -- change requests -------------------------------------------------------------------------

test("an explicit 'cannot apply' readiness gets an explanation; anything else gets none", () => {
  const notSupported = travelerChangesUnavailable({
    change_requests_applicable: false,
    change_requests_unavailable_reason: "change_application_not_supported",
  });
  assert.ok(notSupported);
  assert.match(notSupported.detail, /saved/);
  assert.match(notSupported.detail, /stays as it is/);
  const interpreter = travelerChangesUnavailable({
    change_requests_applicable: false,
    change_requests_unavailable_reason: "change_interpretation_unavailable",
  });
  assert.match(interpreter.title, /right now/);
  for (const readiness of [null, undefined, {}, { change_requests_applicable: true }, { change_requests_applicable: null }]) {
    assert.equal(travelerChangesUnavailable(readiness), null);
  }
  for (const text of [notSupported.title, notSupported.detail, interpreter.title, interpreter.detail]) {
    assert.doesNotMatch(text, OPERATOR_WORDS, text);
  }
});

test("the not-interpretable refusal no longer promises that rewording helps", () => {
  const { title, detail } = travelerChangeRefusal("REGENERATION_FEEDBACK_NOT_INTERPRETABLE");
  assert.doesNotMatch(`${title} ${detail}`, /rewor/i);
  assert.match(detail, /still saved/);
});

test("Traveler view offers no apply action when changes cannot be applied", () => {
  const apply = componentSource("RegenerationReadinessSection");
  assert.match(apply, /const changesUnavailable = mode === "user" \? travelerChangesUnavailable\(readiness\) : null;/);
  // The button (and its hint) render only when applying is possible.
  const gate = apply.indexOf("{!changesUnavailable && (");
  const button = apply.indexOf('"Apply next change"');
  assert.ok(gate > 0 && button > gate, "the apply button is not inside the capability gate");
  assert.match(apply, /changesUnavailable\.title/);
  assert.match(apply, /changesUnavailable\.detail/);

  // Saving still works and its copy does not promise application.
  assert.match(travelerBlock, /changesUnavailable=\{travelerChangesUnavailable\(result\.regenerationReadiness\) !== null\}/);
  const panel = componentSource("FeedbackPanel");
  assert.match(panel, /applyUnavailable\s*\? "Tell us what to change\. Your request is saved with the trip, but the app can't apply/);
  assert.match(panel, /queue: typeof builtQueue = applyUnavailable \? new Map\(\) : builtQueue/);
  // no "waiting" for requests that cannot be applied
  assert.match(apply, /\$\{changesUnavailable \? "\." : " waiting\."\}/);
  assert.match(page, /travelerChangesUnavailable\(tripData\.planning_state\.regeneration_readiness\)/);
});

// -- route diagnostics (Developer view) -------------------------------------------------------

test("the route summary never claims every transfer was verified unless every leg was", () => {
  const legs = (statuses) => statuses.map((status, index) => ({ from_experience_id: `a${index}`, to_experience_id: `b${index}`, status }));
  assert.match(
    routeReportSummary({ status: "success", provider: "geoapify", legs: legs(["success", "success"]) }),
    /^All 2 transfers between stops have a route/,
  );
  const partial = routeReportSummary({ status: "partial", legs: legs(["success", "failed", "unavailable"]) });
  assert.match(partial, /^1 of 3 transfers between stops have a verified route/);
  assert.match(partial, /2 do not and are not verified/);
  assert.doesNotMatch(partial, /^All/);
  const failed = routeReportSummary({ status: "failed", legs: legs(["failed"]) });
  assert.match(failed, /^0 of 1 .*1 does not and is not verified/);
  assert.equal(routeReportSummary({ status: "not_connected", legs: [] }), null);
  assert.equal(routeReportSummary(null), null);
  assert.match(routeReportSummary({ status: "success", legs: [] }), /No transfers between stops were routed/);
});

test("Developer route section reads the real route report", () => {
  const section = componentSource("RouteFeasibilitySection");
  assert.match(section, /routeReportSummary\(routeReport\)/);
  assert.match(page, /routeReport=\{result\.routeFeasibilityReport\}/);
  // the "not connected" sentence is only a fallback for a not_connected plan
  assert.match(section, /routeFeasibility\.data_status === "not_connected"/);
  // the status shown is the route report's once routing ran
  assert.match(section, /reportSummary && routeReport \? routeReport\.status : routeFeasibility\.data_status/);
});
