from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# GitHub-independent backup automation (scripts/backup/aws/README.md), hermetic: the real handler, the real backup
# script's helpers and the real GnuPG, with stand-ins for S3, Parameter Store, pg_restore and the frontend. No AWS
# account, no network, no database. Nothing here deploys or activates anything.

_ROOT = Path(__file__).resolve().parents[4]
_AWS = _ROOT / "scripts" / "backup" / "aws"
_TEMPLATE = json.loads((_AWS / "template.json").read_text())
_RESOURCES = _TEMPLATE["Resources"]

PASSPHRASE = "test-only-passphrase-0123456789abcdef"
DATABASE_URL = "postgresql://travelobligator_backup:Pa55-word-never-shown@ep-quiet-lake-123456.us-east-1.aws.neon.tech/neondb"
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


handler =_load(_AWS / "lambda_handler.py", "aws_backup_lambda_handler")
monitor = _load(_ROOT / "scripts" / "monitoring" / "production_monitor.py", "production_monitor_for_aws_test")


def _key(when: datetime) -> str:
    return f"neon/travelobligator-{when.strftime('%Y%m%dT%H%M%SZ')}-runlocal-1.pgdump.gpg"


class FakeS3:
    def __init__(self, objects: dict[str, tuple[datetime, bytes]]):
        self.objects = objects
        self.downloads: list[str] = []

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        objects = self.objects

        class Paginator:
            def paginate(self, Bucket, Prefix):
                assert Prefix == "neon/"
                yield {"Contents": [{"Key": key, "LastModified": when, "Size": len(body)}
                                    for key, (when, body) in objects.items() if key.startswith(Prefix)]}

        return Paginator()

    def download_file(self, bucket, key, target):
        self.downloads.append(key)
        Path(target).write_bytes(self.objects[key][1])


class FakeSSM:
    def __init__(self, values: dict[str, str]):
        self.values = values

    def get_parameter(self, Name, WithDecryption):
        assert WithDecryption is True
        return {"Parameter": {"Value": self.values[Name]}}


class FakeResponse:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener(*statuses):
    calls = iter(statuses)

    def open_(request, timeout):
        assert request.get_method() == "GET" and "onrender" not in request.full_url
        return FakeResponse(next(calls))

    return open_


_MONITOR_ENV = {"BACKUP_S3_BUCKET": "travelobligator-db-backups-10192026", "FRONTEND_URL": "https://travelobligator.vercel.app"}


# -- template --------------------------------------------------------------------------------------------


def test_nothing_runs_or_notifies_until_activated():
    assert _TEMPLATE["Parameters"]["Activate"]["Default"] == "false"
    schedules = [r for r in _RESOURCES.values() if r["Type"] == "AWS::Scheduler::Schedule"]
    alarms = [r for r in _RESOURCES.values() if r["Type"] == "AWS::CloudWatch::Alarm"]
    assert len(schedules) == 3 and len(alarms) == 5  # within CloudWatch's ten free alarms
    for schedule in schedules:
        assert schedule["Properties"]["State"] == {"Fn::If": ["IsActive", "ENABLED", "DISABLED"]}
    for alarm in alarms:
        assert alarm["Properties"]["ActionsEnabled"] == {"Fn::If": ["IsActive", True, False]}
    assert _TEMPLATE["Conditions"]["IsActive"] == {"Fn::Equals": [{"Ref": "Activate"}, "true"]}


