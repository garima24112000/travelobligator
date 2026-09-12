"use client";

import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import type * as Leaflet from "leaflet";
import {
  ApiRequestError,
  createTrip,
  createTripLock,
  deleteTripLock,
  generatePlan,
  getAiCandidateReview,
  getCurrentUser,
  getDestinationContext,
  getExperiencePlan,
  getGenerationProgress,
  getProviderCoverage,
  getRegenerationAttempts,
  getRegenerationReadiness,
  getTrip,
  getTripSummary,
  getValidationReport,
  listTrips,
  login,
  logout,
  promoteAiCandidates,
  requestRegeneration,
  signup,
  submitTripFeedback,
} from "@/lib/api";
import { buildTrustDashboardModel } from "@/lib/trust-dashboard";
import type {
  TrustDashboardCategoryView,
  TrustDashboardModel,
  TrustDashboardStatusKind,
} from "@/lib/trust-dashboard";
import type {
  AccommodationInventoryReport,
  AccommodationOffer,
  AccommodationRatingDetails,
  AccommodationSuggestion,
  AICandidatePromotionReport,
  AICandidateReviewItem,
  AICandidateReviewReport,
  CandidatePoi,
  ChecklistItemStatus,
  CurrencyContext,
  DailyPlan,
  DecisionSummary,
  ExperienceItem,
  FeedbackChangePreview,
  FeedbackEvent,
  FlightInventoryReport,
  FlightOffer,
  FlightSegment,
  GenerationProgress,
  GeoPoint,
  HolidayContext,
  ImplementationGaps,
  ItineraryNarrativeDayOutput,
  ItineraryNarrativeReport,
  PendingFeedbackSummary,
  PlanDiffPreview,
  ProviderCoverageData,
  ProviderStatusEntry,
  PromotedAICandidate,
  PublicUser,
  ReadinessChecklist,
  RegenerateResponseData,
  RegenerationAttempt,
  RegenerationReadiness,
  RestaurantSuggestion,
  RouteAwareSequencingReport,
  RouteFeasibilityContext,
  RouteFeasibilityReport,
  RoutePathPoint,
  ScrapedAccommodationProvenance,
  ScrapedFlightProvenance,
  StayAreaGuidance,
  TravelTimeBuffer,
  TravelTimeBufferReport,
  TripListItem,
  TripRequestInput,
  TripSummary,
  UserLock,
  ValidationReport,
  VersionHistoryItem,
  WeatherContext,
} from "@/lib/types";

const DEFAULT_TRIP_REQUEST: TripRequestInput = {
  destination_scope: "single_city",
  primary_destination: "Lisbon, Portugal",
  origin_city: "New York",
  start_date: "2026-08-10",
  end_date: "2026-08-12",
  travelers_count: 2,
  travel_group_type: "couple",
  pace: "balanced",
};

// Shared keyboard-focus ring for interactive elements (Step 179D) -- a
// visible focus style purely for keyboard/screen-reader navigation. Appending
// this to an element's className never changes what the element does, only
// how it looks when focused.
const FOCUS_RING_CLASSNAME =
  "focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/50";

type PlanResult = {
  summary: TripSummary;
  candidatePois: CandidatePoi[];
  candidateRestaurants: CandidatePoi[];
  candidateAccommodationPois: CandidatePoi[];
  dailyPlans: DailyPlan[];
  stayAreaGuidance: StayAreaGuidance;
  decisionSummary: DecisionSummary;
  implementationGaps: ImplementationGaps;
  readinessChecklist: ReadinessChecklist;
  routeFeasibilityContext: RouteFeasibilityContext;
  weatherContext: WeatherContext | null;
  holidayContext: HolidayContext | null;
  currencyContext: CurrencyContext | null;
  validationReport: ValidationReport;
  providerCoverage: ProviderCoverageData;
  destinationAssumptions: string[];
  destinationConfidence: number;
  experienceAssumptions: string[];
  experienceConfidence: number;
  feedbackHistory: FeedbackEvent[];
  pendingFeedbackSummary: PendingFeedbackSummary;
  userLocks: UserLock[];
  versionHistory: VersionHistoryItem[];
  planDiffPreview: PlanDiffPreview;
  regenerationReadiness: RegenerationReadiness;
  regenerationAttempts: RegenerationAttempt[];
  accommodationInventoryReport: AccommodationInventoryReport | null;
  flightInventoryReport: FlightInventoryReport | null;
  aiCandidateReviewReport: AICandidateReviewReport | null;
  aiCandidatePromotionReport: AICandidatePromotionReport | null;
  routeAwareSequencingReport: RouteAwareSequencingReport | null;
  routeFeasibilityReport: RouteFeasibilityReport | null;
  travelTimeBufferReport: TravelTimeBufferReport | null;
  itineraryNarrativeReport: ItineraryNarrativeReport | null;
};

function parseCommaList(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

function readinessLabel(status: string | null): string {
  if (status === "ready") return "Ready";
  if (status === "needs_review") return "Needs Review";
  if (status === "blocked") return "Blocked";
  return "Unknown";
}

/**
 * Compact, honest route-aware sequencing status label for one day (Step
 * 172C, docs/16_frontend_architecture.md). Reads only fields the backend
 * already computed -- `experience.route_aware_provenance` (Step 172A) and
 * `RouteAwareSequencingReport.suggestions` (Step 166A/166B) -- and never
 * reorders `day.experiences` or invents a status the backend data doesn't
 * support.
 *
 * - "Provider-grounded route order": at least one experience in this day
 *   has `route_aware_provenance === "provider_backed"`, meaning
 *   `RouteAwareSequencingService.apply_report` actually reordered this
 *   day using a real, successful, provider-backed route.
 * - "Route order needs review": no provider-backed reorder happened, but
 *   this day's own sequencing suggestion exists and honestly reports
 *   `partial`/`unavailable`/`failed` -- a real attempt was made and ran
 *   into a genuine issue (e.g. some but not all routes succeeded).
 * - "Suggested stop order": the safe default -- no provider-backed
 *   reorder happened and there is no report, no matching day suggestion,
 *   or the day suggestion is `not_connected`/`success`-but-unapplied.
 *   This is the common case whenever no routing provider is connected.
 */
function routeAwareDayStatusLabel(
  day: DailyPlan,
  report: RouteAwareSequencingReport | null,
): string {
  const hasProviderBackedOrder = day.experiences.some(
    (experience) => experience.route_aware_provenance === "provider_backed",
  );
  if (hasProviderBackedOrder) {
    return "Provider-grounded route order";
  }

  const daySuggestion = report?.suggestions.find(
    (suggestion) => suggestion.day_index === day.day_number,
  );
  if (
    daySuggestion &&
    (daySuggestion.status === "partial" ||
      daySuggestion.status === "unavailable" ||
      daySuggestion.status === "failed")
  ) {
    return "Route order needs review";
  }

  return "Suggested stop order";
}

/**
 * Whether to show an honest "movement data unavailable" note near the
 * itinerary (Step 172C). `true` whenever there is no
 * `RouteFeasibilityReport` at all, or its own `status` says no usable
 * route data exists (`not_connected`/`unavailable`/`failed`) -- `false`
 * (no note shown) when real route data exists (`success`/`partial`),
 * since showing the note then would understate what is actually
 * available. This never renders a distance/duration value itself --
 * only whether movement data exists at all.
 */
function movementDataIsUnavailable(report: RouteFeasibilityReport | null): boolean {
  if (!report) return true;
  return (
    report.status === "not_connected" ||
    report.status === "unavailable" ||
    report.status === "failed"
  );
}

/**
 * Looks up the backend's own travel-time-buffer entry for the leg
 * between two consecutive scheduled experiences (Step 172D,
 * docs/16_frontend_architecture.md). `TravelTimeBufferService` (backend)
 * already builds one `TravelTimeBuffer` per consecutive pair in every
 * scheduled day, regardless of whether a routing provider is connected --
 * this only reads that existing entry by experience id; it never computes
 * a distance/duration itself and never invents an entry that isn't
 * already there. Returns `null` when there is no report at all (e.g. an
 * older trip persisted before Step 166C) or no matching entry, so a
 * caller can render nothing rather than guessing.
 */
function findMovementBetweenStops(
  fromExperienceId: string,
  toExperienceId: string,
  report: TravelTimeBufferReport | null,
): TravelTimeBuffer | null {
  if (!report) return null;
  return (
    report.buffers.find(
      (buffer) =>
        buffer.from_experience_id === fromExperienceId &&
        buffer.to_experience_id === toExperienceId,
    ) ?? null
  );
}

/** Whether a movement row should render at all for this leg (Step 172D)
 * -- only when the backend already has a matching `TravelTimeBuffer`
 * entry. A missing report/entry renders nothing, never a dummy row.
 */
function shouldRenderMovementRow(buffer: TravelTimeBuffer | null): buffer is TravelTimeBuffer {
  return buffer !== null;
}

/**
 * Whether a raw (lat, lon) pair is safe to hand to Leaflet at all (Step
 * 173D, docs/16_frontend_architecture.md) -- finite (rejects `NaN`/
 * `Infinity`, which a malformed or hand-edited persisted record could
 * carry) and within the same bounds the backend's own `GeoPoint`/
 * `RoutePathPoint` models enforce (`lat` in [-90, 90], `lon` in
 * [-180, 180]). Backend validation already rejects an out-of-range
 * coordinate at write time, but this is a second, independent guard on
 * the read side so a pre-existing/hand-edited local JSON record can
 * never crash `L.marker`/`L.polyline`/`L.latLngBounds` or silently
 * distort the map's fit-bounds computation.
 */
function isValidGeoCoordinate(lat: number, lon: number): boolean {
  return (
    Number.isFinite(lat) &&
    Number.isFinite(lon) &&
    lat >= -90 &&
    lat <= 90 &&
    lon >= -180 &&
    lon <= 180
  );
}

/**
 * Extracts a leg's real, provider-backed route path points -- only when
 * every one of these already-backend-decided conditions holds (Step
 * 173B, docs/16_frontend_architecture.md): the leg has a matching
 * `TravelTimeBuffer` entry, that buffer's `status === "success"` (never
 * `not_connected`/`unavailable`/`failed`/`partial`), and its
 * `route_geometry` is present with at least two points. Returns `null`
 * otherwise -- the caller must render no path for that leg, never a
 * straight line between the two stops' own coordinates as a substitute.
 *
 * Every returned point is copied verbatim from the backend; this
 * function never creates, interpolates, or drops a point to "fix" a
 * malformed one -- a single non-finite or out-of-range (Step 173D)
 * lat/lon anywhere in the list discards the whole leg's path rather than
 * drawing a partial/corrupted one.
 */
function drawableRouteGeometry(buffer: TravelTimeBuffer | null): RoutePathPoint[] | null {
  if (!buffer || buffer.status !== "success" || buffer.route_geometry === null) {
    return null;
  }
  const points = buffer.route_geometry;
  if (points.length < 2) {
    return null;
  }
  const allValid = points.every((point) => isValidGeoCoordinate(point.lat, point.lon));
  return allValid ? points : null;
}

/**
 * Whether at least one leg of this day already has a drawable,
 * provider-backed route path (Step 173C, docs/16_frontend_architecture.md).
 * Walks the same consecutive-pair indexing `DayMapPreview` itself uses so
 * this can gate a day-level legend without duplicating the per-leg
 * drawing loop's own gating logic.
 */
function dayHasDrawableRouteGeometry(
  experiences: ExperienceItem[],
  report: TravelTimeBufferReport | null,
): boolean {
  for (let index = 0; index < experiences.length - 1; index += 1) {
    const buffer = findMovementBetweenStops(
      experiences[index].experience_id,
      experiences[index + 1].experience_id,
      report,
    );
    if (drawableRouteGeometry(buffer) !== null) {
      return true;
    }
  }
  return false;
}

/**
 * Whether this day has any backend movement/route status data at all --
 * i.e. at least one leg has a matching `TravelTimeBuffer` entry, whatever
 * its status. Used only to decide whether an honest "Route path
 * unavailable" legend line is backed by a real backend status rather than
 * simply the absence of any report (e.g. an older trip persisted before
 * Step 166C), which would make that wording unsupported.
 */
function dayHasMovementStatusData(
  experiences: ExperienceItem[],
  report: TravelTimeBufferReport | null,
): boolean {
  if (!report) return false;
  for (let index = 0; index < experiences.length - 1; index += 1) {
    const buffer = findMovementBetweenStops(
      experiences[index].experience_id,
      experiences[index + 1].experience_id,
      report,
    );
    if (buffer !== null) {
      return true;
    }
  }
  return false;
}

/**
 * Small legend label for the day map (Step 173C). Returns
 * "Provider-backed route path" only when this day actually has a drawn
 * path; "Route path unavailable" only when no path is drawn but the
 * backend still reports real movement/route status data for this day
 * (so the claim is backed by an actual status, not just a missing
 * report); `null` otherwise -- e.g. an older trip with no travel-time
 * buffer report at all shows no legend, since "unavailable" would not be
 * a claim the backend data actually supports.
 */
function routePathLegendLabel(
  hasDrawablePath: boolean,
  hasMovementStatusData: boolean,
): string | null {
  if (hasDrawablePath) {
    return "Provider-backed route path";
  }
  if (hasMovementStatusData) {
    return "Route path unavailable";
  }
  return null;
}

/** Rounds a real, provider-backed duration in seconds to whole minutes
 * (or hours + minutes past 60) for display -- a unit conversion of an
 * existing backend number, never an invented or padded value. The `~`
 * prefix signals this is a rounded approximation of the real figure, not
 * extra precision the backend didn't provide.
 */
function formatDurationSeconds(seconds: number): string {
  const totalMinutes = Math.round(seconds / 60);
  if (totalMinutes < 60) {
    return `~${totalMinutes} min`;
  }
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return minutes > 0 ? `~${hours} h ${minutes} min` : `~${hours} h`;
}

/** Converts a real, provider-backed distance in meters to kilometers for
 * display -- a unit conversion only, never a straight-line/haversine
 * estimate computed by the frontend.
 */
function formatDistanceMeters(meters: number): string {
  return `${(meters / 1000).toFixed(1)} km`;
}

/**
 * Compact, honest one-line summary for a single stop-to-stop movement
 * row (Step 172D). Never invents a duration/distance the backend left
 * `null`, never computes one from coordinates, and never states a travel
 * mode (the backend does not record one per leg at all). Covers every
 * `TravelTimeBufferStatus` value the backend can return:
 *
 * - `success` (with a real duration): shows the real, provider-backed
 *   duration/distance under the "Provider-backed movement data" label.
 * - `not_computable` (one or both stops missing coordinates, so the
 *   routing provider was never even called): "No movement details
 *   returned".
 * - `failed` (a real request was attempted and broke): "Route details
 *   need review".
 * - `not_connected`/`unavailable`/anything else: "Movement data
 *   unavailable" -- the safe default when no usable route data exists.
 */
function formatMovementSummary(buffer: TravelTimeBuffer): string {
  if (buffer.status === "success" && buffer.route_duration_seconds !== null) {
    const parts = [formatDurationSeconds(buffer.route_duration_seconds)];
    if (buffer.route_distance_meters !== null) {
      parts.push(formatDistanceMeters(buffer.route_distance_meters));
    }
    const figures = parts.join(" · ");
    return buffer.provider
      ? `Provider-backed movement data: ${figures} (via ${buffer.provider})`
      : `Provider-backed movement data: ${figures}`;
  }
  if (buffer.status === "not_computable") {
    return "No movement details returned";
  }
  if (buffer.status === "failed") {
    return "Route details need review";
  }
  return "Movement data unavailable";
}

/**
 * Small row rendered between two consecutive stop cards showing
 * stop-to-stop movement transparency (Step 172D). Rendered only by a
 * caller that already confirmed (via `shouldRenderMovementRow`) that a
 * real backend `TravelTimeBuffer` entry exists for this leg -- this
 * component itself never fetches, computes, or guesses movement data.
 */
function MovementRow({ buffer }: { buffer: TravelTimeBuffer }) {
  return (
    <li className="ml-3 break-words border-l border-white/10 pl-3 text-[11px] text-slate-500">
      {formatMovementSummary(buffer)}
    </li>
  );
}

// Human-readable labels for backend ValidationIssue.category values (Step
// 175E, docs/16_frontend_architecture.md). Purely a display relabeling of a
// string the backend already sent -- it never changes, reorders, or
// interprets the underlying message/severity/affected_section/
// suggested_fix content, and it never invents a category the backend
// didn't report. A category not listed here (e.g. a future backend
// addition) falls back to a generic snake_case-to-Title-Case reformat
// (validationCategoryLabel below) rather than showing nothing or a raw
// backend key.
const VALIDATION_CATEGORY_LABELS: Record<string, string> = {
  provider_coverage: "Provider coverage",
  provider_coverage_consistency: "Provider coverage consistency",
  scheduling: "Scheduling",
  feasibility: "Route feasibility",
  travel_time_buffer: "Travel-time buffer",
  route_aware_sequencing: "Route-aware sequencing",
  movement_data: "Movement data",
  route_geometry: "Route geometry",
  regeneration: "Regeneration",
  regeneration_state_consistency: "Regeneration state consistency",
  geographic_spread: "Geographic spread",
  constraints: "Constraints",
  must_visit: "Must-visit places",
  budget: "Budget",
  weather: "Weather",
  holidays: "Holidays",
  accommodation_inventory: "Accommodation inventory",
  flight_inventory: "Flight inventory",
  hotel_ratings: "Hotel ratings",
};

function validationCategoryLabel(category: string): string {
  const known = VALIDATION_CATEGORY_LABELS[category];
  if (known) return known;
  return category
    .split("_")
    .filter((word) => word.length > 0)
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join(" ");
}

// Groups an already-backend-ordered issue list by category, preserving the
// order categories first appear in -- never reorders individual issues
// within a category, and never invents or drops an issue (Step 175E).
function groupValidationIssuesByCategory(
  issues: ValidationReport["warnings"],
): { category: string; issues: ValidationReport["warnings"] }[] {
  const order: string[] = [];
  const byCategory: Record<string, ValidationReport["warnings"]> = {};
  for (const issue of issues) {
    if (!byCategory[issue.category]) {
      byCategory[issue.category] = [];
      order.push(issue.category);
    }
    byCategory[issue.category].push(issue);
  }
  return order.map((category) => ({ category, issues: byCategory[category] }));
}

// Purely presentational severity -> tone mapping (Step 175E) -- reads only
// the backend's own `issue.severity` field to pick a border/badge color; it
// never changes which bucket (critical/warnings/suggestions) an issue is
// grouped under, since that is already decided by the backend's severity
// value itself. A `suggestion` gets a visibly quieter, informational tone
// than a `warning`, which in turn is quieter than a `critical` issue.
function validationSeverityToneClassName(severity: string): {
  border: string;
  badge: string;
} {
  if (severity === "critical") {
    return { border: "border-red-500/30 bg-red-950/10", badge: "text-red-300/90" };
  }
  if (severity === "suggestion") {
    return { border: "border-sky-500/20 bg-slate-900/40", badge: "text-sky-300/80" };
  }
  return { border: "border-amber-500/20 bg-slate-900/60", badge: "text-amber-300/90" };
}

function ValidationIssueCard({
  issue,
}: {
  issue: ValidationReport["warnings"][number];
}) {
  const tone = validationSeverityToneClassName(issue.severity);
  return (
    <li className={`rounded-lg border p-3 text-sm ${tone.border}`}>
      <p className={`text-[11px] uppercase tracking-wide ${tone.badge}`}>
        {issue.severity}
      </p>
      <p className="mt-1 break-words text-slate-200">{issue.message}</p>
      {issue.affected_section && (
        <p className="mt-1 break-words text-xs text-slate-400">
          Affects: {issue.affected_section}
        </p>
      )}
      {issue.suggested_fix && (
        <p className="mt-1 break-words text-xs text-slate-400">
          Suggested fix: {issue.suggested_fix}
        </p>
      )}
    </li>
  );
}

/**
 * Renders one severity bucket (critical / warnings / suggestions) of a
 * validation report, grouped by category with a human-readable heading per
 * group (Step 175E). Every rendered field (message, severity,
 * affected_section, suggested_fix) is copied verbatim from the backend's
 * own ValidationIssue -- this never computes, infers, or adds a new
 * validation fact, route/provider/regeneration conclusion, or fabricated
 * travel detail; it only groups and relabels what the backend already
 * sent. Rendering nothing when `issues` is empty means critical issues and
 * warnings are never hidden by this component -- an empty bucket simply
 * has nothing to group.
 */
function ValidationIssueList({
  title,
  issues,
  toneClassName,
}: {
  title: string;
  issues: ValidationReport["warnings"];
  toneClassName: string;
}) {
  if (issues.length === 0) return null;

  const groups = groupValidationIssuesByCategory(issues);

  return (
    <div className="mt-4 first:mt-0">
      <div className="flex flex-wrap items-center gap-2">
        <p className="break-words text-sm font-semibold text-slate-200">{title}</p>
        <span
          className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-semibold ${toneClassName}`}
        >
          {issues.length}
        </span>
      </div>
      <div className="mt-2 flex flex-col gap-2">
        {groups.map((group) => (
          <div
            key={group.category}
            className="rounded-xl border border-white/5 bg-white/[0.02] p-3"
          >
            <div className="flex flex-wrap items-center gap-2">
              <p className="break-words text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                {validationCategoryLabel(group.category)}
              </p>
              <span className="shrink-0 rounded-full border border-white/10 bg-slate-900/60 px-1.5 py-0.5 text-[10px] font-semibold text-slate-400">
                {group.issues.length}
              </span>
            </div>
            <ul className="mt-2 flex flex-col gap-2">
              {group.issues.map((issue, index) => (
                <ValidationIssueCard key={`${group.category}-${index}`} issue={issue} />
              ))}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}

function StayAreaAccommodationCard({
  accommodation,
}: {
  accommodation: AccommodationSuggestion;
}) {
  return (
    <li className="rounded-lg border border-white/10 bg-slate-900/60 p-3 text-sm">
      <p className="break-words font-medium text-slate-100">
        {accommodation.name}
        {accommodation.category && (
          <span className="font-normal text-slate-400">
            {" "}
            ({accommodation.category})
          </span>
        )}
      </p>
      {accommodation.address && (
        <p className="mt-1 break-words text-xs text-slate-400">{accommodation.address}</p>
      )}
      <p className="mt-1 text-[11px] uppercase tracking-wide text-slate-500">
        {accommodation.source} · {accommodation.data_status}
      </p>
      <p className="mt-1 text-xs text-slate-400">
        {accommodation.why_suggested}
      </p>
    </li>
  );
}

function StayAreaGuidanceSection({
  guidance,
}: {
  guidance: StayAreaGuidance;
}) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h2 className="text-lg font-semibold">Stay-area guidance</h2>
      <p className="mt-1 text-xs text-amber-300/90">
        Open-data accommodation location candidates only, not bookable
        inventory.
      </p>
      <p className="mt-1 text-xs text-amber-300/90">
        Stay-area guidance uses open-data accommodation POI locations and
        scheduled attraction proximity only. It does not confirm hotel
        price, availability, rating, safety, or booking suitability.
      </p>
      <p className="mt-2 text-sm text-slate-300">{guidance.summary}</p>

      {guidance.suggested_anchor_accommodation_pois.length === 0 ? (
        <p className="mt-2 text-sm text-slate-400">
          No suggested anchor accommodation POIs available.
        </p>
      ) : (
        <ul className="mt-3 flex flex-col gap-2">
          {guidance.suggested_anchor_accommodation_pois.map(
            (accommodation, index) => (
              <StayAreaAccommodationCard
                key={`${accommodation.name}-${index}`}
                accommodation={accommodation}
              />
            ),
          )}
        </ul>
      )}

      {guidance.warnings.map((warning) => (
        <p key={warning} className="mt-2 text-xs text-amber-300/90">
          {warning}
        </p>
      ))}
    </div>
  );
}

function SummaryList({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;

  return (
    <div className="mt-3">
      <p className="text-sm font-semibold text-slate-200">{title}</p>
      <ul className="mt-2 list-disc break-words pl-5 text-sm text-slate-300">
        {items.map((item, index) => (
          <li key={`${title}-${index}`}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

/**
 * Shared styling for a short, fixed scope/safety disclaimer paragraph
 * (Step 179D) -- a pure style wrapper around the repeated
 * `text-xs text-amber-300/90` / `text-xs text-slate-500` disclaimer
 * pattern used across many sections. It decides no wording, condition, or
 * safety meaning of its own: every call site still supplies its own exact
 * text and its own condition for whether the note renders at all, so no
 * disclaimer's meaning changes by being wrapped in this.
 */
function DisclaimerNote({
  tone,
  spacingClassName = "mt-1",
  children,
}: {
  tone: "amber" | "slate";
  spacingClassName?: string;
  children: React.ReactNode;
}) {
  const toneClassName = tone === "amber" ? "text-amber-300/90" : "text-slate-500";
  return <p className={`${spacingClassName} text-xs ${toneClassName}`}>{children}</p>;
}

function DecisionSummarySection({ summary }: { summary: DecisionSummary }) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h2 className="text-lg font-semibold">Decision summary</h2>
      <p className="mt-2 text-sm text-slate-300">{summary.summary}</p>

      <SummaryList
        title="Provider-backed facts"
        items={summary.provider_backed_facts}
      />
      <SummaryList
        title="Proximity-based decisions"
        items={summary.proximity_based_decisions}
      />
      <SummaryList title="Still unvalidated" items={summary.unvalidated_items} />
      <SummaryList
        title="Review before trusting"
        items={summary.user_review_required}
      />
    </div>
  );
}

function ImplementationGapsSection({ gaps }: { gaps: ImplementationGaps }) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h2 className="text-lg font-semibold">Implementation gaps</h2>
      <p className="mt-2 text-sm text-slate-300">{gaps.summary}</p>

      <SummaryList title="Connected data" items={gaps.connected_data} />
      <SummaryList title="Missing data" items={gaps.missing_data} />
      <SummaryList title="Next data needed" items={gaps.next_data_needed} />
      <SummaryList title="Why this still needs review" items={gaps.why_needs_review} />
    </div>
  );
}

function checklistStatusLabel(status: string): string {
  if (status === "checked") return "Checked";
  if (status === "needs_review") return "Needs Review";
  if (status === "missing_data") return "Missing Data";
  if (status === "not_implemented") return "Not Implemented";
  return status;
}

function ReadinessChecklistSection({
  checklist,
}: {
  checklist: ReadinessChecklist;
}) {
  return (
    <div id="readiness-checklist" className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h2 className="text-lg font-semibold">Readiness checklist</h2>
      <p className="mt-2 text-sm text-slate-300">{checklist.summary}</p>

      <ul className="mt-3 flex flex-col gap-2">
        {checklist.items.map((item) => (
          <li
            key={item.label}
            className="rounded-lg border border-white/10 bg-slate-900/60 p-3 text-sm"
          >
            <p className="flex flex-wrap items-center justify-between gap-2">
              <span className="min-w-0 break-words font-semibold text-slate-200">{item.label}</span>
              <span className="text-[11px] uppercase tracking-wide text-slate-400">
                {checklistStatusLabel(item.status)}
              </span>
            </p>
            <p className="mt-1 text-xs text-slate-400">{item.explanation}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}

const CHECKLIST_STATUS_GROUPS: { title: string; status: ChecklistItemStatus }[] = [
  { title: "Checked", status: "checked" },
  { title: "Needs review", status: "needs_review" },
  { title: "Missing data", status: "missing_data" },
  { title: "Not implemented", status: "not_implemented" },
];

// Purely presentational status-kind -> tone mapping (Step 176C). Reads only
// `category.statusKind`, a value `buildTrustDashboardModel` already
// computed -- this never decides a category's status itself, it only picks
// a border/badge color for a status the helper already assigned. Three
// tones only, matching the dashboard's restrained-wording goal: a neutral/
// positive tone for `available`, a review/attention tone for
// `needs_review`/`partial`/`scraped_source`/`open_data_only`, and one
// muted tone shared by every "nothing to claim yet" state
// (`not_connected`/`unavailable`/`failed`/`blocked_by_locks`/
// `no_pending_feedback`) -- deliberately not colored as an alarming error,
// since most of these are this app's ordinary default state (e.g. no
// routing provider connected) rather than something having gone wrong.
function trustDashboardToneClassName(statusKind: TrustDashboardStatusKind): {
  border: string;
  badge: string;
} {
  if (statusKind === "available") {
    return { border: "border-emerald-500/20 bg-slate-900/60", badge: "text-emerald-300/90" };
  }
  if (
    statusKind === "needs_review" ||
    statusKind === "partial" ||
    statusKind === "scraped_source" ||
    statusKind === "open_data_only"
  ) {
    return { border: "border-amber-500/20 bg-slate-900/60", badge: "text-amber-300/90" };
  }
  return { border: "border-white/10 bg-slate-900/60", badge: "text-slate-400" };
}

/**
 * One trust-dashboard category tile (Step 176C). Renders only fields
 * already present on `TrustDashboardCategoryView` -- `title`,
 * `statusLabel` (via `statusKind`'s tone), `detail`, `supportingFacts`,
 * `relatedValidationIssues`, and `detailAnchorId` -- reusing the existing
 * `SummaryList` component for both list fields rather than duplicating its
 * rendering logic. This component computes no status, count, or label of
 * its own; every value shown was already decided by
 * `buildTrustDashboardModel` before this ever renders.
 */
// A front-door card shows at most this many related-issue messages
// verbatim before pointing at the full list instead -- a display-only cap
// (the count in the title is always the real, untruncated total) so the
// "Validation summary" category's card, whose `relatedValidationIssues`
// intentionally includes every issue, doesn't dwarf its sibling cards.
// This never drops or reorders an issue -- the rest are still fully
// listed in the "Validation report"/"Validation summary" sections below,
// which the card's own "View details" link points at.
const TRUST_DASHBOARD_MAX_RELATED_ISSUES_SHOWN = 3;

/**
 * Compact fact/issue list for one trust-dashboard card (Step 176E
 * readability polish). Mirrors `SummaryList`'s structure (a title plus a
 * bulleted list) at a smaller, denser text size appropriate for a
 * front-door summary card rather than a full detail section -- this
 * never changes `SummaryList` itself, which stays exactly as-is for
 * every other section on the page that already uses it. Renders only
 * strings already computed by `buildTrustDashboardModel` or already
 * passed in by the caller; it creates no new fact and reorders nothing.
 */
function TrustDashboardFactList({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;

  return (
    <div className="mt-2">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
        {title}
      </p>
      <ul className="mt-1 list-disc break-words pl-4 text-[11px] leading-snug text-slate-400">
        {items.map((item, index) => (
          <li key={`${title}-${index}`}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

function TrustDashboardCategoryCard({
  category,
}: {
  category: TrustDashboardCategoryView;
}) {
  const tone = trustDashboardToneClassName(category.statusKind);
  const shownIssues = category.relatedValidationIssues.slice(
    0,
    TRUST_DASHBOARD_MAX_RELATED_ISSUES_SHOWN,
  );
  const hiddenIssueCount = category.relatedValidationIssues.length - shownIssues.length;

  return (
    <div className={`rounded-lg border p-4 text-sm ${tone.border}`}>
      <div className="flex items-start justify-between gap-2">
        <p className="font-semibold text-slate-100">{category.title}</p>
        <span
          className={`shrink-0 whitespace-nowrap rounded-full border border-white/10 bg-slate-950 px-2 py-0.5 text-[11px] uppercase tracking-wide ${tone.badge}`}
        >
          {category.statusLabel}
        </span>
      </div>
      <p className="mt-2 text-xs text-slate-300">{category.detail}</p>

      <TrustDashboardFactList title="Supporting facts" items={category.supportingFacts} />

      {category.relatedValidationIssues.length > 0 && (
        <TrustDashboardFactList
          title={`Related validation issue(s) (${category.relatedValidationIssues.length})`}
          items={
            hiddenIssueCount > 0
              ? [
                  ...shownIssues.map((issue) => issue.message),
                  `+${hiddenIssueCount} more -- see the Validation report section below.`,
                ]
              : shownIssues.map((issue) => issue.message)
          }
        />
      )}

      {category.detailAnchorId && (
        <a
          href={`#${category.detailAnchorId}`}
          aria-label={`View details: ${category.title}`}
          className={`mt-3 inline-block text-[11px] text-cyan-200 hover:text-cyan-100 ${FOCUS_RING_CLASSNAME}`}
        >
          View details
        </a>
      )}
    </div>
  );
}

