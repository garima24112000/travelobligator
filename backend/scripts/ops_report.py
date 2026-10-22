#!/usr/bin/env python3
"""Operator tool: a read-only health report of one deployment, from its database.

    python backend/scripts/ops_report.py            # the last 24 hours
    python backend/scripts/ops_report.py --json     # the same, machine-readable

Reads `OPS_REPORT_DATABASE_URL` (not `DATABASE_URL`, so it is always a deliberate choice of database) and
runs SELECTs only. When and how to use it: docs/25_operations_runbook.md, "Monitoring".

It answers the questions the free-tier deployment cannot answer by itself (no metrics scraper, no error
tracker): is the schema the one this code expects; did generations fail, get interrupted or get stuck; how
close are the generation, sign-up and provider-credit limits; how big is the database.

What it prints: counts, revision ids and fixed labels. Never an email address, a trip, job or user id,
a destination, itinerary text or a connection string.

Exit code: 0 nothing to act on, 1 at least one warning, 2 at least one critical finding, 3 the report could
not be produced. The thresholds are in `evaluate` and are deliberately simple.

Limits of what it can see:
  * Provider credits are taken from each trip's LAST generation only (`provider_usage_report` is replaced
    by the next generation of the same trip), so the credit total is a lower bound. The provider's own
    dashboard is the authoritative figure.
  * A provider's rate limiting or outage is visible only in the application log (`provider.failure`),
    not in the database; here it shows up indirectly, as failed jobs or blocked plans.
  * It reads the database it is pointed at. It says nothing about whether the web service is reachable.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

DATABASE_ENV = "OPS_REPORT_DATABASE_URL"
PRESSURE_SHARE = 0.8  # "close to a limit" = 80 % of it
# A running job whose lease ran out this long ago was abandoned and not yet closed (the next startup or the
# next admission closes it). Shorter than this is the normal gap between a hard stop and that cleanup.
STUCK_GRACE = timedelta(minutes=15)
# The render.yaml variables of the limits this report compares against.
_LIMIT_VARIABLES = frozenset(
    {
        "GENERATION_MAX_JOBS_GLOBAL_PER_WINDOW",
        "GENERATION_MAX_JOBS_PER_USER_PER_WINDOW",
        "GENERATION_LIMIT_WINDOW_SECONDS",
        "SIGNUP_MAX_PER_WINDOW_GLOBAL",
        "SIGNUP_LIMIT_WINDOW_SECONDS",
    }
)


@dataclass
class Limits:
    generation_global_per_window: int
    generation_per_user_per_window: int
    generation_window_seconds: int
    signup_global_per_window: int
    signup_window_seconds: int
    geoapify_daily_credits: int


@dataclass
class Snapshot:
    """Everything `evaluate` judges. Numbers and revision ids only."""

    database_revisions: list[str] = field(default_factory=list)
    code_revisions: list[str] = field(default_factory=list)
    users: int = 0
    trips: int = 0
    database_bytes: int = 0
    jobs_admitted: int = 0  # in the generation window, including jobs of since-deleted trips
    jobs_by_status: dict[str, int] = field(default_factory=dict)
    failures_by_code: dict[str, int] = field(default_factory=dict)
    jobs_stuck: int = 0
    users_at_generation_limit: int = 0
    signups: int = 0
    failed_sign_ins: int = 0
    plans_updated: int = 0
    plans_blocked: int = 0
    credits_used: int = 0
    generations_refused_a_provider_call: int = 0


@dataclass
class Finding:
    severity: str  # "critical" | "warning" | "note"
    code: str
    message: str


def evaluate(snapshot: Snapshot, limits: Limits) -> list[Finding]:
    """Pure: the same snapshot always gives the same findings."""
    found: list[Finding] = []

    def add(severity: str, code: str, message: str) -> None:
        found.append(Finding(severity, code, message))

    if snapshot.database_revisions != snapshot.code_revisions:
        add(
            "critical",
            "schema_mismatch",
            f"The database is at revision {', '.join(snapshot.database_revisions) or 'none'} and this code expects "
            f"{', '.join(snapshot.code_revisions)}. A backend built from this code will not start, and one that is "
            "already running reports not ready.",
        )

    if snapshot.jobs_stuck:
        add("critical", "jobs_stuck", f"{snapshot.jobs_stuck} running job(s) lost their lease more than {int(STUCK_GRACE.total_seconds() // 60)} minutes ago and were not closed. They hold a concurrency slot until the backend next starts or admits a job.")

    failed = snapshot.jobs_by_status.get("failed", 0)
    finished = failed + snapshot.jobs_by_status.get("succeeded", 0)
    interrupted = snapshot.failures_by_code.get("JOB_INTERRUPTED", 0)
    other_failures = failed - interrupted
    if other_failures >= 3 or (finished >= 4 and other_failures * 2 >= finished):
        add("warning", "generation_failures", f"{other_failures} of {finished} finished generation job(s) failed (not counting interruptions). Read the application log for `job.failed` and `provider.failure`.")
    elif other_failures:
        add("note", "generation_failures", f"{other_failures} generation job(s) failed.")
    if interrupted >= 3:
        add("warning", "generation_interrupted", f"{interrupted} generation job(s) were interrupted. The service is being stopped or restarted while people generate (sleep, redeploy, platform restart).")
    elif interrupted:
        add("note", "generation_interrupted", f"{interrupted} generation job(s) were interrupted by a stop or restart; the traveller can start again.")

    def pressure(code: str, what: str, used: int, limit: int) -> None:
        if limit <= 0:
            return
        if used >= limit:
            add("warning", f"{code}_reached", f"{what}: {used} of {limit}. The limit is reached; further requests are refused until the window moves on.")
        elif used >= limit * PRESSURE_SHARE:
            add("warning", f"{code}_pressure", f"{what}: {used} of {limit} ({round(100 * used / limit)} %).")

    pressure("generation_global_limit", "Generations in the window, all accounts", snapshot.jobs_admitted, limits.generation_global_per_window)
    pressure("signup_limit", "Accounts created in the window", snapshot.signups, limits.signup_global_per_window)
    pressure("provider_credits", "Geoapify credits recorded in the last day (a lower bound)", snapshot.credits_used, limits.geoapify_daily_credits)
    if snapshot.users_at_generation_limit:
        add("note", "generation_user_limit_reached", f"{snapshot.users_at_generation_limit} account(s) are at their own generation limit.")
    if snapshot.generations_refused_a_provider_call:
        add("warning", "provider_credit_cap_hit", f"{snapshot.generations_refused_a_provider_call} generation(s) ran into the per-generation provider credit cap and had a request refused locally.")
    if snapshot.plans_updated >= 4 and snapshot.plans_blocked * 2 >= snapshot.plans_updated:
        add("warning", "plans_blocked", f"{snapshot.plans_blocked} of {snapshot.plans_updated} plans written in the window are blocked. Usual causes: a provider that is rate-limited or out of credits, or a destination with too little verified inventory.")
    elif snapshot.plans_blocked:
        add("note", "plans_blocked", f"{snapshot.plans_blocked} plan(s) written in the window are blocked.")
    return found


def exit_code(findings: list[Finding]) -> int:
    severities = {finding.severity for finding in findings}
    return 2 if "critical" in severities else 1 if "warning" in severities else 0


def collect(connection, limits: Limits, now: datetime) -> Snapshot:
    """SELECTs only. Tables of a later migration than the database has are simply reported as zero, so the
    report also works (and says `schema_mismatch`) against a database that is behind."""
    from sqlalchemy import text

    from scripts.db_backup_restore import code_heads

    tables = {row[0] for row in connection.execute(text("select tablename from pg_tables where schemaname = 'public'"))}

    def scalar(query: str, needs: tuple[str, ...], **parameters: Any) -> int:
        if not set(needs) <= tables:
            return 0
        return int(connection.execute(text(query), parameters).scalar() or 0)

    generation_since = now - timedelta(seconds=limits.generation_window_seconds)
    signup_since = now - timedelta(seconds=limits.signup_window_seconds)
    day_ago = now - timedelta(days=1)
    snapshot = Snapshot(code_revisions=code_heads())
    if "alembic_version" in tables:
        snapshot.database_revisions = sorted(row[0] for row in connection.execute(text("select version_num from alembic_version")))
    snapshot.users = scalar("select count(*) from users", ("users",))
    snapshot.trips = scalar("select count(*) from trips", ("trips",))
    snapshot.database_bytes = scalar("select pg_database_size(current_database())", ())

    if "generation_jobs" in tables:
        rows = connection.execute(text("select status, count(*) from generation_jobs where created_at >= :since group by status"), {"since": generation_since})
        snapshot.jobs_by_status = {status: int(count) for status, count in rows}
        rows = connection.execute(
            text("select coalesce(error_code, 'UNKNOWN'), count(*) from generation_jobs where created_at >= :since and status = 'failed' group by 1"),
            {"since": generation_since},
        )
        snapshot.failures_by_code = {code: int(count) for code, count in rows}
    snapshot.jobs_admitted = sum(snapshot.jobs_by_status.values()) + scalar(
        "select count(*) from deleted_job_usage where created_at >= :since", ("deleted_job_usage",), since=generation_since
    )
    snapshot.jobs_stuck = scalar(
        "select count(*) from generation_jobs where status = 'running' and lease_expires_at is not null and lease_expires_at < :cutoff",
        ("generation_jobs",),
        cutoff=now - STUCK_GRACE,
    )
    if limits.generation_per_user_per_window > 0:
        snapshot.users_at_generation_limit = scalar(
            "select count(*) from (select owner_id from generation_jobs where created_at >= :since group by owner_id having count(*) >= :limit) busy",
            ("generation_jobs",),
            since=generation_since,
            limit=limits.generation_per_user_per_window,
        )
    snapshot.signups = scalar("select count(*) from auth_attempts where kind = 'signup' and created_at >= :since", ("auth_attempts",), since=signup_since)
    snapshot.failed_sign_ins = scalar("select count(*) from auth_attempts where kind <> 'signup' and created_at >= :since", ("auth_attempts",), since=day_ago)
    snapshot.plans_updated = scalar("select count(*) from planning_states where updated_at >= :since", ("planning_states",), since=generation_since)
    snapshot.plans_blocked = scalar(
        "select count(*) from planning_states where updated_at >= :since and pipeline_status = 'blocked'", ("planning_states",), since=generation_since
    )
    snapshot.credits_used = scalar(
        "select coalesce(sum((state #>> '{provider_usage_report,credits_used}')::numeric), 0) from planning_states "
        "where updated_at >= :since and jsonb_typeof(state -> 'provider_usage_report') = 'object'",
        ("planning_states",),
        since=day_ago,
    )
    snapshot.generations_refused_a_provider_call = scalar(
        "select count(*) from planning_states where updated_at >= :since "
        "and jsonb_typeof(state -> 'provider_usage_report') = 'object' "
        "and coalesce((state #>> '{provider_usage_report,refused_calls}')::numeric, 0) > 0",
        ("planning_states",),
        since=day_ago,
    )
    return snapshot


def default_limits(geoapify_daily_credits: int) -> Limits:
    """The limits production is declared to run with: the literal values of `render.yaml` over the code's
    defaults. Never the project's `.env`, and never the dashboard (which this tool cannot see: a limit
    changed only there is not reflected here)."""
    from app.core.config import Settings

    declared: dict[str, str] = {}
    try:
        import yaml

        services = yaml.safe_load((_BACKEND_ROOT.parent / "render.yaml").read_text())["services"]
        declared = {
            entry["key"]: str(entry["value"])
            for entry in services[0]["envVars"]
            if "value" in entry and entry["key"] in _LIMIT_VARIABLES
        }
    except Exception:
        declared = {}  # no readable render.yaml (or no YAML library): the code's defaults stand
    # passed by variable name, which takes precedence over the same variable in this shell's environment
    settings = Settings(_env_file=None, **declared)
    return Limits(
        generation_global_per_window=settings.generation_max_jobs_global_per_window,
        generation_per_user_per_window=settings.generation_max_jobs_per_user_per_window,
        generation_window_seconds=settings.generation_limit_window_seconds,
        signup_global_per_window=settings.signup_max_per_window_global,
        signup_window_seconds=settings.signup_limit_window_seconds,
        geoapify_daily_credits=geoapify_daily_credits,
    )


def render(snapshot: Snapshot, limits: Limits, findings: list[Finding]) -> str:
    hours = round(limits.generation_window_seconds / 3600)
    jobs = ", ".join(f"{count} {status}" for status, count in sorted(snapshot.jobs_by_status.items())) or "none"
    codes = ", ".join(f"{count} {code}" for code, count in sorted(snapshot.failures_by_code.items()))
    lines = [
        f"Schema: database {', '.join(snapshot.database_revisions) or 'none'}; this code {', '.join(snapshot.code_revisions)}",
        f"Accounts: {snapshot.users}   Trips: {snapshot.trips}   Database size: {snapshot.database_bytes / 1_048_576:.1f} MB",
        f"Generations, last {hours} h: {snapshot.jobs_admitted} admitted of {limits.generation_global_per_window} allowed ({jobs})",
        f"  failures by cause: {codes or 'none'}; stuck: {snapshot.jobs_stuck}; accounts at their own limit: {snapshot.users_at_generation_limit}",
        f"Plans written, last {hours} h: {snapshot.plans_updated}, of which blocked: {snapshot.plans_blocked}",
        f"Geoapify credits recorded, last 24 h (lower bound): {snapshot.credits_used} of {limits.geoapify_daily_credits}; generations that hit the per-generation cap: {snapshot.generations_refused_a_provider_call}",
        f"Accounts created in the window: {snapshot.signups} of {limits.signup_global_per_window} allowed; failed sign-ins, last 24 h: {snapshot.failed_sign_ins}",
        "",
    ]
    if not findings:
        lines.append("Nothing to act on.")
    for finding in findings:
        lines.append(f"[{finding.severity.upper()}] {finding.code}: {finding.message}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only health report of one deployment, from its database.")
    parser.add_argument("--json", action="store_true", help="print the snapshot and findings as JSON")
    parser.add_argument("--geoapify-daily-credits", type=int, default=3000, help="the provider plan's daily credits (default: 3000)")
    arguments = parser.parse_args(argv)

    os.environ.setdefault("TRAVELOB_TEST_MODE", "1")
    from scripts.db_backup_restore import OperatorError, _engine

    try:
        limits = default_limits(arguments.geoapify_daily_credits)
        engine = _engine(DATABASE_ENV)
        try:
            with engine.connect().execution_options(isolation_level="REPEATABLE READ", postgresql_readonly=True) as connection:
                snapshot = collect(connection, limits, datetime.now(timezone.utc))
        except OperatorError:
            raise
        except Exception:
            raise OperatorError("Could not read the database.") from None
        finally:
            engine.dispose()
    except OperatorError as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return 3

    except Exception as error:
        # unforeseen: the type only, never a driver's message or a traceback (either can name a host)
        print(f"FAILED: unexpected error ({type(error).__name__}).", file=sys.stderr)
        return 3

    findings = evaluate(snapshot, limits)
    if arguments.json:
        print(json.dumps({"snapshot": asdict(snapshot), "limits": asdict(limits), "findings": [asdict(finding) for finding in findings]}, indent=2, sort_keys=True))
    else:
        print(render(snapshot, limits, findings))
    return exit_code(findings)


if __name__ == "__main__":
    sys.exit(main())
