"""Startup recovery: fail runs left mid-flight by a crash or restart.

A run (and its current step) is only ever ``RUNNING`` while the process that owns the
job is alive — there's no cross-process runner. So any ``RUNNING`` row found at startup
belongs to a cycle the previous process died in the middle of. Left alone it would claim
to be running forever: the dashboard's "last run" spins indefinitely and the live
task-log panel tails a run that never finishes. We sweep them to ``FAILURE`` on boot.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Run, RunStatus, StepStatus

_INTERRUPTED_RUN = "Interrupted — Joulenap restarted while the run was in progress"
_INTERRUPTED_STEP = "Interrupted at startup"


def sweep_orphaned_runs(session: Session, *, now: datetime | None = None) -> list[Run]:
    """Mark every ``RUNNING`` run (and its ``RUNNING`` steps) as ``FAILURE``.

    Returns the swept runs (``len()`` for the count) so the caller can alert on them — a
    crash after wake leaves the PBS on with no notification otherwise (BE-R2). The caller
    owns the transaction — wrap in ``session_scope()`` (or commit) to persist.
    """
    ts = now or datetime.now(UTC)
    orphaned = session.scalars(select(Run).where(Run.status == RunStatus.RUNNING)).all()
    for run in orphaned:
        run.status = RunStatus.FAILURE
        run.finished_at = ts
        if not run.error:
            run.error = _INTERRUPTED_RUN
        for step in run.steps:
            if step.status == StepStatus.RUNNING:
                step.status = StepStatus.FAILURE
                step.finished_at = ts
                if not step.detail:
                    step.detail = _INTERRUPTED_STEP
    return list(orphaned)


__all__ = ["sweep_orphaned_runs"]
