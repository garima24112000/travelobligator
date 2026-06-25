"use client";

import { useState } from "react";

import type {
  AccommodationRecommendation,
  Itinerary,
  TripGenerationResponse,
  TripRequest,
} from "../../shared/types";
import { TravelCopilotShell } from "./travel-copilot-shell";
import {
  createTripRequestFormState,
  TripPreferenceForm,
  tripRequestFormStateToTripRequest,
  type TripRequestFormState,
} from "./trip-preference-form";
import { generateTrip, TripGenerationError } from "../lib/trip-api";

type QuickAction = {
  label: string;
  description: string;
};

type TripDashboardClientProps = {
  initialTripRequest: TripRequest;
  initialItinerary: Itinerary;
  initialAccommodationRecommendations: AccommodationRecommendation[];
  initialTransportRecommendations: string[];
  quickActions: QuickAction[];
};

function formatBudgetRange(min?: number, max?: number) {
  if (typeof min === "number" && typeof max === "number") {
    return `${min.toLocaleString()} - ${max.toLocaleString()} USD`;
  }

  if (typeof min === "number") {
    return `From ${min.toLocaleString()} USD`;
  }

  if (typeof max === "number") {
    return `Up to ${max.toLocaleString()} USD`;
  }

  return "Budget not set";
}

function buildTransportRecommendations(itinerary: Itinerary): string[] {
  return [itinerary.transportStrategy.localTransport, ...itinerary.transportStrategy.rationale];
}

export function TripDashboardClient({
  initialTripRequest,
  initialItinerary,
  initialAccommodationRecommendations,
  initialTransportRecommendations,
  quickActions,
}: TripDashboardClientProps) {
  const [formState, setFormState] = useState<TripRequestFormState>(() =>
    createTripRequestFormState(initialTripRequest),
  );
  const [generatedResponse, setGeneratedResponse] = useState<TripGenerationResponse | null>(null);
  const [isGenerating, setIsGenerating] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const displayItinerary = generatedResponse?.itinerary ?? initialItinerary;
  const displayTripRequest = generatedResponse?.tripRequest ?? initialTripRequest;
  const displayAccommodationRecommendations =
    generatedResponse?.itinerary.stayRecommendation.topAccommodations ?? initialAccommodationRecommendations;
  const displayTransportRecommendations =
    generatedResponse ? buildTransportRecommendations(generatedResponse.itinerary) : initialTransportRecommendations;
  const statusLabel = generatedResponse ? "Generated itinerary loaded" : "Demo fallback preview";
  const statusTone = generatedResponse
    ? "text-emerald-100 border-emerald-300/20 bg-emerald-300/10"
    : "text-amber-100 border-amber-300/20 bg-amber-300/10";
  const statusDescription = generatedResponse
    ? `Received from backend at ${new Date(generatedResponse.metadata.generatedAt).toLocaleString()}.`
    : "The dashboard is showing the current mock itinerary until a backend response is generated.";

  async function handleGenerateTrip() {
    if (isGenerating) {
      return;
    }

    setIsGenerating(true);
    setErrorMessage(null);

    try {
      const request = tripRequestFormStateToTripRequest(formState);
      const response = await generateTrip(request);
      setGeneratedResponse(response);
    } catch (error) {
      const message =
        error instanceof TripGenerationError
          ? error.message
          : error instanceof Error
            ? error.message
            : "Trip generation failed.";
      setErrorMessage(message);
    } finally {
      setIsGenerating(false);
    }
  }

  return (
    <>
      <div className="mx-auto mt-6 max-w-7xl px-4 sm:px-6 lg:px-8">
        <div className={`rounded-[1.75rem] border px-5 py-4 text-sm ${statusTone}`}>
          <p className="font-semibold uppercase tracking-[0.22em]">{statusLabel}</p>
          <p className="mt-1 leading-6">{statusDescription}</p>
        </div>
      </div>

      <div className="mx-auto mt-6 max-w-7xl px-4 sm:px-6 lg:px-8">
        <TripPreferenceForm
          value={formState}
          isSubmitting={isGenerating}
          onChange={setFormState}
          onSubmit={(event) => {
            event.preventDefault();
            void handleGenerateTrip();
          }}
        />
      </div>

      {errorMessage ? (
        <div className="mx-auto mt-6 max-w-7xl px-4 sm:px-6 lg:px-8">
          <div className="rounded-[1.75rem] border border-rose-300/20 bg-rose-400/10 px-5 py-4 text-sm text-rose-100">
            <p className="font-semibold">Trip generation failed</p>
            <p className="mt-1 leading-6">{errorMessage}</p>
          </div>
        </div>
      ) : null}

      {isGenerating ? (
        <div className="mx-auto mt-6 max-w-7xl px-4 sm:px-6 lg:px-8">
          <div className="rounded-[1.75rem] border border-amber-300/20 bg-amber-300/10 px-5 py-4 text-sm text-amber-50">
            Generating itinerary from the backend now. The dashboard below stays on the current fallback until the response arrives.
          </div>
        </div>
      ) : null}

      <TravelCopilotShell
        itinerary={displayItinerary}
        preferences={{
          destination: displayTripRequest.destination,
          dates: `${displayTripRequest.startDate} to ${displayTripRequest.endDate}`,
          travelers: `${displayTripRequest.travelersCount} travelers · ${displayTripRequest.travelGroupType}`,
          groupType: displayTripRequest.travelGroupType,
          pace: displayTripRequest.pace,
          accommodationType: displayTripRequest.accommodationType,
          transportPreference: displayTripRequest.transportPreference,
          budget: formatBudgetRange(displayTripRequest.budgetMin, displayTripRequest.budgetMax),
          interests: displayTripRequest.interests,
          mustVisit: displayTripRequest.mustVisit,
          mustAvoid: displayTripRequest.mustAvoid,
          notes: displayTripRequest.freeTextPreferences ?? "",
        }}
        accommodationRecommendations={displayAccommodationRecommendations}
        transportRecommendations={displayTransportRecommendations}
        quickActions={quickActions}
      />
    </>
  );
}