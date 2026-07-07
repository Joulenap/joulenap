"""GET /api/status — dashboard summary: scheduler state, current/last run, PBS power."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..connectors import net
from ..connectors.errors import ConnectorError
from ..core.config_store import ConfigStore
from ..db import get_session
from ..db.models import Run, RunKind
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

# Keep the reachability probe snappy — the dashboard polls this and the PBS is usually off.
_PBS_PROBE_TIMEOUT = 1.0


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
    # "Last run" = the most recent backup cycle (manual or scheduled). Filter to CYCLE so a
    # standalone manual GC run doesn't masquerade as the last backup.
    last = session.scalars(
        select(Run)
        .where(Run.kind == RunKind.CYCLE)
        .order_by(Run.started_at.desc())
        .limit(1)
    ).first()

    pbs = config.pbs
    pbs_online = bool(pbs.host) and net.tcp_reachable(pbs.host, pbs.port, _PBS_PROBE_TIMEOUT)

    datastore: DatastoreInfo | None = None
    load: LoadInfo | None = None
    if pbs_online:
        # Best-effort: a transient PBS/API hiccup shouldn't fail the whole status call.
        try:
            with job_service.deps.build_pbs(config) as client:
                ds = client.datastore_status()
                datastore = DatastoreInfo(used=ds.used, total=ds.total, used_pct=ds.used_pct)
                nl = client.node_status()
                load = LoadInfo(cpu=nl.cpu, mem=nl.mem, uptime=nl.uptime)
        except ConnectorError:
            pass

    return StatusResponse(
        scheduler_enabled=config.backup.enabled,
        schedule=config.backup.schedule,
        next_run=scheduler.next_run_time,
        job_running=job_service.is_running,
        pbs_online=pbs_online,
        last_run=RunSummary.of(last) if last else None,
        datastore=datastore,
        load=load,
    )
