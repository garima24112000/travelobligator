import type {
  ApiResponse,
  DestinationContextData,
  ExperiencePlanData,
  TripCreateData,
  TripRequestInput,
  TripSummary,
} from "./types";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export class ApiRequestError extends Error {
  constructor(
    message: string,
    public status: number,
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
    throw new ApiRequestError(message, response.status);
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
