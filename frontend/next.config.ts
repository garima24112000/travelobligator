import type { NextConfig } from "next";

import { hstsMaxAgeSeconds, staticSecurityHeaders } from "./lib/security-headers";

// Section 203B: same-origin API proxy. When `BACKEND_ORIGIN` is set at BUILD time (the hosted frontend),
// `/api/*` is rewritten to that backend, so the browser only ever talks to the frontend's own origin and the
// session cookie stays a first-party, host-only cookie. Pair it with the runtime `API_BASE_URL=/api`
// (see lib/runtime-config.ts). When it is unset (local dev, the Docker image, CI) no rewrite exists and
// the browser calls the absolute `API_BASE_URL` directly, exactly as before.
//
// `BACKEND_ORIGIN` is a server-side build setting (not `NEXT_PUBLIC_*`): it is never compiled into browser
// JavaScript. It must be a bare http(s) origin -- no credentials, path, query or fragment.
const API_PROXY_PREFIX = "/api";

function backendOrigin(): string | null {
  const raw = process.env.BACKEND_ORIGIN?.trim();
  if (!raw) return null;
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new Error("BACKEND_ORIGIN is not a valid URL (the value is intentionally not shown).");
  }
  const bare =
    (url.protocol === "https:" || url.protocol === "http:") &&
    !url.username &&
    !url.password &&
    url.pathname === "/" &&
    !url.search &&
    !url.hash;
  if (!bare) {
    throw new Error(
      "BACKEND_ORIGIN must be a bare http(s) origin such as https://backend.example.com " +
        "(no credentials, path, query or fragment).",
    );
  }
  return url.origin;
}

const nextConfig: NextConfig = {
  // Section 200E: emit a self-contained server (`.next/standalone`) so the production image needs only the
  // traced runtime files -- no `node_modules` tree, no dev dependencies. `next dev` is unaffected.
  output: "standalone",
  async rewrites() {
    const origin = backendOrigin();
    if (!origin) return [];
    return [{ source: `${API_PROXY_PREFIX}/:path*`, destination: `${origin}/:path*` }];
  },
  async headers() {
    return [
      // P1-B: enforced security headers on EVERY response of this host -- pages, error pages, static
      // assets and the `/api/*` proxy. None of them is a header the backend sets itself, so nothing the
      // backend sends (Cache-Control, Set-Cookie, X-Request-Id) is replaced. The Content Security Policy
      // is not here: it needs a per-response nonce and is added, Report-Only, by proxy.ts.
      {
        source: "/(.*)",
        headers: staticSecurityHeaders({
          hstsMaxAgeSeconds: hstsMaxAgeSeconds(process.env.FRONTEND_HSTS_MAX_AGE_SECONDS),
        }),
      },
      // API responses are session-scoped: never cacheable by the CDN or the browser. The backend already sends
      // `Cache-Control: no-store` on every response; this also covers an error answered by the proxy itself.
      {
        source: `${API_PROXY_PREFIX}/:path*`,
        headers: [{ key: "Cache-Control", value: "no-store" }],
      },
    ];
  },
};

export default nextConfig;
