"""Console auth: a single shared admin secret, hashed; signed session; CSRF tokens.

The password is stored as a parameterized scrypt hash (stdlib — no C-extension dep),
never plaintext. `secretary console-hash` generates the value the operator pastes into
`SECRETARY_CONSOLE_PASSWORD`. An empty configured password means admin is hard-disabled:
the login route and all mutations 404, so a misconfigured public deploy cannot write.

Lost-password recovery is the filesystem: whoever controls `.env` resets the value and
restarts. A single-admin self-hosted tool needs no account-recovery flow.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from secretary.config import Settings

# scrypt cost parameters — sane for a 1-2 GB VM, measured once at hash time.
_N = 32768
_R = 8
_P = 1
_DKLEN = 32
_PREFIX = "scrypt"
# scrypt needs ~128*r*n bytes (≈32 MiB here); OpenSSL's default maxmem (0) rejects that,
# so set a headroom ceiling. ~32 MiB per login attempt is fine on a 1-2 GB VM.
_MAXMEM = 132 * 1024 * 1024


def hash_password(password: str) -> str:
    """Return `scrypt$v=1$n=..$r=..$p=..$salt_hex$hash_hex` for `password`."""
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN, maxmem=_MAXMEM)
    return f"{_PREFIX}$v=1$n={_N}$r={_R}$p={_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verify against a stored scrypt hash. Fails closed on malformed input."""
    try:
        prefix, ver, n_part, r_part, p_part, salt_hex, hash_hex = stored.split("$")
        if prefix != _PREFIX or ver != "v=1":
            return False
        n = int(n_part.removeprefix("n="))
        r = int(r_part.removeprefix("r="))
        p = int(p_part.removeprefix("p="))
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        dk = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p,
                            dklen=len(expected), maxmem=_MAXMEM)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk, expected)


def admin_enabled(settings: Settings) -> bool:
    """Whether admin is configured at all (a password hash is present)."""
    return bool(settings.console_password.strip())


def check_login(settings: Settings, password: str) -> bool:
    """Verify a login attempt; always False when admin is disabled."""
    return admin_enabled(settings) and verify_password(password, settings.console_password.strip())


# --- session + CSRF helpers (operate on a Starlette request's session dict) ----

_CSRF_KEY = "csrf"


def is_admin(session: dict) -> bool:
    return bool(session.get("admin"))


def issue_csrf(session: dict) -> str:
    """Return the session CSRF token, minting one on first use."""
    token = session.get(_CSRF_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[_CSRF_KEY] = token
    return token


def check_csrf(session: dict, token: str | None) -> bool:
    expected = session.get(_CSRF_KEY)
    return bool(expected) and bool(token) and hmac.compare_digest(str(expected), str(token))
