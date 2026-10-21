from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth import abuse
from app.main import app
from app.repositories.auth_attempt_repository import LOGIN_FAILURE, auth_attempt_repository
from app.repositories.user_repository import user_repository
from app.services import data_deletion_service
from app.tests.api.test_data_deletion_p1a import _NOTHING, _account, _active_job, _full_trip, _stored_for, _user_id
from scripts import locked_out_account_deletion as tool

# Phase 1B: the locked-out deletion command, hermetic (Local JSON repositories, a temporary challenge store,
# no database URL, no secret, no email). The deletion itself is the application's own service, whose
# PostgreSQL transaction is covered by tests/repositories/test_postgres_data_deletion_p1a_integration.py.

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path: Path) -> tool.ChallengeStore:
    return tool.ChallengeStore(tmp_path / "challenges")


@pytest.fixture
def isolated_environment(monkeypatch):
    """The command writes DATABASE_URL / PERSISTENCE_BACKEND / SESSION_SECRET_KEY into its own process
    environment; register them so whatever it writes is undone after the test."""
    for name in ("DATABASE_URL", "PERSISTENCE_BACKEND", "SESSION_SECRET_KEY", "TRAVELOB_TEST_MODE"):
        original = os.environ.get(name)
        monkeypatch.setenv(name, original if original is not None else "registered")
        if original is None:
            monkeypatch.delenv(name)
    return monkeypatch


def _verified(store, email: str, user_id: str) -> tool.Challenge:
    challenge, code = tool.create_challenge(store, user_id=user_id, email=email, now=NOW)
    tool.verify_code(store, challenge.request_id, code, now=NOW + timedelta(hours=1))
    return store.load(challenge.request_id)


def _delete(store, challenge: tool.Challenge, email: str, *, dry_run: bool = False, typed: str | None = None,
            confirm: str | None = None, now: datetime | None = None, out: list | None = None) -> int:
    confirm = challenge.fingerprint if confirm is None else confirm
    return tool.run_delete(
        store, challenge.request_id, email=email, confirm_account=confirm,
        typed_confirmation=f"DELETE {confirm}" if typed is None else typed, dry_run=dry_run,
        now=now or NOW + timedelta(hours=2), say=(out.append if out is not None else lambda line: None),
    )


def _failed_sign_in(email: str) -> None:
    assert TestClient(app).post("/auth/login", json={"email": email, "password": "not-the-password"}).status_code == 401


def _login_failures() -> set[str]:
    return {attempt.subject_hash for attempt in auth_attempt_repository._attempts if attempt.kind == LOGIN_FAILURE}


# -- the mailbox challenge -------------------------------------------------------------------------------


def test_the_right_code_verifies_the_request_once(store) -> None:
    challenge, code = tool.create_challenge(store, user_id="user-1", email="Person@Example.com ", now=NOW)
    assert len(tool.normalize_code(code)) == 10 and set(tool.normalize_code(code)) <= set(tool.CODE_ALPHABET)
    verified = tool.verify_code(store, challenge.request_id, code.lower().replace("-", " "), now=NOW + timedelta(hours=71))
    assert verified.status == tool.VERIFIED and verified.attempts == 1
    with pytest.raises(tool.OperatorError, match="already verified"):
        tool.verify_code(store, challenge.request_id, code, now=NOW + timedelta(hours=71))


def test_a_wrong_code_is_refused_and_counted(store) -> None:
    challenge, code = tool.create_challenge(store, user_id="user-1", email="a@example.com", now=NOW)
    with pytest.raises(tool.OperatorError, match="Wrong code. 4 attempt"):
        tool.verify_code(store, challenge.request_id, "ABCDE-FGHJK" if code != "ABCDE-FGHJK" else "BBBBB-BBBBB", now=NOW)
    assert store.load(challenge.request_id).attempts == 1 and store.load(challenge.request_id).status == tool.PENDING
    assert tool.verify_code(store, challenge.request_id, code, now=NOW).status == tool.VERIFIED


