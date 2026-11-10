#!/usr/bin/env python3
"""Local equivalent of the six CI jobs of .github/workflows/ci.yml, with a verifiable evidence record.

Used when GitHub-hosted runners are unavailable (docs/38_local_verification.md). It runs, for ONE exact commit:

    Backend / Hermetic, Backend / Integration, Frontend / Build, Browser / E2E,
    Containers / Build & Smoke, Security / Scan

with the same commands, in the same order, with the same gates as ci.yml, and writes outside the repository:

    <evidence-root>/<commit sha>/<run id>/
        manifest.json      machine-readable: commit, tool, host, images, tool versions, every step's command,
                           exit code, timestamps, log path and log SHA-256, every job's status
        SUMMARY.md         the same, for a person
        logs/<job>/<nn>-<step>.log, results/<job>/...   (junit XML, Playwright results)
        SHA256SUMS         every file above; its own digest is the run's evidence digest

Fail closed: a job is "passed" only if every one of its gate steps ran and exited 0. A step that did not run
(an earlier step failed, the job's time ran out, the tool hit an infrastructure error) is never counted as a
pass; the run is "passed" only if all six jobs are. Exit status 0 means exactly that, and nothing else.

It never contacts a travel or model provider (the jobs themselves are offline by design), never uses a secret
(every credential is a throwaway value for a container that exists only during the job), never pushes,
deploys or sets a GitHub status. Standard library only.

    python3 scripts/ci/local_ci.py --commit <sha>            # all six jobs
    python3 scripts/ci/local_ci.py --commit <sha> --jobs backend-hermetic security
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "travelobligator-local-ci/1"
TOOL_DIR = Path(__file__).resolve().parent
RUNNER_DOCKERFILE = TOOL_DIR / "local-runner.Dockerfile"
REPO_SOURCE_URL = "https://github.com/garima24112000/travelobligator"

# The images ci.yml uses (service containers by tag, Trivy at the version trivy-action ed142fd (v0.36.0) installs).
POSTGRES_IMAGE = "postgres:16"
REDIS_IMAGE = "redis:7.4-alpine"
TRIVY_IMAGE = "aquasec/trivy:0.70.0"
TRIVY_CACHE_VOLUME = "travelob-local-ci-trivy-cache"
BASE_IMAGES = ("python:3.11-bookworm", "node:22-bookworm")

JOBS = {  # id -> (CI check name, ci.yml timeout-minutes)
    "backend-hermetic": ("Backend / Hermetic", 20),
    "backend-integration": ("Backend / Integration", 25),
    "frontend": ("Frontend / Build", 15),
    "browser-e2e": ("Browser / E2E", 30),
    "containers": ("Containers / Build & Smoke", 30),
    "security": ("Security / Scan", 20),
}
# A cold local run installs every dependency without a cache; the budget is ci.yml's timeout times this factor.
TIMEOUT_FACTOR = 2

# Throwaway values, exactly as in ci.yml (they exist only for one job's own containers).
CI_PG = {"POSTGRES_DB": "travelob_ci", "POSTGRES_USER": "travelob_ci", "POSTGRES_PASSWORD": "ci_only_not_a_secret"}
INTEGRATION_ENV = {
    "PERSISTENCE_BACKEND": "postgres",
    "PROVIDER_CACHE_BACKEND": "redis",
    "DATABASE_URL": "postgresql://travelob_ci:ci_only_not_a_secret@127.0.0.1:5432/travelob_ci",
    "REDIS_URL": "redis://127.0.0.1:6379/0",
}
INSTALL_BACKEND = "python -m pip install --upgrade pip\npip install -r backend/requirements-dev.txt"
NO_SKIP_ASSERTION = r'''python - /results/integration.xml <<'PY'
import sys
import xml.etree.ElementTree as ET

root = ET.parse(sys.argv[1]).getroot()
suites = [root] if root.tag == "testsuite" else list(root)
total = sum(int(s.get("tests", 0)) for s in suites)
skipped = sum(int(s.get("skipped", 0)) for s in suites)
print(f"tests={total} skipped={skipped}")
if skipped:
    for case in root.iter("testcase"):
        if case.find("skipped") is not None:
            print("skipped:", case.get("classname"), case.get("name"))
    sys.exit("A test skipped although PostgreSQL and Redis are both available.")
PY'''

# Host-side commands get this environment only: never the operator's shell (which may hold real keys).
HOST_ENV_KEEP = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "USER")


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def host_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: os.environ[k] for k in HOST_ENV_KEEP if k in os.environ}
    env.update(extra or {})
    return env


def capture(cmd: list[str], cwd: Path | None = None, check: bool = True) -> str:
    done = subprocess.run(cmd, cwd=cwd, env=host_env(), capture_output=True, text=True)
    if check and done.returncode != 0:
        raise RuntimeError(f"{shlex.join(cmd)} exited {done.returncode}: {done.stderr.strip()[:400]}")
    return done.stdout.strip()


@dataclass
class Step:
    name: str
    command: str
    where: str  # "runner container", "host", ...
    gate: bool = True
    status: str = "not_run"  # passed | failed | timed_out | skipped_after_failure | not_run | error
    exit_code: int | None = None
    started_at: str | None = None
    finished_at: str | None = None
    duration_seconds: float | None = None
    log: str | None = None
    log_sha256: str | None = None
    note: str | None = None


@dataclass
class Job:
    id: str
    check_name: str
    budget_seconds: int
    status: str = "not_run"  # passed | failed | error | not_run
    started_at: str | None = None
    finished_at: str | None = None
    steps: list[Step] = field(default_factory=list)
    results: dict[str, Any] = field(default_factory=dict)
    note: str | None = None


class Run:
    def __init__(self, evidence: Path, home: str) -> None:
        self.evidence = evidence
        self.home = home

    def log_path(self, job: Job, step_no: int, name: str) -> Path:
        slug = "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")[:60]
        path = self.evidence / "logs" / job.id / f"{step_no:02d}-{slug}.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def scrub(self, path: Path) -> None:
        """Logs never carry the operator's home directory (it names the person)."""
        if self.home and path.exists():
            data = path.read_bytes()
            if self.home.encode() in data:
                path.write_bytes(data.replace(self.home.encode(), b"~"))

    def execute(self, job: Job, step: Step, argv: list[str], deadline: float, cwd: Path | None = None,
                failed_before: bool = False, stdin_path: Path | None = None) -> bool:
        """Runs one step unless an earlier gate failed (GitHub's default `if: success()`). True if it passed."""
        job.steps.append(step)
        if failed_before:
            step.status = "skipped_after_failure"
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            step.status, step.note = "timed_out", "the job's time budget was used up before this step"
            return False
        log = self.log_path(job, len(job.steps), step.name)
        step.log = str(log.relative_to(self.evidence))
        step.started_at, started = utcnow(), time.monotonic()
        with log.open("wb") as out:
            out.write(f"$ {step.command}\n# where: {step.where}\n# started: {step.started_at}\n\n".encode())
            out.flush()
            try:
                stdin = stdin_path.open("rb") if stdin_path else subprocess.DEVNULL
                done = subprocess.run(argv, cwd=cwd, env=host_env(), stdout=out, stderr=subprocess.STDOUT,
                                      stdin=stdin, timeout=remaining)
                step.exit_code = done.returncode
                step.status = "passed" if done.returncode == 0 else "failed"
            except subprocess.TimeoutExpired:
                step.status, step.note = "timed_out", f"killed after {int(remaining)} s (job budget)"
            except OSError as exc:
                step.status, step.note = "error", type(exc).__name__
        step.finished_at = utcnow()
        step.duration_seconds = round(time.monotonic() - started, 1)
        with log.open("ab") as out:
            out.write(f"\n# finished: {step.finished_at} status={step.status} exit={step.exit_code}\n".encode())
        self.scrub(log)
        step.log_sha256 = sha256_file(log)
        return step.status == "passed" or not step.gate


class Box:
    """One fresh runner container per job, holding a fresh copy of the commit under /work."""

    def __init__(self, run: Run, job: Job, image: str, source_tar: Path, env: dict[str, str]) -> None:
        self.run, self.job, self.image, self.source_tar, self.env = run, job, image, source_tar, env
        self.name = f"travelob-local-ci-{job.id}-{uuid.uuid4().hex[:8]}"
        self.extra: list[str] = []

    def start(self, deadline: float) -> bool:
        argv = ["docker", "run", "-d", "--name", self.name, "--user", "runner", self.image, "sleep", "infinity"]
        ok = self.run.execute(self.job, Step("Start runner container", shlex.join(argv), "host"), argv, deadline)
        if not ok:
            return False
        argv = ["docker", "exec", "-i", "-u", "runner", self.name, "tar", "-x", "-C", "/work", "-f", "-"]
        return self.run.execute(self.job, Step("Check out the commit (git archive)", "git archive <sha> | " + shlex.join(argv),
                                               "host -> runner container"), argv, deadline, stdin_path=self.source_tar)

    def service(self, name: str, image: str, env: dict[str, str], health: str) -> str:
        container = f"{self.name}-{name}"
        argv = ["docker", "run", "-d", "--name", container, "--network", f"container:{self.name}",
                "--health-cmd", health, "--health-interval", "5s", "--health-timeout", "5s", "--health-retries", "20"]
        for key, value in env.items():
            argv += ["-e", f"{key}={value}"]
        argv.append(image)
        self.extra.append(container)
        return shlex.join(argv)

    def step(self, name: str, script: str, deadline: float, failed: bool, workdir: str = "/work",
             env: dict[str, str] | None = None, gate: bool = True) -> bool:
        merged = {**self.env, **(env or {})}
        argv = ["docker", "exec", "-u", "runner", "-w", workdir]
        for key, value in merged.items():
            argv += ["-e", f"{key}={value}"]
        argv += [self.name, "bash", "-eo", "pipefail", "-c", script]
        shown = f"(cd {workdir} && {script})" if workdir != "/work" else script
        env_shown = " ".join(f"{k}={v}" for k, v in merged.items())
        return self.run.execute(self.job, Step(name, (env_shown + " " if env_shown else "") + shown, "runner container", gate),
                                argv, deadline, failed_before=failed)

    def copy_out(self, path: str, dest: Path) -> bool:
        dest.parent.mkdir(parents=True, exist_ok=True)
        return subprocess.run(["docker", "cp", f"{self.name}:{path}", str(dest)], env=host_env(),
                              capture_output=True).returncode == 0

    def remove(self) -> None:
        for container in [*self.extra, self.name]:
            subprocess.run(["docker", "rm", "-f", "-v", container], env=host_env(), capture_output=True)


def wait_healthy(containers: list[str], deadline: float) -> tuple[bool, str]:
    while time.monotonic() < deadline:
        states = [capture(["docker", "inspect", "-f", "{{.State.Health.Status}}", c], check=False) for c in containers]
        if all(s == "healthy" for s in states):
            return True, "healthy"
        if any(s in ("unhealthy", "") for s in states) and time.monotonic() > deadline - 1:
            break
        time.sleep(2)
    return False, "service containers did not become healthy"


# -- the six jobs (same steps, order and gates as ci.yml) --------------------------------------------------------

def job_backend_hermetic(run: Run, job: Job, ctx: dict[str, Any], deadline: float) -> None:
    box = Box(run, job, ctx["runner_image"], ctx["source_tar"], {})
    try:
        failed = not box.start(deadline)
        failed = not box.step("Install backend dependencies", INSTALL_BACKEND, deadline, failed) or failed
        failed = not box.step("Compile check", "python -m compileall -q backend/app backend/scripts", deadline, failed) or failed
        failed = not box.step("Run tests (hermetic)", "python -m pytest --junitxml=/results/hermetic.xml", deadline, failed) or failed
        if box.copy_out("/results/hermetic.xml", run.evidence / "results" / job.id / "hermetic.xml"):
            job.results["junit"] = junit_counts(run.evidence / "results" / job.id / "hermetic.xml")
    finally:
        box.remove()


