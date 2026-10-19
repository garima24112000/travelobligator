from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

# Automated Neon -> S3 backup (docs/30_automated_neon_backups.md), hermetic: the real script and the real
# GnuPG, with stand-ins for pg_dump, pg_restore and the AWS CLI. No database, no network, no AWS account.

_ROOT = Path(__file__).resolve().parents[4]
_SCRIPT = _ROOT / "scripts" / "backup" / "neon_s3_backup.py"
_DRILL = _ROOT / "scripts" / "backup" / "restore_drill.py"
_WORKFLOW = (_ROOT / ".github" / "workflows" / "neon-backup.yml").read_text()

PASSWORD = "Pa55-word-never-shown"
HOST = "ep-quiet-lake-123456.us-east-1.aws.neon.tech"
USER = "travelobligator_backup"
DATABASE = "neondb"
PASSPHRASE = "test-only-passphrase-0123456789abcdef"
URL = f"postgresql://{USER}:{PASSWORD}@{HOST}/{DATABASE}?sslmode=require&channel_binding=require"
DUMP_CONTENT = b"PGDMP fake archive " + bytes(range(256)) * 64
SECRETS = (PASSWORD, HOST, USER, DATABASE, PASSPHRASE, URL)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backup = _load(_SCRIPT, "neon_s3_backup")
drill = _load(_DRILL, "restore_drill")

_FAKE_PG_DUMP = """
import json, os, sys
mode = os.environ.get("FAKE_PG_DUMP_MODE", "ok")
record = {"argv": sys.argv[1:], "env": {k: v for k, v in os.environ.items()}}
open(os.environ["FAKE_RECORD_DIR"] + "/pg_dump.json", "w").write(json.dumps(record))
target = next(a.split("=", 1)[1] for a in sys.argv if a.startswith("--file="))
if mode == "fail":
    sys.stderr.write("pg_dump: error: connection to server at \\"%s\\" (10.1.2.3), port 5432 failed: SSL error: certificate verify failed\\n" % os.environ["PGHOST"])
    sys.stderr.write("pg_dump: error: connection to server at \\"%s\\" (10.1.2.3), port 5432 failed: FATAL:  password authentication failed for user \\"%s\\"\\n" % (os.environ["PGHOST"], os.environ["PGUSER"]))
    sys.stderr.write("pg_dump: detail: role %s on %s refused\\n" % (os.environ["PGUSER"], os.environ["PGDATABASE"]))
    sys.exit(1)
if mode != "empty":
    open(target, "wb").write(bytes.fromhex(os.environ["FAKE_DUMP_HEX"]))
"""

_FAKE_PG_RESTORE = """
import os, sys
assert sys.argv[1] == "--list"
print(";\\n; Archive created at 2026-10-19 03:37:00 UTC\\n;     dbname: neondb\\n;     Dumped from database version: 16.9\\n;     Dumped by pg_dump version: 16.15\\n;")
print("215; 1259 16390 TABLE public alembic_version neondb_owner")
print("216; 1259 16395 TABLE public users neondb_owner")
if os.environ.get("FAKE_TOC_MODE") != "no_alembic":
    print("3301; 0 16390 TABLE DATA public alembic_version neondb_owner")
print("3302; 0 16395 TABLE DATA public users neondb_owner")
"""

