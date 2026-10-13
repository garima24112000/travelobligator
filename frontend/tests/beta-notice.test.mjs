// Release-copy alignment: the limited-beta labelling, the password-loss warning at registration and the
// privacy notice's statements about copies of data. Copy and wiring only -- no browser; the rendered page
// is checked in e2e/.
//
//   node --experimental-strip-types --test tests/beta-notice.test.mjs

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { BETA_LABEL, BETA_NOTICE_HEADING, BETA_NOTICE_POINTS, BETA_SUMMARY, PASSWORD_LOSS_WARNING } from "../lib/beta-notice.ts";
import { PRIVACY_NOTICE, PRIVACY_NOTICE_INTRO, PRIVACY_PASSWORD_LOSS_POINT } from "../lib/privacy-notice.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const page = readFileSync(join(root, "app", "page.tsx"), "utf8");
const component = readFileSync(join(root, "app", "BetaNotice.tsx"), "utf8");
const betaText = [BETA_LABEL, BETA_SUMMARY, BETA_NOTICE_HEADING, ...BETA_NOTICE_POINTS].join("\n");
const noticeText = [PRIVACY_NOTICE_INTRO, ...PRIVACY_NOTICE.flatMap((section) => [section.heading, ...section.points])].join("\n");
const limits = PRIVACY_NOTICE.find((section) => section.heading === "Limits you should know about").points;

test("the beta labelling says the five things a traveller needs to know", () => {
  assert.equal(BETA_LABEL, "Limited beta");
  assert.equal(BETA_NOTICE_POINTS.length, 5);
  const [experimental, sources, grounded, review, confirm] = BETA_NOTICE_POINTS;
  assert.match(experimental, /experimental travel planner/);
  assert.match(sources, /external travel-data providers and AI to construct itineraries/);
  // where a place comes from, and exactly what that does NOT establish -- each of the five things is named
  assert.match(grounded, /^Each place in an itinerary was identified by a travel-data provider\./);
  for (const notGuaranteed of ["current existence", "accessibility", "opening hours", "ticket availability", "transport options"]) {
    assert.ok(grounded.includes(notGuaranteed), notGuaranteed);
  }
  assert.match(grounded, /are not independently guaranteed\.$/);
  // it does not vouch for the place: no "real", "exists", "confirmed" or "on the map"
  assert.doesNotMatch(grounded, /\b(real place|exists|confirmed|on the map|genuine)\b/i);
  assert.match(review, /needing review needs your own judgement before you follow it/);
  assert.match(confirm, /Confirm the details that matter to your trip before relying on a plan/);
  // the one-line summary carries the same message
  assert.match(BETA_SUMMARY, /experimental travel planner in limited beta/);
  assert.match(BETA_SUMMARY, /external providers and AI/);
  assert.match(BETA_SUMMARY, /your own checks before you rely on them/);
});

test("the beta labelling never advertises a plan as complete, verified or ready", () => {
  // every positive use of these words would be a claim about itineraries in general
  for (const sentence of betaText.split(/(?<=[.!?])\s+|\n/)) {
    // "guaranteed" is allowed in exactly one form: the denial "are not independently guaranteed"
    const claims = /\b(verified|(?<!are not independently )guaranteed|complete|accurate|reliable|ready to follow|ready to use|fully checked)\b/i.exec(sentence);
    assert.equal(claims, null, `"${sentence}" makes a claim: ${claims?.[0]}`);
  }
  assert.equal(betaText.split("guaranteed").length - 1, 1);
  assert.ok(betaText.includes("are not independently guaranteed"));
  // it names no provider and no implementation detail, and promises no feature
  assert.doesNotMatch(betaText, /Geoapify|Groq|Gemini|PlanningState|validation report|backend/i);
  assert.doesNotMatch(betaText, /coming soon|will be available|shortly/i);
  // it is short enough to stay a label, not a page
  assert.ok(BETA_SUMMARY.length < 180);
  // (200, not 180: the point about provider-identified places names five things and is 197 characters)
  assert.ok(BETA_NOTICE_POINTS.every((point) => point.length < 200));
});

test("the beta labelling is on the signed-out and the signed-in screen, as text", () => {
  // the badge sits beside the product name on both screens
  assert.equal(page.split("<BetaBadge />").length - 1, 2);
  for (const match of page.matchAll(/TravelObligator\s*\n\s*<BetaBadge \/>/g)) assert.ok(match);
  assert.equal([...page.matchAll(/TravelObligator\s*\n\s*<BetaBadge \/>/g)].length, 2);
  // the five points are shown on both, never behind a click
  assert.equal(page.split("<BetaNotice ").length - 1, 2);
  assert.doesNotMatch(component, /<details|<summary|hidden|sr-only|aria-hidden/);
  // signed out: the summary under the heading, the points between the form and the privacy notice
  const signedOut = page.slice(page.indexOf('"Create your account" : "Log in to your account"'), page.indexOf("<PrivacyNotice sectionHeadingLevel={2}"));
  assert.ok(signedOut.includes("<BetaSummary ") && signedOut.includes("<BetaNotice "));
  assert.ok(signedOut.indexOf("</form>") < signedOut.indexOf("<BetaNotice "));
  // signed in: directly under the page introduction, above "Signed in as"
  const signedIn = page.slice(page.indexOf("AI Travel Decision Platform"), page.indexOf("Signed in as"));
  assert.ok(signedIn.includes("<BetaNotice "));
  // a labelled section with a real heading; the label is text, not colour alone
  assert.match(component, /<section\s+aria-labelledby=\{headingId\}/);
  assert.match(component, /<h2 id=\{headingId\}/);
  assert.match(component, /\{BETA_LABEL\}/);
  assert.doesNotMatch(component, /<aside|role="complementary"/); // would be a landmark nested inside <main>
});

