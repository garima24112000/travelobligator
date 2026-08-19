"use client";

import { useEffect, useRef, useState } from "react";
import type * as Leaflet from "leaflet";
import {
  ApiRequestError,
  createTrip,
  createTripLock,
  deleteTripLock,
  generatePlan,
  getDestinationContext,
  getExperiencePlan,
  getGenerationProgress,
  getProviderCoverage,
  getRegenerationAttempts,
  getRegenerationReadiness,
  getTrip,
  getTripSummary,
  getValidationReport,
  requestRegeneration,
  submitTripFeedback,
} from "@/lib/api";
import type {
  AccommodationSuggestion,
  CandidatePoi,
  ChecklistItemStatus,
  CurrencyContext,
  DailyPlan,
  DecisionSummary,
  ExperienceItem,
  FeedbackChangePreview,
  FeedbackEvent,
  GenerationProgress,
  GeoPoint,
  HolidayContext,
  ImplementationGaps,
  PendingFeedbackSummary,
  PlanDiffPreview,
  ProviderCoverageData,
  ProviderStatusEntry,
  ReadinessChecklist,
  RegenerationAttempt,
  RegenerationReadiness,
  RestaurantSuggestion,
  RouteFeasibilityContext,
  StayAreaGuidance,
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

function ValidationIssueList({
  title,
  issues,
}: {
  title: string;
  issues: ValidationReport["critical_issues"];
}) {
  if (issues.length === 0) return null;

  return (
    <div className="mt-3">
      <p className="text-sm font-semibold text-slate-200">{title}</p>
      <ul className="mt-2 flex flex-col gap-2">
        {issues.map((issue, index) => (
          <li
            key={`${issue.category}-${index}`}
            className="rounded-lg border border-white/10 bg-slate-900/60 p-3 text-sm"
          >
            <p className="text-[11px] uppercase tracking-wide text-slate-500">
              {issue.category} · {issue.severity}
            </p>
            <p className="mt-1 text-slate-200">{issue.message}</p>
            {issue.affected_section && (
              <p className="mt-1 text-xs text-slate-400">
                Affects: {issue.affected_section}
              </p>
            )}
            {issue.suggested_fix && (
              <p className="mt-1 text-xs text-slate-400">
                Suggested fix: {issue.suggested_fix}
              </p>
            )}
          </li>
        ))}
      </ul>
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
      <p className="font-medium text-slate-100">
        {accommodation.name}
        {accommodation.category && (
          <span className="font-normal text-slate-400">
            {" "}
            ({accommodation.category})
          </span>
        )}
      </p>
      {accommodation.address && (
        <p className="mt-1 text-xs text-slate-400">{accommodation.address}</p>
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
      <ul className="mt-2 list-disc pl-5 text-sm text-slate-300">
        {items.map((item, index) => (
          <li key={`${title}-${index}`}>{item}</li>
        ))}
      </ul>
    </div>
  );
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
    <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h2 className="text-lg font-semibold">Readiness checklist</h2>
      <p className="mt-2 text-sm text-slate-300">{checklist.summary}</p>

      <ul className="mt-3 flex flex-col gap-2">
        {checklist.items.map((item) => (
          <li
            key={item.label}
            className="rounded-lg border border-white/10 bg-slate-900/60 p-3 text-sm"
          >
            <p className="flex items-center justify-between gap-2">
              <span className="font-semibold text-slate-200">{item.label}</span>
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
            <ul className="mt-2 list-disc pl-4 text-xs text-slate-300">
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
            <ul className="mt-2 list-disc pl-4 text-xs text-slate-300">
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
            <ul className="mt-2 list-disc pl-4 text-xs text-slate-300">
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

function RouteFeasibilitySection({
  routeFeasibility,
}: {
  routeFeasibility: RouteFeasibilityContext;
}) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
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
 * coordinate-backed scheduled experiences (in existing itinerary order),
 * connected by a dotted straight-line polyline. This is not route
 * feasibility, walking distance, walking time, or route optimization --
 * see the caption rendered below the map for the exact wording. Leaflet is
 * loaded via dynamic import inside useEffect (never as a top-level runtime
 * import) so it never touches `window`/`document` during server rendering.
 */
function DayMapPreview({ experiences }: { experiences: ExperienceItem[] }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<Leaflet.Map | null>(null);

  const coordinateBackedCount = experiences.filter(
    (experience) => experience.coordinates !== null,
  ).length;

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

      // Marker labels are the full-day itinerary order number (1-based
      // index into `experiences`), not a renumbering of only the
      // coordinate-backed ones -- e.g. if experience #2 has no
      // coordinates but #3 does, #3's marker still says "3".
      const points = experiences
        .map((experience, index) => ({ experience, orderNumber: index + 1 }))
        .filter((item) => item.experience.coordinates !== null);

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

      if (latLngs.length > 1) {
        L.polyline(latLngs, {
          color: "#67e8f9",
          weight: 2,
          dashArray: "6, 6",
        }).addTo(map);
      }

      if (latLngs.length === 1) {
        map.setView(latLngs[0], 14);
      } else {
        map.fitBounds(L.latLngBounds(latLngs), { padding: [24, 24] });
      }
    })();

    return () => {
      isCancelled = true;
      mapRef.current?.remove();
      mapRef.current = null;
    };
  }, [experiences, coordinateBackedCount]);

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
        className="h-[260px] w-full overflow-hidden rounded-lg border border-white/10"
      />
      <p className="mt-2 text-[11px] text-slate-500">
        Map shows provider-backed scheduled place coordinates in itinerary
        order. Dotted lines are visual straight-line connectors only, not
        walking routes, travel-time estimates, or route optimization.
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
function ExperienceMapLinks({ coordinates }: { coordinates: GeoPoint | null }) {
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
        className="text-cyan-300 underline decoration-cyan-300/40 underline-offset-2 hover:text-cyan-200"
      >
        Open in Google Maps
      </a>
      <a
        href={openStreetMapUrl}
        target="_blank"
        rel="noopener noreferrer"
        className="text-cyan-300 underline decoration-cyan-300/40 underline-offset-2 hover:text-cyan-200"
      >
        Open in OpenStreetMap
      </a>
    </p>
  );
}

/**
 * Compact card for a single scheduled experience. `orderNumber` is the
 * full-day itinerary position (1-based index into the day's `experiences`
 * array), matching the numbering used by `DayMapPreview`'s markers -- not a
 * renumbering of only coordinate-backed items.
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
          <p className="font-medium text-slate-100">
            {experience.name}{" "}
            <span className="font-normal text-slate-400">
              ({experience.category})
            </span>
          </p>
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
            <ExperienceMapLinks coordinates={experience.coordinates} />
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
                  className="rounded-full border border-white/10 bg-slate-900 px-3 py-1 text-xs font-semibold text-slate-300 transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {isSubmittingLock ? "Removing..." : "Remove keep"}
                </button>
              </>
            ) : (
              <button
                type="button"
                onClick={() => void handleKeepThisPlace()}
                disabled={isSubmittingLock}
                className="rounded-full border border-cyan-300/40 bg-slate-900 px-3 py-1 text-xs font-semibold text-cyan-200 transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {isSubmittingLock ? "Saving..." : "Keep this place"}
              </button>
            )}
          </div>

          {lockErrorMessage && (
            <p className="mt-1 text-xs text-red-300">{lockErrorMessage}</p>
          )}
          {lockSuccessMessage && !lockErrorMessage && (
            <p className="mt-1 text-xs text-emerald-300">
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
      <p className="font-medium text-slate-100">
        {restaurant.name}
        {restaurant.category && (
          <span className="font-normal text-slate-400">
            {" "}
            ({restaurant.category})
          </span>
        )}
      </p>
      {restaurant.address && (
        <p className="mt-1 text-xs text-slate-400">{restaurant.address}</p>
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
      <p className="font-medium text-slate-100">
        {accommodation.name}
        {accommodation.category && (
          <span className="font-normal text-slate-400">
            {" "}
            ({accommodation.category})
          </span>
        )}
      </p>
      {accommodation.address && (
        <p className="mt-1 text-xs text-slate-400">{accommodation.address}</p>
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
  const hasNothingToShow =
    report.critical_issues.length === 0 &&
    report.warnings.length === 0 &&
    report.provider_coverage_notes.length === 0 &&
    report.unavailable_data_notes.length === 0;

  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
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

      <ValidationIssueList title="Critical issues" issues={report.critical_issues} />
      <ValidationIssueList title="Warnings" issues={report.warnings} />

      {report.provider_coverage_notes.length > 0 && (
        <div className="mt-3">
          <p className="text-sm font-semibold text-slate-200">
            Provider coverage notes
          </p>
          <ul className="mt-2 list-disc pl-5 text-sm text-slate-300">
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
          <ul className="mt-2 list-disc pl-5 text-sm text-slate-300">
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
      <p className="font-medium text-slate-100">{poi.name}</p>
      <p className="mt-1 text-xs text-slate-400">
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
        <ul className="mt-2 list-disc pl-5 text-sm text-slate-300">
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
  const statusEntries = Object.entries(coverage.provider_status);
  const groupedStatus = groupProviderStatusByType(coverage.provider_status);

  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
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
                {key}
              </dt>
              <dd className="mt-1 text-slate-200">{value}</dd>
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
        <ul className="mt-2 list-disc pl-4 text-xs text-slate-300">
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
                <p className="text-slate-200">{item.field}</p>
                <p className="mt-2 text-[11px] uppercase tracking-wide text-slate-500">
                  Reason
                </p>
                <p className="text-xs text-slate-400">{item.reason}</p>
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
          <ul className="mt-2 list-disc pl-5 text-sm text-slate-300">
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
          <ul className="mt-1 list-disc pl-4 text-xs text-slate-300">
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
          <ul className="mt-1 list-disc pl-4 text-xs text-slate-400">
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
          <ul className="mt-1 list-disc pl-4 text-xs text-slate-400">
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
      <p className="mt-1 text-xs text-amber-300/90">
        These requests are summarized from captured feedback. They have not
        been applied to the plan yet.
      </p>

      <button
        type="button"
        disabled
        title="Feedback-driven regeneration is not implemented yet."
        className="mt-3 cursor-not-allowed rounded-lg border border-white/10 bg-slate-900 px-4 py-2 text-sm font-semibold text-slate-500 opacity-50"
      >
        Regenerate with feedback
      </button>
      <p className="mt-2 text-xs text-slate-500">
        Feedback-driven regeneration is not implemented yet. Your feedback
        is stored and summarized, but the current plan has not changed.
      </p>

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
                    <p className="flex items-center justify-between gap-2">
                      <span className="font-semibold text-slate-200">
                        {item.feedback_type}
                      </span>
                      <span className="text-[11px] uppercase tracking-wide text-slate-400">
                        Count: {item.count}
                      </span>
                    </p>
                    <p className="mt-1 text-xs text-slate-400">
                      Example: {item.example_feedback}
                    </p>
                    {item.likely_changes.length > 0 && (
                      <div className="mt-2">
                        <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                          Likely future changes
                        </p>
                        <ul className="mt-1 list-disc pl-4 text-xs text-slate-300">
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
              <ul className="mt-1 list-disc pl-4 text-xs text-slate-400">
                {summary.blocked_by.map((reason, index) => (
                  <li key={`blocked-by-${index}`}>{reason}</li>
                ))}
              </ul>
            </div>
          )}

          <p className="mt-3 text-xs text-slate-400">{summary.note}</p>
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
    <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h2 className="text-lg font-semibold">Version history</h2>
      <p className="mt-1 text-xs text-amber-300/90">
        Version history records backend bookkeeping only. It does not add
        travel facts.
      </p>

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
              <p className="flex items-center justify-between gap-2">
                <span className="font-semibold text-slate-200">
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
                <p className="mt-2 text-xs text-slate-300">
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
                <p className="mt-1 text-xs text-slate-400">
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

/**
 * Plan-level readout of `PlanningState.plan_diff_preview` (Step 133). Purely
 * a restatement of the backend's deterministic, from-scratch-recomputed
 * preview of what a *future* regeneration would compare/change -- never
 * something this section applies itself. `regeneration_available` always
 * renders as "No" and `to_version` always renders as "None yet" today,
 * since real regeneration/diffing is not implemented.
 */
function PlanDiffPreviewSection({ preview }: { preview: PlanDiffPreview }) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h2 className="text-lg font-semibold">Plan diff preview</h2>
      <p className="mt-1 text-xs text-amber-300/90">
        This is a preview only. No new version or plan diff has been
        generated yet.
      </p>

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
                className="rounded-lg border border-white/10 bg-slate-900/60 p-3 text-xs text-slate-300"
              >
                Type: {item.locked_item_type} · ID: {item.locked_item_id}
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
          <p className="mt-1 text-xs text-slate-400">
            {preview.triggered_by_feedback_event_ids.join(", ")}
          </p>
        </div>
      )}

      {preview.blocked_by.length > 0 && (
        <div className="mt-3">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
            Blocked by
          </p>
          <ul className="mt-1 list-disc pl-4 text-xs text-slate-400">
            {preview.blocked_by.map((reason, index) => (
              <li key={`plan-diff-blocked-by-${index}`}>{reason}</li>
            ))}
          </ul>
        </div>
      )}

      <p className="mt-3 text-xs text-slate-400">{preview.note}</p>
    </div>
  );
}

