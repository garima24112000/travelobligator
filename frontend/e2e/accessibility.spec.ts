import AxeBuilder from "@axe-core/playwright";
import type { Page, TestInfo } from "@playwright/test";

import {
  DEFAULT_TRIP,
  PASSWORD,
  createTripViaApi,
  expect,
  fillTripForm,
  generateViaApi,
  logIn,
  openGeneratedTrip,
  reshapeTrip,
  setReview,
  signUp,
  signUpViaApi,
  submitTripButton,
  test,
  uniqueEmail,
} from "./support/app";

// Accessibility, in a real browser.
//
// An automated scan finds a subset of problems (axe-core's own estimate is roughly a third to a half of
// WCAG issues). Passing these tests is NOT a claim of WCAG conformance. What cannot be checked here --
// how the page actually sounds in a screen reader, whether the reading order makes sense, whether the map
// alternative is sufficient -- is listed as outstanding manual work in e2e/README.md.

// WCAG 2.1 A and AA rules, plus axe's best-practice rules for headings / landmarks.
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"];

// Findings that are known, recorded as defects for review, and not yet fixed. A rule listed here does not
// fail the scan for that state; ANY other violation does, so nothing new can slip in. Remove an entry when
// its defect is fixed -- the test then guards the fix.
// (P1C-4 and P1C-5 were listed here for the sign-up screen until they were fixed; the list is empty again.)
const KNOWN_ISSUES: Record<string, { rule: string; defect: string }[]> = {};

async function scan(page: Page, testInfo: TestInfo, state: string) {
  const results = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  const summary = results.violations.map((violation) => ({
    rule: violation.id,
    impact: violation.impact,
    help: violation.help,
    nodes: violation.nodes.length,
    examples: violation.nodes.slice(0, 3).map((node) => ({ target: node.target.join(" "), summary: node.failureSummary })),
  }));
  await testInfo.attach(`axe-${state}.json`, { body: JSON.stringify(summary, null, 2), contentType: "application/json" });
  const known = new Set((KNOWN_ISSUES[state] ?? []).map((issue) => issue.rule));
  const unexpected = summary.filter((violation) => !known.has(violation.rule));
  expect(unexpected, `axe violations on "${state}" that are not recorded as known defects`).toEqual([]);
  // a known issue that no longer occurs must be removed from the list, so the fix stays guarded
  const stale = [...known].filter((rule) => !summary.some((violation) => violation.rule === rule));
  expect(stale, `known issues for "${state}" that no longer occur (remove them)`).toEqual([]);
}

