// Limited beta: the shareable invitation link in the browser. The token travels in the URL fragment,
// is removed from the address bar before anything else, and is sent to the backend in a JSON body;
// the backend alone decides. Run with `npm test` (Node's built-in test runner).

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  BETA_LINK_ACCEPTED_NOTICE,
  INVITE_REQUIRED_NOTICE,
  REGISTRATION_CLOSED_NOTICE,
  addressWithoutFragment,
  readBetaLinkToken,
  signupAccessNotice,
  takeBetaLinkToken,
} from "../lib/beta-access.ts";
import { travelerErrorMessage } from "../lib/traveler-view.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const page = readFileSync(join(root, "app", "page.tsx"), "utf8");
const api = readFileSync(join(root, "lib", "api.ts"), "utf8");

const TOKEN = "beta-test-token-" + "a".repeat(32);

test("the token is read from the fragment only", () => {
  assert.equal(readBetaLinkToken(`#beta=${TOKEN}`), TOKEN);
  assert.equal(readBetaLinkToken(`beta=${TOKEN}`), TOKEN);
  assert.equal(readBetaLinkToken(`#other=1&beta=${TOKEN}`), TOKEN);
  for (const hash of ["", "#", "#beta=", "#beta=%20", "#something", null, undefined, `#beta=${"x".repeat(257)}`]) {
    assert.equal(readBetaLinkToken(hash), null, String(hash));
  }
});

test("taking the token removes the fragment from the address bar, keeping path and query", () => {
  const calls = [];
  const history = { replaceState: (...args) => calls.push(args) };
  const location = { hash: `#beta=${TOKEN}`, pathname: "/", search: "?trip=t1" };
  assert.equal(takeBetaLinkToken(location, history), TOKEN);
  assert.deepEqual(calls, [[null, "", "/?trip=t1"]]);
  assert.ok(!calls[0][2].includes(TOKEN));
  assert.equal(addressWithoutFragment({ pathname: "/a", search: "" }), "/a");
});

test("without a link the address bar is left alone", () => {
  const calls = [];
  assert.equal(takeBetaLinkToken({ hash: "#section", pathname: "/", search: "" }, { replaceState: (...a) => calls.push(a) }), null);
  assert.deepEqual(calls, []);
});

test("the sign-up screen explains each registration state", () => {
  assert.equal(signupAccessNotice(null), null);
  assert.equal(signupAccessNotice({ mode: "open", authorized: true }), null);
  assert.equal(signupAccessNotice({ mode: "invite_link", authorized: false }), INVITE_REQUIRED_NOTICE);
  assert.equal(signupAccessNotice({ mode: "invite_link", authorized: true }), BETA_LINK_ACCEPTED_NOTICE);
  assert.equal(signupAccessNotice({ mode: "closed", authorized: false }), REGISTRATION_CLOSED_NOTICE);
  assert.equal(signupAccessNotice({ mode: "something-new", authorized: true }), REGISTRATION_CLOSED_NOTICE);
  for (const notice of [INVITE_REQUIRED_NOTICE, REGISTRATION_CLOSED_NOTICE]) assert.match(notice, /already have an account, log in/);
});

test("the token goes to the backend in a POST body, never in a URL", () => {
  assert.match(api, /request<RegistrationAccess>\("\/auth\/registration-access", \{\s*method: "POST",\s*body: JSON\.stringify\(\{ token \}\),/);
  assert.doesNotMatch(api, /registration-access\?|registration-access\/\$\{/);
});

test("the page removes the fragment before exchanging it, and never stores the token", () => {
  const effect = page.indexOf("takeBetaLinkToken(window.location, window.history)");
  const exchange = page.indexOf("await exchangeRegistrationLink(token)");
  assert.ok(effect > 0 && exchange > effect);
  assert.doesNotMatch(page, /(?:localStorage|sessionStorage)\.setItem\([^)]*[Tt]oken/);
  assert.doesNotMatch(page, /document\.cookie/);
  assert.match(page, /data-testid="signup-access-notice"[\s\S]{0,200}role="status"/);
});

test("a paused generation is worded for a traveller", () => {
  const worded = travelerErrorMessage({ code: "GENERATION_DISABLED", status: 503, message: "x", operation: "generate" });
  assert.equal(worded.retryable, false);
  assert.match(worded.message, /paused right now\. Your trips are saved/);
});
