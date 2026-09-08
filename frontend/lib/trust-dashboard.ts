// Trust dashboard data-shaping helper (Step 176B, docs/16_frontend_architecture.md).
//
// This module is a pure, frontend-only display-shaping layer. It reads
// fields already present on an assembled `PlanResult`-shaped object (see
// `TrustDashboardSourceResult` below) and derives labels/counts/groupings
// from them -- it never fetches anything, never calls a browser API, never
// depends on React state, and never imports React. Section 176A's audit
// found every field a trust dashboard needs is already returned by the
// existing `GET` endpoints `frontend/app/page.tsx`'s `loadPlanResult`
// already calls, so this step adds no new network call, no backend model,
// and no backend endpoint.
//
// Every status/count/label produced here is a direct relabeling or a plain
// aggregation (count, "worst of a small fixed list") of a value that
// already exists on the backend response -- never a new travel fact, never
// a computed distance/duration/route, and never an inferred booking,
// price, rating, or safety claim. Where the backend hasn't computed
// something (e.g. no AI candidate discovery ever ran), this says so
// honestly instead of guessing.
//
// Nothing in this module is wired into any component yet (Step 176B is
// data-shaping only) -- `buildTrustDashboardModel` has no caller in this
// step. Rendering it is Step 176C's job.

import type {
  AICandidatePromotionReport,
  AICandidateReviewReport,
  AccommodationInventoryReport,
  DailyPlan,
  FlightInventoryReport,
  PendingFeedbackSummary,
  PlanDiffPreview,
  ProviderCoverageData,
  ReadinessChecklist,
  RegenerationAttempt,
  RegenerationReadiness,
  RouteAwareSequencingReport,
  RouteFeasibilityReport,
  TravelTimeBufferReport,
  TripSummary,
  ValidationIssue,
  ValidationReport,
  VersionHistoryItem,
} from "./types";

// ---------------------------------------------------------------------------
// Source data shape
// ---------------------------------------------------------------------------

/**
 * The slice of `frontend/app/page.tsx`'s `PlanResult` this module actually
 * reads. Deliberately declared here (rather than imported from `page.tsx`,
 * which is a React component module) so this file stays React-free and so
 * `page.tsx` needs no change in this step -- `PlanResult` already has every
 * one of these fields with these exact types, so passing a real
 * `PlanResult` value wherever this type is expected works via normal
 * structural typing, with zero wiring required until Step 176C actually
 * imports and calls `buildTrustDashboardModel`.
 */
export type TrustDashboardSourceResult = {
  summary: TripSummary;
  validationReport: ValidationReport;
  providerCoverage: ProviderCoverageData;
  readinessChecklist: ReadinessChecklist;
  dailyPlans: DailyPlan[];
  accommodationInventoryReport: AccommodationInventoryReport | null;
  flightInventoryReport: FlightInventoryReport | null;
  routeFeasibilityReport: RouteFeasibilityReport | null;
  routeAwareSequencingReport: RouteAwareSequencingReport | null;
  travelTimeBufferReport: TravelTimeBufferReport | null;
  aiCandidateReviewReport: AICandidateReviewReport | null;
  aiCandidatePromotionReport: AICandidatePromotionReport | null;
  regenerationReadiness: RegenerationReadiness;
  planDiffPreview: PlanDiffPreview;
  regenerationAttempts: RegenerationAttempt[];
  pendingFeedbackSummary: PendingFeedbackSummary;
  versionHistory: VersionHistoryItem[];
};

// ---------------------------------------------------------------------------
// Model types
// ---------------------------------------------------------------------------

export type TrustDashboardCategoryId =
  | "core_trip_grounding"
  | "places_and_experiences"
  | "lodging_inventory"
  | "flight_inventory"
  | "routes_and_movement"
  | "weather_holiday_currency"
  | "ai_suggested_candidates"
  | "regeneration_and_change_safety"
  | "validation_summary";

// A small, closed, restrained vocabulary (Step 176A section 7) -- every category
// below picks one of these, never a freeform string. None of these words
// imply certainty, booking, safety, or trip quality; they only restate
// what an existing backend status/count already supports.
export type TrustDashboardStatusKind =
  | "available"
  | "needs_review"
  | "not_connected"
  | "unavailable"
  | "failed"
  | "partial"
  | "open_data_only"
  | "scraped_source"
  | "no_pending_feedback"
  | "blocked_by_locks";

