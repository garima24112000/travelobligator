import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

import { CSP_HEADER_NAME, contentSecurityPolicy, newNonce } from "./lib/security-headers";

// P1-B: Content Security Policy for document requests, in REPORT-ONLY mode.
//
// Next.js calls this file "Proxy" (formerly Middleware). It has nothing to do with the `/api/*` rewrite
// to the backend, which is configured in next.config.ts and which this file never touches (see `matcher`).
//
// A policy without `unsafe-inline` for scripts needs a per-response nonce, so the header cannot be a
// static one in next.config.ts. Per request this:
//   1. makes a fresh nonce and builds the policy (lib/security-headers.ts);
//   2. puts the policy on the REQUEST headers, where Next.js reads the nonce and adds it to its own
//      scripts and styles (it reads the Report-Only header too), plus `x-nonce` for app/layout.tsx;
//   3. puts the same policy on the RESPONSE as `Content-Security-Policy-Report-Only`.
//
// Report-Only: the browser reports what the policy would block and blocks nothing. The enforced
// protections are the static headers in next.config.ts.
export function proxy(request: NextRequest) {
  const nonce = newNonce();
  const policy = contentSecurityPolicy({
    nonce,
    // RUNTIME value (the same one app/layout.tsx serialises for the browser): `/api` in production,
    // an absolute backend URL in local development and Docker.
    apiBaseUrl: process.env.API_BASE_URL ?? process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000",
    development: process.env.NODE_ENV === "development",
  });

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set(CSP_HEADER_NAME, policy);

  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set(CSP_HEADER_NAME, policy);
  return response;
}

export const config = {
  matcher: [
    {
      // Documents only. Never the `/api/*` proxy to the backend (its responses are JSON and carry the
      // backend's own headers), never static assets or the favicon, never a prefetch.
      source: "/((?!api/|_next/static|_next/image|favicon.ico).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