# put-object / head-object against a local directory, with S3's checksum behaviour.
_FAKE_AWS = """
import base64, hashlib, json, os, sys
args = sys.argv[1:]
store = os.environ["FAKE_S3_DIR"]
with open(os.environ["FAKE_RECORD_DIR"] + "/aws.log", "a") as log:
    log.write(json.dumps({"argv": args, "has_secret_env": any(k in os.environ for k in ("NEON_BACKUP_DATABASE_URL", "BACKUP_ENCRYPTION_PASSPHRASE"))}) + "\\n")
def opt(name):
    return args[args.index(name) + 1]
path = os.path.join(store, opt("--bucket"), opt("--key"))
if args[:2] == ["s3api", "put-object"]:
    body = open(opt("--body"), "rb").read()
    checksum = base64.b64encode(hashlib.sha256(body).digest()).decode()
    if checksum != opt("--checksum-sha256"):
        sys.stderr.write("BadDigest\\n"); sys.exit(255)
    if opt("--if-none-match") == "*" and os.path.exists(path):
        sys.stderr.write("PreconditionFailed\\n"); sys.exit(255)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.environ.get("FAKE_S3_MODE") == "corrupt_after_put":
        body = body[:-1] + bytes([body[-1] ^ 1])
    open(path, "wb").write(body)
    print(json.dumps({"ETag": "x", "ChecksumSHA256": checksum}))
elif args[:2] == ["s3api", "head-object"]:
    body = open(path, "rb").read()
    print(json.dumps({"ContentLength": len(body), "ChecksumSHA256": base64.b64encode(hashlib.sha256(body).digest()).decode(), "ServerSideEncryption": "AES256"}))
else:
    sys.exit(2)
"""


def _stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body))
    path.chmod(0o755)


@pytest.fixture
def harness():
    # gpg-agent's socket lives in the GnuPG home inside the work directory: keep the path short.
    base = Path(tempfile.mkdtemp(prefix="nb", dir="/tmp"))
    try:
        paths = {name: base / name for name in ("bin", "pgbin", "record", "s3", "runner")}
        for path in paths.values():
            path.mkdir()
        _stub(paths["pgbin"], "pg_dump", _FAKE_PG_DUMP)
        _stub(paths["pgbin"], "pg_restore", _FAKE_PG_RESTORE)
        _stub(paths["bin"], "aws", _FAKE_AWS)
        environment = {
            "PATH": f"{paths['bin']}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(base),
            "NEON_BACKUP_DATABASE_URL": URL,
            "BACKUP_ENCRYPTION_PASSPHRASE": PASSPHRASE,
            "BACKUP_EXPECTED_DB_USER": USER,
            "BACKUP_S3_BUCKET": "travelobligator-db-backups-10192026",
            "BACKUP_S3_PREFIX": "neon/",
            "PG_BIN_DIR": str(paths["pgbin"]),
            "RUNNER_TEMP": str(paths["runner"]),
            "GITHUB_RUN_ID": "987654321",
            "GITHUB_RUN_ATTEMPT": "2",
            "FAKE_RECORD_DIR": str(paths["record"]),
            "FAKE_S3_DIR": str(paths["s3"]),
            "FAKE_DUMP_HEX": DUMP_CONTENT.hex(),
            "PGPASSWORD": "inherited-must-not-leak",  # an inherited libpq setting is never passed on
        }
        yield paths, environment
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _run(environment: dict[str, str], **overrides: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(_SCRIPT)], env={**environment, **overrides}, capture_output=True, text=True, timeout=120)


def _stored(paths) -> list[Path]:
    return [path for path in paths["s3"].rglob("*") if path.is_file()]


def _gpg_decrypt(data_path: Path, passphrase: str, home: Path) -> subprocess.CompletedProcess:
    home.mkdir(mode=0o700, exist_ok=True)
    return subprocess.run(
        ["gpg", "--batch", "--no-tty", "--quiet", "--homedir", str(home), "--pinentry-mode", "loopback", "--passphrase-fd", "0", "--decrypt", str(data_path)],
        input=passphrase.encode() + b"\n", capture_output=True,
    )


def _assert_no_secret(text: str) -> None:
    for secret in SECRETS:
        assert secret not in text


# -- the whole run -------------------------------------------------------------------------------------


def test_backup_uploads_only_an_authenticated_encrypted_copy_and_leaves_nothing_behind(harness):
    paths, environment = harness
    completed = _run(environment)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    _assert_no_secret(completed.stdout + completed.stderr)

    [stored] = _stored(paths)
    key = stored.relative_to(paths["s3"] / "travelobligator-db-backups-10192026").as_posix()
    assert re.fullmatch(r"neon/travelobligator-\d{8}T\d{6}Z-run987654321-2\.pgdump\.gpg", key)
    ciphertext = stored.read_bytes()
    assert DUMP_CONTENT not in ciphertext and not ciphertext.startswith(b"PGDMP")

    decrypted = _gpg_decrypt(stored, PASSPHRASE, paths["record"] / "gnupg")
    assert decrypted.returncode == 0 and decrypted.stdout == DUMP_CONTENT
    assert _gpg_decrypt(stored, "a-different-passphrase-of-length", paths["record"] / "gnupg2").returncode != 0
    packets = subprocess.run(["gpg", "--list-packets", "--batch", "--homedir", str(paths["record"] / "gnupg"), "--pinentry-mode", "loopback",
                              "--passphrase-fd", "0", str(stored)], input=PASSPHRASE.encode() + b"\n", capture_output=True, text=False).stdout.decode()
    assert ":aead encrypted packet:" in packets and "cipher=9 aead=2" in packets  # AES-256, OCB

    assert list(paths["runner"].iterdir()) == []  # plaintext, ciphertext and GnuPG home all removed

    record = json.loads((paths["record"] / "pg_dump.json").read_text())
    _assert_no_secret(" ".join(record["argv"]))
    for flag in ("--format=custom", "--no-owner", "--no-privileges", "--schema=public", "--no-password"):
        assert flag in record["argv"]
    child = record["env"]
    assert "NEON_BACKUP_DATABASE_URL" not in child and "BACKUP_ENCRYPTION_PASSPHRASE" not in child
    assert (child["PGHOST"], child["PGUSER"], child["PGDATABASE"], child["PGPASSWORD"]) == (HOST, USER, DATABASE, PASSWORD)
    assert (child["PGSSLMODE"], child["PGSSLROOTCERT"], child["PGCHANNELBINDING"]) == ("verify-full", "system", "require")

    calls = [json.loads(line) for line in (paths["record"] / "aws.log").read_text().splitlines()]
    assert [call["argv"][:2] for call in calls] == [["s3api", "put-object"], ["s3api", "head-object"]]
    assert not any(call["has_secret_env"] for call in calls)
    put = calls[0]["argv"]
    assert put[put.index("--if-none-match") + 1] == "*"
    assert put[put.index("--checksum-sha256") + 1] == base64.b64encode(hashlib.sha256(ciphertext).digest()).decode()


def test_a_stored_object_that_differs_from_the_upload_fails_the_run(harness):
    paths, environment = harness
    completed = _run(environment, FAKE_S3_MODE="corrupt_after_put")
    assert completed.returncode == 1
    assert "size or SHA-256 checksum differs" in completed.stdout
    assert list(paths["runner"].iterdir()) == []


def test_an_existing_object_is_never_overwritten(harness, monkeypatch):
    paths, environment = harness
    assert _run(environment).returncode == 0
    [stored] = _stored(paths)
    before = stored.read_bytes()
    # A second upload under the same name (the conditional write is what protects it).
    key = stored.relative_to(paths["s3"] / "travelobligator-db-backups-10192026").as_posix()
    other = paths["record"] / "other.gpg"
    other.write_bytes(b"different")
    for name in ("PATH", "FAKE_S3_DIR", "FAKE_RECORD_DIR"):
        monkeypatch.setenv(name, environment[name])
    with pytest.raises(backup.BackupError, match="put-object failed"):
        backup.upload(other, "travelobligator-db-backups-10192026", key)
    assert stored.read_bytes() == before