export type TrustDashboardCategoryView = {
  id: TrustDashboardCategoryId;
  title: string;
  statusKind: TrustDashboardStatusKind;
  statusLabel: string;
  detail: string;
  supportingFacts: string[];
  relatedValidationIssues: ValidationIssue[];
  /** An existing `id` already present on `frontend/app/page.tsx` (e.g. from
   * `ResultGroupHeader`) that a future dashboard render could link to --
   * never a newly-invented anchor, and unused until Step 176C/D actually
   * wires up navigation. */
  detailAnchorId: string | null;
};

export type TrustDashboardModel = {
  /** Restated verbatim from `validationReport.readiness_status` -- the
   * single existing source of truth for overall plan readiness. This
   * model never computes its own separate readiness verdict. */
  readinessStatus: ValidationReport["readiness_status"];
  categories: TrustDashboardCategoryView[];
};

// ---------------------------------------------------------------------------
// Small, restrained label vocabulary
// ---------------------------------------------------------------------------

const STATUS_KIND_LABELS: Record<TrustDashboardStatusKind, string> = {
  available: "Available",
  needs_review: "Needs review",
  not_connected: "Not connected",
  unavailable: "Unavailable",
  failed: "Failed",
  partial: "Partial",
  open_data_only: "Open-data only",
  scraped_source: "Scraped/manual source",
  no_pending_feedback: "No pending feedback",
  blocked_by_locks: "Blocked by locks",
};

// Severity ranking used only to pick the single worst status among several
// already-known statuses being combined into one category tile (e.g.
// weather + holidays + currency coverage) -- a display aggregation rule,
// not a new fact. Higher number = shown in preference to a lower one.
const STATUS_KIND_SEVERITY: Record<TrustDashboardStatusKind, number> = {
  failed: 100,
  unavailable: 90,
  not_connected: 80,
  blocked_by_locks: 75,
  partial: 60,
  needs_review: 50,
  scraped_source: 40,
  open_data_only: 30,
  no_pending_feedback: 20,
  available: 10,
};

function worstStatusKind(kinds: TrustDashboardStatusKind[]): TrustDashboardStatusKind {
  if (kinds.length === 0) return "not_connected";
  return kinds.reduce((worst, kind) =>
    STATUS_KIND_SEVERITY[kind] > STATUS_KIND_SEVERITY[worst] ? kind : worst,
  );
}

/**
 * Maps one raw `provider_coverage`/report `status` string (the existing
 * `ProviderStatus`/`AccommodationSearchStatus`/`FlightSearchStatus`
 * vocabulary the backend already uses, plus the synthetic
 * `open_poi_available` value `ProviderCoverageService` mints for
 * OSM-backed accommodation POIs) onto this module's small status-kind
 * vocabulary. Never invents a status the backend didn't report -- an
 * unrecognized/unlisted value conservatively falls back to
 * `needs_review` rather than being guessed as good or bad.
 */
function coverageValueToStatusKind(value: string | null | undefined): TrustDashboardStatusKind {
  switch (value) {
    case null:
    case undefined:
    case "not_connected":
    case "not_requested":
      return "not_connected";
    case "unavailable":
      return "unavailable";
    case "failed":
      return "failed";
    case "retrying":
      return "needs_review";
    case "success":
    case "fallback_used":
      return "available";
    case "partial":
      return "partial";
    case "open_poi_available":
      return "open_data_only";
    default:
      return "needs_review";
  }
}

function coverageValueDisplay(value: string | null | undefined): string {
  return value ?? "not_connected";
}

// ---------------------------------------------------------------------------
// Validation-issue grouping helpers
// ---------------------------------------------------------------------------

function issueMentions(issue: ValidationIssue, substrings: string[]): boolean {
  const haystack = issue.message.toLowerCase();
  return substrings.some((needle) => haystack.includes(needle.toLowerCase()));
}

function issuesByCategory(
  issues: ValidationIssue[],
  categories: string[],
): ValidationIssue[] {
  return issues.filter((issue) => categories.includes(issue.category));
}

function countBySeverity(
  issues: ValidationIssue[],
  severity: ValidationIssue["severity"],
): number {
  return issues.filter((issue) => issue.severity === severity).length;
}

