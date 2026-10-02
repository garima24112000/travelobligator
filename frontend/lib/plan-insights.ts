// Section 203C.2B: the pure view model for the inventory/usefulness outcome
// and for which result sections each view shows. Nothing here adds a fact:
// every string restates a value the backend already sent.
//
// Kept free of React and of path-alias imports so it can be unit tested with
// Node's built-in test runner (frontend/tests).

import type {
  InventorySufficiencyReport,
  ProviderUsageReport,
  ValidationReport,
} from "./types";

export const INSUFFICIENT_VERIFIED_INVENTORY = "INSUFFICIENT_VERIFIED_INVENTORY";
export const UNDERFILLED_PLAN = "UNDERFILLED_PLAN";

// -- which result sections each view shows --------------------------------------
//
// Traveler view leads with the itinerary and never foregrounds unavailable
// flight inventory, bookable-lodging inventory or provider/implementation
// diagnostics. Developer view keeps all of them. `frontend/tests` checks
// page.tsx against these lists.

/** Element ids of the Traveler result blocks, in the order they appear. */
export const TRAVELER_SECTION_ORDER = [
  "traveler-inventory-notice",
  "draft-itinerary",
  "feedback",
  "supporting-detail",
] as const;

/** Section components that must only ever render in Developer view. */
export const DEVELOPER_ONLY_SECTIONS = [
  "FlightInventorySection",
  "AccommodationInventorySection",
  "ImplementationGapsSection",
  "ProviderCoverageSection",
  "InventorySufficiencyPanel",
  "ProviderUsagePanel",
] as const;

// -- traveler-facing notice ------------------------------------------------------

export type InventoryNotice = {
  tone: "blocked" | "review";
  code: string;
  title: string;
  body: string;
};

/**
 * The one material warning a traveler must see about plan fullness, or null
 * when there is nothing to say. Derived only from the validation report's
 * outcome codes and the inventory report's own numbers.
 */
export function travelerInventoryNotice(
  validation: Pick<ValidationReport, "blocking_codes" | "review_codes"> | null | undefined,
  inventory: InventorySufficiencyReport | null | undefined,
): InventoryNotice | null {
  const blocking = validation?.blocking_codes ?? [];
  const review = validation?.review_codes ?? [];

  if (blocking.includes(INSUFFICIENT_VERIFIED_INVENTORY)) {
    const counts = inventory
      ? ` We found ${inventory.viable_candidates} verified place${
          inventory.viable_candidates === 1 ? "" : "s"
        }; a ${inventory.trip_days}-day ${inventory.pace} trip needs at least ${inventory.minimum_useful}.`
      : "";
    return {
      tone: "blocked",
      code: INSUFFICIENT_VERIFIED_INVENTORY,
      title: "Not enough verified places for this trip",
      body:
        "There were not enough verified places to build a full itinerary for this destination, " +
        "so the plan below is partial. Nothing was added to fill the gaps." +
        counts +
        " Try a shorter trip, a more relaxed pace, or a larger nearby destination.",
    };
  }
  if (review.includes(UNDERFILLED_PLAN)) {
    return {
      tone: "review",
      code: UNDERFILLED_PLAN,
      title: "This itinerary is lighter than planned",
      body:
        "Fewer places are scheduled than this trip's pace calls for, although more verified places " +
        "were available. Request changes or regenerate to fill it out.",
    };
  }
  return null;
}

// -- developer panels ---------------------------------------------------------------

export type PanelRow = { label: string; value: string };

export function inventoryPanelRows(report: InventorySufficiencyReport | null | undefined): PanelRow[] {
  if (!report) {
    return [];
  }
  return [
    { label: "Status", value: report.status.replace(/_/g, " ") },
    { label: "Verified usable candidates", value: String(report.viable_candidates) },
    { label: "Target stops (T)", value: String(report.target_stops) },
    { label: "Minimum useful (R)", value: String(report.minimum_useful) },
    { label: "Healthy buffer (H)", value: String(report.healthy_buffer) },
    {
      label: "Expansion round",
      value: report.expansion_attempted
        ? `tried (${report.viable_before_expansion ?? "?"} before)`
        : "not needed or not available",
    },
  ];
}

export function usagePanelRows(report: ProviderUsageReport | null | undefined): PanelRow[] {
  if (!report) {
    return [];
  }
  const rows: PanelRow[] = [
    {
      label: "Credits used",
      value: report.budget === null ? String(report.credits_used) : `${report.credits_used} of ${report.budget}`,
    },
    { label: "Calls refused by budget", value: String(report.refused_calls) },
  ];
  for (const api of Object.keys(report.credits_by_api).sort()) {
    rows.push({
      label: api.replace(/_/g, " "),
      value: `${report.credits_by_api[api]} credit(s), ${report.calls_by_api[api] ?? 0} call(s)`,
    });
  }
  return rows;
}