/**
 * Plan-level readout of `PlanningState.regeneration_readiness` (Step 136).
 * Purely a restatement of the backend's deterministic, from-scratch-
 * recomputed readiness gate -- never something this section applies
 * itself. `can_regenerate` always renders as "No" today since no real
 * regeneration engine is connected, and no clickable regenerate action is
 * ever rendered here, only a disabled placeholder button when blocked.
 *
 * The "Check backend refusal" control (Step 140, extended in Step 143) is a
 * separate, explicitly safe test action: it calls
 * `POST /trips/{trip_id}/regenerate` -- which the backend always refuses
 * with 409 REGENERATION_NOT_AVAILABLE today -- purely to surface that
 * refusal message locally. After the call settles it also refetches
 * `GET /trips/{trip_id}/regeneration-attempts` and reports the result via
 * `onRegenerationAttemptsChange`, which the caller uses to update *only*
 * `result.regenerationAttempts`. It never updates any other `result`
 * field, never reloads the rest of the plan, and never calls
 * `loadPlanResult`, so no other displayed plan field changes as a result
 * of clicking it.
 */
function RegenerationReadinessSection({
  tripId,
  readiness,
  onRegenerationAttemptsChange,
}: {
  tripId: string;
  readiness: RegenerationReadiness;
  onRegenerationAttemptsChange: (attempts: RegenerationAttempt[]) => void;
}) {
  const [isCheckingRefusal, setIsCheckingRefusal] = useState(false);
  const [refusalMessage, setRefusalMessage] = useState<string | null>(null);
  const [refusalMessageIsWarning, setRefusalMessageIsWarning] =
    useState(false);

  async function handleCheckBackendRefusal() {
    setIsCheckingRefusal(true);
    setRefusalMessage(null);
    setRefusalMessageIsWarning(false);
    try {
      await requestRegeneration(tripId);
      // The backend is expected to always throw (409 REGENERATION_NOT_AVAILABLE).
      // Reaching here means it unexpectedly returned success -- flag it as a
      // warning rather than silently treating it as a good outcome.
      setRefusalMessage(
        "Unexpected success from regeneration endpoint. Please verify backend behavior before trusting this.",
      );
      setRefusalMessageIsWarning(true);
    } catch (err) {
      setRefusalMessage(
        err instanceof ApiRequestError
          ? err.message
          : "Something went wrong while checking the backend refusal path.",
      );
      setRefusalMessageIsWarning(false);
    } finally {
      // Refresh only the regeneration attempt audit list -- whether the
      // call above threw (expected) or unexpectedly resolved. Never touch
      // any other plan field, and never call loadPlanResult.
      try {
        const attemptsData = await getRegenerationAttempts(tripId);
        onRegenerationAttemptsChange(attemptsData.regeneration_attempts);
      } catch {
        // If refreshing the audit list itself fails, leave the previously
        // displayed attempts as-is instead of clearing them.
      }
      setIsCheckingRefusal(false);
    }
  }

  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h2 className="text-lg font-semibold">Regeneration readiness</h2>
      <p className="mt-1 text-xs text-amber-300/90">
        This gate only explains whether feedback-driven regeneration can
        run. It does not regenerate or change the plan.
      </p>

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
          <ul className="mt-1 list-disc pl-4 text-xs text-slate-400">
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

      {readiness.status === "blocked" && (
        <div className="mt-4">
          <button
            type="button"
            disabled
            title="The regeneration engine is not implemented yet."
            className="cursor-not-allowed rounded-lg border border-white/10 bg-slate-900 px-4 py-2 text-sm font-semibold text-slate-500 opacity-50"
          >
            Regeneration unavailable
          </button>
          <p className="mt-2 text-xs text-slate-500">
            The regeneration engine is not implemented yet.
          </p>
        </div>
      )}

      <div className="mt-4 border-t border-white/10 pt-4">
        <p className="text-xs text-slate-500">
          This only checks the backend refusal path. It will not
          regenerate or change the plan.
        </p>
        <button
          type="button"
          onClick={() => void handleCheckBackendRefusal()}
          disabled={isCheckingRefusal}
          className="mt-2 rounded-lg border border-white/10 bg-slate-900 px-4 py-2 text-sm font-semibold text-slate-300 transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {isCheckingRefusal ? "Checking refusal..." : "Check backend refusal"}
        </button>
        {refusalMessage && (
          <p
            className={`mt-2 text-xs ${
              refusalMessageIsWarning ? "text-amber-300" : "text-red-300"
            }`}
          >
            {refusalMessage}
          </p>
        )}
      </div>
    </div>
  );
}