def job_backend_integration(run: Run, job: Job, ctx: dict[str, Any], deadline: float) -> None:
    box = Box(run, job, ctx["runner_image"], ctx["source_tar"], dict(INTEGRATION_ENV))
    try:
        failed = not box.start(deadline)
        if not failed:
            pg = box.service("postgres", POSTGRES_IMAGE, CI_PG, "pg_isready -U travelob_ci -d travelob_ci")
            rd = box.service("redis", REDIS_IMAGE, {}, "redis-cli ping")
            for command, label in ((pg, "Start service postgres:16"), (rd, "Start service redis:7.4-alpine")):
                failed = not run.execute(job, Step(label, command, "host"), shlex.split(command), deadline, failed_before=failed) or failed
            step = Step("Wait for healthy services", "docker inspect .State.Health.Status (postgres, redis)", "host")
            job.steps.append(step)
            if failed:
                step.status = "skipped_after_failure"
            else:
                step.started_at = utcnow()
                ok, note = wait_healthy(box.extra, min(deadline, time.monotonic() + 180))
                step.status, step.note, step.finished_at = ("passed" if ok else "failed"), note, utcnow()
                failed = not ok
        failed = not box.step("Install backend dependencies", INSTALL_BACKEND, deadline, failed) or failed
        failed = not box.step("Verify a single Alembic head", (
            'heads="$(alembic heads | grep -c .)"\n'
            'if [ "${heads}" != "1" ]; then\n  echo "Expected exactly one Alembic head, found ${heads}:"\n  alembic heads\n  exit 1\nfi'),
            deadline, failed, workdir="/work/backend") or failed
        failed = not box.step("Migrate the empty database to head", 'alembic upgrade head\nalembic current | grep -q "(head)"',
                              deadline, failed, workdir="/work/backend") or failed
        failed = not box.step("PostgreSQL client tools for the restore drill", (
            "command -v pg_dump && command -v pg_restore\npg_dump --version\npg_restore --version\n"
            "major=\"$(pg_dump --version | grep -oE '[0-9]+' | head -n1)\"\n"
            'if [ "${major}" -lt 16 ]; then echo "pg_dump ${major} is older than the PostgreSQL 16 service."; exit 1; fi'),
            deadline, failed) or failed
        failed = not box.step("Run tests (PostgreSQL + Redis gates on)", "python -m pytest --junitxml=/results/integration.xml",
                              deadline, failed, env={"TRAVELOB_RUN_POSTGRES_TESTS": "1", "TRAVELOB_RUN_REDIS_TESTS": "1"}) or failed
        failed = not box.step("Assert that no test skipped", NO_SKIP_ASSERTION, deadline, failed) or failed
        if box.copy_out("/results/integration.xml", run.evidence / "results" / job.id / "integration.xml"):
            job.results["junit"] = junit_counts(run.evidence / "results" / job.id / "integration.xml")
    finally:
        box.remove()


def job_frontend(run: Run, job: Job, ctx: dict[str, Any], deadline: float) -> None:
    box = Box(run, job, ctx["runner_image"], ctx["source_tar"], {"NEXT_TELEMETRY_DISABLED": "1"})
    try:
        failed = not box.start(deadline)
        for name, command in (("Install frontend dependencies", "npm ci"), ("Type check", "npx tsc --noEmit"),
                              ("Lint", "npm run lint"), ("Unit tests", "npm test"), ("Build", "npm run build"),
                              ("Production headers check", "npm run check:headers")):
            failed = not box.step(name, command, deadline, failed, workdir="/work/frontend") or failed
    finally:
        box.remove()


def job_browser_e2e(run: Run, job: Job, ctx: dict[str, Any], deadline: float) -> None:
    box = Box(run, job, ctx["runner_image"], ctx["source_tar"], {"NEXT_TELEMETRY_DISABLED": "1", "E2E_PYTHON": "python"})
    try:
        failed = not box.start(deadline)
        failed = not box.step("Install backend dependencies", INSTALL_BACKEND, deadline, failed) or failed
        failed = not box.step("Install frontend dependencies", "npm ci", deadline, failed, workdir="/work/frontend") or failed
        # ci.yml: `npx playwright install --with-deps chromium`; the system libraries are in the runner image
        # (installed for this commit's Playwright version), so only the browser is installed here.
        failed = not box.step("Install the browser", "npx playwright install chromium", deadline, failed, workdir="/work/frontend") or failed
        failed = not box.step("Run browser tests", "npm run e2e", deadline, failed, workdir="/work/frontend") or failed
        # `if: !cancelled()` in ci.yml: runs after a failure too; informational, as in CI.
        box.step("Report counts", "node e2e/support/report-counts.mjs e2e-results/results.json", deadline, False,
                 workdir="/work/frontend", gate=False)
        target = run.evidence / "results" / job.id / "e2e-results"
        if box.copy_out("/work/frontend/e2e-results", target):
            results = target / "results.json"
            if results.exists():
                job.results["playwright_stats"] = json.loads(results.read_text(encoding="utf-8")).get("stats")
    finally:
        box.remove()


def trivy(args: list[str], mounts: list[str]) -> list[str]:
    argv = ["docker", "run", "--rm", "-v", f"{TRIVY_CACHE_VOLUME}:/root/.cache/trivy"]
    for mount in mounts:
        argv += ["-v", mount]
    return argv + [TRIVY_IMAGE, *args]


