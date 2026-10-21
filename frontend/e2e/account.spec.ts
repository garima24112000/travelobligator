import { PASSWORD_LOSS_WARNING } from "../lib/beta-notice";
import { PASSWORD, createTripViaApi, expect, expectTripRows, generateViaApi, logIn, signUp, signUpViaApi, test } from "./support/app";

// D. Account data: the privacy notice and deleting the account.

const SECTIONS = ["What is stored", "What is sent to other services", "Deleting your data", "Limits you should know about", "Questions"];

function deletionRequests(page: import("@playwright/test").Page): number[] {
  const statuses: number[] = [];
  page.on("response", (response) => {
    if (response.url().endsWith("/api/auth/delete-account")) statuses.push(response.status());
  });
  return statuses;
}

test("the privacy notice can be read before signing up", async ({ page }) => {
  await page.goto("/");
  const notice = page.locator("details").filter({ hasText: "Your data and privacy" });
  await expect(notice).not.toHaveAttribute("open", "");
  await expect(notice.getByText("What is stored")).toBeHidden();

  await notice.locator("summary").click();

  for (const heading of SECTIONS) await expect(notice.getByRole("heading", { name: heading })).toBeVisible();
  await expect(notice).toContainText("a salted hash of your password");
  await expect(notice).toContainText("AI providers (Groq and Google Gemini)");
  // the five kinds of copy, told apart; the daily backups' expiry stated, none for the operator's exports
  await expect(notice).toContainText("Deleting removes your records from this app's own database, and only from there.");
  await expect(notice).toContainText("Each backup is deleted automatically about seven days after it is made.");
  await expect(notice).toContainText("Database exports that the project operator keeps:");
  await expect(notice).toContainText("This project has not yet set a retention period for these exports.");
  await expect(notice).toContainText("Backups and logs kept by the hosting services: deletion does not reach them.");
  await expect(notice).toContainText("What was already sent to the external services above to build an itinerary cannot be recalled.");
  await expect(notice).not.toContainText(/30[\s-]*days?/);
  // a lost password: the same sentence as the registration form, and deletion only after a code sent to the
  // account's own address (owner decisions D3a-D3c)
  await expect(notice).toContainText(PASSWORD_LOSS_WARNING);
  await expect(notice).toContainText("If you cannot sign in and want your account deleted: email garimaprachi2411@gmail.com");
  await expect(notice).toContainText("A message that merely comes from the account's address is not accepted as proof on its own.");
  await expect(notice).not.toContainText("does not delete accounts on request");
  await expect(notice).toContainText("in limited beta");
  await expect(notice).toContainText("Issues are public, so do not include personal details there.");
  const issues = notice.getByRole("link", { name: "Open an issue on GitHub" });
  await expect(issues).toHaveAttribute("href", "https://github.com/garima24112000/travelobligator/issues");
  await expect(issues).toHaveAttribute("rel", "noopener noreferrer");
  // it never claims more than it knows
  await expect(notice).not.toContainText(/GDPR|compliant|guarantee|never share/i);
});

test("the privacy notice opens from the keyboard, inside the account too", async ({ page }) => {
  await signUpViaApi(page);
  await page.goto("/");
  const notice = page.locator("details").filter({ hasText: "Your data and privacy" });
  await notice.locator("summary").focus();
  await page.keyboard.press("Enter");
  await expect(notice.getByRole("heading", { name: "Deleting your data" })).toBeVisible();
  await expect(notice).toContainText("Delete a trip with the Delete button next to it in My trips.");
  await page.keyboard.press("Enter");
  await expect(notice.getByRole("heading", { name: "Deleting your data" })).toBeHidden();
});

test("the account is not deleted without the password and an explicit confirmation", async ({ page }) => {
  await signUp(page);
  const requests = deletionRequests(page);
  const section = page.getByRole("region", { name: "Delete account" });
  await expect(section).toContainText("This permanently deletes your account and every trip in it. It cannot be undone.");
  // nothing destructive is on screen until it is asked for
  await expect(section.getByRole("button", { name: "Permanently delete my account" })).toHaveCount(0);

  await section.getByRole("button", { name: "Delete my account…" }).click();
  const confirm = section.getByRole("button", { name: "Permanently delete my account" });

  await confirm.click();
  await expect(section.getByRole("alert")).toHaveText("Enter your password to confirm it is you.");

  await section.getByLabel("Your password").fill(PASSWORD);
  await confirm.click();
  await expect(section.getByRole("alert")).toHaveText("Tick the box to confirm you want to delete your account.");
  expect(requests).toEqual([]); // nothing was sent either time

  // Cancel closes the form and forgets what was typed
  await section.getByRole("button", { name: "Cancel" }).click();
  await expect(confirm).toHaveCount(0);
  await section.getByRole("button", { name: "Delete my account…" }).click();
  await expect(section.getByLabel("Your password")).toHaveValue("");
  await expect(section.getByRole("checkbox")).not.toBeChecked();
  await expect(page.getByRole("heading", { name: "My trips" })).toBeVisible();
});

