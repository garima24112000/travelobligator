#!/usr/bin/env python3
"""Encrypted backup of the production Neon database to Amazon S3, run by `.github/workflows/neon-backup.yml`.

    pg_dump (custom format) -> check the archive -> encrypt with GnuPG (symmetric, AES-256 in OCB mode,
    an authenticated encryption mode) -> decrypt it again and compare with the plaintext's SHA-256 ->
    delete the plaintext -> S3 PutObject with a SHA-256 checksum, never overwriting -> HeadObject: the
    stored object's size and SHA-256 match what was sent

Design, privileges, restore and the recovery drill: docs/30_automated_neon_backups.md.

Standard library only: the job that holds the database credential installs no Python package.

Inputs (environment):
    NEON_BACKUP_DATABASE_URL       secret: Neon DIRECT connection string of the read-only backup role
    BACKUP_ENCRYPTION_PASSPHRASE   secret: the symmetric passphrase
    BACKUP_EXPECTED_DB_USER        the role the URL must name (so an owner URL put in the secret by mistake
                                   is refused instead of used)
    BACKUP_S3_BUCKET               the bucket
    BACKUP_S3_PREFIX               must be exactly "neon/" (the only prefix the IAM role may write)
    PG_BIN_DIR                     directory holding pg_dump / pg_restore (default: found on PATH)
    GITHUB_RUN_ID, GITHUB_RUN_ATTEMPT   make the object name unique (optional outside Actions)

Safety contract:
  * Never printed or written anywhere: the connection string or any part of it, the passphrase, the dump's
    content. pg_dump receives the connection through libpq environment variables (never a command line);
    its error output is filtered and has the host, user, database and password blanked. An unforeseen
    error is reported by type only, because a library's message may quote its input.
  * The plaintext dump exists only in a new 0700 directory, only until it has been encrypted and the
    encrypted file has been proven to decrypt to it; it is deleted before anything is uploaded, and the
    whole directory is deleted on success, failure and cancellation (SIGTERM).
  * TLS is always `verify-full` against the system's trusted roots, whatever the URL says.
  * Only the encrypted file is uploaded, under a new timestamped name; `If-None-Match: *` means an
    existing object is never overwritten.
  * Any failure exits non-zero, so the workflow run fails.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

URL_ENV = "NEON_BACKUP_DATABASE_URL"
PASSPHRASE_ENV = "BACKUP_ENCRYPTION_PASSPHRASE"
SECRET_ENVS = (URL_ENV, PASSPHRASE_ENV)
REQUIRED_PREFIX = "neon/"
MIN_PASSPHRASE_LENGTH = 24
MAX_PASSPHRASE_LENGTH = 1024
# pg_dump takes an ACCESS SHARE lock on every table; only a migration's ACCESS EXCLUSIVE lock conflicts with
# it. Give up instead of waiting behind a migration indefinitely.
LOCK_WAIT_TIMEOUT = "120s"
CONNECT_TIMEOUT_SECONDS = "30"

# URL query options accepted, and the libpq variable each is passed on as. Any other option is refused: it
# would be dropped silently, and could matter (a second host, a service file ...). `sslmode` is accepted
# only so that a Neon URL as copied from the console is valid; it is always replaced by verify-full.
_ACCEPTED_SSLMODES = {"require", "verify-ca", "verify-full"}
_PASSED_ON_OPTIONS = {"channel_binding": "PGCHANNELBINDING", "options": "PGOPTIONS"}

# GnuPG symmetric encryption, one fixed reviewed invocation (docs/30, "Encryption format"):
#   --force-ocb        AEAD packet (AES-256-OCB, chunked, every chunk and the end authenticated). Decrypting
#                      needs GnuPG >= 2.3, which reports a damaged, truncated or altered file and exits 2.
#   --s2k-*            passphrase -> key: salted, iterated SHA-512 at GnuPG's maximum iteration count.
#   --compress-algo    none: a custom-format dump is already compressed.
GPG_ENCRYPT_ARGS = (
    "--symmetric",
    "--force-ocb",
    "--cipher-algo", "AES256",
    "--s2k-mode", "3",
    "--s2k-digest-algo", "SHA512",
    "--s2k-count", "65011712",
    "--compress-algo", "none",
)


class BackupError(Exception):
    """A refusal or failure, with a fixed message that is safe to print."""


class _Cancelled(BaseException):
    """SIGTERM (a cancelled workflow run): unwind so every `finally` cleans up."""


def _say(message: str) -> None:
    print(message, flush=True)


# -- inputs --------------------------------------------------------------------------------------------


def connection_environment(raw_url: str, expected_user: str) -> tuple[dict[str, str], list[str]]:
    """The connection as libpq variables, and every part of it that must never be shown (longest first)."""
    raw_url = (raw_url or "").strip()
    if not raw_url:
        raise BackupError(f"{URL_ENV} is not set.")
    try:
        parts = urlsplit(raw_url)
        host = parts.hostname or ""
        port = parts.port
        user = unquote(parts.username or "")
        password = unquote(parts.password or "")
        database = unquote(parts.path.lstrip("/"))
        query = parse_qsl(parts.query, keep_blank_values=True)
    except ValueError:
        raise BackupError(f"{URL_ENV} is not a usable PostgreSQL URL.") from None
    if parts.scheme not in {"postgresql", "postgres"} or not host or not user or not database or "/" in database:
        raise BackupError(f"{URL_ENV} is not a usable PostgreSQL URL (scheme, host, user and database are required).")
    if "," in parts.netloc:
        raise BackupError(f"{URL_ENV} names more than one host. Use the single direct endpoint.")
    if host.split(".", 1)[0].endswith("-pooler"):
        raise BackupError(f"{URL_ENV} points at Neon's pooled (-pooler) endpoint. pg_dump needs the DIRECT endpoint.")
    if expected_user and user != expected_user:
        raise BackupError(f"{URL_ENV} does not use the expected backup role (BACKUP_EXPECTED_DB_USER). Refusing to dump with another role.")
    names = [name for name, _ in query]
    if len(names) != len(set(names)):
        raise BackupError(f"{URL_ENV} repeats a connection option.")
    unknown = sorted(set(names) - set(_PASSED_ON_OPTIONS) - {"sslmode"})
    if unknown:
        raise BackupError(f"{URL_ENV} uses a connection option this tool does not pass on. Allowed: channel_binding, options, sslmode.")
    options = dict(query)
    if "sslmode" in options and options["sslmode"] not in _ACCEPTED_SSLMODES:
        raise BackupError(f"{URL_ENV} asks for a connection that may be unencrypted (sslmode). Only TLS connections are allowed.")

    environment = _clean_environment()
    environment.update(
        {
            "PGHOST": host,
            "PGUSER": user,
            "PGDATABASE": database,
            # libpq 16+: verify the server certificate and host name against the system's trusted roots.
            "PGSSLMODE": "verify-full",
            "PGSSLROOTCERT": "system",
            "PGCONNECT_TIMEOUT": CONNECT_TIMEOUT_SECONDS,
            "PGAPPNAME": "travelobligator-neon-backup",
        }
    )
    if port:
        environment["PGPORT"] = str(port)
    if password:
        environment["PGPASSWORD"] = password
    for option, variable in _PASSED_ON_OPTIONS.items():
        if options.get(option):
            environment[variable] = options[option]
    secrets = sorted({part for part in (password, user, host, database) if len(part) >= 3}, key=len, reverse=True)
    return environment, secrets


def checked_passphrase(raw: str | None) -> str:
    """The passphrase as GnuPG will read it (one line). A single trailing newline, as a pasted secret often
    has, is dropped; anything else ambiguous is refused rather than silently changed."""
    passphrase = (raw or "").removesuffix("\n").removesuffix("\r")
    if not passphrase:
        raise BackupError(f"{PASSPHRASE_ENV} is not set.")
    if "\n" in passphrase or "\r" in passphrase or "\0" in passphrase:
        raise BackupError(f"{PASSPHRASE_ENV} contains a line break or NUL; GnuPG reads one line. Use a single-line passphrase.")
    if passphrase != passphrase.strip():
        raise BackupError(f"{PASSPHRASE_ENV} starts or ends with whitespace. Remove it so the passphrase can be typed back reliably.")
    if not MIN_PASSPHRASE_LENGTH <= len(passphrase) <= MAX_PASSPHRASE_LENGTH:
        raise BackupError(f"{PASSPHRASE_ENV} must be {MIN_PASSPHRASE_LENGTH} to {MAX_PASSPHRASE_LENGTH} characters long (generate one: `openssl rand -base64 32`).")
    return passphrase


def object_key(prefix: str, now: datetime, run_id: str, attempt: str) -> str:
    if prefix != REQUIRED_PREFIX:
        raise BackupError(f"BACKUP_S3_PREFIX must be exactly {REQUIRED_PREFIX!r}.")
    run_id = run_id or "local"
    attempt = attempt or "1"
    if not re.fullmatch(r"[0-9]+|local", run_id) or not re.fullmatch(r"[0-9]+", attempt):
        raise BackupError("GITHUB_RUN_ID / GITHUB_RUN_ATTEMPT are not numbers.")
    return f"{prefix}travelobligator-{now.strftime('%Y%m%dT%H%M%SZ')}-run{run_id}-{attempt}.pgdump.gpg"


def _clean_environment() -> dict[str, str]:
    """This process's environment without the two secrets and without any inherited libpq setting."""
    return {key: value for key, value in os.environ.items() if key not in SECRET_ENVS and not key.startswith("PG")}


