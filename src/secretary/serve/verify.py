"""HMAC-SHA256 verification of GitHub's `X-Hub-Signature-256` header.

Pure and socket-free so it is fully unit-testable. GitHub signs the raw request
body with the shared secret and sends `sha256=<hexdigest>`; we recompute and
compare in constant time.
"""

from __future__ import annotations

import hashlib
import hmac

_PREFIX = "sha256="


def verify_signature(body: bytes, header: str | None, secret: str) -> bool:
    """True iff `header` is a valid `sha256=<hexdigest>` HMAC of `body` under `secret`.

    Returns False for a missing/empty header, an empty secret, a wrong algorithm
    prefix, or any mismatch. The comparison is constant-time.
    """
    if not secret or not header or not header.startswith(_PREFIX):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    received = header[len(_PREFIX):]
    return hmac.compare_digest(expected, received)