/**
 * Trust dashboard card (Step 176C, docs/16_frontend_architecture.md) -- a
 * front-door summary built entirely from `buildTrustDashboardModel(result)`
 * (Step 176B, `frontend/lib/trust-dashboard.ts`). This component creates no
 * dashboard fact of its own: every status, label, detail sentence,
 * supporting fact, and related-issue list rendered here was already
 * decided by that pure helper from fields the backend already returned.
 * It never replaces the detailed sections below (validation report,
 * provider coverage, accommodation/flight inventory, etc.) -- each card's
 * optional "View details" link points at one of those sections' own
 * existing anchor ids.
 */
function TrustDashboardSection({ model }: { model: TrustDashboardModel }) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h2 className="text-lg font-semibold">Trust dashboard</h2>
      <p className="mt-1 text-sm text-slate-300">
        A source-based summary of what is available, missing, or needs
        review. This does not replace the detailed sections below -- every
        fact here is read directly from them.
      </p>
      <p className="mt-2 text-xs text-slate-500">
        Overall readiness:{" "}
        <span className="font-semibold text-slate-300">
          {readinessLabel(model.readinessStatus)}
        </span>
      </p>

      <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {model.categories.map((category) => (
          <TrustDashboardCategoryCard key={category.id} category={category} />
        ))}
      </div>
    </div>
  );
}

function trustSummaryAnswer(validationStatus: string | null): string {
  if (validationStatus === "ready") {
    return "This plan has passed the current validation checks, but you should still confirm real-world details before travel.";
  }
  if (validationStatus === "needs_review") {
    return "Use this as a planning draft, not a final itinerary yet.";
  }
  if (validationStatus === "blocked") {
    return "Do not use this as an itinerary yet because required provider-backed data is missing.";
  }
  return "Plan readiness is not available yet.";
}

