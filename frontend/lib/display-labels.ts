// Section 202B.3: the ONE place Traveler-view wording for backend
// identifiers lives. Backend enum names, provider keys, internal stage
// names and dotted PlanningState paths are developer vocabulary; Traveler
// view must never print them raw. Developer view keeps showing the raw
// values (it does not import this module for its own diagnostics).
//
// Nothing here adds a fact: every label is a rename of an identifier the
// backend already sent, and an unknown identifier degrades to a readable
// humanized form rather than being hidden or guessed at.

import type { ValidationIssue } from "./types";

const SOURCE_LABELS: Record<string, string> = {
  openstreetmap_places: "OpenStreetMap",
  openstreetmap: "OpenStreetMap",
  overpass: "OpenStreetMap",
  nominatim: "OpenStreetMap",
  geoapify: "Geoapify",
  geoapify_places: "Geoapify",
  geoapify_routing: "Geoapify routing",
  open_meteo: "Open-Meteo",
  nager_date: "Nager.Date",
  frankfurter: "Frankfurter",
  osrm: "OSRM routing",
  routing_provider: "Routing data",
  routes_provider: "Routing data",
  transit_provider: "Transit data",
  places_provider: "Place data",
  weather_provider: "Weather data",
  holiday_provider: "Holiday data",
  flight_provider: "Flight data",
  kiwi_mcp: "Kiwi",
  kiwi_mcp_flight_provider: "Kiwi",
  kiwi_manual: "Kiwi (manually supplied)",
  scraped_local: "Local file",
  scraped_accommodation_provider: "Local accommodation file",
  scraped_flight_provider: "Local flight file",
  hotel_ratings_provider: "Hotel ratings",
  ai_candidate_promotion: "Place data",
  user_requested_new_place: "Your request",
  trip_request: "Your request",
};

// Section 2B: what a data source is called INSIDE a Traveler sentence -- the
// kind of data, never the provider's name or key.
const TRAVELER_SOURCE_WORDS: Record<string, string> = {
  openstreetmap_places: "map data",
  openstreetmap: "map data",
  overpass: "map data",
  nominatim: "map data",
  geoapify: "map data",
  geoapify_places: "map data",
  geoapify_geocoding: "map data",
  geoapify_routing: "routing data",
  osrm: "routing data",
  open_meteo: "weather data",
  nager_date: "holiday data",
  frankfurter: "exchange-rate data",
  kiwi_mcp: "flight data",
  kiwi_mcp_flight_provider: "flight data",
  kiwi_manual: "flight data",
  scraped_local: "a saved file",
  scraped_accommodation_provider: "a saved file",
  scraped_flight_provider: "a saved file",
  routing_provider: "routing data",
  routes_provider: "routing data",
  transit_provider: "public transport data",
  places_provider: "place data",
  weather_provider: "weather data",
  holiday_provider: "holiday data",
  flight_provider: "flight data",
  hotel_ratings_provider: "hotel rating data",
  ai_candidate_promotion: "place data",
  user_requested_new_place: "your request",
  trip_request: "your request",
  groq: "the AI service",
  anthropic: "the AI service",
};

// Provider names as backend sentences spell them.
const PROVIDER_NAME_REPLACEMENTS: [RegExp, string][] = [
  [/\b(?:Geoapify Routing|OSRM)(?: routing)?\b/gi, "routing data"],
  [/\b(?:OpenStreetMap|Overpass|Nominatim|Geoapify)(?: (?:Places|Geocoding))?\b/g, "map data"],
  [/\bOpen-Meteo\b/gi, "weather data"],
  [/\bNager\.Date\b/gi, "holiday data"],
  [/\bFrankfurter\b/g, "exchange-rate data"],
  [/\bKiwi(?:\.com| MCP)?\b/g, "flight data"],
  [/\b(?:Groq|Anthropic)\b/g, "the AI service"],
];