function distinctCategories(issues: ValidationIssue[]): string[] {
  const seen: string[] = [];
  for (const issue of issues) {
    if (!seen.includes(issue.category)) seen.push(issue.category);
  }
  return seen;
}

// ---------------------------------------------------------------------------
// Category builders
// ---------------------------------------------------------------------------

function buildCoreTripGroundingCategory(
  result: TrustDashboardSourceResult,
): TrustDashboardCategoryView {
  const readinessStatus = result.validationReport.readiness_status;
  const statusKind: TrustDashboardStatusKind =
    readinessStatus === "ready"
      ? "available"
      : readinessStatus === "blocked"
        ? "unavailable"
        : "needs_review";

  const detail =
    result.summary.main_blocking_reason ??
    result.summary.main_review_reason ??
    `This plan's readiness status is "${readinessStatus}".`;

  const supportingFacts = [
    `Places coverage: ${coverageValueDisplay(result.providerCoverage.provider_coverage.places)}`,
    `Data sources used: ${result.providerCoverage.data_sources_used.length}`,
    `Attraction candidates: ${result.summary.candidate_pois_count}`,
    `Restaurant candidates: ${result.summary.candidate_restaurants_count}`,
    `Accommodation POI candidates: ${result.summary.candidate_accommodation_pois_count}`,
    `Scheduled experiences: ${result.summary.scheduled_experiences_count}`,
  ];

  return {
    id: "core_trip_grounding",
    title: "Core trip grounding",
    statusKind,
    statusLabel: STATUS_KIND_LABELS[statusKind],
    detail,
    supportingFacts,
    relatedValidationIssues: result.validationReport.critical_issues,
    // Step 176D: links to the existing "Validation report" section
    // (`id="validation-report"`, added in that step) -- the direct source
    // of this category's own `statusKind`/`relatedValidationIssues`.
    detailAnchorId: "validation-report",
  };
}

function buildPlacesAndExperiencesCategory(
  result: TrustDashboardSourceResult,
): TrustDashboardCategoryView {
  const coverage = result.providerCoverage.provider_coverage;
  const statusKind = worstStatusKind([
    coverageValueToStatusKind(coverage.places),
    coverageValueToStatusKind(coverage.restaurants),
    coverageValueToStatusKind(coverage.accommodations),
  ]);

  const checklistLabels = [
    "Provider-backed attractions available",
    "Restaurant candidates available",
    "Accommodation POI candidates available",
  ];
  const checklistFacts = result.readinessChecklist.items
    .filter((item) => checklistLabels.includes(item.label))
    .map((item) => `${item.label}: ${item.status} -- ${item.explanation}`);

  const supportingFacts = [
    `Places coverage: ${coverageValueDisplay(coverage.places)}`,
    `Restaurants coverage: ${coverageValueDisplay(coverage.restaurants)}`,
    `Accommodation POI coverage: ${coverageValueDisplay(coverage.accommodations)} (open-data location candidates, not bookable lodging inventory)`,
    ...checklistFacts,
  ];

  return {
    id: "places_and_experiences",
    title: "Places and experiences",
    statusKind,
    statusLabel: STATUS_KIND_LABELS[statusKind],
    detail:
      "Attraction, restaurant, and accommodation-POI candidates are provider-backed or open-data location candidates only -- never a rating, price, opening hour, or booking confirmation.",
    supportingFacts,
    relatedValidationIssues: issuesByCategory(result.validationReport.warnings, [
      "must_visit",
      "geographic_spread",
    ]),
    // Step 176D: this category's own supportingFacts are literally
    // `provider_coverage.{places,restaurants,accommodations}` values, so
    // the existing "Provider coverage" section (`id="provider-coverage"`)
    // is the most specific match.
    detailAnchorId: "provider-coverage",
  };
}