function UserTrustSummarySection({
  validationStatus,
  checklist,
  validationReport,
}: {
  validationStatus: string | null;
  checklist: ReadinessChecklist;
  validationReport: ValidationReport;
}) {
  const reliableNow = checklist.items.filter(
    (item) => item.status === "checked",
  );
  const needsReview = checklist.items.filter(
    (item) => item.status === "needs_review",
  );
  const missingOrNotImplemented = checklist.items.filter(
    (item) =>
      item.status === "missing_data" || item.status === "not_implemented",
  );

  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h2 className="text-lg font-semibold">Can I use this plan?</h2>
      <p className="mt-2 text-sm text-slate-300">
        {trustSummaryAnswer(validationStatus)}
      </p>

      <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div className="rounded-lg border border-white/10 bg-slate-900/60 p-3">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-emerald-300/90">
            Reliable right now
          </p>
          {reliableNow.length === 0 ? (
            <p className="mt-2 text-xs text-slate-400">
              No checklist items are fully checked yet.
            </p>
          ) : (
            <ul className="mt-2 list-disc break-words pl-4 text-xs text-slate-300">
              {reliableNow.map((item, index) => (
                <li key={`${item.label}-${index}`}>{item.label}</li>
              ))}
            </ul>
          )}
        </div>

        <div className="rounded-lg border border-white/10 bg-slate-900/60 p-3">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-amber-300/90">
            Needs review
          </p>
          {needsReview.length === 0 ? (
            <p className="mt-2 text-xs text-slate-400">
              No checklist items are currently marked as needs review.
            </p>
          ) : (
            <ul className="mt-2 list-disc break-words pl-4 text-xs text-slate-300">
              {needsReview.map((item, index) => (
                <li key={`${item.label}-${index}`}>{item.label}</li>
              ))}
            </ul>
          )}
        </div>

        <div className="rounded-lg border border-white/10 bg-slate-900/60 p-3">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-red-300/90">
            Missing or not implemented
          </p>
          {missingOrNotImplemented.length === 0 ? (
            <p className="mt-2 text-xs text-slate-400">
              No checklist items are currently missing or not implemented.
            </p>
          ) : (
            <ul className="mt-2 list-disc break-words pl-4 text-xs text-slate-300">
              {missingOrNotImplemented.map((item, index) => (
                <li key={`${item.label}-${index}`}>{item.label}</li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <p className="mt-4 text-xs text-slate-400">
        Critical issues:{" "}
        <span className="font-semibold text-slate-200">
          {validationReport.critical_issues.length}
        </span>
        {" · "}
        Warnings:{" "}
        <span className="font-semibold text-slate-200">
          {validationReport.warnings.length}
        </span>
      </p>

      <p className="mt-3 text-[11px] text-slate-500">
        This summary is derived from backend validation and provider
        coverage. It does not add new travel facts.
      </p>
    </div>
  );
}

function planStatusMessage(validationStatus: string | null): string {
  if (validationStatus === "blocked") {
    return "This plan is blocked because required provider-backed data is missing. Do not use it as an itinerary yet.";
  }
  if (validationStatus === "ready") {
    return "This plan has passed the current validation checks.";
  }
  if (validationStatus === "needs_review") {
    return "This plan is provider-backed but still needs review. Use it as a planning draft, not a final itinerary.";
  }
  return "Plan status is not yet available.";
}

function PlanStatusSection({
  validationStatus,
  checklist,
}: {
  validationStatus: string | null;
  checklist: ReadinessChecklist;
}) {
  const grouped: Record<ChecklistItemStatus, string[]> = {
    checked: [],
    needs_review: [],
    missing_data: [],
    not_implemented: [],
  };
  for (const item of checklist.items) {
    grouped[item.status].push(item.label);
  }

  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h2 className="text-lg font-semibold">Plan status</h2>
      <p className="mt-2 text-sm text-slate-300">
        {planStatusMessage(validationStatus)}
      </p>

      <dl className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        {CHECKLIST_STATUS_GROUPS.map((group) => (
          <div
            key={group.status}
            className="rounded-lg border border-white/10 bg-slate-900/60 p-3 text-sm"
          >
            <dt className="text-[11px] uppercase tracking-wide text-slate-500">
              {group.title}
            </dt>
            <dd className="mt-1 text-base font-semibold text-slate-100">
              {grouped[group.status].length}
            </dd>
          </div>
        ))}
      </dl>

      <div className="mt-3 flex flex-col">
        {CHECKLIST_STATUS_GROUPS.map((group) => (
          <SummaryList
            key={group.status}
            title={group.title}
            items={grouped[group.status]}
          />
        ))}
      </div>
    </div>
  );
}

function WeatherContextSection({ weather }: { weather: WeatherContext | null }) {
  if (!weather) {
    return (
      <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
        <h2 className="text-lg font-semibold">Weather context</h2>
        <p className="mt-2 text-sm text-slate-400">
          Weather data is unavailable for this trip.
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h2 className="text-lg font-semibold">Weather context</h2>
      <p className="mt-1 text-sm text-slate-300">
        Source: <span className="font-semibold">{weather.source ?? "None"}</span>
        {" · "}
        Status: <span className="font-semibold">{weather.data_status}</span>
        {" · "}
        Confidence: <span className="font-semibold">{weather.confidence}</span>
      </p>

      {weather.daily_weather.length === 0 ? (
        <p className="mt-2 text-sm text-slate-400">
          No usable provider-backed daily forecast data is available for{" "}
          {weather.destination} between {weather.start_date} and {weather.end_date}.
        </p>
      ) : (
        <ul className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
          {weather.daily_weather.map((day) => (
            <li
              key={day.date}
              className="rounded-lg border border-white/10 bg-slate-900/60 p-3 text-sm"
            >
              <p className="font-medium">{day.date}</p>
              <p className="mt-1 text-xs text-slate-400">
                High: {day.temperature_max_c ?? "N/A"}°C · Low:{" "}
                {day.temperature_min_c ?? "N/A"}°C
              </p>
              <p className="mt-1 text-xs text-slate-400">
                Precipitation probability:{" "}
                {day.precipitation_probability_max ?? "N/A"}% · Sum:{" "}
                {day.precipitation_sum_mm ?? "N/A"}mm
              </p>
              <p className="mt-1 text-xs text-slate-400">
                Weather code: {day.weather_code ?? "N/A"}
              </p>
              <p className="mt-1 text-[11px] uppercase tracking-wide text-slate-500">
                {day.source} · {day.data_status}
              </p>
            </li>
          ))}
        </ul>
      )}

      <SummaryList title="Assumptions" items={weather.assumptions} />
      <SummaryList title="Warnings" items={weather.warnings} />
    </div>
  );
}

function HolidayContextSection({ holiday }: { holiday: HolidayContext | null }) {
  if (!holiday) {
    return (
      <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
        <h2 className="text-lg font-semibold">Holiday context</h2>
        <p className="mt-2 text-sm text-slate-400">
          Holiday data is unavailable for this trip.
        </p>
      </div>
    );
  }

  const providerHasData = holiday.data_status === "live";

  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h2 className="text-lg font-semibold">Holiday context</h2>
      <p className="mt-1 text-sm text-slate-300">
        Source: <span className="font-semibold">{holiday.source ?? "None"}</span>
        {" · "}
        Status: <span className="font-semibold">{holiday.data_status}</span>
        {" · "}
        Confidence: <span className="font-semibold">{holiday.confidence}</span>
        {" · "}
        Country: <span className="font-semibold">{holiday.country_code ?? "Unknown"}</span>
      </p>

      {holiday.holidays.length === 0 ? (
        <p className="mt-2 text-sm text-slate-400">
          {providerHasData
            ? `Provider data exists for ${holiday.destination}, but no public holidays fall between ${holiday.start_date} and ${holiday.end_date}.`
            : `No usable provider-backed public holiday data is available for ${holiday.destination} between ${holiday.start_date} and ${holiday.end_date}.`}
        </p>
      ) : (
        <ul className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
          {holiday.holidays.map((day, index) => (
            <li
              key={`${day.date}-${index}`}
              className="rounded-lg border border-white/10 bg-slate-900/60 p-3 text-sm"
            >
              <p className="font-medium">
                {day.date} · {day.local_name}
              </p>
              {day.name !== day.local_name && (
                <p className="text-xs text-slate-400">{day.name}</p>
              )}
              <p className="mt-1 text-xs text-slate-400">
                {day.is_global ? "Global" : "Regional"}
                {day.counties.length > 0 ? ` · ${day.counties.join(", ")}` : ""}
              </p>
              {day.types.length > 0 && (
                <p className="mt-1 text-xs text-slate-400">
                  Type: {day.types.join(", ")}
                </p>
              )}
              <p className="mt-1 text-[11px] uppercase tracking-wide text-slate-500">
                {day.country_code} · {day.source} · {day.data_status}
              </p>
            </li>
          ))}
        </ul>
      )}

      <SummaryList title="Assumptions" items={holiday.assumptions} />
      <SummaryList title="Warnings" items={holiday.warnings} />
    </div>
  );
}

function CurrencyContextSection({ currency }: { currency: CurrencyContext | null }) {
  if (!currency) {
    return (
      <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
        <h2 className="text-lg font-semibold">Currency context</h2>
        <p className="mt-2 text-sm text-slate-400">
          Currency data is unavailable for this trip.
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h2 className="text-lg font-semibold">Currency context</h2>
      <p className="mt-1 text-sm text-slate-300">
        Source: <span className="font-semibold">{currency.source ?? "None"}</span>
        {" · "}
        Status: <span className="font-semibold">{currency.data_status}</span>
        {" · "}
        Confidence: <span className="font-semibold">{currency.confidence}</span>
      </p>

      {currency.exchange_rate === null || currency.destination_currency === null ? (
        <p className="mt-2 text-sm text-slate-400">
          No usable provider-backed exchange rate is available from{" "}
          {currency.base_currency} for this destination.
        </p>
      ) : (
        <div className="mt-3 rounded-lg border border-white/10 bg-slate-900/60 p-3 text-sm">
          <p className="font-medium">
            1 {currency.base_currency} = {currency.exchange_rate.toFixed(4)}{" "}
            {currency.destination_currency}
          </p>
          {currency.rate_date && (
            <p className="mt-1 text-xs text-slate-400">
              Rate date: {currency.rate_date}
            </p>
          )}
        </div>
      )}

      <SummaryList title="Assumptions" items={currency.assumptions} />
      <SummaryList title="Warnings" items={currency.warnings} />
    </div>
  );
}

/**
 * One-line, honest weather summary for Traveler view (Step 182D). Only
 * ever aggregates real `temperature_max_c`/`temperature_min_c` values the
 * backend already returned (a plain min/max over whatever numeric values
 * are present) -- never a forecast, invented figure, or value for a day
 * the backend left null. Falls back to a short, honest message when no
 * usable daily forecast exists at all, rather than omitting the row or
 * showing raw provider status internals.
 */
function summarizeWeatherForTravelerView(weather: WeatherContext | null): string {
  if (!weather || weather.daily_weather.length === 0) {
    return "Weather forecast is not available for these dates.";
  }
  const temperatures = weather.daily_weather
    .flatMap((day) => [day.temperature_max_c, day.temperature_min_c])
    .filter((value): value is number => value !== null);
  if (temperatures.length === 0) {
    return `Weather forecast covers ${weather.daily_weather.length} day(s), but temperature figures weren't returned.`;
  }
  const low = Math.round(Math.min(...temperatures));
  const high = Math.round(Math.max(...temperatures));
  const dayCount = weather.daily_weather.length;
  return `Around ${low}–${high}°C over ${dayCount} day${dayCount === 1 ? "" : "s"}${weather.source ? ` (via ${weather.source})` : ""}.`;
}

/**
 * Calm, honest one-line currency summary for Traveler view (Step 182D).
 * Same-currency trips (the common case the task specifically calls out)
 * get "No currency conversion needed" rather than looking like a failure
 * -- `CurrencyContext` already guarantees `exchange_rate=1.0` whenever
 * `destination_currency === base_currency` (see the model docstring), so
 * this never needs to guess currency identity itself. A genuinely missing
 * rate still gets one short, calm fallback line, never raw provider
 * status internals.
 */
function summarizeCurrencyForTravelerView(currency: CurrencyContext | null): string {
  if (!currency || currency.destination_currency === null || currency.exchange_rate === null) {
    return "Currency exchange rate is not available for this destination.";
  }
  if (currency.destination_currency === currency.base_currency) {
    return `No currency conversion needed — both in ${currency.base_currency}.`;
  }
  return `1 ${currency.base_currency} = ${currency.exchange_rate.toFixed(4)} ${currency.destination_currency}${currency.rate_date ? ` (rate as of ${currency.rate_date})` : ""}.`;
}

// Step 182D: Traveler view's single, concise travel-context summary --
// replaces the three full WeatherContextSection/HolidayContextSection/
// CurrencyContextSection reports (kept exactly as-is in Developer view)
// with one short line each. Holidays are shown only when at least one
// real, provider-backed holiday actually falls in the trip's date range
// -- there is nothing relevant to summarize otherwise, so the row is
// omitted rather than padded with a "no holidays" line.
function TravelerContextSummarySection({
  weather,
  holiday,
  currency,
}: {
  weather: WeatherContext | null;
  holiday: HolidayContext | null;
  currency: CurrencyContext | null;
}) {
  const relevantHolidays = holiday?.holidays ?? [];

  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h2 className="text-lg font-semibold">Travel context</h2>
      <dl className="mt-3 flex flex-col gap-3 text-sm">
        <div>
          <dt className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
            Weather
          </dt>
          <dd className="mt-1 text-slate-300">{summarizeWeatherForTravelerView(weather)}</dd>
        </div>
        {relevantHolidays.length > 0 && (
          <div>
            <dt className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
              Holidays
            </dt>
            <dd className="mt-1 flex flex-col gap-0.5 text-slate-300">
              {relevantHolidays.map((day, index) => (
                <span key={`${day.date}-${index}`} className="break-words">
                  {day.date} — {day.local_name}
                </span>
              ))}
            </dd>
          </div>
        )}
        <div>
          <dt className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
            Currency
          </dt>
          <dd className="mt-1 text-slate-300">{summarizeCurrencyForTravelerView(currency)}</dd>
        </div>
      </dl>
      <p className="mt-3 text-[11px] text-slate-500">
        Switch to Developer view for the full weather, holiday, and
        currency reports.
      </p>
    </div>
  );
}

function RouteFeasibilitySection({
  routeFeasibility,
}: {
  routeFeasibility: RouteFeasibilityContext;
}) {
  return (
    <div id="route-feasibility" className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h2 className="text-lg font-semibold">Route feasibility</h2>
      <p className="mt-1 text-sm text-slate-300">
        Status: <span className="font-semibold">{routeFeasibility.data_status}</span>
        {" · "}
        Confidence: <span className="font-semibold">{routeFeasibility.confidence}</span>
      </p>

      {routeFeasibility.daily_route_feasibility.length === 0 ? (
        <p className="mt-2 text-sm text-slate-400">
          Route feasibility is unavailable because no route provider is connected.
        </p>
      ) : (
        <ul className="mt-3 flex flex-col gap-2">
          {routeFeasibility.daily_route_feasibility.map((day) => (
            <li
              key={day.day_number}
              className="rounded-lg border border-white/10 bg-slate-900/60 p-3 text-sm"
            >
              Day {day.day_number}: {day.segments.length} segment(s) ·{" "}
              {day.data_status}
            </li>
          ))}
        </ul>
      )}

      <SummaryList title="Assumptions" items={routeFeasibility.assumptions} />
      <SummaryList title="Warnings" items={routeFeasibility.warnings} />
    </div>
  );
}

/**
 * Small per-day map preview: numbered markers for this day's
 * coordinate-backed scheduled experiences (in existing itinerary order).
 * Leaflet is loaded via dynamic import inside useEffect (never as a
 * top-level runtime import) so it never touches `window`/`document`
 * during server rendering.
 *
 * Step 173C removes the old dashed straight-line connector that used to
 * join every marker regardless of route data: a line between dots reads
 * as a path to a viewer no matter how the caption below the map words
 * it, so a marker-only day now shows markers only -- no fallback line of
 * any kind. The only line ever drawn is the solid green route path added
 * in Step 173B, and only for a leg whose `travelTimeBufferReport` entry
 * is real and provider-backed (`status === "success"` with a real
 * `route_geometry` of at least two points) -- drawn from those exact
 * backend-returned points, never inferred from the two stops' own
 * coordinates. A leg without that data draws no path at all: the safer
 * choice between "real path" and "markers only" is always markers only.
 * A small legend line (`routePathLegendLabel`) states which of those two
 * cases this day is in, and only claims "unavailable" when the backend
 * actually reports movement/route status data to back that claim.
 *
 * Step 173D hardens the viewport/rendering side: "coordinate-backed"
 * (for the marker count, the marker list, and the fit-bounds computation)
 * now means a stop's coordinates are both present and pass
 * `isValidGeoCoordinate` (finite, in-range lat/lon) -- not merely
 * non-null -- so a malformed persisted coordinate can never reach
 * Leaflet or leave the bounds computation with zero points. Each leg's
 * route geometry is validated the same way (inside `drawableRouteGeometry`)
 * before it is ever drawn or added to bounds. Every leg is evaluated
 * independently: on a day with several legs, one leg's missing or
 * invalid geometry never prevents another leg's valid, provider-backed
 * path from drawing.
 */
function DayMapPreview({
  experiences,
  travelTimeBufferReport,
}: {
  experiences: ExperienceItem[];
  travelTimeBufferReport: TravelTimeBufferReport | null;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<Leaflet.Map | null>(null);

  // Step 173D: "coordinate-backed" means the stop's own coordinates are
  // both present and safe to plot -- not merely non-null. This keeps the
  // marker count, the marker/bounds points below, and the early-return
  // "no coordinate-backed places" message all agreeing on the same
  // definition, so a malformed persisted coordinate can never leave the
  // map container rendered with zero valid points to fit bounds around.
  const coordinateBackedCount = experiences.filter(
    (experience) =>
      experience.coordinates !== null &&
      isValidGeoCoordinate(experience.coordinates.lat, experience.coordinates.lng),
  ).length;

  const hasDrawablePath = dayHasDrawableRouteGeometry(
    experiences,
    travelTimeBufferReport,
  );
  const hasMovementStatusData = dayHasMovementStatusData(
    experiences,
    travelTimeBufferReport,
  );
  const legendLabel = routePathLegendLabel(hasDrawablePath, hasMovementStatusData);

  useEffect(() => {
    if (coordinateBackedCount === 0 || !containerRef.current) {
      return;
    }

    const container = containerRef.current;
    let isCancelled = false;

    void (async () => {
      const L = await import("leaflet");
      if (isCancelled) {
        return;
      }

      const map = L.map(container);
      mapRef.current = map;

      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution:
          '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        maxZoom: 19,
      }).addTo(map);

      // Marker labels (Step 172B) prefer the backend's own
      // `experience.stop_order`, falling back to the 1-based index into
      // `experiences` only when `stop_order` is absent -- not a
      // renumbering of only the coordinate-backed ones, and never a
      // frontend-computed reordering -- e.g. if experience #2 has no
      // coordinates but #3 does, #3's marker still says "3" (or its own
      // `stop_order`, if set). These markers show stop order only -- they
      // are never route geometry and never imply a path exists between
      // them.
      const points = experiences
        .map((experience, index) => ({
          experience,
          orderNumber: experience.stop_order ?? index + 1,
        }))
        .filter(
          (item) =>
            item.experience.coordinates !== null &&
            isValidGeoCoordinate(
              item.experience.coordinates.lat,
              item.experience.coordinates.lng,
            ),
        );

      const latLngs: [number, number][] = points.map(({ experience }) => [
        experience.coordinates!.lat,
        experience.coordinates!.lng,
      ]);

      for (const { experience, orderNumber } of points) {
        const icon = L.divIcon({
          className: "",
          html: `<div style="display:flex;align-items:center;justify-content:center;width:26px;height:26px;border-radius:9999px;border:2px solid #67e8f9;background:#0f172a;color:#a5f3fc;font-size:12px;font-weight:600;">${orderNumber}</div>`,
          iconSize: [26, 26],
          iconAnchor: [13, 13],
        });
        L.marker([experience.coordinates!.lat, experience.coordinates!.lng], {
          icon,
        }).addTo(map);
      }

      // Step 173C: no straight-line connector is drawn between markers
      // any more, drawable or not -- the only line this map ever draws is
      // the real, provider-backed route path below. A leg without one
      // shows markers only (the safer of the two options), never a
      // straight-line stand-in.
      const boundsPoints: [number, number][] = [...latLngs];
      for (let index = 0; index < experiences.length - 1; index += 1) {
        const buffer = findMovementBetweenStops(
          experiences[index].experience_id,
          experiences[index + 1].experience_id,
          travelTimeBufferReport,
        );
        const geometry = drawableRouteGeometry(buffer);
        if (!geometry) {
          continue;
        }
        const pathLatLngs: [number, number][] = geometry.map((point) => [
          point.lat,
          point.lon,
        ]);
        L.polyline(pathLatLngs, {
          color: "#34d399",
          weight: 4,
        }).addTo(map);
        // Route geometry points are included in the fit-bounds
        // computation (not just the two stop markers) so a real path
        // that bows away from a straight line is never clipped.
        boundsPoints.push(...pathLatLngs);
      }

      if (boundsPoints.length === 1) {
        map.setView(boundsPoints[0], 14);
      } else {
        map.fitBounds(L.latLngBounds(boundsPoints), { padding: [24, 24] });
      }
    })();

    return () => {
      isCancelled = true;
      mapRef.current?.remove();
      mapRef.current = null;
    };
  }, [experiences, travelTimeBufferReport, coordinateBackedCount]);

  if (coordinateBackedCount === 0) {
    return (
      <p className="mt-3 text-sm text-slate-400">
        No coordinate-backed scheduled places are available for this day map.
      </p>
    );
  }

  return (
    <div className="mt-3">
      <div
        ref={containerRef}
        // Step 182D: `relative` gives this container its own CSS
        // positioning context, so Leaflet's internal absolutely-positioned
        // panes/proxy elements are clipped by this div's own
        // `overflow-hidden` instead of potentially positioning (and
        // overflowing) relative to a further-up, non-clipping ancestor --
        // the likely cause of the ~10px mobile page overflow 182C found.
        // Container-level CSS only; no Leaflet route/marker/path logic
        // touched.
        className="relative h-[260px] w-full max-w-full overflow-hidden rounded-lg border border-white/10"
      />
      {legendLabel && (
        <p
          className={`mt-2 text-[11px] font-medium ${
            hasDrawablePath ? "text-emerald-400" : "text-slate-500"
          }`}
        >
          {legendLabel}
        </p>
      )}
      <p className="mt-1 text-xs text-slate-500">
        Numbered markers show this day&apos;s scheduled stop order only --
        they are not route geometry. Solid green segments are a
        provider-backed route path, shown only for a leg where the backend
        has one; no line of any kind is drawn for any other leg.
      </p>
    </div>
  );
}

/**
 * Handoff links for a single scheduled experience's provider-backed
 * coordinates. These open the place location only -- never a route,
 * walking-directions, travel-time, or booking link. Renders the
 * unavailable message instead of a link when coordinates are missing,
 * rather than falling back to a name-only map search.
 */
function ExperienceMapLinks({
  coordinates,
  placeName,
}: {
  coordinates: GeoPoint | null;
  placeName?: string;
}) {
  if (!coordinates) {
    return (
      <p className="mt-1 text-xs text-slate-500">
        Map links unavailable because this scheduled place has no
        provider-backed coordinates.
      </p>
    );
  }

  const { lat, lng } = coordinates;
  const googleMapsUrl = `https://www.google.com/maps/search/?api=1&query=${lat},${lng}`;
  const openStreetMapUrl = `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lng}#map=16/${lat}/${lng}`;

  return (
    <p className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs">
      <a
        href={googleMapsUrl}
        target="_blank"
        rel="noopener noreferrer"
        aria-label={placeName ? `Open ${placeName} in Google Maps` : undefined}
        className={`text-cyan-300 underline decoration-cyan-300/40 underline-offset-2 hover:text-cyan-200 ${FOCUS_RING_CLASSNAME}`}
      >
        Open in Google Maps
      </a>
      <a
        href={openStreetMapUrl}
        target="_blank"
        rel="noopener noreferrer"
        aria-label={placeName ? `Open ${placeName} in OpenStreetMap` : undefined}
        className={`text-cyan-300 underline decoration-cyan-300/40 underline-offset-2 hover:text-cyan-200 ${FOCUS_RING_CLASSNAME}`}
      >
        Open in OpenStreetMap
      </a>
    </p>
  );
}

/**
 * Badge for one scheduled experience that came from an already-promoted
 * AI candidate (Step 170D, docs/16_frontend_architecture.md). Rendered
 * only when the backend itself set `promoted_from_ai: true` -- a normal
 * provider-backed experience never shows this badge. The label is
 * deliberately "AI-suggested · provider-grounded" and implies no
 * independent verification, certainty, or official-provider status: this
 * candidate was proposed by an AI, then independently matched against
 * real provider/open data and approved by the same quality rules every
 * other candidate must pass -- it is not a claim of price, rating,
 * opening hours, route, or booking status, none of which exist on this
 * model.
 */
function AIPromotedBadge({ experience }: { experience: ExperienceItem }) {
  return (
    <div className="mt-1 flex flex-wrap items-center gap-2">
      <span className="inline-flex items-center rounded-full border border-violet-300/40 bg-violet-950/30 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-violet-200">
        AI-suggested · Provider-grounded
      </span>
      {(experience.provider_source || experience.original_ai_candidate_id) && (
        <span className="break-all text-[11px] text-slate-500">
          {experience.provider_source ? `Source: ${experience.provider_source}` : ""}
          {experience.provider_source && experience.original_ai_candidate_id
            ? " · "
            : ""}
          {experience.original_ai_candidate_id
            ? `Candidate: ${experience.original_ai_candidate_id}`
            : ""}
        </span>
      )}
    </div>
  );
}

/**
 * Compact card for a single scheduled experience. `orderNumber` (Step
 * 172B) is the backend's own `experience.stop_order` when the backend has
 * set it, falling back to the 1-based index of this experience within the
 * day's `experiences` array only when `stop_order` is absent (e.g. an
 * older/partial planning state) -- the frontend never independently
 * reorders `experiences` itself, so this number always matches the
 * backend's own current schedule position. Matches the numbering used by
 * `DayMapPreview`'s markers -- not a renumbering of only coordinate-backed
 * items.
 *
 * `activeLock` and `onLockChange` (Step 128) let this card create/remove its
 * own UserLock directly against POST/DELETE /trips/{trip_id}/locks. This
 * only ever stores or clears a future-regeneration instruction -- it never
 * changes itinerary ordering, scheduled experiences, validation readiness,
 * provider coverage, or route feasibility, and never claims the plan was
 * regenerated (see app.tests.api.test_trip_locks.
 * test_locking_does_not_modify_generated_plan_sections).
 */
function ScheduledExperienceCard({
  experience,
  orderNumber,
  tripId,
  activeLock,
  onLockChange,
}: {
  experience: ExperienceItem;
  orderNumber: number;
  tripId: string;
  activeLock: UserLock | null;
  onLockChange: (
    userLocks: UserLock[],
    planDiffPreview: PlanDiffPreview,
    regenerationReadiness: RegenerationReadiness,
  ) => void;
}) {
  const hasCoordinates = experience.coordinates !== null;
  const [isSubmittingLock, setIsSubmittingLock] = useState(false);
  const [lockSuccessMessage, setLockSuccessMessage] = useState<string | null>(
    null,
  );
  const [lockErrorMessage, setLockErrorMessage] = useState<string | null>(
    null,
  );

  async function handleKeepThisPlace() {
    setIsSubmittingLock(true);
    setLockSuccessMessage(null);
    setLockErrorMessage(null);
    try {
      const tripData = await createTripLock(
        tripId,
        "experience",
        experience.experience_id,
        "user_requested_keep",
      );
      onLockChange(
        tripData.planning_state.user_locks,
        tripData.planning_state.plan_diff_preview,
        tripData.planning_state.regeneration_readiness,
      );
      setLockSuccessMessage(
        "Place marked to keep. Regeneration is not implemented yet.",
      );
    } catch (err) {
      setLockErrorMessage(
        err instanceof ApiRequestError
          ? err.message
          : "Something went wrong while saving the keep marker.",
      );
    } finally {
      setIsSubmittingLock(false);
    }
  }

  async function handleRemoveKeep() {
    if (!activeLock) return;
    setIsSubmittingLock(true);
    setLockSuccessMessage(null);
    setLockErrorMessage(null);
    try {
      const tripData = await deleteTripLock(tripId, activeLock.lock_id);
      onLockChange(
        tripData.planning_state.user_locks,
        tripData.planning_state.plan_diff_preview,
        tripData.planning_state.regeneration_readiness,
      );
      setLockSuccessMessage("Keep marker removed.");
    } catch (err) {
      setLockErrorMessage(
        err instanceof ApiRequestError
          ? err.message
          : "Something went wrong while removing the keep marker.",
      );
    } finally {
      setIsSubmittingLock(false);
    }
  }

  return (
    <li className="rounded-lg border border-white/10 bg-slate-900/60 p-3 text-sm">
      <div className="flex items-start gap-3">
        <span className="flex h-6 w-6 flex-none items-center justify-center rounded-full border border-cyan-300/40 bg-slate-950 text-xs font-semibold text-cyan-200">
          {orderNumber}
        </span>
        <div className="min-w-0 flex-1">
          <p className="break-words font-medium text-slate-100">
            {experience.name}{" "}
            <span className="font-normal text-slate-400">
              ({experience.category})
            </span>
          </p>
          {experience.promoted_from_ai && (
            <AIPromotedBadge experience={experience} />
          )}
          {experience.why_included && (
            <p className="mt-1 text-xs text-slate-400">
              {experience.why_included}
            </p>
          )}
          <p className="mt-2 text-[11px] uppercase tracking-wide text-slate-500">
            {hasCoordinates ? "Coordinates available" : "Coordinates unavailable"}
          </p>
          <div className="mt-1">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
              Open location
            </p>
            <ExperienceMapLinks
              coordinates={experience.coordinates}
              placeName={experience.name}
            />
          </div>

          <div className="mt-2 flex flex-wrap items-center gap-2">
            {activeLock ? (
              <>
                <span className="rounded-full border border-emerald-300/40 bg-slate-950 px-3 py-1 text-xs font-semibold text-emerald-200">
                  Kept for future regeneration
                </span>
                <button
                  type="button"
                  onClick={() => void handleRemoveKeep()}
                  disabled={isSubmittingLock}
                  aria-label={`Remove keep marker for ${experience.name}`}
                  className={`rounded-full border border-white/10 bg-slate-900 px-3 py-1 text-xs font-semibold text-slate-300 transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50 ${FOCUS_RING_CLASSNAME}`}
                >
                  {isSubmittingLock ? "Removing..." : "Remove keep"}
                </button>
              </>
            ) : (
              <button
                type="button"
                onClick={() => void handleKeepThisPlace()}
                disabled={isSubmittingLock}
                aria-label={`Keep ${experience.name} for future regeneration`}
                className={`rounded-full border border-cyan-300/40 bg-slate-900 px-3 py-1 text-xs font-semibold text-cyan-200 transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50 ${FOCUS_RING_CLASSNAME}`}
              >
                {isSubmittingLock ? "Saving..." : "Keep this place"}
              </button>
            )}
          </div>

          {lockErrorMessage && (
            <p className="mt-1 break-words text-xs text-red-300">{lockErrorMessage}</p>
          )}
          {lockSuccessMessage && !lockErrorMessage && (
            <p className="mt-1 break-words text-xs text-emerald-300">
              {lockSuccessMessage}
            </p>
          )}
        </div>
      </div>
    </li>
  );
}

function RestaurantSuggestionCard({
  restaurant,
}: {
  restaurant: RestaurantSuggestion;
}) {
  return (
    <li className="rounded-lg border border-white/10 bg-slate-900/60 p-3 text-sm">
      <p className="break-words font-medium text-slate-100">
        {restaurant.name}
        {restaurant.category && (
          <span className="font-normal text-slate-400">
            {" "}
            ({restaurant.category})
          </span>
        )}
      </p>
      {restaurant.address && (
        <p className="mt-1 break-words text-xs text-slate-400">{restaurant.address}</p>
      )}
      <p className="mt-1 text-[11px] uppercase tracking-wide text-slate-500">
        {restaurant.source} · {restaurant.data_status}
      </p>
      <p className="mt-1 text-xs text-slate-400">
        {restaurant.why_suggested}
      </p>
    </li>
  );
}

function AccommodationSuggestionCard({
  accommodation,
}: {
  accommodation: AccommodationSuggestion;
}) {
  return (
    <li className="rounded-lg border border-white/10 bg-slate-900/60 p-3 text-sm">
      <p className="break-words font-medium text-slate-100">
        {accommodation.name}
        {accommodation.category && (
          <span className="font-normal text-slate-400">
            {" "}
            ({accommodation.category})
          </span>
        )}
      </p>
      {accommodation.address && (
        <p className="mt-1 break-words text-xs text-slate-400">{accommodation.address}</p>
      )}
      <p className="mt-1 text-[11px] uppercase tracking-wide text-slate-500">
        {accommodation.source} · {accommodation.data_status}
      </p>
      <p className="mt-1 text-xs text-slate-400">
        {accommodation.why_suggested}
      </p>
    </li>
  );
}

function ValidationSection({ report }: { report: ValidationReport }) {
  // The backend places every non-critical issue in `warnings`, using its
  // own `severity` field ("warning" vs "suggestion") to distinguish
  // between them (Steps 175C/175D) -- `ValidationReport.suggestions` is
  // never populated. Splitting here is a pure display grouping over an
  // already-backend-decided field; it never reclassifies an issue, and a
  // `critical`-severity item (which should never appear in `warnings`)
  // would still fall into the "Warnings" bucket rather than being
  // silently dropped.
  const actualWarnings = report.warnings.filter(
    (issue) => issue.severity !== "suggestion",
  );
  const suggestions = report.warnings.filter(
    (issue) => issue.severity === "suggestion",
  );

  const hasNothingToShow =
    report.critical_issues.length === 0 &&
    actualWarnings.length === 0 &&
    suggestions.length === 0 &&
    report.provider_coverage_notes.length === 0 &&
    report.unavailable_data_notes.length === 0;

  const criticalTone = validationSeverityToneClassName("critical");
  const warningTone = validationSeverityToneClassName("warning");
  const suggestionTone = validationSeverityToneClassName("suggestion");

  return (
    <div id="validation-report" className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h2 className="text-lg font-semibold">Validation report</h2>
      <p className="mt-1 text-sm text-slate-300">
        Readiness:{" "}
        <span className="font-semibold">
          {readinessLabel(report.readiness_status)}
        </span>
      </p>

      {hasNothingToShow && (
        <p className="mt-2 text-sm text-slate-400">No major issues found.</p>
      )}

      <ValidationIssueList
        title="Critical issues"
        issues={report.critical_issues}
        toneClassName={`${criticalTone.border} ${criticalTone.badge}`}
      />
      <ValidationIssueList
        title="Warnings"
        issues={actualWarnings}
        toneClassName={`${warningTone.border} ${warningTone.badge}`}
      />
      <ValidationIssueList
        title="Suggestions"
        issues={suggestions}
        toneClassName={`${suggestionTone.border} ${suggestionTone.badge}`}
      />

      {report.provider_coverage_notes.length > 0 && (
        <div className="mt-3">
          <p className="text-sm font-semibold text-slate-200">
            Provider coverage notes
          </p>
          <ul className="mt-2 list-disc break-words pl-5 text-sm text-slate-300">
            {report.provider_coverage_notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </div>
      )}

      {report.unavailable_data_notes.length > 0 && (
        <div className="mt-3">
          <p className="text-sm font-semibold text-slate-200">
            Unavailable data
          </p>
          <ul className="mt-2 list-disc break-words pl-5 text-sm text-slate-300">
            {report.unavailable_data_notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function CandidatePoiCard({ poi }: { poi: CandidatePoi }) {
  const hasCoordinates = poi.coordinates !== null;

  return (
    <li className="rounded-lg border border-white/10 bg-slate-900/60 p-3 text-sm">
      <p className="break-words font-medium text-slate-100">{poi.name}</p>
      <p className="mt-1 break-words text-xs text-slate-400">
        {poi.category ?? "Uncategorized"}
        {poi.address ? ` · ${poi.address}` : ""}
      </p>
      <p className="mt-1 text-[11px] uppercase tracking-wide text-slate-500">
        {poi.source} · {poi.data_status} · Confidence: {poi.confidence}
      </p>
      <p className="mt-1 text-[11px] uppercase tracking-wide text-slate-500">
        {hasCoordinates ? "Coordinates available" : "Coordinates unavailable"}
      </p>
    </li>
  );
}

function CandidatePoiSection({
  title,
  notes,
  pois,
  emptyMessage,
}: {
  title: string;
  notes?: string[];
  pois: CandidatePoi[];
  emptyMessage: string;
}) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h2 className="text-lg font-semibold">{title}</h2>
      {notes?.map((note) => (
        <p key={note} className="mt-1 text-xs text-amber-300/90">
          {note}
        </p>
      ))}
      {pois.length === 0 ? (
        <p className="mt-2 text-sm text-slate-400">{emptyMessage}</p>
      ) : (
        <ul className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
          {pois.map((poi) => (
            <CandidatePoiCard key={poi.place_id} poi={poi} />
          ))}
        </ul>
      )}
    </div>
  );
}

function AssumptionsList({
  title,
  assumptions,
  confidence,
}: {
  title: string;
  assumptions: string[];
  confidence: number;
}) {
  return (
    <div>
      <p className="text-sm font-semibold text-slate-200">
        {title}{" "}
        <span className="text-xs font-normal text-slate-400">
          · Confidence: {confidence}
        </span>
      </p>
      {assumptions.length === 0 ? (
        <p className="mt-2 text-sm text-slate-400">No assumptions returned.</p>
      ) : (
        <ul className="mt-2 list-disc break-words pl-5 text-sm text-slate-300">
          {assumptions.map((assumption, index) => (
            <li key={`${title}-${index}`}>{assumption}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function PlanningAssumptionsSection({
  destinationAssumptions,
  destinationConfidence,
  experienceAssumptions,
  experienceConfidence,
}: {
  destinationAssumptions: string[];
  destinationConfidence: number;
  experienceAssumptions: string[];
  experienceConfidence: number;
}) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h2 className="text-lg font-semibold">Planning assumptions</h2>
      <div className="mt-3 flex flex-col gap-4">
        <AssumptionsList
          title="Destination context"
          assumptions={destinationAssumptions}
          confidence={destinationConfidence}
        />
        <AssumptionsList
          title="Experience plan"
          assumptions={experienceAssumptions}
          confidence={experienceConfidence}
        />
      </div>
    </div>
  );
}

// Fixed display groups for `ProviderStatusEntry.provider_type` (backend:
// app.models.providers.ProviderType). Every real provider_type value maps
// to one of these; anything unrecognized falls into "Other" rather than
// being dropped or misgrouped.
const PROVIDER_GROUP_ORDER = [
  "Places",
  "Weather",
  "Holidays",
  "Currency",
  "Routes",
  "Accommodation",
  "Other",
] as const;

type ProviderGroupLabel = (typeof PROVIDER_GROUP_ORDER)[number];

function providerTypeLabel(providerType: string): ProviderGroupLabel {
  switch (providerType) {
    case "places":
      return "Places";
    case "weather":
      return "Weather";
    case "holiday":
      return "Holidays";
    case "currency":
      return "Currency";
    case "routes":
    case "transit":
      return "Routes";
    case "accommodation":
      return "Accommodation";
    default:
      return "Other";
  }
}

type ProviderStatusEntryWithKey = { statusKey: string; entry: ProviderStatusEntry };

function groupProviderStatusByType(
  providerStatus: Record<string, ProviderStatusEntry>,
): Record<ProviderGroupLabel, ProviderStatusEntryWithKey[]> {
  const grouped = Object.fromEntries(
    PROVIDER_GROUP_ORDER.map((label) => [label, [] as ProviderStatusEntryWithKey[]]),
  ) as Record<ProviderGroupLabel, ProviderStatusEntryWithKey[]>;

  for (const [statusKey, entry] of Object.entries(providerStatus)) {
    grouped[providerTypeLabel(entry.provider_type)].push({ statusKey, entry });
  }

  return grouped;
}

function dataStatusLabel(dataStatus: string): string {
  switch (dataStatus) {
    case "live":
      return "Live";
    case "cached":
      return "Cached";
    case "fallback_used":
      return "Fallback used";
    case "estimated":
      return "Estimated";
    case "scheduled":
      return "Scheduled";
    case "user_provided":
      return "User provided";
    case "ai_inferred":
      return "AI inferred";
    case "unavailable":
      return "Unavailable";
    case "failed":
      return "Failed";
    case "not_connected":
      return "Not connected";
    default:
      return dataStatus;
  }
}

// Human-readable labels for known raw backend `provider_name` values
// (Step 154). Purely cosmetic -- the raw provider_name is always shown
// alongside the friendly label, never replaced or hidden, and this mapping
// never implies a provider is connected beyond what provider_status/
// provider_coverage already say. Unknown provider names fall back to the
// raw providerName unchanged.
const PROVIDER_DISPLAY_NAMES: Record<string, string> = {
  openstreetmap_places: "OpenStreetMap / Overpass",
  open_meteo: "Open-Meteo",
  nager_date: "Nager.Date",
  frankfurter: "Frankfurter",
  routes_provider: "Routes provider",
  accommodation_provider: "Accommodation provider",
};

function providerDisplayName(providerName: string): string {
  return PROVIDER_DISPLAY_NAMES[providerName] ?? providerName;
}

function ProviderStatusEntryCard({
  statusKey,
  entry,
}: {
  statusKey: string;
  entry: ProviderStatusEntry;
}) {
  return (
    <li className="rounded-lg border border-white/10 bg-slate-900/60 p-3 text-sm">
      <p className="font-mono text-[11px] text-slate-500">{statusKey}</p>
      <p className="mt-1 font-medium text-slate-100">
        {providerDisplayName(entry.provider_name)}
      </p>
      <p className="mt-0.5 font-mono text-[11px] text-slate-500">
        {entry.provider_name}
      </p>
      <p className="mt-1 text-xs text-slate-400">
        Status: <span className="text-slate-300">{entry.status}</span>
        {" · "}
        Data status:{" "}
        <span className="text-slate-300">{dataStatusLabel(entry.data_status)}</span>
      </p>
      {entry.unavailable_fields.length > 0 ? (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {entry.unavailable_fields.map((field) => (
            <span
              key={field}
              className="rounded-full border border-amber-300/40 bg-slate-950 px-2 py-0.5 text-[11px] text-amber-200"
            >
              {field}
            </span>
          ))}
        </div>
      ) : (
        <p className="mt-2 text-xs text-slate-500">
          No unavailable fields reported.
        </p>
      )}
    </li>
  );
}

function ProviderStatusGroup({
  title,
  entries,
}: {
  title: ProviderGroupLabel;
  entries: ProviderStatusEntryWithKey[];
}) {
  if (entries.length === 0) return null;

  return (
    <div className="mt-3">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
        {title}
      </p>
      <ul className="mt-2 flex flex-col gap-2">
        {entries.map(({ statusKey, entry }) => (
          <ProviderStatusEntryCard key={statusKey} statusKey={statusKey} entry={entry} />
        ))}
      </ul>
    </div>
  );
}

// Human-readable labels for known raw backend `provider_coverage` field
// keys (Step 167E). Purely cosmetic -- the raw field key is always shown
// alongside the friendly label, never replaced or hidden, and this mapping
// never implies a provider is connected beyond what `provider_coverage`
// actually says. `accommodations` (open-data location candidates, e.g.
// OpenStreetMap) and `hotel_prices` (bookable lodging inventory from an
// official provider) are deliberately labeled to keep those two concepts
// visibly distinct -- an open-data location candidate is never a hotel
// price, rating, availability, amenity, or booking link. Unknown field
// keys fall back to the raw key unchanged.
const PROVIDER_COVERAGE_FIELD_LABELS: Record<string, string> = {
  accommodations: "Accommodation-like location candidates (open data)",
  hotel_prices: "Bookable lodging inventory",
};

function providerCoverageFieldLabel(key: string): string {
  return PROVIDER_COVERAGE_FIELD_LABELS[key] ?? key;
}

// Coverage values that mean "no official lodging inventory provider has
// returned usable data" for the `hotel_prices` field -- shown with an
// explanatory note so a bare status string like "not_connected" is never
// left to imply prices/ratings/availability/amenities/booking links exist.
const HOTEL_PRICES_NOT_CONNECTED_VALUES = new Set([
  "not_connected",
  "unavailable",
  "failed",
  null,
]);

/**
 * Provider transparency panel (Step 149, docs/16_frontend_architecture.md
 * section 28). Renders only backend-returned `ProviderCoverageData` fields
 * -- it never invents a rating, price, availability, opening hour, route
 * time, or booking link, and never implies a restricted/paid provider
 * (Booking.com, Airbnb, Expedia, Vrbo, Tripadvisor, Google Flights) is
 * connected beyond what `provider_coverage`/`provider_status` actually say.
 */
function ProviderCoverageSection({ coverage }: { coverage: ProviderCoverageData }) {
  const coverageEntries = Object.entries(coverage.provider_coverage).filter(
    ([, value]) => value !== null,
  );
  const hotelPricesValue = coverage.provider_coverage.hotel_prices ?? null;
  const hotelPricesNotConnected = HOTEL_PRICES_NOT_CONNECTED_VALUES.has(hotelPricesValue);
  const statusEntries = Object.entries(coverage.provider_status);
  const groupedStatus = groupProviderStatusByType(coverage.provider_status);

  return (
    <div id="provider-coverage" className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h2 className="text-lg font-semibold">Provider coverage</h2>

      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div className="rounded-lg border border-white/10 bg-slate-900/60 p-3 text-sm">
          <p className="text-[11px] uppercase tracking-wide text-slate-500">
            Data sources used
          </p>
          <p className="mt-1 text-base font-semibold text-slate-100">
            {coverage.data_sources_used.length}
          </p>
        </div>
        <div className="rounded-lg border border-white/10 bg-slate-900/60 p-3 text-sm">
          <p className="text-[11px] uppercase tracking-wide text-slate-500">
            Provider statuses
          </p>
          <p className="mt-1 text-base font-semibold text-slate-100">
            {statusEntries.length}
          </p>
        </div>
        <div className="rounded-lg border border-white/10 bg-slate-900/60 p-3 text-sm">
          <p className="text-[11px] uppercase tracking-wide text-slate-500">
            Unavailable data items
          </p>
          <p className="mt-1 text-base font-semibold text-slate-100">
            {coverage.unavailable_data.length}
          </p>
        </div>
      </div>
      <p className="mt-3 text-xs text-amber-300/90">
        Unavailable data is shown instead of being guessed. The frontend
        does not invent missing provider facts.
      </p>

      {coverageEntries.length === 0 ? (
        <p className="mt-4 text-sm text-slate-400">
          No provider coverage information returned.
        </p>
      ) : (
        <dl className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-2">
          {coverageEntries.map(([key, value]) => (
            <div
              key={key}
              className="rounded-lg border border-white/10 bg-slate-900/60 p-3 text-sm"
            >
              <dt className="text-[11px] uppercase tracking-wide text-slate-500">
                {providerCoverageFieldLabel(key)}
              </dt>
              <p className="font-mono text-[10px] text-slate-600">{key}</p>
              <dd className="mt-1 text-slate-200">{value}</dd>
              {key === "hotel_prices" && hotelPricesNotConnected && (
                <p className="mt-2 text-xs text-amber-300/90">
                  No official lodging inventory provider is connected yet.
                  Prices, ratings, availability, amenities, and booking links
                  are unavailable unless returned by an official provider.
                  Open-data accommodation-like places (see below) are
                  location candidates only, not bookable hotel inventory.
                </p>
              )}
            </div>
          ))}
        </dl>
      )}

      {statusEntries.length === 0 ? (
        <p className="mt-4 text-sm text-slate-400">
          No provider status information returned.
        </p>
      ) : (
        <div className="mt-4">
          <p className="text-sm font-semibold text-slate-200">
            Provider status by type
          </p>
          {PROVIDER_GROUP_ORDER.map((label) => (
            <ProviderStatusGroup
              key={label}
              title={label}
              entries={groupedStatus[label]}
            />
          ))}
        </div>
      )}

      <div className="mt-4 rounded-lg border border-white/10 bg-slate-950/60 p-3">
        <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
          What this means
        </p>
        <ul className="mt-2 list-disc break-words pl-4 text-xs text-slate-300">
          <li>Provider-backed or open-data-backed fields can be shown.</li>
          <li>Missing fields stay unavailable.</li>
          <li>
            OpenStreetMap places do not provide ratings, prices, reviews,
            opening hours, or booking availability unless those fields are
            explicitly returned by the backend.
          </li>
          <li>
            Route timing is unavailable unless a route provider is
            connected.
          </li>
          <li>
            Open-data accommodation-like places are location candidates
            only, not bookable hotel inventory. Bookable lodging prices,
            availability, ratings, amenities, and booking links are
            unavailable unless returned by an official lodging inventory
            provider.
          </li>
        </ul>
      </div>

      {coverage.unavailable_data.length > 0 && (
        <div className="mt-4">
          <p className="text-sm font-semibold text-slate-200">
            Unavailable data
          </p>
          <ul className="mt-2 flex flex-col gap-2">
            {coverage.unavailable_data.map((item, index) => (
              <li
                key={`${item.field}-${index}`}
                className="rounded-lg border border-white/10 bg-slate-900/60 p-3 text-sm"
              >
                <p className="text-[11px] uppercase tracking-wide text-slate-500">
                  Field
                </p>
                <p className="break-words text-slate-200">{item.field}</p>
                <p className="mt-2 text-[11px] uppercase tracking-wide text-slate-500">
                  Reason
                </p>
                <p className="break-words text-xs text-slate-400">{item.reason}</p>
                <p className="mt-2 text-[11px] uppercase tracking-wide text-slate-500">
                  Status
                </p>
                <p className="text-xs text-slate-300">
                  {dataStatusLabel(item.data_status)}
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}

      {coverage.data_sources_used.length > 0 && (
        <div className="mt-4">
          <p className="text-sm font-semibold text-slate-200">
            Data sources used
          </p>
          <ul className="mt-2 list-disc break-words pl-5 text-sm text-slate-300">
            {coverage.data_sources_used.map((source) => (
              <li key={source}>
                {providerDisplayName(source)}{" "}
                <span className="font-mono text-xs text-slate-500">
                  ({source})
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function accommodationInventoryStatusLabel(status: string): string {
  switch (status) {
    case "success":
      return "Connected";
    case "unavailable":
      return "Unavailable";
    case "failed":
      return "Failed";
    case "not_connected":
    default:
      return "Not connected";
  }
}

// Human-readable label for a scraped offer's confidence (Step 168E) --
// only ever "experimental" or "fragile" per `ScrapedAccommodationProvenance`;
// an unrecognized value displays as-is rather than being hidden.
function scrapedConfidenceLabel(confidence: string): string {
  switch (confidence) {
    case "experimental":
      return "Experimental";
    case "fragile":
      return "Fragile";
    default:
      return confidence;
  }
}

/**
 * One scraped offer's provenance badge (Step 168E/169E, merged into one
 * generic component in Step 179D). `ScrapedAccommodationProvenance` and
 * `ScrapedFlightProvenance` are structurally identical (see
 * `frontend/lib/types.ts`); this renders whichever one the caller passes,
 * with the caller supplying only the one thing that legitimately differs
 * between an accommodation offer and a flight offer -- the exact list of
 * fields not yet verified (`notVerifiedFor`), so the accommodation and
 * flight wording stay distinct, word for word, exactly as before this
 * merge. Renders parser/source metadata only when the backend actually
 * returned it. This never implies official verification: the badge itself
 * is the opposite claim ("not official-provider data").
 */
function ProvenanceBadge({
  provenance,
  notVerifiedFor,
}: {
  provenance: ScrapedAccommodationProvenance | ScrapedFlightProvenance;
  notVerifiedFor: string;
}) {
  return (
    <div className="mt-2 rounded-md border border-amber-300/30 bg-amber-950/20 p-2 text-[11px] text-amber-200/90">
      <p className="font-semibold uppercase tracking-wide">
        Manual/local HTML · {scrapedConfidenceLabel(provenance.confidence)}
      </p>
      <p className="mt-1 text-xs text-amber-200/80">
        This is not official-provider data. It has not been verified for{" "}
        {notVerifiedFor}.
      </p>
      <p className="mt-1 break-words text-amber-300/70">
        Source: {provenance.source_name}
        {provenance.source_url ? (
          <>
            {" · "}
            <span className="break-all">{provenance.source_url}</span>
          </>
        ) : (
          ""
        )}
      </p>
      {provenance.parser_version && (
        <p className="mt-0.5 font-mono text-amber-300/60">
          Parser: {provenance.parser_version}
        </p>
      )}
    </div>
  );
}

// Labels the pre-existing bare `offer.rating` number (Step 167A) with
// where it came from -- Step 177D hardening. This number has no known
// scale (the scraped parser reads whatever free-form text a source page
// used) and is a wholly different concept from `offer.rating_details`
// (Step 177B/177C's richer, bounded-0-5, provider-attributed snapshot),
// so it is never rendered as a bare, context-free number.
function legacyOfferRatingLabel(offer: AccommodationOffer): string {
  return offer.scraped_provenance
    ? "Rating from scraped/manual source"
    : "Rating from lodging source";
}

/**
 * One offer's provider-backed hotel rating snapshot (Step 177D), rendered
 * only when `HotelRatingEnrichmentService` (Step 177C) attached a real,
 * exactly-matched `rating_details` with a non-null `value` -- this never
 * shows a placeholder for a missing/unmatched rating. `scale_max` always
 * comes from the backend, never assumed to be 5 by this component.
 * Review count and source are shown only when the backend actually
 * returned them. This is never labeled verified/official/confirmed, and
 * never used to imply one offer is better than another.
 */
function AccommodationRatingDetailsCard({
  ratingDetails,
}: {
  ratingDetails: AccommodationRatingDetails;
}) {
  if (ratingDetails.value === null) {
    return null;
  }
  return (
    <div className="mt-1 rounded-md border border-white/10 bg-slate-950/40 p-2 text-xs text-slate-300">
      <p>
        Provider-backed rating: {ratingDetails.value} / {ratingDetails.scale_max}
        {ratingDetails.review_count !== null
          ? ` · ${ratingDetails.review_count} review(s)`
          : ""}
      </p>
      {(ratingDetails.source_name || ratingDetails.provider) && (
        <p className="mt-0.5 text-slate-400">
          Source: {ratingDetails.source_name ?? ratingDetails.provider} ·{" "}
          {ratingDetails.data_status}
        </p>
      )}
      <p className="mt-0.5 text-slate-500">
        Provider-reported rating data -- not a claim that it is verified,
        official, or confirmed, and not used to rank or recommend this
        offer.
      </p>
    </div>
  );
}

/**
 * Bookable accommodation inventory panel (Step 167E, extended in Step
 * 168E for scraped-data labeling, Step 177D for hotel-rating enrichment
 * display, docs/16_frontend_architecture.md). Renders only backend-
 * returned `AccommodationInventoryReport` fields -- it never invents a
 * property, price, rating, availability, amenity, cancellation policy,
 * or booking link, and it never upgrades a `not_connected`/`unavailable`/
 * `failed` status into an implied "checked" claim. This is deliberately a
 * separate concept from the open-data accommodation-like location
 * candidates rendered elsewhere on this page (`CandidatePoiSection`,
 * `AccommodationSuggestionCard`, `StayAreaAccommodationCard`) -- an OSM
 * POI never appears here, and a real bookable offer here is never merged
 * into those location-candidate lists. When an offer carries
 * `scraped_provenance` (Step 168C's disabled-by-default local/manual
 * scraped provider), it is visibly labeled "Manual/local HTML" with its
 * experimental/fragile confidence -- never presented as if it were
 * official, verified provider data.
 */
// Extracted from AccommodationInventorySection (Step 182D) so the same
// honest, fields-actually-returned-only offer card can be reused by the
// new Traveler-view trip-level "Where to stay" section without
// duplicating its markup -- behavior-equivalent, not a new component.
function AccommodationOfferCard({ offer }: { offer: AccommodationOffer }) {
  return (
    <li className="rounded-lg border border-white/10 bg-slate-900/60 p-3 text-sm">
      <p className="break-words font-medium text-slate-100">
        {offer.property_name}
      </p>
      <p className="mt-0.5 break-all font-mono text-[11px] text-slate-500">
        {offer.provider}
        {offer.source_name ? ` · ${offer.source_name}` : ""}
      </p>
      {offer.nightly_price_amount !== null && offer.currency && (
        <p className="mt-1 text-xs text-slate-300">
          {offer.nightly_price_amount} {offer.currency} / night
        </p>
      )}
      {offer.rating !== null && (
        <p className="mt-1 text-xs text-slate-300">
          {legacyOfferRatingLabel(offer)}: {offer.rating}
        </p>
      )}
      {offer.rating_details && (
        <AccommodationRatingDetailsCard ratingDetails={offer.rating_details} />
      )}
      <p className="mt-1 text-xs text-slate-400">
        Availability: {offer.availability_status}
      </p>
      {offer.booking_url && (
        <p className="mt-1 break-all text-xs text-cyan-200">
          {offer.booking_url}
        </p>
      )}
      {offer.scraped_provenance && (
        <ProvenanceBadge
          provenance={offer.scraped_provenance}
          notVerifiedFor="price, availability, rating, or booking-link accuracy"
        />
      )}
    </li>
  );
}

function AccommodationInventorySection({
  report,
}: {
  report: AccommodationInventoryReport | null;
}) {
  const status = report?.status ?? "not_connected";
  const offers = report?.offers ?? [];
  const isConnectedWithOffers = status === "success" && offers.length > 0;

  return (
    <div id="accommodation-inventory" className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h2 className="text-lg font-semibold">Bookable lodging inventory</h2>
      <p className="mt-2 text-sm text-slate-200">
        Bookable lodging inventory: {accommodationInventoryStatusLabel(status)}
      </p>

      {!isConnectedWithOffers ? (
        <DisclaimerNote tone="amber" spacingClassName="mt-2">
          No official lodging inventory provider is connected yet. Prices,
          ratings, availability, amenities, and booking links are
          unavailable unless returned by an official provider.
        </DisclaimerNote>
      ) : (
        <ul className="mt-3 flex flex-col gap-2">
          {offers.map((offer, index) => (
            <AccommodationOfferCard key={`${offer.provider_property_id}-${index}`} offer={offer} />
          ))}
        </ul>
      )}

      <p className="mt-3 text-xs text-slate-500">
        Open-data accommodation-like places are location candidates only,
        not bookable hotel inventory -- see the destination candidate
        accommodation POIs below.
      </p>
    </div>
  );
}

// Step 182D: Traveler view's single, trip-level "Where to stay" section.
// Source priority, per spec: (A) real bookable inventory offers if any
// exist, (B) else stay-area open-data candidates if any exist, (C) else
// one honest unavailable note. Capped at 5 cards either way -- this only
// ever slices an already-backend-ranked/ordered list, never re-ranks or
// invents one. Reuses the exact same card components Developer view's
// full AccommodationInventorySection/StayAreaGuidanceSection already use,
// so every field/wording guarantee those carry (fields-actually-returned-
// only, no price/rating/availability/booking claim beyond what a real
// provider or open-data candidate returned) applies here unchanged.
const TRAVELER_WHERE_TO_STAY_MAX_CARDS = 5;

function TravelerWhereToStaySection({
  accommodationInventoryReport,
  stayAreaGuidance,
}: {
  accommodationInventoryReport: AccommodationInventoryReport | null;
  stayAreaGuidance: StayAreaGuidance;
}) {
  const offers = accommodationInventoryReport?.offers ?? [];
  const hasBookableOffers =
    accommodationInventoryReport?.status === "success" && offers.length > 0;
  const stayAreaCandidates = stayAreaGuidance.suggested_anchor_accommodation_pois;
  const hasStayAreaCandidates = stayAreaCandidates.length > 0;

  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h2 className="text-lg font-semibold">Where to stay</h2>

      {hasBookableOffers ? (
        <>
          <DisclaimerNote tone="amber" spacingClassName="mt-2">
            Bookable lodging inventory from a connected provider,
            source-limited to the fields it actually returned -- not a
            confirmed booking or a &ldquo;best&rdquo; ranking.
          </DisclaimerNote>
          <ul className="mt-3 flex flex-col gap-2">
            {offers
              .slice(0, TRAVELER_WHERE_TO_STAY_MAX_CARDS)
              .map((offer, index) => (
                <AccommodationOfferCard key={`${offer.provider_property_id}-${index}`} offer={offer} />
              ))}
          </ul>
        </>
      ) : hasStayAreaCandidates ? (
        <>
          <DisclaimerNote tone="amber" spacingClassName="mt-2">
            Stay-area ideas from open map data (OpenStreetMap), not
            bookable hotels -- no price, availability, rating, or booking
            claim.
          </DisclaimerNote>
          <p className="mt-2 text-sm text-slate-300">{stayAreaGuidance.summary}</p>
          <ul className="mt-3 flex flex-col gap-2">
            {stayAreaCandidates
              .slice(0, TRAVELER_WHERE_TO_STAY_MAX_CARDS)
              .map((accommodation, index) => (
                <AccommodationSuggestionCard
                  key={`${accommodation.name}-${index}`}
                  accommodation={accommodation}
                />
              ))}
          </ul>
        </>
      ) : (
        <DisclaimerNote tone="slate" spacingClassName="mt-2">
          No lodging information is available for this trip yet.
        </DisclaimerNote>
      )}
    </div>
  );
}

// Backend: app.providers.flights.kiwi_mcp_parser._KIWI_PROVIDER_NAME.
// A plain string match against FlightOffer.provider (a public field) --
// mirrors how offer.scraped_provenance is already checked directly
// rather than importing anything backend-side.
const KIWI_MCP_FLIGHT_PROVIDER_NAME = "kiwi_mcp";

function isKiwiMcpFlightOffer(offer: FlightOffer): boolean {
  return offer.provider === KIWI_MCP_FLIGHT_PROVIDER_NAME;
}

// Human-readable label for the flight inventory report's status (Step
// 169E, extended in Step 178D for Kiwi MCP, docs/16_frontend_architecture.md).
// Distinguishes a scraped success and a real Kiwi MCP success (Step 178C)
// from a hypothetical future official-provider success -- three distinct
// trust tiers, never conflated, mirroring accommodationInventoryStatusLabel's
// status set but with wording specific to flights.
function flightInventoryStatusLabel(
  status: string,
  hasScrapedOffers: boolean,
  hasKiwiMcpOffers: boolean,
): string {
  switch (status) {
    case "success":
      if (hasKiwiMcpOffers) return "Available via Kiwi MCP";
      return hasScrapedOffers
        ? "Available from manual/local HTML"
        : "Connected";
    case "unavailable":
      return "Unavailable";
    case "failed":
      return "Failed";
    case "not_connected":
    default:
      return "Not connected";
  }
}

/**
 * One real Kiwi MCP flight offer's third-party-provider badge (Step
 * 178D). Kiwi MCP data (Step 178C) is real, live, provider-backed data --
 * not scraped -- so it is deliberately never shown with the
 * `ProvenanceBadge`'s "not official-provider data" framing.
 * It still gets its own explicit disclaimer: this is third-party data
 * TravelObligator has not itself reviewed for accuracy, and no strong-
 * assurance language (booking confirmation, official status, or
 * readiness claims of any kind) is ever attached to it.
 */
function KiwiMcpOfferBadge() {
  return (
    <div className="mt-2 rounded-md border border-sky-300/30 bg-sky-950/20 p-2 text-[11px] text-sky-200/90">
      <p className="font-semibold uppercase tracking-wide">
        Kiwi MCP · Third-party provider data
      </p>
      <p className="mt-1 text-xs text-sky-200/80">
        This offer was returned by Kiwi via the Model Context Protocol. It
        has not been reviewed by TravelObligator for schedule, price,
        availability, baggage-policy, or booking-link accuracy.
      </p>
    </div>
  );
}

/**
 * One flight offer's outbound/return segment summary line -- renders only
 * fields the backend actually returned (Step 169E). A missing airport,
 * carrier, flight number, time, or duration stays hidden entirely rather
 * than shown as a placeholder/zero/"unknown" value standing in for a real
 * fact.
 */
function FlightSegmentSummary({ segment }: { segment: FlightSegment }) {
  const route =
    segment.origin_airport && segment.destination_airport
      ? `${segment.origin_airport} → ${segment.destination_airport}`
      : segment.origin_airport || segment.destination_airport;
  const carrier = [segment.carrier_name, segment.flight_number]
    .filter(Boolean)
    .join(" ");

  return (
    <p className="mt-1 text-xs text-slate-300">
      {route && <span>{route}</span>}
      {carrier && <span>{route ? " · " : ""}{carrier}</span>}
      {segment.departure_time && (
        <span className="ml-1 text-slate-500">
          dep {segment.departure_time}
        </span>
      )}
      {segment.arrival_time && (
        <span className="ml-1 text-slate-500">
          arr {segment.arrival_time}
        </span>
      )}
      {segment.duration_minutes !== null && (
        <span className="ml-1 text-slate-500">
          ({segment.duration_minutes} min)
        </span>
      )}
      {!route && !carrier && !segment.departure_time && !segment.arrival_time && (
        <span className="text-slate-500">Segment details unavailable</span>
      )}
    </p>
  );
}

/**
 * Bookable flight inventory panel (Step 169E, extended in Step 178D for
 * Kiwi MCP labeling, docs/16_frontend_architecture.md). Renders only
 * backend-returned `FlightInventoryReport` fields -- it never invents an
 * airline, flight number, airport, departure/arrival time, duration,
 * price, availability, baggage policy, cancellation policy, or booking
 * link, and it never upgrades a `not_connected`/`unavailable`/`failed`
 * status into an implied "checked" claim. Flights are never scheduled
 * into daily itinerary experiences -- this panel is inventory reporting
 * only. Three source trust tiers are labeled distinctly and never
 * conflated: an offer carrying `scraped_provenance` (the `scraped_local`
 * provider, Step 169D) is labeled "Manual/local HTML" with its
 * experimental/fragile confidence; a real Kiwi MCP offer (`provider ===
 * "kiwi_mcp"`, Step 178C) is labeled "Third-party provider data" via
 * `KiwiMcpOfferBadge`; neither is ever presented with any strong-
 * assurance language, and neither is ever presented as reserved/booked.
 * Any `booking_url` present is always labeled "provider-supplied ... not
 * a booking confirmation," regardless of source.
 */
function FlightInventorySection({
  report,
}: {
  report: FlightInventoryReport | null;
}) {
  const status = report?.status ?? "not_connected";
  const offers = report?.offers ?? [];
  const isConnectedWithOffers = status === "success" && offers.length > 0;
  const hasScrapedOffers = offers.some((offer) => offer.scraped_provenance !== null);
  const hasKiwiMcpOffers = offers.some(isKiwiMcpFlightOffer);

  return (
    <div id="flight-inventory" className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h2 className="text-lg font-semibold">Flight inventory</h2>
      <p className="mt-2 text-sm text-slate-200">
        Flight inventory: {flightInventoryStatusLabel(status, hasScrapedOffers, hasKiwiMcpOffers)}
      </p>

      {!isConnectedWithOffers ? (
        <DisclaimerNote tone="amber" spacingClassName="mt-2">
          No official flight inventory provider is connected yet. Airlines,
          flight numbers, schedules, prices, availability, baggage
          policies, and booking links are unavailable unless returned by
          an official provider or a manual/local HTML file.
        </DisclaimerNote>
      ) : (
        <ul className="mt-3 flex flex-col gap-2">
          {offers.map((offer, index) => (
            <li
              key={`${offer.offer_id}-${index}`}
              className="rounded-lg border border-white/10 bg-slate-900/60 p-3 text-sm"
            >
              <p className="break-all font-mono text-[11px] text-slate-500">
                {offer.offer_id}
                {offer.source_name ? ` · ${offer.source_name}` : ""}
              </p>
              {offer.outbound_segments.map((segment, segmentIndex) => (
                <FlightSegmentSummary
                  key={`outbound-${segmentIndex}`}
                  segment={segment}
                />
              ))}
              {offer.return_segments.length > 0 && (
                <p className="mt-2 text-[11px] uppercase tracking-wide text-slate-500">
                  Return
                </p>
              )}
              {offer.return_segments.map((segment, segmentIndex) => (
                <FlightSegmentSummary
                  key={`return-${segmentIndex}`}
                  segment={segment}
                />
              ))}
              {offer.total_price_amount !== null && offer.currency && (
                <p className="mt-1 text-xs text-slate-300">
                  {offer.total_price_amount} {offer.currency}
                </p>
              )}
              {offer.availability_status && (
                <p className="mt-1 text-xs text-slate-400">
                  Availability: {offer.availability_status}
                </p>
              )}
              {offer.baggage_policy && (
                <p className="mt-1 text-xs text-slate-400">
                  Baggage: {offer.baggage_policy}
                </p>
              )}
              {offer.cancellation_policy && (
                <p className="mt-1 text-xs text-slate-400">
                  Cancellation: {offer.cancellation_policy}
                </p>
              )}
              {offer.booking_url && (
                <div className="mt-1 text-xs text-cyan-200">
                  <p className="text-xs uppercase tracking-wide text-slate-400">
                    Provider-supplied booking link -- not a booking confirmation
                  </p>
                  <p className="break-all">{offer.booking_url}</p>
                </div>
              )}
              {offer.scraped_provenance && (
                <ProvenanceBadge
                  provenance={offer.scraped_provenance}
                  notVerifiedFor="schedule, price, availability, baggage-policy, or booking-link accuracy"
                />
              )}
              {!offer.scraped_provenance && isKiwiMcpFlightOffer(offer) && <KiwiMcpOfferBadge />}
            </li>
          ))}
        </ul>
      )}

      <p className="mt-3 text-xs text-slate-500">
        Flight offers are inventory reporting only -- they are never
        scheduled into the day-by-day itinerary below.
      </p>
    </div>
  );
}

// Human-readable label for one AI candidate's grounding_status (Step
// 170A/170B). `grounding_status` on the backend holds either a
// CandidateGroundingMatchType value (a real match) or a
// CandidateGroundingRejectReason value (why grounding failed) -- this only
// ever restates that value in a more readable form, never invents a
// judgment beyond what the backend already computed.
function groundingStatusLabel(groundingStatus: string | null): string {
  if (!groundingStatus) return "Not grounded yet";
  return groundingStatus.replaceAll("_", " ");
}

/**
 * One AI candidate review card (Step 170A/170B, docs/16_frontend_
 * architecture.md). Renders only fields the backend actually returned --
 * `quality_bucket`/`grounding_status` only when present, and
 * `rejection_reasons`/`warnings`/`eligibility_reasons` verbatim, never
 * paraphrased into a stronger claim, and never implies independent
 * verification, a confirmed booking, certainty, or official-provider
 * status -- this is a review of an AI-suggested, possibly
 * provider-grounded candidate, not a confirmed itinerary item.
 */
function AICandidateReviewCard({ item }: { item: AICandidateReviewItem }) {
  return (
    <li className="rounded-lg border border-white/10 bg-slate-900/60 p-3 text-sm">
      <p className="break-words font-medium text-slate-100">
        {item.name}
        {item.category && (
          <span className="font-normal text-slate-400"> ({item.category})</span>
        )}
      </p>
      <p className="mt-1 text-[11px] uppercase tracking-wide text-slate-500">
        {item.source} · {item.provider_grounded ? "Provider-grounded" : "Ungrounded"}
        {item.quality_bucket ? ` · Quality: ${item.quality_bucket}` : ""}
      </p>
      <p className="mt-1 text-[11px] uppercase tracking-wide text-slate-500">
        Grounding: {groundingStatusLabel(item.grounding_status)}
      </p>
      <p className="mt-1 text-xs font-semibold text-slate-300">
        {item.eligible_for_promotion ? "Eligible for scheduling" : "Needs review"}
      </p>
      {item.eligibility_reasons.length > 0 && (
        <ul className="mt-1 list-disc break-words pl-4 text-xs text-emerald-300/80">
          {item.eligibility_reasons.map((reason, index) => (
            <li key={`${item.candidate_id}-eligible-${index}`}>{reason}</li>
          ))}
        </ul>
      )}
      {item.rejection_reasons.length > 0 && (
        <ul className="mt-1 list-disc break-words pl-4 text-xs text-amber-300/90">
          {item.rejection_reasons.map((reason, index) => (
            <li key={`${item.candidate_id}-rejection-${index}`}>{reason}</li>
          ))}
        </ul>
      )}
      {item.warnings.length > 0 && (
        <ul className="mt-1 list-disc break-words pl-4 text-xs text-slate-400">
          {item.warnings.map((warning, index) => (
            <li key={`${item.candidate_id}-warning-${index}`}>{warning}</li>
          ))}
        </ul>
      )}
    </li>
  );
}

/**
 * One promoted AI candidate card (Step 170C/170D). A promoted candidate
 * is a provider-grounded, quality-approved candidate that is safe for
 * future scheduling consideration -- it is never itself a claim that the
 * candidate is booked, scheduled, or itinerary-ready; whether it actually
 * appears in a day's itinerary is decided entirely by the backend's
 * existing ExperiencePlannerService rules (Step 170D).
 */
function PromotedAICandidateCard({
  candidate,
}: {
  candidate: PromotedAICandidate;
}) {
  return (
    <li className="rounded-lg border border-violet-300/30 bg-violet-950/10 p-3 text-sm">
      <p className="break-words font-medium text-slate-100">
        {candidate.name}
        {candidate.category && (
          <span className="font-normal text-slate-400">
            {" "}
            ({candidate.category})
          </span>
        )}
      </p>
      <p className="mt-1 text-[11px] uppercase tracking-wide text-violet-200">
        Promoted candidate
        {candidate.quality_bucket ? ` · Quality: ${candidate.quality_bucket}` : ""}
      </p>
      <p className="mt-1 text-[11px] uppercase tracking-wide text-slate-500">
        Grounding: {groundingStatusLabel(candidate.grounding_status)}
        {candidate.provider_source ? ` · Source: ${candidate.provider_source}` : ""}
      </p>
      {candidate.promotion_reasons.length > 0 && (
        <ul className="mt-1 list-disc break-words pl-4 text-xs text-emerald-300/80">
          {candidate.promotion_reasons.map((reason, index) => (
            <li key={`${candidate.candidate_id}-reason-${index}`}>{reason}</li>
          ))}
        </ul>
      )}
      {candidate.warnings.length > 0 && (
        <ul className="mt-1 list-disc break-words pl-4 text-xs text-slate-400">
          {candidate.warnings.map((warning, index) => (
            <li key={`${candidate.candidate_id}-warning-${index}`}>{warning}</li>
          ))}
        </ul>
      )}
    </li>
  );
}

/**
 * AI candidate review/promotion panel (Section 170, docs/13_llm_
 * reasoning_pipeline.md sections 80-83, docs/14_backend_architecture.md
 * sections 57-60). Renders `GET /trips/{trip_id}/ai-candidate-review`
 * (always read-only, never mutates anything) and, when it exists, the
 * already-computed `ai_candidate_promotion_report`.
 *
 * This panel never adds a candidate to the itinerary itself -- scheduling
 * stays entirely backend-owned (Step 170D): "Eligible for scheduling" and
 * "Promoted" are both informational statuses only, never a claim of a
 * confirmed booking, independent verification by the travel provider,
 * certainty, or a settled final decision. A candidate reaches "Promoted"
 * only after being AI-proposed, provider-grounded, quality-approved, and
 * deterministically promoted by the backend -- an AI suggestion alone is
 * never enough.
 */
function AICandidateReviewSection({
  tripId,
  reviewReport,
  promotionReport,
  onPromotionReportChange,
}: {
  tripId: string;
  reviewReport: AICandidateReviewReport | null;
  promotionReport: AICandidatePromotionReport | null;
  onPromotionReportChange: (report: AICandidatePromotionReport) => void;
}) {
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);

  async function handleRefreshPromotions() {
    setIsRefreshing(true);
    setRefreshError(null);
    try {
      const data = await promoteAiCandidates(tripId);
      onPromotionReportChange(data.ai_candidate_promotion_report);
    } catch (err) {
      setRefreshError(
        err instanceof ApiRequestError
          ? err.message
          : "Something went wrong while refreshing the AI promotion report.",
      );
    } finally {
      setIsRefreshing(false);
    }
  }

  const hasCandidateData =
    reviewReport !== null && reviewReport.status !== "no_candidate_data";
  const items = reviewReport?.items ?? [];
  const eligibleItems = items.filter((item) => item.eligible_for_promotion);
  const notEligibleItems = items.filter((item) => !item.eligible_for_promotion);
  const promotedCandidates = promotionReport?.promoted_candidates ?? [];
  const skippedIds = promotionReport?.skipped_candidate_ids ?? [];

  return (
    <div id="ai-candidate-review" className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h2 className="text-lg font-semibold">AI candidate review</h2>
      <DisclaimerNote tone="amber">
        AI-suggested candidates are never scheduled directly. Only
        provider-grounded, quality-approved candidates can become eligible
        for scheduling, and scheduling itself stays fully backend-owned.
      </DisclaimerNote>

      {!hasCandidateData ? (
        <p className="mt-3 text-sm text-slate-300">
          No AI candidate data is available for this trip yet.
        </p>
      ) : (
        <>
          <dl className="mt-3 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
            <div className="rounded-lg border border-white/10 bg-slate-900/60 p-3">
              <dt className="text-[11px] uppercase tracking-wide text-slate-500">
                Total AI candidates
              </dt>
              <dd className="mt-1 font-semibold text-slate-100">
                {reviewReport!.total_ai_candidates}
              </dd>
            </div>
            <div className="rounded-lg border border-white/10 bg-slate-900/60 p-3">
              <dt className="text-[11px] uppercase tracking-wide text-slate-500">
                Provider-grounded
              </dt>
              <dd className="mt-1 font-semibold text-slate-100">
                {reviewReport!.grounded_candidates}
              </dd>
            </div>
            <div className="rounded-lg border border-white/10 bg-slate-900/60 p-3">
              <dt className="text-[11px] uppercase tracking-wide text-slate-500">
                Ungrounded
              </dt>
              <dd className="mt-1 font-semibold text-slate-100">
                {reviewReport!.ungrounded_candidates}
              </dd>
            </div>
            <div className="rounded-lg border border-white/10 bg-slate-900/60 p-3">
              <dt className="text-[11px] uppercase tracking-wide text-slate-500">
                Eligible for scheduling
              </dt>
              <dd className="mt-1 font-semibold text-slate-100">
                {reviewReport!.eligible_for_promotion}
              </dd>
            </div>
            {promotionReport && (
              <>
                <div className="rounded-lg border border-white/10 bg-slate-900/60 p-3">
                  <dt className="text-[11px] uppercase tracking-wide text-slate-500">
                    Promoted candidates
                  </dt>
                  <dd className="mt-1 font-semibold text-slate-100">
                    {promotionReport.promoted_count}
                  </dd>
                </div>
                <div className="rounded-lg border border-white/10 bg-slate-900/60 p-3">
                  <dt className="text-[11px] uppercase tracking-wide text-slate-500">
                    Skipped candidates
                  </dt>
                  <dd className="mt-1 font-semibold text-slate-100">
                    {promotionReport.skipped_count}
                  </dd>
                </div>
              </>
            )}
          </dl>

          <div className="mt-4 flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={() => void handleRefreshPromotions()}
              disabled={isRefreshing}
              className={`rounded-full border border-cyan-300/40 bg-slate-900 px-3 py-1 text-xs font-semibold text-cyan-200 transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50 ${FOCUS_RING_CLASSNAME}`}
            >
              {isRefreshing ? "Refreshing..." : "Refresh AI promotion report"}
            </button>
            {refreshError && (
              <p className="break-words text-xs text-red-300">{refreshError}</p>
            )}
          </div>
          <p className="mt-2 text-[11px] text-slate-500">
            Refreshing only recomputes which already-grounded candidates are
            eligible/promoted -- it never calls an AI provider and never
            schedules anything into the itinerary by itself.
          </p>

          <div className="mt-4">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
              Eligible for scheduling
            </p>
            {eligibleItems.length === 0 ? (
              <p className="mt-2 text-xs text-slate-400">
                No AI candidates are currently eligible for scheduling.
              </p>
            ) : (
              <ul className="mt-2 flex flex-col gap-2">
                {eligibleItems.map((item) => (
                  <AICandidateReviewCard key={item.candidate_id} item={item} />
                ))}
              </ul>
            )}
          </div>

          <div className="mt-4">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
              Not eligible
            </p>
            {notEligibleItems.length === 0 ? (
              <p className="mt-2 text-xs text-slate-400">
                No AI candidates were reviewed as not eligible.
              </p>
            ) : (
              <ul className="mt-2 flex flex-col gap-2">
                {notEligibleItems.map((item) => (
                  <AICandidateReviewCard key={item.candidate_id} item={item} />
                ))}
              </ul>
            )}
          </div>

          <div className="mt-4">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
              Promoted candidates
            </p>
            {!promotionReport ? (
              <p className="mt-2 text-xs text-slate-400">
                No promotion report has been computed yet.
              </p>
            ) : promotedCandidates.length === 0 ? (
              <p className="mt-2 text-xs text-slate-400">
                No candidates have been promoted yet.
              </p>
            ) : (
              <ul className="mt-2 flex flex-col gap-2">
                {promotedCandidates.map((candidate) => (
                  <PromotedAICandidateCard
                    key={candidate.candidate_id}
                    candidate={candidate}
                  />
                ))}
              </ul>
            )}
          </div>

          <div className="mt-4">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
              Skipped candidates
            </p>
            {!promotionReport ? (
              <p className="mt-2 text-xs text-slate-400">
                No promotion report has been computed yet.
              </p>
            ) : skippedIds.length === 0 ? (
              <p className="mt-2 text-xs text-slate-400">
                No candidates were skipped.
              </p>
            ) : (
              <ul className="mt-2 flex flex-col gap-1 break-words text-xs text-slate-400">
                {skippedIds.map((candidateId) => {
                  const matchingItem = items.find(
                    (item) => item.candidate_id === candidateId,
                  );
                  return (
                    <li key={candidateId} className={matchingItem ? "" : "break-all"}>
                      {matchingItem
                        ? `${matchingItem.name}${
                            matchingItem.category
                              ? ` (${matchingItem.category})`
                              : ""
                          }`
                        : candidateId}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          <p className="mt-4 text-xs text-slate-500">
            A promoted candidate is not an itinerary stop by itself -- it
            only becomes a scheduled experience if the backend&apos;s
            existing geographic/quality/pace rules pick it, exactly like
            any real provider candidate. A scheduled experience that came
            from a promoted candidate is labeled &ldquo;AI-suggested ·
            Provider-grounded&rdquo; in the itinerary above.
          </p>
        </>
      )}
    </div>
  );
}

function changePreviewRegenerationLabel(
  wouldRequireRegeneration: boolean | null,
): string {
  if (wouldRequireRegeneration === true) return "Would require regeneration";
  if (wouldRequireRegeneration === false) {
    return "Would not require regeneration (manual review only)";
  }
  return "Regeneration requirement unknown";
}

/**
 * Compact, honest preview of what a future regeneration step would likely
 * need to change (Step 122). Purely a readout of the backend's deterministic
 * `interpretation.change_preview` -- it never claims anything was applied,
 * updated, or regenerated, since the feedback capture endpoint never
 * touches any plan section.
 */
function FeedbackChangePreviewSection({
  changePreview,
}: {
  changePreview: FeedbackChangePreview;
}) {
  return (
    <div className="mt-2 rounded-md border border-white/10 bg-slate-950/60 p-2">
      <p className="text-[11px] uppercase tracking-wide text-slate-500">
        Change preview
      </p>
      <p className="mt-1 text-xs text-amber-300/90">
        This is a preview only. No plan sections have been changed.
      </p>
      <p className="mt-1 text-xs text-slate-400">
        Preview status:{" "}
        <span className="text-slate-300">
          {changePreview.preview_status === "not_applied"
            ? "Not applied"
            : changePreview.preview_status}
        </span>
      </p>
      <p className="mt-1 text-xs text-slate-400">
        {changePreviewRegenerationLabel(
          changePreview.would_require_regeneration,
        )}
      </p>

      {changePreview.likely_changes.length > 0 && (
        <div className="mt-2">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
            Likely future changes
          </p>
          <ul className="mt-1 list-disc break-words pl-4 text-xs text-slate-300">
            {changePreview.likely_changes.map((change, index) => (
              <li key={`likely-change-${index}`}>{change}</li>
            ))}
          </ul>
        </div>
      )}

      {changePreview.unchanged_sections.length > 0 && (
        <div className="mt-2">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
            Unchanged sections
          </p>
          <ul className="mt-1 list-disc break-words pl-4 text-xs text-slate-400">
            {changePreview.unchanged_sections.map((section, index) => (
              <li key={`unchanged-section-${index}`}>{section}</li>
            ))}
          </ul>
        </div>
      )}

      {changePreview.blocked_by.length > 0 && (
        <div className="mt-2">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
            Blocked by
          </p>
          <ul className="mt-1 list-disc break-words pl-4 text-xs text-slate-400">
            {changePreview.blocked_by.map((reason, index) => (
              <li key={`blocked-by-${index}`}>{reason}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function pendingSummaryRegenerationLabel(requiresRegeneration: boolean): string {
  return requiresRegeneration
    ? "Would require regeneration: Yes"
    : "Would require regeneration: No";
}

/**
 * Plan-level rollup of all captured feedback (Step 125). Purely a readout
 * of the backend's deterministic `pending_feedback_summary` -- it never
 * claims anything was applied, updated, or regenerated, since the feedback
 * capture endpoint never touches any plan section.
 */
function PendingRequestedChangesSection({
  summary,
}: {
  summary: PendingFeedbackSummary;
}) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h2 className="text-lg font-semibold">Pending requested changes</h2>
      <DisclaimerNote tone="amber">
        These requests are summarized from captured feedback. They have not
        been applied to the plan yet.
      </DisclaimerNote>

      <DisclaimerNote tone="slate" spacingClassName="mt-3">
        Feedback-driven regeneration is available once the &ldquo;Regeneration
        readiness&rdquo; section below reports it is ready -- use its
        &ldquo;Regenerate from feedback&rdquo; button. Your feedback is stored
        and summarized here regardless; this section never applies it itself.
      </DisclaimerNote>

      {summary.total_feedback_items === 0 ? (
        <p className="mt-3 text-sm text-slate-400">
          No requested changes captured yet.
        </p>
      ) : (
        <>
          <dl className="mt-3 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
            <div className="rounded-lg border border-white/10 bg-slate-900/60 p-3">
              <dt className="text-[11px] uppercase tracking-wide text-slate-500">
                Status
              </dt>
              <dd className="mt-1 font-semibold text-slate-100">
                {summary.status === "captured_not_applied"
                  ? "Captured, not applied"
                  : summary.status}
              </dd>
            </div>
            <div className="rounded-lg border border-white/10 bg-slate-900/60 p-3">
              <dt className="text-[11px] uppercase tracking-wide text-slate-500">
                Total requests
              </dt>
              <dd className="mt-1 font-semibold text-slate-100">
                {summary.total_feedback_items}
              </dd>
            </div>
            <div className="rounded-lg border border-white/10 bg-slate-900/60 p-3">
              <dt className="text-[11px] uppercase tracking-wide text-slate-500">
                Regeneration
              </dt>
              <dd className="mt-1 font-semibold text-slate-100">
                {summary.requires_regeneration ? "Yes" : "No"}
              </dd>
            </div>
            <div className="rounded-lg border border-white/10 bg-slate-900/60 p-3">
              <dt className="text-[11px] uppercase tracking-wide text-slate-500">
                Latest request
              </dt>
              <dd className="mt-1 font-semibold text-slate-100">
                {summary.latest_feedback_at
                  ? new Date(summary.latest_feedback_at).toLocaleString()
                  : "N/A"}
              </dd>
            </div>
          </dl>

          <p className="mt-3 text-xs text-slate-400">
            {pendingSummaryRegenerationLabel(summary.requires_regeneration)}
          </p>

          {summary.affected_stages.length > 0 && (
            <div className="mt-3">
              <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                Affected stages
              </p>
              <p className="mt-1 text-sm text-slate-300">
                {summary.affected_stages.join(", ")}
              </p>
            </div>
          )}

          {summary.summary_items.length > 0 && (
            <div className="mt-4">
              <p className="text-sm font-semibold text-slate-200">
                Requests by type
              </p>
              <ul className="mt-2 flex flex-col gap-2">
                {summary.summary_items.map((item) => (
                  <li
                    key={item.feedback_type}
                    className="rounded-lg border border-white/10 bg-slate-900/60 p-3 text-sm"
                  >
                    <p className="flex flex-wrap items-center justify-between gap-2">
                      <span className="min-w-0 break-words font-semibold text-slate-200">
                        {item.feedback_type}
                      </span>
                      <span className="text-[11px] uppercase tracking-wide text-slate-400">
                        Count: {item.count}
                      </span>
                    </p>
                    <p className="mt-1 break-words text-xs text-slate-400">
                      Example: {item.example_feedback}
                    </p>
                    {item.likely_changes.length > 0 && (
                      <div className="mt-2">
                        <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                          Likely future changes
                        </p>
                        <ul className="mt-1 list-disc break-words pl-4 text-xs text-slate-300">
                          {item.likely_changes.map((change, index) => (
                            <li key={`${item.feedback_type}-change-${index}`}>
                              {change}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {summary.blocked_by.length > 0 && (
            <div className="mt-4">
              <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                Blocked by
              </p>
              <ul className="mt-1 list-disc break-words pl-4 text-xs text-slate-400">
                {summary.blocked_by.map((reason, index) => (
                  <li key={`blocked-by-${index}`}>{reason}</li>
                ))}
              </ul>
            </div>
          )}

          <p className="mt-3 break-words text-xs text-slate-400">{summary.note}</p>
        </>
      )}
    </div>
  );
}

/**
 * Plan-level readout of `PlanningState.version_history` (Step 133). Purely
 * a restatement of backend bookkeeping about which pipeline sections were
 * produced/changed for each recorded version -- never a snapshot of their
 * travel-fact content, and never itself a claim that regeneration ran.
 */
function VersionHistorySection({
  versionHistory,
}: {
  versionHistory: VersionHistoryItem[];
}) {
  return (
    <div id="version-history" className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h2 className="text-lg font-semibold">Version history</h2>
      <DisclaimerNote tone="amber">
        Version history records backend bookkeeping only. It does not add
        travel facts.
      </DisclaimerNote>

      {versionHistory.length === 0 ? (
        <p className="mt-3 text-sm text-slate-400">
          No generated plan version has been recorded yet.
        </p>
      ) : (
        <ul className="mt-3 flex flex-col gap-2">
          {versionHistory.map((version) => (
            <li
              key={version.version_id}
              className="rounded-lg border border-white/10 bg-slate-900/60 p-3 text-sm"
            >
              <p className="flex flex-wrap items-center justify-between gap-2">
                <span className="min-w-0 break-words font-semibold text-slate-200">
                  {version.version_label}
                </span>
                <span className="text-[11px] uppercase tracking-wide text-slate-400">
                  {version.created_by}
                </span>
              </p>
              <p className="mt-1 text-[11px] text-slate-500">
                Recorded: {new Date(version.created_at).toLocaleString()}
              </p>
              {version.summary && (
                <p className="mt-2 break-words text-xs text-slate-300">
                  {version.summary}
                </p>
              )}
              {version.changed_sections.length > 0 && (
                <p className="mt-2 text-xs text-slate-400">
                  Changed sections: {version.changed_sections.join(", ")}
                </p>
              )}
              {version.preserved_sections.length > 0 && (
                <p className="mt-1 text-xs text-slate-400">
                  Preserved sections: {version.preserved_sections.join(", ")}
                </p>
              )}
              {version.feedback_event_id && (
                <p className="mt-1 break-all text-xs text-slate-400">
                  Triggered by feedback event: {version.feedback_event_id}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function planDiffPreviewStatusLabel(previewStatus: string): string {
  if (previewStatus === "regeneration_available") {
    return "Regeneration is available";
  }
  if (previewStatus === "ready_for_future_regeneration_preview") {
    return "Ready for future regeneration preview";
  }
  if (previewStatus === "not_available") {
    return "Diff preview is not available yet.";
  }
  return previewStatus;
}

function formatNullableVersionLabel(version: string | null): string {
  return version ?? "None yet";
}

// Human-readable labels for the backend's regeneration reason/error codes
// (Step 179D copy polish). Backend: app.core.errors's REGENERATION_*
// AppError codes plus the frontend's own "UNKNOWN_ERROR" catch-all -- this
// only relabels a code already shown alongside its own full `message`
// text below it; it never replaces or summarizes that message, and an
// unrecognized code still displays as-is rather than being hidden.
const REGENERATION_REASON_CODE_LABELS: Record<string, string> = {
  REGENERATION_NOT_AVAILABLE: "Regeneration not available",
  REGENERATION_BLOCKED_BY_LOCKS: "Blocked by active locks",
  REGENERATION_NO_PENDING_FEEDBACK: "No pending feedback",
  REGENERATION_APPLIED: "Regeneration applied",
  UNKNOWN_ERROR: "Unknown error",
};

function regenerationReasonCodeLabel(code: string): string {
  return REGENERATION_REASON_CODE_LABELS[code] ?? code;
}

/**
 * Plan-level readout of `PlanningState.plan_diff_preview` (Step 133). Purely
 * a restatement of the backend's deterministic, from-scratch-recomputed
 * preview of what a regeneration would compare/change -- never something
 * this section applies itself; only the real "Regenerate from feedback"
 * button in `RegenerationReadinessSection` below does that. As of Step
 * 174D, `regeneration_available` renders "Yes" only for the exact MVP
 * scope the backend actually supports (generated plan + pending feedback
 * + zero active locks + a real derivable affected stage); `to_version`
 * still always renders "None yet" since this preview never fills it in
 * (a completed diff's `to_version` isn't tracked by this model).
 */
function PlanDiffPreviewSection({ preview }: { preview: PlanDiffPreview }) {
  return (
    <div id="plan-diff-preview" className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h2 className="text-lg font-semibold">Plan diff preview</h2>
      <DisclaimerNote tone="amber">
        This is a preview only. No new version or plan diff has been
        generated yet.
      </DisclaimerNote>

      <dl className="mt-3 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
        <div className="rounded-lg border border-white/10 bg-slate-900/60 p-3">
          <dt className="text-[11px] uppercase tracking-wide text-slate-500">
            Status
          </dt>
          <dd className="mt-1 font-semibold text-slate-100">
            {planDiffPreviewStatusLabel(preview.preview_status)}
          </dd>
        </div>
        <div className="rounded-lg border border-white/10 bg-slate-900/60 p-3">
          <dt className="text-[11px] uppercase tracking-wide text-slate-500">
            From version
          </dt>
          <dd className="mt-1 font-semibold text-slate-100">
            {formatNullableVersionLabel(preview.from_version)}
          </dd>
        </div>
        <div className="rounded-lg border border-white/10 bg-slate-900/60 p-3">
          <dt className="text-[11px] uppercase tracking-wide text-slate-500">
            To version
          </dt>
          <dd className="mt-1 font-semibold text-slate-100">
            {formatNullableVersionLabel(preview.to_version)}
          </dd>
        </div>
        <div className="rounded-lg border border-white/10 bg-slate-900/60 p-3">
          <dt className="text-[11px] uppercase tracking-wide text-slate-500">
            Regeneration available
          </dt>
          <dd className="mt-1 font-semibold text-slate-100">
            {preview.regeneration_available ? "Yes" : "No"}
          </dd>
        </div>
        <div className="rounded-lg border border-white/10 bg-slate-900/60 p-3">
          <dt className="text-[11px] uppercase tracking-wide text-slate-500">
            Pending feedback count
          </dt>
          <dd className="mt-1 font-semibold text-slate-100">
            {preview.pending_feedback_count}
          </dd>
        </div>
        <div className="rounded-lg border border-white/10 bg-slate-900/60 p-3">
          <dt className="text-[11px] uppercase tracking-wide text-slate-500">
            Active lock count
          </dt>
          <dd className="mt-1 font-semibold text-slate-100">
            {preview.active_lock_count}
          </dd>
        </div>
        {preview.would_create_version && (
          <div className="rounded-lg border border-white/10 bg-slate-900/60 p-3">
            <dt className="text-[11px] uppercase tracking-wide text-slate-500">
              Would create version
            </dt>
            <dd className="mt-1 font-semibold text-slate-100">
              {preview.would_create_version}
            </dd>
          </div>
        )}
      </dl>

      {preview.would_consider_sections.length > 0 && (
        <div className="mt-3">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
            Would consider sections
          </p>
          <p className="mt-1 text-sm text-slate-300">
            {preview.would_consider_sections.join(", ")}
          </p>
        </div>
      )}

      {preview.would_preserve_locked_items.length > 0 && (
        <div className="mt-3">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
            Would preserve locked items
          </p>
          <ul className="mt-2 flex flex-col gap-2">
            {preview.would_preserve_locked_items.map((item, index) => (
              <li
                key={`${item.locked_item_type}-${item.locked_item_id}-${index}`}
                className="break-words rounded-lg border border-white/10 bg-slate-900/60 p-3 text-xs text-slate-300"
              >
                Type: {item.locked_item_type} · ID:{" "}
                <span className="break-all">{item.locked_item_id}</span>
                {" · "}
                Reason: {item.reason}
              </li>
            ))}
          </ul>
        </div>
      )}

      {preview.triggered_by_feedback_event_ids.length > 0 && (
        <div className="mt-3">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
            Triggered by feedback events
          </p>
          <p className="mt-1 break-words text-xs text-slate-400">
            {preview.triggered_by_feedback_event_ids.join(", ")}
          </p>
        </div>
      )}

      {preview.blocked_by.length > 0 && (
        <div className="mt-3">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
            Blocked by
          </p>
          <ul className="mt-1 list-disc break-words pl-4 text-xs text-slate-400">
            {preview.blocked_by.map((reason, index) => (
              <li key={`plan-diff-blocked-by-${index}`}>{reason}</li>
            ))}
          </ul>
        </div>
      )}

      <p className="mt-3 break-words text-xs text-slate-400">{preview.note}</p>
    </div>
  );
}

/**
 * Plan-level readout of `PlanningState.regeneration_readiness` (Step 136),
 * plus (Step 174E) the one real regenerate action itself. Purely mirrors
 * the backend's deterministic, from-scratch-recomputed readiness gate --
 * this component never decides for itself whether regeneration is safe;
 * it only enables the button when `readiness.can_regenerate` (computed
 * entirely server-side, Step 174D) already says so, and the backend
 * re-checks every condition again on the actual call regardless.
 *
 * The "Regenerate from feedback" button calls
 * `POST /trips/{trip_id}/regenerate` with `confirm: true` (Step 174B-174D
 * contract). On success it shows the backend's own
 * `RegenerateResponseData` (previous/current version, changed sections,
 * applied feedback event ids -- never a frontend-computed diff) and calls
 * `onRegenerateSuccess`, which the caller wires to a full `loadPlanResult`
 * refresh so the itinerary, movement rows, route paths, version history,
 * diff preview, and readiness panels all reflect the regenerated state
 * together. On refusal, it shows the backend's own error `code`/`message`
 * (via `ApiRequestError`) and refreshes only the attempt audit list via
 * `onRegenerationAttemptsChange` -- never implying the plan changed, and
 * never calling `loadPlanResult`.
 */
function RegenerationReadinessSection({
  tripId,
  readiness,
  onRegenerationAttemptsChange,
  onRegenerateSuccess,
  compact = false,
}: {
  tripId: string;
  readiness: RegenerationReadiness;
  onRegenerationAttemptsChange: (attempts: RegenerationAttempt[]) => void;
  onRegenerateSuccess: () => Promise<void>;
  // Step 182C: `compact` renders the same readiness state and the exact
  // same regenerate button/handler/success/error UI, but hides the
  // dl-grid diagnostic breakdown, required/available/missing-input lists,
  // and blocked-by list -- for User Mode's "concise" regeneration
  // requirement. It never changes what `handleRegenerate` does, and it
  // is never rendered at the same time as the full (non-compact) version
  // -- exactly one of the two is mounted at once, gated by the page's
  // mode toggle, so there is never a risk of two independent regenerate
  // requests racing each other.
  compact?: boolean;
}) {
  const [isRegenerating, setIsRegenerating] = useState(false);
  const [regenerateError, setRegenerateError] = useState<{
    code: string;
    message: string;
  } | null>(null);
  const [regenerateSuccess, setRegenerateSuccess] =
    useState<RegenerateResponseData | null>(null);

  async function handleRegenerate() {
    setIsRegenerating(true);
    setRegenerateError(null);
    setRegenerateSuccess(null);
    try {
      const data = await requestRegeneration(tripId);
      setRegenerateSuccess(data);
      // Full refresh so the itinerary, movement rows, route paths, version
      // history, diff preview, and readiness all reflect the regenerated
      // state together -- never just this section's own local state.
      await onRegenerateSuccess();
    } catch (err) {
      setRegenerateError(
        err instanceof ApiRequestError
          ? { code: err.code ?? "UNKNOWN_ERROR", message: err.message }
          : {
              code: "UNKNOWN_ERROR",
              message: "Something went wrong while requesting regeneration.",
            },
      );
      // Refusal only ever appends one audit attempt -- refresh just that
      // list, never the rest of the plan, and never loadPlanResult.
      try {
        const attemptsData = await getRegenerationAttempts(tripId);
        onRegenerationAttemptsChange(attemptsData.regeneration_attempts);
      } catch {
        // If refreshing the audit list itself fails, leave the previously
        // displayed attempts as-is instead of clearing them.
      }
    } finally {
      setIsRegenerating(false);
    }
  }

  return (
    <div id="regeneration-readiness" className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h2 className="text-lg font-semibold">Regeneration readiness</h2>
      <DisclaimerNote tone="amber">
        This section explains whether feedback-driven regeneration can run
        right now, and lets you apply it only when the backend says it is
        available.
      </DisclaimerNote>

      {compact ? (
        <p className="mt-3 text-sm text-slate-300">
          Status: <span className="font-semibold">{readiness.status}</span>
          {" · "}
          Pending feedback: {readiness.pending_feedback_count}
          {" · "}
          Active locks: {readiness.active_lock_count}
        </p>
      ) : (
        <>
          <dl className="mt-3 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
            <div className="rounded-lg border border-white/10 bg-slate-900/60 p-3">
              <dt className="text-[11px] uppercase tracking-wide text-slate-500">
                Status
              </dt>
              <dd className="mt-1 font-semibold text-slate-100">
                {readiness.status}
              </dd>
            </div>
            <div className="rounded-lg border border-white/10 bg-slate-900/60 p-3">
              <dt className="text-[11px] uppercase tracking-wide text-slate-500">
                Can regenerate
              </dt>
              <dd className="mt-1 font-semibold text-slate-100">
                {readiness.can_regenerate ? "Yes" : "No"}
              </dd>
            </div>
            <div className="rounded-lg border border-white/10 bg-slate-900/60 p-3">
              <dt className="text-[11px] uppercase tracking-wide text-slate-500">
                Current version
              </dt>
              <dd className="mt-1 font-semibold text-slate-100">
                {formatNullableVersionLabel(readiness.current_version)}
              </dd>
            </div>
            <div className="rounded-lg border border-white/10 bg-slate-900/60 p-3">
              <dt className="text-[11px] uppercase tracking-wide text-slate-500">
                Would create version
              </dt>
              <dd className="mt-1 font-semibold text-slate-100">
                {formatNullableVersionLabel(readiness.would_create_version)}
              </dd>
            </div>
            <div className="rounded-lg border border-white/10 bg-slate-900/60 p-3">
              <dt className="text-[11px] uppercase tracking-wide text-slate-500">
                Pending feedback count
              </dt>
              <dd className="mt-1 font-semibold text-slate-100">
                {readiness.pending_feedback_count}
              </dd>
            </div>
            <div className="rounded-lg border border-white/10 bg-slate-900/60 p-3">
              <dt className="text-[11px] uppercase tracking-wide text-slate-500">
                Active lock count
              </dt>
              <dd className="mt-1 font-semibold text-slate-100">
                {readiness.active_lock_count}
              </dd>
            </div>
          </dl>

          {readiness.required_inputs.length > 0 && (
            <div className="mt-3">
              <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                Required inputs
              </p>
              <p className="mt-1 text-sm text-slate-300">
                {readiness.required_inputs.join(", ")}
              </p>
            </div>
          )}

          {readiness.available_inputs.length > 0 && (
            <div className="mt-3">
              <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                Available inputs
              </p>
              <p className="mt-1 text-sm text-slate-300">
                {readiness.available_inputs.join(", ")}
              </p>
            </div>
          )}

          {readiness.missing_capabilities.length > 0 && (
            <div className="mt-3">
              <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                Missing capabilities
              </p>
              <p className="mt-1 text-sm text-slate-300">
                {readiness.missing_capabilities.join(", ")}
              </p>
            </div>
          )}

          {readiness.blocked_by.length > 0 && (
            <div className="mt-3">
              <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                Blocked by
              </p>
              <ul className="mt-1 list-disc break-words pl-4 text-xs text-slate-400">
                {readiness.blocked_by.map((reason, index) => (
                  <li key={`regeneration-readiness-blocked-by-${index}`}>
                    {reason}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <p className="mt-3 text-xs text-slate-400">
            Next step: {readiness.next_step}
          </p>
        </>
      )}

      <div className="mt-4 border-t border-white/10 pt-4">
        <button
          type="button"
          onClick={() => void handleRegenerate()}
          disabled={!readiness.can_regenerate || isRegenerating}
          title={
            readiness.can_regenerate
              ? undefined
              : "Regeneration is available only when feedback is pending and no active locks exist."
          }
          className={`rounded-lg border border-white/10 bg-emerald-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-emerald-500 disabled:cursor-not-allowed disabled:border-white/10 disabled:bg-slate-900 disabled:text-slate-500 disabled:opacity-50 ${FOCUS_RING_CLASSNAME}`}
        >
          {isRegenerating
            ? "Regenerating..."
            : compact
              ? "Regenerate when allowed"
              : "Regenerate from feedback"}
        </button>
        <p className="mt-2 text-xs text-slate-500">
          {compact
            ? "Regeneration is blocked until feedback exists and no active locks are present."
            : "Regeneration is available only when feedback is pending and no active locks exist."}
        </p>

        {regenerateSuccess && (
          <div className="mt-3 rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-3 text-sm">
            <p className="font-semibold text-emerald-300">
              Regeneration applied
            </p>
            <p className="mt-1 text-xs text-emerald-200">
              {formatNullableVersionLabel(regenerateSuccess.previous_version)}
              {" → "}
              {regenerateSuccess.current_version}
            </p>
            {regenerateSuccess.changed_sections.length > 0 && (
              <p className="mt-1 text-xs text-emerald-200">
                Changed: {regenerateSuccess.changed_sections.join(", ")}
              </p>
            )}
            {regenerateSuccess.preserved_sections.length > 0 && (
              <p className="mt-1 text-xs text-emerald-200">
                Preserved: {regenerateSuccess.preserved_sections.join(", ")}
              </p>
            )}
            {regenerateSuccess.applied_feedback_event_ids.length > 0 && (
              <p className="mt-1 break-words text-xs text-emerald-200">
                Applied feedback:{" "}
                {regenerateSuccess.applied_feedback_event_ids.join(", ")}
              </p>
            )}
          </div>
        )}

        {regenerateError && (
          <div className="mt-3 rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm">
            <p className="break-words font-semibold text-red-300">
              {regenerationReasonCodeLabel(regenerateError.code)}
            </p>
            <p className="mt-1 break-words text-xs text-red-200">
              {regenerateError.message}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Plan-level readout of `PlanningState.regeneration_attempts` (Step 143,
 * extended to real "applied" attempts in Step 174C/174D). Purely a
 * restatement of the backend's audit trail -- never itinerary content
 * beyond `attempt.status`/section-name-level bookkeeping the backend
 * itself already recorded. A refusal from `RegenerationReadinessSection`'s
 * "Regenerate from feedback" button refreshes only this list directly
 * (`result.regenerationAttempts`, never `loadPlanResult`); a success
 * refreshes it as part of that same button's full `loadPlanResult` call.
 */
function RegenerationAttemptAuditSection({
  attempts,
}: {
  attempts: RegenerationAttempt[];
}) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h2 className="text-lg font-semibold">Regeneration attempt audit</h2>
      <DisclaimerNote tone="amber">
        This is an audit trail of blocked regeneration requests. It does
        not contain itinerary content and does not mean regeneration ran.
      </DisclaimerNote>

      {attempts.length === 0 ? (
        <p className="mt-3 text-sm text-slate-400">
          No regeneration attempts recorded yet.
        </p>
      ) : (
        <ul className="mt-3 flex flex-col gap-2">
          {attempts.map((attempt) => (
            <li
              key={attempt.attempt_id}
              className="rounded-lg border border-white/10 bg-slate-900/60 p-3 text-sm"
            >
              <p className="flex flex-wrap items-center justify-between gap-2">
                <span className="min-w-0 break-words font-semibold text-slate-200">
                  {attempt.status}
                </span>
                <span className="text-[11px] uppercase tracking-wide text-slate-400">
                  {new Date(attempt.requested_at).toLocaleString()}
                </span>
              </p>
              <p className="mt-1 text-xs text-slate-400">
                Current version:{" "}
                {formatNullableVersionLabel(attempt.current_version)}
                {" · "}
                Would create version:{" "}
                {formatNullableVersionLabel(attempt.would_create_version)}
              </p>
              <p className="mt-1 text-xs text-slate-400">
                Pending feedback: {attempt.pending_feedback_count}
                {" · "}
                Active locks: {attempt.active_lock_count}
              </p>
              <p className="mt-1 text-[11px] uppercase tracking-wide text-slate-500">
                {regenerationReasonCodeLabel(attempt.reason_code)}
              </p>
              <p className="mt-1 break-words text-xs text-slate-300">{attempt.message}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function FeedbackPanel({
  feedbackText,
  onFeedbackTextChange,
  onSubmit,
  isSubmitting,
  successMessage,
  errorMessage,
  feedbackHistory,
}: {
  feedbackText: string;
  onFeedbackTextChange: (value: string) => void;
  onSubmit: () => void;
  isSubmitting: boolean;
  successMessage: string | null;
  errorMessage: string | null;
  feedbackHistory: FeedbackEvent[];
}) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h2 className="text-lg font-semibold">Request changes</h2>
      <p className="mt-1 text-xs text-slate-500">
        Feedback is captured for now. Plan regeneration will be added later.
      </p>

      <textarea
        className={`mt-3 w-full rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-sm text-slate-100 ${FOCUS_RING_CLASSNAME}`}
        rows={3}
        placeholder="e.g. Make this less packed"
        value={feedbackText}
        onChange={(event) => onFeedbackTextChange(event.target.value)}
      />

      <button
        type="button"
        onClick={onSubmit}
        disabled={isSubmitting}
        className={`mt-3 rounded-lg border border-cyan-300/40 bg-slate-900 px-4 py-2 text-sm font-semibold text-cyan-200 transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50 ${FOCUS_RING_CLASSNAME}`}
      >
        {isSubmitting ? "Saving feedback..." : "Submit feedback"}
      </button>

      {errorMessage && (
        <p className="mt-3 break-words text-sm text-red-300">{errorMessage}</p>
      )}
      {successMessage && !errorMessage && (
        <p className="mt-3 break-words text-sm text-emerald-300">{successMessage}</p>
      )}

      <p className="mt-4 text-xs font-semibold uppercase tracking-wide text-slate-500">
        Feedback history ({feedbackHistory.length})
      </p>
      <p className="mt-1 text-xs text-amber-300/90">
        Interpretation is preliminary and rule-based. These requests are
        stored but not applied to the plan yet.
      </p>
      {feedbackHistory.length === 0 ? (
        <p className="mt-2 text-sm text-slate-400">
          No feedback captured yet.
        </p>
      ) : (
        <ul className="mt-2 flex flex-col gap-2">
          {feedbackHistory.map((event) => (
            <li
              key={event.feedback_event_id}
              className="rounded-lg border border-white/10 bg-slate-900/60 p-3 text-sm"
            >
              <p className="break-words text-slate-200">{event.feedback_text}</p>
              <p className="mt-1 text-[11px] uppercase tracking-wide text-slate-500">
                {event.handling_status} ·{" "}
                {new Date(event.created_at).toLocaleString()}
              </p>
              {event.feedback_type && (
                <p className="mt-1 text-xs text-slate-400">
                  Feedback type:{" "}
                  <span className="text-slate-300">{event.feedback_type}</span>
                </p>
              )}
              {event.affected_stages.length > 0 && (
                <p className="mt-1 text-xs text-slate-400">
                  Possibly affected stages:{" "}
                  {event.affected_stages.join(", ")}
                </p>
              )}
              <p className="mt-1 text-xs text-slate-400">
                Regeneration strategy: {event.regeneration_strategy}
              </p>
              {event.interpretation && (
                <div className="mt-2 rounded-md border border-white/10 bg-slate-950/60 p-2">
                  <p className="text-[11px] uppercase tracking-wide text-slate-500">
                    Preliminary interpretation
                  </p>
                  <p className="mt-1 break-words text-xs text-slate-300">
                    {event.interpretation.summary}
                  </p>
                  <p className="mt-1 break-words text-xs text-amber-300/90">
                    {event.interpretation.note}
                  </p>
                  {event.interpretation.change_preview && (
                    <FeedbackChangePreviewSection
                      changePreview={event.interpretation.change_preview}
                    />
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ResultGroupHeader({
  id,
  title,
  description,
}: {
  id?: string;
  title: string;
  description: string;
}) {
  return (
    <div id={id} className="mt-2 scroll-mt-6 border-b border-white/10 pb-2">
      <p className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-300/80">
        {title}
      </p>
      <p className="mt-1 text-xs text-slate-400">{description}</p>
    </div>
  );
}

/**
 * A lighter-weight heading than ResultGroupHeader, used to visually split a
 * single ResultGroupHeader group (e.g. "Plan overview") into scannable
 * subgroups without hiding, collapsing, reordering, or removing any section
 * inside it -- purely a heading + spacing treatment (Step 179B).
 */
function PlanOverviewSubheading({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <div className="mt-2 flex flex-col gap-0.5 border-l-2 border-cyan-300/20 pl-3">
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
        {title}
      </p>
      <p className="text-xs text-slate-500">{description}</p>
    </div>
  );
}

// Step 182C: User Mode's jump links only ever point at ids that actually
// render while User Mode is active (see the "summary"/"where-to-stay"/
// "flights"/"feedback" wrapper ids added around existing components, plus
// the always-rendered "travel-context"/"draft-itinerary" group headers).
const USER_MODE_JUMP_LINKS: { id: string; label: string }[] = [
  { id: "summary", label: "Summary" },
  { id: "travel-context", label: "Context" },
  { id: "draft-itinerary", label: "Itinerary" },
  { id: "where-to-stay", label: "Where to stay" },
  { id: "flights", label: "Flights" },
  { id: "feedback", label: "Feedback" },
];

// Developer Mode keeps every id the original jump-link list already used,
// plus anchors onto the validation/provider-coverage/inventories/
// regeneration reports that Developer Mode's own "View details" links
// used to be the only way to reach.
const DEVELOPER_MODE_JUMP_LINKS: { id: string; label: string }[] = [
  { id: "plan-overview", label: "Plan overview" },
  { id: "travel-context", label: "Travel context" },
  { id: "draft-itinerary", label: "Draft itinerary" },
  { id: "review-required", label: "Review required" },
  { id: "data-sources", label: "Data sources" },
  { id: "validation", label: "Validation" },
  { id: "provider-coverage", label: "Provider coverage" },
  { id: "inventories", label: "Inventories" },
  { id: "itinerary-narrative", label: "Narrative (AI)" },
  { id: "regeneration-readiness", label: "Regeneration" },
];

function ResultJumpLinks({ mode }: { mode: "user" | "developer" }) {
  const links =
    mode === "developer" ? DEVELOPER_MODE_JUMP_LINKS : USER_MODE_JUMP_LINKS;

  return (
    <nav
      aria-label="Jump to result section"
      className="rounded-2xl border border-white/10 bg-white/5 p-4"
    >
      <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
        Jump to a broad section
      </p>
      <ul className="mt-2 flex flex-wrap gap-2">
        {links.map((link) => (
          <li key={link.id}>
            <a
              href={`#${link.id}`}
              className="inline-block rounded-full border border-white/10 bg-slate-900/60 px-3 py-1 text-xs text-cyan-200 hover:border-cyan-300/40 hover:text-cyan-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/50"
            >
              {link.label}
            </a>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-[11px] text-slate-500">
        {mode === "developer"
          ? "This view exposes backend PlanningState diagnostics, provider coverage, validation, regeneration, and source details."
          : "Switch to Developer view above for full validation, provider-coverage, and regeneration diagnostics."}
      </p>
    </nav>
  );
}

// Step 182C: visible near the top of a generated result. Two plain
// buttons, no auth/role gating -- this only ever changes which already-
// fetched `PlanningState` sections are shown, never what data exists.
function ModeToggle({
  mode,
  onModeChange,
}: {
  mode: "user" | "developer";
  onModeChange: (mode: "user" | "developer") => void;
}) {
  const baseClassName =
    "rounded-full border px-3 py-1.5 text-xs font-semibold transition-colors " +
    FOCUS_RING_CLASSNAME;
  const activeClassName = "border-cyan-300/60 bg-cyan-300/20 text-cyan-50";
  const inactiveClassName =
    "border-white/10 bg-slate-900/60 text-slate-300 hover:border-cyan-300/30 hover:text-cyan-100";

  return (
    <div
      role="group"
      aria-label="Result view mode"
      className="flex flex-wrap items-center gap-2"
    >
      <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
        View
      </span>
      <button
        type="button"
        aria-pressed={mode === "user"}
        onClick={() => onModeChange("user")}
        className={`${baseClassName} ${mode === "user" ? activeClassName : inactiveClassName}`}
      >
        Traveler view
      </button>
      <button
        type="button"
        aria-pressed={mode === "developer"}
        onClick={() => onModeChange("developer")}
        className={`${baseClassName} ${mode === "developer" ? activeClassName : inactiveClassName}`}
      >
        Developer view
      </button>
    </div>
  );
}

// Step 182C: User Mode's single, short readiness banner. It only ever
// restates `validation_status` (a real backend field) in plain language
// and points to Developer Mode for detail -- it never claims the plan is
// booking-ready, final, complete, guaranteed, verified, or "ready for
// real-world use without further review."
function UserModeReadinessBanner({
  validationStatus,
}: {
  validationStatus: string | null;
}) {
  const status = validationStatus ?? "unknown";
  const toneClassName =
    status === "ready"
      ? "border-emerald-300/30 bg-emerald-400/10 text-emerald-100"
      : status === "blocked"
        ? "border-red-400/30 bg-red-400/10 text-red-100"
        : "border-amber-300/30 bg-amber-400/10 text-amber-100";
  const message =
    status === "ready"
      ? "This draft passed automated validation checks — still review it yourself before relying on it."
      : status === "blocked"
        ? "This draft is blocked on required checks and needs review before it's usable."
        : "Use as a planning draft — some checks still need review.";

  return (
    <div className={`rounded-2xl border p-4 text-sm ${toneClassName}`}>
      <p className="leading-6">{message}</p>
      <p className="mt-1 text-[11px] opacity-80">
        Switch to Developer view above for the full validation report and
        provider details.
      </p>
    </div>
  );
}

// Step 182C: concise, User-Mode-only flight summary. Reads the same
// already-fetched `FlightInventoryReport` as the full `FlightInventorySection`
// (Developer Mode only) -- no new API call -- but shows only a status line
// and offer count, never re-deriving or inventing offer details, prices,
// or booking links.
// Step 182D: concise, one-line-per-fact flight offer card for Traveler
// view's trip-level Flights section -- shows only the first outbound
// segment, total price, and the same booking-link/provenance badges the
// full FlightInventorySection (Developer view) uses; never the return
// segments, baggage policy, or cancellation policy detail that section
// shows. No field here is invented -- every value is read straight off
// the same already-fetched `FlightOffer` Developer view renders in full.
function TravelerFlightOfferCard({ offer }: { offer: FlightOffer }) {
  const firstOutboundSegment = offer.outbound_segments[0];
  return (
    <li className="rounded-lg border border-white/10 bg-slate-900/60 p-3 text-sm">
      {firstOutboundSegment && <FlightSegmentSummary segment={firstOutboundSegment} />}
      {offer.total_price_amount !== null && offer.currency && (
        <p className="mt-1 text-xs text-slate-300">
          {offer.total_price_amount} {offer.currency}
        </p>
      )}
      {offer.booking_url && (
        <p className="mt-1 text-[11px] uppercase tracking-wide text-slate-500">
          Provider-supplied booking link -- not a booking confirmation
        </p>
      )}
      {offer.scraped_provenance && (
        <ProvenanceBadge
          provenance={offer.scraped_provenance}
          notVerifiedFor="schedule, price, availability, baggage-policy, or booking-link accuracy"
        />
      )}
      {!offer.scraped_provenance && isKiwiMcpFlightOffer(offer) && <KiwiMcpOfferBadge />}
    </li>
  );
}

// Step 182F: optional, read-only LLM itinerary narrator. This section
// (and `DailyNarrativeNote` below) render backend-generated presentation
// prose ONLY when `itineraryNarrativeReport.status === "success"` --
// disabled (the default), not_connected, unavailable, or failed all
// render nothing here, in either mode, so Traveler view is never
// cluttered with a "not enabled" message and never shows a scary error.
// A failed/not_connected reason is only ever surfaced in Developer view,
// via `ItineraryNarrativeDiagnosticSection` below. The narrative text
// itself is never treated as a new travel fact -- it is prose over
// fields the rest of the page already renders from PlanningState
// directly.
function ItineraryNarrativeSummarySection({
  report,
}: {
  report: ItineraryNarrativeReport | null;
}) {
  if (!report || report.status !== "success" || !report.summary) {
    return null;
  }

  return (
    <div className="rounded-2xl border border-cyan-300/20 bg-cyan-300/10 p-5">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-cyan-200">
        Trip summary
      </p>
      <p className="mt-2 break-words text-sm leading-6 text-cyan-50">{report.summary}</p>
      <p className="mt-2 text-[11px] text-cyan-100/70">
        AI-written summary of the plan below -- not a new fact, and not a
        claim that anything is booked or finalized.
      </p>
    </div>
  );
}

function _findDailyNarrative(
  report: ItineraryNarrativeReport | null,
  dayNumber: number,
): ItineraryNarrativeDayOutput | null {
  if (!report || report.status !== "success") return null;
  return report.daily_narratives.find((day) => day.day_number === dayNumber) ?? null;
}

// Renders one day's narrator prose inside its day card, if present --
// `null` (renders nothing) whenever the narrator is disabled/not
// connected/failed, or simply didn't return a narrative for this
// specific day (e.g. it was outside the truncated window). Caveats are
// preserved and shown alongside the prose, never dropped silently.
function DailyNarrativeNote({
  report,
  dayNumber,
}: {
  report: ItineraryNarrativeReport | null;
  dayNumber: number;
}) {
  const daily = _findDailyNarrative(report, dayNumber);
  if (!daily) return null;

  return (
    <div className="mt-2 rounded-lg border border-cyan-300/20 bg-cyan-300/5 p-3">
      <p className="text-xs font-semibold text-cyan-100">{daily.title}</p>
      <p className="mt-1 break-words text-sm leading-6 text-cyan-50/90">{daily.narrative}</p>
      {daily.caveats.length > 0 && (
        <ul className="mt-2 flex flex-col gap-1">
          {daily.caveats.map((caveat, index) => (
            <li key={index} className="break-words text-[11px] text-amber-300/90">
              {caveat}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// Step 182F: Developer-view-only diagnostic section. Makes explicit that
// this is presentation prose only, never provider data -- shows the
// exact status/provider/model/reason and which allow-listed
// PlanningState fields were used, mirroring how every other
// Developer-view report in this app discloses its own status/source.
function ItineraryNarrativeDiagnosticSection({
  report,
}: {
  report: ItineraryNarrativeReport | null;
}) {
  const status = report?.status ?? "not_connected";

  return (
    <div id="itinerary-narrative" className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h2 className="text-lg font-semibold">Itinerary narrative (AI)</h2>
      <DisclaimerNote tone="amber" spacingClassName="mt-2">
        Presentation prose only, generated from this plan&apos;s own
        already-computed data. It is never treated as provider data and
        never affects validation, provider coverage, or regeneration.
      </DisclaimerNote>
      <p className="mt-3 text-sm text-slate-200">
        Status: <span className="font-semibold">{status}</span>
        {report?.provider ? ` · Provider: ${report.provider}` : ""}
        {report?.model ? ` · Model: ${report.model}` : ""}
      </p>
      {report?.message && (
        <p className="mt-1 text-xs text-slate-400">{report.message}</p>
      )}
      {report?.source_fields_used && report.source_fields_used.length > 0 && (
        <div className="mt-3">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
            PlanningState fields used
          </p>
          <p className="mt-1 break-words text-xs text-slate-400">
            {report.source_fields_used.join(", ")}
          </p>
        </div>
      )}
      <SummaryList title="Assumptions" items={report?.assumptions ?? []} />
      <SummaryList title="Warnings" items={report?.warnings ?? []} />
      {report?.status === "success" && report.daily_narratives.length > 0 && (
        <p className="mt-3 text-xs text-slate-500">
          {report.daily_narratives.length} day narrative(s) generated -- shown inline in the
          day-wise itinerary above in both views.
        </p>
      )}
    </div>
  );
}

const TRAVELER_FLIGHTS_MAX_CARDS = 5;

function UserModeFlightSummary({
  report,
}: {
  report: FlightInventoryReport | null;
}) {
  const status = report?.status ?? "not_connected";
  const offers = report?.offers ?? [];
  const hasOffers = status === "success" && offers.length > 0;
  // Step 182G: a real, connected/enabled flight provider that genuinely
  // found nothing (or errored) for this route/date is a different, more
  // accurate message than "not connected at all" -- confirmed live
  // against a real Kiwi MCP call that returned zero offers for a real
  // search, where the old copy read as if no provider were configured.
  const noOfferMessage =
    status === "unavailable"
      ? "No flight offers were found for this trip's route and dates."
      : status === "failed"
        ? "Flight search is temporarily unavailable -- see Developer view for details."
        : "Connect or enable a flight provider to show flight options for this trip.";

  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h2 className="text-lg font-semibold">Flights</h2>
      {hasOffers ? (
        <>
          <DisclaimerNote tone="amber" spacingClassName="mt-2">
            Concise flight offer summary -- not a booking. See Developer
            view for full schedule, baggage, and cancellation details.
          </DisclaimerNote>
          <ul className="mt-3 flex flex-col gap-2">
            {offers
              .slice(0, TRAVELER_FLIGHTS_MAX_CARDS)
              .map((offer, index) => (
                <TravelerFlightOfferCard key={`${offer.offer_id}-${index}`} offer={offer} />
              ))}
          </ul>
        </>
      ) : (
        <DisclaimerNote tone="slate" spacingClassName="mt-2">
          {noOfferMessage}
        </DisclaimerNote>
      )}
    </div>
  );
}

/**
 * Fetches the same five endpoints for a given trip_id and assembles them
 * into a PlanResult, regardless of whether the trip was just generated by
 * this session or is an existing trip being reloaded from persisted
 * backend state. Throws ApiRequestError (unknown trip_id, or -- via the
 * explicit check below -- a trip that exists but has no generated plan
 * yet) so callers can render the same error handling either way.
 */
async function loadPlanResult(tripId: string): Promise<PlanResult> {
  const summary = await getTripSummary(tripId);

  if (
    !summary.destination_context_generated ||
    !summary.experience_plan_generated ||
    !summary.validation_report_generated
  ) {
    throw new ApiRequestError(
      `Trip '${tripId}' exists, but its plan has not been generated yet. ` +
        "Generate the plan first, then load this trip again.",
      409,
    );
  }

  const [
    destinationContext,
    experiencePlan,
    validationReport,
    providerCoverage,
    trip,
    regenerationReadiness,
    regenerationAttempts,
    aiCandidateReview,
  ] = await Promise.all([
    getDestinationContext(tripId),
    getExperiencePlan(tripId),
    getValidationReport(tripId),
    getProviderCoverage(tripId),
    getTrip(tripId),
    getRegenerationReadiness(tripId),
    getRegenerationAttempts(tripId),
    getAiCandidateReview(tripId),
  ]);

  return {
    summary,
    candidatePois: destinationContext.destination_context.candidate_pois,
    candidateRestaurants:
      destinationContext.destination_context.candidate_restaurants,
    candidateAccommodationPois:
      destinationContext.destination_context.candidate_accommodation_pois,
    dailyPlans: experiencePlan.experience_plan.daily_plans,
    stayAreaGuidance: experiencePlan.experience_plan.stay_area_guidance,
    decisionSummary: experiencePlan.experience_plan.decision_summary,
    implementationGaps: experiencePlan.experience_plan.implementation_gaps,
    readinessChecklist: experiencePlan.experience_plan.readiness_checklist,
    routeFeasibilityContext: experiencePlan.experience_plan.route_feasibility_context,
    weatherContext: destinationContext.weather_context,
    holidayContext: destinationContext.holiday_context,
    currencyContext: destinationContext.currency_context,
    validationReport: validationReport.validation_report,
    providerCoverage,
    destinationAssumptions: destinationContext.destination_context.assumptions,
    destinationConfidence: destinationContext.destination_context.confidence,
    experienceAssumptions: experiencePlan.experience_plan.assumptions,
    experienceConfidence: experiencePlan.experience_plan.confidence,
    feedbackHistory: trip.planning_state.feedback_history,
    pendingFeedbackSummary: trip.planning_state.pending_feedback_summary,
    userLocks: trip.planning_state.user_locks,
    versionHistory: trip.planning_state.version_history,
    planDiffPreview: trip.planning_state.plan_diff_preview,
    regenerationReadiness: regenerationReadiness.regeneration_readiness,
    regenerationAttempts: regenerationAttempts.regeneration_attempts,
    accommodationInventoryReport:
      trip.planning_state.accommodation_inventory_report,
    flightInventoryReport: trip.planning_state.flight_inventory_report,
    aiCandidateReviewReport: aiCandidateReview.ai_candidate_review_report,
    aiCandidatePromotionReport:
      trip.planning_state.ai_candidate_promotion_report,
    routeAwareSequencingReport:
      trip.planning_state.route_aware_sequencing_report,
    routeFeasibilityReport: trip.planning_state.route_feasibility_report,
    travelTimeBufferReport: trip.planning_state.travel_time_buffer_report,
    itineraryNarrativeReport: trip.planning_state.itinerary_narrative_report,
  };
}

/**
 * Finds the currently active "experience" lock for a given experience_id,
 * if one exists. A locked experience can only ever have one active lock at
 * a time (the backend's add_lock is a no-op against an existing active
 * lock), so this returns at most one match.
 */
function findActiveLockForExperience(
  userLocks: UserLock[],
  experienceId: string,
): UserLock | null {
  return (
    userLocks.find(
      (lock) =>
        lock.is_active &&
        lock.locked_item_type === "experience" &&
        lock.locked_item_id === experienceId,
    ) ?? null
  );
}

/** All currently active (not removed) locks, in stored order. */
function activeUserLocks(userLocks: UserLock[]): UserLock[] {
  return userLocks.filter((lock) => lock.is_active);
}

/**
 * Looks up a scheduled experience by ID across every day of the current
 * `dailyPlans`, purely so the locked-items summary can show a human-readable
 * name instead of a bare ID. Returns null (rather than a fabricated name) if
 * the experience isn't found in the current plan.
 */
function findExperienceById(
  dailyPlans: DailyPlan[],
  experienceId: string,
): ExperienceItem | null {
  for (const day of dailyPlans) {
    const match = day.experiences.find(
      (experience) => experience.experience_id === experienceId,
    );
    if (match) return match;
  }
  return null;
}

type LockActionState = {
  isSubmitting: boolean;
  successMessage: string | null;
  errorMessage: string | null;
};

/**
 * Plan-level summary of active "keep this place" markers (Step 129). Purely
 * a readout of `PlanningState.user_locks` plus a Remove keep action that
 * reuses the same `deleteTripLock` call as `ScheduledExperienceCard`.
 * Removing a lock here updates the same `result.userLocks` state the cards
 * read from (via `onLockChange`), so both this summary and the matching
 * card stay in sync automatically. Like the per-card actions, this never
 * regenerates or claims to change the itinerary, validation readiness,
 * provider coverage, or route feasibility -- it only stores/clears a
 * future-regeneration instruction.
 */
function LockedItemsSummarySection({
  tripId,
  userLocks,
  dailyPlans,
  onLockChange,
}: {
  tripId: string;
  userLocks: UserLock[];
  dailyPlans: DailyPlan[];
  onLockChange: (
    userLocks: UserLock[],
    planDiffPreview: PlanDiffPreview,
    regenerationReadiness: RegenerationReadiness,
  ) => void;
}) {
  const [actionState, setActionState] = useState<
    Record<string, LockActionState>
  >({});

  const locks = activeUserLocks(userLocks);

  async function handleRemoveKeep(lockId: string) {
    setActionState((previous) => ({
      ...previous,
      [lockId]: {
        isSubmitting: true,
        successMessage: null,
        errorMessage: null,
      },
    }));
    try {
      const tripData = await deleteTripLock(tripId, lockId);
      onLockChange(
        tripData.planning_state.user_locks,
        tripData.planning_state.plan_diff_preview,
        tripData.planning_state.regeneration_readiness,
      );
      setActionState((previous) => ({
        ...previous,
        [lockId]: {
          isSubmitting: false,
          successMessage: "Keep marker removed.",
          errorMessage: null,
        },
      }));
    } catch (err) {
      setActionState((previous) => ({
        ...previous,
        [lockId]: {
          isSubmitting: false,
          successMessage: null,
          errorMessage:
            err instanceof ApiRequestError
              ? err.message
              : "Something went wrong while removing the keep marker.",
        },
      }));
    }
  }

  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h2 className="text-lg font-semibold">Kept for future regeneration</h2>
      <p className="mt-1 text-xs text-amber-300/90">
        These keep markers are stored for future regeneration. They do not
        change the current plan yet.
      </p>

      {locks.length === 0 ? (
        <p className="mt-3 text-sm text-slate-400">
          No places marked to keep yet.
        </p>
      ) : (
        <ul className="mt-3 flex flex-col gap-2">
          {locks.map((lock) => {
            const matchedExperience =
              lock.locked_item_type === "experience"
                ? findExperienceById(dailyPlans, lock.locked_item_id)
                : null;
            const state = actionState[lock.lock_id];

            return (
              <li
                key={lock.lock_id}
                className="rounded-lg border border-white/10 bg-slate-900/60 p-3 text-sm"
              >
                <p className="break-words font-medium text-slate-100">
                  {matchedExperience
                    ? matchedExperience.name
                    : "Matching scheduled experience not found in the current plan."}
                </p>
                <p className="mt-1 break-all text-xs text-slate-400">
                  Type: {lock.locked_item_type} · ID: {lock.locked_item_id}
                </p>
                <p className="mt-1 break-words text-xs text-slate-400">
                  Reason: {lock.reason}
                </p>
                <p className="mt-1 text-[11px] uppercase tracking-wide text-emerald-300/90">
                  Status: active
                </p>
                <p className="mt-1 text-xs text-slate-400">
                  Created: {new Date(lock.created_at).toLocaleString()}
                </p>

                <button
                  type="button"
                  onClick={() => void handleRemoveKeep(lock.lock_id)}
                  disabled={state?.isSubmitting}
                  aria-label={
                    matchedExperience
                      ? `Remove keep marker for ${matchedExperience.name}`
                      : "Remove keep marker"
                  }
                  className={`mt-2 rounded-full border border-white/10 bg-slate-900 px-3 py-1 text-xs font-semibold text-slate-300 transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50 ${FOCUS_RING_CLASSNAME}`}
                >
                  {state?.isSubmitting ? "Removing..." : "Remove keep"}
                </button>

                {state?.errorMessage && (
                  <p className="mt-2 break-words text-xs text-red-300">
                    {state.errorMessage}
                  </p>
                )}
                {state?.successMessage && !state.errorMessage && (
                  <p className="mt-2 break-words text-xs text-emerald-300">
                    {state.successMessage}
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

const TRAVEL_LOADING_STAGES = [
  "Preparing your travel plan",
  "Checking destination data",
  "Building your draft itinerary",
  "Validating available provider data",
  "Almost there",
];

// Step 182C: friendlier, traveler-facing copy for the loading animation,
// keyed strictly off the real backend stage keys the orchestrator reports
// (see `_GENERATION_STAGE_LABELS` in
// backend/app/services/planning_orchestrator.py). This is purely a nicer
// re-wording of a real, already-reported stage -- it never invents a
// stage the backend didn't actually report, and several backend stages
// intentionally share one friendly phrase rather than being split into
// unbacked sub-steps.
const FRIENDLY_STAGE_LABEL_BY_BACKEND_KEY: Record<string, string> = {
  traveler_profile: "Preparing your trip",
  destination_context: "Finding places",
  candidate_quality: "Checking providers",
  ai_candidate_shadow: "Checking providers",
  trip_strategy: "Building daily plan",
  stay_transport: "Checking movement",
  experience_plan: "Building daily plan",
  validation: "Validating draft",
  post_processing: "Finalizing itinerary view",
};

/**
 * Decorative loading animation shown while a new trip is being created and
 * generated (Step 163A, wired to real backend pipeline progress in Step
 * 163C). The plane/progress bar are always decorative -- they never
 * represent a real flight, a real flight route, or a real route/travel
 * time. By default (no backend props, or before a trip_id exists yet)
 * progress and stage copy are driven purely by local timers, since
 * generation may still be running with no signal read yet. Once the
 * caller has a real `GenerationProgress` poll result, it can pass
 * `progressPercent`/`stageLabel`/`progressMessage`/
 * `isRealBackendStageProgress` and this component prefers those values
 * over the local timer loop -- but the "Loading animation only" disclaimer
 * always stays visible either way. It resets and unmounts as soon as
 * `isLoading` goes false, whether that's because the result rendered or
 * an error rendered.
 */
function TravelGenerationLoading({
  originCity,
  destination,
  isLoading,
  progressPercent,
  stageKey,
  stageLabel,
  progressMessage,
  isRealBackendStageProgress,
  isCompleted,
}: {
  originCity?: string;
  destination?: string;
  isLoading: boolean;
  progressPercent?: number;
  stageKey?: string | null;
  stageLabel?: string;
  progressMessage?: string;
  isRealBackendStageProgress?: boolean;
  // Step 182C: true only once the backend itself reports
  // `GenerationProgress.status === "completed"` -- never inferred locally
  // -- so the landing visual is a reaction to a real event, not a guess.
  isCompleted?: boolean;
}) {
  const [localProgress, setLocalProgress] = useState(0);
  const [stageIndex, setStageIndex] = useState(0);

  useEffect(() => {
    if (!isLoading) {
      return;
    }

    const progressTimer = setInterval(() => {
      setLocalProgress((previous) => (previous >= 88 ? 88 : previous + 4));
    }, 500);
    const stageTimer = setInterval(() => {
      setStageIndex(
        (previous) => (previous + 1) % TRAVEL_LOADING_STAGES.length,
      );
    }, 2200);

    return () => {
      clearInterval(progressTimer);
      clearInterval(stageTimer);
    };
  }, [isLoading]);

  if (!isLoading) return null;

  const hasBackendProgress = progressPercent !== undefined;
  const showBackendNote = hasBackendProgress && isRealBackendStageProgress === true;
  const origin = originCity?.trim() || "Origin";
  const dest = destination?.trim() || "Destination";
  const displayProgress = isCompleted
    ? 100
    : hasBackendProgress
      ? progressPercent
      : localProgress;
  const friendlyStageLabel = stageKey
    ? FRIENDLY_STAGE_LABEL_BY_BACKEND_KEY[stageKey]
    : undefined;
  const displayMessage = isCompleted
    ? "Trip generated — preparing your itinerary view"
    : friendlyStageLabel ||
      stageLabel ||
      progressMessage ||
      TRAVEL_LOADING_STAGES[stageIndex];

  return (
    <div
      className={`mt-8 rounded-2xl border p-6 transition-colors duration-500 ${
        isCompleted
          ? "border-emerald-300/30 bg-emerald-400/5"
          : "border-white/10 bg-white/5"
      }`}
    >
      <div className="flex items-center justify-between text-sm font-medium text-slate-300">
        <span>{origin}</span>
        <span>{dest}</span>
      </div>
      <div
        className="relative mt-4 h-1.5 rounded-full bg-slate-800"
        aria-hidden="true"
      >
        <div
          className={`h-1.5 rounded-full transition-[width] duration-500 motion-reduce:transition-none ${
            isCompleted ? "bg-emerald-400/80" : "bg-cyan-400/70"
          }`}
          style={{ width: `${displayProgress}%` }}
        />
        <span
          className={`absolute -top-3 -translate-x-1/2 text-base transition-all duration-500 motion-reduce:transition-none ${
            isCompleted ? "scale-110" : ""
          }`}
          style={{ left: `${displayProgress}%` }}
        >
          {isCompleted ? "🛬" : "✈️"}
        </span>
      </div>
      <p
        className="mt-4 break-words text-sm text-slate-200"
        role="status"
        aria-live="polite"
      >
        {displayMessage}
      </p>
      <p className="mt-2 text-[11px] text-slate-500">
        Loading animation only — not live flight tracking.
      </p>
      {showBackendNote && !isCompleted && (
        <p className="mt-1 text-[11px] text-emerald-300/80">
          Using backend stage progress
        </p>
      )}
    </div>
  );
}

// Step 182C: User Mode / Developer Mode toggle persistence key.
const VIEW_MODE_STORAGE_KEY = "travelobligator.viewMode";

export default function Home() {
  const [form, setForm] = useState<TripRequestInput>(DEFAULT_TRIP_REQUEST);
  const [interestsText, setInterestsText] = useState("");
  const [mustVisitText, setMustVisitText] = useState("");
  const [constraintsText, setConstraintsText] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [backendProgress, setBackendProgress] = useState<GenerationProgress | null>(
    null,
  );
  const [existingTripId, setExistingTripId] = useState("");
  const [isLoadingExisting, setIsLoadingExisting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<PlanResult | null>(null);
  const [feedbackText, setFeedbackText] = useState("");
  const [isSubmittingFeedback, setIsSubmittingFeedback] = useState(false);
  const [feedbackSuccessMessage, setFeedbackSuccessMessage] = useState<
    string | null
  >(null);
  const [feedbackErrorMessage, setFeedbackErrorMessage] = useState<
    string | null
  >(null);

  // Step 184E: full frontend auth. `currentUser` is the sole gate for
  // rendering the trip app shell below -- no anonymous fallback, no fake
  // user, nothing trip-related ever fetched before this is set. The
  // session itself lives only in the backend's signed HttpOnly cookie
  // (see lib/api.ts's `credentials: "include"`); nothing here stores a
  // token.
  const [currentUser, setCurrentUser] = useState<PublicUser | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [authNotConfigured, setAuthNotConfigured] = useState(false);
  const [authMode, setAuthMode] = useState<"login" | "signup">("login");
  const [authEmail, setAuthEmail] = useState("");
  const [authPassword, setAuthPassword] = useState("");
  const [authConfirmPassword, setAuthConfirmPassword] = useState("");
  const [authError, setAuthError] = useState<string | null>(null);
  const [isAuthSubmitting, setIsAuthSubmitting] = useState(false);

  const [myTrips, setMyTrips] = useState<TripListItem[]>([]);
  const [myTripsLoading, setMyTripsLoading] = useState(false);
  const [myTripsError, setMyTripsError] = useState<string | null>(null);
  // Step 184F: which "My trips" row is currently being loaded, if any --
  // lets that one row say "Loading…" instead of every row just going
  // uniformly disabled with no indication of which one was clicked.
  const [loadingTripId, setLoadingTripId] = useState<string | null>(null);

  const refreshMyTrips = useCallback(async () => {
    setMyTripsLoading(true);
    setMyTripsError(null);
    try {
      const data = await listTrips();
      setMyTrips(data.trips);
    } catch (err) {
      setMyTripsError(
        err instanceof ApiRequestError
          ? err.message
          : "Could not load your trips.",
      );
    } finally {
      setMyTripsLoading(false);
    }
  }, []);

  const checkAuth = useCallback(async () => {
    setAuthLoading(true);
    setAuthNotConfigured(false);
    try {
      const data = await getCurrentUser();
      setCurrentUser(data.user);
      void refreshMyTrips();
    } catch (err) {
      if (err instanceof ApiRequestError && err.code === "AUTH_NOT_CONFIGURED") {
        setAuthNotConfigured(true);
      }
      setCurrentUser(null);
    } finally {
      setAuthLoading(false);
    }
  }, [refreshMyTrips]);

  useEffect(() => {
    // `checkAuth`'s identity is stable (it only closes over the stable
    // `refreshMyTrips`), so this effectively still only runs once on
    // mount. The state updates inside `checkAuth` happen asynchronously,
    // after a real network response -- this is the standard mount-time
    // data-fetch pattern, not a synchronous cascading setState.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void checkAuth();
  }, [checkAuth]);

  // Shared by every trip-related action below: a session that expired or
  // was revoked mid-use surfaces here as a normal `ApiRequestError` with
  // `code === "AUTHENTICATION_REQUIRED"` -- when that happens this clears
  // the signed-in state so the login screen reappears, rather than leaving
  // a half-authenticated shell with a trip request that will never
  // succeed. A `FORBIDDEN` (wrong-user trip access) never reveals trip
  // details, just a plain access message.
  function describeTripApiError(err: unknown, fallback: string): string {
    if (err instanceof ApiRequestError) {
      if (err.code === "AUTHENTICATION_REQUIRED") {
        setCurrentUser(null);
        setMyTrips([]);
        setResult(null);
        return "Your session has ended. Please log in again.";
      }
      if (err.code === "FORBIDDEN") {
        return "You do not have access to this trip.";
      }
      return err.message;
    }
    return fallback;
  }

  async function handleAuthSubmit() {
    setAuthError(null);

    const email = authEmail.trim();
    if (!email || !authPassword) {
      setAuthError("Enter your email and password.");
      return;
    }
    if (authMode === "signup" && authPassword !== authConfirmPassword) {
      setAuthError("Passwords do not match.");
      return;
    }

    setIsAuthSubmitting(true);
    try {
      const data =
        authMode === "signup"
          ? await signup(email, authPassword)
          : await login(email, authPassword);
      setCurrentUser(data.user);
      setAuthPassword("");
      setAuthConfirmPassword("");
      void refreshMyTrips();
    } catch (err) {
      if (err instanceof ApiRequestError && err.code === "AUTH_NOT_CONFIGURED") {
        setAuthNotConfigured(true);
      } else {
        setAuthError(
          err instanceof ApiRequestError
            ? err.message
            : "Something went wrong. Please try again.",
        );
      }
    } finally {
      setIsAuthSubmitting(false);
    }
  }

  async function handleLogout() {
    try {
      await logout();
    } catch {
      // Even if the backend call itself fails, the local state below is
      // still cleared -- logout must never leave another user's trip data
      // visible on screen.
    }
    setCurrentUser(null);
    setMyTrips([]);
    setMyTripsError(null);
    setLoadingTripId(null);
    setResult(null);
    setExistingTripId("");
    setError(null);
    resetFeedbackPanelState();
    setAuthMode("login");
    setAuthEmail("");
    setAuthPassword("");
    setAuthConfirmPassword("");
    setAuthError(null);
  }

  async function handleSelectMyTrip(tripId: string) {
    setExistingTripId(tripId);
    setIsLoadingExisting(true);
    setLoadingTripId(tripId);
    setError(null);
    setResult(null);
    resetFeedbackPanelState();

    try {
      setResult(await loadPlanResult(tripId));
    } catch (err) {
      setError(
        describeTripApiError(
          err,
          "Something went wrong while talking to the backend.",
        ),
      );
    } finally {
      setIsLoadingExisting(false);
      setLoadingTripId(null);
    }
  }

  // Step 182C: User Mode / Developer Mode split. The initial value on
  // both the server render and the client's first render is always
  // "user" -- matching exactly -- so there is no hydration mismatch; the
  // real localStorage read happens only inside the effect below, which
  // runs after hydration. If localStorage is unavailable or throws, the
  // catch leaves `mode` at its "user" default, per spec.
  const [mode, setMode] = useState<"user" | "developer">("user");
  const [hasReadStoredMode, setHasReadStoredMode] = useState(false);

  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(VIEW_MODE_STORAGE_KEY);
      if (stored === "user" || stored === "developer") {
        // This is the one deliberate post-hydration sync read from an
        // external system (localStorage) that this state is allowed to
        // diverge from its server-matching "user" default for -- not a
        // React-state-derived update, so the cascading-render concern the
        // set-state-in-effect rule normally warns about doesn't apply.
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setMode(stored);
      }
    } catch {
      // localStorage unavailable -- keep the "user" default.
    } finally {
      setHasReadStoredMode(true);
    }
  }, []);

  useEffect(() => {
    if (!hasReadStoredMode) return;
    try {
      window.localStorage.setItem(VIEW_MODE_STORAGE_KEY, mode);
    } catch {
      // localStorage unavailable -- mode still works for this page load,
      // it just won't persist across reloads.
    }
  }, [mode, hasReadStoredMode]);

  function resetFeedbackPanelState() {
    setFeedbackText("");
    setFeedbackSuccessMessage(null);
    setFeedbackErrorMessage(null);
  }

  async function handleSubmitFeedback() {
    if (!result) return;

    const trimmedFeedback = feedbackText.trim();
    setFeedbackSuccessMessage(null);
    setFeedbackErrorMessage(null);

    if (!trimmedFeedback) {
      setFeedbackErrorMessage("Feedback cannot be blank.");
      return;
    }

    setIsSubmittingFeedback(true);
    try {
      const tripData = await submitTripFeedback(
        result.summary.trip_id,
        trimmedFeedback,
      );
      setResult((previous) =>
        previous
          ? {
              ...previous,
              feedbackHistory: tripData.planning_state.feedback_history,
              pendingFeedbackSummary:
                tripData.planning_state.pending_feedback_summary,
              planDiffPreview: tripData.planning_state.plan_diff_preview,
              regenerationReadiness:
                tripData.planning_state.regeneration_readiness,
            }
          : previous,
      );
      setFeedbackText("");
      setFeedbackSuccessMessage(
        "Feedback saved. Regeneration is not implemented yet.",
      );
    } catch (err) {
      setFeedbackErrorMessage(
        describeTripApiError(
          err,
          "Something went wrong while saving feedback.",
        ),
      );
    } finally {
      setIsSubmittingFeedback(false);
    }
  }

  async function handlePlanTrip() {
    setIsLoading(true);
    setError(null);
    setResult(null);
    setBackendProgress(null);
    resetFeedbackPanelState();

    // Real backend pipeline stage-progress polling (Step 163C). This is
    // additive to the decorative animation, never a replacement for
    // generation itself: exactly one POST /generate call still happens
    // below, and loadPlanResult still runs exactly once at the end.
    let pollTimer: ReturnType<typeof setInterval> | null = null;
    let pollingStopped = false;

    function stopPolling() {
      pollingStopped = true;
      if (pollTimer !== null) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    }

    try {
      const requestBody: TripRequestInput = {
        ...form,
        interests: parseCommaList(interestsText),
        must_visit: parseCommaList(mustVisitText),
        constraints: parseCommaList(constraintsText),
      };
      const { trip_id: tripId } = await createTrip(requestBody);

      // Poll while POST /generate (below) is in flight. A transient poll
      // failure never fails generation or surfaces an error -- it's
      // silently ignored so the loading animation just keeps running,
      // decoratively, until the next successful poll or completion.
      pollTimer = setInterval(() => {
        void getGenerationProgress(tripId)
          .then((data) => {
            if (!pollingStopped) {
              setBackendProgress(data.generation_progress);
            }
          })
          .catch(() => {
            // Ignore transient polling failures.
          });
      }, 700);

      await generatePlan(tripId);
      stopPolling();

      // Briefly show the completed/100% backend state before switching to
      // the rendered result.
      try {
        const finalProgress = await getGenerationProgress(tripId);
        setBackendProgress(finalProgress.generation_progress);
      } catch {
        // Non-critical: still render the result even if this last read fails.
      }
      await new Promise((resolve) => setTimeout(resolve, 500));

      setResult(await loadPlanResult(tripId));
      void refreshMyTrips();
    } catch (err) {
      setError(
        describeTripApiError(
          err,
          "Something went wrong while talking to the backend.",
        ),
      );
    } finally {
      stopPolling();
      setIsLoading(false);
      setBackendProgress(null);
    }
  }

  async function handleLoadExistingTrip() {
    const tripId = existingTripId.trim();
    if (!tripId) {
      setError("Enter a trip_id to load.");
      return;
    }

    setIsLoadingExisting(true);
    setError(null);
    setResult(null);
    resetFeedbackPanelState();

    try {
      setResult(await loadPlanResult(tripId));
    } catch (err) {
      setError(
        describeTripApiError(
          err,
          "Something went wrong while talking to the backend.",
        ),
      );
    } finally {
      setIsLoadingExisting(false);
    }
  }

  // Step 182D: the day-wise itinerary card list, shared between Developer
  // view (unchanged from before 182D) and Traveler view (cleaned up per
  // spec: no per-day accommodation POIs, no repeated per-leg "movement
  // unavailable" rows, lighter restaurant framing, a clearer empty-day
  // message, and the more technical route-aware-order label/disclaimer
  // hidden). Computed once here (rather than duplicated per mode branch
  // below) so both branches render the exact same day-card logic; `mode`
  // only ever changes what's shown, never the underlying data read.
  const dayWiseItinerarySection = result ? (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h2 className="text-lg font-semibold">Day-wise experiences</h2>
      <p className="mt-1 text-xs text-slate-500">
        Map links open the scheduled place coordinates only. They are
        not route, travel-time, or booking links.
      </p>
      <p className="mt-1 text-xs text-amber-300/90">
        Keep markers are stored for future regeneration. They do not
        change the current plan.
      </p>
      {mode === "developer" && (
        <p className="mt-1 text-xs text-slate-500">
          Route-aware sequencing uses provider-backed movement data
          when available. If unavailable, the itinerary keeps a
          fallback order.
        </p>
      )}
      {movementDataIsUnavailable(result.routeFeasibilityReport) && (
        <p className="mt-1 text-xs text-amber-300/90">
          Movement data unavailable for this trip -- no connected
          routing provider could supply real distances or travel
          times between stops.
        </p>
      )}
      {result.dailyPlans.length === 0 && (
        <p className="mt-2 text-sm text-slate-400">
          No daily plans returned yet.
        </p>
      )}
      <div className="mt-3 flex flex-col gap-4">
        {result.dailyPlans.map((day) => (
          <div
            key={day.day_plan_id}
            className="rounded-xl border border-white/10 bg-slate-900/60 p-4"
          >
            <p className="font-semibold">
              Day {day.day_number} · {day.date}
            </p>
            <DailyNarrativeNote
              report={result.itineraryNarrativeReport}
              dayNumber={day.day_number}
            />
            {day.experiences.length === 0 ? (
              mode === "developer" ? (
                <p className="mt-1 text-sm text-slate-400">
                  No experiences scheduled for this day.
                </p>
              ) : (
                <>
                  <p className="mt-1 text-sm text-slate-400">
                    No strong provider-backed places were scheduled for
                    this day.
                  </p>
                  {day.warnings.map((warning) => (
                    <p
                      key={warning}
                      className="mt-2 break-words text-xs text-amber-300/90"
                    >
                      {warning}
                    </p>
                  ))}
                </>
              )
            ) : (
              <>
                {mode === "developer" && (
                  <p className="mt-1 text-[11px] uppercase tracking-wide text-slate-500">
                    {routeAwareDayStatusLabel(
                      day,
                      result.routeAwareSequencingReport,
                    )}
                  </p>
                )}
                <ul className="mt-2 flex flex-col gap-2">
                  {/* Step 172E: the last stop in a day (or the
                      only stop in a single-stop day) has no
                      `nextExperience`, so `movement` stays `null`
                      and no row is rendered after it -- a
                      single-stop day never shows a movement row
                      at all. */}
                  {day.experiences.map((experience, index) => {
                    const nextExperience = day.experiences[index + 1];
                    const movement = nextExperience
                      ? findMovementBetweenStops(
                          experience.experience_id,
                          nextExperience.experience_id,
                          result.travelTimeBufferReport,
                        )
                      : null;
                    // Step 182D: Traveler view only ever shows a movement
                    // row when it carries a real, successful duration --
                    // never the repeated "Movement data unavailable"/"No
                    // movement details returned"/"Route details need
                    // review" rows Developer view still shows per leg.
                    // The trip-level note above already covers the
                    // "unavailable for this trip" case once, concisely.
                    const showMovementRow =
                      mode === "developer"
                        ? shouldRenderMovementRow(movement)
                        : movement !== null && movement.status === "success";
                    return (
                      <Fragment key={experience.experience_id}>
                        <ScheduledExperienceCard
                          experience={experience}
                          orderNumber={experience.stop_order ?? index + 1}
                          tripId={result.summary.trip_id}
                          activeLock={findActiveLockForExperience(
                            result.userLocks,
                            experience.experience_id,
                          )}
                          onLockChange={(
                            userLocks,
                            planDiffPreview,
                            regenerationReadiness,
                          ) =>
                            setResult((previous) =>
                              previous
                                ? {
                                    ...previous,
                                    userLocks,
                                    planDiffPreview,
                                    regenerationReadiness,
                                  }
                                : previous,
                            )
                          }
                        />
                        {showMovementRow && movement && (
                          <MovementRow buffer={movement} />
                        )}
                      </Fragment>
                    );
                  })}
                </ul>
                {mode === "developer" && (
                  <p className="mt-2 text-xs text-slate-500">
                    Scheduled place cards use backend-returned
                    provider-backed fields only. They do not include
                    ratings, prices, or opening hours yet. Movement
                    rows between stops only ever show provider-backed
                    duration/distance when the backend already has
                    it -- never an invented or frontend-computed
                    figure. Stop numbers reflect the backend&apos;s
                    current schedule order only -- not a claim about
                    route certainty, safety, or speed.
                  </p>
                )}
              </>
            )}
            <DayMapPreview
              experiences={day.experiences}
              travelTimeBufferReport={result.travelTimeBufferReport}
            />
            {day.restaurant_suggestions.length > 0 && (
              <div className="mt-3">
                <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                  {mode === "developer"
                    ? "Nearby restaurant suggestions"
                    : "Nearby food ideas"}
                </p>
                {mode === "developer" ? (
                  <p className="mt-1 text-xs text-amber-300/90">
                    Restaurant suggestions are provider-backed location
                    candidates only. They are not reservations,
                    ratings, prices, opening-hours checks, or route
                    recommendations.
                  </p>
                ) : (
                  <p className="mt-1 text-xs text-amber-300/90">
                    Nearby food ideas only -- not reservations, ratings,
                    or recommendations.
                  </p>
                )}
                <ul className="mt-2 flex flex-col gap-2">
                  {day.restaurant_suggestions.map((restaurant, index) => (
                    <RestaurantSuggestionCard
                      key={`${restaurant.name}-${index}`}
                      restaurant={restaurant}
                    />
                  ))}
                </ul>
              </div>
            )}
            {mode === "developer" && day.accommodation_suggestions.length > 0 && (
              <div className="mt-3">
                <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                  Nearby accommodation POI suggestions
                </p>
                <p className="mt-1 text-xs text-amber-300/90">
                  Open-data location candidates only, not bookable
                  inventory.
                </p>
                <p className="mt-1 text-xs text-amber-300/90">
                  Accommodation POI suggestions are open-data
                  location candidates only. They are not hotel
                  prices, availability, ratings, booking links, or
                  final stay recommendations.
                </p>
                <ul className="mt-2 flex flex-col gap-2">
                  {day.accommodation_suggestions.map((accommodation, index) => (
                    <AccommodationSuggestionCard
                      key={`${accommodation.name}-${index}`}
                      accommodation={accommodation}
                    />
                  ))}
                </ul>
              </div>
            )}
            {mode === "developer" &&
              day.warnings.map((warning) => (
                <p
                  key={warning}
                  className="mt-2 break-words text-xs text-amber-300/90"
                >
                  {warning}
                </p>
              ))}
          </div>
        ))}
      </div>
    </div>
  ) : null;

  // Step 184E: auth gating. Nothing trip-related is ever rendered or
  // fetched until `currentUser` is set below -- no anonymous fallback.
  if (authLoading) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-slate-950 px-6 py-12 text-slate-100">
        <p className="text-sm text-slate-400">Checking your sign-in status…</p>
      </main>
    );
  }

  if (authNotConfigured) {
    return (
      <main className="min-h-screen bg-slate-950 px-6 py-12 text-slate-100">
        <section className="mx-auto max-w-lg rounded-3xl border border-amber-400/30 bg-amber-400/5 p-6 shadow-2xl sm:p-8">
          <p className="text-sm font-semibold uppercase tracking-[0.3em] text-amber-200">
            TravelObligator
          </p>
          <h1 className="mt-4 text-xl font-semibold text-amber-100">
            Authentication is not configured on this server
          </h1>
          <p className="mt-3 text-sm leading-6 text-slate-300">
            This backend has no <code className="text-amber-200">SESSION_SECRET_KEY</code>{" "}
            set, so sign-in cannot work yet. For local development, generate
            one and add it only to your backend&apos;s real, local{" "}
            <code className="text-amber-200">.env</code> file (never commit
            it), then restart the backend:
          </p>
          <pre className="mt-3 overflow-x-auto rounded-lg border border-white/10 bg-slate-900 p-3 text-xs text-slate-300">
{`python - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY`}
          </pre>
          <p className="mt-2 text-sm text-slate-300">
            Then add{" "}
            <code className="text-amber-200">
              SESSION_SECRET_KEY=&lt;generated_value&gt;
            </code>{" "}
            to your local <code className="text-amber-200">.env</code> only.
          </p>
          <button
            type="button"
            onClick={() => void checkAuth()}
            className={`mt-5 rounded-lg border border-white/10 bg-slate-900 px-4 py-2 text-sm font-semibold text-slate-200 transition hover:bg-slate-800 ${FOCUS_RING_CLASSNAME}`}
          >
            Retry
          </button>
        </section>
      </main>
    );
  }

  if (!currentUser) {
    // Step 184F: a dedicated, field-level message for the one client-side
    // validation this screen does itself (everything else is the
    // backend's own error text, shown verbatim in the generic error box
    // below) -- lets the confirm-password input point `aria-describedby`
    // at it instead of a rose-colored one-line box floating at the bottom.
    const passwordMismatch =
      authMode === "signup" && authError === "Passwords do not match.";

    return (
      <main className="flex min-h-screen items-center justify-center bg-slate-950 px-6 py-12 text-slate-100">
        <section className="w-full max-w-md rounded-3xl border border-white/10 bg-white/5 p-6 shadow-2xl sm:p-8">
          <p className="text-sm font-semibold uppercase tracking-[0.3em] text-cyan-200">
            TravelObligator
          </p>
          <h1 className="mt-4 text-2xl font-semibold tracking-tight">
            {authMode === "signup" ? "Create your account" : "Log in to your account"}
          </h1>
          <p className="mt-2 text-sm text-slate-400">
            {authMode === "signup"
              ? "Sign up to create and manage your own trips. Trips are private to your account."
              : "Log in to see your own trips. Trips are private to your account."}
          </p>

          <div
            role="group"
            aria-label="Choose log in or sign up"
            className="mt-6 flex rounded-lg border border-white/10 bg-slate-900 p-1 text-sm"
          >
            <button
              type="button"
              aria-pressed={authMode === "login"}
              disabled={isAuthSubmitting}
              onClick={() => {
                setAuthMode("login");
                setAuthError(null);
              }}
              className={`flex-1 rounded-md px-3 py-1.5 font-semibold transition disabled:cursor-not-allowed disabled:opacity-50 ${FOCUS_RING_CLASSNAME} ${
                authMode === "login"
                  ? "bg-cyan-400 text-slate-950"
                  : "text-slate-300 hover:text-cyan-200"
              }`}
            >
              Log in
            </button>
            <button
              type="button"
              aria-pressed={authMode === "signup"}
              disabled={isAuthSubmitting}
              onClick={() => {
                setAuthMode("signup");
                setAuthError(null);
              }}
              className={`flex-1 rounded-md px-3 py-1.5 font-semibold transition disabled:cursor-not-allowed disabled:opacity-50 ${FOCUS_RING_CLASSNAME} ${
                authMode === "signup"
                  ? "bg-cyan-400 text-slate-950"
                  : "text-slate-300 hover:text-cyan-200"
              }`}
            >
              Sign up
            </button>
          </div>

          <form
            className="mt-6 flex flex-col gap-4"
            aria-busy={isAuthSubmitting}
            onSubmit={(event) => {
              event.preventDefault();
              void handleAuthSubmit();
            }}
          >
            <div className="flex flex-col gap-1">
              <label
                htmlFor="auth-email"
                className="text-sm text-slate-300"
              >
                Email
              </label>
              <input
                id="auth-email"
                type="email"
                name="email"
                autoComplete="email"
                required
                disabled={isAuthSubmitting}
                className={`rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-slate-100 disabled:cursor-not-allowed disabled:opacity-50 ${FOCUS_RING_CLASSNAME}`}
                value={authEmail}
                onChange={(event) => setAuthEmail(event.target.value)}
              />
            </div>

            <div className="flex flex-col gap-1">
              <label
                htmlFor="auth-password"
                className="text-sm text-slate-300"
              >
                Password
              </label>
              <input
                id="auth-password"
                type="password"
                name="password"
                autoComplete={
                  authMode === "signup" ? "new-password" : "current-password"
                }
                required
                minLength={authMode === "signup" ? 8 : undefined}
                disabled={isAuthSubmitting}
                // The hint below is a sibling of this input, outside the
                // <label>, and connected only via aria-describedby -- so a
                // screen reader announces the label as plain "Password",
                // then the hint separately, never "Password At least 8
                // characters." folded into one run-on accessible name.
                aria-describedby={
                  authMode === "signup" ? "password-hint" : undefined
                }
                className={`rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-slate-100 disabled:cursor-not-allowed disabled:opacity-50 ${FOCUS_RING_CLASSNAME}`}
                value={authPassword}
                onChange={(event) => setAuthPassword(event.target.value)}
              />
              {authMode === "signup" && (
                <span id="password-hint" className="text-xs text-slate-500">
                  At least 8 characters.
                </span>
              )}
            </div>

            {authMode === "signup" && (
              <div className="flex flex-col gap-1">
                <label
                  htmlFor="auth-confirm-password"
                  className="text-sm text-slate-300"
                >
                  Confirm password
                </label>
                <input
                  id="auth-confirm-password"
                  type="password"
                  name="confirm-password"
                  autoComplete="new-password"
                  required
                  disabled={isAuthSubmitting}
                  aria-invalid={passwordMismatch}
                  aria-describedby={
                    passwordMismatch ? "confirm-password-error" : undefined
                  }
                  className={`rounded-lg border bg-slate-900 px-3 py-2 text-slate-100 disabled:cursor-not-allowed disabled:opacity-50 ${FOCUS_RING_CLASSNAME} ${
                    passwordMismatch ? "border-rose-400/60" : "border-white/10"
                  }`}
                  value={authConfirmPassword}
                  onChange={(event) =>
                    setAuthConfirmPassword(event.target.value)
                  }
                />
                {passwordMismatch && (
                  <span
                    id="confirm-password-error"
                    role="alert"
                    className="text-xs text-rose-300"
                  >
                    Passwords do not match.
                  </span>
                )}
              </div>
            )}

            {authError && !passwordMismatch && (
              <p
                role="alert"
                aria-live="polite"
                className="rounded-lg border border-rose-400/30 bg-rose-400/10 px-3 py-2 text-sm text-rose-200"
              >
                {authError}
              </p>
            )}

            <button
              type="submit"
              disabled={isAuthSubmitting}
              className={`mt-1 rounded-lg bg-cyan-400 px-4 py-2 font-semibold text-slate-950 transition disabled:cursor-not-allowed disabled:opacity-50 ${FOCUS_RING_CLASSNAME}`}
            >
              {isAuthSubmitting
                ? authMode === "signup"
                  ? "Creating account…"
                  : "Logging in…"
                : authMode === "signup"
                  ? "Create account"
                  : "Log in"}
            </button>
          </form>
        </section>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-slate-950 px-6 py-12 text-slate-100">
      <section className="mx-auto max-w-4xl rounded-3xl border border-white/10 bg-white/5 p-6 shadow-2xl sm:p-8">
        <p className="text-sm font-semibold uppercase tracking-[0.3em] text-cyan-200">
          TravelObligator
        </p>
        <h1 className="mt-4 text-4xl font-semibold tracking-tight sm:text-5xl">
          AI Travel Decision Platform
        </h1>
        <p className="mt-5 max-w-2xl text-base leading-7 text-slate-300">
          Everything below is read directly from the backend PlanningState.
          Nothing here is invented by the frontend.
        </p>

        <div className="mt-6 flex flex-col gap-2 rounded-2xl border border-white/10 bg-white/5 p-4 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-xs text-slate-400">
            Signed in as{" "}
            <span className="text-sm font-semibold text-cyan-200 break-words">
              {currentUser.email}
            </span>
          </p>
          <button
            type="button"
            onClick={() => void handleLogout()}
            className={`self-start rounded-lg border border-white/20 bg-slate-900 px-3 py-1.5 text-sm font-semibold text-slate-200 transition hover:border-cyan-300/40 hover:bg-slate-800 hover:text-cyan-200 sm:self-auto ${FOCUS_RING_CLASSNAME}`}
          >
            Log out
          </button>
        </div>

        <div className="mt-6 rounded-2xl border border-cyan-300/15 bg-cyan-400/[0.03] p-5">
          <div className="flex items-center justify-between">
            <p className="text-xs font-semibold uppercase tracking-wide text-cyan-300/80">
              My trips
            </p>
            <button
              type="button"
              onClick={() => void refreshMyTrips()}
              disabled={myTripsLoading}
              className={`text-xs font-semibold text-slate-400 transition hover:text-cyan-200 disabled:cursor-not-allowed disabled:opacity-50 ${FOCUS_RING_CLASSNAME}`}
            >
              {myTripsLoading ? "Refreshing…" : "Refresh trips"}
            </button>
          </div>
          <div aria-live="polite">
            {myTripsError && (
              <p role="alert" className="mt-2 text-sm text-rose-300">
                {myTripsError}
              </p>
            )}
            {!myTripsError && myTripsLoading && myTrips.length === 0 && (
              <p className="mt-2 text-sm text-slate-400">
                Loading your trips…
              </p>
            )}
            {!myTripsError && !myTripsLoading && myTrips.length === 0 && (
              <p className="mt-2 text-sm text-slate-400">
                You have not created any trips yet. Use the form below to
                plan your first one.
              </p>
            )}
          </div>
          {myTrips.length > 0 && (
            <ul className="mt-3 flex flex-col gap-2">
              {myTrips.map((trip) => {
                const isThisTripLoading =
                  isLoadingExisting && loadingTripId === trip.trip_id;
                return (
                  <li key={trip.trip_id}>
                    <button
                      type="button"
                      onClick={() => void handleSelectMyTrip(trip.trip_id)}
                      disabled={isLoading || isLoadingExisting}
                      aria-busy={isThisTripLoading}
                      className={`w-full rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-left text-sm text-slate-200 transition hover:border-cyan-300/40 hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50 ${FOCUS_RING_CLASSNAME}`}
                    >
                      <p className="break-words font-semibold text-cyan-200">
                        {trip.primary_destination ?? "Untitled destination"}
                      </p>
                      <p className="mt-0.5 break-words text-xs text-slate-400">
                        {trip.origin_city ? `From ${trip.origin_city} · ` : ""}
                        {trip.start_date ?? "?"} → {trip.end_date ?? "?"} ·{" "}
                        {trip.status}
                      </p>
                      {isThisTripLoading && (
                        <p className="mt-1 text-xs font-semibold text-cyan-300">
                          Loading…
                        </p>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <div className="mt-8">
          <p className="text-xs font-semibold uppercase tracking-wide text-cyan-300/80">
            Create a new trip
          </p>
          <p className="mt-1 text-xs text-slate-400">
            The main action on this page — fill in the details below and the
            backend pipeline will generate a draft plan.
          </p>
        </div>

        <form
          className="mt-3 grid grid-cols-1 gap-4 rounded-2xl border border-cyan-300/20 bg-white/5 p-6 sm:grid-cols-2"
          onSubmit={(event) => {
            event.preventDefault();
            void handlePlanTrip();
          }}
        >
          <label className="flex flex-col gap-1 text-sm text-slate-300">
            Destination
            <input
              className="rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/50"
              value={form.primary_destination}
              onChange={(event) =>
                setForm({ ...form, primary_destination: event.target.value })
              }
            />
          </label>

          <label className="flex flex-col gap-1 text-sm text-slate-300">
            Origin city
            <input
              className="rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/50"
              value={form.origin_city}
              onChange={(event) =>
                setForm({ ...form, origin_city: event.target.value })
              }
            />
          </label>

          <label className="flex flex-col gap-1 text-sm text-slate-300">
            Start date
            <input
              type="date"
              className="rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/50"
              value={form.start_date}
              onChange={(event) =>
                setForm({ ...form, start_date: event.target.value })
              }
            />
          </label>

          <label className="flex flex-col gap-1 text-sm text-slate-300">
            End date
            <input
              type="date"
              className="rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/50"
              value={form.end_date}
              onChange={(event) =>
                setForm({ ...form, end_date: event.target.value })
              }
            />
          </label>

          <label className="flex flex-col gap-1 text-sm text-slate-300">
            Travelers
            <input
              type="number"
              min={1}
              max={20}
              className="rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/50"
              value={form.travelers_count}
              onChange={(event) =>
                setForm({
                  ...form,
                  travelers_count: Number(event.target.value),
                })
              }
            />
          </label>

          <label className="flex flex-col gap-1 text-sm text-slate-300">
            Pace
            <select
              className="rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/50"
              value={form.pace}
              onChange={(event) =>
                setForm({
                  ...form,
                  pace: event.target.value as TripRequestInput["pace"],
                })
              }
            >
              <option value="relaxed">Relaxed</option>
              <option value="balanced">Balanced</option>
              <option value="packed">Packed</option>
            </select>
          </label>

          <label className="flex flex-col gap-1 text-sm text-slate-300">
            Travel group
            <select
              className="rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/50"
              value={form.travel_group_type}
              onChange={(event) =>
                setForm({
                  ...form,
                  travel_group_type: event.target
                    .value as TripRequestInput["travel_group_type"],
                })
              }
            >
              <option value="solo">Solo</option>
              <option value="couple">Couple</option>
              <option value="family">Family</option>
              <option value="friends">Friends</option>
              <option value="group">Group</option>
            </select>
          </label>

          <label className="flex flex-col gap-1 text-sm text-slate-300">
            Budget min (USD)
            <input
              type="number"
              min={0}
              className="rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/50"
              value={form.budget_min ?? ""}
              onChange={(event) =>
                setForm({
                  ...form,
                  budget_min:
                    event.target.value === ""
                      ? undefined
                      : Number(event.target.value),
                })
              }
            />
          </label>

          <label className="flex flex-col gap-1 text-sm text-slate-300">
            Budget max (USD)
            <input
              type="number"
              min={0}
              className="rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/50"
              value={form.budget_max ?? ""}
              onChange={(event) =>
                setForm({
                  ...form,
                  budget_max:
                    event.target.value === ""
                      ? undefined
                      : Number(event.target.value),
                })
              }
            />
          </label>

          <label className="flex flex-col gap-1 text-sm text-slate-300 sm:col-span-2">
            Interests (comma-separated)
            <input
              className="rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/50"
              placeholder="museums, hiking, local food"
              value={interestsText}
              onChange={(event) => setInterestsText(event.target.value)}
            />
          </label>

          <label className="flex flex-col gap-1 text-sm text-slate-300 sm:col-span-2">
            Must-visit places (comma-separated)
            <input
              className="rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/50"
              placeholder="Eiffel Tower, Louvre Museum"
              value={mustVisitText}
              onChange={(event) => setMustVisitText(event.target.value)}
            />
          </label>

          <label className="flex flex-col gap-1 text-sm text-slate-300 sm:col-span-2">
            Constraints (comma-separated)
            <input
              className="rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/50"
              placeholder="no early mornings, wheelchair accessible"
              value={constraintsText}
              onChange={(event) => setConstraintsText(event.target.value)}
            />
          </label>

          <label className="flex flex-col gap-1 text-sm text-slate-300 sm:col-span-2">
            Anything else we should know?
            <textarea
              className="rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/50"
              rows={3}
              value={form.free_text_preferences ?? ""}
              onChange={(event) =>
                setForm({
                  ...form,
                  free_text_preferences:
                    event.target.value === "" ? undefined : event.target.value,
                })
              }
            />
          </label>

          <button
            type="submit"
            disabled={isLoading || isLoadingExisting}
            className="col-span-full mt-2 rounded-lg bg-cyan-400 px-4 py-2 font-semibold text-slate-950 transition focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-200 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isLoading ? "Planning..." : "Create trip and generate plan"}
          </button>
        </form>

        <div className="mt-6 rounded-2xl border border-white/5 bg-white/[0.02] p-5">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
            Advanced: load a trip by ID
          </p>
          <p className="mt-1 text-xs text-slate-500">
            A power-user fallback for &ldquo;My trips&rdquo; above — most
            people won&apos;t need this. Only works for a trip_id your own
            account owns; any other trip_id is refused.
          </p>
          <label className="mt-3 flex flex-col gap-1 text-sm text-slate-400">
            trip_id
            <div className="mt-1 flex flex-col gap-2 sm:flex-row">
              <input
                className="flex-1 rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/50"
                placeholder="trip_..."
                value={existingTripId}
                onChange={(event) => setExistingTripId(event.target.value)}
              />
              <button
                type="button"
                onClick={() => void handleLoadExistingTrip()}
                disabled={isLoading || isLoadingExisting}
                className="rounded-lg border border-white/10 bg-slate-900 px-4 py-2 font-semibold text-slate-300 transition hover:bg-slate-800 hover:text-cyan-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/50 disabled:cursor-not-allowed disabled:opacity-50 sm:shrink-0"
              >
                {isLoadingExisting ? "Loading trip…" : "Load existing trip"}
              </button>
            </div>
          </label>
          <p className="mt-2 text-xs text-slate-500">
            Reloads a previously generated plan stored on the backend, using
            its trip_id. Useful after a backend restart, since generated
            plans are persisted locally.
          </p>
        </div>

        <TravelGenerationLoading
          key={isLoading ? "loading" : "idle"}
          originCity={form.origin_city}
          destination={form.primary_destination}
          isLoading={isLoading}
          progressPercent={backendProgress?.progress_percent}
          stageKey={backendProgress?.current_stage}
          stageLabel={backendProgress?.current_stage_label ?? undefined}
          progressMessage={backendProgress?.message}
          isRealBackendStageProgress={backendProgress?.is_real_backend_stage_progress}
          isCompleted={backendProgress?.status === "completed"}
        />

        {error && (
          <div className="mt-6 break-words rounded-2xl border border-red-400/30 bg-red-400/10 p-5 text-sm text-red-100">
            {error}
          </div>
        )}

        {result && (
          <div className="mt-8 flex flex-col gap-6">
            <div id="summary" className="flex flex-col gap-6">
              <div className="rounded-2xl border border-cyan-300/20 bg-cyan-300/10 p-5 text-sm text-cyan-50">
                <p className="break-all font-semibold">
                  Trip {result.summary.trip_id}
                </p>
                <p className="mt-2 leading-6">
                  Pipeline status:{" "}
                  <span className="font-semibold">
                    {result.summary.pipeline_status}
                  </span>
                  {" · "}
                  Validation:{" "}
                  <span className="font-semibold">
                    {readinessLabel(result.summary.validation_status)}
                  </span>
                </p>
                {(result.summary.main_blocking_reason ||
                  result.summary.main_review_reason) && (
                  <p className="mt-2 break-words leading-6 text-cyan-100/90">
                    {result.summary.main_blocking_reason ??
                      result.summary.main_review_reason}
                  </p>
                )}
                <dl className="mt-4 grid grid-cols-2 gap-3 text-xs text-cyan-100/80 sm:grid-cols-4">
                  <div>
                    <dt className="uppercase tracking-wide">Attractions</dt>
                    <dd className="text-base font-semibold text-cyan-50">
                      {result.summary.candidate_pois_count}
                    </dd>
                  </div>
                  <div>
                    <dt className="uppercase tracking-wide">Restaurants</dt>
                    <dd className="text-base font-semibold text-cyan-50">
                      {result.summary.candidate_restaurants_count}
                    </dd>
                  </div>
                  <div>
                    <dt className="uppercase tracking-wide">
                      Accommodation POIs
                    </dt>
                    <dd className="text-base font-semibold text-cyan-50">
                      {result.summary.candidate_accommodation_pois_count}
                    </dd>
                  </div>
                  <div>
                    <dt className="uppercase tracking-wide">
                      Scheduled experiences
                    </dt>
                    <dd className="text-base font-semibold text-cyan-50">
                      {result.summary.scheduled_experiences_count}
                    </dd>
                  </div>
                </dl>
              </div>

              <ModeToggle mode={mode} onModeChange={setMode} />
              <p className="text-xs text-slate-500">
                Developer view shows diagnostics for your own trip. It does
                not change provider data or generation behavior.
              </p>

              {mode === "user" && (
                <UserModeReadinessBanner
                  validationStatus={result.summary.validation_status}
                />
              )}
            </div>

            <ResultJumpLinks mode={mode} />

            {mode === "developer" ? (
              <>
                {/* ---- Developer view: full diagnostic stack, order
                    unchanged since before Step 182D. ---- */}
                <ResultGroupHeader
                  id="plan-overview"
                  title="Plan overview"
                  description="Start here. This section explains whether the generated plan is usable as a draft and what still needs review."
                />

                <PlanOverviewSubheading
                  title="Trust & readiness status"
                  description="Where things currently stand — trust signals, validation readiness, and overall plan status."
                />

                <TrustDashboardSection model={buildTrustDashboardModel(result)} />

                <UserTrustSummarySection
                  validationStatus={result.summary.validation_status}
                  checklist={result.readinessChecklist}
                  validationReport={result.validationReport}
                />

                <PlanStatusSection
                  validationStatus={result.summary.validation_status}
                  checklist={result.readinessChecklist}
                />

                <PlanOverviewSubheading
                  title="Feedback & regeneration workflow"
                  description="Requesting changes, and the regeneration readiness, diff preview, version history, and audit trail those changes affect."
                />

                <div id="feedback">
                  <FeedbackPanel
                    feedbackText={feedbackText}
                    onFeedbackTextChange={setFeedbackText}
                    onSubmit={() => void handleSubmitFeedback()}
                    isSubmitting={isSubmittingFeedback}
                    successMessage={feedbackSuccessMessage}
                    errorMessage={feedbackErrorMessage}
                    feedbackHistory={result.feedbackHistory}
                  />
                </div>

                <PendingRequestedChangesSection
                  summary={result.pendingFeedbackSummary}
                />

                <VersionHistorySection versionHistory={result.versionHistory} />

                <PlanDiffPreviewSection preview={result.planDiffPreview} />

                <RegenerationReadinessSection
                  tripId={result.summary.trip_id}
                  readiness={result.regenerationReadiness}
                  compact={false}
                  onRegenerationAttemptsChange={(regenerationAttempts) =>
                    setResult((previous) =>
                      previous ? { ...previous, regenerationAttempts } : previous,
                    )
                  }
                  onRegenerateSuccess={async () => {
                    setResult(await loadPlanResult(result.summary.trip_id));
                  }}
                />

                <RegenerationAttemptAuditSection
                  attempts={result.regenerationAttempts}
                />

                <ResultGroupHeader
                  id="travel-context"
                  title="Travel context"
                  description="Provider-backed context that may affect planning, but does not automatically make the itinerary final."
                />

                <WeatherContextSection weather={result.weatherContext} />

                <HolidayContextSection holiday={result.holidayContext} />

                <CurrencyContextSection currency={result.currencyContext} />

                <RouteFeasibilitySection routeFeasibility={result.routeFeasibilityContext} />

                <ResultGroupHeader
                  id="draft-itinerary"
                  title="Draft itinerary"
                  description="Scheduled places, map previews, and nearby open-data suggestions generated from backend-returned data."
                />

                <LockedItemsSummarySection
                  tripId={result.summary.trip_id}
                  userLocks={result.userLocks}
                  dailyPlans={result.dailyPlans}
                  onLockChange={(userLocks, planDiffPreview, regenerationReadiness) =>
                    setResult((previous) =>
                      previous
                        ? { ...previous, userLocks, planDiffPreview, regenerationReadiness }
                        : previous,
                    )
                  }
                />

                <ItineraryNarrativeSummarySection report={result.itineraryNarrativeReport} />

                {dayWiseItinerarySection}

                <div id="where-to-stay">
                  <StayAreaGuidanceSection guidance={result.stayAreaGuidance} />
                </div>

                <ResultGroupHeader
                  id="review-required"
                  title="Why this needs review"
                  description="Decision explanations, implementation gaps, readiness checklist, validation report, and assumptions."
                />

                <DecisionSummarySection summary={result.decisionSummary} />

                <ImplementationGapsSection gaps={result.implementationGaps} />

                <ReadinessChecklistSection checklist={result.readinessChecklist} />

                <div id="validation">
                  <ValidationSection report={result.validationReport} />
                </div>

                <PlanningAssumptionsSection
                  destinationAssumptions={result.destinationAssumptions}
                  destinationConfidence={result.destinationConfidence}
                  experienceAssumptions={result.experienceAssumptions}
                  experienceConfidence={result.experienceConfidence}
                />

                <ResultGroupHeader
                  id="data-sources"
                  title="Data sources and candidates"
                  description="Provider coverage and raw candidate places used to build the draft plan."
                />

                <ProviderCoverageSection coverage={result.providerCoverage} />

                <div id="inventories" className="flex flex-col gap-6">
                  <AccommodationInventorySection
                    report={result.accommodationInventoryReport}
                  />

                  <FlightInventorySection report={result.flightInventoryReport} />
                </div>

                <ItineraryNarrativeDiagnosticSection
                  report={result.itineraryNarrativeReport}
                />

                <AICandidateReviewSection
                  tripId={result.summary.trip_id}
                  reviewReport={result.aiCandidateReviewReport}
                  promotionReport={result.aiCandidatePromotionReport}
                  onPromotionReportChange={(report) =>
                    setResult((previous) =>
                      previous
                        ? { ...previous, aiCandidatePromotionReport: report }
                        : previous,
                    )
                  }
                />

                <CandidatePoiSection
                  title="Destination candidate attractions"
                  notes={[
                    "Attraction candidates are provider-backed place candidates only. They are not checked for opening hours, tickets, visit duration, or route feasibility yet.",
                  ]}
                  pois={result.candidatePois}
                  emptyMessage="No attraction candidates returned."
                />

                <CandidatePoiSection
                  title="Destination candidate restaurants"
                  notes={[
                    "Restaurant candidates are provider-backed location candidates only. They are not ratings, prices, reservations, opening-hours checks, or final restaurant recommendations.",
                  ]}
                  pois={result.candidateRestaurants}
                  emptyMessage="No restaurant candidates returned."
                />

                <CandidatePoiSection
                  title="Destination candidate accommodation POIs"
                  notes={[
                    "Open-data location candidates only, not bookable inventory.",
                    "Accommodation POI candidates are open-data location candidates only. They are not hotel prices, availability, ratings, booking links, or final stay recommendations.",
                  ]}
                  pois={result.candidateAccommodationPois}
                  emptyMessage="No accommodation POI candidates returned."
                />
              </>
            ) : (
              <>
                {/* ---- Traveler view (Step 182D): Summary (already
                    rendered above) -> Context -> Itinerary -> Where to
                    stay -> Flights -> Feedback/regenerate. Every
                    component here is the exact same one Developer view
                    uses elsewhere (or an additive concise wrapper around
                    the same underlying data) -- nothing is deleted,
                    only reordered/hidden. ---- */}
                <div id="travel-context">
                  <TravelerContextSummarySection
                    weather={result.weatherContext}
                    holiday={result.holidayContext}
                    currency={result.currencyContext}
                  />
                </div>

                <div id="draft-itinerary" className="flex flex-col gap-6">
                  <LockedItemsSummarySection
                    tripId={result.summary.trip_id}
                    userLocks={result.userLocks}
                    dailyPlans={result.dailyPlans}
                    onLockChange={(userLocks, planDiffPreview, regenerationReadiness) =>
                      setResult((previous) =>
                        previous
                          ? { ...previous, userLocks, planDiffPreview, regenerationReadiness }
                          : previous,
                      )
                    }
                  />

                  <ItineraryNarrativeSummarySection report={result.itineraryNarrativeReport} />

                  {dayWiseItinerarySection}
                </div>

                <div id="where-to-stay">
                  <TravelerWhereToStaySection
                    accommodationInventoryReport={result.accommodationInventoryReport}
                    stayAreaGuidance={result.stayAreaGuidance}
                  />
                </div>

                <div id="flights">
                  <UserModeFlightSummary report={result.flightInventoryReport} />
                </div>

                <div id="feedback" className="flex flex-col gap-6">
                  <FeedbackPanel
                    feedbackText={feedbackText}
                    onFeedbackTextChange={setFeedbackText}
                    onSubmit={() => void handleSubmitFeedback()}
                    isSubmitting={isSubmittingFeedback}
                    successMessage={feedbackSuccessMessage}
                    errorMessage={feedbackErrorMessage}
                    feedbackHistory={result.feedbackHistory}
                  />

                  <RegenerationReadinessSection
                    tripId={result.summary.trip_id}
                    readiness={result.regenerationReadiness}
                    compact={true}
                    onRegenerationAttemptsChange={(regenerationAttempts) =>
                      setResult((previous) =>
                        previous ? { ...previous, regenerationAttempts } : previous,
                      )
                    }
                    onRegenerateSuccess={async () => {
                      setResult(await loadPlanResult(result.summary.trip_id));
                    }}
                  />
                </div>
              </>
            )}
          </div>
        )}
      </section>
    </main>
  );
}
