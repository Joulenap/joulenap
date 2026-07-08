"""Authentication endpoints: first-run setup, login, logout, current user.

Mirrors the design's auth screen: a fresh install (empty password_hash) shows
"Create the first account"; afterwards it's a normal login.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

from ..core import security
from ..core.config_store import ConfigStore
from ..core.security import get_config_store
from .deps import LoginRateLimiter, Scheduler, get_login_limiter, get_scheduler

router = APIRouter(tags=["auth"])


class Credentials(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class SetupRequest(BaseModel):
    # First-run minimums mirror the prototype's client-side validation.
    username: str = Field(min_length=3)
    password: str = Field(min_length=8)
    # IANA timezone the first-run screen detects from the browser (empty => keep the
    # config default: TZ env, then UTC). An invalid name is tolerated — the scheduler
    # falls back to UTC (see core/scheduler.resolve_timezone).
    timezone: str = ""


class AuthStatus(BaseModel):
    setup_needed: bool
    authenticated: bool
    username: str | None = None


class UserInfo(BaseModel):
    username: str


@router.get("/auth/status", response_model=AuthStatus)
def auth_status(request: Request, store: ConfigStore = Depends(get_config_store)) -> AuthStatus:
    user = security.session_user(request)
    return AuthStatus(
        setup_needed=security.setup_needed(store),
        authenticated=user is not None,
        username=user,
    )


@router.post("/auth/setup", response_model=UserInfo, status_code=status.HTTP_201_CREATED)
def setup(
    body: SetupRequest,
    request: Request,
    store: ConfigStore = Depends(get_config_store),
    scheduler: Scheduler = Depends(get_scheduler),
    limiter: LoginRateLimiter = Depends(get_login_limiter),
) -> UserInfo:
    ip = request.client.host if request.client else "unknown"
    remaining = limiter.locked_for(ip)
    if remaining > 0:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many attempts — try again in {int(remaining) + 1}s",
        )
    if not security.setup_needed(store):
        limiter.record_failure(ip)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="An account already exists"
        )
    password_hash = security.hash_password(body.password)
    timezone = body.timezone.strip()

    def apply(cfg) -> None:
        cfg.app.auth.username = body.username
        cfg.app.auth.password_hash = password_hash
        if timezone:
            cfg.app.timezone = timezone

    store.update(apply)
    # Re-arm so the chosen timezone drives the (default) backup schedule right away,
    # rather than only after the user next saves config.
    if timezone:
        scheduler.rearm(store.config)
    limiter.reset(ip)
    security.login_session(request, body.username, password_hash)
    return UserInfo(username=body.username)


@router.post("/login", response_model=UserInfo)
def login(
    body: Credentials,
    request: Request,
    store: ConfigStore = Depends(get_config_store),
    limiter: LoginRateLimiter = Depends(get_login_limiter),
) -> UserInfo:
    ip = request.client.host if request.client else "unknown"
    remaining = limiter.locked_for(ip)
    if remaining > 0:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many attempts — try again in {int(remaining) + 1}s",
        )
    if security.setup_needed(store):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No account configured yet — complete setup first",
        )
    auth = store.config.app.auth
    # Always run the (slow) hash check, even on a wrong username, so response time doesn't
    # reveal whether the username exists (avoids a timing-based enumeration oracle).
    user_ok = body.username == auth.username
    pw_ok = security.verify_password(body.password, auth.password_hash)
    if not (user_ok and pw_ok):
        limiter.record_failure(ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect username or password"
        )
    limiter.reset(ip)
    security.login_session(request, auth.username, auth.password_hash)
    return UserInfo(username=auth.username)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request) -> None:
    security.logout_session(request)


@router.get("/auth/me", response_model=UserInfo)
def me(user: str = security.CurrentUser) -> UserInfo:
    return UserInfo(username=user)


class AccountUpdate(BaseModel):
    username: str = Field(min_length=3)
    # Optional: empty string / null / omitted all mean "keep the current password"
    # (the design's "leave empty"). Length is enforced only for a real new password.
    password: str | None = None

    @field_validator("password")
    @classmethod
    def _min_len_when_set(cls, v: str | None) -> str | None:
        if v and len(v) < 8:
            raise ValueError("password must be at least 8 characters")
        return v


@router.put("/account", response_model=UserInfo)
def update_account(
    body: AccountUpdate,
    _user: str = security.CurrentUser,
    store: ConfigStore = Depends(get_config_store),
) -> UserInfo:
    """Change the admin username and (optionally) password. Auth-guarded."""
    password_hash = security.hash_password(body.password) if body.password else None

    def apply(cfg) -> None:
        cfg.app.auth.username = body.username
        if password_hash is not None:
            cfg.app.auth.password_hash = password_hash

    store.update(apply)
    return UserInfo(username=body.username)
