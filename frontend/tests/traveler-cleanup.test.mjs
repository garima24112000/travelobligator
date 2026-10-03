// Section 2B (Traveler-view leak and warning cleanup) regression tests. Run
// with `npm test` (Node's built-in test runner; no test framework
// dependency).
//
// As in the other suites: unit tests of the pure wording helpers that
// produce Traveler view's strings, plus structure tests of the page source
// (there is no DOM renderer in this repo).

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  aggregateIssues,
  travelerText,
  travelerWhyIncluded,
  unavailableDataLabel,
  unavailableDataLabels,
} from "../lib/display-labels.ts";
import {
  HOLIDAY_NOTICE,
  HOLIDAY_NOT_APPLIED_NOTICE,
  WEATHER_NOTICE,
  WEATHER_NOT_APPLIED_NOTICE,
  travelerChangeRefusal,
  travelerReviewNotices,
} from "../lib/traveler-view.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const page = readFileSync(join(root, "app", "page.tsx"), "utf8");
const travelerSections = readFileSync(join(root, "app", "TravelerSections.tsx"), "utf8");

const developerStart = page.indexOf('{mode === "developer" ? (');
const travelerStart = page.indexOf("{/* ---- Traveler view", developerStart);
assert.ok(developerStart > 0 && travelerStart > developerStart, "result view blocks not found in page.tsx");
const developerBlock = page.slice(developerStart, travelerStart);
const travelerBlock = page.slice(travelerStart, page.indexOf("</main>", travelerStart));

function componentSource(name) {
  const start = page.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `${name} not found in page.tsx`);
  const next = page.indexOf("\nfunction ", start + 1);
  return page.slice(start, next === -1 ? undefined : next);
}

/** Source with comments removed, so a comment naming a term is not a false positive. */
function withoutComments(source) {
  return source
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, "")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "");
}

const PROVIDER_NAMES =
  /OpenStreetMap|Geoapify|Open-Meteo|Nager|Frankfurter|\bOSRM\b|Overpass|Nominatim|\bKiwi\b|\bGroq\b|Anthropic/i;
const PROVIDER_ENUMS = /\b[a-z]+_(?:places|routing|provider|mcp|meteo|date|local)\b|\b[A-Z]+_[A-Z_]+\b/;
const FIELD_NAMES = /destination_context|candidate_[a-z_]+|[a-z]+_[a-z]+\.[a-z_]+|\b[a-z]+(?:_[a-z0-9]+)+\b/;
const PIPELINE_WORDING =
  /candidate|haversine|\bPOIs?\b|provider-backed|grounded|grounding|AI-proposed|promoted from|quality-approved/i;
// The data-status diagnostic as the page used to print it ("GEOAPIFY_PLACES · LIVE").
const STATUS_DIAGNOSTIC = /\bLIVE\b|· live\b/;

function assertTravelerSafe(text, label = text) {
  for (const pattern of [PROVIDER_NAMES, PROVIDER_ENUMS, FIELD_NAMES, PIPELINE_WORDING, STATUS_DIAGNOSTIC]) {
    assert.doesNotMatch(text, pattern, `${label} -> "${text}" exposes ${pattern}`);
  }
}

const liveWeather = { daily_weather: [{ temperature_max_c: 24 }], data_status: "live" };
const liveHoliday = { data_status: "live" };

// -- 1. weather / holiday semantics ---------------------------------------------------

test("a WEATHER finding with weather data present never says weather is unavailable", () => {
  const notices = travelerReviewNotices(
    { review_codes: ["WEATHER"] },
    { weather: liveWeather, holiday: liveHoliday },
  );
  assert.deepEqual(notices, [WEATHER_NOT_APPLIED_NOTICE]);
  assert.equal(
    WEATHER_NOT_APPLIED_NOTICE,
    "Weather information is available, but the itinerary has not been adjusted around it yet.",
  );
  assert.doesNotMatch(notices.join(" "), /unavailable|could not/i);
  // the code alone (no context passed) is still never worded as "unavailable":
  // the backend only raises it when weather data exists
  assert.deepEqual(travelerReviewNotices({ review_codes: ["WEATHER"] }), [WEATHER_NOT_APPLIED_NOTICE]);
});

