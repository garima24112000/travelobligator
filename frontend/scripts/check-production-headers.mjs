// P1-B: runtime check of the PRODUCTION frontend server -- response security headers, the Report-Only
// Content Security Policy, the same-origin `/api` proxy, and what ends up in the browser assets.
//
//   npm run check:headers        (builds first; needs no backend, no network and no secret)
//
// It builds the app with a rewrite to a throwaway local stub "backend" and with SENTINEL values in the
// environment where real secrets would be, starts the real production server (`next start`), and then
// asks it for pages, an error page, a static asset and proxied API responses. Nothing here opens a
// browser: it proves what the server SENDS, not how a browser renders the map or reacts to the policy.

import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { createServer } from "node:http";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const nextBin = join(root, "node_modules", "next", "dist", "bin", "next");

// Values that must never reach the browser. Not real credentials: fixed markers that are easy to find.
const SENTINELS = {
  DATABASE_URL: "postgresql://owner:SENTINEL_P1B_DB_PASSWORD@db.invalid/app",
  REDIS_URL: "rediss://default:SENTINEL_P1B_REDIS_PASSWORD@cache.invalid:6379",
  SESSION_SECRET_KEY: "SENTINEL_P1B_SESSION_SECRET_0123456789abcdef",
  OPS_TOKEN: "SENTINEL_P1B_OPS_TOKEN_0123456789",
  GROQ_API_KEY: "SENTINEL_P1B_GROQ_KEY",
  GEMINI_API_KEY: "SENTINEL_P1B_GEMINI_KEY",
  GEOAPIFY_API_KEY: "SENTINEL_P1B_GEOAPIFY_KEY",
  ANTHROPIC_API_KEY: "SENTINEL_P1B_ANTHROPIC_KEY",
};

const checks = [];
const check = (name, fn) => checks.push([name, fn]);