function buildLodgingInventoryCategory(
  result: TrustDashboardSourceResult,
): TrustDashboardCategoryView {
  const coverage = result.providerCoverage.provider_coverage;
  const report = result.accommodationInventoryReport;

  let statusKind: TrustDashboardStatusKind;
  const supportingFacts: string[] = [
    `provider_coverage.hotel_prices: ${coverageValueDisplay(coverage.hotel_prices)}`,
    `provider_coverage.accommodations (open-data location candidates, not bookable lodging inventory): ${coverageValueDisplay(coverage.accommodations)}`,
  ];

  if (report === null) {
    statusKind = "not_connected";
    supportingFacts.push("No accommodation inventory report has been computed for this trip yet.");
  } else {
    const hasScrapedOffer = report.offers.some((offer) => offer.scraped_provenance !== null);
    if (report.status === "success" && report.offers.length > 0) {
      statusKind = hasScrapedOffer ? "scraped_source" : "available";
      supportingFacts.push(
        `${report.offers.length} accommodation inventory offer(s) available via ${report.provider}. This is not a confirmed booking.`,
      );
      if (hasScrapedOffer) {
        supportingFacts.push(
          "At least one offer is scraped_public_page/experimental/fragile data -- not official-provider data, not verified for price, availability, rating, or booking-link accuracy.",
        );
      }
    } else if (report.status === "failed") {
      statusKind = "failed";
      supportingFacts.push(report.message ?? "The accommodation inventory provider request failed.");
    } else {
      statusKind = "unavailable";
      supportingFacts.push(
        report.message ?? "No bookable lodging offers were available from the connected provider.",
      );
    }
  }

  return {
    id: "lodging_inventory",
    title: "Lodging inventory",
    statusKind,
    statusLabel: STATUS_KIND_LABELS[statusKind],
    detail:
      "Bookable lodging inventory is a separate concept from open-data accommodation-like location candidates. Prices, availability, ratings, amenities, and booking links are only ever present when an offer explicitly carries them.",
    supportingFacts,
    relatedValidationIssues: [
      ...issuesByCategory(result.validationReport.warnings, ["accommodation_inventory"]),
      ...issuesByCategory(result.validationReport.warnings, ["provider_coverage_consistency"]).filter(
        (issue) => issueMentions(issue, ["hotel_prices"]),
      ),
    ],
    // Step 176D: links to the existing "Bookable lodging inventory"
    // section (`id="accommodation-inventory"`, added in that step).
    detailAnchorId: "accommodation-inventory",
  };
}

function buildFlightInventoryCategory(
  result: TrustDashboardSourceResult,
): TrustDashboardCategoryView {
  const coverage = result.providerCoverage.provider_coverage;
  const report = result.flightInventoryReport;

  let statusKind: TrustDashboardStatusKind;
  const supportingFacts: string[] = [
    `provider_coverage.flights: ${coverageValueDisplay(coverage.flights)}`,
  ];

  if (report === null) {
    statusKind = "not_connected";
    supportingFacts.push("No flight inventory report has been computed for this trip yet.");
  } else {
    const hasScrapedOffer = report.offers.some((offer) => offer.scraped_provenance !== null);
    if (report.status === "success" && report.offers.length > 0) {
      statusKind = hasScrapedOffer ? "scraped_source" : "available";
      supportingFacts.push(
        `${report.offers.length} flight inventory offer(s) available via ${report.provider}. This is not a confirmed booking, and flights are never scheduled into the itinerary as a daily experience.`,
      );
      if (hasScrapedOffer) {
        supportingFacts.push(
          "At least one offer is scraped_public_page/experimental/fragile data -- not official-provider data, not verified for schedule, price, availability, baggage, or booking-link accuracy.",
        );
      }
    } else if (report.status === "failed") {
      statusKind = "failed";
      supportingFacts.push(report.message ?? "The flight inventory provider request failed.");
    } else {
      statusKind = "unavailable";
      supportingFacts.push(
        report.message ?? "No flight offers were available from the connected provider.",
      );
    }
  }

  return {
    id: "flight_inventory",
    title: "Flight inventory",
    statusKind,
    statusLabel: STATUS_KIND_LABELS[statusKind],
    detail:
      "Airline, schedule, price, availability, baggage, and booking-link data are only ever present when an offer explicitly carries them.",
    supportingFacts,
    relatedValidationIssues: [
      ...issuesByCategory(result.validationReport.warnings, ["flight_inventory"]),
      ...issuesByCategory(result.validationReport.warnings, ["provider_coverage_consistency"]).filter(
        (issue) => issueMentions(issue, ["flights"]),
      ),
    ],
    // Step 176D: links to the existing "Flight inventory" section
    // (`id="flight-inventory"`, added in that step).
    detailAnchorId: "flight-inventory",
  };
}