def test_pg_dump_failure_fails_the_run_without_upload_and_without_showing_the_connection(harness):
    paths, environment = harness
    completed = _run(environment, FAKE_PG_DUMP_MODE="fail")
    assert completed.returncode == 1
    assert "pg_dump failed (exit code 1)" in completed.stdout
    _assert_no_secret(completed.stdout + completed.stderr)
    assert "[hidden]" in completed.stderr  # the line that named role and database was kept, blanked
    assert "connection failed: SSL error: certificate verify failed" in completed.stderr  # the reason, not the address
    assert "10.1.2.3" not in completed.stderr and "authentication failed" not in completed.stderr
    assert _stored(paths) == [] and not (paths["record"] / "aws.log").exists()
    assert list(paths["runner"].iterdir()) == []


@pytest.mark.parametrize(("overrides", "message"), [
    ({"FAKE_PG_DUMP_MODE": "empty"}, "wrote an empty file"),
    ({"FAKE_TOC_MODE": "no_alembic"}, "no alembic_version table"),
])
def test_an_empty_or_foreign_archive_is_never_uploaded(harness, overrides, message):
    paths, environment = harness
    completed = _run(environment, **overrides)
    assert completed.returncode == 1 and message in completed.stdout
    assert _stored(paths) == [] and list(paths["runner"].iterdir()) == []


def test_missing_tools_or_inputs_fail_before_anything_is_dumped(harness):
    paths, environment = harness
    for overrides in ({"NEON_BACKUP_DATABASE_URL": ""}, {"BACKUP_ENCRYPTION_PASSPHRASE": ""}, {"BACKUP_S3_PREFIX": "other/"},
                      {"BACKUP_S3_BUCKET": ""}, {"PG_BIN_DIR": str(paths["record"])}):
        completed = _run(environment, **overrides)
        assert completed.returncode == 1, overrides
        assert not (paths["record"] / "pg_dump.json").exists()
        _assert_no_secret(completed.stdout + completed.stderr)


# -- inputs --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("url", "message"), [
    (URL.replace(HOST, "ep-quiet-lake-123456-pooler.us-east-1.aws.neon.tech"), "pooled"),
    (URL.replace(USER, "neondb_owner"), "expected backup role"),
    (URL.replace("sslmode=require", "sslmode=disable"), "unencrypted"),
    (URL.replace("sslmode=require", "sslmode=prefer"), "unencrypted"),
    (URL + "&host=elsewhere", "does not pass on"),
    (URL + "&sslmode=require", "repeats"),
    (URL.replace(HOST, HOST + ",other.example"), "more than one host"),
    (URL.replace(HOST, HOST + ":notaport"), "not a usable"),
    ("mysql://u:p@h/db", "not a usable"),
    (f"postgresql://{USER}:{PASSWORD}@{HOST}/", "not a usable"),
])
def test_unsafe_or_unexpected_urls_are_refused_without_echoing_them(url, message):
    with pytest.raises(backup.BackupError, match=message) as refusal:
        backup.connection_environment(url, USER)
    _assert_no_secret(str(refusal.value))


def test_tls_is_always_verify_full_and_inherited_libpq_settings_are_dropped(monkeypatch):
    monkeypatch.setenv("PGSSLMODE", "disable")
    monkeypatch.setenv("PGSERVICEFILE", "/elsewhere")
    environment, secrets = backup.connection_environment(URL.replace("&channel_binding=require", "").replace("sslmode=require", ""), USER)
    assert environment["PGSSLMODE"] == "verify-full" and environment["PGSSLROOTCERT"] == "system"
    assert "PGSERVICEFILE" not in environment and "PGCHANNELBINDING" not in environment
    assert secrets[0] == HOST and set(secrets) == {PASSWORD, HOST, USER, DATABASE}


def test_percent_encoded_credentials_are_decoded():
    environment, _ = backup.connection_environment(f"postgresql://{USER}:p%40ss%2Fw0rd@{HOST}:5432/{DATABASE}", USER)
    assert environment["PGPASSWORD"] == "p@ss/w0rd" and environment["PGPORT"] == "5432"


