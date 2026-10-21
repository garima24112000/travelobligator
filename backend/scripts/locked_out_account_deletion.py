#!/usr/bin/env python3
"""Operator command: delete the account of someone who cannot sign in, after a mailbox challenge.

    python backend/scripts/locked_out_account_deletion.py challenge [--hours 72] [--replace]
    python backend/scripts/locked_out_account_deletion.py verify  --request <id>
    python backend/scripts/locked_out_account_deletion.py status  --request <id>
    python backend/scripts/locked_out_account_deletion.py delete  --request <id> --confirm-account <fingerprint> [--dry-run]

Procedure (who sends what to whom, and the wording of the replies): docs/25_operations_runbook.md section 8.1.

Which database and secret it uses is decided by this tool's OWN variables, never by `DATABASE_URL`:

    ACCOUNT_DELETION_DATABASE_URL         the database the account lives in (challenge, delete)
    ACCOUNT_DELETION_SESSION_SECRET_KEY   that deployment's SESSION_SECRET_KEY (delete, not --dry-run): the
                                          account's failed-sign-in counters are stored under a hash keyed
                                          with it, and are removed together with the account
    ACCOUNT_DELETION_STATE_DIR            where challenges are kept (default ~/.travelobligator-account-deletion)

The project's `.env` is never read. The account's email address is typed in without echo; the code too.

Safety contract:
  * Deletion only through the application's own service (`data_deletion_service.delete_account`: one
    transaction, every table, refused while a generation runs) -- never SQL written here.
  * Deletion only for a challenge that is VERIFIED (the code the requester received at the account's own
    address was typed back correctly), unexpired (at most 72 hours after it was created) and never used
    before; the email typed at deletion must belong to that same account (checked against both the account
    id and the address the challenge was created for), and the operator must repeat the account's
    fingerprint. Anything else is refused and nothing is deleted.
  * A code is random (about 49 bits), shown once to the operator to send, and stored only as an scrypt hash.
    At most 5 wrong entries, then the challenge is locked; a used, expired, locked or replaced challenge
    never authorizes anything.
  * The challenge store holds no email address, no account id and no code: only keyed hashes, times and
    counters. It is a private directory (0700, files 0600) outside the repository, locked while in use.
  * Output names an account only by its fingerprint (a keyed hash prefix, meaningless outside this store):
    never an email address, an account id, a code, a connection string or a secret.
  * Never runs automatically and is never reachable over HTTP.
"""

from __future__ import annotations

import argparse
import fcntl
import getpass
import hashlib
import hmac
import json
import os
import secrets
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_REPOSITORY_ROOT = _BACKEND_ROOT.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

DATABASE_ENV = "ACCOUNT_DELETION_DATABASE_URL"
SECRET_ENV = "ACCOUNT_DELETION_SESSION_SECRET_KEY"
STATE_DIR_ENV = "ACCOUNT_DELETION_STATE_DIR"
DEFAULT_STATE_DIR = Path("~/.travelobligator-account-deletion")

MAX_HOURS = 72
MAX_ATTEMPTS = 5
# No 0/O, 1/I/L: the requester types the code back by hand.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 10
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}

PENDING, VERIFIED, CONSUMED, LOCKED, EXPIRED, SUPERSEDED = "pending", "verified", "consumed", "locked", "expired", "superseded"


class OperatorError(Exception):
    """A refusal or failure, with a fixed message that is safe to print."""


# -- the challenge store -------------------------------------------------------------------------------


@dataclass
class Challenge:
    request_id: str
    account_ref: str  # keyed hash of the account id
    email_ref: str  # keyed hash of the normalized address the challenge was created for
    code_salt: str
    code_hash: str
    created_at: str
    expires_at: str
    attempts: int = 0
    status: str = PENDING
    verified_at: str | None = None
    closed_at: str | None = None
    outcome: str | None = None

    @property
    def fingerprint(self) -> str:
        return self.account_ref[:12]


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def normalize_code(code: str) -> str:
    return "".join(character for character in (code or "").upper() if character.isalnum())


def new_code() -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    return f"{raw[:5]}-{raw[5:]}"


def _code_hash(code: str, salt: bytes) -> str:
    return hashlib.scrypt(normalize_code(code).encode(), salt=salt, **_SCRYPT).hex()


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


