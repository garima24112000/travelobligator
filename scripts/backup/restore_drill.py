#!/usr/bin/env python3
"""Recovery drill: decrypt one encrypted Neon backup and restore it into a THROWAWAY local PostgreSQL.

    python3 scripts/backup/restore_drill.py --file <travelobligator-...pgdump.gpg>
    python3 scripts/backup/restore_drill.py --s3-key neon/<travelobligator-...pgdump.gpg>   # downloads it first
    [--expect-revision <alembic revision>]

Procedure and checklist: docs/30_automated_neon_backups.md, "Restore".

What makes it safe by construction:
  * It accepts NO database URL. The only database it ever writes is a new PostgreSQL 16 container it starts
    itself with `--network none` (no network interface but loopback) and its data directory on tmpfs; it
    talks to it only through `docker exec`, and removes the container when it ends, whatever happened. So
    it cannot reach Neon -- production or any other branch -- or any other server.
  * Decryption must succeed AND authenticate the whole file (GnuPG AEAD); otherwise the partial output is
    deleted and nothing is restored.
  * The plaintext dump lives in a new private (0700) temporary directory only until it has been copied into
    the container, and is deleted on every exit path.
  * It prints the Alembic revision, table names and row counts only -- never a row's content.

Needs: Docker, GnuPG >= 2.3 (`gpg --version`), and for --s3-key the AWS CLI signed in as an identity that
may read the backup prefix. The passphrase is asked for without echo (or read from
BACKUP_ENCRYPTION_PASSPHRASE when that is set, for an unattended drill).
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# The official image, pinned by digest. Its pg_restore (16) reads archives written by pg_dump 16.
POSTGRES_IMAGE = "postgres:16.15-bookworm@sha256:0ea6700a3b4f0ae6ce746519073558aed4d88a79d8d07622a9a644946c7319c4"
DEFAULT_BUCKET = "travelobligator-db-backups-10192026"
DRILL_DATABASE = "restore_drill"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
# Tables every application database since users were added has. Later tables are reported when present.
CORE_TABLES = ("alembic_version", "users", "trips", "planning_states", "generation_jobs", "itinerary_branches", "itinerary_revisions")
# (description, tables it needs, SQL returning one count that must be 0)
INTEGRITY_CHECKS = (
    ("trips whose owner does not exist", ("trips", "users"), "select count(*) from trips t where t.owner_id is not null and not exists (select 1 from users u where u.user_id = t.owner_id)"),
    ("planning states without a trip", ("planning_states", "trips"), "select count(*) from planning_states p where not exists (select 1 from trips t where t.trip_id = p.trip_id)"),
    ("jobs without a trip", ("generation_jobs", "trips"), "select count(*) from generation_jobs j where not exists (select 1 from trips t where t.trip_id = j.trip_id)"),
    ("branches without a trip", ("itinerary_branches", "trips"), "select count(*) from itinerary_branches b where not exists (select 1 from trips t where t.trip_id = b.trip_id)"),
    ("revisions without a trip", ("itinerary_revisions", "trips"), "select count(*) from itinerary_revisions r where not exists (select 1 from trips t where t.trip_id = r.trip_id)"),
)


class DrillError(Exception):
    """A refusal or failure, with a fixed message that is safe to print."""


class _Cancelled(BaseException):
    pass


def _say(message: str) -> None:
    print(message, flush=True)


def _clean_environment() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key != "BACKUP_ENCRYPTION_PASSPHRASE" and not key.startswith("PG")}


def _run(argv: list[str], *, what: str, stdin=subprocess.DEVNULL, **kwargs) -> subprocess.CompletedProcess:
    completed = subprocess.run(argv, env=_clean_environment(), stdin=stdin, capture_output=True, **kwargs)
    if completed.returncode != 0:
        error = completed.stderr if isinstance(completed.stderr, str) else completed.stderr.decode(errors="replace")
        for line in error.splitlines()[-8:]:
            print(f"  {what}: {line}", file=sys.stderr, flush=True)
        raise DrillError(f"{what} failed (exit code {completed.returncode}).")
    return completed


def check_gpg() -> str:
    gpg = shutil.which("gpg")
    if not gpg:
        raise DrillError("gpg was not found. Install GnuPG 2.4 (macOS: `brew install gnupg`; Debian/Ubuntu: `apt install gnupg`).")
    first_line = subprocess.run([gpg, "--version"], capture_output=True, text=True).stdout.splitlines()[:1]
    match = re.search(r"(\d+)\.(\d+)", first_line[0] if first_line else "")
    if not match or (int(match.group(1)), int(match.group(2))) < (2, 3):
        raise DrillError("GnuPG 2.3 or newer is required to decrypt these backups (OCB packets).")
    return gpg


def read_passphrase() -> str:
    passphrase = os.environ.get("BACKUP_ENCRYPTION_PASSPHRASE")
    if passphrase is None:
        passphrase = getpass.getpass("Backup encryption passphrase (not echoed): ")
    passphrase = passphrase.removesuffix("\n").removesuffix("\r")
    if not passphrase or "\n" in passphrase or "\r" in passphrase:
        raise DrillError("No usable passphrase was given.")
    return passphrase


def decrypt(gpg: str, encrypted: Path, target: Path, passphrase: str, home: Path) -> None:
    """Decrypt to `target`. GnuPG writes output as it goes and only finds a damaged or altered file at the
    end, so anything it wrote is deleted unless it exits 0."""
    read_end, write_end = os.pipe()
    os.write(write_end, passphrase.encode() + b"\n")
    os.close(write_end)
    try:
        completed = subprocess.run(
            [gpg, "--batch", "--no-tty", "--quiet", "--homedir", str(home), "--no-symkey-cache", "--pinentry-mode", "loopback",
             "--passphrase-fd", str(read_end), "--output", str(target), "--decrypt", str(encrypted)],
            env=_clean_environment(), stdin=subprocess.DEVNULL, capture_output=True, text=True, pass_fds=(read_end,),
        )
    finally:
        os.close(read_end)
    if completed.returncode != 0 or not target.is_file() or target.stat().st_size == 0:
        target.unlink(missing_ok=True)
        for line in completed.stderr.splitlines()[-4:]:
            print(f"  gpg: {line}", file=sys.stderr, flush=True)
        raise DrillError("Decryption FAILED: wrong passphrase, or the file is damaged, truncated or was altered. Nothing was restored.")


def download(bucket: str, key: str, target: Path) -> None:
    if not re.fullmatch(r"neon/[A-Za-z0-9._-]+\.pgdump\.gpg", key):
        raise DrillError("--s3-key must be an object directly under neon/ ending in .pgdump.gpg.")
    aws = shutil.which("aws")
    if not aws:
        raise DrillError("The AWS CLI was not found.")
    # --checksum-mode ENABLED: the CLI checks the object's stored SHA-256 while downloading.
    _run([aws, "s3api", "get-object", "--bucket", bucket, "--key", key, "--checksum-mode", "ENABLED", "--output", "json", str(target)],
         what="aws s3api get-object", text=True)


class Container:
    """A disposable PostgreSQL with no network, reachable only through `docker exec`."""

    def __init__(self, docker: str):
        self.docker = docker
        self.name = f"travelobligator-restore-drill-{secrets.token_hex(4)}"
        self.started = False

    def start(self) -> None:
        _run([self.docker, "run", "--detach", "--rm", "--name", self.name, "--network", "none",
              "--tmpfs", "/var/lib/postgresql/data", "--tmpfs", "/drill:mode=0700,uid=999,gid=999",
              "--label", "travelobligator.restore-drill=true",
              # trust is safe only because nothing can connect from outside: there is no network.
              "-e", "POSTGRES_HOST_AUTH_METHOD=trust", "-e", f"POSTGRES_DB={DRILL_DATABASE}", POSTGRES_IMAGE],
             what="docker run", text=True)
        self.started = True
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            logs = subprocess.run([self.docker, "logs", self.name], capture_output=True, text=True)
            # The entrypoint first runs a temporary server to initialise, then restarts it: wait for the real one.
            if "PostgreSQL init process complete" in logs.stdout + logs.stderr:
                ready = subprocess.run([self.docker, "exec", self.name, "pg_isready", "-U", "postgres", "-d", DRILL_DATABASE], capture_output=True)
                if ready.returncode == 0:
                    return
            time.sleep(1)
        raise DrillError("The disposable PostgreSQL container did not become ready.")

    def exec(self, argv: list[str], *, what: str, stdin=subprocess.DEVNULL, text_input: str | None = None) -> str:
        if text_input is not None:
            return _run([self.docker, "exec", "-i", "-u", "postgres", self.name, *argv], what=what, stdin=None, input=text_input, text=True).stdout
        return _run([self.docker, "exec", "-i", "-u", "postgres", self.name, *argv], what=what, stdin=stdin, text=True).stdout

    def sql(self, query: str) -> str:
        return self.exec(["psql", "-X", "-At", "-v", "ON_ERROR_STOP=1", "-d", DRILL_DATABASE, "-c", query], what="psql").strip()

    def remove(self) -> None:
        if self.started:
            subprocess.run([self.docker, "rm", "--force", "--volumes", self.name], capture_output=True)


def restore_into(container: Container, dump: Path) -> None:
    with dump.open("rb") as handle:
        container.exec(["sh", "-c", "umask 077 && cat > /drill/backup.pgdump"], what="copy into container", stdin=handle)
    dump.unlink()  # the copy inside the container (tmpfs) is the only plaintext from here on
    # The archive holds schema `public` only and so begins with `CREATE SCHEMA public`, which a new database
    # already has: restore everything except that one entry (as backend/scripts/db_backup_restore.py does).
    listing = container.exec(["pg_restore", "--list", "/drill/backup.pgdump"], what="pg_restore --list")
    wanted = [line for line in listing.splitlines() if not (line and not line.startswith(";") and " SCHEMA - public " in f"{line} ")]
    container.exec(["sh", "-c", "umask 077 && cat > /drill/restore.list"], what="write restore list", text_input="\n".join(wanted) + "\n")
    container.exec(["pg_restore", "--no-owner", "--no-privileges", "--exit-on-error", "--single-transaction",
                    "--use-list=/drill/restore.list", "--dbname", DRILL_DATABASE, "/drill/backup.pgdump"], what="pg_restore")


def check_restored(container: Container, expect_revision: str | None) -> list[str]:
    """Schema and count checks. Prints names and counts only. Returns the problems found."""
    problems = []
    tables = container.sql("select tablename from pg_tables where schemaname = 'public' order by tablename").splitlines()
    missing = [table for table in CORE_TABLES if table not in tables]
    if missing:
        problems.append(f"expected tables missing: {', '.join(missing)}")
    revisions = container.sql("select version_num from alembic_version order by 1").splitlines() if "alembic_version" in tables else []
    _say(f"Alembic revision: {', '.join(revisions) or 'none'}")
    if len(revisions) != 1:
        problems.append("alembic_version must hold exactly one revision")
    elif expect_revision and revisions[0] != expect_revision:
        problems.append(f"revision is {revisions[0]}, expected {expect_revision}")
    _say("Row counts (no content is read):")
    for table in tables:
        count = container.sql(f'select count(*) from public."{table}"')
        _say(f"  {table}: {count}")
    reference_problems = []
    for description, needs, query in INTEGRITY_CHECKS:
        if all(table in tables for table in needs):
            count = int(container.sql(query))
            if count:
                reference_problems.append(f"{count} {description}")
    _say(f"Reference checks: {'FAILED' if reference_problems else 'passed'}.")
    return problems + reference_problems


def run(arguments: argparse.Namespace) -> int:
    gpg = check_gpg()
    docker = shutil.which("docker")
    if not docker:
        raise DrillError("Docker was not found.")
    _run([docker, "info"], what="docker info", text=True)

    work = Path(tempfile.mkdtemp(prefix="travelobligator-restore-drill-"))  # 0700, outside the repository
    if work == REPOSITORY_ROOT or REPOSITORY_ROOT in work.parents:
        shutil.rmtree(work, ignore_errors=True)
        raise DrillError("The temporary directory is inside the repository. Set TMPDIR to a private directory outside it.")
    home = work / "gnupg"
    home.mkdir(mode=0o700)
    container = Container(docker)
    try:
        if arguments.s3_key:
            encrypted = work / Path(arguments.s3_key).name
            _say(f"Downloading s3://{arguments.bucket}/{arguments.s3_key} (checksum verified) ...")
            download(arguments.bucket, arguments.s3_key, encrypted)
        else:
            encrypted = Path(arguments.file).expanduser().resolve()
            if not encrypted.is_file():
                raise DrillError("--file does not name a file.")
        passphrase = read_passphrase()

        _say("Decrypting and authenticating ...")
        dump = work / "backup.pgdump"
        decrypt(gpg, encrypted, dump, passphrase, home)
        _say(f"Decrypted and authenticated ({dump.stat().st_size} bytes).")

        _say("Starting a disposable PostgreSQL 16 (no network, data on tmpfs) ...")
        container.start()
        _say("Restoring (one transaction) ...")
        restore_into(container, dump)
        problems = check_restored(container, arguments.expect_revision)
        if problems:
            for problem in problems:
                _say(f"  PROBLEM: {problem}")
            _say("Drill FAILED. The disposable database is being removed.")
            return 1
        _say("Drill PASSED: decrypted, authenticated, restored and checked. The disposable database is being removed.")
        return 0
    finally:
        container.remove()
        gpgconf = shutil.which("gpgconf")
        if gpgconf:
            subprocess.run([gpgconf, "--homedir", str(home), "--kill", "gpg-agent"], capture_output=True)
        shutil.rmtree(work, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", help="an encrypted backup already downloaded (.pgdump.gpg)")
    source.add_argument("--s3-key", help="download this object first (neon/....pgdump.gpg)")
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--expect-revision", help="fail unless the restored Alembic revision is this one")
    arguments = parser.parse_args(argv)
    os.umask(0o077)

    def cancelled(signum, frame):
        raise _Cancelled()

    signal.signal(signal.SIGTERM, cancelled)
    try:
        return run(arguments)
    except DrillError as error:
        print(f"FAILED: {error}", file=sys.stderr, flush=True)
        return 1
    except (_Cancelled, KeyboardInterrupt):
        print("Cancelled; the container and temporary files were removed.", file=sys.stderr, flush=True)
        return 130
    except Exception as error:
        print(f"FAILED with an unexpected {type(error).__name__}.", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
