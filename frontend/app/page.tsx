"use client";

import { useEffect, useRef, useState } from "react";
import type * as Leaflet from "leaflet";
import {
  ApiRequestError,
  createTrip,
  generatePlan,
  getDestinationContext,
  getExperiencePlan,
  getProviderCoverage,
  getTripSummary,
  getValidationReport,
} from "@/lib/api";
import type {
  AccommodationSuggestion,
  CandidatePoi,
  ChecklistItemStatus,
  CurrencyContext,
  DailyPlan,
  DecisionSummary,
  ExperienceItem,
  GeoPoint,
  HolidayContext,
  ImplementationGaps,
  ProviderCoverageData,
  ReadinessChecklist,
  RestaurantSuggestion,
  RouteFeasibilityContext,
  StayAreaGuidance,
  TripRequestInput,
  TripSummary,
  ValidationReport,
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
    return "This plan is provider-backed but not travel-ready yet. Use it as a planning draft, not a final itinerary.";
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
 */
function ScheduledExperienceCard({
  experience,
  orderNumber,
}: {
  experience: ExperienceItem;
  orderNumber: number;
}) {
  const hasCoordinates = experience.coordinates !== null;

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

function ProviderCoverageSection({ coverage }: { coverage: ProviderCoverageData }) {
  const coverageEntries = Object.entries(coverage.provider_coverage).filter(
    ([, value]) => value !== null,
  );
  const statusEntries = Object.entries(coverage.provider_status);

  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h2 className="text-lg font-semibold">Provider coverage</h2>

      {coverageEntries.length === 0 ? (
        <p className="mt-2 text-sm text-slate-400">
          No provider coverage information returned.
        </p>
      ) : (
        <dl className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
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

      {statusEntries.length > 0 && (
        <div className="mt-4">
          <p className="text-sm font-semibold text-slate-200">
            Provider status
          </p>
          <ul className="mt-2 flex flex-col gap-2">
            {statusEntries.map(([key, entry]) => (
              <li
                key={key}
                className="rounded-lg border border-white/10 bg-slate-900/60 p-3 text-sm"
              >
                <p className="font-mono text-xs text-slate-300">{key}</p>
                <p className="mt-1 text-[11px] uppercase tracking-wide text-slate-500">
                  {entry.provider_name} · {entry.provider_type}
                </p>
                <p className="mt-1 text-slate-200">
                  {entry.status} · {entry.data_status}
                </p>
                {entry.unavailable_fields.length > 0 && (
                  <p className="mt-1 text-xs text-slate-400">
                    Unavailable: {entry.unavailable_fields.join(", ")}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

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
                <p className="text-slate-200">{item.field}</p>
                <p className="mt-1 text-xs text-slate-400">{item.reason}</p>
                <p className="mt-1 text-[11px] uppercase tracking-wide text-slate-500">
                  {item.data_status}
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
              <li key={source}>{source}</li>
            ))}
          </ul>
        </div>
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

  const [destinationContext, experiencePlan, validationReport, providerCoverage] =
    await Promise.all([
      getDestinationContext(tripId),
      getExperiencePlan(tripId),
      getValidationReport(tripId),
      getProviderCoverage(tripId),
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
  };
}

export default function Home() {
  const [form, setForm] = useState<TripRequestInput>(DEFAULT_TRIP_REQUEST);
  const [interestsText, setInterestsText] = useState("");
  const [mustVisitText, setMustVisitText] = useState("");
  const [constraintsText, setConstraintsText] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [existingTripId, setExistingTripId] = useState("");
  const [isLoadingExisting, setIsLoadingExisting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<PlanResult | null>(null);

  async function handlePlanTrip() {
    setIsLoading(true);
    setError(null);
    setResult(null);

    try {
      const requestBody: TripRequestInput = {
        ...form,
        interests: parseCommaList(interestsText),
        must_visit: parseCommaList(mustVisitText),
        constraints: parseCommaList(constraintsText),
      };
      const { trip_id: tripId } = await createTrip(requestBody);
      await generatePlan(tripId);
      setResult(await loadPlanResult(tripId));
    } catch (err) {
      setError(
        err instanceof ApiRequestError
          ? err.message
          : "Something went wrong while talking to the backend.",
      );
    } finally {
      setIsLoading(false);
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

            <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
              <h2 className="text-lg font-semibold">Day-wise experiences</h2>
              <p className="mt-1 text-[11px] text-slate-500">
                Map links open the scheduled place coordinates only. They are
                not route, travel-time, or booking links.
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
