"use client";

import { useState } from "react";
import {
  ApiRequestError,
  createTrip,
  generatePlan,
  getDestinationContext,
  getExperiencePlan,
  getTripSummary,
} from "@/lib/api";
import type {
  CandidatePoi,
  DailyPlan,
  TripRequestInput,
  TripSummary,
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
};

function readinessLabel(status: string | null): string {
  if (status === "ready") return "Ready";
  if (status === "needs_review") return "Needs Review";
  if (status === "blocked") return "Blocked";
  return "Unknown";
}

function CandidatePoiSection({
  title,
  note,
  pois,
  emptyMessage,
}: {
  title: string;
  note?: string;
  pois: CandidatePoi[];
  emptyMessage: string;
}) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <h2 className="text-lg font-semibold">{title}</h2>
      {note && <p className="mt-1 text-xs text-amber-300/90">{note}</p>}
      {pois.length === 0 ? (
        <p className="mt-2 text-sm text-slate-400">{emptyMessage}</p>
      ) : (
        <ul className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
          {pois.map((poi) => (
            <li
              key={poi.place_id}
              className="rounded-lg border border-white/10 bg-slate-900/60 p-3 text-sm"
            >
              <p className="font-medium">{poi.name}</p>
              <p className="text-xs text-slate-400">
                {poi.category ?? "Uncategorized"}
                {poi.address ? ` · ${poi.address}` : ""}
              </p>
              <p className="mt-1 text-[11px] uppercase tracking-wide text-slate-500">
                {poi.source} · {poi.data_status}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function Home() {
  const [form, setForm] = useState<TripRequestInput>(DEFAULT_TRIP_REQUEST);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<PlanResult | null>(null);

  async function handlePlanTrip() {
    setIsLoading(true);
    setError(null);
    setResult(null);

    try {
      const { trip_id: tripId } = await createTrip(form);
      await generatePlan(tripId);

      const [summary, destinationContext, experiencePlan] = await Promise.all(
        [
          getTripSummary(tripId),
          getDestinationContext(tripId),
          getExperiencePlan(tripId),
        ],
      );

      setResult({
        summary,
        candidatePois: destinationContext.destination_context.candidate_pois,
        candidateRestaurants:
          destinationContext.destination_context.candidate_restaurants,
        candidateAccommodationPois:
          destinationContext.destination_context.candidate_accommodation_pois,
        dailyPlans: experiencePlan.experience_plan.daily_plans,
      });
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

          <button
            type="submit"
            disabled={isLoading}
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

            <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
              <h2 className="text-lg font-semibold">Day-wise experiences</h2>
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
                      <ul className="mt-2 flex flex-col gap-2">
                        {day.experiences.map((experience) => (
                          <li
                            key={experience.experience_id}
                            className="text-sm text-slate-200"
                          >
                            <span className="font-medium">
                              {experience.name}
                            </span>{" "}
                            <span className="text-slate-400">
                              ({experience.category})
                            </span>
                            {experience.why_included && (
                              <p className="text-xs text-slate-400">
                                {experience.why_included}
                              </p>
                            )}
                          </li>
                        ))}
                      </ul>
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

            <CandidatePoiSection
              title="Destination candidate attractions"
              pois={result.candidatePois}
              emptyMessage="No attraction candidates returned."
            />

            <CandidatePoiSection
              title="Destination candidate restaurants"
              pois={result.candidateRestaurants}
              emptyMessage="No restaurant candidates returned."
            />

            <CandidatePoiSection
              title="Destination candidate accommodation POIs"
              note="Open-data location candidates only, not bookable inventory."
              pois={result.candidateAccommodationPois}
              emptyMessage="No accommodation POI candidates returned."
            />
          </div>
        )}
      </section>
    </main>
  );
}
