"""GET/PUT /api/config — read the redacted config; apply changes and re-arm.

This is the design's "Apply changes" action: validate the whole config, persist it, then
re-arm the scheduler so a new schedule/enabled flag takes effect immediately.
"""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError

from ..config import (
    Config,
    RedactionError,
    deep_merge,
    enforce_server_managed,
    redacted_dict,
    restore_secrets,
)
from ..core.config_store import ConfigStore
from .deps import Scheduler, get_config_store, get_scheduler, require_auth

router = APIRouter(dependencies=[Depends(require_auth)], tags=["config"])


@router.get("/config")
def get_config(store: ConfigStore = Depends(get_config_store)) -> dict[str, Any]:
    return redacted_dict(store.config)


@router.put("/config")
def put_config(
    incoming: dict[str, Any],
    store: ConfigStore = Depends(get_config_store),
    scheduler: Scheduler = Depends(get_scheduler),
) -> dict[str, Any]:
    # Deep-merge over the stored config so PUT means "apply these changes", not "replace
    # everything": an omitted section/field keeps its current value (a partial body can no
    # longer wipe secrets). Then resolve any ***REDACTED*** the client echoed back, and force
    # server-managed secrets (secret_key, password_hash, api_key) to the stored values.
    base = store.config.model_dump(mode="python")
    try:
        merged = restore_secrets(deep_merge(base, incoming), store.config)
    except RedactionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    merged = enforce_server_managed(merged, store.config)
    try:
        new_config = Config.model_validate(merged)
    except ValidationError as exc:
        # 422 to mirror FastAPI's own body-validation responses (literal avoids the
        # deprecated HTTP_422_UNPROCESSABLE_ENTITY constant name).
        raise HTTPException(status_code=422, detail=jsonable_encoder(exc.errors())) from exc

    try:
        store.replace(new_config)
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc

    scheduler.rearm(new_config)
    return redacted_dict(new_config)


@router.post("/config/api-key", status_code=status.HTTP_200_OK)
def generate_api_key(store: ConfigStore = Depends(get_config_store)) -> dict[str, str]:
    """Generate (or rotate) the dashboard integration key; returns it once."""
    key = secrets.token_urlsafe(32)
    try:
        store.update(lambda c: setattr(c.app, "api_key", key))
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc
    return {"api_key": key}


@router.delete("/config/api-key", status_code=status.HTTP_204_NO_CONTENT)
def delete_api_key(store: ConfigStore = Depends(get_config_store)) -> None:
    """Clear the dashboard integration key (disables GET /api/dashboard)."""
    try:
        store.update(lambda c: setattr(c.app, "api_key", ""))
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc
