"""GET /api/status — everything the homepage polls: the state pill, the queue, the
upcoming runs, and one entry per device for the topology.

Describes *Joulenap's* state, not a PBS's: with several backup servers "on/off" is no
longer a single fact, so the pill says what the app is doing and the per-device rows carry
the rest.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import config as config_mod
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


class PveState(BaseModel):
    id: str
    online: bool


class PbsState(BaseModel):
    id: str
    online: bool
    managed_power: bool
    #: How many runs currently hold this box's power lease. > 0 means the ⏻ button must be
    #: disabled: a run is using it.
    holders: int
    datastore: DatastoreInfo | None = None
    load: LoadInfo | None = None


class RunningRun(BaseModel):
    run_id: int
    kind: str
    started_at: datetime
    route_id: str | None = None
    route_name: str | None = None


class QueuedRunInfo(BaseModel):
    #: The queue key: a route id, or ``pbs:<id>:gc`` for an ad-hoc maintenance run.
    key: str
    route_id: str | None = None
    pbs_id: str | None = None


class NextRun(BaseModel):
    route_id: str
    route_name: str
    at: datetime


class StatusResponse(BaseModel):
    #: The header pill: "running" (a run is in flight), "paused" (kill-switch off) or
    #: "idle". Deliberately a small closed set — the frontend localizes it, never shows it.
    state: str
    scheduler_enabled: bool
    running: RunningRun | None = None
    queued: list[QueuedRunInfo] = []
    #: Every armed route's next fire, soonest first.
    next_runs: list[NextRun] = []
    pves: list[PveState] = []
    pbss: list[PbsState] = []
    last_run: RunSummary | None = None
    #: Set when the 0.9 -> 1.0 config migration was refused at startup: the config in use
    #: has no devices and no routes, so the UI must say why instead of looking like a fresh
    #: install. Rendered as a persistent banner under the header.
    config_error: str | None = None


@router.get("/status", response_model=StatusResponse)
def get_status(
    store: ConfigStore = Depends(get_config_store),
    scheduler: Scheduler = Depends(get_scheduler),
    job_service: JobService = Depends(get_job_service),
    session: Session = Depends(get_session),
) -> StatusResponse:
    config = store.config
    running = _probe.running_run(session)
    last = _probe.latest_finished_run(session)
    pbs_probes = _probe.probe_pbss(config, job_service.deps.connect_pbs)
    pve_online = _probe.probe_pves(config)
    names = {r.id: (r.name or r.id) for r in config.routes}

    if running is not None:
        state = "running"
    elif not config.app.scheduler_enabled:
        state = "paused"
    else:
        state = "idle"

    return StatusResponse(
        state=state,
        scheduler_enabled=config.app.scheduler_enabled,
        running=(
            RunningRun(
                run_id=running.id,
                kind=running.kind,
                started_at=running.started_at,
                route_id=running.route_id,
                route_name=running.route_name,
            )
            if running
            else None
        ),
        queued=[
            QueuedRunInfo(key=item.key, route_id=item.route_id, pbs_id=item.pbs_id)
            for item in job_service.pending()
        ],
        next_runs=[
            NextRun(route_id=route_id, route_name=names.get(route_id, route_id), at=when)
            for route_id, when in scheduler.next_runs()
        ],
        pves=[PveState(id=pve.id, online=pve_online.get(pve.id, False)) for pve in config.pves],
        pbss=[_pbs_state(pbs, pbs_probes, job_service) for pbs in config.pbss],
        last_run=RunSummary.of(last, config.app.language) if last else None,
        # Read live, not captured at import: a reload after the user fixes the file clears it.
        config_error=config_mod.MIGRATION_ERROR,
    )


def _pbs_state(pbs, probes: dict[str, _probe.PbsProbe], job_service: JobService) -> PbsState:
    probe = probes.get(pbs.id)
    ds = probe.datastore if probe else None
    load = probe.load if probe else None
    return PbsState(
        id=pbs.id,
        online=bool(probe and probe.online),
        managed_power=pbs.managed_power,
        holders=job_service.lease.state(pbs).holders,
        datastore=DatastoreInfo(used=ds.used, total=ds.total, used_pct=ds.used_pct) if ds else None,
        load=LoadInfo(cpu=load.cpu, mem=load.mem, uptime=load.uptime) if load else None,
    )
