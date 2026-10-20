#!/usr/bin/env python3
"""Production monitoring, run by `.github/workflows/production-monitor.yml`. Standard library only.

    production_monitor.py health    backend liveness (GET /health), with an allowance for a free-tier cold start
    production_monitor.py backups   freshness of the encrypted Neon backups in S3, and that the backup workflow
                                    and its schedule are still switched on
    production_monitor.py alert     open, update or close one GitHub issue per check; optional heartbeat ping

Thresholds, notifications and the owner's response checklist: docs/31_production_monitoring.md.

Exit codes of `health` and `backups`: 0 ok, 1 a problem was detected, 2 the check itself could not run (bad
configuration, no credentials, an API error). `alert`: 0 nothing is wrong, 1 a problem is open or delivering the
alert failed. Each check writes `status` (ok | problem | error) and a one-line `summary` to $GITHUB_OUTPUT.

What it never does: send anything but GET to the backend (no sign-in, registration or generation endpoint, no
planning provider), download or decrypt a backup, print a response body, an object key, a token or a URL from a
secret. Its output is fixed wording, counts, ages, durations and HTTP status codes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

DEFAULT_BACKEND = "https://travelobligator-backend.onrender.com"
HEALTH_PATH = "/health"
USER_AGENT = "travelobligator-production-monitor"
# A warm backend answers in well under a second. A sleeping Render Free instance holds the first request while
# it starts, which takes tens of seconds; a request that slow is a cold start, not a fault.
WARM_SECONDS = 10.0
ATTEMPT_TIMEOUT_SECONDS = 60.0
# Waits before attempts 2, 3 and 4. With the per-attempt timeout the check ends within about eight minutes; a
# backend that has not answered correctly by then is down, not starting.
RETRY_DELAYS_SECONDS = (20.0, 60.0, 120.0)
MAX_BODY_BYTES = 65_536

BACKUP_PREFIX = "neon/"
# The backup runs daily at 03:37 UTC. 36 hours = one full interval, plus 12 hours for GitHub's scheduling delays
# and one retried run, before a missing day becomes an alert.
MAX_BACKUP_AGE_HOURS = 36.0
# An encrypted dump of even an empty application schema is tens of kilobytes; anything under 1 KiB is broken.
MIN_BACKUP_BYTES = 1024
FUTURE_TOLERANCE = timedelta(minutes=10)
BACKUP_KEY = re.compile(r"^neon/travelobligator-\d{8}T\d{6}Z-run(?:\d+|local)-\d+\.pgdump\.gpg$")
BACKUP_WORKFLOW = "neon-backup.yml"

CHECKS = {
    # key: (environment prefix the workflow passes, issue title, what is checked)
    "backend-health": ("HEALTH", "Backend health check failing", "the production backend's /health endpoint"),
    "backup-freshness": ("BACKUP", "Backup freshness check failing", "the encrypted Neon backups in S3 and the backup workflow"),
}
MARKER = "<!-- production-monitor:{} -->"


@dataclass
class Result:
    check: str
    status: str = "ok"  # ok | problem | error
    summary: str = ""
    details: list[str] = field(default_factory=list)


def _say(message: str) -> None:
    print(message, flush=True)


def _one_line(text: str, limit: int = 300) -> str:
    """Safe for $GITHUB_OUTPUT and an issue body: one line, bounded, no @-mention, no HTML comment."""
    text = " ".join(str(text).split()).replace("@", "(at)").replace("<!--", "").replace("-->", "")
    return text[:limit]


def _publish(result: Result) -> int:
    for line in result.details:
        _say(f"  {line}")
    _say(f"{result.check}: {result.status.upper()} - {result.summary}")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"status={result.status}\nsummary={_one_line(result.summary)}\n")
    return {"ok": 0, "problem": 1}.get(result.status, 2)


# -- backend health ------------------------------------------------------------------------------------


@dataclass
class Response:
    status: int  # 0 = no HTTP response (DNS, TLS, refused, timed out)
    content_type: str
    body: bytes
    seconds: float
    error: str = ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *arguments, **keywords):  # a redirect is a failure, never followed
        return None


def fetch(url: str, *, timeout: float) -> Response:
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    started = time.monotonic()
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout) as reply:
            return Response(reply.status, reply.headers.get("Content-Type", ""), reply.read(MAX_BODY_BYTES + 1), time.monotonic() - started)
    except urllib.error.HTTPError as error:
        return Response(error.code, error.headers.get("Content-Type", "") if error.headers else "", b"", time.monotonic() - started)
    except Exception as error:  # URLError, timeout, TLS, reset: the type only, a message may quote the URL
        return Response(0, "", b"", time.monotonic() - started, type(error).__name__)


def health_failure(response: Response) -> str | None:
    """Why this is not the backend's healthy answer, or None when it is: HTTP 200, JSON, `success` true and
    `data.status` "ok" -- not any 200 (a hosting error page or a captive portal answers 200 too)."""
    if response.status == 0:
        return f"no response ({response.error})"
    if response.status != 200:
        return f"HTTP {response.status}"
    if "application/json" not in response.content_type.lower():
        return "HTTP 200 but not JSON"
    if len(response.body) > MAX_BODY_BYTES:
        return "HTTP 200 but an oversized body"
    try:
        document = json.loads(response.body.decode("utf-8"))
    except Exception:
        return "HTTP 200 but malformed JSON"
    data = document.get("data") if isinstance(document, dict) else None
    if not (isinstance(data, dict) and document.get("success") is True and data.get("status") == "ok"):
        return "HTTP 200 but the health status is not ok"
    return None


def backend_origin(value: str) -> str:
    value = (value or DEFAULT_BACKEND).strip().rstrip("/")
    if not (value.startswith("https://") or re.match(r"^http://(127\.0\.0\.1|localhost)(:\d+)?$", value)):
        raise ValueError("MONITOR_BACKEND_URL must be an https:// origin")
    if value.count("/") != 2:
        raise ValueError("MONITOR_BACKEND_URL must be an origin without a path")
    return value


def check_health(origin: str, *, timeout: float = ATTEMPT_TIMEOUT_SECONDS, delays=RETRY_DELAYS_SECONDS,
                 warm_seconds: float = WARM_SECONDS, sleep=time.sleep) -> Result:
    result = Result("backend-health")
    started = time.monotonic()
    first_seconds = None
    for attempt in range(1, len(delays) + 2):
        if attempt > 1:
            sleep(delays[attempt - 2])
        response = fetch(origin + HEALTH_PATH, timeout=timeout)
        failure = health_failure(response)
        first_seconds = response.seconds if first_seconds is None else first_seconds
        shown = f"HTTP {response.status}" if response.status else "no response"
        result.details.append(f"attempt {attempt}: {shown} in {response.seconds:.1f} s - {'valid health response' if failure is None else failure}")
        if failure is None:
            if attempt == 1 and response.seconds < warm_seconds:
                result.summary = f"healthy (answered in {response.seconds:.1f} s)"
            else:
                result.summary = (f"healthy after a cold start (attempt {attempt}, first answer after {first_seconds:.0f} s); "
                                  "a sleeping free-tier backend is expected to start this way")
            return result
    result.status = "problem"
    minutes = (time.monotonic() - started) / 60
    result.summary = f"no valid health response in {attempt} attempts over {minutes:.0f} min (last: {failure}); longer than a cold start"
    return result


# -- backups -------------------------------------------------------------------------------------------


class CheckError(Exception):
    """The check could not determine the state. The message is fixed wording, safe to print."""


def list_backup_objects(bucket: str, prefix: str) -> list[dict]:
    aws = shutil.which("aws")
    if not aws:
        raise CheckError("the AWS CLI is not installed")
    completed = subprocess.run(
        [aws, "s3api", "list-objects-v2", "--bucket", bucket, "--prefix", prefix, "--output", "json"],
        env={**os.environ, "AWS_PAGER": ""}, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120,
    )
    if completed.returncode != 0:
        error = completed.stderr
        if "AccessDenied" in error or "Forbidden" in error:
            raise CheckError("S3 refused the listing (AccessDenied): check s3:ListBucket on neon/ for the monitoring session")
        if re.search(r"ExpiredToken|InvalidToken|InvalidAccessKeyId|Unable to locate credentials|NoCredential", error):
            raise CheckError("no valid AWS credentials (the OIDC role could not be assumed)")
        if "NoSuchBucket" in error:
            raise CheckError("the backup bucket does not exist")
        raise CheckError(f"the S3 listing failed (AWS CLI exit code {completed.returncode})")
    try:
        document = json.loads(completed.stdout or "{}")
    except ValueError:
        raise CheckError("the AWS CLI did not return JSON") from None
    contents = (document.get("Contents") or []) if isinstance(document, dict) else []
    return [item for item in contents if isinstance(item, dict)]


def _timestamp(value) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else None


def evaluate_backups(objects: list[dict], now: datetime, *, max_age_hours: float = MAX_BACKUP_AGE_HOURS,
                     min_bytes: int = MIN_BACKUP_BYTES) -> tuple[list[str], list[str]]:
    """(problems, facts) for a listing of the prefix. A backup is valid when its name is one the backup workflow
    writes, it holds at least `min_bytes`, and S3's own LastModified is a real time not in the future. Age comes
    from LastModified (what S3 recorded), never from the name."""
    valid: list[datetime] = []
    counts = {"zero-byte": 0, "below the minimum size": 0, "with an unrecognised name": 0, "with an unusable timestamp": 0}
    for item in objects:
        key, size = str(item.get("Key", "")), item.get("Size")
        if key == BACKUP_PREFIX and size == 0:
            continue  # a console "folder" placeholder, not a backup
        moment = _timestamp(item.get("LastModified"))
        if not BACKUP_KEY.match(key):
            counts["with an unrecognised name"] += 1
        elif not isinstance(size, int) or size == 0:
            counts["zero-byte"] += 1
        elif size < min_bytes:
            counts["below the minimum size"] += 1
        elif moment is None or moment > now + FUTURE_TOLERANCE:
            counts["with an unusable timestamp"] += 1
        else:
            valid.append(moment)
    problems, facts = [], [f"valid encrypted backups under {BACKUP_PREFIX}: {len(valid)}"]
    if not valid:
        problems.append(f"no valid encrypted backup exists under {BACKUP_PREFIX}" + ("" if objects else " (the prefix is empty)"))
    else:
        age = (now - max(valid)).total_seconds() / 3600
        facts.append(f"newest valid backup: {age:.1f} h old (limit {max_age_hours:.0f} h)")
        if age > max_age_hours:
            problems.append(f"the newest valid backup is {age:.0f} h old, older than {max_age_hours:.0f} h")
    unexpected = [f"{count} {kind}" for kind, count in counts.items() if count]
    if unexpected:
        problems.append(f"unexpected objects under {BACKUP_PREFIX}: {', '.join(unexpected)}")
    return problems, facts


def github_api(method: str, path: str, body: dict | None = None) -> dict | list:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise CheckError("GITHUB_TOKEN is not set")
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    request = urllib.request.Request(
        api + path, method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": USER_AGENT, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as reply:
            raw = reply.read()
    except urllib.error.HTTPError as error:
        raise CheckError(f"the GitHub API answered HTTP {error.code} to {method} {path.split('?')[0]}") from None
    except Exception as error:
        raise CheckError(f"the GitHub API could not be reached ({type(error).__name__})") from None
    return json.loads(raw) if raw else {}


def backup_workflow_findings(repository: str, schedule_variable: str) -> tuple[list[str], list[str]]:
    """(problems, errors): the backup workflow is enabled, its schedule switch is on, its latest run succeeded."""
    problems, errors = [], []
    if schedule_variable != "true":
        problems.append("scheduled backups are switched off (the variable NEON_BACKUP_SCHEDULE_ENABLED is not true)")
    try:
        state = github_api("GET", f"/repos/{repository}/actions/workflows/{BACKUP_WORKFLOW}").get("state", "unknown")
        if state != "active":
            problems.append(f"the backup workflow is not active (GitHub state: {_one_line(state, 40)})")
        runs = github_api("GET", f"/repos/{repository}/actions/workflows/{BACKUP_WORKFLOW}/runs?branch=main&status=completed&per_page=1")
        latest = (runs.get("workflow_runs") or [None])[0]
        if latest and latest.get("conclusion") in {"failure", "timed_out", "startup_failure"}:
            problems.append(f"the latest backup run on main ended with '{latest.get('conclusion')}' (run {latest.get('id')})")
    except CheckError as error:
        errors.append(str(error))
    return problems, errors


def check_backups(*, now: datetime | None = None) -> Result:
    result = Result("backup-freshness")
    now = now or datetime.now(timezone.utc)
    bucket = os.environ.get("BACKUP_S3_BUCKET", "").strip()
    prefix = os.environ.get("BACKUP_S3_PREFIX", "")
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    missing = [name for name, value in (("BACKUP_S3_BUCKET", bucket), ("GITHUB_REPOSITORY", repository)) if not value]
    if missing or prefix != BACKUP_PREFIX:
        result.status = "error"
        result.summary = "configuration missing: " + (", ".join(missing) if missing else f"BACKUP_S3_PREFIX must be exactly {BACKUP_PREFIX!r}")
        return result

    problems, errors = backup_workflow_findings(repository, os.environ.get("NEON_BACKUP_SCHEDULE_ENABLED", ""))
    try:
        found, facts = evaluate_backups(list_backup_objects(bucket, prefix), now)
        problems = found + problems
        result.details.extend(facts)
    except CheckError as error:
        errors.insert(0, str(error))
    result.details.extend(f"problem: {problem}" for problem in problems)
    result.details.extend(f"could not check: {error}" for error in errors)
    if problems:
        result.status, result.summary = "problem", "; ".join(problems)
    elif errors:
        result.status, result.summary = "error", "; ".join(errors)
    else:
        result.summary = "fresh: " + "; ".join(result.details)
    return result


# -- alerts --------------------------------------------------------------------------------------------


def check_state(prefix: str) -> tuple[str, str]:
    """(state, summary) of one check as the workflow reports it. A check job that was skipped was not run
    (no change to its alert); one that ended without a status did not complete, which is itself alerted."""
    job = os.environ.get(f"{prefix}_JOB", "").strip()
    status = os.environ.get(f"{prefix}_STATUS", "").strip()
    summary = _one_line(os.environ.get(f"{prefix}_SUMMARY", ""))
    if job == "skipped" or (not job and not status):
        return "not_run", "not run"
    if status in {"ok", "problem", "error"}:
        return status, summary or status
    return "error", f"the check did not complete (job result: {_one_line(job or 'unknown', 30)})"


def _open_issue(marker: str, repository: str) -> dict | None:
    for page in range(1, 6):
        issues = github_api("GET", f"/repos/{repository}/issues?state=open&per_page=100&page={page}")
        for issue in issues:
            if "pull_request" not in issue and marker in (issue.get("body") or ""):
                return issue
        if len(issues) < 100:
            return None
    return None


def _issue_body(key: str, state: str, summary: str, run_url: str, mention: str, first_seen: str, now: str) -> str:
    _, title, subject = CHECKS[key]
    headline = "A problem was detected" if state == "problem" else "The check could not run, so the state is unknown"
    return "\n".join([
        MARKER.format(key),
        f"{f'@{mention} ' if mention else ''}{headline} with {subject}.",
        "",
        f"- **Status:** {state}",
        f"- **Finding:** {summary}",
        f"- **First detected:** {first_seen}",
        f"- **Last seen:** {now} ([run]({run_url}))",
        "",
        "What to do: `docs/31_production_monitoring.md`, section 6 (owner response checklist).",
        "This issue is closed automatically by the first run in which the check passes again.",
    ])


def run_alerts(now: datetime | None = None) -> int:
    now_text = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    run_url = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{repository}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"
    mention = os.environ.get("MONITOR_ALERT_MENTION", "").strip()
    mention = mention if re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", mention) else ""
    failing = delivery_failed = False
    rows = []
    for key, (prefix, title, _) in CHECKS.items():
        state, summary = check_state(prefix)
        rows.append((key, state, summary))
        failing = failing or state in {"problem", "error"}
        if state == "not_run":
            _say(f"{key}: not run in this workflow run; its alert is left as it is")
            continue
        try:
            if not repository:
                raise CheckError("GITHUB_REPOSITORY is not set")
            marker = MARKER.format(key)
            issue = _open_issue(marker, repository)
            if state in {"problem", "error"}:
                if issue is None:
                    created = github_api("POST", f"/repos/{repository}/issues", {
                        "title": f"[monitor] {title}" if state == "problem" else f"[monitor] {title.replace(' failing', '')} could not run",
                        "body": _issue_body(key, state, summary, run_url, mention, now_text, now_text)})
                    _say(f"{key}: {state} - alert issue #{created.get('number')} opened")
                else:
                    match = re.search(r"\*\*First detected:\*\* ([^\n]+)", issue.get("body") or "")
                    github_api("PATCH", f"/repos/{repository}/issues/{issue['number']}",
                               {"body": _issue_body(key, state, summary, run_url, mention, match.group(1) if match else now_text, now_text)})
                    _say(f"{key}: {state} - alert issue #{issue['number']} still open (updated, no new notification)")
            elif issue is not None:
                github_api("POST", f"/repos/{repository}/issues/{issue['number']}/comments",
                           {"body": f"Recovered at {now_text}: {summary} ([run]({run_url})). Closing."})
                github_api("PATCH", f"/repos/{repository}/issues/{issue['number']}", {"state": "closed", "state_reason": "completed"})
                _say(f"{key}: ok - alert issue #{issue['number']} closed")
            else:
                _say(f"{key}: ok - no open alert")
        except CheckError as error:
            delivery_failed = True
            print(f"::error::{key}: the alert issue could not be updated: {error}", flush=True)

    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as handle:
            handle.write("### Production monitor\n\n| Check | State | Finding |\n|---|---|---|\n")
            handle.writelines(f"| {key} | {state} | {summary.replace('|', '/')} |\n" for key, state, summary in rows)
    if failing:
        print("::error::Production monitor: a problem is open (see the alert issue).", flush=True)
    # Last, after every check result and every alert update has been handled: a run that crashes before this
    # line sends nothing (the external service then alerts on the missing ping), and a run in which no check
    # ran says nothing either way, because it proved nothing.
    if any(state != "not_run" for _, state, _ in rows):
        heartbeat(healthy=not (failing or delivery_failed))
    else:
        _say("heartbeat: not sent (no check ran in this workflow run)")
    return 1 if failing or delivery_failed else 0


def heartbeat(*, healthy: bool) -> None:
    """Optional dead-man signal to an external service the owner set up (MONITOR_HEARTBEAT_URL, a secret;
    healthchecks.io-style: the URL on success, URL + "/fail" on a problem). Its absence is what the external
    service alerts on, which is the one thing a GitHub workflow cannot report about itself."""
    url = os.environ.get("MONITOR_HEARTBEAT_URL", "").strip()
    if not url:
        _say("heartbeat: not configured (no independent dead-man check; docs/31 section 4)")
        return
    if not url.startswith("https://"):
        print("::warning::heartbeat: MONITOR_HEARTBEAT_URL is not an https URL; not sent", flush=True)
        return
    target = url.rstrip("/") + ("" if healthy else "/fail")
    for _ in range(2):
        response = fetch(target, timeout=10)
        if 200 <= response.status < 300:
            _say(f"heartbeat: sent ({'success' if healthy else 'failure'} signal)")
            return
    print(f"::warning::heartbeat: not delivered ({'HTTP ' + str(response.status) if response.status else response.error})", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TravelObligator production monitoring (GET-only health, S3 listing, alert issues).")
    parser.add_argument("command", choices=("health", "backups", "alert"))
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "health":
            try:
                origin = backend_origin(os.environ.get("MONITOR_BACKEND_URL", ""))
            except ValueError as error:
                return _publish(Result("backend-health", "error", str(error)))
            return _publish(check_health(origin))
        if arguments.command == "backups":
            return _publish(check_backups())
        return run_alerts()
    except Exception as error:  # the message is not shown: it may quote an input
        print(f"::error::{arguments.command}: unexpected {type(error).__name__}", flush=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
