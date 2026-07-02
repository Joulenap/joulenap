"""POST /api/scheduler/toggle — flip the backup job on/off (applies immediately).

Per the UI convention only this master switch applies instantly; text fields are saved
with the explicit "Apply changes" PUT /api/config.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..core.config_store import ConfigStore
from .deps import get_config_store, get_scheduler, require_auth

router = APIRouter(dependencies=[Depends(require_auth)], tags=["scheduler"])


class TogglePayload(BaseModel):
    enabled: bool


class ToggleResponse(BaseModel):
    enabled: bool
    next_run: datetime | None


@router.post("/scheduler/toggle", response_model=ToggleResponse)
def toggle_scheduler(
    payload: TogglePayload,
    store: ConfigStore = Depends(get_config_store),
    scheduler=Depends(get_scheduler),
) -> ToggleResponse:
    def apply(cfg) -> None:
        cfg.backup.enabled = payload.enabled

    new_config = store.update(apply)
    scheduler.rearm(new_config)
    return ToggleResponse(enabled=payload.enabled, next_run=scheduler.next_run_time)
