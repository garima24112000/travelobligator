// Section 2 (Traveler UX polish): the pure view model for what Traveler view
// says about movement between stops, the "Getting around" advisory, plan
// readiness, the loading state and request errors. Nothing here adds a fact:
// every string restates a value the backend already sent, or is fixed copy.
//
// Kept free of React and of runtime imports so it can be unit tested with
// Node's built-in test runner (frontend/tests).

import type {
  HolidayContext,
  ItineraryNarrativeReport,
  RouteFeasibilityReport,
  TravelTimeBuffer,
  ValidationReport,
  WeatherContext,
} from "./types";

// -- movement between stops ------------------------------------------------------

/** "12 min", "1 h 5 min". Whole minutes only; never raw seconds. */
export function formatTravelDuration(seconds: number | null | undefined): string | null {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) {
    return null;
  }
  const totalMinutes = Math.round(seconds / 60);
  if (totalMinutes < 1) return "under 1 min";
  if (totalMinutes < 60) return `${totalMinutes} min`;
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return minutes > 0 ? `${hours} h ${minutes} min` : `${hours} h`;
}

/** "0.9 km", "8.4 km", "120 km". Kilometres only; never raw metres. */
export function formatTravelDistance(meters: number | null | undefined): string | null {
  if (meters === null || meters === undefined || !Number.isFinite(meters) || meters < 0) {
    return null;
  }
  const km = meters / 1000;
  if (km < 0.05) return "under 0.1 km";
  if (km >= 100) return `${Math.round(km)} km`;
  return `${km.toFixed(1)} km`;
}

/**
 * Traveler label for a leg's stored mode. `drive` is only ever "Vehicle
 * transfer": the routing data is a driving-route estimate, not a statement
 * about which vehicle or service the traveler will use. An unknown or
 * missing mode gets a neutral label -- the raw value is never shown.
 */
export function movementModeLabel(mode: string | null | undefined): string {
  const normalized = (mode ?? "").trim().toLowerCase();
  if (normalized === "walk") return "Walk";
  if (normalized === "drive") return "Vehicle transfer";
  return "Travel";
}

type RouteLeg = NonNullable<RouteFeasibilityReport["legs"]>[number];

function findRouteLeg(
  report: RouteFeasibilityReport | null | undefined,
  fromExperienceId: string,
  toExperienceId: string,
): RouteLeg | null {
  return (
    report?.legs?.find(
      (leg) =>
        leg.from_experience_id === fromExperienceId && leg.to_experience_id === toExperienceId,
    ) ?? null
  );
}

/**
 * The mode stored for the leg between two stops, or null when the route
 * report has no successfully routed leg for them. A routed leg stored before
 * modes existed has none and was a walking route (backend `leg_mode`).
 */
export function legMode(
  report: RouteFeasibilityReport | null | undefined,
  fromExperienceId: string,
  toExperienceId: string,
): string | null {
  const leg = findRouteLeg(report, fromExperienceId, toExperienceId);
  if (!leg || leg.status !== "success") return null;
  return leg.mode ?? "walk";
}

/**
 * "Walk · 12 min · 0.9 km" / "Vehicle transfer · 18 min · 8.4 km". Only the
 * figures that exist are shown; with neither a duration nor a distance there
 * is nothing factual to say and the result is null.
 */
export function travelerMovementLine(leg: {
  mode: string | null | undefined;
  durationSeconds: number | null | undefined;
  distanceMeters: number | null | undefined;
}): string | null {
  const figures = [
    formatTravelDuration(leg.durationSeconds),
    formatTravelDistance(leg.distanceMeters),
  ].filter((part): part is string => part !== null);
  if (figures.length === 0) return null;
  return [movementModeLabel(leg.mode), ...figures].join(" · ");
}

