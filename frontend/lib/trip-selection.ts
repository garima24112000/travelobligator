// Section 202B.3: the only thing persisted across a browser reload is WHICH
// trip is open -- a trip id in the page URL (`?trip=...`). Never a
// PlanningState, plan content, token or user identity. The id is a hint,
// not authorization: on restore the app always fetches the trip through the
// authenticated API, and the backend decides (401/403/404 clear the hint).

const TRIP_PARAM = "trip";
const TRIP_ID_PATTERN = /^[A-Za-z0-9_-]{1,100}$/;

export function readSelectedTripId(): string | null {
  try {
    const value = new URLSearchParams(window.location.search).get(TRIP_PARAM);
    return value && TRIP_ID_PATTERN.test(value) ? value : null;
  } catch {
    return null;
  }
}

export function writeSelectedTripId(tripId: string | null): void {
  try {
    const url = new URL(window.location.href);
    if (tripId === null) {
      if (!url.searchParams.has(TRIP_PARAM)) return;
      url.searchParams.delete(TRIP_PARAM);
    } else {
      if (url.searchParams.get(TRIP_PARAM) === tripId) return;
      url.searchParams.set(TRIP_PARAM, tripId);
    }
    window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  } catch {
    // History API unavailable: the app still works, it just won't survive a reload.
  }
}