/**
 * Plan-level readout of `PlanningState.regeneration_attempts` (Step 143).
 * Purely a restatement of the backend's audit trail of blocked
 * `POST /trips/{trip_id}/regenerate` calls -- never itinerary content, and
 * never a claim that regeneration ran, a diff was generated, or a new plan
 * version was created. Only ever refreshed via the "Check backend refusal"
 * control in `RegenerationReadinessSection`, which updates
 * `result.regenerationAttempts` directly and never calls `loadPlanResult`.
 */
function RegenerationAttemptAuditSection({
  attempts,
}: {
  attempts: RegenerationAttempt[];
}) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h2 className="text-lg font-semibold">Regeneration attempt audit</h2>
      <p className="mt-1 text-xs text-amber-300/90">
        This is an audit trail of blocked regeneration requests. It does
        not contain itinerary content and does not mean regeneration ran.
      </p>

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
              <p className="flex items-center justify-between gap-2">
                <span className="font-semibold text-slate-200">
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
                {attempt.reason_code}
              </p>
              <p className="mt-1 text-xs text-slate-300">{attempt.message}</p>
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
        className="mt-3 w-full rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-sm text-slate-100"
        rows={3}
        placeholder="e.g. Make this less packed"
        value={feedbackText}
        onChange={(event) => onFeedbackTextChange(event.target.value)}
      />

      <button
        type="button"
        onClick={onSubmit}
        disabled={isSubmitting}
        className="mt-3 rounded-lg border border-cyan-300/40 bg-slate-900 px-4 py-2 text-sm font-semibold text-cyan-200 transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {isSubmitting ? "Saving feedback..." : "Submit feedback"}
      </button>

      {errorMessage && (
        <p className="mt-3 text-sm text-red-300">{errorMessage}</p>
      )}
      {successMessage && !errorMessage && (
        <p className="mt-3 text-sm text-emerald-300">{successMessage}</p>
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
              <p className="text-slate-200">{event.feedback_text}</p>
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
                  <p className="mt-1 text-xs text-slate-300">
                    {event.interpretation.summary}
                  </p>
                  <p className="mt-1 text-xs text-amber-300/90">
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

const RESULT_JUMP_LINKS: { id: string; label: string }[] = [
  { id: "plan-overview", label: "Plan overview" },
  { id: "travel-context", label: "Travel context" },
  { id: "draft-itinerary", label: "Draft itinerary" },
  { id: "review-required", label: "Review required" },
  { id: "data-sources", label: "Data sources" },
];

function ResultJumpLinks() {
  return (
    <nav
      aria-label="Jump to result section"
      className="rounded-2xl border border-white/10 bg-white/5 p-4"
    >
      <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
        Jump to
      </p>
      <ul className="mt-2 flex flex-wrap gap-2">
        {RESULT_JUMP_LINKS.map((link) => (
          <li key={link.id}>
            <a
              href={`#${link.id}`}
              className="inline-block rounded-full border border-white/10 bg-slate-900/60 px-3 py-1 text-xs text-cyan-200 hover:border-cyan-300/40 hover:text-cyan-100"
            >
              {link.label}
            </a>
          </li>
        ))}
      </ul>
    </nav>
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
  ] = await Promise.all([
    getDestinationContext(tripId),
    getExperiencePlan(tripId),
    getValidationReport(tripId),
    getProviderCoverage(tripId),
    getTrip(tripId),
    getRegenerationReadiness(tripId),
    getRegenerationAttempts(tripId),
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
                <p className="font-medium text-slate-100">
                  {matchedExperience
                    ? matchedExperience.name
                    : "Matching scheduled experience not found in the current plan."}
                </p>
                <p className="mt-1 text-xs text-slate-400">
                  Type: {lock.locked_item_type} · ID: {lock.locked_item_id}
                </p>
                <p className="mt-1 text-xs text-slate-400">
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
                  className="mt-2 rounded-full border border-white/10 bg-slate-900 px-3 py-1 text-xs font-semibold text-slate-300 transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {state?.isSubmitting ? "Removing..." : "Remove keep"}
                </button>

                {state?.errorMessage && (
                  <p className="mt-2 text-xs text-red-300">
                    {state.errorMessage}
                  </p>
                )}
                {state?.successMessage && !state.errorMessage && (
                  <p className="mt-2 text-xs text-emerald-300">
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
  stageLabel,
  progressMessage,
  isRealBackendStageProgress,
}: {
  originCity?: string;
  destination?: string;
  isLoading: boolean;
  progressPercent?: number;
  stageLabel?: string;
  progressMessage?: string;
  isRealBackendStageProgress?: boolean;
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
  const displayProgress = hasBackendProgress ? progressPercent : localProgress;
  const displayMessage =
    stageLabel || progressMessage || TRAVEL_LOADING_STAGES[stageIndex];

  return (
    <div className="mt-8 rounded-2xl border border-white/10 bg-white/5 p-6">
      <div className="flex items-center justify-between text-sm font-medium text-slate-300">
        <span>{origin}</span>
        <span>{dest}</span>
      </div>
      <div
        className="relative mt-4 h-1.5 rounded-full bg-slate-800"
        aria-hidden="true"
      >
        <div
          className="h-1.5 rounded-full bg-cyan-400/70 transition-[width] duration-500 motion-reduce:transition-none"
          style={{ width: `${displayProgress}%` }}
        />
        <span
          className="absolute -top-2.5 -translate-x-1/2 text-base transition-[left] duration-500 motion-reduce:transition-none"
          style={{ left: `${displayProgress}%` }}
        >
          ✈️
        </span>
      </div>
      <p
        className="mt-4 text-sm text-slate-200"
        role="status"
        aria-live="polite"
      >
        {displayMessage}
      </p>
      <p className="mt-2 text-[11px] text-slate-500">
        Loading animation only — not live flight tracking.
      </p>
      {showBackendNote && (
        <p className="mt-1 text-[11px] text-emerald-300/80">
          Using backend stage progress
        </p>
      )}
    </div>
  );
}

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
        err instanceof ApiRequestError
          ? err.message
          : "Something went wrong while saving feedback.",
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
    } catch (err) {
      setError(
        err instanceof ApiRequestError
          ? err.message
          : "Something went wrong while talking to the backend.",
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
        err instanceof ApiRequestError
          ? err.message
          : "Something went wrong while talking to the backend.",
      );
    } finally {
      setIsLoadingExisting(false);
    }
  }

  return (
    <main className="min-h-screen bg-slate-950 px-6 py-12 text-slate-100">
      <section className="mx-auto max-w-4xl rounded-3xl border border-white/10 bg-white/5 p-8 shadow-2xl">
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

        <div className="mt-8 rounded-2xl border border-white/10 bg-white/5 p-6">
          <label className="flex flex-col gap-1 text-sm text-slate-300">
            Load an existing trip by trip_id
            <div className="mt-1 flex flex-col gap-2 sm:flex-row">
              <input
                className="flex-1 rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-slate-100"
                placeholder="trip_..."
                value={existingTripId}
                onChange={(event) => setExistingTripId(event.target.value)}
              />
              <button
                type="button"
                onClick={() => void handleLoadExistingTrip()}
                disabled={isLoading || isLoadingExisting}
                className="rounded-lg border border-cyan-300/40 bg-slate-900 px-4 py-2 font-semibold text-cyan-200 transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50 sm:shrink-0"
              >
                {isLoadingExisting ? "Loading trip..." : "Load existing trip"}
              </button>
            </div>
          </label>
          <p className="mt-2 text-xs text-slate-500">
            Reloads a previously generated plan stored on the backend, using
            its trip_id. Useful after a backend restart, since generated
            plans are persisted locally.
          </p>
        </div>

        <form
          className="mt-8 grid grid-cols-1 gap-4 rounded-2xl border border-white/10 bg-white/5 p-6 sm:grid-cols-2"
          onSubmit={(event) => {
            event.preventDefault();
            void handlePlanTrip();
          }}
        >
          <label className="flex flex-col gap-1 text-sm text-slate-300">
            Destination
            <input
              className="rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-slate-100"
              value={form.primary_destination}
              onChange={(event) =>
                setForm({ ...form, primary_destination: event.target.value })
              }
            />
          </label>

          <label className="flex flex-col gap-1 text-sm text-slate-300">
            Origin city
            <input
              className="rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-slate-100"
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
              className="rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-slate-100"
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
              className="rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-slate-100"
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
              className="rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-slate-100"
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
              className="rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-slate-100"
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
              className="rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-slate-100"
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
              className="rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-slate-100"
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
              className="rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-slate-100"
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
              className="rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-slate-100"
              placeholder="museums, hiking, local food"
              value={interestsText}
              onChange={(event) => setInterestsText(event.target.value)}
            />
          </label>

          <label className="flex flex-col gap-1 text-sm text-slate-300 sm:col-span-2">
            Must-visit places (comma-separated)
            <input
              className="rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-slate-100"
              placeholder="Eiffel Tower, Louvre Museum"
              value={mustVisitText}
              onChange={(event) => setMustVisitText(event.target.value)}
            />
          </label>

          <label className="flex flex-col gap-1 text-sm text-slate-300 sm:col-span-2">
            Constraints (comma-separated)
            <input
              className="rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-slate-100"
              placeholder="no early mornings, wheelchair accessible"
              value={constraintsText}
              onChange={(event) => setConstraintsText(event.target.value)}
            />
          </label>

          <label className="flex flex-col gap-1 text-sm text-slate-300 sm:col-span-2">
            Anything else we should know?
            <textarea
              className="rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-slate-100"
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
            className="col-span-full mt-2 rounded-lg bg-cyan-400 px-4 py-2 font-semibold text-slate-950 transition disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isLoading ? "Planning..." : "Create trip and generate plan"}
          </button>
        </form>

        <TravelGenerationLoading
          key={isLoading ? "loading" : "idle"}
          originCity={form.origin_city}
          destination={form.primary_destination}
          isLoading={isLoading}
          progressPercent={backendProgress?.progress_percent}
          stageLabel={backendProgress?.current_stage_label ?? undefined}
          progressMessage={backendProgress?.message}
          isRealBackendStageProgress={backendProgress?.is_real_backend_stage_progress}
        />

        {error && (
          <div className="mt-6 rounded-2xl border border-red-400/30 bg-red-400/10 p-5 text-sm text-red-100">
            {error}
          </div>
        )}

        {result && (
          <div className="mt-8 flex flex-col gap-6">
            <div className="rounded-2xl border border-cyan-300/20 bg-cyan-300/10 p-5 text-sm text-cyan-50">
              <p className="font-semibold">Trip {result.summary.trip_id}</p>
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
                <p className="mt-2 leading-6 text-cyan-100/90">
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

            <ResultJumpLinks />

            <ResultGroupHeader
              id="plan-overview"
              title="Plan overview"
              description="Start here. This section explains whether the generated plan is usable as a draft and what still needs review."
            />

            <UserTrustSummarySection
              validationStatus={result.summary.validation_status}
              checklist={result.readinessChecklist}
              validationReport={result.validationReport}
            />

            <PlanStatusSection
              validationStatus={result.summary.validation_status}
              checklist={result.readinessChecklist}
            />

            <FeedbackPanel
              feedbackText={feedbackText}
              onFeedbackTextChange={setFeedbackText}
              onSubmit={() => void handleSubmitFeedback()}
              isSubmitting={isSubmittingFeedback}
              successMessage={feedbackSuccessMessage}
              errorMessage={feedbackErrorMessage}
              feedbackHistory={result.feedbackHistory}
            />

            <PendingRequestedChangesSection
              summary={result.pendingFeedbackSummary}
            />

            <VersionHistorySection versionHistory={result.versionHistory} />

            <PlanDiffPreviewSection preview={result.planDiffPreview} />

            <RegenerationReadinessSection
              tripId={result.summary.trip_id}
              readiness={result.regenerationReadiness}
              onRegenerationAttemptsChange={(regenerationAttempts) =>
                setResult((previous) =>
                  previous ? { ...previous, regenerationAttempts } : previous,
                )
              }
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

            <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
              <h2 className="text-lg font-semibold">Day-wise experiences</h2>
              <p className="mt-1 text-[11px] text-slate-500">
                Map links open the scheduled place coordinates only. They are
                not route, travel-time, or booking links.
              </p>
              <p className="mt-1 text-[11px] text-amber-300/90">
                Keep markers are stored for future regeneration. They do not
                change the current plan.
              </p>
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
                    {day.experiences.length === 0 ? (
                      <p className="mt-1 text-sm text-slate-400">
                        No experiences scheduled for this day.
                      </p>
                    ) : (
                      <>
                        <ul className="mt-2 flex flex-col gap-2">
                          {day.experiences.map((experience, index) => (
                            <ScheduledExperienceCard
                              key={experience.experience_id}
                              experience={experience}
                              orderNumber={index + 1}
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
                          ))}
                        </ul>
                        <p className="mt-2 text-[11px] text-slate-500">
                          Scheduled place cards use backend-returned
                          provider-backed fields only. They do not include
                          ratings, prices, opening hours, duration, or route
                          timing yet.
                        </p>
                      </>
                    )}
                    <DayMapPreview experiences={day.experiences} />
                    {day.restaurant_suggestions.length > 0 && (
                      <div className="mt-3">
                        <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                          Nearby restaurant suggestions
                        </p>
                        <p className="mt-1 text-xs text-amber-300/90">
                          Restaurant suggestions are provider-backed location
                          candidates only. They are not reservations,
                          ratings, prices, opening-hours checks, or route
                          recommendations.
                        </p>
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
                    {day.accommodation_suggestions.length > 0 && (
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
                    {day.warnings.map((warning) => (
                      <p
                        key={warning}
                        className="mt-2 text-xs text-amber-300/90"
                      >
                        {warning}
                      </p>
                    ))}
                  </div>
                ))}
              </div>
            </div>

            <StayAreaGuidanceSection guidance={result.stayAreaGuidance} />

            <ResultGroupHeader
              id="review-required"
              title="Why this needs review"
              description="Decision explanations, implementation gaps, readiness checklist, validation report, and assumptions."
            />

            <DecisionSummarySection summary={result.decisionSummary} />

            <ImplementationGapsSection gaps={result.implementationGaps} />

            <ReadinessChecklistSection checklist={result.readinessChecklist} />

            <ValidationSection report={result.validationReport} />

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
          </div>
        )}
      </section>
    </main>
  );
}