function freePort() {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

/** The stand-in backend: answers like the real one (envelope, no-store, a session cookie, a request id). */
function startStubBackend(port) {
  const seen = [];
  const server = createServer((request, response) => {
    seen.push({ method: request.method, url: request.url, cookie: request.headers.cookie ?? null });
    // What the real backend puts on EVERY response itself (backend/app/core/no_store_middleware.py and
    // security_headers_middleware.py; asserted there by the backend's own tests).
    const base = {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
      "X-Request-Id": "req_stub_backend",
      "X-Content-Type-Options": "nosniff",
      "X-Frame-Options": "DENY",
      "Referrer-Policy": "no-referrer",
      "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    };
    if (request.url === "/auth/login") {
      response.writeHead(200, {
        ...base,
        "Set-Cookie": "travelobligator_session=stub-session; HttpOnly; Secure; SameSite=lax; Path=/",
      });
      response.end(JSON.stringify({ success: true, data: { user: { email: "stub@example.test" } }, errors: [] }));
    } else if (request.url === "/trips") {
      response.writeHead(200, base);
      response.end(JSON.stringify({ success: true, data: { trips: [] }, errors: [] }));
    } else if (request.url === "/trips/missing") {
      response.writeHead(404, base);
      response.end(JSON.stringify({ success: false, data: null, errors: [{ code: "TRIP_NOT_FOUND" }] }));
    } else if (request.url === "/boom") {
      response.writeHead(503, base);
      response.end(JSON.stringify({ success: false, data: null, errors: [{ code: "PERSISTENCE_UNAVAILABLE" }] }));
    } else {
      response.writeHead(404, base);
      response.end(JSON.stringify({ success: false, data: null, errors: [{ code: "NOT_FOUND" }] }));
    }
  });
  return new Promise((resolve) => server.listen(port, "127.0.0.1", () => resolve({ server, seen })));
}

async function waitForServer(url, child, output) {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`the frontend server exited early:\n${output.join("")}`);
    try {
      await fetch(url, { redirect: "manual" });
      return;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
  }
  throw new Error(`the frontend server did not start:\n${output.join("")}`);
}

function filesUnder(directory) {
  return readdirSync(directory).flatMap((name) => {
    const path = join(directory, name);
    return statSync(path).isDirectory() ? filesUnder(path) : [path];
  });
}

function parsePolicy(value) {
  return Object.fromEntries(
    value.split(";").map((directive) => {
      const [name, ...sources] = directive.trim().split(/\s+/);
      return [name, sources];
    }),
  );
}

const STATIC_HEADERS = {
  "x-content-type-options": "nosniff",
  "referrer-policy": "strict-origin-when-cross-origin",
  "x-frame-options": "DENY",
};

function assertStaticHeaders(response, where) {
  for (const [name, value] of Object.entries(STATIC_HEADERS)) {
    assert.equal(response.headers.get(name), value, `${where}: ${name}`);
  }
  const permissions = response.headers.get("permissions-policy") ?? "";
  for (const feature of ["camera=()", "microphone=()", "geolocation=()", "payment=()"]) {
    assert.ok(permissions.includes(feature), `${where}: Permissions-Policy lacks ${feature}`);
  }
}

async function main() {
  const backendPort = await freePort();
  const frontendPort = await freePort();
  const backendOrigin = `http://127.0.0.1:${backendPort}`;
  const frontend = `http://127.0.0.1:${frontendPort}`;
  const hsts = process.env.CHECK_HSTS === "1";

  const env = {
    ...process.env,
    ...SENTINELS,
    NEXT_TELEMETRY_DISABLED: "1",
    NODE_ENV: "production",
    BACKEND_ORIGIN: backendOrigin, // build time: creates the `/api/*` rewrite
    API_BASE_URL: "/api", // runtime: what the browser is told to call
    PORT: String(frontendPort),
    HOSTNAME: "127.0.0.1",
  };
  delete env.NEXT_PUBLIC_API_BASE_URL;
  if (hsts) env.FRONTEND_HSTS_MAX_AGE_SECONDS = "86400";
  else delete env.FRONTEND_HSTS_MAX_AGE_SECONDS;

  console.log(`== build (rewrite to a stub backend${hsts ? ", HSTS opted in" : ""})`);
  const build = spawnSync(process.execPath, [nextBin, "build"], { cwd: root, env, encoding: "utf8" });
  if (build.status !== 0) throw new Error(`next build failed:\n${build.stdout}\n${build.stderr}`);

  const { server: stub, seen } = await startStubBackend(backendPort);
  const output = [];
  const child = spawn(process.execPath, [nextBin, "start", "-p", String(frontendPort), "-H", "127.0.0.1"], {
    cwd: root,
    env,
  });
  child.stdout.on("data", (chunk) => output.push(String(chunk)));
  child.stderr.on("data", (chunk) => output.push(String(chunk)));

  try {
    await waitForServer(`${frontend}/`, child, output);

    // -- pages ---------------------------------------------------------------------------------------
    const page = await fetch(`${frontend}/`);
    const html = await page.text();
    const policyHeader = page.headers.get("content-security-policy-report-only");

    check("the page is served with the enforced security headers", () => {
      assert.equal(page.status, 200);
      assertStaticHeaders(page, "page /");
    });

    check("the policy is Report-Only: no enforcing Content-Security-Policy header is sent", () => {
      assert.ok(policyHeader, "Content-Security-Policy-Report-Only is missing on the page");
      assert.equal(page.headers.get("content-security-policy"), null);
    });

    check("the policy allows exactly the origins the application uses", () => {
      const policy = parsePolicy(policyHeader);
      assert.deepEqual(policy["default-src"], ["'self'"]);
      assert.deepEqual(policy["img-src"], ["'self'", "data:", "blob:", "https://*.tile.openstreetmap.org"]);
      assert.deepEqual(policy["connect-src"], ["'self'"]); // the API is the same-origin /api proxy
      assert.deepEqual(policy["font-src"], ["'self'"]);
      assert.deepEqual(policy["object-src"], ["'none'"]);
      assert.deepEqual(policy["frame-src"], ["'none'"]);
      assert.deepEqual(policy["frame-ancestors"], ["'none'"]);
      assert.deepEqual(policy["base-uri"], ["'self'"]);
      assert.deepEqual(policy["form-action"], ["'self'"]);
      assert.deepEqual(policy["style-src-attr"], ["'unsafe-inline'"]);
      // scripts: nonce + strict-dynamic only -- no inline, no eval, no wildcard, no extra host
      assert.equal(policy["script-src"].length, 3);
      assert.equal(policy["script-src"][0], "'self'");
      assert.match(policy["script-src"][1], /^'nonce-[A-Za-z0-9+/=]{16,}'$/);
      assert.equal(policy["script-src"][2], "'strict-dynamic'");
      assert.equal(policy["style-src"].length, 2);
      assert.match(policy["style-src"][1], /^'nonce-/);
      assert.doesNotMatch(policyHeader, /unsafe-eval|\*\s|\s\*;|https:\s|http:\s|script-src[^;]*unsafe-inline/);
      assert.ok(!policyHeader.includes(backendOrigin), "the backend origin must not appear in the policy");
    });

    check("every script in the page carries the response's nonce, including the runtime-config script", () => {
      const nonce = /'nonce-([^']+)'/.exec(policyHeader)[1];
      const scripts = [...html.matchAll(/<script\b([^>]*)>/g)].map((match) => match[1]);
      assert.ok(scripts.length >= 2, "expected the framework's scripts and the runtime-config script");
      for (const attributes of scripts) {
        assert.ok(attributes.includes(`nonce="${nonce}"`), `a script has no nonce: <script${attributes}>`);
      }
      assert.match(html, new RegExp(`<script nonce="${nonce.replace(/[+/=]/g, "\\$&")}">window\\.__TRAVELOBLIGATOR_RUNTIME__=`));
      assert.ok(html.includes('"apiBaseUrl":"/api"'), "the runtime API base URL is still serialised");
    });

    const second = await fetch(`${frontend}/`);
    await second.text();
    check("the nonce is new for every response", () => {
      const first = /'nonce-([^']+)'/.exec(policyHeader)[1];
      const next = /'nonce-([^']+)'/.exec(second.headers.get("content-security-policy-report-only"))[1];
      assert.notEqual(first, next);
    });

    const withQuery = await fetch(`${frontend}/?trip=trip_abc123`);
    await withQuery.text();
    check("a deep link to a trip gets the same headers", () => {
      assert.equal(withQuery.status, 200);
      assertStaticHeaders(withQuery, "page /?trip=");
      assert.ok(withQuery.headers.get("content-security-policy-report-only"));
    });

    // -- error page ----------------------------------------------------------------------------------
    const missing = await fetch(`${frontend}/no-such-page`);
    await missing.text();
    check("an error page carries the same protections", () => {
      assert.equal(missing.status, 404);
      assertStaticHeaders(missing, "404 page");
      assert.ok(missing.headers.get("content-security-policy-report-only"), "404 page: no CSP");
      assert.equal(missing.headers.get("content-security-policy"), null);
    });

    // -- static assets -------------------------------------------------------------------------------
    const asset = /\/_next\/static\/[^"']+\.js/.exec(html)?.[0];
    const assetResponse = asset ? await fetch(`${frontend}${asset}`) : null;
    if (assetResponse) await assetResponse.arrayBuffer();
    check("a static asset is served nosniff and without a per-response policy", () => {
      assert.ok(asset, "no script asset found in the page");
      assert.equal(assetResponse.status, 200);
      assertStaticHeaders(assetResponse, "static asset");
      assert.equal(assetResponse.headers.get("content-security-policy-report-only"), null);
      assert.match(assetResponse.headers.get("cache-control") ?? "", /immutable/); // assets stay cacheable
    });

    // -- the same-origin API proxy -------------------------------------------------------------------
    const login = await fetch(`${frontend}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: "stub@example.test", password: "x" }),
    });
    const loginBody = await login.json();
    const loginAsSeenByTheBackend = seen.at(-1);
    check("the API proxy forwards the backend's response and its session cookie unchanged", () => {
      assert.equal(login.status, 200);
      assert.equal(loginBody.data.user.email, "stub@example.test");
      const cookie = login.headers.get("set-cookie") ?? "";
      assert.match(cookie, /^travelobligator_session=stub-session;/);
      assert.match(cookie, /HttpOnly/i);
      assert.match(cookie, /Secure/i);
      assert.match(cookie, /SameSite=lax/i);
      assert.doesNotMatch(cookie, /Domain=/i);
      assert.equal(login.headers.get("x-request-id"), "req_stub_backend");
      // `/api` is stripped: the backend sees its own path and method
      assert.deepEqual(
        [loginAsSeenByTheBackend.method, loginAsSeenByTheBackend.url],
        ["POST", "/auth/login"],
      );
    });

    const trips = await fetch(`${frontend}/api/trips`, { headers: { Cookie: "travelobligator_session=stub-session" } });
    await trips.text();
    const notFound = await fetch(`${frontend}/api/trips/missing`);
    await notFound.text();
    const outage = await fetch(`${frontend}/api/boom`);
    await outage.text();
    // This server forwards a proxied response as the backend sent it: the headers in next.config.ts are
    // NOT added to it (a hosting platform may behave differently). What protects an API response is
    // therefore what the backend itself sets, and the proxy must not drop or replace any of it.
    check("no API response can be cached: the backend's no-store survives the proxy for every status", () => {
      for (const [response, status] of [[login, 200], [trips, 200], [notFound, 404], [outage, 503]]) {
        assert.equal(response.status, status);
        assert.equal(response.headers.get("cache-control"), "no-store", `status ${status}`);
      }
      assert.equal(seen.find((request) => request.url === "/trips").cookie, "travelobligator_session=stub-session");
    });

    check("the backend's own security headers survive the proxy, and the page policy is not put on API responses", () => {
      for (const response of [login, trips, notFound, outage]) {
        const where = `api ${response.status}`;
        assert.equal(response.headers.get("x-content-type-options"), "nosniff", where);
        assert.equal(response.headers.get("x-frame-options"), "DENY", where);
        // the backend's stricter values are kept, not replaced by the page's
        assert.equal(response.headers.get("referrer-policy"), "no-referrer", where);
        assert.equal(response.headers.get("content-security-policy"), "default-src 'none'; frame-ancestors 'none'", where);
        assert.equal(response.headers.get("content-security-policy-report-only"), null, where);
        assert.match(response.headers.get("content-type") ?? "", /application\/json/);
      }
    });

    check(hsts ? "HSTS is sent when it was opted into" : "HSTS is not sent unless it was opted into", () => {
      for (const response of [page, missing]) {
        const value = response.headers.get("strict-transport-security");
        // exactly max-age: never includeSubDomains or preload
        if (hsts) assert.equal(value, "max-age=86400");
        else assert.equal(value, null);
      }
      // a forwarded API response is not decorated by this server either way (see above)
      assert.equal(login.headers.get("strict-transport-security"), null);
    });

    // -- what the browser can download ---------------------------------------------------------------
    const browserAssets = filesUnder(join(root, ".next", "static"));
    check("no secret value and no backend address is in the browser assets", () => {
      assert.ok(browserAssets.length > 5, "no browser assets were built");
      const forbidden = [
        ...Object.values(SENTINELS),
        ...Object.values(SENTINELS).map((value) => value.split("@")[0].split(":").at(-1)),
        backendOrigin,
        `127.0.0.1:${backendPort}`,
      ];
      for (const file of browserAssets) {
        const content = readFileSync(file, "latin1");
        for (const value of forbidden) {
          assert.ok(!content.includes(value), `${file.slice(root.length + 1)} contains a value that must stay on the server`);
        }
        for (const name of Object.keys(SENTINELS)) {
          assert.ok(!content.includes(`process.env.${name}`), `${file.slice(root.length + 1)} reads ${name}`);
        }
      }
      assert.ok(!html.includes(backendOrigin) && !Object.values(SENTINELS).some((value) => html.includes(value)));
    });
  } finally {
    child.kill("SIGTERM");
    stub.close();
  }

  let failed = 0;
  for (const [name, fn] of checks) {
    try {
      fn();
      console.log(`ok   ${name}`);
    } catch (error) {
      failed += 1;
      console.log(`FAIL ${name}\n     ${String(error.message).split("\n").join("\n     ")}`);
    }
  }
  console.log(`\n${checks.length - failed} passed, ${failed} failed`);
  process.exitCode = failed ? 1 : 0;
}

await main();