test("the beta labelling changes nothing about how a plan's readiness is stated", () => {
  // the readiness wording and its sources are where they were
  for (const kept of ["Review needed before you follow this", "Not ready to use", "Checks passed", "Not everything you asked for is covered"]) {
    assert.ok(page.includes(kept) || readFileSync(join(root, "lib", "traveler-view.ts"), "utf8").includes(kept) || readFileSync(join(root, "lib", "plan-insights.ts"), "utf8").includes(kept), kept);
  }
  // the beta copy is not derived from, and does not feed, any plan state
  const betaModule = readFileSync(join(root, "lib", "beta-notice.ts"), "utf8");
  assert.doesNotMatch(betaModule, /^import /m);
  const componentCode = component.replace(/^\s*(\/\/|\*|\/\*).*$/gm, ""); // its code, without the comments
  assert.doesNotMatch(componentCode, /readiness|validation|reviewSeverity|result\b|useState|useEffect|fetch\(/);
  // the earlier guarantee still holds: no generic "ready" announcement came back with the new copy
  assert.doesNotMatch(page, /"Your itinerary is ready"|"Itinerary ready\. Opening it now\."/);
});

test("the password-loss warning is the exact sentence, shown in the registration form only", () => {
  assert.equal(
    PASSWORD_LOSS_WARNING,
    "Password recovery is not available during this beta. If you lose your password, you may lose access to your saved trips and may be unable to delete your account yourself.",
  );
  // rendered once, inside the sign-up branch of the form, before the submit button, as plain visible text
  assert.equal(page.split("{PASSWORD_LOSS_WARNING}").length - 1, 1);
  const form = page.slice(page.indexOf('id="auth-confirm-password"'), page.indexOf('? "Create account"'));
  const warning = form.indexOf('id="password-loss-warning"');
  assert.ok(warning > 0);
  const opening = form.lastIndexOf('{authMode === "signup" && (', warning);
  assert.ok(opening > 0 && !form.slice(opening, warning).includes(")}"), "the warning must be inside the sign-up branch");
  assert.ok(warning < form.indexOf('type="submit"'));
  assert.doesNotMatch(form.slice(warning, warning + 400), /sr-only|hidden|<details/);
  // the password field names it as part of its description, in sign-up only
  assert.match(page, /authMode === "signup" \? "password-hint password-loss-warning" : undefined/);
});

test("the privacy notice says the same about a lost password, and promises no recovery or assisted deletion", () => {
  assert.equal(PRIVACY_PASSWORD_LOSS_POINT, PASSWORD_LOSS_WARNING);
  assert.ok(limits.includes(PRIVACY_PASSWORD_LOSS_POINT));
  assert.match(noticeText, /no verified way to confirm that an account belongs to someone who cannot sign in to it/);
  assert.match(noticeText, /does not delete accounts on request for people who have lost their password/);
  assert.doesNotMatch(noticeText, /There is no password reset yet/); // the old wording is gone, not duplicated
  assert.doesNotMatch(noticeText, /reset your password|password reset is available|recover your password by/i);
  assert.doesNotMatch(noticeText, /(contact|email|write to) (us|the operator|the project) (to|and we)/i);
  assert.doesNotMatch(noticeText, /we (will|can) delete your account/i);
  assert.match(PRIVACY_NOTICE_INTRO, /in limited beta/);
});

test("the privacy notice tells four kinds of copy apart and states no retention period", () => {
  const [database, exports_, hosting, external] = limits.slice(1, 5);
  // 1. the application's own database: what deletion does, and that it does nothing else
  assert.match(database, /^Deleting removes your records from this app's own database, and only from there\./);
  // 2. exports kept by the operator: possible, temporary, not edited on deletion, no period set
  assert.match(exports_, /^Database exports that the project operator keeps:/);
  assert.match(exports_, /the operator may export a copy of the database/);
  assert.match(exports_, /may be kept temporarily/);
  assert.match(exports_, /is not edited when you delete a trip or your account/);
  assert.match(exports_, /can remain in an export made before you deleted it, until that export is disposed of/);
  assert.match(exports_, /has not yet set a retention period for these exports/);
  // true whether or not an export has been made yet: it never says one exists or is always made
  assert.doesNotMatch(exports_, /\b(is exported|are exported|always|every deployment|each deployment|regularly|daily|weekly)\b/i);
  // 3. the hosting services' backups and logs: not reached, not vouched for
  assert.match(hosting, /^Backups and logs kept by the hosting services: deletion does not reach them\./);
  assert.match(hosting, /which this project has not verified/);
  // 4. what was already sent out
  assert.match(external, /^What was already sent to the external services above .* cannot be recalled\./);
  assert.match(external, /according to their own policies/);

  // no number of days, weeks or months for any copy the project does not control or has not configured
  for (const point of [exports_, hosting, external]) {
    assert.doesNotMatch(point, /\d/, point);
    assert.doesNotMatch(point, /\b(encrypted|securely stored|access-controlled|automatically deleted)\b/i, point);
  }
  assert.doesNotMatch(noticeText, /30[\s-]*days?/);
  // the one period the notice does state is the one the application itself enforces
  assert.equal([...noticeText.matchAll(/\b\d+\s*(hours?|days?|weeks?|months?)\b/g)].map((match) => match[0]).join(), "24 hours");
  assert.match(noticeText, /expires after seven days/); // the sign-in cookie, set by the application's own code
});
