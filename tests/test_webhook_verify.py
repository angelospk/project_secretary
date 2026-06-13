import hashlib
import hmac

from secretary.serve.verify import verify_signature

SECRET = "it's a secret to everybody"
BODY = b'{"action":"opened"}'


def _sig(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_valid_signature_passes():
    assert verify_signature(BODY, _sig(BODY, SECRET), SECRET) is True


def test_tampered_body_fails():
    assert verify_signature(b'{"action":"closed"}', _sig(BODY, SECRET), SECRET) is False


def test_wrong_secret_fails():
    assert verify_signature(BODY, _sig(BODY, "wrong"), SECRET) is False


def test_missing_header_fails():
    assert verify_signature(BODY, None, SECRET) is False
    assert verify_signature(BODY, "", SECRET) is False


def test_malformed_header_fails():
    # Right digest, wrong/absent algorithm prefix.
    digest = hmac.new(SECRET.encode(), BODY, hashlib.sha256).hexdigest()
    assert verify_signature(BODY, digest, SECRET) is False           # no "sha256=" prefix
    assert verify_signature(BODY, f"sha1={digest}", SECRET) is False  # wrong algo


def test_empty_secret_never_verifies():
    # An empty secret must not authenticate anything — serve refuses to start with one,
    # but verify is defensive too.
    assert verify_signature(BODY, _sig(BODY, ""), "") is False
