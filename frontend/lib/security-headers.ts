// Response security headers for the frontend (P1-B), as pure functions so they can be unit tested with
// Node's built-in test runner. Two consumers:
//
//   * next.config.ts `headers()`  -> `staticSecurityHeaders()` on every route (pages, the `/api/*` proxy,
//     static assets). These are plain response headers and are ENFORCED by browsers.
//   * proxy.ts                    -> `contentSecurityPolicy()` on document requests, sent as
//     `Content-Security-Policy-Report-Only`. Report-Only means the browser only REPORTS what the policy
//     would have blocked (in its console); it blocks nothing. See docs/22_free_tier_deployment.md
//     section 13C for what has to be verified before it may be sent as an enforcing policy.
//
// Nothing here reads a secret, and nothing is specific to one hosting provider.

export type Header = { key: string; value: string };

/** Report-Only on purpose (see above). Changing this to the enforcing name is the promotion step. */
export const CSP_HEADER_NAME = "Content-Security-Policy-Report-Only";

// Features the application does not use. An empty allowlist `()` switches the feature off for this
// page and for anything it might embed.
const DISABLED_BROWSER_FEATURES = [
  "accelerometer",
  "autoplay",
  "browsing-topics",
  "camera",
  "display-capture",
  "encrypted-media",
  "geolocation",
  "gyroscope",
  "magnetometer",
  "microphone",
  "midi",
  "payment",
  "picture-in-picture",
  "screen-wake-lock",
  "usb",
  "xr-spatial-tracking",
] as const;

export const PERMISSIONS_POLICY = DISABLED_BROWSER_FEATURES.map((feature) => `${feature}=()`).join(", ");

// `strict-origin-when-cross-origin`: full URL to the app's own origin, only the ORIGIN (never the path
// or the `?trip=` id) to other sites, nothing on an HTTPS -> HTTP downgrade. Deliberately not
// `no-referrer` / `same-origin`: the map tiles are requested from OpenStreetMap, whose tile usage policy
// expects a browser to identify the referring site.
export const REFERRER_POLICY = "strict-origin-when-cross-origin";

/**
 * `Strict-Transport-Security` max-age from the build-time `FRONTEND_HSTS_MAX_AGE_SECONDS`, or null when
 * the header must not be sent. HSTS is opt-in: it tells a browser to refuse plain HTTP for the HOST for
 * that long, which is only safe to promise for a hostname you control and serve over HTTPS for good --
 * never for localhost, the Docker image or CI. No `includeSubDomains`, no `preload`: both make a
 * promise about names beyond this application.
 */
export function hstsMaxAgeSeconds(raw: string | undefined): number | null {
  const value = (raw ?? "").trim();
  if (!value) return null;
  if (!/^\d{1,9}$/.test(value)) {
    throw new Error("FRONTEND_HSTS_MAX_AGE_SECONDS must be a whole number of seconds (or unset).");
  }
  const seconds = Number(value);
  return seconds > 0 ? seconds : null;
}

/** The enforced headers every response of the frontend carries. */
export function staticSecurityHeaders(options: { hstsMaxAgeSeconds?: number | null } = {}): Header[] {
  const headers: Header[] = [
    // A response is only ever interpreted as the type it declares.
    { key: "X-Content-Type-Options", value: "nosniff" },
    { key: "Referrer-Policy", value: REFERRER_POLICY },
    // No site may put this application in a frame. This header is the enforced clickjacking
    // protection: the CSP `frame-ancestors` directive is ignored while the policy is Report-Only.
    { key: "X-Frame-Options", value: "DENY" },
    { key: "Permissions-Policy", value: PERMISSIONS_POLICY },
  ];
  if (options.hstsMaxAgeSeconds) {
    headers.push({ key: "Strict-Transport-Security", value: `max-age=${options.hstsMaxAgeSeconds}` });
  }
  return headers;
}

/** The OpenStreetMap tile hosts the day maps load images from (`https://{s}.tile.openstreetmap.org`). */
export const MAP_TILE_SOURCE = "https://*.tile.openstreetmap.org";

/**
 * The origin of an ABSOLUTE API base URL (local development, Docker: the browser calls the backend
 * directly), or null for the same-origin `/api` prefix used in production, an unset or an unusable
 * value. Only ever an `http(s)://host[:port]` origin -- never a path, a query or credentials.
 */
export function apiOrigin(apiBaseUrl: string | undefined | null): string | null {
  const value = (apiBaseUrl ?? "").trim();
  if (!/^https?:\/\//i.test(value)) return null;
  try {
    const url = new URL(value);
    if (url.username || url.password) return null;
    return url.origin;
  } catch {
    return null;
  }
}

/**
 * The Content Security Policy for one document response.
 *
 *   script-src   only scripts carrying this response's nonce (Next.js puts it on its own scripts, the
 *                layout puts it on the one inline runtime-config script), and what those load
 *                (`strict-dynamic`). No `unsafe-inline`. `unsafe-eval` only in development, where React
 *                needs it for its debugging overlay -- never in a production build.
 *   style-src    stylesheets from this origin, and `<style>` elements carrying the nonce.
 *   style-src-attr  `unsafe-inline`: inline `style` ATTRIBUTES only (the numbered map markers and the
 *                progress bar set one). It does not allow inline scripts or `<style>` blocks.
 *   img-src      this origin, `data:`/`blob:` (Leaflet), and the OpenStreetMap tile hosts.
 *   connect-src  this origin (the same-origin `/api` proxy), plus the backend's origin when the API
 *                base URL is absolute.
 *   everything else is closed: no plugins, no frames in or out, forms and `<base>` stay on this origin.
 *
 * `upgrade-insecure-requests` is left out on purpose: it is ignored in Report-Only mode and would
 * break the plain-HTTP local and Docker setups if the policy were enforced unchanged.
 */
export function contentSecurityPolicy(options: {
  nonce: string;
  apiBaseUrl?: string | null;
  development?: boolean;
}): string {
  if (!/^[A-Za-z0-9+/_=-]{16,}$/.test(options.nonce)) {
    throw new Error("A CSP nonce must be at least 16 base64 characters.");
  }
  const nonce = `'nonce-${options.nonce}'`;
  const backend = apiOrigin(options.apiBaseUrl);
  const directives: [string, string[]][] = [
    ["default-src", ["'self'"]],
    ["script-src", ["'self'", nonce, "'strict-dynamic'", ...(options.development ? ["'unsafe-eval'"] : [])]],
    ["style-src", ["'self'", options.development ? "'unsafe-inline'" : nonce]],
    ["style-src-attr", ["'unsafe-inline'"]],
    ["img-src", ["'self'", "data:", "blob:", MAP_TILE_SOURCE]],
    ["font-src", ["'self'"]],
    ["connect-src", ["'self'", ...(backend ? [backend] : [])]],
    ["object-src", ["'none'"]],
    ["frame-src", ["'none'"]],
    ["frame-ancestors", ["'none'"]],
    ["base-uri", ["'self'"]],
    ["form-action", ["'self'"]],
  ];
  return directives.map(([name, sources]) => `${name} ${sources.join(" ")}`).join("; ");
}

/** A fresh, unpredictable nonce for one response (122 random bits, base64). */
export function newNonce(): string {
  return btoa(crypto.randomUUID());
}
