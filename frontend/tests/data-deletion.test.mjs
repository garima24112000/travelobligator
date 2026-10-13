// P1-A: the privacy notice, and the trip / account deletion controls (confirmation, wording, what the
// page does afterwards). Run with `npm test` (Node's built-in test runner).

import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  ACCOUNT_DELETED_MESSAGE,
  ACCOUNT_DELETE_FAILED_MESSAGE,
  ACCOUNT_DELETION_CHECKBOX_LABEL,
  ACCOUNT_DELETION_WARNING,
  PRIVACY_NOTICE,
  PRIVACY_NOTICE_INTRO,
  PROJECT_ISSUES_URL,
  PROJECT_REPOSITORY_URL,
  TRIP_DELETED_MESSAGE,
  TRIP_DELETE_FAILED_MESSAGE,
  accountDeletionBlocker,
  tripDeletionQuestion,
} from "../lib/privacy-notice.ts";
import { travelerErrorMessage } from "../lib/traveler-view.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const repo = join(root, "..");
const read = (...parts) => readFileSync(join(...parts), "utf8");
const page = read(root, "app", "page.tsx");
const panel = read(root, "app", "AccountDataPanel.tsx");
const api = read(root, "lib", "api.ts");
const readme = read(repo, "README.md");

// The body of the account-deletion form's `submit`, up to the component's own JSX.
const submitSource = panel.slice(
  panel.indexOf("async function submit()"),
  panel.indexOf('aria-labelledby="delete-account-heading"'),
);
assert.ok(submitSource.includes("await deleteAccount(password)"), "submit() source not found");

const noticeText =[PRIVACY_NOTICE_INTRO, ...PRIVACY_NOTICE.flatMap((s) => [s.heading, ...s.points])].join("\n");

// -- privacy notice ---------------------------------------------------------------------------------

test("the notice covers what is stored, what is sent out, deletion, limits and contact", () => {
  assert.deepEqual(
    PRIVACY_NOTICE.map((section) => section.heading),
    [
      "What is stored",
      "What is sent to other services",
      "Deleting your data",
      "Limits you should know about",
      "Questions",
    ],
  );
  for (const expected of [
    /email address/,
    /salted hash of your password/,
    /destination, origin, dates/,
    /travel-data providers/,
    /AI providers/,
    /Delete button next to it in My trips/,
    /Delete account/,
    /cannot be undone/,
    /while its itinerary is being built/,
    /Database exports that the project operator keeps/,
    /Backups and logs kept by the hosting services/,
    /Password recovery is not available during this beta/,
  ]) {
    assert.match(noticeText, expected);
  }
});