// Identifier fragments that are pure internals: when they appear inside a
// longer backend sentence they are replaced with a plain phrase (or
// removed) instead of shown verbatim.
const TEXT_REPLACEMENTS: [RegExp, string][] = [
  [/\s*\(see ai_candidate_promotion_report\)/gi, ""],
  [/\bai_candidate_promotion_report(?:\.[a-z_]+)*/gi, "the place-check step"],
  [/destination_context\.candidate_restaurants/gi, "nearby restaurant data"],
  [/destination_context\.candidate_pois/gi, "nearby place data"],
  [/destination_context\.candidate_accommodation_pois/gi, "nearby stay data"],
  [/straight-line \(haversine\)/gi, "straight-line"],
  [/\(haversine\)/gi, ""],
  [/\bhaversine\b/gi, "straight-line"],
  [/\bprovider-grounded\b/gi, "verified"],
  [/\bPOIs\b/g, "places"],
  [/\bPOI\b/g, "place"],
  // Section 2: planner vocabulary. Multi-word phrases match in any case;
  // single common words match lower case only, so a place name such as
  // "Bike Repair Café" inside a sentence is never rewritten.
  [/\bAI itinerary reasoning rationale for this day:\s*/gi, ""],
  [/\bAI itinerary reasoning\b/gi, "AI trip planning"],
  [/\broute[- ]burden repair\b/gi, "travel-time adjustment"],
  [/\broute[- ]burden\b/gi, "travel time"],
  [/\bfallback pass\b/gi, "second pass"],
  [/\bprovider identity\b/gi, "place record"],
  [/\bquality-approved\b/gi, "suitable"],
  [/\bdeterministic(?:ally)? /g, ""],
  [/\bgrounded\b/g, "verified"],
  [/\bgrounding\b/g, "verification"],
  [/\b(attraction|restaurant|place) candidates?\b/g, "$1 options"],
  [/\bcandidate (attractions|restaurants|places)\b/g, "$1"],
  [/\bcandidate\(s\)/g, "option(s)"],
  [/\bcandidates\b/g, "options"],
  [/\bcandidate\b/g, "option"],
  [/^Candidates\b/, "Options"],
  [/\breplacements\b/g, "alternatives"],
  [/\breplacement\b/g, "alternative"],
  [/\brepaired\b/g, "adjusted"],
  [/\brepairs?\b/g, "adjustment"],
];

// Planner / pipeline vocabulary that must never reach a traveler. Used to
// decide whether AI-written prose (a narrator summary, a day rationale) is
// shown at all: prose describing planner operations is dropped in favour of
// the factual itinerary rather than reworded.
const PLANNER_WORDING: RegExp[] = [
  /AI itinerary reasoning/i,
  /\brepair(?:ed|s|ing)?\b/i,
  /\bfallback\b/i,
  /\bcandidates?\b/i,
  /\bprovider identity\b/i,
  /\bground(?:ed|ing)\b/i,
  /\bmoved\b.{0,80}\bfrom day\b/i,
  /\breplacements?\b/i,
  /\broute[- ]burden\b/i,
  /\bdeterministic/i,
  /\b[a-z]+(?:_[a-z0-9]+)+\b/,
  /\b[A-Z]{2,}(?:_[A-Z0-9]+)+\b/,
];

/** True when text carries planner-operation wording or a raw identifier. */
export function hasPlannerWording(text: string): boolean {
  return PLANNER_WORDING.some((pattern) => pattern.test(text));
}

/**
 * AI-written prose for Traveler view: the text in plain wording, or null
 * when it describes planner operations (the caller then shows the factual
 * itinerary instead).
 */
export function travelerProse(text: string | null | undefined): string | null {
  if (!text || text.trim().length === 0) return null;
  if (hasPlannerWording(text)) return null;
  return travelerText(text);
}

/** "tourist_attraction" -> "tourist attraction". */
export function humanizeIdentifier(value: string): string {
  return value.replace(/[_]+/g, " ").replace(/\s+/g, " ").trim();
}

/** Friendly source/provider name; unknown keys are humanized, never raw. */
export function sourceLabel(source: string | null | undefined): string | null {
  if (!source) return null;
  const known = SOURCE_LABELS[source.toLowerCase()];
  if (known) return known;
  return humanizeIdentifier(source);
}

/** "Place data: OpenStreetMap" */
export function placeDataLabel(source: string | null | undefined): string | null {
  const label = sourceLabel(source);
  return label ? `Place data: ${label}` : null;
}

export function routingDataLabel(available: boolean): string {
  return available ? "Routing data available" : "Routing data unavailable";
}

/**
 * Rewrites internal identifiers inside a backend sentence into plain
 * wording. Any leftover snake_case token (a raw enum/field name) is
 * humanized so an identifier never reaches a traveler verbatim.
 */
