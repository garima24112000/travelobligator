import type {
  ActivateBranchResponse,
  AICandidatePromotionData,
  AICandidateReviewData,
  ApiResponse,
  AuthResponse,
  CreateForkRequest,
  CreateForkResponse,
  DestinationContextData,
  ExperiencePlanData,
  GenerateOrJobResponse,
  GenerationProgressData,
  JobListResponseData,
  ItineraryBranchListData,
  ItineraryRevisionComparison,
  ItineraryRevisionDetail,
  ItineraryRevisionListData,
  JobResponseData,
  ProviderCoverageData,
  RegenerateOrJobResponse,
  RegenerateRequestInput,
  RegenerationAttemptsData,
  RegenerationReadinessData,
  TripCreateData,
  TripData,
  TripListResponseData,
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
    // Step 187G: the backend's own `metadata.request_id` for this failed
    // call (falling back to the `X-Request-Id` response header if the body
    // was unreadable), or null when neither was available -- e.g. a
    // network-level failure with no response at all, or a call site that
    // constructs this error itself from job-poll state rather than a raw
    // fetch response. A correlation label only, safe to show in the UI
    // ("Request ID: req_...") to help match up a Developer Mode diagnostic
    // entry with the matching backend structured log line -- never a
    // session token, never proof of identity.
    public requestId: string | null = null,
  ) {
    super(message);
    this.name = "ApiRequestError";
  }
}

// Step 184E: `credentials: "include"` sends/receives the backend's signed,
// HttpOnly session cookie on every request -- this is the only place a
// session is ever attached. There is no token to read or store on the
// frontend side (no localStorage, no Authorization header) -- the browser
// owns the cookie entirely.
async function request<T>(
  path: string,
  init?: RequestInit,
  options?: { allowNullData?: boolean },
): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    ...init,
  });

  const body = (await response.json()) as ApiResponse<T>;

  if (
    !response.ok ||
    !body.success ||
    (body.data === null && !options?.allowNullData)
  ) {
    const message =
      body.errors[0]?.message ?? body.message ?? "The request failed.";
    // Step 187G: prefer the body's own `metadata.request_id` (always
    // present on a real backend response) and fall back to the
    // `X-Request-Id` response header (readable cross-origin only because
    // `app.main` now exposes it via CORS `expose_headers`) -- covers the
    // rare case of a response body that parsed but happened to omit
    // metadata (e.g. a hand-built test fixture).
    const requestId =
      body.metadata?.request_id ?? response.headers.get("X-Request-Id");
    throw new ApiRequestError(
      message,
      response.status,
      body.errors[0]?.code ?? null,
      requestId,
    );
  }

  return body.data as T;
}

