// Section 203C.2B: result blocks kept out of page.tsx. Everything here
// renders values the backend already sent (the inventory sufficiency
// report, the validation outcome codes, the provider usage report);
// nothing computes a new travel fact.

import {
  inventoryPanelRows,
  travelerInventoryNotice,
  usagePanelRows,
  type PanelRow,
} from "@/lib/plan-insights";
import type {
  InventorySufficiencyReport,
  ProviderUsageReport,
  ValidationReport,
} from "@/lib/types";

/**
 * Traveler view: the one material warning about plan fullness. Rendered
 * above the itinerary and never inside a disclosure -- a traveler must not
 * have to open anything to learn the plan is partial.
 */
export function TravelerInventoryNotice({
  validationReport,
  inventoryReport,
}: {
  validationReport: ValidationReport | null;
  inventoryReport: InventorySufficiencyReport | null;
}) {
  const notice = travelerInventoryNotice(validationReport, inventoryReport);
  if (!notice) return null;

  const toneClassName =
    notice.tone === "blocked"
      ? "border-rose-400/40 bg-rose-500/10 text-rose-100"
      : "border-amber-400/40 bg-amber-500/10 text-amber-100";
  return (
    <section
      id="traveler-inventory-notice"
      role="status"
      data-outcome-code={notice.code}
      className={`scroll-mt-6 rounded-2xl border p-4 ${toneClassName}`}
    >
      <h2 className="text-base font-semibold">{notice.title}</h2>
      <p className="mt-1 break-words text-sm">{notice.body}</p>
    </section>
  );
}

function RowsPanel({
  id,
  title,
  description,
  rows,
  emptyMessage,
}: {
  id: string;
  title: string;
  description: string;
  rows: PanelRow[];
  emptyMessage: string;
}) {
  return (
    <section id={id} className="scroll-mt-6 rounded-2xl border border-white/10 bg-white/5 p-4">
      <h2 className="text-lg font-semibold">{title}</h2>
      <p className="mt-1 text-xs text-slate-400">{description}</p>
      {rows.length === 0 ? (
        <p className="mt-3 text-sm text-slate-300">{emptyMessage}</p>
      ) : (
        <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
          {rows.map((row) => (
            <div key={row.label} className="flex justify-between gap-3 border-b border-white/5 py-1">
              <dt className="text-slate-400">{row.label}</dt>
              <dd className="break-words text-right text-slate-100">{row.value}</dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  );
}

/** Developer view: the inventory sufficiency gate's own numbers. */
export function InventorySufficiencyPanel({ report }: { report: InventorySufficiencyReport | null }) {
  return (
    <RowsPanel
      id="inventory-sufficiency"
      title="Inventory sufficiency"
      description="Whether enough verified places existed before the itinerary was built. Only fewer than R blocks a plan."
      rows={inventoryPanelRows(report)}
      emptyMessage="No inventory sufficiency report for this plan."
    />
  );
}

/** Developer view: provider calls and credits the last generation spent. */
export function ProviderUsagePanel({ report }: { report: ProviderUsageReport | null }) {
  return (
    <RowsPanel
      id="provider-usage"
      title="Provider usage"
      description="Calls and credits spent by the last generation, per provider API. Counts only."
      rows={usagePanelRows(report)}
      emptyMessage="No provider usage was recorded for this plan."
    />
  );
}
