"""GET /api/status — dashboard summary: scheduler state, current/last run, PBS power."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core.config_store import ConfigStore
from ..db import get_session
from . import _probe
from .deps import (
    JobService,
    Scheduler,
    get_config_store,
    get_job_service,
    get_scheduler,
    require_auth,
)
from .schemas import RunSummary

router = APIRouter(dependencies=[Depends(require_auth)], tags=["status"])


class DatastoreInfo(BaseModel):
    used: int  # bytes
    total: int
    used_pct: float


class LoadInfo(BaseModel):
    cpu: int  # percent, 0-100
    mem: int  # percent, 0-100
    uptime: int  # seconds since the PBS booted


class StatusResponse(BaseModel):
    scheduler_enabled: bool
    schedule: str
    next_run: datetime | None
    job_running: bool
    running_kind: str | None = None  # "cycle" | "gc" | "verify" while a run is in flight
    pbs_online: bool
    last_run: RunSummary | None
    datastore: DatastoreInfo | None = None
    load: LoadInfo | None = None


@router.get("/status", response_model=StatusResponse)
def get_status(
    store: ConfigStore = Depends(get_config_store),
    scheduler: Scheduler = Depends(get_scheduler),
    job_service: JobService = Depends(get_job_service),
    session: Session = Depends(get_session),
) -> StatusResponse:
    config = store.config
    last = _probe.latest_cycle_run(session)
    running = _probe.running_run(session)
    pbs_online, live_ds, nl = _probe.probe_pbs(config, job_service.deps.build_pbs)
    ds = _probe.resolve_datastore(config.pbs.datastore, live_ds)

    datastore = (
        DatastoreInfo(used=ds.used, total=ds.total, used_pct=ds.used_pct) if ds else None
    )
    load = LoadInfo(cpu=nl.cpu, mem=nl.mem, uptime=nl.uptime) if nl else None

    return StatusResponse(
        scheduler_enabled=config.backup.enabled,
        schedule=config.backup.schedule,
        next_run=scheduler.next_run_time,
        job_running=job_service.is_running,
        running_kind=running.kind if running else None,
        pbs_online=pbs_online,
        last_run=RunSummary.of(last) if last else None,
        datastore=datastore,
        load=load,
    )