test.describe("automated scan (axe-core, WCAG 2.1 A/AA rules)", () => {
  test("sign-in screen", async ({ page }, testInfo) => {
    await page.goto("/");
    await expect(page.getByLabel("Email")).toBeVisible();
    await scan(page, testInfo, "sign-in");
  });

  test("sign-up screen with an error and the privacy notice open", async ({ page }, testInfo) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Sign up" }).click();
    await page.getByLabel("Email").fill(uniqueEmail());
    await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
    await page.getByLabel("Confirm password").fill("something-else");
    await page.getByRole("button", { name: "Create account" }).click();
    await expect(page.getByText("Passwords do not match.")).toBeVisible();
    await page.locator("details").filter({ hasText: "Your data and privacy" }).locator("summary").click();
    await scan(page, testInfo, "sign-up-error");
  });

  test("signed-in home with the create-trip form", async ({ page }, testInfo) => {
    await signUp(page);
    // the settled screen: while the trip list loads, "Refresh trips" is disabled and half transparent
    // (an inactive control, which the contrast minimum does not apply to)
    const refresh = page.getByRole("button", { name: "Refresh trips" });
    await expect(refresh).toBeEnabled();
    await expect.poll(() => refresh.evaluate((button) => getComputedStyle(button).opacity)).toBe("1");
    await scan(page, testInfo, "home");
  });

  test("an itinerary in Traveler view, with maps and a material warning", async ({ page }, testInfo) => {
    await openGeneratedTrip(page);
    await expect(page.locator(".leaflet-container").first().locator("img.leaflet-tile").first()).toBeVisible();
    await scan(page, testInfo, "itinerary");
  });

  test("signed in, with the privacy notice open", async ({ page }, testInfo) => {
    await signUp(page);
    await expect(page.getByRole("button", { name: "Refresh trips" })).toBeEnabled();
    await page.locator("details").filter({ hasText: "Your data and privacy" }).locator("summary").click();
    await expect(page.getByRole("heading", { level: 3, name: "What is stored" })).toBeVisible();
    await scan(page, testInfo, "home-privacy-open");
  });

  test("a generation that produced no usable itinerary", async ({ page }, testInfo) => {
    await signUp(page);
    await fillTripForm(page, { ...DEFAULT_TRIP, destination: "Failville, Testland" });
    await submitTripButton(page).click();
    await expect(page.getByRole("alert").filter({ hasText: "We couldn't build an itinerary for this trip" })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("button", { name: "Refresh trips" })).toBeEnabled();
    await scan(page, testInfo, "no-usable-itinerary");
  });

  test("a blocked itinerary", async ({ page }, testInfo) => {
    await signUpViaApi(page);
    const tripId = await createTripViaApi(page);
    await generateViaApi(page, tripId);
    await reshapeTrip(page, tripId, {
      readiness: "blocked",
      validation: (report) =>
        setReview(report, {
          readiness: "blocked",
          blockingCodes: ["INSUFFICIENT_VERIFIED_INVENTORY"],
          classification: { INSUFFICIENT_VERIFIED_INVENTORY: "material" },
        }),
    });
    await page.goto(`/?trip=${tripId}`);
    await expect(page.getByRole("alert").filter({ hasText: "Not ready to use" })).toBeVisible({ timeout: 20_000 });
    await scan(page, testInfo, "blocked-itinerary");
  });

  test("the delete-trip confirmation and the delete-account form, both open", async ({ page }, testInfo) => {
    await openGeneratedTrip(page);
    await page.getByRole("button", { name: /^Delete trip: / }).click();
    const account = page.getByRole("region", { name: "Delete account" });
    await account.getByRole("button", { name: "Delete my account…" }).click();
    await account.getByRole("button", { name: "Permanently delete my account" }).click(); // shows its error
    await expect(account.getByRole("alert")).toBeVisible();
    await scan(page, testInfo, "delete-controls");
  });
});

test.describe("structure", () => {
  test("each screen has one main landmark, one top heading and no skipped heading level", async ({ page }) => {
    const outline = async () =>
      page.evaluate(() => ({
        mains: document.querySelectorAll("main, [role=main]").length,
        lang: document.documentElement.lang,
        title: document.title,
        headings: [...document.querySelectorAll("h1, h2, h3, h4, h5, h6")]
          // a heading inside a closed <details> is not exposed to anyone
          .filter((heading) => (heading as HTMLElement).offsetParent !== null && !heading.closest("details:not([open])"))
          .map((heading) => Number(heading.tagName.slice(1))),
      }));
    const check = (screen: Awaited<ReturnType<typeof outline>>) => {
      expect(screen.mains).toBe(1);
      expect(screen.lang).toBe("en");
      expect(screen.title.trim().length).toBeGreaterThan(0);
      expect(screen.headings.filter((level) => level === 1)).toHaveLength(1);
      expect(screen.headings[0]).toBe(1);
      screen.headings.reduce((previous, level) => {
        expect(level - previous, `heading level jumps from h${previous} to h${level}`).toBeLessThanOrEqual(1);
        return level;
      }, 0);
    };

    await page.goto("/");
    await expect(page.getByLabel("Email")).toBeVisible();
    check(await outline());

    await openGeneratedTrip(page);
    check(await outline());
    // signed in, the privacy notice sits under its own h2, so opening it keeps the outline whole
    await page.locator("details").filter({ hasText: "Your data and privacy" }).locator("summary").click();
    check(await outline());
  });

  // P1C-5 (fixed): on the sign-in screen the opened privacy notice used to put h3 sections directly under
  // the h1.
  test("P1C-5: the privacy notice opened on the sign-in screen skips no heading level", async ({ page }) => {
    await page.goto("/");
    await page.locator("details").filter({ hasText: "Your data and privacy" }).locator("summary").click();
    const levels = await page.evaluate(() =>
      [...document.querySelectorAll("h1, h2, h3, h4, h5, h6")]
        .filter((heading) => !heading.closest("details:not([open])"))
        .map((heading) => Number(heading.tagName.slice(1))),
    );
    expect(levels.slice(0, 2)).toEqual([1, 2]);
    // the whole notice: five sections, all one level below the h1, same words as when signed in
    expect(levels).toEqual([1, 2, 2, 2, 2, 2]);
  });

  test("P1C-5: the privacy notice says the same thing, with fitting heading levels, signed out and signed in", async ({ page }) => {
    const notice = page.locator("details").filter({ hasText: "Your data and privacy" });
    const read = async () => {
      await notice.locator("summary").click();
      return {
        text: await notice.innerText(),
        headings: await notice.locator("h1, h2, h3, h4, h5, h6").evaluateAll((nodes) => nodes.map((node) => `${node.tagName} ${node.textContent}`)),
      };
    };
    await page.goto("/");
    const signedOut = await read();
    await signUp(page);
    const signedIn = await read();

    expect(signedIn.text).toBe(signedOut.text); // not one word differs
    expect(signedOut.headings).toHaveLength(5);
    expect(signedOut.headings.every((heading) => heading.startsWith("H2 "))).toBe(true);
    // signed in, the notice sits under the h2 "Your account and data"
    expect(signedIn.headings).toEqual(signedOut.headings.map((heading) => heading.replace(/^H2 /, "H3 ")));
  });

  // P1C-4 (fixed): the password hint under the sign-up form used to be too faint (3.89:1).
  test("P1C-4: the sign-up password hint meets the 4.5:1 contrast minimum", async ({ page }, testInfo) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Sign up" }).click();
    const results = await new AxeBuilder({ page }).include("#password-hint").withRules(["color-contrast"]).analyze();
    expect(results.violations).toEqual([]);
    // axe must have actually judged it (a colour it cannot determine is "incomplete", not a pass)
    expect(results.incomplete).toEqual([]);
    expect(results.passes.map((pass) => pass.id)).toContain("color-contrast");

    // and measured directly from what the browser computed, not from a class name
    const measured = await page.locator("#password-hint").evaluate((hint) => {
      const channels = (color: string) => {
        const canvas = document.createElement("canvas");
        canvas.width = canvas.height = 1;
        const context = canvas.getContext("2d", { willReadFrequently: true })!;
        context.fillStyle = color;
        context.fillRect(0, 0, 1, 1);
        const [red, green, blue, alpha] = context.getImageData(0, 0, 1, 1).data;
        return { rgb: [red, green, blue], alpha: alpha / 255 };
      };
      // the backdrop: every ancestor's background, composited from the page background up
      const layers: { rgb: number[]; alpha: number }[] = [];
      for (let element: Element | null = hint; element; element = element.parentElement) {
        const style = getComputedStyle(element);
        if (style.backgroundImage !== "none") layers.push({ rgb: [], alpha: -1 }); // a gradient: cannot be reduced to one colour
        layers.push(channels(style.backgroundColor));
      }
      let backdrop = [255, 255, 255];
      let unknown = false;
      for (const layer of layers.reverse()) {
        if (layer.alpha === -1) {
          unknown = true;
          continue;
        }
        if (layer.alpha === 1) unknown = false; // an opaque layer above hides whatever was uncertain below
        backdrop = backdrop.map((value, index) => layer.rgb[index] * layer.alpha + value * (1 - layer.alpha));
      }
      const text = channels(getComputedStyle(hint).color);
      const foreground = text.rgb.map((value, index) => value * text.alpha + backdrop[index] * (1 - text.alpha));
      const luminance = (rgb: number[]) => {
        const [red, green, blue] = rgb.map((value) => {
          const channel = value / 255;
          return channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
        });
        return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
      };
      const [lighter, darker] = [luminance(foreground), luminance(backdrop)].sort((a, b) => b - a);
      return {
        ratio: (lighter + 0.05) / (darker + 0.05),
        unknown,
        color: getComputedStyle(hint).color,
        backdrop: backdrop.map(Math.round),
        fontSize: getComputedStyle(hint).fontSize,
      };
    });
    await testInfo.attach("password-hint-contrast.json", { body: JSON.stringify(measured, null, 2), contentType: "application/json" });
    console.log(`password hint: ${measured.color} on rgb(${measured.backdrop.join(", ")}) at ${measured.fontSize} = ${measured.ratio.toFixed(2)}:1`);
    expect(measured.unknown, "the backdrop could not be reduced to one colour").toBe(false);
    expect(measured.ratio).toBeGreaterThanOrEqual(4.5);
  });

  test("every form control has a label a screen reader will read", async ({ page }) => {
    await signUp(page);
    const unlabelled = await page.evaluate(() =>
      [...document.querySelectorAll("input, select, textarea")]
        .filter((control) => (control as HTMLElement).offsetParent !== null && (control as HTMLInputElement).type !== "hidden")
        .filter((control) => {
          const element = control as HTMLInputElement;
          return !(element.labels && element.labels.length > 0) && !element.getAttribute("aria-label") && !element.getAttribute("aria-labelledby");
        })
        .map((control) => control.outerHTML.slice(0, 120)),
    );
    expect(unlabelled).toEqual([]);
    // and the labels are the visible words
    const form = page.getByRole("form", { name: "Create a new trip" });
    for (const name of ["Destination", "Origin city", "Start date", "End date", "Travelers"]) {
      await expect(form.getByLabel(name, { exact: true })).toBeVisible();
    }
    // a <select> inside its <label>: the label text is followed by the options
    await expect(form.getByRole("combobox", { name: /^Pace/ })).toBeVisible();
    await expect(form.getByRole("combobox", { name: /^Travel group/ })).toBeVisible();
  });
});

test.describe("keyboard", () => {
  test("the sign-in form can be completed and submitted with the keyboard alone", async ({ page }) => {
    const email = await signUp(page);
    await page.getByRole("button", { name: "Log out" }).click();
    await expect(page.getByLabel("Email")).toBeVisible();

    await page.getByLabel("Email").focus();
    await page.keyboard.type(email);
    await page.keyboard.press("Tab");
    await expect(page.getByLabel("Password", { exact: true })).toBeFocused();
    await page.keyboard.type(PASSWORD);
    await page.keyboard.press("Enter");

    await expect(page.getByRole("heading", { name: "My trips" })).toBeVisible();
  });

  test("Tab moves through the sign-in screen in reading order and never gets stuck", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByLabel("Email")).toBeVisible();
    const order: string[] = [];
    for (let index = 0; index < 8; index += 1) {
      await page.keyboard.press("Tab");
      order.push(
        await page.evaluate(() => {
          const element = document.activeElement as HTMLElement | null;
          if (!element || element === document.body) return "(body)";
          return (element.getAttribute("aria-label") || element.innerText || element.getAttribute("type") || element.tagName).trim().slice(0, 30);
        }),
      );
    }
    expect(order.slice(0, 6)).toEqual(["Log in", "Sign up", "email", "password", "Log in", "Your data and privacy"]);
  });

  test("focus is always visible on the controls that matter", async ({ page }) => {
    await openGeneratedTrip(page);
    const targets = [
      page.getByRole("button", { name: "Log out" }),
      page.getByRole("button", { name: "Refresh trips" }),
      page.getByRole("button", { name: /^Delete trip: / }),
      page.getByRole("form", { name: "Create a new trip" }).getByLabel("Destination"),
      page.getByRole("form", { name: "Create a new trip" }).locator('button[type="submit"]'),
      page.getByRole("button", { name: "Developer view" }),
      page.getByRole("button", { name: /^Keep .* for future regeneration$/ }).first(),
      page.getByRole("link", { name: /^Open .* in OpenStreetMap$/ }).first(),
      page.locator("details").filter({ hasText: "Your data and privacy" }).locator("summary"),
      page.getByRole("button", { name: "Delete my account…" }),
    ];
    const invisible: string[] = [];
    for (const target of targets) {
      await target.scrollIntoViewIfNeeded();
      // keyboard focus (not a mouse click), so :focus-visible applies
      await target.focus();
      await page.keyboard.press("Shift+Tab");
      await page.keyboard.press("Tab");
      await expect(target).toBeFocused();
      const indicator = await target.evaluate((element) => {
        const style = getComputedStyle(element);
        return { outline: style.outlineStyle !== "none" && style.outlineWidth !== "0px", shadow: style.boxShadow !== "none" };
      });
      if (!indicator.outline && !indicator.shadow) invisible.push((await target.getAttribute("aria-label")) ?? (await target.innerText()));
    }
    expect(invisible, "controls with no visible focus indicator").toEqual([]);
  });

  test("deleting a trip can be done, and cancelled, with the keyboard alone", async ({ page }) => {
    await openGeneratedTrip(page, { destination: "Keyboardville, Testland" });
    const open = page.getByRole("button", { name: "Delete trip: Keyboardville, Testland" });

    await open.focus();
    await page.keyboard.press("Enter");
    const confirmation = page.getByRole("group", { name: "Confirm deleting Keyboardville, Testland" });
    await expect(confirmation).toBeVisible();
    // the confirmation's buttons are the next stops, in a safe order
    await page.keyboard.press("Tab");
    await expect(confirmation.getByRole("button", { name: "Delete trip" })).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(confirmation.getByRole("button", { name: "Cancel" })).toBeFocused();
    await page.keyboard.press("Space");
    await expect(confirmation).toHaveCount(0);

    await open.focus();
    await page.keyboard.press("Space");
    await expect(confirmation).toBeVisible();
    await page.keyboard.press("Tab");
    await expect(confirmation.getByRole("button", { name: "Delete trip" })).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("status").filter({ hasText: "Trip deleted." })).toBeVisible();
  });

  test("the map does not trap the keyboard", async ({ page }) => {
    await openGeneratedTrip(page);
    const map = page.locator(".leaflet-container").first();
    await map.scrollIntoViewIfNeeded();
    await map.getByRole("button", { name: "Zoom in" }).focus();
    const seen: string[] = [];
    for (let index = 0; index < 12; index += 1) {
      await page.keyboard.press("Tab");
      seen.push(await page.evaluate(() => (document.activeElement?.closest(".leaflet-container") ? "map" : "outside")));
    }
    expect(seen).toContain("outside"); // focus leaves the map by Tab alone
  });
});