/** The Traveler movement row for one stored leg, or null when it has no successful route. */
export function travelerLegLine(
  buffer: Pick<
    TravelTimeBuffer,
    "from_experience_id" | "to_experience_id" | "status" | "route_duration_seconds" | "route_distance_meters"
  > | null,
  report: RouteFeasibilityReport | null | undefined,
): string | null {
  if (!buffer || buffer.status !== "success") return null;
  return travelerMovementLine({
    mode: legMode(report, buffer.from_experience_id, buffer.to_experience_id),
    durationSeconds: buffer.route_duration_seconds,
    distanceMeters: buffer.route_distance_meters,
  });
}

/** One trip-level sentence about route coverage, or null when every leg has route data. */
export function travelerRouteCoverageNote(
  report: Pick<RouteFeasibilityReport, "status"> | null | undefined,
): string | null {
  if (!report) return "Travel times between stops are not available for this trip.";
  if (report.status === "partial") {
    return "Travel times are shown where available. Some legs between stops could not be estimated.";
  }
  if (report.status === "success") return null;
  return "Travel times between stops are not available for this trip.";
}

// -- getting around ---------------------------------------------------------------

export const GETTING_AROUND_HEADING = "Getting around";
export const GETTING_AROUND_DISCLAIMER =
  "General AI guidance about the city, not based on this itinerary's route data.";

/**
 * The narrator's general "getting around" advisory, or null. Read on its own:
 * it does not depend on the narrator's status or summary, so an advisory that
 * survived a summary fallback is still shown. Nothing is substituted when it
 * is absent, and the accompanying profile is never displayed.
 */
export function gettingAroundAdvisory(
  report: Pick<ItineraryNarrativeReport, "getting_around_advisory"> | null | undefined,
): string | null {
  const advisory = report?.getting_around_advisory;
  if (typeof advisory !== "string") return null;
  const trimmed = advisory.trim();
  return trimmed.length > 0 ? trimmed : null;
}

/**
 * The line under the trip summary saying where the summary came from. A
 * summary built from the itinerary (the narrator's fallback) is a normal
 * outcome for a traveler, so it is described without failure wording.
 */
export function tripSummarySourceNote(
  report: Pick<ItineraryNarrativeReport, "narrative_source"> | null | undefined,
): string {
  return report?.narrative_source === "deterministic_fallback"
    ? "Summary put together directly from your itinerary."
    : "AI-written summary of the plan below. It adds no new facts, and nothing is booked.";
}

// -- readiness ----------------------------------------------------------------------

export type ReadinessTone = "ok" | "review" | "blocked" | "unknown";

export type TravelerReadiness = { tone: ReadinessTone; label: string; message: string };

/** Plain-language restatement of the backend's readiness status; never the raw value. */
export function travelerReadiness(status: string | null | undefined): TravelerReadiness {
  if (status === "ready") {
    return {
      tone: "ok",
      label: "Checks passed",
      message:
        "This itinerary passed our automated checks. Still confirm opening hours, prices and bookings yourself before you travel.",
    };
  }
  if (status === "needs_review") {
    return {
      tone: "review",
      label: "Worth a second look",
      message:
        "You can use this itinerary as a planning draft. A few things are worth double-checking first.",
    };
  }
  if (status === "blocked") {
    return {
      tone: "blocked",
      label: "Not ready to use",
      message: "This itinerary is incomplete and should not be relied on yet.",
    };
  }
  return {
    tone: "unknown",
    label: "Not checked yet",
    message: "This itinerary has not been checked yet.",
  };
}

// Outcome codes with their own dedicated notice above the itinerary.
const CODES_WITH_OWN_NOTICE = new Set(["INSUFFICIENT_VERIFIED_INVENTORY", "UNDERFILLED_PLAN"]);

// Weather / holiday data was not returned at all.
export const WEATHER_NOTICE = "Weather information may be unavailable for these dates.";
export const HOLIDAY_NOTICE = "Holiday information could not be fully verified.";
// Section 2B: the backend raises a WEATHER / HOLIDAYS review finding only
// when that data IS available -- the finding says the itinerary has not been
// adjusted around it. It must never be worded as "unavailable".
export const WEATHER_NOT_APPLIED_NOTICE =
  "Weather information is available, but the itinerary has not been adjusted around it yet.";
