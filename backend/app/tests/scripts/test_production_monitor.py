from __future__ import annotations

import http.server
import importlib.util
import json
import re
import subprocess
import sys
import textwrap
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Production monitoring (docs/31_production_monitoring.md), hermetic: the real script against a local HTTP
# server standing in for the backend, a fake AWS CLI and an in-memory GitHub API. No network, no AWS, no GitHub.

_ROOT = Path(__file__).resolve().parents[4]
_SCRIPT = _ROOT / "scripts" / "monitoring" / "production_monitor.py"
_WORKFLOW = (_ROOT / ".github" / "workflows" / "production-monitor.yml").read_text()
_TOKEN = "ghs_test_token_never_printed"


def _load():
    spec = importlib.util.spec_from_file_location("production_monitor", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(module)
    return module


monitor = _load()
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
HEALTHY_BODY = {"success": True, "data": {"status": "ok", "service": "SERVICE-NAME-NOT-PRINTED"}, "message": "running", "errors": []}


# -- a stand-in backend ----------------------------------------------------------------------------------


class _Backend:
    """Serves a scripted sequence of answers to GET /health: (status, content type, body, delay seconds)."""

    def __init__(self, script):
        self.script, self.paths = list(script), []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.paths.append(self.path)
                status, content_type, body, delay = outer.script.pop(0) if len(outer.script) > 1 else outer.script[0]
                time.sleep(delay)
                payload = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                if status in (301, 302):
                    self.send_header("Location", "https://elsewhere.example/health")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *arguments):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.origin = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def backend():
    servers = []

    def start(*script):
        servers.append(_Backend(script))
        return servers[-1]

    yield start
    for server in servers:
        server.close()


def _health(origin, **overrides):
    options = {"timeout": 2.0, "delays": (0, 0, 0), "warm_seconds": 1.0, "sleep": lambda seconds: None}
    return monitor.check_health(origin, **{**options, **overrides})


OK = (200, "application/json", HEALTHY_BODY, 0)


def test_a_warm_healthy_backend_passes_in_one_request(backend):
    server = backend(OK)
    result = _health(server.origin)
    assert (result.status, server.paths) == ("ok", ["/health"])
    assert result.summary.startswith("healthy (answered in")


def test_a_slow_first_answer_is_a_cold_start_not_an_outage(backend):
    server = backend((200, "application/json", HEALTHY_BODY, 1.2))
    result = _health(server.origin)
    assert result.status == "ok" and "cold start" in result.summary and server.paths == ["/health"]


def test_a_gateway_error_while_starting_then_recovery_is_a_cold_start(backend):
    server = backend((502, "text/html", b"<html>starting</html>", 0), (503, "text/html", b"", 0), OK)
    result = _health(server.origin)
    assert result.status == "ok" and "attempt 3" in result.summary
    assert len(server.paths) == 3 and set(server.paths) == {"/health"}


def test_repeated_failure_over_every_attempt_is_a_problem(backend):
    server = backend((503, "text/html", b"Service Unavailable", 0))
    waits = []
    result = _health(server.origin, delays=(20.0, 60.0, 120.0), sleep=waits.append)
    assert result.status == "problem" and "4 attempts" in result.summary and "HTTP 503" in result.summary
    assert waits == [20.0, 60.0, 120.0] and len(server.paths) == 4


@pytest.mark.parametrize(("answer", "reason"), [
    ((200, "text/html", b"<html>TravelObligator</html>", 0), "not JSON"),
    ((200, "application/json", b"{not json", 0), "malformed JSON"),
    ((200, "application/json", {"success": True, "data": {"status": "starting"}}, 0), "health status is not ok"),
    ((200, "application/json", {"success": False, "data": {"status": "ok"}}, 0), "health status is not ok"),
    ((200, "application/json", ["ok"], 0), "health status is not ok"),
    ((302, "text/html", b"", 0), "HTTP 302"),
])
def test_an_http_200_is_not_enough_the_answer_must_be_the_health_document(backend, answer, reason):
    result = _health(backend(answer).origin)
    assert result.status == "problem" and reason in result.summary


def test_no_response_at_all_is_reported_by_kind_without_the_url():
    result = _health("http://127.0.0.1:9", timeout=0.5)  # nothing listens on the discard port
    assert result.status == "problem" and "no response (" in result.summary
    assert "127.0.0.1" not in result.summary


def test_a_hung_backend_is_cut_off_by_the_per_attempt_timeout(backend):
    server = backend((200, "application/json", HEALTHY_BODY, 1.5))
    started = time.monotonic()
    result = _health(server.origin, timeout=0.3, delays=(0,))
    assert result.status == "problem" and time.monotonic() - started < 5


def test_the_health_output_never_contains_the_response_body(backend, capsys, monkeypatch):
    server = backend(OK)
    monkeypatch.setenv("MONITOR_BACKEND_URL", server.origin)
    assert monitor.main(["health"]) == 0
    assert "SERVICE-NAME-NOT-PRINTED" not in capsys.readouterr().out


@pytest.mark.parametrize("value", ["http://travelobligator-backend.onrender.com", "https://example.com/health", "ftp://x"])
def test_only_an_https_origin_is_accepted_as_the_backend(value, monkeypatch, tmp_path):
    monkeypatch.setenv("MONITOR_BACKEND_URL", value)
    output = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert monitor.main(["health"]) == 2
    assert "status=error" in output.read_text()
    assert monitor.backend_origin("") == "https://travelobligator-backend.onrender.com"


def test_the_monitor_only_ever_requests_health():
    source = _SCRIPT.read_text()
    assert 'HEALTH_PATH = "/health"' in source
    for path in ("/auth", "/trips", "/generate", "/ready", "/metrics", "/register", "/api/"):
        assert f'"{path}' not in source


# -- backups: the listing -------------------------------------------------------------------------------


def _object(hours_old: float, size: int = 250_000, key: str | None = None, *, last_modified: str | None = None) -> dict:
    moment = NOW - timedelta(hours=hours_old)
    return {
        "Key": key or f"neon/travelobligator-{moment:%Y%m%dT%H%M%SZ}-run123-1.pgdump.gpg",
        "Size": size,
        "LastModified": last_modified or moment.isoformat(),
        "ETag": '"abc"',
    }


def test_a_fresh_encrypted_backup_passes():
    problems, facts = monitor.evaluate_backups([_object(30), _object(6)], NOW)
    assert problems == [] and "6.0 h old" in " ".join(facts) and "valid encrypted backups under neon/: 2" in facts


def test_an_empty_prefix_is_a_missing_backup():
    problems, _ = monitor.evaluate_backups([], NOW)
    assert problems == ["no valid encrypted backup exists under neon/ (the prefix is empty)"]


@pytest.mark.parametrize(("hours", "stale"), [(35.9, False), (36.5, True), (80, True)])
def test_staleness_allows_one_interval_plus_scheduling_delay(hours, stale):
    problems, _ = monitor.evaluate_backups([_object(hours)], NOW)
    assert bool(problems) is stale and (not stale or "older than 36 h" in problems[0])


def test_s3_last_modified_decides_the_age_not_the_object_name():
    renamed_fresh = _object(40, key=f"neon/travelobligator-{NOW:%Y%m%dT%H%M%SZ}-run9-1.pgdump.gpg")
    assert monitor.evaluate_backups([renamed_fresh], NOW)[0]  # the name says now, S3 says 40 h: stale
    old_name = _object(2, key="neon/travelobligator-20200101T000000Z-run9-1.pgdump.gpg")
    assert monitor.evaluate_backups([old_name], NOW)[0] == []


@pytest.mark.parametrize(("bad", "kind"), [
    (_object(1, size=0), "1 zero-byte"),
    (_object(1, size=200), "1 below the minimum size"),
    (_object(1, key="neon/notes.txt"), "1 with an unrecognised name"),
    (_object(1, key="neon/travelobligator-20261020T110000Z-run1-1.pgdump"), "1 with an unrecognised name"),
    (_object(-2), "1 with an unusable timestamp"),
    (_object(1, last_modified="yesterday"), "1 with an unusable timestamp"),
])
def test_malformed_zero_byte_or_odd_objects_are_problems_even_beside_a_fresh_backup(bad, kind):
    problems, facts = monitor.evaluate_backups([_object(5), bad], NOW)
    assert problems == [f"unexpected objects under neon/: {kind}"] and "valid encrypted backups under neon/: 1" in facts


def test_only_malformed_objects_means_no_valid_backup():
    problems, _ = monitor.evaluate_backups([_object(1, size=0)], NOW)
    assert problems[0] == "no valid encrypted backup exists under neon/" and "zero-byte" in problems[1]