@pytest.mark.parametrize("raw", ["", "short", " " + PASSPHRASE, PASSPHRASE + " ", PASSPHRASE + "\nsecond", "x" * 1025])
def test_ambiguous_or_weak_passphrases_are_refused(raw):
    with pytest.raises(backup.BackupError) as refusal:
        backup.checked_passphrase(raw)
    assert PASSPHRASE not in str(refusal.value)


def test_a_single_trailing_newline_of_a_pasted_secret_is_dropped():
    assert backup.checked_passphrase(PASSPHRASE + "\n") == PASSPHRASE
    assert backup.checked_passphrase(PASSPHRASE + "\r\n") == PASSPHRASE


def test_object_names_stay_under_the_one_permitted_prefix():
    from datetime import datetime, timezone

    now = datetime(2026, 10, 19, 3, 37, 5, tzinfo=timezone.utc)
    assert backup.object_key("neon/", now, "42", "1") == "neon/travelobligator-20261019T033705Z-run42-1.pgdump.gpg"
    assert backup.object_key("neon/", now, "", "") == "neon/travelobligator-20261019T033705Z-runlocal-1.pgdump.gpg"
    for prefix in ("", "neon", "/neon/", "neon/../x/", "other/"):
        with pytest.raises(backup.BackupError):
            backup.object_key(prefix, now, "42", "1")
    with pytest.raises(backup.BackupError):
        backup.object_key("neon/", now, "42/../../x", "1")


# -- encryption ----------------------------------------------------------------------------------------


@pytest.fixture
def gnupg_dir():
    base = Path(tempfile.mkdtemp(prefix="ng", dir="/tmp"))
    (base / "home").mkdir(mode=0o700)
    try:
        yield base
    finally:
        backup._stop_agent(base / "home")
        shutil.rmtree(base, ignore_errors=True)