export const HOLIDAY_NOT_APPLIED_NOTICE =
  "Holiday information is available, but the itinerary has not been checked against closures or opening hours on those dates.";

// A review code is the upper-cased category of a validation warning. Only
// categories whose meaning is fixed get a sentence; any other code is left to
// its own warning text under "Important limitations".
const REVIEW_CODE_NOTICES: Record<string, string> = {
  WEATHER: WEATHER_NOT_APPLIED_NOTICE,
  HOLIDAYS: HOLIDAY_NOT_APPLIED_NOTICE,
  LONG_TRAVEL_DAY: "At least one day involves a lot of travel between stops.",
  FEASIBILITY: "Travel between some stops could not be fully checked.",
};

const UNAVAILABLE_DATA_STATUSES = new Set(["unavailable", "not_connected", "failed"]);

/**
 * Short, plain sentences for what a traveler should double-check, derived
 * from the validation outcome codes and from whether weather / holiday data
 * was returned at all. Never a raw code, and nothing beyond what those
 * signals say.
 */
export function travelerReviewNotices(
  validation: Pick<ValidationReport, "blocking_codes" | "review_codes"> | null | undefined,
  context?: {
    weather?: Pick<WeatherContext, "daily_weather" | "data_status"> | null;
    holiday?: Pick<HolidayContext, "data_status"> | null;
  },
): string[] {
  const notices: string[] = [];
  const add = (notice: string | undefined) => {
    if (notice && !notices.includes(notice)) notices.push(notice);
  };

  // What the page itself holds decides "unavailable": the data either came
  // back or it did not. A missing argument means "not known here".
  const weather = context?.weather;
  const weatherAbsent =
    weather === null ||
    (weather !== undefined &&
      (weather.daily_weather.length === 0 || UNAVAILABLE_DATA_STATUSES.has(weather.data_status)));
  const holiday = context?.holiday;
  const holidayAbsent =
    holiday === null ||
    (holiday !== undefined && UNAVAILABLE_DATA_STATUSES.has(holiday.data_status));

  for (const code of [...(validation?.blocking_codes ?? []), ...(validation?.review_codes ?? [])]) {
    if (CODES_WITH_OWN_NOTICE.has(code)) continue;
    // Never say "available" about data the page can see is absent.
    if (code === "WEATHER" && weatherAbsent) continue;
    if (code === "HOLIDAYS" && holidayAbsent) continue;
    add(REVIEW_CODE_NOTICES[code]);
  }
  if (weatherAbsent) add(WEATHER_NOTICE);
  if (holidayAbsent) add(HOLIDAY_NOTICE);
  return notices;
}

// -- applying requested changes ---------------------------------------------------------

export type ChangeRefusal = { title: string; detail: string };

const STILL_SAVED = "Nothing was changed, and your request is still saved.";

// Section 2B: Traveler wording for why saved requests could not be applied.
// Keyed by the backend's refusal code; the code and the backend's own
// message (which may name versions, locks or an AI service) are not shown.
const CHANGE_REFUSALS: Record<string, ChangeRefusal> = {
  REGENERATION_BLOCKED_BY_LOCKS: {
    title: "Remove kept places first",
    detail:
      "At least one place is marked to keep, so no changes were applied. Remove the keep, then try again. Your request is still saved.",
  },
  REGENERATION_NO_PENDING_FEEDBACK: {
    title: "No saved requests",
    detail: "Add a request under Request changes first.",
  },
  REGENERATION_CONFLICT: {
    title: "The itinerary changed while applying",
    detail: "The latest itinerary has been reloaded below. Nothing was overwritten.",
  },
  REGENERATION_PROVIDER_UNAVAILABLE: {
    title: "We couldn't look up that place",
    detail:
      "This does not mean the place doesn't exist. Your request is still saved, so you can try again later.",
  },
  REGENERATION_FEEDBACK_NOT_INTERPRETABLE: {
    title: "We couldn't work out what to change",
    detail: `Try rewording your request, for example by naming the place or the day. ${STILL_SAVED}`,
  },
  REGENERATION_NO_EFFECT: {
    title: "Nothing to change",
    detail: "Your request did not lead to any change in the itinerary. It is still saved.",
  },
  REGENERATION_PROVIDER_RATE_LIMITED: {
    title: "Temporarily unavailable",
    detail: `We couldn't process your request right now. ${STILL_SAVED} Please try again in a few minutes.`,
  },
  REGENERATION_AI_UNAVAILABLE: {
    title: "Temporarily unavailable",
    detail: `We couldn't process your request right now. ${STILL_SAVED} Please try again in a few minutes.`,
  },
  JOB_ALREADY_RUNNING: {
    title: "Already in progress",
    detail: "This itinerary is already being updated. Please wait for it to finish.",
  },
  CONCURRENT_UPDATE: {
    title: "The itinerary changed somewhere else",
    detail: "Reload the trip to see the latest itinerary, then try again. Nothing was overwritten.",
  },
};

