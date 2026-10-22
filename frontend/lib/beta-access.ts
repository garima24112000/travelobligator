// Limited beta: the shareable invitation link. Pure helpers (no React, no runtime imports) so they can be
// unit tested with Node's built-in test runner.
//
// The link carries its token in the URL FRAGMENT: https://<frontend>/#beta=<token>. A fragment is never
// sent to any server, so the token reaches neither a request log nor a Referer header. The page reads it
// once, removes it from the address bar immediately (before any request or further navigation), and
// sends it in a JSON body to the backend, which alone decides whether it is valid.

const FRAGMENT_KEY = "beta";

/** The token of a `#beta=<token>` fragment, or null. Accepts the hash with or without its leading "#". */
export function readBetaLinkToken(hash: string | null | undefined): string | null {
  const raw = (hash ?? "").replace(/^#/, "");
  if (!raw) return null;
  const token = new URLSearchParams(raw).get(FRAGMENT_KEY);
  const trimmed = token?.trim() ?? "";
  return trimmed.length > 0 && trimmed.length <= 256 ? trimmed : null;
}

/** The current address without its fragment: what the address bar is replaced with. */
export function addressWithoutFragment(location: { pathname: string; search: string }): string {
  return `${location.pathname}${location.search}`;
}

/**
 * Removes a `#beta=` fragment from the address bar and returns its token (or null when there is none).
 * `history.replaceState` changes the visible address and the history entry without a navigation, so
 * the token is not kept in the history, a bookmark or a copied address.
 */
export function takeBetaLinkToken(
  location: { hash: string; pathname: string; search: string },
  history: { replaceState: (data: unknown, unused: string, url?: string) => void },
): string | null {
  const token = readBetaLinkToken(location.hash);
  if (token !== null) history.replaceState(null, "", addressWithoutFragment(location));
  return token;
}

export const BETA_LINK_ACCEPTED_NOTICE =
  "Your invitation link was accepted. Create your account now; the invitation is valid in this browser for a short time.";
export const INVITE_REQUIRED_NOTICE =
  "During the limited beta, creating an account needs an invitation link. Open the link you were given. If you already have an account, log in.";
export const REGISTRATION_CLOSED_NOTICE =
  "New accounts are not being created right now. If you already have an account, log in.";

/** What the sign-up form says about registration, from the backend's status; null when nothing is needed. */
export function signupAccessNotice(status: { mode: string; authorized: boolean } | null): string | null {
  if (status === null || status.mode === "open") return null;
  if (status.mode === "invite_link") return status.authorized ? BETA_LINK_ACCEPTED_NOTICE : INVITE_REQUIRED_NOTICE;
  return REGISTRATION_CLOSED_NOTICE; // "closed", or any value this page does not know
}
