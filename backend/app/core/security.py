"""Password hashing and session-based authentication.

Credentials are a single admin user stored in config (``app.auth``): username plus a
bcrypt ``password_hash``. Login puts the username in a signed session cookie
(Starlette ``SessionMiddleware``); ``require_auth`` guards protected routes.
"""

from __future__ import annotations

import bcrypt
from fastapi import Depends, HTTPException, Request, status

from .config_store import ConfigStore

# bcrypt operates on at most 72 bytes; longer passwords are truncated by the algorithm.
_BCRYPT_MAX_BYTES = 72

_SESSION_USER_KEY = "user"


def hash_password(password: str) -> str:
    pw = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(pw, bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    pw = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    try:
        return bcrypt.checkpw(pw, password_hash.encode("ascii"))
    except (ValueError, TypeError):
        # Malformed/non-bcrypt hash in config — treat as a failed check, not a crash.
        return False


# --- session helpers ---------------------------------------------------------


def login_session(request: Request, username: str) -> None:
    request.session[_SESSION_USER_KEY] = username


def logout_session(request: Request) -> None:
    request.session.pop(_SESSION_USER_KEY, None)


def session_user(request: Request) -> str | None:
    return request.session.get(_SESSION_USER_KEY)


# --- dependencies ------------------------------------------------------------


def get_config_store(request: Request) -> ConfigStore:
    return request.app.state.config_store


def setup_needed(store: ConfigStore) -> bool:
    """True when no admin password is set yet (first-run registration required)."""
    return not store.config.app.auth.password_hash


def require_auth(request: Request) -> str:
    """FastAPI dependency: return the logged-in username or raise 401."""
    user = session_user(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required"
        )
    return user


CurrentUser = Depends(require_auth)
