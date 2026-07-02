"""Authentication endpoints: first-run setup, login, logout, current user.

Mirrors the design's auth screen: a fresh install (empty password_hash) shows
"Create the first account"; afterwards it's a normal login.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..core import security
from ..core.config_store import ConfigStore
from ..core.security import get_config_store
from .deps import get_scheduler

router = APIRouter(tags=["auth"])


class Credentials(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class SetupRequest(BaseModel):
    # First-run minimums mirror the prototype's client-side validation.
    username: str = Field(min_length=3)
    password: str = Field(min_length=4)
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
    scheduler=Depends(get_scheduler),
) -> UserInfo:
    if not security.setup_needed(store):
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
    security.login_session(request, body.username)
    return UserInfo(username=body.username)


@router.post("/login", response_model=UserInfo)
def login(
    body: Credentials,
    request: Request,
    store: ConfigStore = Depends(get_config_store),
) -> UserInfo:
    if security.setup_needed(store):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No account configured yet — complete setup first",
        )
    auth = store.config.app.auth
    if body.username != auth.username or not security.verify_password(
        body.password, auth.password_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect username or password"
        )
    security.login_session(request, auth.username)
    return UserInfo(username=auth.username)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request) -> None:
    security.logout_session(request)


@router.get("/auth/me", response_model=UserInfo)
def me(user: str = security.CurrentUser) -> UserInfo:
    return UserInfo(username=user)


class AccountUpdate(BaseModel):
    username: str = Field(min_length=3)
    # Optional: empty/omitted keeps the current password (the design's "leave empty").
    password: str | None = Field(default=None, min_length=4)


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