test("a wrong password deletes nothing and leaves the visitor signed in", async ({ page }) => {
  const email = await signUpViaApi(page);
  const tripId = await createTripViaApi(page, { destination: "Safeville, Testland" });
  await page.goto("/");
  const requests = deletionRequests(page);
  const section = page.getByRole("region", { name: "Delete account" });
  await section.getByRole("button", { name: "Delete my account…" }).click();
  await expect(section.getByText(email)).toBeVisible(); // it says which account

  await section.getByLabel("Your password").fill("not-my-password");
  await section.getByRole("checkbox").check();
  await section.getByRole("button", { name: "Permanently delete my account" }).click();

  await expect(section.getByRole("alert")).toHaveText("That password is not correct. Nothing was deleted.");
  expect(requests).toEqual([401]);
  await expect(section.getByLabel("Your password")).toHaveValue(""); // the wrong password is not kept
  await expect(page.getByRole("heading", { name: "My trips" })).toBeVisible();
  await expectTripRows(page, ["Safeville, Testland"]);
  expect((await page.request.get("/api/auth/me")).status()).toBe(200);
  expect((await page.request.get(`/api/trips/${tripId}`)).status()).toBe(200);
});

test("deleting the account removes it and every trip, and ends the session", async ({ page, context }) => {
  const email = await signUpViaApi(page);
  const trips = [await createTripViaApi(page, { destination: "First, Testland" }), await createTripViaApi(page, { destination: "Second, Testland" })];
  await generateViaApi(page, trips[0]);
  await page.goto(`/?trip=${trips[0]}`);
  await expect(page.getByRole("heading", { name: "Your itinerary, day by day" })).toBeVisible();
  const section = page.getByRole("region", { name: "Delete account" });

  await section.getByRole("button", { name: "Delete my account…" }).click();
  await section.getByLabel("Your password").fill(PASSWORD);
  await section.getByRole("checkbox").check();
  await section.getByRole("button", { name: "Permanently delete my account" }).click();

  // back at sign-in, told what happened, with nothing of the account left on screen
  await expect(page.getByRole("status").filter({ hasText: "Your account and trips were deleted." })).toBeVisible();
  await expect(page.getByLabel("Email")).toBeVisible();
  await expect(page.getByText("First, Testland")).toHaveCount(0);
  await expect(page.getByText(email)).toHaveCount(0);
  expect(new URL(page.url()).searchParams.get("trip")).toBeNull();
  // the session cookie is gone and the server no longer knows the session
  expect((await context.cookies()).find((cookie) => cookie.name === "travelobligator_session")).toBeUndefined();
  expect((await page.request.get("/api/auth/me")).status()).toBe(401);
  await page.reload();
  await expect(page.getByLabel("Email")).toBeVisible();
  await expect(page.getByRole("heading", { name: "My trips" })).toHaveCount(0);

  // the account can no longer be signed in to
  await logIn(page, email);
  await expect(page.getByRole("alert").filter({ hasText: "Invalid email or password." })).toBeVisible();
});

test("an account with an itinerary being built is not deleted", async ({ page }) => {
  await signUpViaApi(page);
  const tripId = await createTripViaApi(page, { destination: "Slowville, Testland" });
  await generateViaApi(page, tripId, { wait: false });
  await page.goto("/");
  const section = page.getByRole("region", { name: "Delete account" });

  await section.getByRole("button", { name: "Delete my account…" }).click();
  await section.getByLabel("Your password").fill(PASSWORD);
  await section.getByRole("checkbox").check();
  await section.getByRole("button", { name: "Permanently delete my account" }).click();

  await expect(section.getByRole("alert")).toContainText("One of your itineraries is being built right now");
  await expect(section.getByRole("alert")).toContainText("Nothing was deleted.");
  await expect(page.getByRole("heading", { name: "My trips" })).toBeVisible();
  expect((await page.request.get("/api/auth/me")).status()).toBe(200);
  expect((await page.request.get(`/api/trips/${tripId}`)).status()).toBe(200);
});

test("deleting one account leaves another person's account and trips alone", async ({ page, browser }) => {
  const otherContext = await browser.newContext();
  const other = await otherContext.newPage();
  const otherEmail = await signUpViaApi(other);
  const otherTrip = await createTripViaApi(other, { destination: "Bystander, Testland" });

  await signUp(page);
  const section = page.getByRole("region", { name: "Delete account" });
  await section.getByRole("button", { name: "Delete my account…" }).click();
  await section.getByLabel("Your password").fill(PASSWORD);
  await section.getByRole("checkbox").check();
  await section.getByRole("button", { name: "Permanently delete my account" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Your account and trips were deleted." })).toBeVisible();

  expect((await other.request.get("/api/auth/me")).status()).toBe(200);
  expect((await other.request.get(`/api/trips/${otherTrip}`)).status()).toBe(200);
  await other.goto("/");
  await expect(other.getByText(otherEmail)).toBeVisible();
  await expectTripRows(other, ["Bystander, Testland"]);
  await otherContext.close();
});
