from __future__ import annotations

from app.auth.passwords import hash_password, verify_password

# Tests for Step 184B's password hashing utility
# (backend/app/auth/passwords.py). Not imported by any route yet.


def test_hash_password_returns_a_salted_hash_not_the_plaintext() -> None:
    plain = "correct horse battery staple"
    hashed = hash_password(plain)

    assert hashed != plain
    assert isinstance(hashed, str)
    # bcrypt's own format marker -- confirms this is a real bcrypt hash,
    # not some other/no-op transformation.
    assert hashed.startswith("$2")


def test_hash_password_produces_a_different_hash_each_time() -> None:
    """Salted -- two hashes of the same password must differ."""
    plain = "correct horse battery staple"
    assert hash_password(plain) != hash_password(plain)


def test_verify_password_returns_true_for_correct_password() -> None:
    plain = "correct horse battery staple"
    hashed = hash_password(plain)
    assert verify_password(plain, hashed) is True


def test_verify_password_returns_false_for_wrong_password() -> None:
    hashed = hash_password("correct horse battery staple")
    assert verify_password("wrong password", hashed) is False


def test_verify_password_returns_false_for_malformed_hash_not_an_exception() -> None:
    """A corrupt/malformed stored hash must fail closed -- never raise."""
    assert verify_password("anything", "not-a-real-bcrypt-hash") is False
    assert verify_password("anything", "") is False


def test_hash_password_rejects_password_over_bcrypt_byte_limit() -> None:
    too_long = "a" * 73
    try:
        hash_password(too_long)
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_verify_password_returns_false_rather_than_raising_for_over_limit_password() -> None:
    hashed = hash_password("a normal password")
    too_long = "a" * 100
    assert verify_password(too_long, hashed) is False
