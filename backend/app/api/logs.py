"""Run history and activity log: GET /api/logs, /api/runs, /api/runs/{id}."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..db.models import LogEvent, Run, TaskLogLine
from .deps import require_auth
from .schemas import LogLine, RunDetail, RunSummary, TaskLogLineSchema, TaskLogResponse

router = APIRouter(dependencies=[Depends(require_auth)], tags=["logs"])


@router.get("/logs", response_model=list[LogLine])
def get_logs(
    limit: int = Query(default=100, ge=1, le=1000),
    session: Session = Depends(get_session),
) -> list[LogLine]:
    """Recent activity-log lines across all runs, newest first."""
    events = session.scalars(
        select(LogEvent).order_by(LogEvent.ts.desc(), LogEvent.id.desc()).limit(limit)
    ).all()
    return [LogLine.of(e) for e in events]


@router.get("/tasklog", response_model=TaskLogResponse)
def get_task_log(
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=2000, ge=1, le=10000),
    run: int | None = Query(
        default=None, description="Read this run's lines instead of the newest run's"
    ),
    session: Session = Depends(get_session),
) -> TaskLogResponse:
    """Task-log lines (backup/GC/verify/sync output) for one run.

    Without ``run`` this follows the most recent run that has any, which is what the live
    tail wants: the client polls with ``after`` = the highest line id it has seen, line ids
    increase globally, so a newer run's lines naturally supersede an older run's and the
    panel resets when ``run_id`` changes.

    With ``run`` it reads that run instead — the homepage's expandable history rows, where
    the run in question is finished and is *not* the newest. A run with no lines answers
    ``{run_id: <id>, lines: []}``: the row asked about a specific run, so echoing its id
    (rather than null) keeps the client's buffer keyed correctly.
    """
    if run is not None:
        target_run_id: int | None = run
    else:
        target_run_id = session.scalar(
            select(TaskLogLine.run_id).order_by(TaskLogLine.id.desc()).limit(1)
        )
    if target_run_id is None:
        return TaskLogResponse(run_id=None, lines=[])
    lines = session.scalars(
        select(TaskLogLine)
        .where(TaskLogLine.run_id == target_run_id, TaskLogLine.id > after)
        .order_by(TaskLogLine.id)
        .limit(limit)
    ).all()
    return TaskLogResponse(
        run_id=target_run_id, lines=[TaskLogLineSchema.of(line) for line in lines]
    )


@router.get("/runs", response_model=list[RunSummary])
def get_runs(
    limit: int = Query(default=50, ge=1, le=500),
    route: str | None = Query(default=None, description="Only runs of this route id"),
    session: Session = Depends(get_session),
) -> list[RunSummary]:
    """Run history (summaries), newest first, optionally filtered to one route.

    The filter matches on the recorded ``route_id``, so history stays readable after a route
    is deleted (the chip disappears, the rows don't).
    """
    stmt = select(Run).order_by(Run.started_at.desc(), Run.id.desc()).limit(limit)
    if route is not None:
        stmt = stmt.where(Run.route_id == route)
    return [RunSummary.of(r) for r in session.scalars(stmt).all()]


@router.get("/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: int, session: Session = Depends(get_session)) -> RunDetail:
    run = session.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return RunDetail.of(run)
