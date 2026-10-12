import { TILE_HOST, expect, openGeneratedTrip, signUp, test } from "./support/app";

// Security headers and the Content Security Policy, on the PRODUCTION build, in a real browser.
//
// The page policy is Report-Only: the browser reports what it WOULD block and blocks nothing. These tests
// therefore prove two things only: which headers are sent, and that ordinary use of the application
// produces no report. They do not prove that an enforcing policy would be harmless -- that is a decision
// for a human after reading the reports (docs/22_free_tier_deployment.md, section 13C).
//
// Every other spec file also fails on any policy report (the `watch` fixture), so the journeys there are
// CSP evidence too.

function directives(policy: string): Record<string, string[]> {
  return Object.fromEntries(
    policy.split(";").map((directive) => {
      const [name, ...sources] = directive.trim().split(/\s+/);
      return [name, sources];
    }),
  );
}

test("the page is served with the enforced headers and a Report-Only policy", async ({ page }) => {
  const response = await page.goto("/");
  const headers = response!.headers();

  expect(headers["x-content-type-options"]).toBe("nosniff");
  expect(headers["x-frame-options"]).toBe("DENY");
  expect(headers["referrer-policy"]).toBe("strict-origin-when-cross-origin");
  expect(headers["permissions-policy"]).toContain("camera=()");
  expect(headers["permissions-policy"]).toContain("geolocation=()");
  // Report-Only, and no enforcing policy alongside it
  expect(headers["content-security-policy-report-only"]).toBeTruthy();
  expect(headers["content-security-policy"]).toBeUndefined();
  // no HSTS on this plain-HTTP origin
  expect(headers["strict-transport-security"]).toBeUndefined();

  const policy = directives(headers["content-security-policy-report-only"]);
  expect(policy["default-src"]).toEqual(["'self'"]);
  expect(policy["script-src"][0]).toBe("'self'");
  expect(policy["script-src"][1]).toMatch(/^'nonce-[A-Za-z0-9+/=]{16,}'$/);
  expect(policy["script-src"][2]).toBe("'strict-dynamic'");
  expect(policy["script-src"]).toHaveLength(3); // no unsafe-inline, no unsafe-eval, no host
  expect(policy["img-src"]).toEqual(["'self'", "data:", "blob:", "https://*.tile.openstreetmap.org"]);
  expect(policy["connect-src"]).toEqual(["'self'"]); // the API is same-origin through /api
  expect(policy["frame-ancestors"]).toEqual(["'none'"]);
  expect(policy["object-src"]).toEqual(["'none'"]);
});

test("the browser itself sees the page as not enforcing a policy, and the framework's scripts run", async ({ page }) => {
  await page.goto("/");
  // Next.js hydrated: the form is interactive, which needs every framework script to have executed
  await page.getByRole("button", { name: "Sign up" }).click();
  await expect(page.getByRole("heading", { name: "Create your account" })).toBeVisible();
  // the runtime configuration script ran and carries the nonce
  expect(await page.evaluate(() => (window as unknown as { __TRAVELOBLIGATOR_RUNTIME__?: { apiBaseUrl?: string } }).__TRAVELOBLIGATOR_RUNTIME__?.apiBaseUrl)).toBe("/api");
  const scripts = await page.locator("script").evaluateAll((nodes) =>
    nodes.map((node) => ({ src: node.getAttribute("src"), hasNonce: Boolean((node as HTMLScriptElement).nonce) })),
  );
  expect(scripts.length).toBeGreaterThan(2);
  expect(scripts.filter((script) => !script.hasNonce)).toEqual([]);
});

test("the policy check itself works: a resource from a disallowed origin is reported, and still not blocked", async ({ page, watch }) => {
  watch.allowCspViolations();
  watch.allowPageErrors();
  await page.route("https://disallowed.example.test/**", (route) =>
    route.fulfill({ status: 200, contentType: "image/png", body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==", "base64") }),
  );
  await page.goto("/");

  const loaded = await page.evaluate(
    () =>
      new Promise<boolean>((resolve) => {
        const image = new Image();
        image.onload = () => resolve(true);
        image.onerror = () => resolve(false);
        image.src = "https://disallowed.example.test/pixel.png";
      }),
  );

  await expect.poll(() => watch.cspViolations.length).toBeGreaterThan(0);
  const report = watch.cspViolations.find((violation) => violation.blocked.includes("disallowed.example.test"));
  expect(report?.directive).toBe("img-src");
  // "report", not "enforce": the image loaded anyway. Report-Only protects nothing.
  expect(report?.disposition).toBe("report");
  expect(loaded).toBe(true);
});

test("a full journey with maps produces no policy report at all", async ({ page, watch }) => {
  await signUp(page);
  await page.getByRole("form", { name: "Create a new trip" }).getByLabel("Destination").fill("Fixtureville, Testland");
  await page.getByRole("form", { name: "Create a new trip" }).locator('button[type="submit"]').click();
  await expect(page.getByRole("heading", { name: "Your itinerary, day by day" })).toBeVisible({ timeout: 30_000 });

  // every map: Leaflet loaded (a dynamically imported script), tiles and markers drawn
  const maps = page.locator(".leaflet-container");
  await expect(maps).toHaveCount(3);
  for (let index = 0; index < 3; index += 1) {
    await maps.nth(index).scrollIntoViewIfNeeded();
    await expect(maps.nth(index).locator("img.leaflet-tile").first()).toBeVisible();
    await expect(maps.nth(index).locator(".leaflet-marker-icon").first()).toBeVisible();
  }
  // the markers' inline style attribute (the reason for `style-src-attr`) took effect
  const marker = maps.first().locator(".leaflet-marker-icon div").first();
  expect(await marker.evaluate((node) => getComputedStyle(node).borderRadius)).not.toBe("0px");
  // interacting with the map (and letting the zoom finish: see DEFECT P1C-3 in itinerary.spec.ts)
  await maps.first().getByRole("button", { name: "Zoom in" }).click();
  await expect(maps.first().locator(".leaflet-zoom-anim")).toHaveCount(0);
  await page.waitForTimeout(400);
  // the rest of the application
  await page.getByRole("button", { name: "Developer view" }).click();
  await page.getByRole("button", { name: "Traveler view" }).click();
  await page.locator("details").filter({ hasText: "Your data and privacy" }).locator("summary").click();
  await page.getByRole("button", { name: /^Delete trip: / }).click();
  await page.getByRole("group", { name: /^Confirm deleting / }).getByRole("button", { name: "Delete trip" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Trip deleted." })).toBeVisible();
  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page.getByLabel("Email")).toBeVisible();

  expect(watch.cspViolations).toEqual([]);
  expect(watch.cspConsole).toEqual([]);
  expect(watch.tileRequests.length).toBeGreaterThan(0);
  expect(watch.tileRequests.every((host) => TILE_HOST.test(`https://${host}/`))).toBe(true);
});

test("API responses through the proxy are uncacheable and carry the backend's own headers", async ({ page }) => {
  await openGeneratedTrip(page);
  const seen: { path: string; status: number; headers: Record<string, string> }[] = [];
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (url.pathname.startsWith("/api/")) seen.push({ path: url.pathname, status: response.status(), headers: response.headers() });
  });

  await page.reload();
  await expect(page.getByRole("heading", { name: "Your itinerary, day by day" })).toBeVisible({ timeout: 20_000 });
  const denied = await page.request.get("/api/trips/trip_that_does_not_exist");

  expect(seen.length).toBeGreaterThan(8); // the whole plan was fetched through the proxy
  for (const response of seen) {
    expect(response.headers["cache-control"], response.path).toBe("no-store");
    expect(response.headers["x-content-type-options"], response.path).toBe("nosniff");
    expect(response.headers["x-frame-options"], response.path).toBe("DENY");
    expect(response.headers["content-security-policy"], response.path).toBe("default-src 'none'; frame-ancestors 'none'");
    expect(response.headers["content-type"], response.path).toContain("application/json");
    // authenticated data is never marked as shareable
    expect(response.headers["cache-control"]).not.toMatch(/public|max-age=[1-9]/);
  }
  expect(denied.status()).toBe(404);
  expect(denied.headers()["cache-control"]).toBe("no-store");
});

test("the session cookie works through the proxy and is not exposed or cached", async ({ page, context }) => {
  await signUp(page);
  const cookie = (await context.cookies()).find((entry) => entry.name === "travelobligator_session")!;
  expect(cookie.httpOnly).toBe(true);
  expect(cookie.sameSite).toBe("Lax");
  expect(cookie.path).toBe("/");

  // authenticated through the proxy
  const me = await page.request.get("/api/auth/me");
  expect(me.status()).toBe(200);
  expect(me.headers()["cache-control"]).toBe("no-store");
  expect(me.headers()["set-cookie"]).toBeUndefined(); // reading the session does not re-issue it
  // a second person in another browser context gets nothing of this session
  const stranger = await page.context().browser()!.newContext();
  expect((await stranger.request.get("http://127.0.0.1:3100/api/auth/me")).status()).toBe(401);
  await stranger.close();
});

test("the application cannot be framed by another page", async ({ page, watch }) => {
  watch.allowPageErrors(); // the browser logs the refused frame
  watch.allowCspViolations();
  await page.route("http://framing.example.test/", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: '<html><body><iframe id="victim" src="http://127.0.0.1:3100/" width="800" height="600"></iframe></body></html>',
    }),
  );
  await page.goto("http://framing.example.test/");

  const frame = page.frameLocator("#victim");
  // X-Frame-Options: DENY is enforced (unlike the Report-Only frame-ancestors): the form never renders
  await expect(frame.getByLabel("Email")).toHaveCount(0);
  await page.waitForTimeout(1000);
  await expect(frame.getByRole("heading", { name: "Log in to your account" })).toHaveCount(0);
});
