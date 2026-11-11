"""AWS Lambda entry point for the GitHub-independent backup schedule (scripts/backup/aws/README.md).

One container image, three functions. Each function's mode is fixed by its configuration
(BACKUP_FUNCTION_MODE), never taken from the event:

    backup    runs the unchanged scripts/backup/neon_s3_backup.py, with its two secrets read from SSM Parameter
              Store at invocation time (they are never part of the function's configuration)
    verify    downloads the newest encrypted object under neon/, decrypts and authenticates it with GnuPG, and
              reads its table of contents with pg_restore --list. No database is involved. The full restore drill
              stays the operator's monthly step (docs/30).
    monitor   the newest object under neon/ is at most MAX_BACKUP_AGE_HOURS old, and the public frontend page
              answers 200. It never contacts the backend: nothing here may keep the free-tier backend awake.

Any failure raises, so Lambda counts an error and the CloudWatch alarm notifies the owner by email.

Output is fixed wording, counts, sizes and ages. Never logged: the event, the environment, a secret, any part of
the connection string, an object key or an object's content. The plaintext archive (verify) exists only in a new
0700 directory under /tmp and is removed on every path. Because Lambda can reuse a sandbox after a timeout, every
invocation first removes what an earlier, killed invocation may have left behind.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The image keeps the repository layout: /opt/backup/neon_s3_backup.py and /opt/backup/aws/lambda_handler.py.
BACKUP_SCRIPT = HERE.parent / "neon_s3_backup.py"

PREFIX = "neon/"
OBJECT_PATTERN = re.compile(r"^neon/travelobligator-\d{8}T\d{6}Z-run[0-9a-z]+-\d+\.pgdump\.gpg$")
# The same allowance as scripts/monitoring/production_monitor.py: one daily interval plus 12 hours.
MAX_BACKUP_AGE_HOURS = 36.0
FRONTEND_TIMEOUT_SECONDS = 20.0
FRONTEND_RETRY_DELAY_SECONDS = 30.0
USER_AGENT = "travelobligator-aws-monitor"
# Time kept back from Lambda's own timeout, so the backup script can be stopped and clean up first.
SHUTDOWN_MARGIN_SECONDS = 60.0
STOP_GRACE_SECONDS = 30.0
LEFTOVER_PREFIXES = ("neon-backup-", "backup-verify-")

# What the backup script's process receives besides its two secrets. AWS_* carries the function's own temporary
# credentials and region; nothing else of the Lambda environment is passed on.
_PASSED_TO_BACKUP = ("PATH", "LANG", "LC_ALL", "BACKUP_S3_BUCKET", "BACKUP_S3_PREFIX", "BACKUP_EXPECTED_DB_USER", "PG_BIN_DIR")


class CheckFailed(Exception):
    """A failure with a fixed message that is safe to log."""


def _say(message: str) -> None:
    print(message, flush=True)


def _required(environment: dict[str, str], name: str) -> str:
    value = (environment.get(name) or "").strip()
    if not value:
        raise CheckFailed(f"{name} is not configured.")
    return value


def _backup_module():
    spec = importlib.util.spec_from_file_location("neon_s3_backup", BACKUP_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def remove_leftovers(directory: str | None = None) -> int:
    """Delete work directories an earlier invocation could not remove (a timeout kills without cleanup)."""
    root = Path(directory or tempfile.gettempdir())
    removed = 0
    for path in root.glob("*"):
        if path.is_dir() and path.name.startswith(LEFTOVER_PREFIXES):
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
    return removed


def _parameter(ssm, name: str) -> str:
    try:
        value = ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
    except Exception as error:  # the message may name the parameter's value in some SDK errors: type only
        raise CheckFailed(f"Reading a secret from Parameter Store failed ({type(error).__name__}).") from None
    if not value:
        raise CheckFailed("A secret in Parameter Store is empty.")
    return value


# -- backup --------------------------------------------------------------------------------------------


def backup_environment(environment: dict[str, str], database_url: str, passphrase: str) -> dict[str, str]:
    child = {name: environment[name] for name in _PASSED_TO_BACKUP if name in environment}
    child.update({name: value for name, value in environment.items() if name.startswith("AWS_")})
    child.update({"HOME": "/tmp", "TMPDIR": "/tmp", "AWS_PAGER": "",
                  "NEON_BACKUP_DATABASE_URL": database_url, "BACKUP_ENCRYPTION_PASSPHRASE": passphrase})
    return child


def run_backup(environment: dict[str, str], ssm, remaining_seconds: float, popen=subprocess.Popen) -> dict:
    database_url = _parameter(ssm, _required(environment, "DATABASE_URL_PARAMETER"))
    passphrase = _parameter(ssm, _required(environment, "PASSPHRASE_PARAMETER"))
    budget = remaining_seconds - SHUTDOWN_MARGIN_SECONDS
    if budget <= 0:
        raise CheckFailed("Not enough time left in this invocation to run a backup.")
    started = time.monotonic()
    # The script prints only its own sanitized lines; they go to the function's log as they are.
    process = popen([sys.executable, "-I", str(BACKUP_SCRIPT)], env=backup_environment(environment, database_url, passphrase),
                    stdin=subprocess.DEVNULL)
    try:
        code = process.wait(timeout=budget)
    except subprocess.TimeoutExpired:
        process.send_signal(signal.SIGTERM)  # the script removes its temporary files on SIGTERM
        try:
            process.wait(timeout=STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise CheckFailed("The backup did not finish in time and was stopped.") from None
    if code != 0:
        raise CheckFailed(f"The backup script failed (exit code {code}).")
    return {"duration_seconds": round(time.monotonic() - started)}


# -- newest object -------------------------------------------------------------------------------------


def newest_backup(s3, bucket: str) -> dict:
    newest = None
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=PREFIX):
        for item in page.get("Contents", []):
            if OBJECT_PATTERN.match(item["Key"]) and (newest is None or item["LastModified"] > newest["LastModified"]):
                newest = item
    if newest is None:
        raise CheckFailed("There is no backup under neon/.")
    return newest


def age_hours(last_modified: datetime, now: datetime) -> float:
    return (now - last_modified).total_seconds() / 3600.0


# -- verify --------------------------------------------------------------------------------------------


def run_verify(environment: dict[str, str], ssm, s3) -> dict:
    passphrase = _parameter(ssm, _required(environment, "PASSPHRASE_PARAMETER"))
    bucket = _required(environment, "BACKUP_S3_BUCKET")
    newest = newest_backup(s3, bucket)
    module = _backup_module()
    work = Path(tempfile.mkdtemp(prefix="backup-verify-"))  # 0700
    home = work / "gnupg"
    home.mkdir(mode=0o700)
    encrypted, archive = work / "object.pgdump.gpg", work / "archive.pgdump"
    try:
        s3.download_file(bucket, newest["Key"], str(encrypted))
        if encrypted.stat().st_size != newest["Size"]:
            raise CheckFailed("The downloaded object's size differs from the listing.")
        descriptor = module._passphrase_fd(passphrase)
        try:
            module._run(module._gpg_base(home, descriptor) + ["--output", str(archive), "--decrypt", str(encrypted)],
                        env=module._clean_environment(), what="gpg (decrypt)", pass_fds=(descriptor,))
        except module.BackupError:
            raise CheckFailed("The newest backup did not decrypt and authenticate.") from None
        finally:
            os.close(descriptor)
        try:
            facts = module.check_archive(archive)
        except module.BackupError as error:
            raise CheckFailed(f"The decrypted archive is not usable: {error}") from None
        return {"encrypted_bytes": newest["Size"], "tables_with_data": facts["tables_with_data"],
                "pg_dump_version": facts["pg_dump_version"],
                "age_hours": round(age_hours(newest["LastModified"], datetime.now(timezone.utc)), 1)}
    finally:
        module._stop_agent(home)
        shutil.rmtree(work, ignore_errors=True)


# -- monitor -------------------------------------------------------------------------------------------


def frontend_status(url: str, opener=urllib.request.urlopen) -> int:
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": USER_AGENT})
    try:
        with opener(request, timeout=FRONTEND_TIMEOUT_SECONDS) as response:
            return int(response.status)
    except urllib.error.HTTPError as error:
        return int(error.code)
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0


def run_monitor(environment: dict[str, str], s3, now: datetime | None = None, opener=urllib.request.urlopen,
                sleep=time.sleep) -> dict:
    now = now or datetime.now(timezone.utc)
    bucket = _required(environment, "BACKUP_S3_BUCKET")
    frontend = _required(environment, "FRONTEND_URL")
    if not frontend.startswith("https://"):
        raise CheckFailed("FRONTEND_URL must be an https:// address.")
    problems = []
    try:
        age = age_hours(newest_backup(s3, bucket)["LastModified"], now)
    except CheckFailed as error:
        age, problems = None, [str(error)]
    if age is not None and age > MAX_BACKUP_AGE_HOURS:
        problems.append(f"The newest backup is {age:.1f} hours old (limit {MAX_BACKUP_AGE_HOURS:.0f}).")
    status = frontend_status(frontend, opener)
    if status != 200:
        sleep(FRONTEND_RETRY_DELAY_SECONDS)  # one retry: a single failed request is not an outage
        status = frontend_status(frontend, opener)
    if status != 200:
        problems.append(f"The frontend page answered {status or 'nothing'} (expected 200).")
    if problems:
        raise CheckFailed(" ".join(problems))
    return {"backup_age_hours": round(age, 1), "frontend_status": status}


# -- entry point ---------------------------------------------------------------------------------------


def handler(event, context):  # the event is never read or logged
    os.umask(0o077)
    mode = os.environ.get("BACKUP_FUNCTION_MODE", "")
    import boto3  # in the image only; the tests pass stand-ins to the functions above

    try:
        removed = remove_leftovers()
        if removed:
            _say(f"Removed {removed} work directory(ies) left by an earlier invocation.")
        if mode == "backup":
            result = run_backup(dict(os.environ), boto3.client("ssm"), context.get_remaining_time_in_millis() / 1000.0)
        elif mode == "verify":
            result = run_verify(dict(os.environ), boto3.client("ssm"), boto3.client("s3"))
        elif mode == "monitor":
            result = run_monitor(dict(os.environ), boto3.client("s3"))
        else:
            raise CheckFailed("BACKUP_FUNCTION_MODE must be backup, verify or monitor.")
    except CheckFailed as error:
        _say(f"{mode or 'unknown'} FAILED: {error}")
        raise RuntimeError(f"{mode or 'unknown'} failed") from None
    except Exception as error:  # the message is not shown: it may quote an input
        _say(f"{mode or 'unknown'} FAILED with an unexpected {type(error).__name__}.")
        raise RuntimeError(f"{mode or 'unknown'} failed") from None
    _say(f"{mode} ok: " + ", ".join(f"{key}={value}" for key, value in result.items()))
    return {"status": "ok", **result}