const DEFAULT_CHANGE_REFUSAL: ChangeRefusal = {
  title: "Your changes could not be applied",
  detail: STILL_SAVED,
};

export function travelerChangeRefusal(code: string | null | undefined): ChangeRefusal {
  return (code && CHANGE_REFUSALS[code]) || DEFAULT_CHANGE_REFUSAL;
}

// -- loading ------------------------------------------------------------------------

// The pipeline stages the backend reports while generating, in traveler words.
const LOADING_STAGE_LABELS: Record<string, string> = {
  traveler_profile: "Preparing your trip",
  destination_context: "Finding verified places",
  candidate_quality: "Checking place details",
  ai_candidate_shadow: "Checking place details",
  trip_strategy: "Building the day-by-day plan",
  stay_transport: "Checking travel options",
  experience_plan: "Building the day-by-day plan",
  validation: "Checking the plan",
  post_processing: "Finishing your itinerary",
};

export const LOADING_DEFAULT_MESSAGE = "Working on your itinerary";

/** Traveler label for a stage the backend reported, or null for none / an unknown one. */
export function loadingStageLabel(stageKey: string | null | undefined): string | null {
  return stageKey ? (LOADING_STAGE_LABELS[stageKey] ?? null) : null;
}

/**
 * What building an itinerary involves. Shown as rotating, clearly generic
 * copy -- never as a claim that the named step is the one running now or
 * that an earlier one has finished.
 */
export const GENERIC_LOADING_STEPS = [
  "Finding verified places",
  "Checking your must-visits",
  "Building the day-by-day plan",
  "Checking travel between stops",
  "Finishing your itinerary",
] as const;

export const LOADING_STEP_ROTATION_SECONDS = 6;

export function genericLoadingStep(elapsedSeconds: number): string {
  const safeElapsed = Number.isFinite(elapsedSeconds) && elapsedSeconds > 0 ? elapsedSeconds : 0;
  const index = Math.floor(safeElapsed / LOADING_STEP_ROTATION_SECONDS) % GENERIC_LOADING_STEPS.length;
  return GENERIC_LOADING_STEPS[index];
}

/** Sets expectations about the wait from elapsed time alone; never a percentage. */
export function loadingPatienceNote(elapsedSeconds: number): string {
  if (elapsedSeconds < 20) return "This usually takes 30 to 60 seconds.";
  if (elapsedSeconds < 75) {
    return "Still working. Itineraries usually take 30 to 60 seconds, sometimes a little longer.";
  }
  return "This is taking longer than usual, but it is still running. There is no need to submit again.";
}

// -- errors --------------------------------------------------------------------------

export type TravelerError = {
  message: string;
  /** Whether repeating the same action is safe and may succeed. */
  retryable: boolean;
};

export type TravelerErrorInput = {
  code: string | null;
  status: number | null;
  message: string | null;
  /** What the app was doing: "create", "generate", "load", "feedback", ... */
  operation: string;
};

export const SESSION_ENDED_MESSAGE = "Your session has ended. Please log in again.";
// Frontend-only codes (never sent by the backend).
export const BACKEND_UNREACHABLE = "BACKEND_UNREACHABLE";
export const PLAN_NOT_GENERATED = "PLAN_NOT_GENERATED";

