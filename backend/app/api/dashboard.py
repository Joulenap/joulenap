"""GET /api/dashboard — flat, stable, API-key-protected status for external
dashboards (Homepage, Homarr, Dashy, Glance).

Independent of /api/status: this is a public contract (additive changes only),
with machine-style enum values that never get localized. Auth is a shared API
key sent via the ``X-API-Key`` header or a ``?key=`` query param — NOT the
session cookie — so this router is deliberately outside require_auth.

**Changed in 1.0.0**: the flat single-PBS fields became two lists, one entry per route and
one per PBS. There is no longer a single "next run" or "datastore" to report, so widgets
built on 0.9's shape pick a list entry (``.routes[0].next_run``) instead.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core.config_store import ConfigStore
from ..db import get_session
from ..db.models import RunStatus
from . import _probe
from ._apikey import authorize_api_key
from .deps import JobService, Scheduler, get_config_store, get_job_service, get_scheduler

router = APIRouter(tags=["dashboard"])


class RouteEntry(BaseModel):
    id: str
    name: str
    kind: str  # "backup" | "sync" | "external" | "verify"
    enabled: bool
    next_run: datetime | None
    last_run_status: str  # "success" | "failed" | "never"
    last_run_time: datetime | None


class PbsEntry(BaseModel):
    id: str
    state: str  # "sleeping" | "online" | "backing_up"
    datastore_used_pct: float | None
    datastore_used_bytes: int | None
    datastore_total_bytes: int | None


class DashboardResponse(BaseModel):
    state: str  # "idle" | "running" | "paused"
    routes: list[RouteEntry]
    pbss: list[PbsEntry]


@router.get("/dashboard", response_model=DashboardResponse)
def get_dashboard(
    request: Request,
    store: ConfigStore = Depends(get_config_store),
    scheduler: Scheduler = Depends(get_scheduler),
    job_service: JobService = Depends(get_job_service),
    session: Session = Depends(get_session),
) -> DashboardResponse:
    authorize_api_key(request, store)

    config = store.config
    probes = _probe.probe_pbss(config, job_service.deps.connect_pbs)
    running = _probe.running_run(session)
    busy_pbs = _busy_pbs_ids(config, job_service)

    if running is not None:
        state = "running"
    elif not config.app.scheduler_enabled:
        state = "paused"
    else:
        state = "idle"

    routes = []
    for route in config.routes:
        last = _probe.latest_finished_run(session, route.id)
        routes.append(
            RouteEntry(
                id=route.id,
                name=route.name or route.id,
                kind=route.kind,
                enabled=route.enabled,
                next_run=scheduler.next_run_time(route.id),
                last_run_status=_status_word(last),
                last_run_time=last.started_at if last else None,
            )
        )

    pbss = []
    for pbs in config.pbss:
        probe = probes.get(pbs.id)
        ds = probe.datastore if probe else None
        if pbs.id in busy_pbs:
            # "backing_up" is reported for *any* active run on this box — a GC-only or verify
            # run included. It's a coarse "the box is awake and working" signal; the value is
            # a frozen public-contract enum (see module docstring), so we don't split it.
            pbs_state = "backing_up"
        elif probe and probe.online:
            pbs_state = "online"
        else:
            pbs_state = "sleeping"
        pbss.append(
            PbsEntry(
                id=pbs.id,
                state=pbs_state,
                datastore_used_pct=ds.used_pct if ds else None,
                datastore_used_bytes=ds.used if ds else None,
                datastore_total_bytes=ds.total if ds else None,
            )
        )

    return DashboardResponse(state=state, routes=routes, pbss=pbss)


def _status_word(last) -> str:
    if last is None:
        return "never"
    return "success" if last.status == RunStatus.SUCCESS else "failed"


def _busy_pbs_ids(config, job_service: JobService) -> set[str]:
    """Which PBS devices a run is currently holding — the power lease is the one place that
    knows, and it knows per device rather than "is anything running at all"."""
    return {pbs.id for pbs in config.pbss if job_service.lease.state(pbs.id).holders}