test("absent weather uses the unavailable wording, with or without the code", () => {
  for (const weather of [
    null,
    { daily_weather: [], data_status: "live" },
    { daily_weather: [{}], data_status: "unavailable" },
    { daily_weather: [{}], data_status: "failed" },
  ]) {
    for (const review_codes of [[], ["WEATHER"]]) {
      const notices = travelerReviewNotices({ review_codes }, { weather, holiday: liveHoliday });
      assert.deepEqual(notices, [WEATHER_NOTICE]);
      assert.doesNotMatch(notices.join(" "), /is available/);
    }
  }
  // available weather with no finding says nothing at all
  assert.deepEqual(
    travelerReviewNotices({ review_codes: [] }, { weather: liveWeather, holiday: liveHoliday }),
    [],
  );
});

test("holidays follow the same rule", () => {
  assert.deepEqual(
    travelerReviewNotices({ review_codes: ["HOLIDAYS"] }, { weather: liveWeather, holiday: liveHoliday }),
    [HOLIDAY_NOT_APPLIED_NOTICE],
  );
  assert.match(HOLIDAY_NOT_APPLIED_NOTICE, /is available/);
  assert.doesNotMatch(HOLIDAY_NOT_APPLIED_NOTICE, /could not be|unavailable/);
  for (const holiday of [null, { data_status: "unavailable" }, { data_status: "not_connected" }]) {
    for (const review_codes of [[], ["HOLIDAYS"]]) {
      assert.deepEqual(
        travelerReviewNotices({ review_codes }, { weather: liveWeather, holiday }),
        [HOLIDAY_NOTICE],
      );
    }
  }
  // the two notices never contradict each other in one banner
  const both = travelerReviewNotices(
    { review_codes: ["HOLIDAYS", "WEATHER"] },
    { weather: liveWeather, holiday: liveHoliday },
  );
  assert.deepEqual(both, [HOLIDAY_NOT_APPLIED_NOTICE, WEATHER_NOT_APPLIED_NOTICE]);
});

test("the backend's own weather warning stays intact under Important limitations", () => {
  const [row] = aggregateIssues([
    {
      category: "weather",
      severity: "warning",
      message:
        "Provider-backed weather data is available for this trip, but the itinerary has not been adjusted around weather yet -- review outdoor/long-walk days manually.",
      affected_section: "experience_plan",
      suggested_fix: null,
    },
  ]);
  assert.match(row.message, /^Weather data is available for this trip, but the itinerary has not been adjusted/);
  assertTravelerSafe(row.message);
});

// -- 2. internal language ----------------------------------------------------------------

test("sentences that leaked in the live smoke are rewritten without internals", () => {
  const leaked = [
    "Promoted from an AI-proposed candidate that was independently provider-grounded and quality-approved (see ai_candidate_promotion_report).",
    "Selected from provider-backed accommodation POI candidates in destination_context.candidate_accommodation_pois, ranked by average haversine proximity to this plan's scheduled attractions. This is an open-data location candidate only, not a bookable offer.",
    "Suggested from provider-backed restaurant candidates in destination_context.candidate_restaurants, selected by straight-line (haversine) distance.",
    "3 of 4 scheduled leg(s) have a provider-backed route (via geoapify_routing).",
    "This is separate from any OpenStreetMap accommodation-location candidates suggested above.",
    "Weather came from open_meteo and holidays from nager_date.",
    "Places were found by geoapify_places and checked with Geoapify Routing.",
    "Narration by Groq was skipped.",
  ];
  for (const sentence of leaked) {
    const text = travelerText(sentence);
    assert.doesNotMatch(text, PROVIDER_NAMES, text);
    assert.doesNotMatch(text, /destination_context|candidate|haversine|\bPOIs?\b|provider-backed|grounded|quality-approved/i, text);
    assert.doesNotMatch(text, /\b[a-z]+(?:_[a-z0-9]+)+\b/, text);
  }
  assert.equal(
    travelerText("3 of 4 scheduled leg(s) have a provider-backed route (via geoapify_routing)."),
    "3 of 4 scheduled leg(s) have a route.",
  );
});