const GENERATION_FAILED_MESSAGE =
  "We couldn't finish building this itinerary. Please try again in a moment.";

// A backend sentence is shown to a traveler only when it carries none of
// these: identifiers, codes, stage / provider vocabulary, URLs, or anything
// that looks like a stack trace or configuration name.
const INTERNAL_DETAIL_PATTERNS: RegExp[] = [
  /[a-z0-9]+_[a-z0-9_]+/i,
  /https?:\/\//i,
  /\b(traceback|exception|stack|errno|sql|psycopg|redis|postgres|timeout=|status code|http \d{3})\b/i,
  /\b(provider|adapter|gateway|orchestrator|pipeline|stage|planning ?state|api key|token|secret|env)\b/i,
  /\b(groq|geoapify|overpass|nominatim|osrm|anthropic|kiwi)\b/i,
  /[{}<>\[\]]/,
];

function isSafeForTraveler(message: string | null): message is string {
  if (!message) return false;
  const trimmed = message.trim();
  if (trimmed.length === 0 || trimmed.length > 240) return false;
  return !INTERNAL_DETAIL_PATTERNS.some((pattern) => pattern.test(trimmed));
}

function genericFailure(operation: string): string {
  if (operation === "create" || operation === "generate") return GENERATION_FAILED_MESSAGE;
  if (operation === "load") return "We couldn't open that trip. Please try again.";
  if (operation === "feedback") return "We couldn't save your request. Please try again.";
  return "Something went wrong. Please try again.";
}

/**
 * Traveler wording for a failed request: concise, actionable, and free of
 * codes, failure kinds, provider names, stack traces and configuration
 * detail. Developer view keeps the backend's own message.
 */
export function travelerErrorMessage(input: TravelerErrorInput): TravelerError {
  const { code, status, message, operation } = input;

  switch (code) {
    case BACKEND_UNREACHABLE:
      return {
        message:
          "We couldn't reach the server. It may be starting up after a quiet period, which can take about a minute. Please try again.",
        retryable: true,
      };
    case "AUTHENTICATION_REQUIRED":
      return { message: SESSION_ENDED_MESSAGE, retryable: false };
    case "FORBIDDEN":
      return { message: "You do not have access to this trip.", retryable: false };
    case "TRIP_NOT_FOUND":
      return { message: "We couldn't find that trip. It may have been removed.", retryable: false };
    case PLAN_NOT_GENERATED:
      return {
        message:
          "This trip doesn't have an itinerary yet. If you just created it, it may still be in progress. Open it from My trips in a minute.",
        retryable: true,
      };
    case "JOB_ALREADY_RUNNING":
      return {
        message: "This trip is already being worked on. Please wait for it to finish.",
        retryable: false,
      };
    case "CONCURRENT_UPDATE":
      return {
        message: "This trip was changed somewhere else. Reload it to see the latest version.",
        retryable: true,
      };
    case "JOB_INTERRUPTED":
      return {
        message: "Building this itinerary was interrupted before it finished. Please try again.",
        retryable: true,
      };
    case "STAGE_FAILED":
      return { message: GENERATION_FAILED_MESSAGE, retryable: true };
    case "DESTINATION_UNRESOLVED":
      return {
        message:
          'We couldn\'t find that destination with confidence, so no itinerary was planned. Try including the city and country, for example "Lisbon, Portugal".',
        retryable: false,
      };
    default:
      break;
  }

  if (status === 429) {
    return {
      message: "Too many requests right now. Please wait a moment and try again.",
      retryable: true,
    };
  }
  if (status !== null && status >= 500) {
    return { message: genericFailure(operation), retryable: true };
  }
  // A 4xx the backend explained in plain words (e.g. a date problem, or a
  // destination it could not find) is the most actionable thing to show.
  if (isSafeForTraveler(message)) {
    return { message: message.trim(), retryable: false };
  }
  return { message: genericFailure(operation), retryable: status === null || status === 0 };
}