def test_a_console_folder_placeholder_is_ignored():
    assert monitor.evaluate_backups([_object(4), {"Key": "neon/", "Size": 0, "LastModified": NOW.isoformat()}], NOW)[0] == []


# -- backups: AWS CLI and GitHub API stand-ins ----------------------------------------------------------


class _GitHub:
    """An in-memory GitHub REST API: workflow state, latest backup run, issues and comments."""

    def __init__(self):
        self.state, self.conclusion, self.fail_with = "active", "success", None
        self.issues, self.comments, self.calls, self.authorized = {}, [], [], True
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _reply(self, status, body):
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _handle(self, method):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                outer.calls.append((method, self.path))
                outer.authorized &= self.headers.get("Authorization") == f"Bearer {_TOKEN}"
                if outer.fail_with:
                    return self._reply(outer.fail_with, {"message": "nope"})
                path = self.path.split("?")[0]
                if path.endswith("/actions/workflows/neon-backup.yml"):
                    return self._reply(200, {"state": outer.state})
                if path.endswith("/actions/workflows/neon-backup.yml/runs"):
                    runs = [{"id": 77, "conclusion": outer.conclusion}] if outer.conclusion else []
                    return self._reply(200, {"workflow_runs": runs})
                if path.endswith("/issues") and method == "GET":
                    return self._reply(200, [issue for issue in outer.issues.values() if issue["state"] == "open"])
                if path.endswith("/issues") and method == "POST":
                    number = len(outer.issues) + 1
                    outer.issues[number] = {"number": number, "state": "open", **body}
                    return self._reply(201, outer.issues[number])
                match = re.search(r"/issues/(\d+)(/comments)?$", path)
                if match and match.group(2):
                    outer.comments.append((int(match.group(1)), body["body"]))
                    return self._reply(201, {})
                if match and method == "PATCH":
                    outer.issues[int(match.group(1))].update(body)
                    return self._reply(200, outer.issues[int(match.group(1))])
                return self._reply(404, {})

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

            def do_PATCH(self):
                self._handle("PATCH")

            def log_message(self, *arguments):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"


_FAKE_AWS = """
import json, os, sys
mode = os.environ.get("FAKE_AWS_MODE", "ok")
assert sys.argv[1:3] == ["s3api", "list-objects-v2"] and sys.argv[sys.argv.index("--prefix") + 1] == "neon/"
if mode == "denied":
    sys.stderr.write("An error occurred (AccessDenied) when calling the ListObjectsV2 operation: Access Denied\\n"); sys.exit(254)
if mode == "nocreds":
    sys.stderr.write("Unable to locate credentials. You can configure credentials by running aws configure.\\n"); sys.exit(253)
if mode == "garbage":
    print("not json"); sys.exit(0)
print(os.environ["FAKE_LISTING"])
"""


@pytest.fixture
def services(tmp_path, monkeypatch):
    github = _GitHub()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "aws").write_text(f"#!{sys.executable}\n" + textwrap.dedent(_FAKE_AWS))
    (bin_dir / "aws").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    for name, value in {
        "GITHUB_API_URL": github.url, "GITHUB_TOKEN": _TOKEN, "GITHUB_REPOSITORY": "garima24112000/travelobligator",
        "GITHUB_SERVER_URL": "https://github.com", "GITHUB_RUN_ID": "4242", "BACKUP_S3_BUCKET": "travelobligator-db-backups-10192026",
        "BACKUP_S3_PREFIX": "neon/", "NEON_BACKUP_SCHEDULE_ENABLED": "true", "MONITOR_ALERT_MENTION": "garima24112000",
        "FAKE_LISTING": json.dumps({"Contents": [_object(6)]}),
    }.items():
        monkeypatch.setenv(name, value)
    for name in ("MONITOR_HEARTBEAT_URL", "GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY", "FAKE_AWS_MODE"):
        monkeypatch.delenv(name, raising=False)
    yield github
    github.server.shutdown()
    github.server.server_close()


def test_fresh_backups_and_a_healthy_backup_workflow_pass(services):
    result = monitor.check_backups(now=NOW)
    assert result.status == "ok" and result.summary.startswith("fresh:") and services.authorized


@pytest.mark.parametrize(("mode", "message"), [
    ("denied", "AccessDenied"),
    ("nocreds", "no valid AWS credentials"),
    ("garbage", "did not return JSON"),
])
def test_aws_access_failures_mean_the_check_could_not_run(services, monkeypatch, mode, message):
    monkeypatch.setenv("FAKE_AWS_MODE", mode)
    result = monitor.check_backups(now=NOW)
    assert result.status == "error" and message in result.summary