test("cookie and storage statements say only what the application's own code does", () => {
  // What the wording claims ...
  assert.match(noticeText, /one sign-in cookie/);
  assert.match(noticeText, /your choice of view, kept in the browser's local storage/);
  assert.match(noticeText, /the only things this application's own code stores there/);
  // ... and what it explicitly does not vouch for
  assert.match(noticeText, /may set or read their own cookies or similar data; this project has not checked what they do/);
  // no blanket claim about cookies or tracking in general
  assert.doesNotMatch(noticeText, /sets no|no advertising|no tracking|does not track|never tracks/i);

  // Evidence for "one cookie": the backend sets a cookie in exactly one place, the session cookie.
  const backendFiles = (dir) =>
    readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
      const full = join(dir, entry.name);
      if (entry.isDirectory()) return entry.name === "tests" || entry.name === "__pycache__" ? [] : backendFiles(full);
      return entry.name.endsWith(".py") ? [full] : [];
    });
  const setters = backendFiles(join(repo, "backend", "app")).filter((file) => /\.set_cookie\(/.test(read(file)));
  assert.deepEqual(setters.map((file) => file.slice(file.indexOf("backend"))), ["backend/app/auth/sessions.py"]);
  assert.equal(read(repo, "backend", "app", "auth", "sessions.py").split(".set_cookie(").length - 1, 1);

  // Evidence for the browser side: no cookie is written by frontend code, and local storage holds one key.
  const frontendSources = ["app", "lib"].flatMap((dir) =>
    readdirSync(join(root, dir)).filter((name) => /\.tsx?$/.test(name)).map((name) => read(root, dir, name)),
  );
  const allFrontend = frontendSources.join("\n");
  assert.doesNotMatch(allFrontend, /document\.cookie/);
  const written = [...allFrontend.matchAll(/(?:localStorage|sessionStorage)\.setItem\(\s*([A-Za-z_"'.]+)/g)].map((m) => m[1]);
  assert.deepEqual(written, ["VIEW_MODE_STORAGE_KEY"]);
  // Evidence for "this application's own code": it ships no analytics or advertising package.
  const dependencies = Object.keys(JSON.parse(read(root, "package.json")).dependencies).sort();
  assert.deepEqual(dependencies, ["leaflet", "next", "react", "react-dom"]);
  assert.doesNotMatch(allFrontend, /next\/script|googletagmanager|gtag\(|@vercel\/analytics|posthog|plausible/i);
});

test("log statements separate the application's own logs from the hosting services' logs", () => {
  const stored = PRIVACY_NOTICE[0].points;
  const own = stored.find((point) => point.startsWith("The application's own logs"));
  const hosting = stored.find((point) => point.startsWith("Logs kept by the hosting services"));
  assert.ok(own && hosting, "both kinds of log must be described separately");

  // the application's own logs: a design statement, with how it was checked and where it can fail
  assert.match(own, /designed to leave out your email address, your password and the text of your trips/);
  assert.match(own, /checked in the code and its tests, not by inspecting the logs of the running service/);
  assert.match(own, /an unexpected error report could still contain fragments of a request/);
  assert.match(own, /account and trip identifiers/);
  // the hosting services' logs: not vouched for
  assert.match(hosting, /commonly include IP addresses and the web addresses requested, which contain trip identifiers/);
  assert.match(hosting, /has not independently verified what they record or for how long/);
  // no unqualified promise about log contents anywhere
  assert.doesNotMatch(noticeText, /logs (that )?(contain|do not contain|never contain)[^.]*but not your email/i);
  assert.doesNotMatch(noticeText, /never log|are never logged|is never logged/i);

  // Evidence for the design statement: the backend's log field allowlist has no email / password field,
  // and there is a redaction step every line passes through.
  const logging = read(repo, "backend", "app", "core", "logging_config.py");
  const allowlist = logging.slice(logging.indexOf("ALLOWED_EXTRA_FIELDS = frozenset("), logging.indexOf("SENSITIVE_LOG_FIELD_NAMES = frozenset("));
  assert.ok(allowlist.includes('"trip_id"') && allowlist.includes('"user_id"'));
  assert.doesNotMatch(allowlist, /^\s*"(email|password|feedback_text|destination|free_text[a-z_]*)",/m);
  assert.match(logging, /from app\.core\.redaction import redact/);
  // Evidence for "web addresses requested ... contain trip identifiers": the API paths do.
  assert.match(api, /`\/trips\/\$\{tripId\}/);
});

test("the notice makes no legal, compliance or absolute promise", () => {
  assert.doesNotMatch(
    noticeText,
    /GDPR|CCPA|HIPAA|compliant|compliance|certif|guarantee|we never|never share|fully secure|encrypted at rest|anonymi[sz]ed forever/i,
  );
  // no retention period is claimed for what the project does not control, or has not yet configured
  assert.match(noticeText, /has not yet set a retention period for these exports/);
  assert.match(noticeText, /removed on those services' own schedules, which this project has not verified/);
  // the only durations stated are ones the code enforces
  const durations = noticeText.match(/\b(\w+ (days?|hours?|months?|years?))\b/gi) ?? [];
  assert.deepEqual(durations.map((d) => d.toLowerCase()).sort(), ["24 hours", "seven days"]);
});

test("the durations in the notice are the configured ones", () => {
  const config = read(repo, "backend", "app", "core", "config.py");
  const setting = (name) => Number(new RegExp(`${name}: int = Field\\(\\s*default=(\\d+)`).exec(config)[1]);
  assert.equal(setting("session_ttl_seconds"), 7 * 24 * 3600); // "expires after seven days"
  assert.equal(setting("generation_limit_window_seconds"), 24 * 3600); // "kept for up to 24 hours"
});

test("every provider and host named in the notice is one the project documents", () => {
  const documented = read(repo, "docs", "23_v1_release_reference.md");
  for (const name of ["Geoapify", "Open-Meteo", "Nager.Date", "Frankfurter", "Groq", "Gemini", "Vercel", "Render", "Neon", "Upstash"]) {
    assert.ok(noticeText.includes(name), `${name} missing from the notice`);
    assert.ok(documented.includes(name), `${name} is not in the release reference`);
  }
  // the map tile host the browser really contacts
  assert.match(page, /tile\.openstreetmap\.org/);
  assert.match(noticeText, /OpenStreetMap/);
});

test("the only contact is the project's own public repository", () => {
  assert.equal(PROJECT_ISSUES_URL, `${PROJECT_REPOSITORY_URL}/issues`);
  assert.ok(readme.includes(PROJECT_REPOSITORY_URL), "the repository URL must be the one in the README");
  assert.doesNotMatch(noticeText + panel, /@[a-z0-9-]+\.[a-z]{2,}|mailto:|tel:|\+\d{6,}/i); // no invented email / phone
  assert.match(noticeText, /Issues are public, so do not include personal details/);
  assert.match(panel, /href=\{PROJECT_ISSUES_URL\}/);
  assert.match(panel, /rel="noopener noreferrer"/);
});

test("the notice is shown before sign-up and inside the account", () => {
  assert.equal(page.split("<PrivacyNotice").length - 1, 2);
  const signIn = page.slice(page.indexOf('"Create account"'), page.indexOf("AI Travel Decision Platform"));
  assert.match(signIn, /<PrivacyNotice/);
  // a native disclosure: operable without script, by keyboard and screen reader
  assert.match(panel, /<details/);
  assert.match(panel, /<summary/);
});

// -- deleting a trip --------------------------------------------------------------------------------

test("deleting a trip asks first, naming the trip and saying it cannot be undone", () => {
  assert.equal(
    tripDeletionQuestion("Lisbon, Portugal"),
    "Delete Lisbon, Portugal? Its itinerary, earlier versions and change requests will be removed. This cannot be undone.",
  );
  assert.match(tripDeletionQuestion(null), /^Delete this trip\?/);
  assert.match(tripDeletionQuestion("   "), /^Delete this trip\?/);
});

test("the Delete button only opens the confirmation; a second, explicit button deletes", () => {
  const list = page.slice(page.indexOf("myTrips.map((trip)"), page.indexOf("Create a new trip"));
  // the row's Delete button toggles the confirmation and never calls the API
  const rowButton = list.slice(list.indexOf("aria-label={`Delete trip: ${tripName}`}"), list.indexOf("{isConfirmingDelete && ("));
  assert.match(rowButton, /setPendingDeleteTripId\(isConfirmingDelete \? null : trip\.trip_id\)/);
  assert.doesNotMatch(rowButton, /handleDeleteTrip|deleteTrip\(/);
  // the request is only sent from inside the confirmation
  assert.equal(page.split("handleDeleteTrip(trip.trip_id)").length - 1, 1);
  const confirmation = list.slice(list.indexOf("{isConfirmingDelete && ("));
  assert.ok(confirmation.indexOf("tripDeletionQuestion(") < confirmation.indexOf("handleDeleteTrip(trip.trip_id)"));
  assert.match(confirmation, />\s*Cancel\s*</);
  assert.equal(page.split("deleteTrip(tripId)").length - 1, 1);
  assert.doesNotMatch(page, /window\.confirm|[^a-zA-Z]confirm\(/); // no blocking browser dialog
});

test("after a deletion the list is refreshed and an open trip is closed", () => {
  const handler = page.slice(page.indexOf("async function handleDeleteTrip("), page.indexOf("async function handleAccountDeleted("));
  const success = handler.slice(0, handler.indexOf("} catch (err) {"));
  assert.match(success, /await deleteTrip\(tripId\);/);
  assert.match(success, /openTripId === tripId \|\| readSelectedTripId\(\) === tripId/);
  for (const cleanup of ["setResult(null);", "writeSelectedTripId(null);", "clearJob();", "resetFeedbackPanelState();"]) {
    assert.ok(success.includes(cleanup), `missing ${cleanup}`);
  }
  assert.match(success, /ungeneratedTripRef\.current\?\.tripId === tripId\) ungeneratedTripRef\.current = null/);
  assert.match(success, /setMyTrips\(\(trips\) => trips\.filter\(\(trip\) => trip\.trip_id !== tripId\)\)/);
  assert.match(success, /setTripDeleteNotice\(TRIP_DELETED_MESSAGE\)/);
  assert.match(success, /void refreshMyTrips\(\);/);
  // nothing is cleaned up before the server confirmed the deletion
  assert.ok(success.indexOf("await deleteTrip(tripId);") < success.indexOf("setResult(null);"));
  // one deletion at a time
  assert.match(handler, /if \(deletingTripId !== null\) return;/);
});

test("a failed deletion keeps the trip and explains why", () => {
  const handler = page.slice(page.indexOf("async function handleDeleteTrip("), page.indexOf("async function handleAccountDeleted("));
  const failure = handler.slice(handler.indexOf("} catch (err) {"));
  assert.doesNotMatch(failure, /setResult\(null\)|trips\.filter/); // the trip stays on screen and in the list
  assert.match(failure, /handleAuthenticationRequired\(\)/);
  assert.match(failure, /err\.code === "TRIP_NOT_FOUND"/);
  assert.match(page, /tripDeleteError\?\.tripId === trip\.trip_id && \(\s*<p role="alert"/);

  const blocked = travelerErrorMessage({ code: "TRIP_DELETION_BLOCKED", status: 409, message: "x", operation: "delete_trip" });
  assert.match(blocked.message, /being built right now/);
  assert.equal(blocked.retryable, true);
  const forbidden = travelerErrorMessage({ code: "FORBIDDEN", status: 403, message: "x", operation: "delete_trip" });
  assert.equal(forbidden.message, "You do not have access to this trip.");
  const outage = travelerErrorMessage({ code: "PERSISTENCE_UNAVAILABLE", status: 503, message: "x", operation: "delete_trip" });
  assert.match(outage.message, /try again/i);
  for (const message of [blocked.message, forbidden.message, outage.message, TRIP_DELETED_MESSAGE, TRIP_DELETE_FAILED_MESSAGE]) {
    assert.doesNotMatch(message, /[A-Z]{3,}_[A-Z_]+|[a-z]+_[a-z_]+|409|503/);
  }
});

test("the delete controls are announced and reachable", () => {
  assert.match(page, /aria-label=\{`Delete trip: \$\{tripName\}`\}/);
  assert.match(page, /aria-expanded=\{isConfirmingDelete\}/);
  assert.match(page, /role="group"\s+aria-label=\{`Confirm deleting \$\{tripName\}`\}/);
  assert.match(page, /\{tripDeleteNotice && \(\s*<p role="status"/);
});

// -- deleting the account ---------------------------------------------------------------------------

test("the account cannot be deleted without the password and an explicit tick", () => {
  assert.match(accountDeletionBlocker({ password: "", confirmed: true }), /Enter your password/);
  assert.match(accountDeletionBlocker({ password: "secret", confirmed: false }), /Tick the box/);
  assert.equal(accountDeletionBlocker({ password: "secret", confirmed: true }), null);

  const submit = submitSource;
  assert.ok(submit.indexOf("accountDeletionBlocker(") < submit.indexOf("await deleteAccount(password)"));
  assert.match(submit, /if \(blocker\) \{\s*setError\(blocker\);\s*return;/);
  assert.match(submit, /if \(isDeleting\) return;/);
  // the form is closed until asked for, and the request is sent only from its submit
  assert.match(panel, /const \[open, setOpen\] = useState\(false\)/);
  assert.equal(panel.split("deleteAccount(").length - 1, 1);
  assert.match(panel, /type="password"\s+autoComplete="current-password"/);
  assert.match(panel, /type="checkbox"/);
  assert.match(ACCOUNT_DELETION_WARNING, /permanently deletes your account and every trip/);
  assert.match(ACCOUNT_DELETION_CHECKBOX_LABEL, /permanently deleted/);
});

test("the client always sends the confirmation the backend requires, with the password in the body", () => {
  const fn = api.slice(api.indexOf("export function deleteAccount("), api.indexOf("// Section 199A/199B/199C"));
  assert.match(fn, /"\/auth\/delete-account"/);
  assert.match(fn, /method: "POST"/);
  assert.match(fn, /JSON\.stringify\(\{ password, confirm: true \}\)/);
  assert.doesNotMatch(api, /delete-account\?|password=\$\{/); // never in a URL
  assert.match(api, /request<TripDeletedData>\(`\/trips\/\$\{tripId\}`, \{ method: "DELETE" \}\)/);
});

test("the password never outlives the attempt and is never logged or stored", () => {
  const submit = submitSource;
  assert.ok(submit.split('setPassword("")').length - 1 >= 2); // cleared on success and on failure
  assert.doesNotMatch(panel, /localStorage|sessionStorage|console\.|recordApiError/);
});

test("after the account is deleted the app is signed out and says so", () => {
  const handler = page.slice(page.indexOf("async function handleAccountDeleted()"), page.indexOf("async function handleSelectMyTrip("));
  assert.match(handler, /await handleLogout\(\);\s*setAuthNotice\(ACCOUNT_DELETED_MESSAGE\);/);
  assert.equal(ACCOUNT_DELETED_MESSAGE, "Your account and trips were deleted.");
  assert.match(page, /\{authNotice && !authError && \(\s*<p\s+role="status"/);
  // the next sign-in attempt clears the notice
  assert.match(page, /async function handleAuthSubmit\(\) \{\s*setAuthError\(null\);\s*setAuthNotice\(null\);/);
  // logging out drops every trip and deletion state of the previous user
  const logout = page.slice(page.indexOf("async function handleLogout()"), page.indexOf("async function resumeExistingJobIfAny("));
  for (const cleared of ["setMyTrips([]);", "setResult(null);", "setPendingDeleteTripId(null);", "setTripDeleteError(null);", "ungeneratedTripRef.current = null;"]) {
    assert.ok(logout.includes(cleared), `logout must do ${cleared}`);
  }
});

test("account deletion failures are specific and leave the user signed in", () => {
  const view = (code, status) =>
    travelerErrorMessage({ code, status, message: "x", operation: "delete_account" });
  assert.equal(view("INVALID_CREDENTIALS", 401).message, "That password is not correct. Nothing was deleted.");
  assert.match(view("ACCOUNT_DELETION_BLOCKED", 409).message, /being built right now.*Nothing was deleted/);
  assert.match(view("TOO_MANY_ATTEMPTS", 429).message, /wait a few minutes/);
  assert.match(view("PERSISTENCE_UNAVAILABLE", 503).message, /try again/i);
  // a wrong password on the sign-in form keeps its usual wording
  assert.equal(
    travelerErrorMessage({ code: "INVALID_CREDENTIALS", status: 401, message: "x", operation: "login" }).message,
    "Invalid email or password.",
  );
  const submit = submitSource;
  const failure = submit.slice(submit.indexOf("} catch (err) {"));
  assert.doesNotMatch(failure, /onDeleted\(\)/); // only a confirmed deletion signs the user out
  assert.match(failure, /err\.code === "AUTHENTICATION_REQUIRED"\) \{\s*onAuthenticationRequired\(\);/);
  assert.match(panel, /\{error && \(\s*<p role="alert"/);
  for (const message of [ACCOUNT_DELETE_FAILED_MESSAGE, view("INVALID_CREDENTIALS", 401).message]) {
    assert.doesNotMatch(message, /[A-Z]{3,}_[A-Z_]+|[a-z]+_[a-z_]+|401/);
  }
});
