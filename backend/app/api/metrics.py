"""GET /metrics — Prometheus exposition for Grafana homelabs (11.11).

Mounted at the root, not under ``/api``: ``/metrics`` is Prometheus's default
``metrics_path``, so a scrape config needs no extra setting. Auth is the same read-only
API key as ``/api/dashboard`` (header or ``?key=``), because a scraper can't hold a
session cookie.

The exposition format is plain text, so we write it directly rather than take on
``prometheus_client`` — a registry and its multiprocess machinery for what is a formatted
string here.

Everything below is read from the DB and the same status probe the dashboard uses: a
scrape never wakes the PBS, and reports cached datastore/last-backup values while it
sleeps (which is the normal state — that's the whole point of Joulenap).
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import __version__
from ..core.config_store import ConfigStore
from ..db import get_session
from ..db.guest_backups import list_last_backups
from ..db.models import Run, RunStatus
from . import _probe
from ._apikey import authorize_api_key
from .deps import JobService, Scheduler, get_config_store, get_job_service, get_scheduler

router = APIRouter(tags=["metrics"])

# The classic text format. Prometheus content-negotiates, but this is what every scraper
# understands and what `promtool check metrics` validates against.
CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

_PREFIX = "joulenap_"


def _escape(value: str) -> str:
    """Escape a label value per the exposition format (backslash, quote, newline)."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _number(value: float) -> str:
    """Render a sample value without losing precision.

    Deliberately not ``f"{value:g}"``: %g keeps 6 significant digits, which rounds a Unix
    timestamp (10 digits) to the nearest ~1000 seconds — every ``*_timestamp_seconds``
    series would have been silently wrong by up to a quarter of an hour. Integral values
    print as integers; anything else takes repr's shortest round-trip form.
    """
    f = float(value)
    if f.is_integer():
        return str(int(f))
    return repr(f)


class _Writer:
    """Accumulates metric families in exposition order."""

    def __init__(self) -> None:
        self._lines: list[str] = []

    def metric(
        self,
        name: str,
        help_text: str,
        kind: str,
        samples: list[tuple[dict[str, str], float]],
    ) -> None:
        """Emit one metric family. ``samples`` empty -> nothing at all is written.

        Omitting is deliberate: a missing last-run time must not be published as 0, which
        graphs as January 1970 and quietly poisons every average. Prometheus's answer to
        "no value" is an absent series (query it with ``absent()``), not a sentinel.
        """
        if not samples:
            return
        full = _PREFIX + name
        self._lines.append(f"# HELP {full} {help_text}")
        self._lines.append(f"# TYPE {full} {kind}")
        for labels, value in samples:
            rendered = ",".join(f'{k}="{_escape(v)}"' for k, v in labels.items())
            suffix = f"{{{rendered}}}" if rendered else ""
            self._lines.append(f"{full}{suffix} {_number(value)}")

    def gauge(self, name: str, help_text: str, value: float | None) -> None:
        """Single unlabelled gauge; ``None`` omits the series."""
        self.metric(name, help_text, "gauge", [] if value is None else [({}, value)])

    def render(self) -> str:
        return "\n".join(self._lines) + "\n"


def _epoch(dt: datetime | None) -> float | None:
    return dt.timestamp() if dt else None


