"""Smoke tests for the SQLite layer."""

from __future__ import annotations

from sqlalchemy import select

from app.db import session_scope
from app.db.models import LogEvent, LogLevel, Run, RunKind, RunStatus, RunTrigger


def test_create_run_with_logs(temp_db):
    with session_scope() as s:
        run = Run(kind=RunKind.BACKUP, trigger=RunTrigger.MANUAL)
        run.logs.append(LogEvent(level=LogLevel.INFO, message="started"))
        run.logs.append(LogEvent(level=LogLevel.OK, message="done"))
        s.add(run)

    with session_scope() as s:
        run = s.scalars(select(Run)).one()
        assert run.status == RunStatus.RUNNING
        assert run.started_at is not None
        assert len(run.logs) == 2
        assert run.logs[0].message == "started"


def test_timestamps_read_back_utc_aware(temp_db):
    # SQLite drops tzinfo on read; UtcDateTime must re-attach UTC so timestamps serialize
    # with an offset (otherwise the frontend misreads them as local time).
    with session_scope() as s:
        run = Run(kind=RunKind.BACKUP, trigger=RunTrigger.MANUAL)
        run.logs.append(LogEvent(level=LogLevel.INFO, message="started"))
        s.add(run)

    with session_scope() as s:
        run = s.scalars(select(Run)).one()
        assert run.started_at.tzinfo is not None
        assert run.started_at.utcoffset().total_seconds() == 0  # UTC
        assert run.logs[0].ts.tzinfo is not None

    # And the API schema serializes it with a trailing offset/Z, not a bare local-looking time.
    from app.api.schemas import RunSummary

    with session_scope() as s:
        run = s.scalars(select(Run)).one()
        payload = RunSummary.of(run).model_dump_json()
        assert '"started_at":"' in payload
        started = payload.split('"started_at":"', 1)[1].split('"', 1)[0]
        assert started.endswith("Z") or "+" in started


def test_cascade_delete_logs(temp_db):
    with session_scope() as s:
        run = Run(kind=RunKind.GC, trigger=RunTrigger.SCHEDULED)
        run.logs.append(LogEvent(level=LogLevel.WARN, message="orphan chunks"))
        s.add(run)

    with session_scope() as s:
        run = s.scalars(select(Run)).one()
        s.delete(run)

    with session_scope() as s:
        assert s.scalars(select(LogEvent)).all() == []
