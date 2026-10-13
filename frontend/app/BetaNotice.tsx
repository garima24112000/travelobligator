"use client";

// The limited-beta labelling: a small badge beside the product name, and a short list of what a
// traveller should know. Wording lives in lib/beta-notice.ts so it can be unit tested; nothing here says
// anything about a particular itinerary -- the validation banners do that.

import { useId } from "react";
import { BETA_LABEL, BETA_NOTICE_HEADING, BETA_NOTICE_POINTS, BETA_SUMMARY } from "@/lib/beta-notice";

/** "Limited beta", as a text label (never colour alone) next to the product name. */
export function BetaBadge() {
  return (
    <span className="ml-3 inline-block rounded-full border border-amber-300/40 bg-amber-300/10 px-2 py-0.5 text-[11px] font-semibold normal-case tracking-normal text-amber-100">
      {BETA_LABEL}
    </span>
  );
}

/** The one-sentence summary shown under the page heading. */
export function BetaSummary({ className = "" }: { className?: string }) {
  return <p className={`break-words text-sm text-amber-100/90 ${className}`}>{BETA_SUMMARY}</p>;
}

/**
 * The five points, always visible (not collapsed). A labelled section with its own heading, one level
 * below the page's h1 on both screens it is used on.
 */
export function BetaNotice({ className = "" }: { className?: string }) {
  const headingId = useId();
  return (
    <section
      aria-labelledby={headingId}
      className={`rounded-2xl border border-amber-300/25 bg-amber-300/5 p-4 text-sm ${className}`}
    >
      <h2 id={headingId} className="text-xs font-semibold uppercase tracking-wide text-amber-200">
        {BETA_NOTICE_HEADING}
      </h2>
      <ul className="mt-2 list-disc space-y-1.5 pl-5 text-slate-200">
        {BETA_NOTICE_POINTS.map((point) => (
          <li key={point} className="break-words">
            {point}
          </li>
        ))}
      </ul>
    </section>
  );
}