def test_too_many_wrong_codes_lock_the_request_even_against_the_right_one(store) -> None:
    challenge, code = tool.create_challenge(store, user_id="user-1", email="a@example.com", now=NOW)
    for _ in range(tool.MAX_ATTEMPTS):
        with pytest.raises(tool.OperatorError, match="Wrong code"):
            tool.verify_code(store, challenge.request_id, "ZZZZZ-ZZZZZ", now=NOW)
    assert store.load(challenge.request_id).status == tool.LOCKED
    with pytest.raises(tool.OperatorError, match="locked"):
        tool.verify_code(store, challenge.request_id, code, now=NOW)


def test_an_expired_code_is_refused(store) -> None:
    challenge, code = tool.create_challenge(store, user_id="user-1", email="a@example.com", now=NOW, hours=72)
    with pytest.raises(tool.OperatorError, match="expired"):
        tool.verify_code(store, challenge.request_id, code, now=NOW + timedelta(hours=72))
    assert store.load(challenge.request_id).status == tool.EXPIRED


@pytest.mark.parametrize("hours", [0, 73, 1000])
def test_a_code_never_lives_longer_than_72_hours(store, hours: int) -> None:
    with pytest.raises(tool.OperatorError, match="1 to 72 hours"):
        tool.create_challenge(store, user_id="user-1", email="a@example.com", now=NOW, hours=hours)


def test_one_open_request_per_account_and_a_replaced_one_authorizes_nothing(store) -> None:
    first, first_code = tool.create_challenge(store, user_id="user-1", email="a@example.com", now=NOW)
    with pytest.raises(tool.OperatorError, match="already has an open request"):
        tool.create_challenge(store, user_id="user-1", email="a@example.com", now=NOW)
    second, _ = tool.create_challenge(store, user_id="user-1", email="a@example.com", now=NOW, replace=True)
    assert store.load(first.request_id).status == tool.SUPERSEDED and second.request_id != first.request_id
    with pytest.raises(tool.OperatorError, match="superseded"):
        tool.verify_code(store, first.request_id, first_code, now=NOW)


def test_the_store_holds_no_address_no_account_id_and_no_code(store) -> None:
    challenge, code = tool.create_challenge(store, user_id="user-secret-id", email="private.person@example.com", now=NOW)
    for path in store.directory.rglob("*"):
        if path.is_file() and path.name != "store.key":
            text = path.read_text()
            for secret in ("user-secret-id", "private.person@example.com", code, tool.normalize_code(code)):
                assert secret not in text, path.name
    assert stat.S_IMODE(store.directory.stat().st_mode) == 0o700
    for path in store.directory.rglob("*"):
        if path.is_file():
            assert stat.S_IMODE(path.stat().st_mode) == 0o600, path.name
    record = json.loads((store.directory / "requests" / f"{challenge.request_id}.json").read_text())
    assert record["code_hash"] and len(record["code_salt"]) == 32


