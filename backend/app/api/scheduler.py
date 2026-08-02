"""POST /api/scheduler/toggle — the global kill-switch (applies immediately).

Off arms nothing at all: no route fires on its schedule, while manual runs still work.
Pausing one route is ``routes[].enabled``, edited through /api/routes.

Per the UI convention only this master switch applies instantly; text fields are saved
with the explicit "Apply changes" PUT /api/config.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..core.config_store import ConfigStore
from .deps import Scheduler, get_config_store, get_scheduler, require_auth

router = APIRouter(dependencies=[Depends(require_auth)], tags=["scheduler"])


class TogglePayload(BaseModel):
    enabled: bool


class NextRun(BaseModel):
    route_id: str
    at: datetime


class ToggleResponse(BaseModel):
    enabled: bool
    #: What is armed after the flip — empty when the switch has just been turned off.
    next_runs: list[NextRun]


@router.post("/scheduler/toggle", response_model=ToggleResponse)
def toggle_scheduler(
    payload: TogglePayload,
    store: ConfigStore = Depends(get_config_store),
    scheduler: Scheduler = Depends(get_scheduler),
) -> ToggleResponse:
    def apply(cfg) -> None:
        cfg.app.scheduler_enabled = payload.enabled

    new_config = store.update(apply)
    scheduler.rearm(new_config)
    return ToggleResponse(
        enabled=payload.enabled,
        next_runs=[
            NextRun(route_id=route_id, at=when) for route_id, when in scheduler.next_runs()
        ],
    )
