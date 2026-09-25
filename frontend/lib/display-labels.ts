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
  [/\bopenstreetmap_places\b/gi, "OpenStreetMap"],
  [/\bprovider-grounded\b/gi, "matched in place data"],
];

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
  // "(via osrm)" style attributions: a known lowercase source key becomes
  // its friendly label.
  out = out.replace(/\b(via|from|by) ([a-z][a-z0-9_]*)\b/g, (whole, word: string, key: string) => {
    const label = SOURCE_LABELS[key];
    return label ? `${word} ${label}` : whole;
  });
  out = out.replace(/\b([a-z]+(?:_[a-z0-9]+)+)\b/g, (token) => {
    return SOURCE_LABELS[token] ?? humanizeIdentifier(token);
  });
  return out.replace(/\s{2,}/g, " ").replace(/\s+([.,;])/g, "$1").trim();
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
