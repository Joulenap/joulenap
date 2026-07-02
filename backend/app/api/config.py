"""GET/PUT /api/config — read the redacted config; apply changes and re-arm.

This is the design's "Apply changes" action: validate the whole config, persist it, then
re-arm the scheduler so a new schedule/enabled flag takes effect immediately.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError

from ..config import Config, redacted_dict, restore_secrets
from ..core.config_store import ConfigStore
from .deps import get_config_store, get_scheduler, require_auth

router = APIRouter(dependencies=[Depends(require_auth)], tags=["config"])


@router.get("/config")
def get_config(store: ConfigStore = Depends(get_config_store)) -> dict[str, Any]:
    return redacted_dict(store.config)


@router.put("/config")
def put_config(
    incoming: dict[str, Any],
    store: ConfigStore = Depends(get_config_store),
    scheduler=Depends(get_scheduler),
) -> dict[str, Any]:
    # Restore any secret the client left as ***REDACTED*** before validating.
    merged = restore_secrets(incoming, store.config)
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