test.describe("announcements", () => {
  test("errors and outcomes are in live regions a screen reader announces", async ({ page }) => {
    await page.goto("/");
    await logIn(page, uniqueEmail("nobody"), "wrong-password");
    const loginError = page.getByRole("alert").filter({ hasText: "Invalid email or password." });
    await expect(loginError).toBeVisible();

    await signUpViaApi(page);
    await page.goto("/");
    await fillTripForm(page, DEFAULT_TRIP);
    await page.getByRole("form", { name: "Create a new trip" }).getByLabel("End date").evaluate((input: HTMLInputElement) => input.removeAttribute("max"));
    await fillTripForm(page, { endDate: "2027-03-20" });
    await submitTripButton(page).click();
    await expect(page.getByRole("alert").filter({ hasText: "A trip can be at most 7 days long" })).toBeVisible();
  });

  test("a material warning is an alert; an informational one is a polite status", async ({ page }) => {
    await openGeneratedTrip(page);
    const material = page.getByText("Review needed before you follow this").locator("xpath=ancestor::*[@role][1]");
    await expect(material).toHaveAttribute("role", "alert");
    const coverage = page.getByRole("status", { name: "Not everything you asked for is covered" });
    await expect(coverage).toBeVisible();
    // nothing of the application's own interrupts beyond its alerts (the framework's route announcer aside)
    await expect(page.locator('main [aria-live="assertive"]:not([role="alert"])')).toHaveCount(0);
  });

  test("the confirmation controls say what they will do", async ({ page }) => {
    await openGeneratedTrip(page, { destination: "Namesville, Testland" });
    await expect(page.getByRole("button", { name: "Delete trip: Namesville, Testland" })).toHaveAttribute("aria-expanded", "false");
    await page.getByRole("button", { name: "Delete trip: Namesville, Testland" }).click();
    await expect(page.getByRole("button", { name: "Delete trip: Namesville, Testland" })).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByRole("group", { name: "Confirm deleting Namesville, Testland" })).toBeVisible();

    const account = page.getByRole("region", { name: "Delete account" });
    await account.getByRole("button", { name: "Delete my account…" }).click();
    await expect(account.getByRole("checkbox", { name: "I understand that my account and all my trips will be permanently deleted." })).toBeVisible();
    await expect(account.getByLabel("Your password")).toHaveAttribute("autocomplete", "current-password");
  });
});