@pytest.mark.parametrize("damage", ["flip_a_byte", "truncate"])
def test_a_damaged_or_altered_encrypted_file_is_detected(gnupg_dir, damage):
    plain, encrypted = gnupg_dir / "plain", gnupg_dir / "plain.gpg"
    plain.write_bytes(DUMP_CONTENT)
    backup.encrypt(plain, encrypted, PASSPHRASE, gnupg_dir / "home")
    assert backup.decrypted_digest(encrypted, PASSPHRASE, gnupg_dir / "home", gnupg_dir) == (hashlib.sha256(DUMP_CONTENT).hexdigest(), len(DUMP_CONTENT))
    data = bytearray(encrypted.read_bytes())
    if damage == "flip_a_byte":
        data[len(data) // 2] ^= 0x01
    else:
        del data[-20:]
    encrypted.write_bytes(bytes(data))
    with pytest.raises(backup.BackupError, match="did not decrypt and authenticate"):
        backup.decrypted_digest(encrypted, PASSPHRASE, gnupg_dir / "home", gnupg_dir)

    # The drill's decryption deletes whatever GnuPG had already written.
    target = gnupg_dir / "out.pgdump"
    with pytest.raises(drill.DrillError, match="Decryption FAILED"):
        drill.decrypt(shutil.which("gpg"), encrypted, target, PASSPHRASE, gnupg_dir / "home")
    assert not target.exists()


def test_the_drill_decrypts_what_the_backup_encrypts(gnupg_dir):
    plain, encrypted, target = gnupg_dir / "plain", gnupg_dir / "plain.gpg", gnupg_dir / "out.pgdump"
    plain.write_bytes(DUMP_CONTENT)
    backup.encrypt(plain, encrypted, PASSPHRASE, gnupg_dir / "home")
    drill.decrypt(drill.check_gpg(), encrypted, target, PASSPHRASE, gnupg_dir / "home")
    assert target.read_bytes() == DUMP_CONTENT
    with pytest.raises(drill.DrillError):
        drill.decrypt(drill.check_gpg(), encrypted, gnupg_dir / "wrong.pgdump", "not-the-right-passphrase-at-all", gnupg_dir / "home")
    assert not (gnupg_dir / "wrong.pgdump").exists()


# -- the drill cannot target a server --------------------------------------------------------------


def test_the_drill_takes_no_database_url_and_restores_only_into_its_own_networkless_container():
    source = _DRILL.read_text()
    assert "DATABASE_URL" not in source.replace("BACKUP_ENCRYPTION_PASSPHRASE", "")
    assert '"--network", "none"' in source and '"--tmpfs", "/var/lib/postgresql/data"' in source
    assert re.search(r'POSTGRES_IMAGE = "postgres:16\.\d+-bookworm@sha256:[0-9a-f]{64}"', source)
    with pytest.raises(SystemExit):
        drill.main(["--file", "x", "--dbname", "postgresql://elsewhere/db"])
    with pytest.raises(drill.DrillError, match="directly under neon/"):
        drill.download("bucket", "neon/../other/x.pgdump.gpg", Path("/nonexistent"))


# -- the workflow --------------------------------------------------------------------------------------


def _top_level(name: str) -> str:
    match = re.search(rf"^{name}:(.*?)(?=^\S)", _WORKFLOW + "\nEND:", re.MULTILINE | re.DOTALL)
    assert match, name
    return match.group(1)


def test_workflow_runs_only_on_schedule_or_by_hand_from_main():
    triggers = _top_level("on")
    assert re.findall(r"^  (\w+):", triggers, re.MULTILINE) == ["schedule", "workflow_dispatch"]
    code = "\n".join(line for line in _WORKFLOW.splitlines() if not line.lstrip().startswith("#"))
    assert "pull_request" not in code and "push:" not in code and "workflow_run" not in code
    condition = re.search(r"if: >-\n(.*?)\n    runs-on", _WORKFLOW, re.DOTALL).group(1)
    assert "github.ref == 'refs/heads/main'" in condition
    assert "github.repository == 'garima24112000/travelobligator'" in condition
    # The schedule stays off until the owner turns it on; landing the file starts nothing.
    assert "(github.event_name == 'workflow_dispatch' || vars.NEON_BACKUP_SCHEDULE_ENABLED == 'true')" in condition


def test_workflow_permissions_are_minimal_and_actions_are_pinned():
    assert re.search(r"^permissions: \{\}$", _WORKFLOW, re.MULTILINE)
    job_permissions = re.search(r"^    permissions:\n((?:      .*\n)+)", _WORKFLOW, re.MULTILINE).group(1)
    assert [line.split("#")[0].strip() for line in job_permissions.splitlines()] == ["contents: read", "id-token: write"]
    uses = re.findall(r"uses: (\S+)", _WORKFLOW)
    assert uses and all(re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref) for ref in uses)
    assert "aws-access-key-id" not in _WORKFLOW and "aws-secret-access-key" not in _WORKFLOW
    assert "persist-credentials: false" in _WORKFLOW


def test_secrets_reach_only_the_backup_step_and_no_third_party_action():
    steps = re.split(r"\n      - ", _WORKFLOW.split("    steps:", 1)[1])
    holding = [step for step in steps if "secrets." in step]
    assert len(holding) == 1
    [step] = holding
    assert "uses:" not in step and "run: python3 -I scripts/backup/neon_s3_backup.py" in step
    assert set(re.findall(r"secrets\.(\w+)", _WORKFLOW)) == {"NEON_BACKUP_DATABASE_URL", "BACKUP_ENCRYPTION_PASSPHRASE"}
    assert "BACKUP_S3_PREFIX: neon/" in _WORKFLOW and "postgresql-client-16" in _WORKFLOW
    assert "upload-artifact" not in _WORKFLOW and "set -x" not in _WORKFLOW


def test_backup_scripts_use_only_the_standard_library():
    allowed = set(sys.stdlib_module_names)
    for path in (_SCRIPT, _DRILL):
        imported = set(re.findall(r"^(?:from|import) (\w+)", path.read_text(), re.MULTILINE)) - {"__future__"}
        assert imported <= allowed, (path.name, imported - allowed)
