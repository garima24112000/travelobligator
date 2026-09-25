"use client";

// Section 202B.3: Traveler-view building blocks kept out of page.tsx.
// Everything here renders data the backend already sent (validation
// report, limitation notes); nothing computes a new travel fact.

import { useEffect, useId, useState, type ReactNode } from "react";
import {
  SEVERITY_ORDER,
  SEVERITY_PRESENTATION,
  aggregateIssues,
  travelerText,
  type IssueSeverity,
} from "@/lib/display-labels";
import type { ValidationReport } from "@/lib/types";

const FOCUS_RING_CLASSNAME =
  "focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/50";

/**
 * Accessible show/hide control for SECONDARY detail. Never wrap a critical
 * warning in this. `id` is the anchor jump links point at; navigating to
 * `#id` opens the section so a jump link never lands on hidden content.
 */
export function DisclosureSection({
  id,
  title,
  hint,
  defaultOpen = false,
  children,
}: {
  id: string;
  title: string;
  hint?: string;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const panelId = useId();

  useEffect(() => {
    function openIfTargeted() {
      if (window.location.hash === `#${id}`) setOpen(true);
    }
    openIfTargeted();
    window.addEventListener("hashchange", openIfTargeted);
    return () => window.removeEventListener("hashchange", openIfTargeted);
  }, [id]);

  return (
    <section id={id} className="scroll-mt-6 rounded-2xl border border-white/10 bg-white/5">
      <h2 className="m-0 text-base font-semibold">
        <button
          type="button"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => setOpen((value) => !value)}
          className={`flex min-h-11 w-full items-center justify-between gap-3 rounded-2xl px-4 py-3 text-left ${FOCUS_RING_CLASSNAME}`}
        >
          <span className="min-w-0 break-words">
            {title}
            {hint && (
              <span className="mt-0.5 block text-xs font-normal text-slate-400">{hint}</span>
            )}
          </span>
          <span aria-hidden="true" className="shrink-0 text-xs text-cyan-200">
            {open ? "Hide" : "Show"}
          </span>
        </button>
      </h2>
      <div id={panelId} hidden={!open} className="px-4 pb-4">
        {open ? children : null}
      </div>
    </section>
  );
}

function SeverityBadge({ severity }: { severity: IssueSeverity }) {
  const presentation = SEVERITY_PRESENTATION[severity];
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${presentation.badge}`}
    >
      <span aria-hidden="true">{presentation.symbol}</span>
      {presentation.label}
    </span>
  );
}

/**
 * Traveler view "Important limitations": the validation report's findings
 * grouped CRITICAL / WARNING / SUGGESTION, near-identical findings folded
 * into one row with a count. Developer view keeps every individual finding
 * (ValidationSection); nothing is deleted from the backend report.
 */
export function TravelerLimitationsSection({ report }: { report: ValidationReport }) {
  const findings = aggregateIssues([...report.critical_issues, ...report.warnings]);
  const bySeverity = (severity: IssueSeverity) =>
    findings.filter((finding) => finding.severity === severity);
  const dataNotes = Array.from(
    new Set(report.unavailable_data_notes.map((note) => travelerText(note))),
  );

  const critical = bySeverity("critical");
  const warnings = bySeverity("warning");
  const suggestions = bySeverity("suggestion");

  function renderRows(rows: typeof findings) {
    return (
      <ul className="mt-2 flex flex-col gap-2">
        {rows.map((row) => (
          <li
            key={row.key}
            className={`rounded-lg border p-3 text-sm ${SEVERITY_PRESENTATION[row.severity].border}`}
          >
            <SeverityBadge severity={row.severity} />
            <p className="mt-1 break-words text-slate-100">
              {row.message}
              {row.count > 1 && (
                <span className="text-slate-400"> (applies to {row.count} items)</span>
              )}
            </p>
          </li>
        ))}
      </ul>
    );
  }

  return (
    <section
      id="limitations"
      aria-labelledby="limitations-heading"
      className="scroll-mt-6 rounded-2xl border border-white/10 bg-white/5 p-4 sm:p-5"
    >
      <h2 id="limitations-heading" className="text-lg font-semibold">
        Important limitations
      </h2>
      <p className="mt-1 text-xs text-slate-400">
        Critical items need fixing, warnings are worth checking, suggestions are optional.
      </p>

      {findings.length === 0 && (
        <p className="mt-3 text-sm text-slate-300">
          No validation findings were recorded for this plan. Still confirm opening hours,
          prices and availability yourself.
        </p>
      )}

      {SEVERITY_ORDER.filter((severity) => severity !== "suggestion").map((severity) => {
        const rows = severity === "critical" ? critical : warnings;
        if (rows.length === 0) return null;
        return (
          <div key={severity} className="mt-3">
            <h3 className="text-sm font-semibold text-slate-200">
              {SEVERITY_PRESENTATION[severity].label}{" "}
              <span className="font-normal text-slate-400">
                — {SEVERITY_PRESENTATION[severity].meaning}
              </span>
            </h3>
            {renderRows(rows)}
          </div>
        );
      })}

      {suggestions.length > 0 && (
        <SuggestionsDisclosure count={suggestions.length}>{renderRows(suggestions)}</SuggestionsDisclosure>
      )}

      {dataNotes.length > 0 && (
        <div className="mt-3">
          <h3 className="text-sm font-semibold text-slate-200">Data not available</h3>
          <ul className="mt-2 list-disc break-words pl-5 text-xs text-slate-300">
            {dataNotes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

// Suggestions are the one severity that may be tucked away; critical items
// and warnings above are always visible.
function SuggestionsDisclosure({ count, children }: { count: number; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  return (
    <div className="mt-3">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((value) => !value)}
        className={`min-h-11 rounded-lg border border-sky-400/20 px-3 py-2 text-sm text-sky-200 ${FOCUS_RING_CLASSNAME}`}
      >
        {open ? "Hide" : "Show"} {count} optional suggestion{count === 1 ? "" : "s"}
      </button>
      <div id={panelId} hidden={!open}>
        {open ? children : null}
      </div>
    </div>
  );
}
