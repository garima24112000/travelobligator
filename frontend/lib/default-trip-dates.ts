// Default dates for the create-trip form, relative to the user's LOCAL
// calendar date -- never a hardcoded date that goes stale.
//
// Everything here works on local calendar fields (year / month / day). It
// never goes through `toISOString()` or any other UTC conversion, which can
// shift the visible date by a day for users away from UTC.
//
// Kept free of React and of runtime imports so it can be unit tested with
// Node's built-in test runner (frontend/tests).

/** Days from today to the default start date. */
export const DEFAULT_TRIP_START_OFFSET_DAYS = 7;
/** Nights in the default trip: 2 nights = 3 calendar days. */
export const DEFAULT_TRIP_NIGHTS = 2;

/** `YYYY-MM-DD` from a Date's local calendar fields (the `<input type="date">` value format). */
export function formatLocalDate(date: Date): string {
  const year = String(date.getFullYear()).padStart(4, "0");
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

/**
 * The local calendar date `days` after `date`. Built from calendar fields at
 * local noon, so month / year roll-over is handled by the Date constructor
 * and a daylight-saving change can never move the result to another day.
 */
export function addLocalDays(date: Date, days: number): Date {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate() + days, 12);
}

export function defaultTripDates(today: Date = new Date()): {
  start_date: string;
  end_date: string;
} {
  const start = addLocalDays(today, DEFAULT_TRIP_START_OFFSET_DAYS);
  const end = addLocalDays(start, DEFAULT_TRIP_NIGHTS);
  return { start_date: formatLocalDate(start), end_date: formatLocalDate(end) };
}
