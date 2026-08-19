"""Access gate for the application.

Enforced on the server, not in the browser. The tool is published at a public
URL and spends an API key on every question, so a check that only hid the
interface would be a formality - the API is one curl away.

The password is read from the environment. The default below is committed to a
public repository and is therefore public knowledge: any instance that is
actually meant to be closed must set APP_PASSWORD.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time

DEFAULT_PASSWORD = "Studio2026"
COOKIE_NAME = "mi_access"
SESSION_HOURS = 12

# Signing key for the session cookie. Generated per process when not
# configured, so a restart invalidates outstanding sessions rather than the
# application trusting a key that anyone can read in the source.
_SECRET = os.getenv("APP_SECRET") or secrets.token_hex(32)


def password() -> str:
    return os.getenv("APP_PASSWORD") or DEFAULT_PASSWORD


def using_default_password() -> bool:
    """Whether this instance is open to anyone who has read the repository."""
    return password() == DEFAULT_PASSWORD


def _sign(payload: str) -> str:
    return hmac.new(_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def check_password(candidate: str | None) -> bool:
    # Compared in constant time so the comparison itself does not leak how much
    # of a guess was right.
    return hmac.compare_digest(str(candidate or ""), password())


def make_token() -> str:
    expires = str(int(time.time()) + SESSION_HOURS * 3600)
    return f"{expires}.{_sign(expires)}"


def valid(token: str | None) -> bool:
    """Whether a cookie was issued by this process and has not expired."""
    if not token or "." not in token:
        return False
    expires, _, signature = token.partition(".")
    if not hmac.compare_digest(signature, _sign(expires)):
        return False
    try:
        return int(expires) > time.time()
    except ValueError:
        return False