export function travelerText(text: string): string {
  let out = text;
  for (const [pattern, replacement] of TEXT_REPLACEMENTS) {
    out = out.replace(pattern, replacement);
  }
  // "(via osrm)" style attributions name a provider and add nothing for a
  // traveler: a parenthesised one is removed, an inline one keeps only the
  // kind of data.
  out = out.replace(/\s*\((?:via|from|by) ([a-z][a-z0-9_]*)\)/g, (whole, key: string) =>
    TRAVELER_SOURCE_WORDS[key] ? "" : whole,
  );
  out = out.replace(/\b(via|from|by) ([a-z][a-z0-9_]*)\b/g, (whole, word: string, key: string) => {
    const label = TRAVELER_SOURCE_WORDS[key];
    return label ? `${word} ${label}` : whole;
  });
  out = out.replace(/\b([a-z]+(?:_[a-z0-9]+)+)\b/g, (token) => {
    return TRAVELER_SOURCE_WORDS[token] ?? humanizeIdentifier(token);
  });
  for (const [pattern, replacement] of PROVIDER_NAME_REPLACEMENTS) {
    out = out.replace(pattern, replacement);
  }
  // "Provider-backed" is developer vocabulary for "came from a data source";
  // dropping the qualifier never adds a claim.
  out = out.replace(/\b([Pp])rovider-backed\s+(\w)/g, (_whole, first: string, next: string) =>
    first === "P" ? next.toUpperCase() : next,
  );
  // Machine-readable outcome codes (INSUFFICIENT_VERIFIED_INVENTORY, ...).
  out = out.replace(/\b[A-Z]{2,}(?:_[A-Z0-9]+)+\b/g, (code) =>
    humanizeIdentifier(code.toLowerCase()),
  );
  // A humanized identifier can itself be planner vocabulary
  // ("deterministic_fallback" -> "deterministic fallback").
  out = out.replace(/\bdeterministic fallback\b/gi, "standard");
  return out.replace(/\s{2,}/g, " ").replace(/\s+([.,;])/g, "$1").trim();
}

// ---------------------------------------------------------------------------
// Section 2B: per-place and per-field Traveler wording
// ---------------------------------------------------------------------------

/**
 * Why a scheduled place is in the itinerary, in traveler words, or null to
 * show nothing. The backend sentence is recognised by what it says (a
 * must-visit, an interest match, an explicit request); how the place was
 * found -- AI proposals, matching, candidate pools -- is never restated.
 */
export function travelerWhyIncluded(whyIncluded: string | null | undefined): string | null {
  const text = (whyIncluded ?? "").trim();
  if (!text) return null;
  if (/must-visit/i.test(text)) return "One of your must-visit places.";
  if (/explicitly asked|you asked/i.test(text)) return "Added because you asked for it.";
  if (/matches your interests/i.test(text)) return "Matches your interests.";
  // The default sentence and the AI-promotion sentence say nothing a
  // traveler can use; every place in the plan comes from verified place data.
  return null;
}

// Fields the validation report lists as "data not available". The keys are
// backend field names; a traveler sees what is actually missing. Internal
// reasoning-stage fields are not travel data and are not listed at all.
const UNAVAILABLE_DATA_LABELS: Record<string, string | null> = {
  // What is missing is live, bookable inventory -- not all lodging
  // information (nearby stays from mapped locations are still shown).
  accommodation_options: "Live accommodation prices and availability",
  accommodation_details: "Live accommodation prices and availability",
  accommodations: "Live accommodation prices and availability",
  availability: "Live availability",
  price: "Live prices",
  flight_options: "Live flight offers",
  flight_details: "Live flight offers",
  transit_options: "Public transport routes",
  transit_feasibility: "Public transport routes",
  nearby_transit_stops: "Public transport stops",
  city_events: "Local events",
  weather_forecast: "Weather forecast",
  weather_alerts: "Weather alerts",
  public_holidays: "Public holidays",
  exchange_rate: "Currency exchange rate",
  converted_amount: "Currency exchange rate",
  distance_km: "Travel times between some stops",
  travel_time_minutes: "Travel times between some stops",
  walking_distance_km: "Travel times between some stops",
  route_matrix: "Travel times between some stops",
  places: "Some place details",
  attractions: "Some place details",
  place_details: "Some place details",
  restaurants: "Nearby restaurants",
  accommodation_pois: "Nearby places to stay",
  must_visit_place: "A must-visit place you asked for",
  decision_card: null,
  change_summary: null,
  feedback_interpretation: null,
  validation_reasoning: null,
  traveler_profile: null,
  trip_strategy: null,
  experience_explanation: null,
};

