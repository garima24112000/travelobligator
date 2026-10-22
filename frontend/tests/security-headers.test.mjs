// P1-B: the frontend's response security headers and its Report-Only Content Security Policy.
// Run with `npm test` (Node's built-in test runner). These check the header builders and how they are
// wired; `npm run check:headers` checks what the real production server sends.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  CSP_HEADER_NAME,
  MAP_TILE_SOURCE,
  PERMISSIONS_POLICY,
  REFERRER_POLICY,
  apiOrigin,
  contentSecurityPolicy,
  hstsMaxAgeSeconds,
  newNonce,
  staticSecurityHeaders,
} from "../lib/security-headers.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const repo = join(root, "..");
const read = (...parts) => readFileSync(join(...parts), "utf8");
const nextConfig = read(root, "next.config.ts");
const proxy = read(root, "proxy.ts");
const layout = read(root, "app", "layout.tsx");
const page = read(root, "app", "page.tsx");
const doc = read(repo, "docs", "22_free_tier_deployment.md");

const NONCE = "bm9uY2UtZm9yLXRoZS10ZXN0cw==";
const directives = (policy) =>
  Object.fromEntries(
    policy.split(";").map((directive) => {
      const [name, ...sources] = directive.trim().split(/\s+/);
      return [name, sources];
    }),
  );
const headerMap = (headers) => Object.fromEntries(headers.map(({ key, value }) => [key, value]));

// -- enforced headers ---------------------------------------------------------------------------------

test("every response gets the four enforced headers, and no HSTS unless opted in", () => {
  assert.deepEqual(headerMap(staticSecurityHeaders()), {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": PERMISSIONS_POLICY,
  });
  assert.deepEqual(headerMap(staticSecurityHeaders({ hstsMaxAgeSeconds: null })), headerMap(staticSecurityHeaders()));
});

test("the referrer policy never sends a path or a trip id to another site", () => {
  assert.equal(REFERRER_POLICY, "strict-origin-when-cross-origin");
  // not weaker ...
  assert.ok(!["unsafe-url", "no-referrer-when-downgrade", "origin-when-cross-origin"].includes(REFERRER_POLICY));
  // ... and the reason it is not `no-referrer` is still true: tiles come from OpenStreetMap
  assert.match(page, /tile\.openstreetmap\.org/);
});

test("the permissions policy switches off every browser feature the app does not use", () => {
  const features = Object.fromEntries(PERMISSIONS_POLICY.split(", ").map((entry) => entry.split("=")));
  for (const feature of ["camera", "microphone", "geolocation", "payment", "usb", "display-capture", "browsing-topics"]) {
    assert.equal(features[feature], "()", feature);
  }
  assert.ok(Object.values(features).every((allowlist) => allowlist === "()"), "nothing is granted to anyone");
  // the app really uses none of them
  const sources = ["page.tsx", "AccountDataPanel.tsx", "BranchWorkspacePanel.tsx", "TravelerSections.tsx"]
    .map((name) => read(root, "app", name))
    .join("\n");
  assert.doesNotMatch(sources, /navigator\.(geolocation|mediaDevices|usb|clipboard)|getUserMedia|PaymentRequest/);
});

test("HSTS is opt-in, validated, and never promises more than this host", () => {
  for (const unset of [undefined, "", "   ", "0"]) assert.equal(hstsMaxAgeSeconds(unset), null);
  assert.equal(hstsMaxAgeSeconds("31536000"), 31536000);
  assert.equal(hstsMaxAgeSeconds(" 86400 "), 86400);
  for (const invalid of ["yes", "true", "-1", "1.5", "31536000; includeSubDomains", "1e9", "max-age=1"]) {
    assert.throws(() => hstsMaxAgeSeconds(invalid), /FRONTEND_HSTS_MAX_AGE_SECONDS/, invalid);
  }
  const value = headerMap(staticSecurityHeaders({ hstsMaxAgeSeconds: 86400 }))["Strict-Transport-Security"];
  assert.equal(value, "max-age=86400");
  assert.doesNotMatch(value, /includeSubDomains|preload/i);
});

