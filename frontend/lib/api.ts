import type {
  AICandidatePromotionData,
  AICandidateReviewData,
  ApiResponse,
  DestinationContextData,
  ExperiencePlanData,
  GenerationProgressData,
  ProviderCoverageData,
  RegenerateRequestInput,
  RegenerateResponseData,
  RegenerationAttemptsData,
  RegenerationReadinessData,
  TripCreateData,
  TripData,
  TripRequestInput,
  TripSummary,
  ValidationReportData,
} from "./types";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export class ApiRequestError extends Error {
  constructor(
    message: string,
    public status: number,
    // The backend's own ApiError.code (e.g. "REGENERATION_BLOCKED_BY_LOCKS"),
    // or null when the response body carried no error entry at all (e.g. a
    // network-level failure surfaced some other way). Callers that need to
    // distinguish refusal reasons (not just show a message) read this
    // instead of parsing the message text.
    public code: string | null = null,
  ) {
    super(message);
    this.name = "ApiRequestError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });

  const body = (await response.json()) as ApiResponse<T>;

  if (!response.ok || !body.success || body.data === null) {
    const message =
      body.errors[0]?.message ?? body.message ?? "The request failed.";
    throw new ApiRequestError(message, response.status, body.errors[0]?.code ?? null);
  }

  return body.data;
}

export function createTrip(input: TripRequestInput): Promise<TripCreateData> {
  return request<TripCreateData>("/trips", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function generatePlan(tripId: string): Promise<unknown> {
  return request(`/trips/${tripId}/generate`, { method: "POST" });
}

export function getTripSummary(tripId: string): Promise<TripSummary> {
  return request<TripSummary>(`/trips/${tripId}/summary`);
}

export function getDestinationContext(
  tripId: string,
): Promise<DestinationContextData> {
  return request<DestinationContextData>(
    `/trips/${tripId}/destination-context`,
  );
}

export function getExperiencePlan(
  tripId: string,
): Promise<ExperiencePlanData> {
  return request<ExperiencePlanData>(`/trips/${tripId}/experience-plan`);
}

export function getValidationReport(
  tripId: string,
): Promise<ValidationReportData> {
  return request<ValidationReportData>(`/trips/${tripId}/validation-report`);
}

export function getProviderCoverage(
  tripId: string,
): Promise<ProviderCoverageData> {
  return request<ProviderCoverageData>(`/trips/${tripId}/provider-coverage`);
}

export function getTrip(tripId: string): Promise<TripData> {
  return request<TripData>(`/trips/${tripId}`);
}

export function submitTripFeedback(
  tripId: string,
  feedbackText: string,
): Promise<TripData> {
  return request<TripData>(`/trips/${tripId}/feedback`, {
    method: "POST",
    body: JSON.stringify({ feedback_text: feedbackText }),
  });
}

export function createTripLock(
  tripId: string,
  lockedItemType: string,
  lockedItemId: string,
  reason?: string,
): Promise<TripData> {
  return request<TripData>(`/trips/${tripId}/locks`, {
    method: "POST",
    body: JSON.stringify({
      locked_item_type: lockedItemType,
      locked_item_id: lockedItemId,
      ...(reason !== undefined ? { reason } : {}),
    }),
  });
}

export function deleteTripLock(
  tripId: string,
  lockId: string,
): Promise<TripData> {
  return request<TripData>(`/trips/${tripId}/locks/${lockId}`, {
    method: "DELETE",
  });
}

export function getRegenerationReadiness(
  tripId: string,
): Promise<RegenerationReadinessData> {
  return request<RegenerationReadinessData>(
    `/trips/${tripId}/regeneration-readiness`,
  );
}

// Requests real regeneration (backend: app.api.routes.trips.
// regenerate_trip_plan, Step 174B-174D). Always sends `confirm: true` --
// the backend itself decides whether that succeeds (a generated plan,
// pending feedback, zero active locks, and a real derivable affected
// stage all being required) or refuses with a specific error code
// (REGENERATION_BLOCKED_BY_LOCKS, REGENERATION_NO_PENDING_FEEDBACK, or
// REGENERATION_NOT_AVAILABLE). Callers should only ever call this when
// `RegenerationReadiness.can_regenerate` is already `true` -- the backend
// re-checks every condition itself regardless, so this call is never the
// sole safety gate. On success this resolves with the real
// `RegenerateResponseData`; on refusal it throws `ApiRequestError` (read
// `.code`/`.message`) exactly like every other endpoint here.
export function requestRegeneration(
  tripId: string,
): Promise<RegenerateResponseData> {
  const body: RegenerateRequestInput = {
    confirm: true,
    scope: "affected_stages",
  };
  return request<RegenerateResponseData>(`/trips/${tripId}/regenerate`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getRegenerationAttempts(
  tripId: string,
): Promise<RegenerationAttemptsData> {
  return request<RegenerationAttemptsData>(
    `/trips/${tripId}/regeneration-attempts`,
  );
}

// Read-only backend pipeline stage-progress readout (Step 163B/163C). Never
// triggers generation and never mutates state -- see GenerationProgress in
// ./types for why this is never flight tracking or real travel progress.
export function getGenerationProgress(
  tripId: string,
): Promise<GenerationProgressData> {
  return request<GenerationProgressData>(
    `/trips/${tripId}/generation-progress`,
  );
}

// Read-only AI candidate discovery/grounding/eligibility review (Step
// 170A/170B). Never triggers new AI candidate discovery, never calls a
// provider/LLM, and never mutates PlanningState -- see
// AICandidateReviewReport in ./types.
export function getAiCandidateReview(
  tripId: string,
): Promise<AICandidateReviewData> {
  return request<AICandidateReviewData>(
    `/trips/${tripId}/ai-candidate-review`,
  );
}

// Materializes already-computed Step 170B eligibility verdicts into
// PlanningState.ai_candidate_promotion_report (Step 170C). Never calls a
// provider/LLM/AI candidate proposal provider itself, and never schedules
// anything into the itinerary directly -- scheduling is decided entirely
// by the backend's existing ExperiencePlannerService rules (Step 170D).
export function promoteAiCandidates(
  tripId: string,
): Promise<AICandidatePromotionData> {
  return request<AICandidatePromotionData>(
    `/trips/${tripId}/ai-candidate-promotions`,
    { method: "POST" },
  );
}
