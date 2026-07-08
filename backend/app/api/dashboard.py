"""GET /api/dashboard — flat, stable, API-key-protected status for external
dashboards (Homepage, Homarr, Dashy, Glance).

Independent of /api/status: this is a public contract (additive changes only),
with machine-style enum values that never get localized. Auth is a shared API
key sent via the ``X-API-Key`` header or a ``?key=`` query param — NOT the
session cookie — so this router is deliberately outside require_auth.
"""

from __future__ import annotations

import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core.config_store import ConfigStore
from ..db import get_session
from ..db.models import RunStatus
from . import _probe
from .deps import JobService, Scheduler, get_config_store, get_job_service, get_scheduler

router = APIRouter(tags=["dashboard"])


class DashboardResponse(BaseModel):
    pbs_state: str  # "sleeping" | "online" | "backing_up"
    next_run: datetime | None
    last_run_status: str  # "success" | "failed" | "never"
    last_run_time: datetime | None
    datastore_used_pct: float | None
    datastore_used_bytes: int | None
    datastore_total_bytes: int | None


def _authorize(request: Request, store: ConfigStore) -> None:
    key = store.config.app.api_key
    if not key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Dashboard integration is disabled (no API key configured)",
        )
    provided = request.headers.get("X-API-Key") or request.query_params.get("key") or ""
    if not secrets.compare_digest(provided.encode("utf-8"), key.encode("utf-8")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API key"
        )


@router.get("/dashboard", response_model=DashboardResponse)
def get_dashboard(
    request: Request,
    store: ConfigStore = Depends(get_config_store),
    scheduler: Scheduler = Depends(get_scheduler),
    job_service: JobService = Depends(get_job_service),
    session: Session = Depends(get_session),
) -> DashboardResponse:
    _authorize(request, store)

    config = store.config
    last = _probe.latest_finished_cycle_run(session)
    pbs_online, ds, _load = _probe.probe_pbs(config, job_service.deps.build_pbs)

    if job_service.is_running:
        pbs_state = "backing_up"
    elif pbs_online:
        pbs_state = "online"
    else:
        pbs_state = "sleeping"

    if last is None:
        last_run_status = "never"
    elif last.status == RunStatus.SUCCESS:
        last_run_status = "success"
    else:
        last_run_status = "failed"

    return DashboardResponse(
        pbs_state=pbs_state,
        next_run=scheduler.next_run_time,
        last_run_status=last_run_status,
        last_run_time=last.started_at if last else None,
        datastore_used_pct=ds.used_pct if ds else None,
        datastore_used_bytes=ds.used if ds else None,
        datastore_total_bytes=ds.total if ds else None,
    )
