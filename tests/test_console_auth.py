"""Console auth: scrypt hashing, login gating, CSRF tokens."""

from __future__ import annotations

from secretary.config import Settings
from secretary.console import auth


def test_hash_is_parseable_and_round_trips():
    h = auth.hash_password("hunter2")
    assert h.startswith("scrypt$v=1$n=")
    assert auth.verify_password("hunter2", h)
    assert not auth.verify_password("wrong", h)


def test_verify_fails_closed_on_malformed_hash():
    assert auth.verify_password("x", "") is False
    assert auth.verify_password("x", "not-a-hash") is False
    assert auth.verify_password("x", "scrypt$v=1$n=bad$r=8$p=1$zz$zz") is False


def test_admin_disabled_when_password_empty():
    s = Settings(github_repo="o/r", console_password="")
    assert auth.admin_enabled(s) is False
    assert auth.check_login(s, "anything") is False


def test_check_login_against_configured_hash():
    h = auth.hash_password("s3cret")
    s = Settings(github_repo="o/r", console_password=h)
    assert auth.check_login(s, "s3cret") is True
    assert auth.check_login(s, "nope") is False


def test_csrf_token_is_stable_per_session_and_checked():
    session: dict = {}
    token = auth.issue_csrf(session)
    assert token and auth.issue_csrf(session) == token  # stable
    assert auth.check_csrf(session, token) is True
    assert auth.check_csrf(session, "forged") is False
    assert auth.check_csrf({}, token) is False  # no token in session