def _tool(name: str) -> str:
    directory = os.environ.get("PG_BIN_DIR") if name in {"pg_dump", "pg_restore"} else None
    path = str(Path(directory) / name) if directory else shutil.which(name)
    if not path or not os.access(path, os.X_OK):
        raise BackupError(f"{name} was not found{' in PG_BIN_DIR' if directory else ' on PATH'}.")
    return path


# -- running tools -------------------------------------------------------------------------------------


def _run(argv: list[str], *, env: dict[str, str], what: str, secrets: list[str] = (), **kwargs) -> subprocess.CompletedProcess:
    completed = subprocess.run(argv, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, **kwargs)
    if completed.returncode != 0:
        for line in _safe_lines(completed.stderr, secrets):
            print(f"  {what}: {line}", file=sys.stderr, flush=True)
        raise BackupError(f"{what} failed (exit code {completed.returncode}).")
    return completed


def _safe_lines(stderr: str, secrets: list[str], keep: int = 8) -> list[str]:
    """A tool's error output minus every line that could carry connection detail, with the connection's own
    host, user, database and password blanked wherever else they appear. Of a libpq connection failure only
    the reason is kept (`connection to server at "<host>" (<address>), port N failed: <reason>`)."""
    safe = []
    for line in (stderr or "").splitlines():
        if "connection to server" in line:
            reason = line.rsplit("failed:", 1)[1].strip() if "failed:" in line else ""
            if not reason:
                continue
            line = f"connection failed: {reason}"
        if "://" in line or "password" in line.lower():
            continue
        for secret in secrets:
            line = line.replace(secret, "[hidden]")
        safe.append(line)
    return safe[-keep:]