test("next.config applies the enforced headers to every route and keeps the API rule", () => {
  assert.match(nextConfig, /source: "\/\(\.\*\)",\s+headers: staticSecurityHeaders\(/);
  assert.match(nextConfig, /hstsMaxAgeSeconds\(process\.env\.FRONTEND_HSTS_MAX_AGE_SECONDS\)/);
  // the session-scoped API responses stay uncacheable, and no other Cache-Control is introduced
  assert.match(nextConfig, /source: `\$\{API_PROXY_PREFIX\}\/:path\*`,\s+headers: \[\{ key: "Cache-Control", value: "no-store" \}\]/);
  const code = nextConfig
    .split("\n")
    .filter((line) => !line.trim().startsWith("//"))
    .join("\n");
  assert.equal(code.split("Cache-Control").length - 1, 1); // exactly that one rule
  // nothing the backend sets itself is configured here, so nothing of the backend's can be replaced
  assert.doesNotMatch(code, /Set-Cookie|X-Request-Id|Content-Security-Policy/);
  // the rewrite to the backend is exactly as before
  assert.match(nextConfig, /\{ source: `\$\{API_PROXY_PREFIX\}\/:path\*`, destination: `\$\{origin\}\/:path\*` \}/);
});

// -- the page policy ----------------------------------------------------------------------------------

test("the policy is sent Report-Only, and nothing claims it blocks anything", () => {
  assert.equal(CSP_HEADER_NAME, "Content-Security-Policy-Report-Only");
  assert.equal(proxy.split("CSP_HEADER_NAME").length - 1, 3); // import, request header, response header
  assert.doesNotMatch(proxy, /["']Content-Security-Policy["']/); // the enforcing name is never written out
  assert.match(proxy, /blocks nothing/);
  assert.match(doc, /\*\*Report-Only blocks nothing\.\*\*/);
  assert.match(doc, /Promoting the policy to enforcing/);
});

test("the production policy allows exactly what the application uses", () => {
  const policy = contentSecurityPolicy({ nonce: NONCE, apiBaseUrl: "/api" });
  assert.deepEqual(directives(policy), {
    "default-src": ["'self'"],
    "script-src": ["'self'", `'nonce-${NONCE}'`, "'strict-dynamic'"],
    "style-src": ["'self'", `'nonce-${NONCE}'`],
    "style-src-attr": ["'unsafe-inline'"],
    "img-src": ["'self'", "data:", "blob:", "https://*.tile.openstreetmap.org"],
    "font-src": ["'self'"],
    "connect-src": ["'self'"],
    "object-src": ["'none'"],
    "frame-src": ["'none'"],
    "frame-ancestors": ["'none'"],
    "base-uri": ["'self'"],
    "form-action": ["'self'"],
  });
});

test("scripts are never allowed inline or through eval in production", () => {
  for (const apiBaseUrl of ["/api", "http://localhost:8000", undefined]) {
    const policy = contentSecurityPolicy({ nonce: NONCE, apiBaseUrl });
    const script = directives(policy)["script-src"];
    assert.ok(!script.includes("'unsafe-inline'") && !script.includes("'unsafe-eval'"));
    assert.doesNotMatch(policy, /unsafe-eval|wasm-unsafe-eval|unsafe-hashes/);
    // no scheme-wide or wildcard source anywhere except the one tile host pattern
    const sources = Object.values(directives(policy)).flat();
    assert.deepEqual(sources.filter((source) => source.includes("*")), [MAP_TILE_SOURCE]);
    assert.ok(!sources.some((source) => ["*", "https:", "http:", "ws:", "wss:"].includes(source)));
    // the only 'unsafe-inline' is for style ATTRIBUTES
    assert.deepEqual(
      Object.entries(directives(policy)).filter(([, list]) => list.includes("'unsafe-inline'")).map(([name]) => name),
      ["style-src-attr"],
    );
  }
});

test("the map's needs are what the policy allows", () => {
  // tiles: the exact host pattern the page requests
  assert.match(page, /L\.tileLayer\("https:\/\/\{s\}\.tile\.openstreetmap\.org\/\{z\}\/\{x\}\/\{y\}\.png"/);
  assert.equal(MAP_TILE_SOURCE, "https://*.tile.openstreetmap.org");
  // Leaflet is bundled (a dynamic import of the package, its stylesheet imported by the layout): no CDN
  assert.match(page, /await import\("leaflet"\)/);
  assert.match(layout, /import "leaflet\/dist\/leaflet\.css";/);
  assert.doesNotMatch(page + layout, /unpkg\.com|cdnjs|jsdelivr|<link[^>]+leaflet/);
  // markers are divIcons with an inline style attribute -- the reason for `style-src-attr`
  assert.match(page, /L\.divIcon\(\{[\s\S]{0,80}html: `<div style="/);
  // links to other map sites are navigations, which a CSP does not restrict
  assert.match(page, /https:\/\/www\.google\.com\/maps\/search/);
});

test("the API origin is allowed only when the browser calls the backend directly", () => {
  assert.equal(apiOrigin("/api"), null);
  assert.equal(apiOrigin(undefined), null);
  assert.equal(apiOrigin(""), null);
  assert.equal(apiOrigin("http://localhost:8000"), "http://localhost:8000");
  assert.equal(apiOrigin("https://backend.example.test/some/path?x=1"), "https://backend.example.test");
  assert.equal(apiOrigin("https://user:secret@backend.example.test"), null); // never credentials
  for (const unusable of ["javascript:alert(1)", "//evil.example", "ftp://x", "http://", "not a url"]) {
    assert.equal(apiOrigin(unusable), null, unusable);
  }
  assert.deepEqual(
    directives(contentSecurityPolicy({ nonce: NONCE, apiBaseUrl: "http://localhost:8000" }))["connect-src"],
    ["'self'", "http://localhost:8000"],
  );
  // an API base URL can never inject a directive
  const hostile = contentSecurityPolicy({ nonce: NONCE, apiBaseUrl: "https://x.test; script-src *" });
  assert.deepEqual(directives(hostile)["script-src"], ["'self'", `'nonce-${NONCE}'`, "'strict-dynamic'"]);
});

test("development gets the two relaxations it needs and production never does", () => {
  const dev = directives(contentSecurityPolicy({ nonce: NONCE, apiBaseUrl: "http://localhost:8000", development: true }));
  assert.ok(dev["script-src"].includes("'unsafe-eval'")); // React's debugging overlay
  assert.deepEqual(dev["style-src"], ["'self'", "'unsafe-inline'"]);
  assert.deepEqual(dev["connect-src"], ["'self'", "http://localhost:8000"]);
  // wired to NODE_ENV === "development" only: a production build or `next start` is never development
  assert.match(proxy, /development: process\.env\.NODE_ENV === "development"/);
});

test("a nonce is unpredictable, fresh, and cannot break out of the header", () => {
  const nonces = new Set(Array.from({ length: 200 }, () => newNonce()));
  assert.equal(nonces.size, 200);
  for (const nonce of nonces) assert.match(nonce, /^[A-Za-z0-9+/=]{40,}$/);
  for (const bad of ["", "short", "abc'; script-src *", "x y z x y z x y z x y z"]) {
    assert.throws(() => contentSecurityPolicy({ nonce: bad }), /nonce/, bad);
  }
});

// -- wiring -------------------------------------------------------------------------------------------

test("the policy runs for documents only -- never for the API proxy or static assets", () => {
  const source = /source: "([^"]+)"/.exec(proxy)[1];
  const matcher = new RegExp(`^${source}$`);
  for (const path of ["/", "/no-such-page", "/trips/abc", "/apiary"]) assert.ok(matcher.test(path), path);
  for (const path of ["/api/trips", "/api/auth/login", "/api/health", "/_next/static/chunks/a.js", "/_next/image", "/favicon.ico"]) {
    assert.ok(!matcher.test(path), path);
  }
  // prefetches are skipped, as the framework recommends
  assert.match(proxy, /key: "next-router-prefetch"/);
  // it only adds headers: it never redirects, rewrites or answers a request itself
  assert.match(proxy, /NextResponse\.next\(\{ request: \{ headers: requestHeaders \} \}\)/);
  assert.doesNotMatch(proxy, /NextResponse\.(redirect|rewrite|json)|new Response\(/);
});

test("the nonce reaches Next.js and the one inline script", () => {
  // Next.js reads the nonce from the policy on the REQUEST headers (it accepts the Report-Only name)
  assert.match(proxy, /requestHeaders\.set\(CSP_HEADER_NAME, policy\);/);
  assert.match(proxy, /requestHeaders\.set\("x-nonce", nonce\);/);
  assert.match(proxy, /response\.headers\.set\(CSP_HEADER_NAME, policy\);/);
  // Next.js 16.4 moved the request-header parsing out of app-render.js into
  // parse-request-headers.js (same expression); the renderer takes the nonce
  // from that parsed result. The real server's behaviour is checked
  // end-to-end by `npm run check:headers`.
  const nextHeaders = read(
    root, "node_modules", "next", "dist", "server", "route-modules", "app-page", "parse-request-headers.js",
  );
  assert.match(nextHeaders, /headers\['content-security-policy'\] \|\| headers\['content-security-policy-report-only'\]/);
  assert.match(nextHeaders, /getScriptNonceFromHeader\)\(csp\)/);
  const nextRender = read(root, "node_modules", "next", "dist", "server", "app-render", "app-render.js");
  assert.match(nextRender, /const \{ flightRouterState, isPrefetchRequest, nonce \} = parsedRequestHeaders;/);
  // the layout puts the nonce on the application's own inline script, and is rendered per request
  assert.match(layout, /const nonce = \(await headers\(\)\)\.get\("x-nonce"\) \?\? undefined;/);
  assert.match(layout, /<script\s+nonce=\{nonce\}\s+dangerouslySetInnerHTML=/);
  assert.match(layout, /await connection\(\);/);
  // it is the only inline script the application writes
  assert.equal((layout + page).split("dangerouslySetInnerHTML").length - 1, 1);
});

test("the policy uses the same runtime API base URL the browser is given", () => {
  assert.match(proxy, /process\.env\.API_BASE_URL \?\? process\.env\.NEXT_PUBLIC_API_BASE_URL \?\? "http:\/\/localhost:8000"/);
  assert.match(layout, /sanitizeApiBaseUrl\(process\.env\.API_BASE_URL\)/);
  assert.match(read(root, "lib", "runtime-config.ts"), /process\.env\.NEXT_PUBLIC_API_BASE_URL \?\? "http:\/\/localhost:8000"/);
});

test("no secret is read by the frontend configuration or the policy code", () => {
  const sources = [nextConfig, proxy, layout, read(root, "lib", "security-headers.ts"), read(root, "lib", "runtime-config.ts")].join("\n");
  const read_env = [...sources.matchAll(/process\.env\.([A-Z0-9_]+)/g)].map((match) => match[1]);
  assert.deepEqual([...new Set(read_env)].sort(), [
    "API_BASE_URL",
    "BACKEND_ORIGIN",
    "FRONTEND_HSTS_MAX_AGE_SECONDS",
    "NEXT_PUBLIC_API_BASE_URL",
    "NODE_ENV",
  ]);
});

test("the promotion steps are documented, and the checks that pin Report-Only say so", () => {
  for (const step of [
    "With the developer console open throughout",
    "map tiles and numbered markers render",
    "change `CSP_HEADER_NAME`",
    "upgrade-insecure-requests",
    "keep the previous build ready to roll back",
  ]) {
    assert.ok(doc.includes(step), step);
  }
  assert.match(doc, /has not been exercised in a browser/);
  // the runtime check says what it does not prove
  assert.match(read(root, "scripts", "check-production-headers.mjs"), /Nothing here opens a\s+\/\/ browser/);
});