function buildRoutesAndMovementCategory(
  result: TrustDashboardSourceResult,
): TrustDashboardCategoryView {
  const coverage = result.providerCoverage.provider_coverage;
  const routeFeasibilityStatus = result.routeFeasibilityReport?.status ?? null;
  const routeAwareStatus = result.routeAwareSequencingReport?.status ?? null;
  const travelTimeBufferStatus = result.travelTimeBufferReport?.status ?? null;

  const statusKind = worstStatusKind([
    coverageValueToStatusKind(coverage.routes),
    coverageValueToStatusKind(routeFeasibilityStatus),
    coverageValueToStatusKind(routeAwareStatus),
    coverageValueToStatusKind(travelTimeBufferStatus),
  ]);

  // Route geometry is only ever counted from the existing
  // `route_geometry` field already present on each `TravelTimeBuffer` --
  // never inferred from stop coordinates, and never a straight-line
  // substitute (mirrors backend `_route_geometry_leg_counts`, Step 175C).
  const buffers = result.travelTimeBufferReport?.buffers ?? [];
  const legsWithGeometry = buffers.filter((buffer) => Boolean(buffer.route_geometry)).length;

  const supportingFacts = [
    `provider_coverage.routes: ${coverageValueDisplay(coverage.routes)} (route data available only when this is a connected/successful status)`,
    `Route feasibility status: ${routeFeasibilityStatus ?? "not computed"}`,
    `Route-aware sequencing status: ${routeAwareStatus ?? "not computed"}`,
    `Travel-time buffer status: ${travelTimeBufferStatus ?? "not computed"}`,
    buffers.length > 0
      ? `Route path geometry present on ${legsWithGeometry} of ${buffers.length} scheduled leg(s).`
      : "Route path geometry unavailable -- no scheduled legs to report on.",
  ];

  return {
    id: "routes_and_movement",
    title: "Routes and movement",
    statusKind,
    statusLabel: STATUS_KIND_LABELS[statusKind],
    detail:
      "Route feasibility, route-aware sequencing, and travel-time buffers only ever reflect real provider-backed lookups. A missing route provider means this data is unavailable, not that travel between stops is instant or unnecessary.",
    supportingFacts,
    relatedValidationIssues: [
      ...issuesByCategory(result.validationReport.warnings, [
        "feasibility",
        "travel_time_buffer",
        "route_aware_sequencing",
        "movement_data",
        "route_geometry",
      ]),
      ...issuesByCategory(result.validationReport.warnings, ["provider_coverage_consistency"]).filter(
        (issue) => issueMentions(issue, ["routes"]),
      ),
    ],
    // Step 176D: the real per-day movement rows, route-aware order
    // badges, and provider-backed route paths this category summarizes
    // are rendered inline in "Draft itinerary" (`id="draft-itinerary"`,
    // an existing `ResultGroupHeader` anchor) -- not in the legacy,
    // always-`not_connected` "Route feasibility" section
    // (`RouteFeasibilityContext`, a separate, mostly-unused data-model
    // foundation), so that is deliberately not used as the target here.
    detailAnchorId: "draft-itinerary",
  };
}

function buildWeatherHolidayCurrencyCategory(
  result: TrustDashboardSourceResult,
): TrustDashboardCategoryView {
  const coverage = result.providerCoverage.provider_coverage;
  const statusKind = worstStatusKind([
    coverageValueToStatusKind(coverage.weather),
    coverageValueToStatusKind(coverage.holidays),
    coverageValueToStatusKind(coverage.currency),
  ]);

  const contextChecklistLabels = ["Weather impact checked", "Holiday/closure context checked"];
  const checklistFacts = result.readinessChecklist.items
    .filter((item) => contextChecklistLabels.includes(item.label))
    .map((item) => `${item.label}: ${item.status} -- ${item.explanation}`);

  return {
    id: "weather_holiday_currency",
    title: "Weather, holiday, and currency context",
    statusKind,
    statusLabel: STATUS_KIND_LABELS[statusKind],
    detail:
      "Weather, holiday, and currency data (when connected) is contextual only -- it is not yet used to adjust the itinerary, and a currency rate is a single-unit conversion, never a calculated total trip cost.",
    supportingFacts: [
      `provider_coverage.weather: ${coverageValueDisplay(coverage.weather)}`,
      `provider_coverage.holidays: ${coverageValueDisplay(coverage.holidays)}`,
      `provider_coverage.currency: ${coverageValueDisplay(coverage.currency)}`,
      ...checklistFacts,
    ],
    relatedValidationIssues: issuesByCategory(result.validationReport.warnings, [
      "weather",
      "holidays",
      "budget",
    ]),
    detailAnchorId: "travel-context",
  };
}

