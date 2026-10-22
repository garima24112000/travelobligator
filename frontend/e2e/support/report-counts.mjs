// Prints what the browser run did, per viewport, from Playwright's JSON report:
//
//   node e2e/support/report-counts.mjs e2e-results/results.json
//
// passed            ran and passed
// failed            ran and failed (or passed although marked as an expected failure)
// expected failure  a test marked `test.fail()`: a reported application defect that is not fixed yet. It
//                   ran, and failed as recorded. These are listed by name so they cannot go unnoticed.
// skipped           did not run. Nothing in this suite is meant to skip, so any skip fails this script.
//
// In GitHub Actions the same table is written to the job summary.

import { appendFileSync, readFileSync } from "node:fs";

const file = process.argv[2] ?? "e2e-results/results.json";
let report;
try {
  report = JSON.parse(readFileSync(file, "utf8"));
} catch (error) {
  console.error(`No readable Playwright report at ${file}: ${error.message}`);
  process.exit(1);
}

const counts = new Map(); // project -> { passed, failed, expectedFailure, skipped }
const expectedFailures = [];
const failures = [];
const skips = [];

function visit(suite, trail) {
  for (const spec of suite.specs ?? []) {
    for (const test of spec.tests ?? []) {
      const row = counts.get(test.projectName) ?? { passed: 0, failed: 0, expectedFailure: 0, skipped: 0 };
      counts.set(test.projectName, row);
      const name = `[${test.projectName}] ${[...trail, spec.title].join(" › ")}`;
      if (test.status === "skipped") {
        row.skipped += 1;
        skips.push(name);
      } else if (test.status === "expected" && test.expectedStatus === "failed") {
        row.expectedFailure += 1;
        expectedFailures.push(name);
      } else if (test.status === "expected") {
        row.passed += 1;
      } else {
        row.failed += 1; // "unexpected" or "flaky"
        failures.push(name);
      }
    }
  }
  for (const child of suite.suites ?? []) visit(child, child.title && child.title !== suite.file ? [...trail, child.title] : trail);
}
for (const suite of report.suites ?? []) visit(suite, [suite.title]);

const lines = ["| viewport | passed | failed | expected failure | skipped |", "| --- | ---: | ---: | ---: | ---: |"];
const total = { passed: 0, failed: 0, expectedFailure: 0, skipped: 0 };
for (const [project, row] of counts) {
  lines.push(`| ${project} | ${row.passed} | ${row.failed} | ${row.expectedFailure} | ${row.skipped} |`);
  for (const key of Object.keys(total)) total[key] += row[key];
}
lines.push(`| **total** | ${total.passed} | ${total.failed} | ${total.expectedFailure} | ${total.skipped} |`);
const list = (title, names) => (names.length ? ["", `**${title}**`, "", ...names.map((name) => `- ${name}`)] : []);
const text = [
  "## Browser E2E",
  "",
  ...lines,
  ...list("Expected failures (reported application defects, not fixed)", expectedFailures),
  ...list("Failed", failures),
  ...list("Skipped", skips),
  "",
].join("\n");

console.log(text);
if (process.env.GITHUB_STEP_SUMMARY) appendFileSync(process.env.GITHUB_STEP_SUMMARY, text);

if (counts.size === 0) {
  console.error("The report contains no test.");
  process.exit(1);
}
if (skips.length > 0) {
  console.error(`${skips.length} test(s) skipped; none is meant to.`);
  process.exit(1);
}