def job_containers(run: Run, job: Job, ctx: dict[str, Any], deadline: float) -> None:
    src, sha = ctx["host_source"], ctx["commit"]
    labels = ["--label", f"org.opencontainers.image.revision={sha}", "--label", f"org.opencontainers.image.source={REPO_SOURCE_URL}"]
    builds = (
        ("Build backend production image", ["docker", "buildx", "build", "--load", "--provenance=false", *labels,
                                            "-t", "travelobligator-backend:local", "backend"]),
        ("Build frontend production image", ["docker", "buildx", "build", "--load", "--provenance=false", "--target", "production",
                                             *labels, "-t", "travelobligator-frontend:local", "frontend"]),
    )
    failed = False
    for name, argv in builds:
        failed = not run.execute(job, Step(name, shlex.join(argv), "host (Docker)"), argv, deadline, cwd=src, failed_before=failed) or failed
    audit = ["env", f"EXPECT_REVISION={sha}", "bash", "scripts/ci/image_audit.sh", "travelobligator-backend:local", "travelobligator-frontend:local"]
    failed = not run.execute(job, Step("Audit built images", shlex.join(audit), "host"), audit, deadline, cwd=src, failed_before=failed) or failed
    sock = "/var/run/docker.sock:/var/run/docker.sock"
    for image in ("backend", "frontend"):
        ref = f"travelobligator-{image}:local"
        report = trivy(["image", "--scanners", "vuln", "--severity", "MEDIUM,HIGH,CRITICAL", "--exit-code", "0", ref], [sock])
        run.execute(job, Step(f"{image.title()} image vulnerabilities (report, non-blocking)", shlex.join(report), "host (Trivy container)", gate=False),
                    report, deadline, failed_before=failed)
        gate = trivy(["image", "--scanners", "vuln", "--severity", "HIGH,CRITICAL", "--ignore-unfixed", "--exit-code", "1", ref], [sock])
        failed = not run.execute(job, Step(f"{image.title()} image vulnerabilities (gate)", shlex.join(gate), "host (Trivy container)"),
                                 gate, deadline, failed_before=failed) or failed
    # `if: !cancelled()` in ci.yml: the smoke runs even when a vulnerability gate failed (the job still fails).
    smoke = ["bash", "scripts/ci/container_smoke.sh"]
    images_built = all(s.status == "passed" for s in job.steps[:2])
    run.execute(job, Step("Production-style container smoke", shlex.join(smoke), "host (Docker Compose)"), smoke, deadline,
                cwd=src, failed_before=not images_built)
    job.results["images"] = {name: image_identity(name) for name in ("travelobligator-backend:local", "travelobligator-frontend:local")}


def job_security(run: Run, job: Job, ctx: dict[str, Any], deadline: float) -> None:
    mount = f"{ctx['host_source']}:/src:ro"
    failed = False
    secret = trivy(["fs", "--scanners", "secret", "--exit-code", "1", "/src"], [mount])
    failed = not run.execute(job, Step("Repository secret scan (gate)", shlex.join(secret), "host (Trivy container)"), secret, deadline) or failed
    report = trivy(["fs", "--scanners", "vuln", "--severity", "MEDIUM,HIGH,CRITICAL", "--exit-code", "0", "/src"], [mount])
    run.execute(job, Step("Dependency vulnerabilities (report, non-blocking)", shlex.join(report), "host (Trivy container)", gate=False),
                report, deadline, failed_before=failed)
    gate = trivy(["fs", "--scanners", "vuln", "--severity", "HIGH,CRITICAL", "--ignore-unfixed", "--exit-code", "1", "/src"], [mount])
    run.execute(job, Step("Dependency vulnerabilities (gate)", shlex.join(gate), "host (Trivy container)"), gate, deadline, failed_before=failed)


JOB_FUNCTIONS = {
    "backend-hermetic": job_backend_hermetic,
    "backend-integration": job_backend_integration,
    "frontend": job_frontend,
    "browser-e2e": job_browser_e2e,
    "containers": job_containers,
    "security": job_security,
}


# -- evidence helpers ---------------------------------------------------------------------------------------------

def junit_counts(path: Path) -> dict[str, int]:
    import xml.etree.ElementTree as ET

    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root)
    return {key: sum(int(s.get(key, 0)) for s in suites) for key in ("tests", "failures", "errors", "skipped")}


def image_identity(ref: str) -> dict[str, Any]:
    raw = capture(["docker", "image", "inspect", ref], check=False)
    if not raw:
        return {"present": False}
    data = json.loads(raw)[0]
    return {"present": True, "id": data.get("Id"), "repo_digests": data.get("RepoDigests"), "architecture": data.get("Architecture"),
            "os": data.get("Os"), "revision_label": (data.get("Config", {}).get("Labels") or {}).get("org.opencontainers.image.revision")}