def test_each_function_has_a_fixed_mode_and_only_its_own_permissions():
    functions = {r["Properties"]["Environment"]["Variables"]["BACKUP_FUNCTION_MODE"]: r["Properties"]
                 for r in _RESOURCES.values() if r["Type"] == "AWS::Lambda::Function"}
    assert set(functions) == {"backup", "verify", "monitor"}
    roles = {mode: _RESOURCES[props["Role"]["Fn::GetAtt"][0]] for mode, props in functions.items()}

    def actions(role):
        found = set()
        for statement in role["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]:
            assert statement["Effect"] == "Allow"
            found.update([statement["Action"]] if isinstance(statement["Action"], str) else statement["Action"])
        return found

    assert actions(roles["backup"]) == {"logs:CreateLogStream", "logs:PutLogEvents", "ssm:GetParameter", "s3:PutObject", "s3:GetObject"}
    assert actions(roles["verify"]) == {"logs:CreateLogStream", "logs:PutLogEvents", "ssm:GetParameter", "s3:ListBucket", "s3:GetObject"}
    assert actions(roles["monitor"]) == {"logs:CreateLogStream", "logs:PutLogEvents", "s3:ListBucket"}
    text = json.dumps(_RESOURCES)
    assert '"Action": "*"' not in text and '"Resource": "*"' not in text and "s3:DeleteObject" not in text
    for role in roles.values():
        for statement in role["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]:
            resource = json.dumps(statement["Resource"])
            if statement["Action"] == "s3:ListBucket":
                assert statement["Condition"] == {"StringEquals": {"s3:prefix": "neon/"}}
            elif "s3:" in json.dumps(statement["Action"]):
                assert resource.endswith('/neon/*"}')


def test_the_template_holds_no_secret_and_never_checks_the_backend():
    text = (_AWS / "template.json").read_text()
    assert "postgresql://" not in text and "neon.tech" not in text and "onrender" not in text
    assert "SecureString" not in json.dumps(_RESOURCES)  # the parameters are created by the owner, not here
    assert _TEMPLATE["Parameters"]["FrontendUrl"]["Default"] == "https://travelobligator.vercel.app"
    assert _TEMPLATE["Parameters"]["ImageUri"]["AllowedPattern"].endswith("@sha256:[0-9a-f]{64}$")


def test_the_age_limit_is_the_production_monitors():
    assert handler.MAX_BACKUP_AGE_HOURS == monitor.MAX_BACKUP_AGE_HOURS


def test_the_image_carries_the_unchanged_backup_script_and_hash_pinned_packages():
    dockerfile = (_AWS / "Dockerfile").read_text()
    assert "COPY scripts/backup/neon_s3_backup.py /opt/backup/neon_s3_backup.py" in dockerfile
    assert "--require-hashes" in dockerfile and "postgresql-client-16" in dockerfile
    requirements = [line for line in (_AWS / "requirements.txt").read_text().splitlines() if line and not line.startswith("#")]
    pinned = [line for line in requirements if not line.startswith(" ") and not line.startswith("--hash")]
    assert pinned and all("==" in line for line in pinned)
    assert handler.BACKUP_SCRIPT == _ROOT / "scripts" / "backup" / "neon_s3_backup.py"


# -- monitor ---------------------------------------------------------------------------------------------


def test_monitor_passes_with_a_fresh_backup_and_the_frontend_up():
    s3 = FakeS3({_key(NOW - timedelta(hours=8)): (NOW - timedelta(hours=8), b"x"), "neon/other.txt": (NOW, b"y")})
    assert handler.run_monitor(_MONITOR_ENV, s3, NOW, _opener(200), sleep=lambda s: None) == {"backup_age_hours": 8.0, "frontend_status": 200}


@pytest.mark.parametrize("objects, statuses, expected", [
    ({_key(NOW - timedelta(hours=37)): (NOW - timedelta(hours=37), b"x")}, (200,), "37.0 hours old"),
    ({}, (200,), "no backup under neon/"),
    ({"neon/unrelated-file": (NOW, b"x")}, (200,), "no backup under neon/"),
    ({_key(NOW): (NOW, b"x")}, (503, 503), "answered 503"),
])
def test_monitor_fails_on_a_stale_or_missing_backup_or_a_down_frontend(objects, statuses, expected):
    with pytest.raises(handler.CheckFailed, match=expected):
        handler.run_monitor(_MONITOR_ENV, FakeS3(objects), NOW, _opener(*statuses), sleep=lambda s: None)


def test_monitor_retries_the_frontend_once():
    s3 = FakeS3({_key(NOW): (NOW, b"x")})
    assert handler.run_monitor(_MONITOR_ENV, s3, NOW, _opener(502, 200), sleep=lambda s: None)["frontend_status"] == 200


# -- backup ----------------------------------------------------------------------------------------------


class FakeProcess:
    def __init__(self, code=0, hang=False):
        self.code, self.hang, self.signals = code, hang, []

    def wait(self, timeout=None):
        if self.hang and not self.signals:
            raise subprocess.TimeoutExpired("backup", timeout)
        return self.code

    def send_signal(self, number):
        self.signals.append(number)

    def kill(self):
        self.signals.append("kill")


_BACKUP_ENV = {
    "PATH": "/usr/bin", "BACKUP_S3_BUCKET": "travelobligator-db-backups-10192026", "BACKUP_S3_PREFIX": "neon/",
    "BACKUP_EXPECTED_DB_USER": "travelobligator_backup", "PG_BIN_DIR": "/usr/lib/postgresql/16/bin",
    "DATABASE_URL_PARAMETER": "/travelobligator/backup/neon-database-url",
    "PASSPHRASE_PARAMETER": "/travelobligator/backup/encryption-passphrase",
    "AWS_REGION": "us-east-1", "AWS_ACCESS_KEY_ID": "AKIAEXAMPLE", "AWS_LAMBDA_FUNCTION_NAME": "travelobligator-neon-backup",
    "UNRELATED_SETTING": "must-not-pass",
}
_SSM = FakeSSM({"/travelobligator/backup/neon-database-url": DATABASE_URL,
                "/travelobligator/backup/encryption-passphrase": PASSPHRASE})


def test_backup_runs_the_unchanged_script_with_the_secrets_only_in_its_environment(capsys):
    seen = {}

    def popen(argv, env, stdin):
        seen.update(argv=argv, env=env)
        return FakeProcess()

    result = handler.run_backup(dict(_BACKUP_ENV), _SSM, remaining_seconds=900, popen=popen)
    assert result["duration_seconds"] >= 0
    assert seen["argv"] == [sys.executable, "-I", str(_ROOT / "scripts" / "backup" / "neon_s3_backup.py")]
    assert seen["env"]["NEON_BACKUP_DATABASE_URL"] == DATABASE_URL and seen["env"]["BACKUP_ENCRYPTION_PASSPHRASE"] == PASSPHRASE
    assert seen["env"]["AWS_ACCESS_KEY_ID"] == "AKIAEXAMPLE" and seen["env"]["HOME"] == "/tmp"
    assert "UNRELATED_SETTING" not in seen["env"] and "DATABASE_URL_PARAMETER" not in seen["env"]
    output = capsys.readouterr()
    assert "Pa55-word" not in output.out + output.err and PASSPHRASE not in output.out + output.err


def test_a_failed_backup_raises_and_a_slow_one_is_stopped_with_sigterm():
    with pytest.raises(handler.CheckFailed, match="exit code 1"):
        handler.run_backup(dict(_BACKUP_ENV), _SSM, remaining_seconds=900, popen=lambda *a, **k: FakeProcess(code=1))
    slow = FakeProcess(hang=True)
    with pytest.raises(handler.CheckFailed, match="stopped"):
        handler.run_backup(dict(_BACKUP_ENV), _SSM, remaining_seconds=900, popen=lambda *a, **k: slow)
    assert slow.signals[0] == handler.signal.SIGTERM
    with pytest.raises(handler.CheckFailed, match="Not enough time"):
        handler.run_backup(dict(_BACKUP_ENV), _SSM, remaining_seconds=30, popen=lambda *a, **k: FakeProcess())


def test_a_parameter_store_error_is_reported_by_type_only():
    class Failing:
        def get_parameter(self, **kwargs):
            raise ValueError(f"oops {DATABASE_URL}")

    with pytest.raises(handler.CheckFailed) as error:
        handler.run_backup(dict(_BACKUP_ENV), Failing(), remaining_seconds=900, popen=lambda *a, **k: FakeProcess())
    assert "Pa55" not in str(error.value) and "ValueError" in str(error.value)


def test_work_left_by_a_killed_invocation_is_removed(tmp_path):
    (tmp_path / "neon-backup-abc").mkdir()
    (tmp_path / "backup-verify-def").mkdir()
    (tmp_path / "keep-me").mkdir()
    assert handler.remove_leftovers(str(tmp_path)) == 2
    assert [p.name for p in tmp_path.iterdir()] == ["keep-me"]


# -- verify (real GnuPG) ---------------------------------------------------------------------------------

_FAKE_PG_RESTORE = """
import sys
assert sys.argv[1] == "--list"
assert open(sys.argv[2], "rb").read().startswith(b"PGDMP")
print(";\\n;     Dumped from database version: 16.9\\n;     Dumped by pg_dump version: 16.15\\n;")
print("3301; 0 16390 TABLE DATA public alembic_version neondb_owner")
print("3302; 0 16395 TABLE DATA public users neondb_owner")
"""


@pytest.fixture
def verify_setup(monkeypatch):
    base =Path(tempfile.mkdtemp(prefix="vb", dir="/tmp"))  # short: gpg-agent's socket path
    try:
        pgbin = base / "pgbin"
        pgbin.mkdir()
        (pgbin / "pg_restore").write_text(f"#!{sys.executable}\n" + textwrap.dedent(_FAKE_PG_RESTORE))
        (pgbin / "pg_restore").chmod(0o755)
        monkeypatch.setenv("PG_BIN_DIR", str(pgbin))
        monkeypatch.setenv("TMPDIR", str(base))
        monkeypatch.setattr(tempfile, "tempdir", None)
        module = handler._backup_module()
        plain, encrypted, home = base / "p.pgdump", base / "p.gpg", base / "g"
        home.mkdir(mode=0o700)
        plain.write_bytes(b"PGDMP fake archive " + bytes(range(256)) * 16)
        module.encrypt(plain, encrypted, PASSPHRASE, home)
        module._stop_agent(home)
        yield base, encrypted.read_bytes()
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_verify_decrypts_and_reads_the_newest_backup_and_leaves_nothing(verify_setup, capsys):
    base, blob = verify_setup
    older, newer = NOW - timedelta(days=2), NOW - timedelta(hours=3)
    s3 = FakeS3({_key(older): (older, b"not even encrypted"), _key(newer): (newer, blob)})
    env = {"BACKUP_S3_BUCKET": "b-bucket", "PASSPHRASE_PARAMETER": "/travelobligator/backup/encryption-passphrase"}
    result = handler.run_verify(env, _SSM, s3)
    assert s3.downloads == [_key(newer)]
    assert result["tables_with_data"] == 2 and result["pg_dump_version"] == "16.15" and result["encrypted_bytes"] == len(blob)
    assert not [p for p in base.iterdir() if p.name.startswith("backup-verify-")]
    assert PASSPHRASE not in capsys.readouterr().out


def test_verify_fails_on_a_wrong_passphrase_or_a_damaged_object(verify_setup):
    base, blob = verify_setup
    env = {"BACKUP_S3_BUCKET": "b-bucket", "PASSPHRASE_PARAMETER": "/travelobligator/backup/encryption-passphrase"}
    wrong = FakeSSM({"/travelobligator/backup/encryption-passphrase": "another-passphrase-0123456789abcdef"})
    with pytest.raises(handler.CheckFailed, match="did not decrypt"):
        handler.run_verify(env, wrong, FakeS3({_key(NOW): (NOW, blob)}))
    damaged = blob[:-40] + bytes(b ^ 0xFF for b in blob[-40:])
    with pytest.raises(handler.CheckFailed, match="did not decrypt"):
        handler.run_verify(env, _SSM, FakeS3({_key(NOW): (NOW, damaged)}))
    assert not [p for p in base.iterdir() if p.name.startswith("backup-verify-")]


# -- entry point -----------------------------------------------------------------------------------------


def test_the_entry_point_hides_unexpected_errors_and_refuses_an_unknown_mode(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=lambda name: None))
    monkeypatch.setattr(handler, "remove_leftovers", lambda: 0)
    monkeypatch.setenv("BACKUP_FUNCTION_MODE", "drop-everything")
    with pytest.raises(RuntimeError, match="^drop-everything failed$"):
        handler.handler({"mode": "backup"}, None)
    monkeypatch.setenv("BACKUP_FUNCTION_MODE", "monitor")
    monkeypatch.setenv("BACKUP_S3_BUCKET", "b-bucket")
    monkeypatch.setenv("FRONTEND_URL", "https://travelobligator.vercel.app")

    def explode(*args, **kwargs):
        raise KeyError(DATABASE_URL)

    monkeypatch.setattr(handler, "run_monitor", explode)
    with pytest.raises(RuntimeError) as error:
        handler.handler({}, None)
    assert error.value.__cause__ is None and error.value.__suppress_context__
    output = capsys.readouterr().out
    assert "unexpected KeyError" in output and "Pa55" not in output
