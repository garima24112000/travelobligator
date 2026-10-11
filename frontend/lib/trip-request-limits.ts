// Size limits of a NEW trip request, checked in the form before anything is sent.
//
// The backend is the authority (`backend/app/schemas/trips.py` `TripCreateRequest`, configured by
// MAX_TRIP_DAYS / TRIP_REQUEST_MAX_LIST_ITEMS / TRIP_REQUEST_MAX_ITEM_LENGTH) and answers HTTP 422 for
// anything this misses. These constants mirror its documented defaults so the traveller is told what
// to change without a round trip; the wording matches the backend's.
//
// Kept free of React and of runtime imports so it can be unit tested with Node's built-in test runner
// (frontend/tests).

export const MAX_TRIP_DAYS = 7;
export const MAX_LIST_ITEMS = 20;
export const MAX_ITEM_LENGTH = 120;

type CalendarDate = { year: number; month: number; day: number };

function parseCalendarDate(value: string): CalendarDate | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return null;
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])];
  const check = new Date(Date.UTC(year, month - 1, day));
  const real =
    check.getUTCFullYear() === year && check.getUTCMonth() === month - 1 && check.getUTCDate() === day;
  return real ? { year, month, day } : null;
}

// Pure calendar arithmetic on `YYYY-MM-DD` values: UTC is used only as a day counter, so the
// viewer's timezone and daylight-saving changes cannot move a date.
function dayNumber(date: CalendarDate): number {
  return Math.round(Date.UTC(date.year, date.month - 1, date.day) / 86_400_000);
}

/** Calendar days of a trip INCLUDING the first and the last day; null when a date is not valid. */
export function tripLengthDays(startDate: string, endDate: string): number | null {
  const start = parseCalendarDate(startDate);
  const end = parseCalendarDate(endDate);
  if (!start || !end) return null;
  return dayNumber(end) - dayNumber(start) + 1;
}

/** The last end date the limit allows for `startDate` (`YYYY-MM-DD`), for the date input's `max`. */
export function latestEndDate(startDate: string): string | undefined {
  const start = parseCalendarDate(startDate);
  if (!start) return undefined;
  const last = new Date((dayNumber(start) + MAX_TRIP_DAYS - 1) * 86_400_000);
  const pad = (value: number, width: number) => String(value).padStart(width, "0");
  return `${pad(last.getUTCFullYear(), 4)}-${pad(last.getUTCMonth() + 1, 2)}-${pad(last.getUTCDate(), 2)}`;
}

export type TripRequestLimitInput = {
  start_date: string;
  end_date: string;
  interests?: string[];
  must_visit?: string[];
  constraints?: string[];
};

const LIST_LABELS: ["interests" | "must_visit" | "constraints", string][] = [
  ["interests", "interests"],
  ["must_visit", "must-visit places"],
  ["constraints", "constraints"],
];

/**
 * The first thing the traveller has to change before this request can be sent, or null when it is
 * within every limit. One short, actionable sentence; no field name, code or internal term.
 */
export function tripRequestLimitProblem(input: TripRequestLimitInput): string | null {
  const days = tripLengthDays(input.start_date, input.end_date);
  if (days !== null && days < 1) {
    return "The end date must be on or after the start date.";
  }
  if (days !== null && days > MAX_TRIP_DAYS) {
    return (
      `A trip can be at most ${MAX_TRIP_DAYS} days long, counting the first and the last day. ` +
      "Choose an earlier end date."
    );
  }
  for (const [field, label] of LIST_LABELS) {
    const items = input[field] ?? [];
    if (items.length > MAX_LIST_ITEMS) {
      return `Please list at most ${MAX_LIST_ITEMS} ${label}. You entered ${items.length}.`;
    }
    if (items.some((item) => item.length > MAX_ITEM_LENGTH)) {
      return `Each entry in ${label} can be at most ${MAX_ITEM_LENGTH} characters long. Shorten the long one, or split it with commas.`;
    }
  }
  return null;
}