class ChallengeStore:
    def __init__(self, directory: Path):
        directory = directory.expanduser().resolve()
        if directory == _REPOSITORY_ROOT or _REPOSITORY_ROOT in directory.parents:
            raise OperatorError("Refusing to keep challenges inside the repository. Choose a directory outside it.")
        if directory.exists():
            if not directory.is_dir() or directory.stat().st_mode & 0o077:
                raise OperatorError("The challenge directory is readable by other users. Use a new directory, or one with mode 0700.")
        else:
            directory.mkdir(mode=0o700, parents=True)
            os.chmod(directory, 0o700)
        (directory / "requests").mkdir(mode=0o700, exist_ok=True)
        self.directory = directory
        key_path = directory / "store.key"
        if not key_path.exists():
            descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(secrets.token_bytes(32))
        self._key = key_path.read_bytes()

    def ref(self, purpose: str, value: str) -> str:
        return hmac.new(self._key, f"{purpose}:{value}".encode(), hashlib.sha256).hexdigest()

    @contextmanager
    def locked(self):
        descriptor = os.open(self.directory / "store.lock", os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield self
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _path(self, request_id: str) -> Path:
        if not request_id or not all(c.isalnum() or c == "-" for c in request_id):
            raise OperatorError("That is not a request id of this tool.")
        return self.directory / "requests" / f"{request_id}.json"

    def load(self, request_id: str) -> Challenge:
        path = self._path(request_id)
        if not path.is_file():
            raise OperatorError("No such request in this challenge store.")
        return Challenge(**json.loads(path.read_text()))

    def save(self, challenge: Challenge) -> None:
        path = self._path(challenge.request_id)
        temporary = path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            json.dump(asdict(challenge), handle, indent=1, sort_keys=True)
        os.replace(temporary, path)

    def all(self) -> list[Challenge]:
        return [Challenge(**json.loads(path.read_text())) for path in sorted((self.directory / "requests").glob("*.json"))]


# -- the three steps -----------------------------------------------------------------------------------


def create_challenge(store: ChallengeStore, *, user_id: str, email: str, now: datetime, hours: int = MAX_HOURS,
                     replace: bool = False) -> tuple[Challenge, str]:
    """A new challenge for the account; returns it and the code, which exists nowhere else afterwards."""
    if not 1 <= hours <= MAX_HOURS:
        raise OperatorError(f"A code may be valid for 1 to {MAX_HOURS} hours.")
    with store.locked():
        account_ref = store.ref("account", user_id)
        open_ones = [c for c in store.all() if c.account_ref == account_ref and c.status in (PENDING, VERIFIED)
                     and _parse(c.expires_at) > now]
        if open_ones and not replace:
            raise OperatorError("This account already has an open request. Use it, or pass --replace to cancel it and start again.")
        for old in open_ones:
            old.status, old.closed_at, old.outcome = SUPERSEDED, _iso(now), "replaced by a new request"
            store.save(old)
        code, salt = new_code(), secrets.token_bytes(16)
        challenge = Challenge(
            request_id=f"R-{secrets.token_hex(6)}", account_ref=account_ref, email_ref=store.ref("email", normalize_email(email)),
            code_salt=salt.hex(), code_hash=_code_hash(code, salt), created_at=_iso(now), expires_at=_iso(now + timedelta(hours=hours)),
        )
        store.save(challenge)
        return challenge, code


def verify_code(store: ChallengeStore, request_id: str, code: str, *, now: datetime) -> Challenge:
    """Checks the code the requester sent back. Every attempt is counted (and saved) BEFORE the comparison."""
    with store.locked():
        challenge = store.load(request_id)
        _refuse_if_closed(store, challenge, now)
        if challenge.status == VERIFIED:
            raise OperatorError("This request is already verified; a code is accepted once.")
        if challenge.attempts >= MAX_ATTEMPTS:
            challenge.status, challenge.closed_at, challenge.outcome = LOCKED, _iso(now), "too many wrong codes"
            store.save(challenge)
            raise OperatorError("Too many wrong codes: this request is locked. Start a new one if the requester asks again.")
        challenge.attempts += 1
        store.save(challenge)
        if not hmac.compare_digest(_code_hash(code, bytes.fromhex(challenge.code_salt)), challenge.code_hash):
            left = MAX_ATTEMPTS - challenge.attempts
            if left <= 0:
                challenge.status, challenge.closed_at, challenge.outcome = LOCKED, _iso(now), "too many wrong codes"
                store.save(challenge)
                raise OperatorError("Wrong code. No attempts left: this request is locked.")
            raise OperatorError(f"Wrong code. {left} attempt(s) left.")
        challenge.status, challenge.verified_at = VERIFIED, _iso(now)
        store.save(challenge)
        return challenge


def _refuse_if_closed(store: ChallengeStore, challenge: Challenge, now: datetime) -> None:
    if challenge.status == CONSUMED:
        raise OperatorError("This request has already been used. A code never authorizes a second deletion.")
    if challenge.status in (LOCKED, SUPERSEDED, EXPIRED):
        raise OperatorError(f"This request is {challenge.status}; it cannot authorize anything.")
    if now >= _parse(challenge.expires_at):
        challenge.status, challenge.closed_at, challenge.outcome = EXPIRED, _iso(now), "expired"
        store.save(challenge)
        raise OperatorError("This request has expired. Start a new one if the requester still asks.")


def authorize_deletion(store: ChallengeStore, request_id: str, *, user_id: str, email: str, confirm_account: str,
                       now: datetime) -> Challenge:
    """The checks that must ALL pass before anything is deleted. Raises on the first that does not."""
    challenge = store.load(request_id)
    _refuse_if_closed(store, challenge, now)
    if challenge.status != VERIFIED:
        raise OperatorError("This request has not been verified: the requester's code has not been checked.")
    if not hmac.compare_digest(store.ref("account", user_id), challenge.account_ref) or not hmac.compare_digest(
        store.ref("email", normalize_email(email)), challenge.email_ref
    ):
        raise OperatorError("That address does not belong to the account this request was verified for. Nothing was deleted.")
    if confirm_account != challenge.fingerprint:
        raise OperatorError("--confirm-account does not match this request's account fingerprint. Nothing was deleted.")
    return challenge


# -- the application side (repositories and the deletion service) ---------------------------------------


@dataclass(frozen=True)
class Account:
    user_id: str
    email: str
    trips: int
    generating: bool


def find_account(email: str) -> Account | None:
    from app.repositories.factory import get_job_repository, get_trip_repository, get_user_repository

    user = get_user_repository().get_by_email(normalize_email(email))
    if user is None:
        return None
    trips = get_trip_repository().list_by_owner_id(user.user_id)
    jobs = get_job_repository()
    generating = any(jobs.list_running_by_trip_id(trip.trip_id) for trip in trips)
    return Account(user_id=user.user_id, email=user.email, trips=len(trips), generating=generating)


def delete_account(account: Account):
    """The application's own account deletion, exactly as `POST /auth/delete-account` performs it."""
    from app.auth import abuse
    from app.services import data_deletion_service

    return data_deletion_service.delete_account(account.user_id, email_digest=abuse.login_failure_key(account.email))


def run_delete(store: ChallengeStore, request_id: str, *, email: str, confirm_account: str, typed_confirmation: str,
               dry_run: bool, now: datetime, say=print) -> int:
    from app.repositories.errors import GenerationInProgressError, NothingToDeleteError

    with store.locked():
        account = find_account(email)
        if account is None:
            raise OperatorError("No account uses that address (any more). Nothing was deleted.")
        challenge = authorize_deletion(store, request_id, user_id=account.user_id, email=email, confirm_account=confirm_account, now=now)
        say(f"Request {challenge.request_id}: verified; account {challenge.fingerprint}: {account.trips} trip(s)"
            f"{', a generation is running' if account.generating else ''}.")
        if dry_run:
            say("Dry run: nothing was deleted, and the request stays usable.")
            return 0
        if typed_confirmation != f"DELETE {challenge.fingerprint}":
            raise OperatorError("Confirmation not given. Nothing was deleted.")
        try:
            summary = delete_account(account)
        except GenerationInProgressError:
            raise OperatorError("A generation is running for this account. Nothing was deleted; try again once it has finished.") from None
        except NothingToDeleteError:
            challenge.status, challenge.closed_at, challenge.outcome = CONSUMED, _iso(now), "account already gone"
            store.save(challenge)
            raise OperatorError("The account no longer exists. Nothing was deleted; the request is closed.") from None
        except Exception as error:  # rolled back by the service's transaction; the message may quote data
            raise OperatorError(f"Deletion failed ({type(error).__name__}); nothing was deleted and the request stays verified.") from None
        challenge.status, challenge.closed_at, challenge.outcome = CONSUMED, _iso(now), "deleted"
        store.save(challenge)
        say(f"Deleted account {challenge.fingerprint}: {summary.trips} trip(s), {summary.planning_states} itinerary state(s), "
            f"{summary.jobs} job(s), {summary.branches} branch(es), {summary.revisions} revision(s). The request is closed.")
        say("Tell the requester it is done, and that copies in the daily encrypted backups expire about seven days later.")
        return 0


# -- command line --------------------------------------------------------------------------------------


def _bind_environment(*, need_database: bool, need_secret: bool) -> None:
    """Point the application at THIS tool's database and secret. Never the project `.env`, never the
    shell's own `DATABASE_URL` / `SESSION_SECRET_KEY`: both are overwritten in this process."""
    os.environ["TRAVELOB_TEST_MODE"] = "1"  # the established operator-tool switch: do not read the project .env
    if need_database:
        from scripts.db_backup_restore import OperatorError as UrlError, _url

        try:
            _url(DATABASE_ENV)  # shape and connection options, without printing anything of it
        except UrlError as error:
            raise OperatorError(str(error)) from None
        os.environ["DATABASE_URL"] = os.environ[DATABASE_ENV].strip()
        os.environ["PERSISTENCE_BACKEND"] = "postgres"
        _refuse_unless_schema_at_head()
    if need_secret:
        secret = os.environ.get(SECRET_ENV, "")
        if len(secret) < 32:
            raise OperatorError(f"{SECRET_ENV} must be that deployment's SESSION_SECRET_KEY (at least 32 characters).")
        os.environ["SESSION_SECRET_KEY"] = secret
    else:
        os.environ.pop("SESSION_SECRET_KEY", None)


def _refuse_unless_schema_at_head() -> None:
    """Like the backend itself: only a database at exactly this code's Alembic head."""
    from scripts.db_backup_restore import _engine, _revisions, code_heads

    engine = _engine(DATABASE_ENV)
    try:
        with engine.connect() as connection:
            revisions = _revisions(connection)
    except Exception:
        raise OperatorError("Could not read the database.") from None
    finally:
        engine.dispose()
    if revisions != code_heads():
        raise OperatorError("The database is not at this code's migration head. Run this tool from the commit that production runs.")


def _store() -> ChallengeStore:
    return ChallengeStore(Path(os.environ.get(STATE_DIR_ENV) or DEFAULT_STATE_DIR))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Delete a locked-out user's account after a mailbox challenge (docs/25 section 8.1).")
    commands = parser.add_subparsers(dest="command", required=True)
    challenge_parser = commands.add_parser("challenge", help="create a code to send to the account's own address")
    challenge_parser.add_argument("--hours", type=int, default=MAX_HOURS, help=f"validity, at most {MAX_HOURS}")
    challenge_parser.add_argument("--replace", action="store_true", help="cancel this account's open request first")
    for name in ("verify", "status"):
        commands.add_parser(name).add_argument("--request", required=True)
    delete_parser = commands.add_parser("delete", help="delete the account of a VERIFIED request")
    delete_parser.add_argument("--request", required=True)
    delete_parser.add_argument("--confirm-account", required=True, help="the account fingerprint the request shows")
    delete_parser.add_argument("--dry-run", action="store_true", help="check everything, delete nothing")
    arguments = parser.parse_args(argv)
    os.umask(0o077)
    now = datetime.now(timezone.utc)

    try:
        store = _store()
        if arguments.command == "status":
            c = store.load(arguments.request)
            print(f"Request {c.request_id}: {c.status}; account {c.fingerprint}; created {c.created_at}; expires {c.expires_at}; "
                  f"wrong or right attempts {c.attempts} of {MAX_ATTEMPTS}{'; ' + c.outcome if c.outcome else ''}.")
            return 0
        if arguments.command == "verify":
            challenge = verify_code(store, arguments.request, getpass.getpass("Code the requester sent back (not echoed): "), now=now)
            print(f"Request {challenge.request_id} verified for account {challenge.fingerprint}. It may be used for one deletion until {challenge.expires_at}.")
            return 0
        if arguments.command == "challenge":
            _bind_environment(need_database=True, need_secret=False)
            email = getpass.getpass("Email address of the account (not echoed): ")
            account = find_account(email)
            if account is None:
                print("No account uses that address. Send the requester the standard reply (it does not say this); send no code.")
                return 0
            challenge, code = create_challenge(store, user_id=account.user_id, email=email, now=now, hours=arguments.hours, replace=arguments.replace)
            print(f"Request {challenge.request_id} for account {challenge.fingerprint} ({account.trips} trip(s)); expires {challenge.expires_at}.")
            print(f"Code, to send ONLY to the account's own address and to keep nowhere else: {code}")
            return 0
        _bind_environment(need_database=True, need_secret=not arguments.dry_run)
        email = getpass.getpass("Email address of the account (not echoed): ")
        typed = "" if arguments.dry_run else input(f"Type exactly: DELETE {arguments.confirm_account}\n> ")
        return run_delete(store, arguments.request, email=email, confirm_account=arguments.confirm_account,
                          typed_confirmation=typed.strip(), dry_run=arguments.dry_run, now=now)
    except OperatorError as error:
        print(f"Refused: {error}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("Cancelled; nothing was deleted.", file=sys.stderr)
        return 130
    except Exception as error:  # the message is not shown: it may quote an address or a URL
        print(f"Failed with an unexpected {type(error).__name__}; nothing was deleted.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