function buildAiSuggestedCandidatesCategory(
  result: TrustDashboardSourceResult,
): TrustDashboardCategoryView {
  const review = result.aiCandidateReviewReport;
  const promotion = result.aiCandidatePromotionReport;

  const scheduledPromotedCount = result.dailyPlans.reduce(
    (total, day) => total + day.experiences.filter((experience) => experience.promoted_from_ai).length,
    0,
  );

  let statusKind: TrustDashboardStatusKind;
  if (review === null || review.status === "no_candidate_data") {
    statusKind = "not_connected";
  } else if (review.total_ai_candidates === 0) {
    statusKind = "unavailable";
  } else if (scheduledPromotedCount > 0) {
    statusKind = "available";
  } else if (review.eligible_for_promotion > 0) {
    statusKind = "needs_review";
  } else {
    statusKind = "unavailable";
  }

  const supportingFacts = [
    review === null
      ? "No AI candidate discovery has run for this trip."
      : `AI candidate review status: ${review.status}. Total AI-proposed: ${review.total_ai_candidates}, provider-grounded: ${review.grounded_candidates}, eligible for promotion: ${review.eligible_for_promotion}.`,
    promotion === null
      ? "No AI candidate promotion has run for this trip."
      : `Promoted: ${promotion.promoted_count}, skipped: ${promotion.skipped_count}.`,
    `Scheduled into itinerary (promoted_from_ai=true on a scheduled experience): ${scheduledPromotedCount}.`,
  ];

  return {
    id: "ai_suggested_candidates",
    title: "AI-suggested candidates",
    statusKind,
    statusLabel: STATUS_KIND_LABELS[statusKind],
    detail:
      "An AI-proposed candidate is not a real place until it is provider-grounded; being eligible or promoted is not the same as being scheduled into the itinerary.",
    supportingFacts,
    // No PlanValidatorService category exists for AI candidate review/
    // promotion today -- an honestly empty list, not an omission.
    relatedValidationIssues: [],
    // Step 176D: links to the existing "AI candidate review" section
    // (`id="ai-candidate-review"`, added in that step).
    detailAnchorId: "ai-candidate-review",
  };
}

function buildRegenerationAndChangeSafetyCategory(
  result: TrustDashboardSourceResult,
): TrustDashboardCategoryView {
  const readiness = result.regenerationReadiness;
  const diffPreview = result.planDiffPreview;
  const pendingSummary = result.pendingFeedbackSummary;
  const latestAttempt =
    result.regenerationAttempts.length > 0
      ? result.regenerationAttempts[result.regenerationAttempts.length - 1]
      : null;

  let statusKind: TrustDashboardStatusKind;
  if (pendingSummary.status === "none") {
    statusKind = "no_pending_feedback";
  } else if (readiness.active_lock_count > 0) {
    statusKind = "blocked_by_locks";
  } else if (readiness.can_regenerate) {
    statusKind = "available";
  } else {
    statusKind = "needs_review";
  }
  // A failed latest attempt is worth surfacing even when the readiness gate
  // currently looks "available" again (e.g. after new feedback) -- this
  // never overrides an active-lock block, which is the more specific/
  // actionable reason.
  if (latestAttempt?.status === "failed" && statusKind !== "blocked_by_locks") {
    statusKind = "needs_review";
  }

  const supportingFacts = [
    `Pending feedback: ${pendingSummary.status} (${pendingSummary.note})`,
    `regeneration_readiness.can_regenerate: ${readiness.can_regenerate}`,
    `regeneration_readiness.active_lock_count: ${readiness.active_lock_count}`,
    `plan_diff_preview.regeneration_available: ${diffPreview.regeneration_available}`,
  ];
  if (readiness.can_regenerate) {
    supportingFacts.push("Pending feedback can be applied through deterministic regeneration.");
  }
  if (readiness.blocked_by.length > 0) {
    supportingFacts.push(`Blocked by: ${readiness.blocked_by.join("; ")}`);
  }
  if (readiness.can_regenerate !== diffPreview.regeneration_available) {
    supportingFacts.push(
      "Note: regeneration_readiness and plan_diff_preview report different availability for this plan.",
    );
  }
  if (latestAttempt !== null) {
    supportingFacts.push(`Latest regeneration attempt: ${latestAttempt.status}`);
  }

  return {
    id: "regeneration_and_change_safety",
    title: "Regeneration and change safety",
    statusKind,
    statusLabel: STATUS_KIND_LABELS[statusKind],
    detail:
      "Regeneration reruns only the deterministic stages pending feedback maps to. It never claims a locked item will be preserved beyond what the current MVP scope supports (locks currently block regeneration entirely), and it never claims the resulting plan will be better.",
    supportingFacts,
    relatedValidationIssues: issuesByCategory(result.validationReport.warnings, [
      "regeneration",
      "regeneration_state_consistency",
    ]),
    // Step 176D: links to the existing "Regeneration readiness" section
    // (`id="regeneration-readiness"`, added in that step) -- the primary
    // gate (`can_regenerate`/`blocked_by`/`active_lock_count`) this
    // category restates.
    detailAnchorId: "regeneration-readiness",
  };
}

