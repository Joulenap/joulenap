"""Startup catch-up check: did a scheduled route fall in a window the process was down for?

The scheduler's jobstore is in-memory, so ``coalesce`` only collapses missed fires while the
process is alive — a run due while the container was stopped is simply lost, and the only
symptom is the *absence* of a success notification (BE-R1). At startup we compare each armed
route against its own last finished run; if a fire came due in between, we log and notify (we
do not auto-run — a restart shouldn't silently kick off a heavy PBS-waking backup).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select

from ..config import Config, Route
from ..db import session_scope
from ..db.models import Run, RunStatus
from ..notify import NotificationService
from .scheduler import Scheduler

log = logging.getLogger("joulenap.catchup")


def _last_finished_start(session, route_id: str) -> datetime | None:
    """Start time of ``route_id``'s most recent finished run (any terminal status).

    Anchored on *finished* rather than *successful* on purpose: a slot that fired but
    failed/aborted was attempted (and already notified), not missed due to downtime — so it
    must not re-trigger a 'missed' alert on every restart while a failure persists."""
    run = session.scalars(
        select(Run)
        .where(Run.route_id == route_id, Run.status != RunStatus.RUNNING)
        .order_by(Run.started_at.desc())
        .limit(1)
    ).first()
    return run.started_at if run else None


def check_missed_runs(
    config: Config,
    scheduler: Scheduler,
    notifier: NotificationService,
    *,
    now: datetime | None = None,
) -> list[tuple[Route, datetime]]:
    """Log + notify every armed route whose scheduled run was due while the process was down.

    Returns the ``(route, missed_at)`` pairs reported. Only routes the scheduler actually
    armed are considered, so a disabled route — or every route, with the kill-switch off —
    is silent by construction. A notify failure is logged, never raised: this is a
    best-effort startup safety net.
    """
    now = now or datetime.now(UTC)
    routes = {r.id: r for r in config.routes}
    reported: list[tuple[Route, datetime]] = []
    for route_id in scheduler.armed_route_ids():
        route = routes.get(route_id)
        if route is None:
            continue
        with session_scope() as session:
            anchor = _last_finished_start(session, route_id)
        if anchor is None:
            # This route has never completed a run (freshly created) — nothing to miss.
            continue
        missed = scheduler.missed_run_since(route_id, anchor, now)
        if missed is None:
            continue
        log.warning(
            "Route '%s' missed a scheduled run while Joulenap was down (due %s; last run %s)",
            route_id,
            missed,
            anchor,
        )
        try:
            notifier.send_missed_backup(
                config, route, missed, anchor, scheduler.next_run_time(route_id)
            )
        except Exception:  # noqa: BLE001 - a notify failure must not matter at startup
            log.exception("Failed to send missed-run notification for route '%s'", route_id)
        reported.append((route, missed))
    return reported


__all__ = ["check_missed_runs"]