def _passphrase_fd(passphrase: str) -> int:
    """A pipe holding the passphrase, for gpg's --passphrase-fd: never an argument, file or variable of gpg."""
    read_end, write_end = os.pipe()
    try:
        os.write(write_end, passphrase.encode() + b"\n")  # far below a pipe's buffer (MAX_PASSPHRASE_LENGTH)
    finally:
        os.close(write_end)
    return read_end


def _gpg_base(home: Path, passphrase_fd: int) -> list[str]:
    return [
        _tool("gpg"), "--batch", "--no-tty", "--quiet", "--homedir", str(home),
        "--no-symkey-cache", "--pinentry-mode", "loopback", "--passphrase-fd", str(passphrase_fd),
    ]


def encrypt(source: Path, target: Path, passphrase: str, home: Path) -> None:
    descriptor = _passphrase_fd(passphrase)
    try:
        _run(_gpg_base(home, descriptor) + [*GPG_ENCRYPT_ARGS, "--output", str(target), str(source)],
             env=_clean_environment(), what="gpg (encrypt)", pass_fds=(descriptor,))
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    finally:
        os.close(descriptor)


def decrypted_digest(source: Path, passphrase: str, home: Path, scratch: Path) -> tuple[str, int]:
    """SHA-256 and size of what `source` decrypts to, streamed: the plaintext is never written again. Raises
    unless GnuPG decrypts AND authenticates the whole file."""
    descriptor = _passphrase_fd(passphrase)
    digest, size = hashlib.sha256(), 0
    try:
        with tempfile.TemporaryFile(dir=scratch) as errors:
            process = subprocess.Popen(_gpg_base(home, descriptor) + ["--decrypt", str(source)], env=_clean_environment(),
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors, pass_fds=(descriptor,))
            for chunk in iter(lambda: process.stdout.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
            process.stdout.close()
            if process.wait() != 0:
                errors.seek(0)
                for line in _safe_lines(errors.read().decode(errors="replace"), []):
                    print(f"  gpg (verify): {line}", file=sys.stderr, flush=True)
                raise BackupError("The encrypted file did not decrypt and authenticate. Nothing was uploaded.")
    finally:
        os.close(descriptor)
    return digest.hexdigest(), size


def _stop_agent(home: Path) -> None:
    gpgconf = shutil.which("gpgconf")
    if gpgconf and home.exists():
        subprocess.run([gpgconf, "--homedir", str(home), "--kill", "gpg-agent"], env=_clean_environment(),
                       stdin=subprocess.DEVNULL, capture_output=True, check=False)


def file_sha256(path: Path) -> bytes:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.digest()


# -- the backup ----------------------------------------------------------------------------------------


def dump_database(target: Path, environment: dict[str, str], secrets: list[str]) -> None:
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    # Schema `public` only, without owners or privileges, so it restores under any role -- the same archive
    # shape as backend/scripts/db_backup_restore.py. One REPEATABLE READ snapshot (pg_dump's own).
    _run([_tool("pg_dump"), "--format=custom", "--no-owner", "--no-privileges", "--schema=public", "--no-password",
          f"--lock-wait-timeout={LOCK_WAIT_TIMEOUT}", f"--file={target}"],
         env=environment, what="pg_dump", secrets=secrets)
    if target.stat().st_size == 0:
        raise BackupError("pg_dump reported success but wrote an empty file.")


def check_archive(dump: Path) -> dict[str, str | int]:
    """Read the archive's table of contents (no connection). Proves the file is a complete, readable
    custom-format archive of an application database. Returns non-secret facts only."""
    listing = _run([_tool("pg_restore"), "--list", str(dump)], env=_clean_environment(), what="pg_restore --list").stdout
    entries = [line for line in listing.splitlines() if line.strip() and not line.startswith(";")]
    data_entries = [line for line in entries if " TABLE DATA public " in line]
    if not any(" TABLE DATA public alembic_version " in f"{line} " for line in data_entries):
        raise BackupError("The archive holds no alembic_version table. This is not the application database; nothing was uploaded.")

    def header(label: str) -> str:
        match = re.search(rf"^;\s+{re.escape(label)}:\s*(\S+)", listing, re.MULTILINE)
        return match.group(1) if match else "unknown"

    return {"entries": len(entries), "tables_with_data": len(data_entries),
            "server_version": header("Dumped from database version"), "pg_dump_version": header("Dumped by pg_dump version")}


def upload(encrypted: Path, bucket: str, key: str) -> dict[str, str | int]:
    size = encrypted.stat().st_size
    checksum = base64.b64encode(file_sha256(encrypted)).decode()
    environment = {**_clean_environment(), "AWS_PAGER": ""}
    aws = _tool("aws")
    put = _run([aws, "s3api", "put-object", "--bucket", bucket, "--key", key, "--body", str(encrypted),
                "--content-type", "application/octet-stream", "--checksum-algorithm", "SHA256",
                "--checksum-sha256", checksum, "--if-none-match", "*", "--output", "json"],
               env=environment, what="aws s3api put-object")
    if _json(put.stdout).get("ChecksumSHA256") != checksum:
        raise BackupError("S3 did not confirm the SHA-256 checksum of the upload.")
    head = _json(_run([aws, "s3api", "head-object", "--bucket", bucket, "--key", key, "--checksum-mode", "ENABLED",
                       "--output", "json"], env=environment, what="aws s3api head-object").stdout)
    if head.get("ContentLength") != size or head.get("ChecksumSHA256") != checksum:
        raise BackupError("The stored object's size or SHA-256 checksum differs from the encrypted file.")
    return {"bytes": size, "sha256": base64.b64decode(checksum).hex(), "server_side_encryption": str(head.get("ServerSideEncryption", "not reported"))}


def _json(text: str) -> dict:
    try:
        value = json.loads(text or "{}")
    except ValueError:
        raise BackupError("The AWS CLI did not return JSON.") from None
    return value if isinstance(value, dict) else {}


def run_backup(now: datetime | None = None) -> str:
    started = time.monotonic()
    now = now or datetime.now(timezone.utc)
    environment, secrets = connection_environment(os.environ.get(URL_ENV, ""), os.environ.get("BACKUP_EXPECTED_DB_USER", "").strip())
    passphrase = checked_passphrase(os.environ.get(PASSPHRASE_ENV))
    bucket = os.environ.get("BACKUP_S3_BUCKET", "").strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
        raise BackupError("BACKUP_S3_BUCKET is not set to a bucket name.")
    key = object_key(os.environ.get("BACKUP_S3_PREFIX", ""), now, os.environ.get("GITHUB_RUN_ID", ""), os.environ.get("GITHUB_RUN_ATTEMPT", ""))
    if os.environ.get("GITHUB_ACTIONS") == "true":
        # The whole secret is masked by GitHub already; its parts are not.
        for part in [*secrets, passphrase]:
            print(f"::add-mask::{part}", flush=True)
    for name in ("pg_dump", "pg_restore", "gpg", "aws"):
        _tool(name)

    work = Path(tempfile.mkdtemp(prefix="neon-backup-", dir=os.environ.get("RUNNER_TEMP") or None))  # 0700
    home = work / "gnupg"
    home.mkdir(mode=0o700)
    dump, encrypted = work / "backup.pgdump", work / "backup.pgdump.gpg"
    try:
        _say("Dumping the database (pg_dump, custom format) ...")
        dump_database(dump, environment, secrets)
        facts = check_archive(dump)
        _say(f"Archive readable: {facts['tables_with_data']} tables with data; server {facts['server_version']}, pg_dump {facts['pg_dump_version']}.")
        plaintext_sha256, plaintext_bytes = file_sha256(dump).hex(), dump.stat().st_size

        _say("Encrypting (GnuPG, AES-256-OCB) and proving the encrypted file decrypts to the dump ...")
        encrypt(dump, encrypted, passphrase, home)
        if decrypted_digest(encrypted, passphrase, home, work) != (plaintext_sha256, plaintext_bytes):
            raise BackupError("The encrypted file decrypts to something other than the dump. Nothing was uploaded.")
        dump.unlink()  # the plaintext never outlives its encrypted, verified copy

        _say(f"Uploading s3://{bucket}/{key} ...")
        stored = upload(encrypted, bucket, key)
        elapsed = time.monotonic() - started
        _say(f"Backup stored and verified: {key}, {stored['bytes']} bytes, SHA-256 {stored['sha256']}, "
             f"server-side encryption {stored['server_side_encryption']}, {elapsed:.0f} s.")
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as handle:
                handle.write(f"### Neon backup stored\n\n| | |\n|---|---|\n| Object | `{key}` |\n| Encrypted size | {stored['bytes']} bytes |\n"
                             f"| SHA-256 (encrypted object) | `{stored['sha256']}` |\n| Server / pg_dump | {facts['server_version']} / {facts['pg_dump_version']} |\n"
                             f"| Tables with data | {facts['tables_with_data']} |\n| Duration | {elapsed:.0f} s |\n")
        return key
    finally:
        _stop_agent(home)
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    os.umask(0o077)

    def cancelled(signum, frame):
        raise _Cancelled()

    signal.signal(signal.SIGTERM, cancelled)
    try:
        run_backup()
        return 0
    except BackupError as error:
        print(f"::error::Backup FAILED: {error}", flush=True)
        return 1
    except (_Cancelled, KeyboardInterrupt):  # a cancelled run gets SIGINT, then SIGTERM
        print("::error::Backup cancelled; temporary files were removed and nothing further was uploaded.", flush=True)
        return 143
    except Exception as error:  # the message is not shown: it may quote a secret input
        print(f"::error::Backup FAILED with an unexpected {type(error).__name__}.", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
