// Section 200E: runtime (not build-time) API base URL.
//
// `NEXT_PUBLIC_*` variables are compiled into the browser bundle by `next build`, so an image built once
// could only ever talk to the backend URL it was BUILT with. To let one image run in any environment
// (local Compose, staging, production) without source edits or a rebuild, the root layout reads
// `API_BASE_URL` from the SERVER's runtime environment on each request and injects it as
// `window.__TRAVELOBLIGATOR_RUNTIME__` before the app hydrates. Resolution order in the browser:
//
//   1. window.__TRAVELOBLIGATOR_RUNTIME__.apiBaseUrl   (runtime env `API_BASE_URL` of the frontend container)
//   2. process.env.NEXT_PUBLIC_API_BASE_URL             (build-time fallback, `--build-arg`)
//   3. http://localhost:8000                            (local-development default only)
//
// The value is public, browser-visible configuration (it is the address the BROWSER calls) -- never a secret,
// never an internal service name such as `http://backend:8000`, which a browser cannot resolve.

declare global {
  interface Window {
    __TRAVELOBLIGATOR_RUNTIME__?: { apiBaseUrl?: string };
  }
}

const BUILD_TIME_API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

const SAFE_URL = /^https?:\/\/[^\s"'<>\\]+$/;

/** Only an absolute http(s) URL is ever accepted (also used when the layout serialises the value). */
export function sanitizeApiBaseUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim().replace(/\/+$/, "");
  return SAFE_URL.test(trimmed) ? trimmed : null;
}

export function apiBaseUrl(): string {
  if (typeof window !== "undefined") {
    const injected = sanitizeApiBaseUrl(
      window.__TRAVELOBLIGATOR_RUNTIME__?.apiBaseUrl,
    );
    if (injected) return injected;
  }
  return BUILD_TIME_API_BASE_URL;
}
