"""Auto-prune old run history + activity-log rows.

Joulenap is meant to run on a tiny LXC (≈1 GB disk), so the SQLite DB under data/
must not grow without bound. This deletes runs (and, by cascade, their steps + log
lines) and any run-less activity-log lines once they age past the retention window.
Scheduled daily by core/scheduler.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import LogEvent, Run


@dataclass(frozen=True)
class PruneResult:
    runs_deleted: int
    logs_deleted: int

    @property
    def total(self) -> int:
        return self.runs_deleted + self.logs_deleted


def prune_history(
    session: Session, *, retention_days: int, now: datetime | None = None
) -> PruneResult:
    """Delete finished runs and stray activity-log lines older than ``retention_days``.

    Runs still in progress (``finished_at IS NULL``) are always kept. The caller owns the
    transaction — wrap in ``session_scope()`` (or commit) to persist. ``retention_days <= 0``
    disables pruning and returns an empty result.
    """
    if retention_days <= 0:
        return PruneResult(0, 0)
    cutoff = (now or datetime.now(UTC)) - timedelta(days=retention_days)

    # Finished runs past the cutoff. Delete through the ORM so the relationship cascade
    # removes each run's steps + log lines — SQLite enforces no FKs by default, so a bulk
    # DELETE would orphan the children instead of cascading.
    stale_runs = session.scalars(
        select(Run).where(Run.finished_at.is_not(None), Run.finished_at < cutoff)
    ).all()
    for run in stale_runs:
        session.delete(run)

    # Activity-log lines not tied to any run age out on their own timestamp. These have no
    # parent to cascade from, so a bulk DELETE is both correct and cheaper.
    logs_deleted = session.execute(
        delete(LogEvent).where(LogEvent.run_id.is_(None), LogEvent.ts < cutoff)
    ).rowcount

    return PruneResult(runs_deleted=len(stale_runs), logs_deleted=logs_deleted)