test("a scheduled place shows a plain reason or nothing", () => {
  assert.equal(travelerWhyIncluded("Matches your must-visit request."), "One of your must-visit places.");
  assert.equal(
    travelerWhyIncluded(
      "Matches your interests based on this candidate's provider-backed name, category, and address.",
    ),
    "Matches your interests.",
  );
  assert.equal(
    travelerWhyIncluded("You explicitly asked for this place to be added."),
    "Added because you asked for it.",
  );
  // the AI-promotion sentence, the default sentence and anything unknown: nothing
  for (const internal of [
    "Promoted from an AI-proposed candidate that was independently provider-grounded and quality-approved (see ai_candidate_promotion_report).",
    "Selected from provider-backed attraction candidates.",
    "Chosen by the fallback pass from geoapify_places.",
    "",
    null,
    undefined,
  ]) {
    assert.equal(travelerWhyIncluded(internal), null);
  }
  for (const shown of ["One of your must-visit places.", "Matches your interests.", "Added because you asked for it."]) {
    assertTravelerSafe(shown);
    assert.doesNotMatch(shown, /open|hours|book|available|recommend|best/i);
  }
});

test("the AI-suggestion badge and place-data labels are not in Traveler view", () => {
  assert.doesNotMatch(page, /Suggested by AI/i);
  assert.doesNotMatch(page, /matched in place data/i);
  assert.doesNotMatch(page, /placeDataLabel|sourceLabel\(/);

  const card = componentSource("ScheduledExperienceCard");
  assert.match(card, /mode === "developer" && experience\.promoted_from_ai && \(\s*<AIPromotedBadge/);
  assert.match(card, /mode === "developer" && experience\.why_included &&/);
  assert.match(card, /mode === "user" && travelerWhyIncluded\(experience\.why_included\)/);
  assert.doesNotMatch(card, /travelerText\(experience\.why_included\)/);
  // the badge component has no Traveler variant any more
  assert.doesNotMatch(componentSource("AIPromotedBadge"), /mode/);

  // a food card shows its source key / data status / selection text only to developers
  const food = componentSource("RestaurantSuggestionCard");
  const developerOnly = food.slice(food.indexOf('{mode === "developer" && ('));
  for (const field of ["restaurant.source", "restaurant.data_status", "restaurant.why_suggested"]) {
    assert.ok(developerOnly.includes(field), `${field} must be developer-only`);
    assert.ok(!food.slice(0, food.indexOf('{mode === "developer" && (')).includes(field));
  }
});

test("Traveler-only components carry no provider names, field names or pipeline terms", () => {
  for (const name of [
    "TravelerWhereToStaySection",
    "TravelerContextSummarySection",
    "summarizeWeatherForTravelerView",
    "summarizeCurrencyForTravelerView",
    "UserModeReadinessBanner",
    "GettingAroundSection",
  ]) {
    const source = withoutComments(componentSource(name));
    assert.doesNotMatch(source, PROVIDER_NAMES, name);
    assert.doesNotMatch(source, /haversine|provider-backed|\bPOIs?\b|candidate (?!s\b)[a-z]+ data/i, name);
    assert.doesNotMatch(source, /\.source\b|\.data_status\b|\.why_suggested\b|\.provider\b/, name);
  }
  const sections = withoutComments(travelerSections);
  assert.doesNotMatch(sections, PROVIDER_NAMES);
  assert.doesNotMatch(sections, /provider|haversine|candidate/i);
  // the weather line no longer names its source
  assert.doesNotMatch(componentSource("summarizeWeatherForTravelerView"), /weather\.source|via /);
  // the Traveler result block itself prints no raw source / status field
  const block = withoutComments(travelerBlock);
  assert.doesNotMatch(block, PROVIDER_NAMES);
  assert.doesNotMatch(block, /provider-backed|Provider-backed|data_status|\.source\b/);
});

// -- 3. where to stay ----------------------------------------------------------------------

test("Where to stay shows name, category and address with traveler copy only", () => {
  const section = componentSource("TravelerWhereToStaySection");
  const code = withoutComments(section);
  assert.match(
    code.replace(/\s+/g, " "),
    /Areas and nearby stays based on mapped locations\. These are not live hotel offers, and prices or availability have not been checked\./,
  );
  assert.match(code.replace(/\s+/g, " "), /relatively close to places in your itinerary/);
  // its own card: no developer card, no source / status / explanation fields
  assert.doesNotMatch(code, /<AccommodationSuggestionCard/);
  assert.doesNotMatch(code, /accommodation\.source|accommodation\.data_status|accommodation\.why_suggested/);
  assert.doesNotMatch(code, /stayAreaGuidance\.summary|stayAreaGuidance\.assumptions|stayAreaGuidance\.warnings/);
  for (const field of ["accommodation.name", "accommodation.category", "accommodation.address"]) {
    assert.ok(code.includes(field), `${field} should be shown`);
  }
  // never presented as recommendations or bookable offers
  assert.doesNotMatch(code, /recommended|best stay|book now|bookable hotels/i);
  // the developer card (source · status · explanation) is still used in Developer view
  assert.match(componentSource("StayAreaGuidanceSection"), /<StayAreaAccommodationCard|<AccommodationSuggestionCard/);
  // a live offer card in Traveler view does not print the provider key
  assert.match(componentSource("AccommodationOfferCard"), /\{!concise && \(\s*<p[^>]*>\s*\{offer\.provider\}/);
});

// -- 4. accommodation availability wording ----------------------------------------------------

test("the accommodation limitation is about live availability, not all accommodation options", () => {
  for (const field of ["accommodation_options", "accommodation_details", "accommodations"]) {
    const label = unavailableDataLabel(field);
    assert.equal(label, "Live accommodation prices and availability");
    assert.doesNotMatch(label, /accommodation options/i);
  }
  assert.equal(unavailableDataLabel("flight_options"), "Live flight offers");
  // internal reasoning-stage fields are not travel data and are not listed
  for (const internal of ["decision_card", "validation_reasoning", "trip_strategy", "feedback_interpretation"]) {
    assert.equal(unavailableDataLabel(internal), null);
  }
  // an unknown field is humanized, never shown raw
  assert.equal(unavailableDataLabel("some_new_field"), "Some new field");
  const labels = unavailableDataLabels([
    "accommodation_options",
    "accommodation_details",
    "flight_options",
    "transit_options",
    "decision_card",
    "weather_alerts",
  ]);
  assert.deepEqual(labels, [
    "Live accommodation prices and availability",
    "Live flight offers",
    "Public transport routes",
    "Weather alerts",
  ]);
  labels.forEach((label) => assertTravelerSafe(label));
  assert.match(travelerSections, /unavailableDataLabels\(report\.unavailable_data_notes\)/);
  assert.doesNotMatch(travelerSections, />Data not available</);
});

// -- 5. branches / regeneration ---------------------------------------------------------------

test("branch and revision internals are not in Traveler view", () => {
  const block = withoutComments(travelerBlock);
  assert.doesNotMatch(block, /<BranchWorkspacePanel/);
  assert.doesNotMatch(block, /<VersionHistorySection|<PlanDiffPreviewSection|<RegenerationAttemptAuditSection/);
  assert.doesNotMatch(block, /branch|revision|head\b/i);
  // no jump link to a section that is not rendered
  const linksStart = page.indexOf("const USER_MODE_JUMP_LINKS");
  const links = page.slice(linksStart, page.indexOf("];", linksStart));
  assert.doesNotMatch(links, /branches|regeneration/i);

  // the one remaining control is a plainly worded "apply" action
  const apply = componentSource("RegenerationReadinessSection");
  assert.match(apply, /mode === "user" \? "Apply your changes" : "Regeneration readiness"/);
  assert.match(apply, /"Apply next change"/);
  assert.match(apply, /id=\{mode === "user" \? "apply-changes" : "regeneration-readiness"\}/);
  // version labels, section names and raw refusal text are Developer-only
  assert.match(apply, /mode === "developer" && \(\s*<p[^>]*>\s*\{formatNullableVersionLabel\(regenerateSuccess\.previousVersion\)\}/);
  assert.match(apply, /mode === "developer" && regenerateSuccess\.changedSections\.length > 0/);
  assert.match(apply, /travelerChangeRefusal\(regenerateError\.code\)\.title/);
  assert.match(apply, /travelerChangeRefusal\(regenerateError\.code\)\.detail/);
  assert.match(apply, /mode === "user"\s*\? readiness\.pending_feedback_count === 0|\{mode === "user" \? \(\s*<p[^>]*>\s*\{readiness\.pending_feedback_count === 0/);
});

test("refusals to apply a change are worded for a traveler", () => {
  for (const code of [
    "REGENERATION_BLOCKED_BY_LOCKS",
    "REGENERATION_NO_PENDING_FEEDBACK",
    "REGENERATION_NOT_AVAILABLE",
    "REGENERATION_CONFLICT",
    "REGENERATION_PROVIDER_UNAVAILABLE",
    "REGENERATION_FEEDBACK_NOT_INTERPRETABLE",
    "REGENERATION_NO_EFFECT",
    "REGENERATION_PROVIDER_RATE_LIMITED",
    "REGENERATION_AI_UNAVAILABLE",
    "JOB_ALREADY_RUNNING",
    "CONCURRENT_UPDATE",
    "BRANCH_STATE_CONFLICT",
    "SOMETHING_NEW",
    null,
  ]) {
    const { title, detail } = travelerChangeRefusal(code);
    for (const text of [title, detail]) {
      assert.ok(text.length > 0);
      assertTravelerSafe(text, String(code));
      assert.doesNotMatch(text, /regenerat|lock|version|revision|branch|rate.?limit|\bAI\b|provider|deterministic/i, String(code));
    }
  }
  assert.match(travelerChangeRefusal("REGENERATION_BLOCKED_BY_LOCKS").detail, /Remove the keep/);
});

test("Request changes stays in Traveler view, with a way to apply it", () => {
  assert.match(travelerBlock, /<FeedbackPanel/);
  assert.match(travelerBlock, /mode="user"/);
  const panel = componentSource("FeedbackPanel");
  assert.match(panel, /<h2[^>]*>Request changes<\/h2>/);
  // saving a request would be a dead end without the apply control
  assert.ok(travelerBlock.indexOf("<RegenerationReadinessSection") > travelerBlock.indexOf("<FeedbackPanel"));
  // Traveler copy in the panel does not use the developer vocabulary
  assert.match(panel, /itinerary changes only when you apply it below/);
  assert.match(panel, /isTraveler \? "Requests are applied" : "Regenerate handles"/);
});

test("Developer view keeps every diagnostic that left Traveler view", () => {
  for (const marker of [
    "<BranchWorkspacePanel",
    "<RegenerationReadinessSection",
    "<VersionHistorySection",
    "<PlanDiffPreviewSection",
    "<RegenerationAttemptAuditSection",
    "<StayAreaGuidanceSection",
    "<ValidationSection",
    "<ProviderCoverageSection",
    "<WeatherContextSection",
    "<HolidayContextSection",
    "<ItineraryNarrativeDiagnosticSection",
  ]) {
    assert.ok(developerBlock.includes(marker), `${marker} must stay in Developer view`);
  }
  assert.match(developerBlock, /compact=\{false\}\s+mode="developer"/);
  const apply = componentSource("RegenerationReadinessSection");
  for (const kept of [
    "Regeneration readiness",
    "Regenerate from feedback",
    "readiness.status",
    "readiness.blocked_by",
    "Targeted regeneration summary",
    "regenerateError.message",
  ]) {
    assert.ok(apply.includes(kept), `Developer view lost "${kept}"`);
  }
  assert.match(componentSource("AIPromotedBadge"), /AI-suggested · Provider-grounded/);
  assert.match(componentSource("ScheduledExperienceCard"), /\{experience\.why_included\}/);
  assert.match(componentSource("AccommodationSuggestionCard"), /accommodation\.why_suggested/);
});

// -- 6. readiness duplication -----------------------------------------------------------------

test("readiness is stated once, not in the header and again right below it", () => {
  const header = page.slice(page.indexOf('<div id="summary"'), page.indexOf("<ModeToggle mode={mode}"));
  assert.ok(header.length > 0);
  const headerCode = withoutComments(header);
  // the header's status line is Developer-only
  assert.doesNotMatch(headerCode, /travelerReadiness|Plan check/);
  assert.match(headerCode, /\{mode === "developer" && \(\s*<p className="mt-3 leading-6">\s*Pipeline status:/);
  assert.match(headerCode, /mode === "developer" &&\s*\(result\.summary\.main_blocking_reason \|\|/);
  // one banner, and it still carries the meaningful detail
  assert.equal(page.split("<UserModeReadinessBanner").length - 1, 1);
  const banner = componentSource("UserModeReadinessBanner");
  assert.match(banner, /readiness\.label/);
  assert.match(banner, /blockingReason && \(/);
  assert.match(banner, /notices\.map/);
  assert.match(page, /blockingReason=\{result\.summary\.main_blocking_reason\}/);
  // warnings themselves are still shown, outside any disclosure
  assert.match(travelerBlock, /<TravelerLimitationsSection report=\{result\.validationReport\} \/>/);
  assert.match(travelerBlock, /<TravelerInventoryNotice/);
});
