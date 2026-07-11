"""Startup catch-up check: did a scheduled backup fall in a window the process was down for?

The scheduler's jobstore is in-memory, so ``coalesce`` only collapses missed fires while the
process is alive — a backup due while the container was stopped is simply lost, and the only
symptom is the *absence* of a success notification (BE-R1). At startup we compare the last
finished cycle against the armed schedule; if a fire came due in between, we log and notify
(we do not auto-run — a restart shouldn't silently kick off a heavy PBS-waking backup).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select

from ..config import Config
from ..db import session_scope
from ..db.models import Run, RunKind, RunStatus
from ..notify import NotificationService
from .scheduler import Scheduler

log = logging.getLogger("joulenap.catchup")


def _last_finished_cycle_start(session) -> datetime | None:
    """Start time of the most recent finished backup cycle (any terminal status).

    Anchored on *finished* rather than *successful* on purpose: a slot that fired but
    failed/aborted was attempted (and already notified), not missed due to downtime — so it
    must not re-trigger a 'missed' alert on every restart while a failure persists."""
    run = session.scalars(
        select(Run)
        .where(Run.kind == RunKind.CYCLE, Run.status != RunStatus.RUNNING)
        .order_by(Run.started_at.desc())
        .limit(1)
    ).first()
    return run.started_at if run else None


def check_missed_backup(
    config: Config,
    scheduler: Scheduler,
    notifier: NotificationService,
    *,
    now: datetime | None = None,
) -> datetime | None:
    """If a scheduled backup was due while the process was down, log + notify (BE-R1).

    Returns the missed fire time when one was detected and reported, else None. A notify
    failure is logged, never raised — this is a best-effort startup safety net."""
    now = now or datetime.now(UTC)
    with session_scope() as session:
        anchor = _last_finished_cycle_start(session)
    if anchor is None:
        # No completed cycle yet (fresh install) — nothing could have been missed.
        return None
    missed = scheduler.missed_backup_since(anchor, now)
    if missed is None:
        return None
    log.warning(
        "A scheduled backup was missed while Joulenap was down (due %s; last run %s)",
        missed,
        anchor,
    )
    try:
        notifier.send_missed_backup(config, missed, anchor, scheduler.next_run_time)
    except Exception:  # noqa: BLE001 - a notify failure must not matter at startup
        log.exception("Failed to send missed-backup notification")
    return missed


__all__ = ["check_missed_backup"]