def tool_versions(image: str) -> dict[str, str]:
    script = ("echo python=$(python --version 2>&1); echo node=$(node --version); echo npm=$(npm --version); "
              "echo pg_dump=$(pg_dump --version); echo gpg=\"$(gpg --version | head -n1)\"; echo git=\"$(git --version)\"; "
              "echo os=\"$(. /etc/os-release && echo $PRETTY_NAME)\"; echo arch=$(uname -m)")
    out = capture(["docker", "run", "--rm", "--user", "runner", image, "bash", "-c", script], check=False)
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


def trivy_db_metadata() -> Any:
    out = capture(trivy(["version", "--format", "json"], []), check=False)
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"unparsed": out[:500]}


def write_summary(path: Path, manifest: dict[str, Any]) -> None:
    lines = [f"# Local CI verification — {manifest['overall_status'].upper()}", "",
             f"- Commit: `{manifest['commit']['sha']}` (tree `{manifest['commit']['tree']}`)",
             f"- Refs pointing at it: {', '.join(manifest['commit']['refs']) or 'none'}",
             f"- Run: `{manifest['run_id']}`, {manifest['started_at']} → {manifest['finished_at']} (UTC)",
             f"- Tool: `scripts/ci/local_ci.py` sha256 `{manifest['tool']['local_ci_py_sha256']}` from commit `{manifest['tool']['commit']}`"
             f" (tool files clean: {manifest['tool']['tool_files_clean']})",
             f"- Host: {manifest['host']['os']} {manifest['host']['machine']}, Docker {manifest['host']['docker_server']}",
             f"- Runner image: `{manifest['images']['runner'].get('id')}` ({manifest['runner_tool_versions'].get('os')}, "
             f"{manifest['runner_tool_versions'].get('arch')}, {manifest['runner_tool_versions'].get('python')}, "
             f"node {manifest['runner_tool_versions'].get('node')})", "",
             "| Check | Status | Started | Finished | Notes |", "| --- | --- | --- | --- | --- |"]
    for job in manifest["jobs"]:
        notes = []
        if job["results"].get("junit"):
            notes.append("junit " + ", ".join(f"{k}={v}" for k, v in job["results"]["junit"].items()))
        if job["results"].get("playwright_stats"):
            notes.append("playwright " + ", ".join(f"{k}={v}" for k, v in job["results"]["playwright_stats"].items()
                                                   if k in ("expected", "unexpected", "flaky", "skipped")))
        if job.get("note"):
            notes.append(job["note"])
        lines.append(f"| {job['check_name']} | **{job['status']}** | {job['started_at'] or '—'} | {job['finished_at'] or '—'} | {'; '.join(notes)} |")
    lines += ["", "## Steps", ""]
    for job in manifest["jobs"]:
        lines += [f"### {job['check_name']} — {job['status']}", "", "| # | Step | Gate | Status | Exit | Seconds | Log (sha256) |",
                  "| --- | --- | --- | --- | --- | --- | --- |"]
        for n, step in enumerate(job["steps"], 1):
            log = f"`{step['log']}` (`{step['log_sha256'][:16]}…`)" if step["log"] else "—"
            lines.append(f"| {n} | {step['name']} | {'yes' if step['gate'] else 'no'} | {step['status']} | {step['exit_code']} | "
                         f"{step['duration_seconds']} | {log} |")
        lines.append("")
    lines += ["## Differences from GitHub-hosted CI", "", *[f"- {d}" for d in manifest["differences"]], "",
              "Exact commands, environment and digests: `manifest.json`. Integrity: `SHA256SUMS`.", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


DIFFERENCES = [
    "Runs on the operator's machine in Docker, not on GitHub's ubuntu-24.04 VM: the runner image is Debian bookworm "
    "(python:3.11 + node:22 binaries + postgresql-client-16 from apt.postgresql.org) on the host's native architecture.",
    "Architecture follows the host. On Apple silicon everything (runner, production images, Trivy image scans) is "
    "linux/arm64; GitHub builds and scans linux/amd64. Image vulnerability results can differ by architecture.",
    "No dependency cache (actions/setup-python, setup-node, buildx type=gha): every install is cold. Job time budget "
    f"is ci.yml's timeout-minutes x {TIMEOUT_FACTOR}.",
    "Checkout is `git archive <sha>` (tracked files at that commit, no .git directory); actions/checkout has a .git "
    "directory. Trivy's secret scan therefore never sees .git (it skips it on CI as well).",
    "Chromium's system libraries are installed when the runner image is built (`playwright install-deps` for the "
    "commit's pinned Playwright version) instead of `--with-deps` inside the job.",
    "Trivy runs from the aquasec/trivy:0.70.0 image (the version trivy-action v0.36.0 installs); its vulnerability "
    "database is downloaded at run time, so results depend on the database date recorded in manifest.json.",
    "Service containers share the runner container's network namespace (127.0.0.1:5432 / :6379, as on CI).",
    "Not reproduced: actions/upload-artifact (results are kept in the evidence folder instead), the job summary page, "
    "concurrency cancellation, and GitHub's check-run status. Nothing is reported to GitHub by this tool.",
    "Jobs run one after another (CI runs them in parallel); each job gets its own fresh container and checkout.",
    "The Containers job replaces the local tags travelobligator-backend:local and travelobligator-frontend:local.",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--commit", default="HEAD", help="the commit to verify (default HEAD of this checkout)")
    parser.add_argument("--evidence-root", default=str(Path.home() / "TravelObligatorEvidence" / "local-ci"),
                        help="evidence is written under <root>/<sha>/<run id>/ (outside the repository)")
    parser.add_argument("--jobs", nargs="+", choices=list(JOBS), default=list(JOBS))
    args = parser.parse_args(argv)
    os.umask(0o077)

    repo = Path(capture(["git", "-C", str(TOOL_DIR), "rev-parse", "--show-toplevel"]))
    sha = capture(["git", "-C", str(repo), "rev-parse", "--verify", f"{args.commit}^{{commit}}"])
    evidence_root = Path(args.evidence_root).expanduser().resolve()
    if evidence_root == repo or repo in evidence_root.parents:
        print("refused: the evidence root must be outside the repository", file=sys.stderr)
        return 2
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    evidence = evidence_root / sha / run_id
    evidence.mkdir(parents=True, mode=0o700)
    for parent in (evidence_root, evidence_root / sha):
        parent.chmod(0o700)
    run = Run(evidence, str(Path.home()))
    started_at = utcnow()

    work = Path(tempfile.mkdtemp(prefix="travelob-local-ci-"))
    manifest: dict[str, Any] = {"schema": SCHEMA, "run_id": run_id, "started_at": started_at}
    try:
        source_tar = work / "source.tar"
        subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", "-o", str(source_tar), sha], check=True, env=host_env())
        source_tar.chmod(0o644)
        host_source = work / "src"
        host_source.mkdir()
        with tarfile.open(source_tar) as archive:
            archive.extractall(host_source, filter="data")
        lock = json.loads((host_source / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
        playwright = lock["packages"]["node_modules/@playwright/test"]["version"]

        tool_rel = [str(p.relative_to(repo)) for p in (Path(__file__).resolve(), RUNNER_DOCKERFILE)]
        tool_clean = capture(["git", "-C", str(repo), "status", "--porcelain", "--", *tool_rel]) == ""
        manifest.update({
            "commit": {
                "sha": sha,
                "tree": capture(["git", "-C", str(repo), "rev-parse", f"{sha}^{{tree}}"]),
                "subject": capture(["git", "-C", str(repo), "log", "-1", "--format=%s", sha]),
                "committed_at": capture(["git", "-C", str(repo), "log", "-1", "--format=%cI", sha]),
                "refs": sorted(set(capture(["git", "-C", str(repo), "for-each-ref", "--points-at", sha, "--format=%(refname)"]).split())),
                "source_archive_sha256": sha256_file(source_tar),
            },
            "tool": {
                "commit": capture(["git", "-C", str(repo), "rev-parse", "HEAD"]),
                "local_ci_py_sha256": sha256_file(Path(__file__).resolve()),
                "runner_dockerfile_sha256": sha256_file(RUNNER_DOCKERFILE),
                "tool_files_clean": tool_clean,
                "ci_yml_sha256_at_commit": hashlib.sha256((host_source / ".github" / "workflows" / "ci.yml").read_bytes()).hexdigest(),
            },
            "host": {
                "os": f"{platform.system()} {platform.release()} ({platform.mac_ver()[0] or platform.version()})",
                "machine": platform.machine(),
                "python": platform.python_version(),
                "docker_server": capture(["docker", "version", "--format", "{{.Server.Version}} ({{.Server.Os}}/{{.Server.Arch}})"]),
                "docker_client": capture(["docker", "version", "--format", "{{.Client.Version}}"]),
                "buildx": capture(["docker", "buildx", "version"]),
                "compose": capture(["docker", "compose", "version", "--short"]),
                "git": capture(["git", "--version"]),
            },
            "differences": DIFFERENCES,
            "privacy": "No secret is used. Host commands get only PATH/HOME/TMPDIR/locale/Docker variables (never the operator's "
                       "shell environment). The operator's home directory is replaced by ~ in every log. Folder mode 0700, files 0600.",
        })

        # Images: pull what ci.yml uses, build the runner for this commit's Playwright version.
        prep = Job("prepare", "Prepare images", 1800)
        deadline = time.monotonic() + 1800
        ok = True
        for image in (*BASE_IMAGES, POSTGRES_IMAGE, REDIS_IMAGE, TRIVY_IMAGE):
            ok = run.execute(prep, Step(f"Pull {image}", f"docker pull {image}", "host"), ["docker", "pull", image], deadline) and ok
        runner_image = f"travelob-local-ci-runner:pw{playwright}"
        build = ["docker", "build", "--pull=false", "-f", str(RUNNER_DOCKERFILE), "--build-arg", f"PLAYWRIGHT_VERSION={playwright}",
                 "-t", runner_image, str(TOOL_DIR)]
        ok = run.execute(prep, Step("Build runner image", shlex.join(build).replace(str(repo), "<tool checkout>"), "host"), build, deadline) and ok
        prep.status = "passed" if ok else "error"
        manifest["preparation"] = asdict(prep)
        manifest["images"] = {"runner": image_identity(runner_image),
                              **{image: image_identity(image) for image in (*BASE_IMAGES, POSTGRES_IMAGE, REDIS_IMAGE, TRIVY_IMAGE)}}
        manifest["runner_tool_versions"] = tool_versions(runner_image) if ok else {}
        manifest["playwright_version"] = playwright

        ctx = {"commit": sha, "runner_image": runner_image, "source_tar": source_tar, "host_source": host_source}
        jobs: list[Job] = []
        for job_id in JOBS:
            check_name, minutes = JOBS[job_id]
            job = Job(job_id, check_name, minutes * 60 * TIMEOUT_FACTOR)
            jobs.append(job)
            if job_id not in args.jobs:
                job.note = "not selected for this run"
                continue
            if not ok:
                job.status, job.note = "error", "image preparation failed; nothing was run"
                continue
            print(f"[{utcnow()}] {check_name} ...", flush=True)
            job.started_at = utcnow()
            try:
                JOB_FUNCTIONS[job_id](run, job, ctx, time.monotonic() + job.budget_seconds)
                gates = [s for s in job.steps if s.gate]
                job.status = "passed" if gates and all(s.status == "passed" for s in gates) else "failed"
            except Exception as exc:  # an infrastructure fault is never a pass
                job.status, job.note = "error", f"{type(exc).__name__}: {str(exc)[:300]}"
            job.finished_at = utcnow()
            print(f"[{job.finished_at}] {check_name}: {job.status}", flush=True)
        if any(j.id == "containers" and j.status != "not_run" for j in jobs) or "security" in args.jobs:
            manifest["trivy"] = trivy_db_metadata()
        manifest["jobs"] = [asdict(j) for j in jobs]
        manifest["selected_jobs"] = args.jobs
        manifest["overall_status"] = ("passed" if all(j.status == "passed" for j in jobs) else
                                      "failed" if set(args.jobs) == set(JOBS) else "incomplete")
    except Exception as exc:
        manifest["overall_status"] = "error"
        manifest["error"] = f"{type(exc).__name__}: {str(exc)[:500]}"
        manifest.setdefault("jobs", [])
    finally:
        shutil.rmtree(work, ignore_errors=True)
        subprocess.run(["docker", "compose", "-p", "travelob-smoke", "down", "-v", "--remove-orphans"], env=host_env(), capture_output=True)

    manifest["finished_at"] = utcnow()
    (evidence / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if manifest.get("commit") and manifest.get("images"):
        write_summary(evidence / "SUMMARY.md", manifest)
    files = sorted(p for p in evidence.rglob("*") if p.is_file() and p.name != "SHA256SUMS")
    (evidence / "SHA256SUMS").write_text("".join(f"{sha256_file(p)}  {p.relative_to(evidence)}\n" for p in files), encoding="utf-8")
    for path in evidence.rglob("*"):
        path.chmod(0o700 if path.is_dir() else 0o600)
    print(f"overall: {manifest['overall_status']}")
    print(f"evidence: {evidence}")
    print(f"evidence digest (sha256 of SHA256SUMS): {sha256_file(evidence / 'SHA256SUMS')}")
    return 0 if manifest["overall_status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
