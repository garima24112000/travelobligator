import { PASSWORD, expect, logIn, signUp, test, uniqueEmail } from "./support/app";

// A. Authentication, through the real forms and the real session cookie.

test("a visitor signs up and lands in their own, empty account", async ({ page, context }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "Log in to your account" })).toBeVisible();

  const email = await signUp(page);

  await expect(page.getByText("You have not created any trips yet.")).toBeVisible();
  await expect(page.getByRole("form", { name: "Create a new trip" })).toBeVisible();
  // the session is an HttpOnly cookie on this origin: page scripts cannot read it
  const session = (await context.cookies()).find((cookie) => cookie.name === "travelobligator_session");
  expect(session, "session cookie").toBeTruthy();
  expect(session?.httpOnly).toBe(true);
  expect(session?.sameSite).toBe("Lax");
  expect(session?.domain).toBe("127.0.0.1"); // host-only: set through the same-origin /api proxy
  expect(await page.evaluate(() => document.cookie)).not.toContain("travelobligator_session");
  expect(await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }))).not.toContain(email);
});

test("sign-up refuses mismatched passwords and a too-short password without creating an account", async ({ page }) => {
  const email = uniqueEmail();
  const signups: number[] = [];
  page.on("response", (response) => {
    if (response.url().endsWith("/api/auth/signup")) signups.push(response.status());
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Sign up" }).click();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByLabel("Confirm password").fill(`${PASSWORD}-different`);
  await page.getByRole("button", { name: "Create account" }).click();

  await expect(page.getByText("Passwords do not match.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Create your account" })).toBeVisible();
  expect(signups).toEqual([]); // nothing was sent

  await logIn(page, email);
  await expect(page.getByRole("alert").filter({ hasText: "Invalid email or password." })).toBeVisible();
});

test("an email that is already registered cannot sign up again", async ({ page, browser }) => {
  const email = await signUp(page);
  const other = await browser.newContext();
  const visitor = await other.newPage();
  await visitor.goto("/");
  await visitor.getByRole("button", { name: "Sign up" }).click();
  await visitor.getByLabel("Email").fill(email);
  await visitor.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await visitor.getByLabel("Confirm password").fill(PASSWORD);
  await visitor.getByRole("button", { name: "Create account" }).click();
  await expect(visitor.getByRole("alert").filter({ hasText: "An account with this email already exists." })).toBeVisible();
  await expect(visitor.getByRole("heading", { name: "My trips" })).toHaveCount(0);
  await other.close();
});

test("log out ends the session, and logging in again restores the account", async ({ page }) => {
  const email = await signUp(page);

  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Log in to your account" })).toBeVisible();
  expect((await page.request.get("/api/auth/me")).status()).toBe(401);
  // going back to the page does not bring the account back
  await page.reload();
  await expect(page.getByRole("heading", { level: 1, name: "Log in to your account" })).toBeVisible();

  await logIn(page, email);
  await expect(page.getByRole("heading", { name: "My trips" })).toBeVisible();
  await expect(page.getByText(email)).toBeVisible();
});

test("wrong credentials are refused with one generic message, for a real and an unknown email alike", async ({ page }) => {
  const email = await signUp(page);
  await page.getByRole("button", { name: "Log out" }).click();

  const messages: string[] = [];
  for (const attempt of [
    { email, password: "not-the-password" },
    { email: uniqueEmail("nobody"), password: PASSWORD },
  ]) {
    await logIn(page, attempt.email, attempt.password);
    const alert = page.getByRole("alert").filter({ hasText: /\S/ }).first();
    await expect(alert).toBeVisible();
    messages.push((await alert.innerText()).trim());
    await expect(page.getByRole("heading", { name: "My trips" })).toHaveCount(0);
  }
  expect(messages).toEqual(["Invalid email or password.", "Invalid email or password."]);
  expect((await page.request.get("/api/auth/me")).status()).toBe(401);
});

test("the session survives a refresh and a new tab", async ({ page, context }) => {
  const email = await signUp(page);

  await page.reload();
  await expect(page.getByRole("heading", { name: "My trips" })).toBeVisible();
  await expect(page.getByText(email)).toBeVisible();

  const tab = await context.newPage();
  await tab.goto("/");
  await expect(tab.getByText(email)).toBeVisible();
  await tab.close();
});

test("a session that ended elsewhere sends the visitor back to sign-in with an explanation", async ({ page, context }) => {
  await signUp(page);
  await context.clearCookies(); // the session ends (expired or cleared) while the page is still open

  // the next action that needs the session says what happened; nothing of the account stays on screen
  await page.getByRole("form", { name: "Create a new trip" }).locator('button[type="submit"]').click();
  await expect(page.getByRole("alert").filter({ hasText: "Your session has ended. Please log in again." })).toBeVisible();
  await expect(page.getByRole("heading", { name: "My trips" })).toHaveCount(0);
  await expect(page.getByRole("form", { name: "Create a new trip" })).toHaveCount(0);
  await expect(page.getByLabel("Email")).toBeVisible();
});

// P1C-1 (fixed): an account that signed UP in this tab used to be sent back to the SIGN-UP form when its
// session ended, under the message "Your session has ended. Please log in again."
test("P1C-1: after a session ends the log-in form is shown, as the message says", async ({ page, context }) => {
  const email = await signUp(page);
  await context.clearCookies();
  await page.getByRole("form", { name: "Create a new trip" }).locator('button[type="submit"]').click();
  await expect(page.getByRole("alert").filter({ hasText: "Your session has ended." })).toBeVisible();
  await expect(page.getByRole("heading", { level: 1, name: "Log in to your account" })).toBeVisible({ timeout: 3000 });

  // it is the log-in form in every respect, and logging in from it works
  await expect(page.getByRole("button", { name: "Log in" }).first()).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByLabel("Confirm password")).toHaveCount(0);
  await expect(page.getByLabel("Password", { exact: true })).toHaveValue("");
  await expect(page.getByLabel("Password", { exact: true })).toHaveAttribute("autocomplete", "current-password");
  await logIn(page, email, PASSWORD);
  await expect(page.getByText(`Signed in as ${email}`)).toBeVisible();
});