export function createTrip(input: TripRequestInput): Promise<TripCreateData> {
  return request<TripCreateData>("/trips", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

// Step 186C/D: returns the full sync `{trip_id, planning_state}` shape
// (200) with the default `ASYNC_GENERATION_ENABLED=false`, or a
// `StartJobResponseData` job envelope (202) when the backend has async
// generation enabled. Callers must check `isStartJobResponseData(...)`
// (app/page.tsx) before assuming either shape -- this function itself
// makes no assumption and performs no polling; that's the caller's job.
export function generatePlan(tripId: string): Promise<GenerateOrJobResponse> {
  return request<GenerateOrJobResponse>(`/trips/${tripId}/generate`, {
    method: "POST",
  });
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
// regenerate_trip_plan, Step 174B-174D, async job path Step 186C).
// Always sends `confirm: true` -- the backend itself decides whether that
// succeeds (a generated plan, pending feedback, zero active locks, and a
// real derivable affected stage all being required) or refuses with a
// specific error code (REGENERATION_BLOCKED_BY_LOCKS,
// REGENERATION_NO_PENDING_FEEDBACK, or REGENERATION_NOT_AVAILABLE) --
// those refusal checks always run synchronously regardless of async mode.
// Callers should only ever call this when
// `RegenerationReadiness.can_regenerate` is already `true` -- the backend
// re-checks every condition itself regardless, so this call is never the
// sole safety gate. On success this resolves with either the real
// `RegenerateResponseData` (200, sync/default) or a `StartJobResponseData`
// job envelope (202, async mode) -- see `isStartJobResponseData` in
// app/page.tsx. On refusal (including `JOB_ALREADY_RUNNING`) it throws
// `ApiRequestError` (read `.code`/`.message`) exactly like every other
// endpoint here.
export function requestRegeneration(
  tripId: string,
): Promise<RegenerateOrJobResponse> {
  const body: RegenerateRequestInput = {
    confirm: true,
    scope: "affected_stages",
  };
  return request<RegenerateOrJobResponse>(`/trips/${tripId}/regenerate`, {
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

// Async job foundation (Step 186B/C, backend: app.models.generation_job.
// GenerationJob). Read-only, owner-protected -- never triggers a job and
// never mutates PlanningState. Only ever returns non-empty/real data when
// the backend has `ASYNC_GENERATION_ENABLED=true` and at least one
// generate/regenerate call has actually created a job for this trip;
// with the default sync mode, `getTripJobs` always resolves to an empty
// list since nothing ever creates a job.
export function getTripJobs(tripId: string): Promise<JobListResponseData> {
  return request<JobListResponseData>(`/trips/${tripId}/jobs`);
}

export function getTripJob(
  tripId: string,
  jobId: string,
): Promise<JobResponseData> {
  return request<JobResponseData>(`/trips/${tripId}/jobs/${jobId}`);
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

// Auth (Step 184E; backend: app.api.routes.auth). Every call here goes
// through the shared `request()` helper above, so the session cookie set
// by signup/login is sent back automatically on every later call -- there
// is nothing else for the caller to attach or store.
export function signup(email: string, password: string): Promise<AuthResponse> {
  return request<AuthResponse>("/auth/signup", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export function login(email: string, password: string): Promise<AuthResponse> {
  return request<AuthResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

// The backend's logout response carries no data payload (`data: null` on
// success) -- `allowNullData` tells the shared helper not to treat that as
// a failure, unlike every other endpoint here.
export async function logout(): Promise<void> {
  await request<null>(
    "/auth/logout",
    { method: "POST" },
    { allowNullData: true },
  );
}

export function getCurrentUser(): Promise<AuthResponse> {
  return request<AuthResponse>("/auth/me");
}

export function listTrips(): Promise<TripListResponseData> {
  return request<TripListResponseData>("/trips");
}

// Section 199A/199B/199C: itinerary branches. Every call goes through the
// shared `request()` helper so a failure keeps the backend's structured
// error `code` (BRANCH_SWITCH_BLOCKED, BRANCH_STATE_CONFLICT,
// BRANCH_NAME_CONFLICT, REVISION_SNAPSHOT_UNAVAILABLE, ...) on
// `ApiRequestError.code`. Identity is always branch_id/revision_id.
export function listBranches(tripId: string): Promise<ItineraryBranchListData> {
  return request<ItineraryBranchListData>(`/trips/${tripId}/branches`);
}

export function listBranchRevisions(
  tripId: string,
  branchId: string,
): Promise<ItineraryRevisionListData> {
  return request<ItineraryRevisionListData>(
    `/trips/${tripId}/branches/${encodeURIComponent(branchId)}/revisions`,
  );
}

export function getRevisionDetail(
  tripId: string,
  revisionId: string,
): Promise<ItineraryRevisionDetail> {
  return request<ItineraryRevisionDetail>(
    `/trips/${tripId}/revisions/${encodeURIComponent(revisionId)}`,
  );
}

export function createBranch(
  tripId: string,
  input: CreateForkRequest,
): Promise<CreateForkResponse> {
  return request<CreateForkResponse>(`/trips/${tripId}/branches`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function activateBranch(
  tripId: string,
  branchId: string,
): Promise<ActivateBranchResponse> {
  return request<ActivateBranchResponse>(
    `/trips/${tripId}/branches/${encodeURIComponent(branchId)}/activate`,
    { method: "POST" },
  );
}

export function compareRevisions(
  tripId: string,
  leftRevisionId: string,
  rightRevisionId: string,
): Promise<ItineraryRevisionComparison> {
  const params = new URLSearchParams({
    left_revision_id: leftRevisionId,
    right_revision_id: rightRevisionId,
  });
  return request<ItineraryRevisionComparison>(
    `/trips/${tripId}/revisions/compare?${params.toString()}`,
  );
}