/** Traveler label for one "data not available" field, or null to omit it. */
export function unavailableDataLabel(field: string): string | null {
  const key = field.trim().toLowerCase();
  if (key in UNAVAILABLE_DATA_LABELS) return UNAVAILABLE_DATA_LABELS[key];
  const text = travelerText(field);
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : null;
}

/** The deduplicated Traveler list for the validation report's unavailable-data notes. */
export function unavailableDataLabels(fields: string[]): string[] {
  const labels: string[] = [];
  for (const field of fields) {
    const label = unavailableDataLabel(field);
    if (label && !labels.includes(label)) labels.push(label);
  }
  return labels;
}

// ---------------------------------------------------------------------------
// Warning severity (CRITICAL / WARNING / SUGGESTION)
// ---------------------------------------------------------------------------

export type IssueSeverity = ValidationIssue["severity"];

export const SEVERITY_ORDER: IssueSeverity[] = ["critical", "warning", "suggestion"];

// Text AND visual treatment differ per level so the distinction never
// relies on colour alone.
export const SEVERITY_PRESENTATION: Record<
  IssueSeverity,
  { label: string; meaning: string; symbol: string; border: string; badge: string }
> = {
  critical: {
    label: "Critical",
    meaning: "Needs fixing before you rely on this plan.",
    symbol: "!",
    border: "border-red-400/50 bg-red-950/20",
    badge: "border-red-400/50 bg-red-500/15 text-red-200",
  },
  warning: {
    label: "Warning",
    meaning: "Worth checking; the plan is still usable as a draft.",
    symbol: "▲",
    border: "border-amber-400/30 bg-amber-950/10",
    badge: "border-amber-400/40 bg-amber-500/10 text-amber-200",
  },
  suggestion: {
    label: "Suggestion",
    meaning: "Optional improvement or note.",
    symbol: "i",
    border: "border-sky-400/20 bg-slate-900/40",
    badge: "border-sky-400/30 bg-sky-500/10 text-sky-200",
  },
};

export type AggregatedIssue = {
  key: string;
  severity: IssueSeverity;
  message: string;
  count: number;
  suggestedFix: string | null;
};

function aggregationKey(issue: ValidationIssue): string {
  // Near-identical caveats differ only in numbers/indices ("Day 2 ..."
  // vs "Day 3 ..."), so digits are folded away for the comparison.
  const normalized = issue.message.toLowerCase().replace(/\d+/g, "#").replace(/\s+/g, " ").trim();
  return `${issue.severity}|${issue.category}|${normalized}`;
}

/**
 * Traveler view: collapse near-identical findings into one line with a
 * count. Severity is never merged across levels, so a critical issue
 * always stays its own critical row. Developer view lists every raw
 * finding and does not use this.
 */
export function aggregateIssues(issues: ValidationIssue[]): AggregatedIssue[] {
  const groups = new Map<string, AggregatedIssue>();
  for (const issue of issues) {
    const key = aggregationKey(issue);
    const existing = groups.get(key);
    if (existing) {
      existing.count += 1;
      continue;
    }
    groups.set(key, {
      key,
      severity: issue.severity,
      message: travelerText(issue.message),
      count: 1,
      suggestedFix: issue.suggested_fix ? travelerText(issue.suggested_fix) : null,
    });
  }
  const rank = (s: IssueSeverity) => SEVERITY_ORDER.indexOf(s);
  return [...groups.values()].sort((a, b) => rank(a.severity) - rank(b.severity));
}

/** Aggregate plain caveat strings (e.g. per-day narrator caveats). */
export function aggregateCaveats(caveats: string[]): { text: string; count: number }[] {
  const groups = new Map<string, { text: string; count: number }>();
  for (const caveat of caveats) {
    const key = caveat.toLowerCase().replace(/\d+/g, "#").replace(/\s+/g, " ").trim();
    const existing = groups.get(key);
    if (existing) existing.count += 1;
    else groups.set(key, { text: travelerText(caveat), count: 1 });
  }
  return [...groups.values()];
}