@router.get("/metrics", include_in_schema=False)
def get_metrics(
    request: Request,
    store: ConfigStore = Depends(get_config_store),
    scheduler: Scheduler = Depends(get_scheduler),
    job_service: JobService = Depends(get_job_service),
    session: Session = Depends(get_session),
) -> Response:
    authorize_api_key(request, store)

    config = store.config
    w = _Writer()

    w.metric(
        "build_info",
        "Joulenap build information; the value is always 1.",
        "gauge",
        [({"version": __version__}, 1)],
    )

    # --- global state ---
    w.gauge(
        "scheduler_enabled",
        "1 if the scheduler kill-switch is on (routes may be armed).",
        int(config.app.scheduler_enabled),
    )
    w.gauge(
        "job_running",
        "1 while a backup, sync, GC or verify run is in flight.",
        int(job_service.is_running),
    )
    w.gauge("queued_runs", "Runs waiting in the queue behind the one in flight.",
            len(job_service.pending()))

    # --- per PBS: power, load, datastore ---
    # Every series below carries a pbs="<id>" label. The datastore values come from the
    # cache while the box sleeps, which is its normal state — a scrape never wakes anything.
    probes = _probe.probe_pbss(config, job_service.deps.connect_pbs)
    w.metric(
        "pbs_online",
        "1 if this PBS answers on its API port, 0 while asleep.",
        "gauge",
        [({"pbs": pbs.id}, int(probes[pbs.id].online)) for pbs in config.pbss if pbs.id in probes],
    )
    for name, help_text, pick in (
        ("pbs_cpu_percent", "PBS CPU usage percent (only while the PBS is awake).",
         lambda load: load.cpu),
        ("pbs_memory_percent", "PBS memory usage percent (only while the PBS is awake).",
         lambda load: load.mem),
        ("pbs_uptime_seconds", "PBS uptime in seconds (only while the PBS is awake).",
         lambda load: load.uptime),
    ):
        w.metric(name, help_text, "gauge", [
            ({"pbs": pbs_id}, pick(probe.load))
            for pbs_id, probe in probes.items()
            if probe.load is not None
        ])
    for name, help_text, pick in (
        ("datastore_used_bytes", "PBS datastore bytes used (last known value).",
         lambda ds: ds.used),
        ("datastore_total_bytes", "PBS datastore total bytes (last known value).",
         lambda ds: ds.total),
    ):
        w.metric(name, help_text, "gauge", [
            ({"pbs": pbs.id, "datastore": pbs.datastore}, pick(probes[pbs.id].datastore))
            for pbs in config.pbss
            if pbs.id in probes and probes[pbs.id].datastore is not None
        ])

    # --- per route: schedule + last outcome ---
    # A route with no armed job (disabled, or the kill-switch is off) simply has no
    # next_run series — absent, not zero, so alerts use absent() rather than a sentinel.
    w.metric(
        "route_next_run_timestamp_seconds",
        "Unix time this route next fires; absent when it isn't armed.",
        "gauge",
        [({"route": route_id}, when.timestamp()) for route_id, when in scheduler.next_runs()],
    )
    last_runs = {
        route.id: _probe.latest_finished_run(session, route.id) for route in config.routes
    }
    for name, help_text, pick in (
        ("route_last_run_timestamp_seconds",
         "Unix time this route's last finished run started.",
         lambda run: _epoch(run.started_at)),
        ("route_last_run_success",
         "1 if this route's last finished run succeeded, 0 if it failed or was aborted.",
         lambda run: int(run.status == RunStatus.SUCCESS)),
        ("route_last_run_duration_seconds",
         "Wall-clock duration of this route's last finished run.",
         lambda run: (run.finished_at - run.started_at).total_seconds() if run.finished_at
         else None),
        ("route_last_run_guests",
         "Number of guests backed up by this route's last finished run.",
         lambda run: run.guests_ok),
    ):
        w.metric(name, help_text, "gauge", [
            ({"route": route_id}, value)
            for route_id, run in last_runs.items()
            if run is not None and (value := pick(run)) is not None
        ])

    # --- per-guest freshness: the series worth alerting on ---
    # Labelled by vmid plus the PVE it lives on and the PBS holding the snapshot — a vmid
    # alone stopped being unique the moment a second PVE could exist. Guest *names* still
    # live on PVE, and fetching them would put an API call on every scrape to decorate a
    # label.
    w.metric(
        "guest_last_backup_timestamp_seconds",
        "Unix time of each guest's most recent snapshot, from Joulenap's cache.",
        "gauge",
        [
            ({"vmid": str(row.vmid), "pve": row.pve_id, "pbs": row.pbs_id},
             row.last_backup.timestamp())
            for row in list_last_backups(session)
        ],
    )

    # --- run history ---
    # A gauge, NOT a `_total` counter: the daily prune job deletes runs older than
    # maintenance.history.retention_days, so this legitimately goes down and would break
    # every rate()/increase() over a counter.
    rows = session.execute(
        select(Run.kind, Run.status, func.count(Run.id))
        .where(Run.status != RunStatus.RUNNING)
        .group_by(Run.kind, Run.status)
    ).all()
    w.metric(
        "runs_recent",
        "Finished runs currently in the history window, by kind and status.",
        "gauge",
        [({"kind": kind, "status": status}, count) for kind, status, count in rows],
    )

    return Response(content=w.render(), media_type=CONTENT_TYPE)
