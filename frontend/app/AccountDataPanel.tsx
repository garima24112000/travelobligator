"use client";

// P1-A: the privacy notice and the account-deletion control, kept out of
// page.tsx. Wording lives in lib/privacy-notice.ts so it can be unit tested;
// nothing here states anything about data handling that is not written there.

import { useId, useState } from "react";
import { ApiRequestError, deleteAccount } from "@/lib/api";
import {
  ACCOUNT_DELETE_FAILED_MESSAGE,
  ACCOUNT_DELETION_CHECKBOX_LABEL,
  ACCOUNT_DELETION_WARNING,
  PRIVACY_NOTICE,
  PRIVACY_NOTICE_INTRO,
  PRIVACY_NOTICE_TITLE,
  PROJECT_ISSUES_URL,
  accountDeletionBlocker,
} from "@/lib/privacy-notice";
import { travelerErrorMessage } from "@/lib/traveler-view";

const FOCUS_RING_CLASSNAME =
  "focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/50";

/**
 * The privacy notice, collapsed by default behind a real `<details>` so it is
 * keyboard- and screen-reader-operable without script.
 */
export function PrivacyNotice({
  sectionHeadingLevel,
  className = "",
}: {
  /**
   * The level of the notice's section headings, one below the heading the
   * notice sits under where it is used: 2 on the sign-in screen (directly
   * under the page's h1), 3 inside "Your account and data" (an h2). Only the
   * element changes; the text and its appearance are the same in both places.
   */
  sectionHeadingLevel: 2 | 3;
  className?: string;
}) {
  const SectionHeading = sectionHeadingLevel === 2 ? "h2" : "h3";
  return (
    <details className={`rounded-2xl border border-white/10 bg-white/5 p-4 text-sm ${className}`}>
      <summary
        className={`cursor-pointer font-semibold text-slate-200 hover:text-cyan-200 ${FOCUS_RING_CLASSNAME}`}
      >
        {PRIVACY_NOTICE_TITLE}
      </summary>
      <p className="mt-3 text-slate-300">{PRIVACY_NOTICE_INTRO}</p>
      {PRIVACY_NOTICE.map((section) => (
        <section key={section.heading} className="mt-4">
          <SectionHeading className="text-xs font-semibold uppercase tracking-wide text-cyan-300/80">
            {section.heading}
          </SectionHeading>
          <ul className="mt-2 list-disc space-y-1.5 pl-5 text-slate-300">
            {section.points.map((point) => (
              <li key={point} className="break-words">
                {point}
              </li>
            ))}
          </ul>
        </section>
      ))}
      <p className="mt-3 break-words text-slate-300">
        <a
          href={PROJECT_ISSUES_URL}
          target="_blank"
          rel="noopener noreferrer"
          className={`font-semibold text-cyan-200 underline ${FOCUS_RING_CLASSNAME}`}
        >
          Open an issue on GitHub
        </a>
      </p>
    </details>
  );
}

/**
 * Account deletion. Nothing is sent until the user has opened the form,
 * typed their current password and ticked the confirmation box. A session
 * that ended meanwhile is handed to `onAuthenticationRequired`; a successful
 * deletion to `onDeleted`, which must drop every piece of signed-in state.
 */
export function DeleteAccountSection({
  email,
  onDeleted,
  onAuthenticationRequired,
}: {
  email: string;
  onDeleted: () => void;
  onAuthenticationRequired: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const passwordId = useId();
  const warningId = useId();

  function close() {
    setOpen(false);
    setPassword("");
    setConfirmed(false);
    setError(null);
  }

  async function submit() {
    if (isDeleting) return;
    const blocker = accountDeletionBlocker({ password, confirmed });
    if (blocker) {
      setError(blocker);
      return;
    }
    setIsDeleting(true);
    setError(null);
    try {
      await deleteAccount(password);
      setPassword("");
      onDeleted();
    } catch (err) {
      if (err instanceof ApiRequestError && err.code === "AUTHENTICATION_REQUIRED") {
        onAuthenticationRequired();
        return;
      }
      setPassword("");
      setError(
        err instanceof ApiRequestError
          ? travelerErrorMessage({
              code: err.code,
              status: err.status,
              message: err.message,
              operation: "delete_account",
            }).message
          : ACCOUNT_DELETE_FAILED_MESSAGE,
      );
    } finally {
      setIsDeleting(false);
    }
  }

  return (
    <section
      aria-labelledby="delete-account-heading"
      className="rounded-2xl border border-rose-300/20 bg-rose-400/[0.03] p-4 text-sm"
    >
      <h2
        id="delete-account-heading"
        className="text-xs font-semibold uppercase tracking-wide text-rose-200/90"
      >
        Delete account
      </h2>
      <p id={warningId} className="mt-2 text-slate-300">
        {ACCOUNT_DELETION_WARNING}
      </p>
      {!open ? (
        <button
          type="button"
          onClick={() => setOpen(true)}
          className={`mt-3 rounded-lg border border-rose-300/40 bg-slate-900 px-3 py-1.5 font-semibold text-rose-200 transition hover:bg-rose-500/10 ${FOCUS_RING_CLASSNAME}`}
        >
          Delete my account…
        </button>
      ) : (
        <form
          className="mt-3 flex flex-col gap-3"
          aria-describedby={warningId}
          aria-busy={isDeleting}
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <p className="break-words text-slate-300">
            Account: <span className="font-semibold text-slate-100">{email}</span>
          </p>
          <label htmlFor={passwordId} className="flex flex-col gap-1 text-slate-300">
            Your password
            <input
              id={passwordId}
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              disabled={isDeleting}
              className="rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/50"
            />
          </label>
          <label className="flex items-start gap-2 text-slate-300">
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(event) => setConfirmed(event.target.checked)}
              disabled={isDeleting}
              className="mt-1"
            />
            <span>{ACCOUNT_DELETION_CHECKBOX_LABEL}</span>
          </label>
          {error && (
            <p role="alert" className="break-words text-rose-300">
              {error}
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <button
              type="submit"
              disabled={isDeleting}
              className={`rounded-lg bg-rose-500 px-3 py-1.5 font-semibold text-white transition hover:bg-rose-400 disabled:cursor-not-allowed disabled:opacity-50 ${FOCUS_RING_CLASSNAME}`}
            >
              {isDeleting ? "Deleting…" : "Permanently delete my account"}
            </button>
            <button
              type="button"
              onClick={close}
              disabled={isDeleting}
              className={`rounded-lg border border-white/20 bg-slate-900 px-3 py-1.5 font-semibold text-slate-200 transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50 ${FOCUS_RING_CLASSNAME}`}
            >
              Cancel
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
