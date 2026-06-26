import type { TripGenerationResponse, TripRequest } from "../../shared/types";

const API_BASE_URL = "http://localhost:8000";

export class TripGenerationError extends Error {
  status: number | undefined;

  constructor(message: string, status?: number) {
    super(message);
    this.name = "TripGenerationError";
    this.status = status;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isTripGenerationResponse(
  value: unknown,
): value is TripGenerationResponse {
  if (!isRecord(value)) {
    return false;
  }

  return (
    isRecord(value.tripRequest) &&
    isRecord(value.itinerary) &&
    isRecord(value.metadata)
  );
}

function formatValidationDetail(detail: unknown): string | null {
  if (!Array.isArray(detail)) {
    return null;
  }

  const messages = detail
    .map((entry) => {
      if (!isRecord(entry)) {
        return null;
      }

      if (typeof entry.msg === "string") {
        const location = Array.isArray(entry.loc) ? entry.loc.join(".") : null;
        return location ? `${location}: ${entry.msg}` : entry.msg;
      }

      return null;
    })
    .filter((message): message is string => Boolean(message));

  return messages.length > 0 ? messages.join("; ") : null;
}

function getErrorMessage(payload: unknown, status: number): string {
  if (isRecord(payload)) {
    const validationMessage = formatValidationDetail(payload.detail);
    if (validationMessage) {
      return validationMessage;
    }

    if (typeof payload.message === "string") {
      return payload.message;
    }
  }

  return `Trip generation failed with status ${status}.`;
}

export async function generateTrip(
  tripRequest: TripRequest,
): Promise<TripGenerationResponse> {
  let response: Response;

  try {
    response = await fetch(`${API_BASE_URL}/api/trips/generate`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(tripRequest),
    });
  } catch {
    throw new TripGenerationError(
      "Unable to reach the trip generation API. Make sure the backend is running on http://localhost:8000.",
    );
  }

  const payload: unknown = await response.json().catch(() => null);

  if (!response.ok) {
    throw new TripGenerationError(
      getErrorMessage(payload, response.status),
      response.status,
    );
  }

  if (!isTripGenerationResponse(payload)) {
    throw new TripGenerationError(
      "The backend returned an unexpected trip response shape.",
      response.status,
    );
  }

  return payload;
}
