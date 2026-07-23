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
from ..db.guest_backups import get_last_backups
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

    # --- power + scheduler state ---
    pbs_online, live_ds, load = _probe.probe_pbs(config, job_service.deps.build_pbs)
    w.gauge("pbs_online", "1 if the PBS answers on its API port, 0 while asleep.", int(pbs_online))
    w.gauge(
        "scheduler_enabled",
        "1 if the scheduled backup job is armed.",
        int(config.backup.enabled),
    )
    w.gauge(
        "job_running",
        "1 while a backup, GC or verify run is in flight.",
        int(job_service.is_running),
    )
    w.gauge(
        "next_run_timestamp_seconds",
        "Unix time of the next scheduled backup; absent when the scheduler is off.",
        _epoch(scheduler.next_run_time),
    )

    # PBS load is only meaningful while the box is awake, so those series come and go.
    w.gauge("pbs_cpu_percent", "PBS CPU usage percent (only while the PBS is awake).",
            load.cpu if load else None)
    w.gauge("pbs_memory_percent", "PBS memory usage percent (only while the PBS is awake).",
            load.mem if load else None)
    w.gauge("pbs_uptime_seconds", "PBS uptime in seconds (only while the PBS is awake).",
            load.uptime if load else None)

    # --- last completed backup cycle ---
    last = _probe.latest_finished_cycle_run(session)
    w.gauge(
        "last_run_timestamp_seconds",
        "Unix time the last finished backup cycle started; absent until one has run.",
        _epoch(last.started_at) if last else None,
    )
    w.gauge(
        "last_run_success",
        "1 if the last finished backup cycle succeeded, 0 if it failed or was aborted.",
        int(last.status == RunStatus.SUCCESS) if last else None,
    )
    w.gauge(
        "last_run_duration_seconds",
        "Wall-clock duration of the last finished backup cycle.",
        (last.finished_at - last.started_at).total_seconds()
        if last and last.finished_at
        else None,
    )
    w.gauge(
        "last_run_guests",
        "Number of guests backed up by the last finished backup cycle.",
        last.guests_ok if last and last.guests_ok is not None else None,
    )

    # --- datastore (cached, so it still reports while the PBS sleeps) ---
    ds = _probe.resolve_datastore(config.pbs.datastore, live_ds)
    w.gauge("datastore_used_bytes", "PBS datastore bytes used (last known value).",
            ds.used if ds else None)
    w.gauge("datastore_total_bytes", "PBS datastore total bytes (last known value).",
            ds.total if ds else None)

    # --- per-guest freshness: the series worth alerting on ---
    # Labelled by vmid only. Guest *names* live on PVE, and fetching them would put an API
    # call on every scrape just to decorate a label.
    guests = get_last_backups(session)
    w.metric(
        "guest_last_backup_timestamp_seconds",
        "Unix time of each guest's most recent snapshot on the PBS, from Joulenap's cache.",
        "gauge",
        [({"vmid": str(vmid)}, ts.timestamp()) for vmid, ts in sorted(guests.items())],
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