def test_the_store_refuses_a_shared_directory_or_one_inside_the_repository(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    os.chmod(shared, 0o755)
    with pytest.raises(tool.OperatorError, match="readable by other users"):
        tool.ChallengeStore(shared)
    with pytest.raises(tool.OperatorError, match="inside the repository"):
        tool.ChallengeStore(Path(tool.__file__).resolve().parents[1] / ".challenges")


# -- deletion: refusals ------------------------------------------------------------------------------


def test_deletion_without_a_verified_request_is_refused_and_deletes_nothing(store) -> None:
    browser, email = _account()
    user_id = _user_id(browser)
    trip_id = _full_trip(browser)
    pending, _ = tool.create_challenge(store, user_id=user_id, email=email, now=NOW)
    with pytest.raises(tool.OperatorError, match="has not been verified"):
        _delete(store, pending, email)
    with pytest.raises(tool.OperatorError, match="No such request"):
        _delete(store, tool.Challenge(**{**pending.__dict__, "request_id": "R-000000000000"}), email)
    assert user_repository.get_by_user_id(user_id) is not None and _stored_for(trip_id) != _NOTHING


def test_a_verified_request_cannot_delete_a_different_account(store) -> None:
    owner, owner_email = _account()
    other, other_email = _account()
    challenge = _verified(store, owner_email, _user_id(owner))
    with pytest.raises(tool.OperatorError, match="does not belong to the account"):
        _delete(store, challenge, other_email)
    assert user_repository.get_by_user_id(_user_id(other)) is not None
    assert user_repository.get_by_user_id(_user_id(owner)) is not None


def test_a_wrong_fingerprint_or_a_missing_confirmation_deletes_nothing(store) -> None:
    browser, email = _account()
    challenge = _verified(store, email, _user_id(browser))
    with pytest.raises(tool.OperatorError, match="--confirm-account does not match"):
        _delete(store, challenge, email, confirm="000000000000")
    with pytest.raises(tool.OperatorError, match="Confirmation not given"):
        _delete(store, challenge, email, typed="delete it")
    with pytest.raises(tool.OperatorError, match="Confirmation not given"):
        _delete(store, challenge, email, typed="")
    assert user_repository.get_by_user_id(_user_id(browser)) is not None
    assert store.load(challenge.request_id).status == tool.VERIFIED


def test_a_verified_request_expires_too(store) -> None:
    browser, email = _account()
    challenge = _verified(store, email, _user_id(browser))
    with pytest.raises(tool.OperatorError, match="expired"):
        _delete(store, challenge, email, now=NOW + timedelta(hours=72, minutes=1))
    assert user_repository.get_by_user_id(_user_id(browser)) is not None


def test_a_dry_run_checks_everything_and_deletes_nothing(store) -> None:
    browser, email = _account()
    trip_id = _full_trip(browser)
    challenge = _verified(store, email, _user_id(browser))
    out: list[str] = []
    assert _delete(store, challenge, email, dry_run=True, typed="", out=out) == 0
    assert "Dry run: nothing was deleted" in out[-1] and "1 trip(s)" in out[0]
    assert _stored_for(trip_id) != _NOTHING and store.load(challenge.request_id).status == tool.VERIFIED


# -- deletion: the intended account, and only it ----------------------------------------------------


def test_the_verified_account_is_deleted_with_everything_it_owns_and_nothing_else(store, capsys) -> None:
    browser, email = _account()
    user_id = _user_id(browser)
    trips = [_full_trip(browser), _full_trip(browser)]
    staying, staying_email = _account()
    staying_trip = _full_trip(staying)
    _failed_sign_in(email)
    _failed_sign_in(staying_email)
    assert abuse.login_failure_key(email) in _login_failures()

    challenge = _verified(store, email, user_id)
    out: list[str] = []
    assert _delete(store, challenge, email.upper(), out=out) == 0

    assert user_repository.get_by_user_id(user_id) is None
    for trip_id in trips:
        assert _stored_for(trip_id) == _NOTHING
    assert abuse.login_failure_key(email) not in _login_failures()  # its failed-sign-in counters went too
    # the other account is untouched, counters included
    assert user_repository.get_by_user_id(_user_id(staying)) is not None
    assert _stored_for(staying_trip) != _NOTHING and abuse.login_failure_key(staying_email) in _login_failures()
    assert browser.get("/auth/me").status_code == 401  # the deleted account's session no longer works
    assert store.load(challenge.request_id).status == tool.CONSUMED

    # output names the account only by fingerprint: no address, no id, no trip id
    printed = "\n".join(out) + capsys.readouterr().out
    for secret in (email, email.upper(), user_id, *trips):
        assert secret not in printed
    assert challenge.fingerprint in printed and "2 trip(s)" in printed


def test_a_used_request_never_authorizes_a_second_deletion(store) -> None:
    browser, email = _account()
    challenge = _verified(store, email, _user_id(browser))
    assert _delete(store, challenge, email) == 0
    # someone registers again with the same address: the old, used request must not touch the new account
    newcomer = TestClient(app)
    assert newcomer.post("/auth/signup", json={"email": email, "password": "testpassword123"}).status_code == 201
    with pytest.raises(tool.OperatorError, match="does not belong|already been used"):
        _delete(store, challenge, email)
    assert newcomer.get("/auth/me").status_code == 200


def test_a_running_generation_blocks_deletion_and_the_request_stays_usable(store) -> None:
    browser, email = _account()
    user_id = _user_id(browser)
    trip_id = _full_trip(browser)
    job = _active_job(trip_id, user_id, running=True)
    challenge = _verified(store, email, user_id)
    with pytest.raises(tool.OperatorError, match="generation is running"):
        _delete(store, challenge, email)
    assert user_repository.get_by_user_id(user_id) is not None and _stored_for(trip_id) != _NOTHING
    assert store.load(challenge.request_id).status == tool.VERIFIED
    from app.repositories.job_repository import job_repository

    job_repository._jobs.pop(job.job_id)
    assert _delete(store, challenge, email) == 0
    assert user_repository.get_by_user_id(user_id) is None


def test_an_unexpected_failure_reports_the_type_only_and_keeps_the_request(store, monkeypatch) -> None:
    browser, email = _account()
    challenge = _verified(store, email, _user_id(browser))

    def broken(user_id, *, email_digest):
        raise RuntimeError(f"database said something about {email}")

    monkeypatch.setattr(data_deletion_service, "delete_account", broken)
    with pytest.raises(tool.OperatorError) as refusal:
        _delete(store, challenge, email)
    assert "RuntimeError" in str(refusal.value) and email not in str(refusal.value)
    assert store.load(challenge.request_id).status == tool.VERIFIED
    assert user_repository.get_by_user_id(_user_id(browser)) is not None


def test_deletion_goes_through_the_applications_own_service(store, monkeypatch) -> None:
    browser, email = _account()
    user_id = _user_id(browser)
    challenge = _verified(store, email, user_id)
    calls = []
    real = data_deletion_service.delete_account
    monkeypatch.setattr(data_deletion_service, "delete_account", lambda uid, *, email_digest: calls.append((uid, email_digest)) or real(uid, email_digest=email_digest))
    assert _delete(store, challenge, email) == 0
    assert calls == [(user_id, abuse.login_failure_key(email))]
    source = Path(tool.__file__).read_text()
    assert "sqlalchemy" not in source and "execute(" not in source  # no SQL of its own


# -- command line: no production access, no secret, nothing exposed ----------------------------------


def test_the_command_refuses_without_its_own_database_variable(store, isolated_environment, capsys) -> None:
    monkeypatch = isolated_environment
    monkeypatch.setenv(tool.STATE_DIR_ENV, str(store.directory))
    monkeypatch.delenv(tool.DATABASE_ENV, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:app-secret@db.example.test/app")  # never used by this tool
    for argv in (["challenge"], ["delete", "--request", "R-000000000000", "--confirm-account", "x", "--dry-run"]):
        assert tool.main(argv) == 1
        captured = capsys.readouterr()
        assert f"{tool.DATABASE_ENV} is not set" in captured.err and "app-secret" not in captured.out + captured.err


def test_a_real_deletion_needs_the_deployments_session_secret(isolated_environment) -> None:
    monkeypatch = isolated_environment
    monkeypatch.setenv(tool.DATABASE_ENV, "postgresql://u:p@db.example.test/app")
    monkeypatch.setattr(tool, "_refuse_unless_schema_at_head", lambda: None)
    monkeypatch.delenv(tool.SECRET_ENV, raising=False)
    with pytest.raises(tool.OperatorError, match="SESSION_SECRET_KEY"):
        tool._bind_environment(need_database=True, need_secret=True)


def test_the_command_is_not_reachable_over_http() -> None:
    paths = {route.path for route in app.routes}
    assert not any("challenge" in path or "locked" in path or "operator" in path for path in paths)
    application = "\n".join(p.read_text() for p in (Path(tool.__file__).resolve().parents[1] / "app").rglob("*.py") if "tests" not in p.parts)
    assert "locked_out_account_deletion" not in application


def test_verify_and_status_work_without_any_database_or_secret(store, monkeypatch, capsys) -> None:
    monkeypatch.setenv(tool.STATE_DIR_ENV, str(store.directory))
    challenge, code = tool.create_challenge(store, user_id="user-1", email="a@example.com", now=datetime.now(timezone.utc))
    monkeypatch.setattr(tool.getpass, "getpass", lambda prompt: code)
    assert tool.main(["verify", "--request", challenge.request_id]) == 0
    assert tool.main(["status", "--request", challenge.request_id]) == 0
    printed = capsys.readouterr().out
    assert "verified" in printed and code not in printed and "a@example.com" not in printed