def test_a_missing_aws_cli_is_a_check_error(services, monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert "AWS CLI is not installed" in monitor.check_backups(now=NOW).summary


def test_a_stale_backup_is_a_problem_even_if_github_cannot_be_read(services, monkeypatch):
    monkeypatch.setenv("FAKE_LISTING", json.dumps({"Contents": [_object(50)]}))
    services.fail_with = 403
    result = monitor.check_backups(now=NOW)
    assert result.status == "problem" and "50 h old" in result.summary
    assert any("could not check: the GitHub API answered HTTP 403" in line for line in result.details)


def test_an_empty_bucket_listing_is_a_missing_backup(services, monkeypatch):
    monkeypatch.setenv("FAKE_LISTING", json.dumps({"KeyCount": 0}))
    result = monitor.check_backups(now=NOW)
    assert result.status == "problem" and "prefix is empty" in result.summary


@pytest.mark.parametrize(("change", "message"), [
    ({"state": "disabled_manually"}, "not active (GitHub state: disabled_manually)"),
    ({"state": "disabled_inactivity"}, "disabled_inactivity"),
    ({"conclusion": "failure"}, "latest backup run on main ended with 'failure'"),
])
def test_a_disabled_or_failing_backup_workflow_is_flagged_before_backups_go_stale(services, change, message):
    for name, value in change.items():
        setattr(services, name, value)
    result = monitor.check_backups(now=NOW)
    assert result.status == "problem" and message in result.summary


def test_scheduled_backups_switched_off_is_a_problem(services, monkeypatch):
    monkeypatch.setenv("NEON_BACKUP_SCHEDULE_ENABLED", "")
    assert "NEON_BACKUP_SCHEDULE_ENABLED is not true" in monitor.check_backups(now=NOW).summary


def test_a_github_api_failure_alone_is_a_check_error(services):
    services.fail_with = 500
    result = monitor.check_backups(now=NOW)
    assert result.status == "error" and "HTTP 500" in result.summary


@pytest.mark.parametrize(("name", "value"), [("BACKUP_S3_BUCKET", ""), ("GITHUB_REPOSITORY", ""), ("BACKUP_S3_PREFIX", "other/")])
def test_missing_configuration_is_a_check_error(services, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    result = monitor.check_backups(now=NOW)
    assert result.status == "error" and "configuration missing" in result.summary
    assert services.calls == []


def test_the_backup_output_shows_no_object_key_and_exit_codes_follow_the_status(services, monkeypatch, tmp_path):
    output = tmp_path / "out"
    environment = {key: value for key, value in __import__("os").environ.items()}
    environment["GITHUB_OUTPUT"] = str(output)
    # The subprocess uses the real clock: date the object six hours before it.
    real = datetime.now(timezone.utc) - timedelta(hours=6)
    environment["FAKE_LISTING"] = json.dumps({"Contents": [{
        "Key": f"neon/travelobligator-{real:%Y%m%dT%H%M%SZ}-run123-1.pgdump.gpg", "Size": 250_000, "LastModified": real.isoformat()}]})
    completed = subprocess.run([sys.executable, "-I", str(_SCRIPT), "backups"], env=environment, capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0 and "status=ok" in output.read_text()
    assert "travelobligator-2026" not in completed.stdout and "run123" not in completed.stdout and _TOKEN not in completed.stdout
    environment["FAKE_LISTING"] = json.dumps({"Contents": []})
    assert subprocess.run([sys.executable, "-I", str(_SCRIPT), "backups"], env=environment, capture_output=True, timeout=60).returncode == 1
    environment["FAKE_AWS_MODE"] = "denied"
    assert subprocess.run([sys.executable, "-I", str(_SCRIPT), "backups"], env=environment, capture_output=True, timeout=60).returncode == 2


# -- alerting -------------------------------------------------------------------------------------------


def _alert(monkeypatch, health=("success", "ok", "healthy"), backup=("success", "ok", "fresh")):
    for prefix, (job, status, summary) in (("HEALTH", health), ("BACKUP", backup)):
        monkeypatch.setenv(f"{prefix}_JOB", job)
        monkeypatch.setenv(f"{prefix}_STATUS", status)
        monkeypatch.setenv(f"{prefix}_SUMMARY", summary)
    return monitor.run_alerts(now=NOW)


def _open(github):
    return [issue for issue in github.issues.values() if issue["state"] == "open"]


def test_all_healthy_sends_nothing(services, monkeypatch):
    assert _alert(monkeypatch) == 0
    assert services.issues == {} and not [call for call in services.calls if call[0] != "GET"]


def test_a_problem_opens_one_issue_that_mentions_the_owner(services, monkeypatch, capsys):
    assert _alert(monkeypatch, health=("success", "problem", "no valid health response in 4 attempts")) == 1
    [issue] = _open(services)
    assert issue["title"] == "[monitor] Backend health check failing"
    assert issue["body"].startswith("<!-- production-monitor:backend-health -->\n@garima24112000 A problem was detected")
    assert "4 attempts" in issue["body"] and "actions/runs/4242" in issue["body"] and "docs/31_production_monitoring.md" in issue["body"]
    assert _TOKEN not in capsys.readouterr().out


def test_a_continuing_problem_updates_the_issue_without_a_new_one(services, monkeypatch):
    _alert(monkeypatch, backup=("success", "problem", "the newest valid backup is 40 h old"))
    first = _open(services)[0]["body"]
    later = NOW + timedelta(hours=1)
    for prefix, values in (("HEALTH", ("success", "ok", "healthy")), ("BACKUP", ("success", "problem", "the newest valid backup is 41 h old"))):
        for suffix, value in zip(("_JOB", "_STATUS", "_SUMMARY"), values):
            monkeypatch.setenv(prefix + suffix, value)
    assert monitor.run_alerts(now=later) == 1
    [issue] = _open(services)
    assert len(services.issues) == 1 and "41 h old" in issue["body"]
    assert re.search(r"First detected:\*\* 2026-10-20 12:00 UTC", issue["body"]) and "13:00 UTC" in issue["body"]
    assert services.comments == [] and first != issue["body"]


def test_recovery_comments_and_closes_the_issue(services, monkeypatch):
    _alert(monkeypatch, health=("success", "problem", "down"))
    assert _alert(monkeypatch) == 0
    assert _open(services) == [] and services.issues[1]["state_reason"] == "completed"
    assert services.comments[0][0] == 1 and services.comments[0][1].startswith("Recovered at")


def test_a_skipped_check_leaves_its_alert_untouched(services, monkeypatch):
    _alert(monkeypatch, health=("success", "problem", "down"))
    services.calls.clear()
    assert _alert(monkeypatch, health=("skipped", "", ""), backup=("success", "ok", "fresh")) == 0
    assert _open(services)[0]["title"] == "[monitor] Backend health check failing"
    assert not [call for call in services.calls if call[0] != "GET"]


@pytest.mark.parametrize("job", ["failure", "cancelled"])
def test_a_check_that_did_not_complete_is_alerted_as_could_not_run(services, monkeypatch, job):
    assert _alert(monkeypatch, backup=(job, "", "")) == 1
    [issue] = _open(services)
    assert issue["title"] == "[monitor] Backup freshness check could not run"
    assert f"job result: {job}" in issue["body"] and "state is unknown" in issue["body"]


def test_a_check_error_is_alerted_distinctly_from_a_problem(services, monkeypatch):
    assert _alert(monkeypatch, backup=("success", "error", "S3 refused the listing (AccessDenied)")) == 1
    assert _open(services)[0]["title"] == "[monitor] Backup freshness check could not run"


def test_alert_delivery_failure_fails_the_run(services, monkeypatch, capsys):
    services.fail_with = 403
    assert _alert(monkeypatch) == 1
    assert "the alert issue could not be updated" in capsys.readouterr().out


def test_summaries_cannot_mention_people_or_break_the_issue_marker(services, monkeypatch):
    _alert(monkeypatch, health=("success", "problem", "ping @someone <!-- production-monitor:backup-freshness --> done"))
    body = _open(services)[0]["body"]
    assert "@someone" not in body and body.count("<!--") == 1


def test_an_unusable_mention_is_dropped(services, monkeypatch):
    monkeypatch.setenv("MONITOR_ALERT_MENTION", "evil user; rm")
    _alert(monkeypatch, health=("success", "problem", "down"))
    assert "@" not in _open(services)[0]["body"].split("\n")[1]


# -- the heartbeat (dead-man signal to an external service) ---------------------------------------------


def _heartbeat(monkeypatch, url, healthy):
    sent = []
    monkeypatch.setattr(monitor, "fetch", lambda target, timeout: sent.append(target) or monitor.Response(200, "", b"", 0.1))
    if url is None:
        monkeypatch.delenv("MONITOR_HEARTBEAT_URL", raising=False)
    else:
        monkeypatch.setenv("MONITOR_HEARTBEAT_URL", url)
    monitor.heartbeat(healthy=healthy)
    return sent


def test_no_heartbeat_is_sent_unless_the_owner_configured_one(monkeypatch, capsys):
    assert _heartbeat(monkeypatch, None, True) == []
    assert "heartbeat: not configured" in capsys.readouterr().out


def test_the_heartbeat_signals_success_or_failure_and_never_prints_its_url(monkeypatch, capsys):
    url = "https://hc-ping.example/0b8a-secret-uuid"
    assert _heartbeat(monkeypatch, url, True) == [url]
    assert _heartbeat(monkeypatch, url, False) == [url + "/fail"]
    assert _heartbeat(monkeypatch, "http://plain.example/x", True) == []
    assert "secret-uuid" not in capsys.readouterr().out


def _recording_heartbeat(monkeypatch):
    sent = []
    monkeypatch.setenv("MONITOR_HEARTBEAT_URL", "https://hc-ping.example/uuid")
    real_fetch = monitor.fetch
    monkeypatch.setattr(monitor, "fetch", lambda target, timeout: sent.append(target) or (
        monitor.Response(200, "", b"", 0.1) if target.startswith("https://hc-ping") else real_fetch(target, timeout=timeout)))
    return sent


def test_no_heartbeat_at_all_when_no_check_ran(services, monkeypatch, capsys):
    sent = _recording_heartbeat(monkeypatch)
    assert _alert(monkeypatch, health=("skipped", "", ""), backup=("skipped", "", "")) == 0
    assert sent == [] and "heartbeat: not sent (no check ran" in capsys.readouterr().out


def test_a_failed_alert_delivery_sends_the_failure_heartbeat(services, monkeypatch):
    sent = _recording_heartbeat(monkeypatch)
    services.fail_with = 403
    assert _alert(monkeypatch) == 1
    assert sent == ["https://hc-ping.example/uuid/fail"]


def test_a_crash_during_alert_processing_sends_no_heartbeat(services, monkeypatch):
    sent = _recording_heartbeat(monkeypatch)
    monkeypatch.setattr(monitor, "_open_issue", lambda marker, repository: (_ for _ in ()).throw(KeyError("number")))
    for prefix in ("HEALTH", "BACKUP"):
        monkeypatch.setenv(f"{prefix}_JOB", "success")
        monkeypatch.setenv(f"{prefix}_STATUS", "ok")
        monkeypatch.setenv(f"{prefix}_SUMMARY", "fine")
    assert monitor.main(["alert"]) == 2
    assert sent == []


def test_the_monitor_recognises_exactly_the_names_the_backup_workflow_writes():
    spec = importlib.util.spec_from_file_location("neon_s3_backup_for_monitor", _ROOT / "scripts" / "backup" / "neon_s3_backup.py")
    backup = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = backup
    spec.loader.exec_module(backup)
    for run_id, attempt in (("39333739258", "1"), ("", ""), ("7", "3")):
        assert monitor.BACKUP_KEY.match(backup.object_key("neon/", NOW, run_id, attempt))
    # The first production backup, as its run log reported it (log masking undone).
    assert monitor.BACKUP_KEY.match("neon/travelobligator-20261019T221633Z-run39333739258-1.pgdump.gpg")


def test_alerts_send_the_failure_heartbeat_when_a_problem_is_open(services, monkeypatch):
    sent = []
    monkeypatch.setenv("MONITOR_HEARTBEAT_URL", "https://hc-ping.example/uuid")
    real_fetch = monitor.fetch
    monkeypatch.setattr(monitor, "fetch", lambda target, timeout: sent.append(target) or (
        monitor.Response(200, "", b"", 0.1) if target.startswith("https://hc-ping") else real_fetch(target, timeout=timeout)))
    _alert(monkeypatch, backup=("success", "problem", "stale"))
    _alert(monkeypatch)
    assert sent == ["https://hc-ping.example/uuid/fail", "https://hc-ping.example/uuid"]


# -- the workflow -------------------------------------------------------------------------------------


def _job(name: str) -> str:
    match = re.search(rf"^  {name}:\n(.*?)(?=^  [a-z-]+:\n|\Z)", _WORKFLOW.split("\njobs:\n", 1)[1], re.MULTILINE | re.DOTALL)
    assert match, name
    return match.group(1)


def _code(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def test_the_workflow_runs_only_on_schedule_or_by_hand_from_main():
    on_block = re.search(r"^on:\n(.*?)^permissions:", _WORKFLOW, re.MULTILINE | re.DOTALL).group(1)
    assert re.findall(r"^  (\w+):", on_block, re.MULTILINE) == ["schedule", "workflow_dispatch"]
    assert re.search(r'cron: "17 \* \* \* \*"', on_block)
    code = _code(_WORKFLOW)
    assert "pull_request" not in code and "push:" not in code and "workflow_run" not in code
    for job in ("backend-health", "backup-freshness", "alert"):
        condition = _job(job)
        assert "github.ref == 'refs/heads/main'" in condition and "github.repository == 'garima24112000/travelobligator'" in condition


def test_scheduled_runs_stay_off_until_switched_on_and_the_backend_check_has_its_own_switch():
    health, backups, alert = _job("backend-health"), _job("backup-freshness"), _job("alert")
    assert "vars.PRODUCTION_MONITOR_ENABLED == 'true'" in health and "vars.MONITOR_BACKEND_HEALTH_ENABLED == 'true'" in health
    assert "vars.PRODUCTION_MONITOR_ENABLED == 'true'" in backups and "MONITOR_BACKEND_HEALTH_ENABLED" not in backups
    assert "(github.event_name == 'workflow_dispatch' || vars.PRODUCTION_MONITOR_ENABLED == 'true')" in alert
    assert "always()" in alert and "needs: [backend-health, backup-freshness]" in alert


def test_each_job_has_only_the_permissions_it_needs():
    assert re.search(r"^permissions: \{\}$", _WORKFLOW, re.MULTILINE)

    def permissions(job):
        block = re.search(r"    permissions:\n((?:      .*\n)+)", _job(job)).group(1)
        return sorted(line.split("#")[0].strip() for line in block.splitlines())

    assert permissions("backend-health") == ["contents: read"]
    assert permissions("backup-freshness") == ["actions: read", "contents: read", "id-token: write"]
    assert permissions("alert") == ["contents: read", "issues: write"]


def test_actions_are_pinned_and_no_long_lived_aws_key_exists():
    uses = re.findall(r"uses: (\S+)", _WORKFLOW)
    assert uses and all(re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref) for ref in uses)
    assert "aws-access-key-id" not in _WORKFLOW and "aws-secret-access-key" not in _WORKFLOW
    assert _WORKFLOW.count("persist-credentials: false") == 3
    assert "upload-artifact" not in _WORKFLOW and "set -x" not in _WORKFLOW


def test_the_monitoring_session_can_only_list_the_backup_prefix():
    block = re.search(r"inline-session-policy: >-\n((?:            .*\n)+)", _WORKFLOW).group(1)
    policy = json.loads(" ".join(line.strip() for line in block.splitlines()))
    assert policy["Statement"] == [{
        "Effect": "Allow", "Action": "s3:ListBucket", "Resource": "arn:aws:s3:::travelobligator-db-backups-10192026",
        "Condition": {"StringLike": {"s3:prefix": ["neon/*"]}},
    }]
    assert "arn:aws:iam::078038703506:role/TravelObligatorNeonBackupRole" in _job("backup-freshness")
    assert "id-token" not in _job("backend-health") and "aws-actions" not in _job("backend-health")


def test_secrets_are_limited_to_the_optional_heartbeat_in_the_alert_job():
    assert set(re.findall(r"secrets\.(\w+)", _WORKFLOW)) == {"MONITOR_HEARTBEAT_URL"}
    assert "secrets." in _job("alert") and "secrets." not in _job("backend-health") and "secrets." not in _job("backup-freshness")
    # Check findings reach the alert job as environment values, never interpolated into a script.
    for line in _code(_WORKFLOW).splitlines():
        if line.strip().startswith("run:"):
            assert "${{" not in line


def test_the_monitor_script_uses_only_the_standard_library():
    imported = set(re.findall(r"^(?:from|import) (\w+)", _SCRIPT.read_text(), re.MULTILINE)) - {"__future__"}
    assert imported <= set(sys.stdlib_module_names)