function buildValidationSummaryCategory(
  result: TrustDashboardSourceResult,
): TrustDashboardCategoryView {
  const { critical_issues: criticalIssues, warnings } = result.validationReport;
  const warningCount = countBySeverity(warnings, "warning");
  const suggestionCount = countBySeverity(warnings, "suggestion");

  let statusKind: TrustDashboardStatusKind;
  if (criticalIssues.length > 0) {
    statusKind = "failed";
  } else if (warningCount > 0) {
    statusKind = "needs_review";
  } else {
    statusKind = "available";
  }

  const supportingFacts = [
    `Critical issues: ${criticalIssues.length}`,
    `Warnings: ${warningCount} across ${distinctCategories(warnings.filter((issue) => issue.severity === "warning")).length} categor${
      distinctCategories(warnings.filter((issue) => issue.severity === "warning")).length === 1
        ? "y"
        : "ies"
    }`,
    `Suggestions: ${suggestionCount} across ${distinctCategories(warnings.filter((issue) => issue.severity === "suggestion")).length} categor${
      distinctCategories(warnings.filter((issue) => issue.severity === "suggestion")).length === 1
        ? "y"
        : "ies"
    }`,
    `Provider coverage notes: ${result.validationReport.provider_coverage_notes.length}`,
    `Unavailable data notes: ${result.validationReport.unavailable_data_notes.length}`,
  ];

  return {
    id: "validation_summary",
    title: "Validation summary",
    statusKind,
    statusLabel: STATUS_KIND_LABELS[statusKind],
    detail:
      "This restates the validation report's own counts. It never marks a plan ready by itself -- readiness_status remains the single source of truth.",
    supportingFacts,
    relatedValidationIssues: [...criticalIssues, ...warnings],
    // Step 176D: links to the existing "Validation report" section
    // (`id="validation-report"`, added in that step) -- more specific
    // than the broader "review-required" jump-link group it lives in.
    detailAnchorId: "validation-report",
  };
}

// ---------------------------------------------------------------------------
// Public entry point
// ---------------------------------------------------------------------------

/**
 * Builds a display-ready trust dashboard model purely from fields already
 * present on `result`. Pure function: never mutates `result` or any nested
 * object, never fetches anything, never calls a browser API (no `window`/
 * `document`/`fetch`/`localStorage`), and depends on no React state or
 * hook. Every status/count/label is a direct read or a plain aggregation
 * (count, "worst of a small fixed list", substring match against an
 * already-existing message) of a field the backend already computed --
 * this never calculates a distance, duration, or route geometry itself,
 * never invents a price/rating/booking-link value, and never says an
 * AI-proposed candidate is scheduled unless a real scheduled experience
 * already carries `promoted_from_ai=true`.
 */
export function buildTrustDashboardModel(
  result: TrustDashboardSourceResult,
): TrustDashboardModel {
  return {
    readinessStatus: result.validationReport.readiness_status,
    categories: [
      buildCoreTripGroundingCategory(result),
      buildPlacesAndExperiencesCategory(result),
      buildLodgingInventoryCategory(result),
      buildFlightInventoryCategory(result),
      buildRoutesAndMovementCategory(result),
      buildWeatherHolidayCurrencyCategory(result),
      buildAiSuggestedCandidatesCategory(result),
      buildRegenerationAndChangeSafetyCategory(result),
      buildValidationSummaryCategory(result),
    ],
  };
}
