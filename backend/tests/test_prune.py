"""History pruning: drop old runs (with their steps + logs) and stray log lines."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fakes import make_deps

from app.core.config_store import ConfigStore
from app.db import session_scope
from app.db.models import (
    LogEvent,
    LogLevel,
    Run,
    RunKind,
    RunStatus,
    RunStep,
    RunTrigger,
    StepName,
    StepStatus,
)
from app.db.prune import prune_history
from app.jobs import JobService


def _make_run(session, *, finished_days_ago: int | None, running: bool = False) -> int:
    """Create a run with one step + two log lines; finished N days ago (or unfinished)."""
    now = datetime.now(UTC)
    finished_at = None if finished_days_ago is None else now - timedelta(days=finished_days_ago)
    run = Run(
        kind=RunKind.CYCLE,
        trigger=RunTrigger.SCHEDULED,
        status=RunStatus.RUNNING if running else RunStatus.SUCCESS,
        started_at=now - timedelta(days=(finished_days_ago or 0)),
        finished_at=finished_at,
    )
    run.steps.append(RunStep(name=StepName.BACKUP, status=StepStatus.SUCCESS))
    run.logs.append(LogEvent(level=LogLevel.INFO, message="started"))
    run.logs.append(LogEvent(level=LogLevel.OK, message="done"))
    session.add(run)
    session.flush()
    return run.id


def test_prunes_runs_older_than_retention(temp_db):
    with session_scope() as s:
        old_id = _make_run(s, finished_days_ago=30)
        fresh_id = _make_run(s, finished_days_ago=2)

    with session_scope() as s:
        result = prune_history(s, retention_days=14)

    assert result.runs_deleted == 1
    with session_scope() as s:
        assert s.get(Run, old_id) is None
        assert s.get(Run, fresh_id) is not None


def test_cascades_to_steps_and_logs(temp_db):
    with session_scope() as s:
        _make_run(s, finished_days_ago=30)

    with session_scope() as s:
        prune_history(s, retention_days=14)

    # The deleted run's steps + log lines go with it (no orphans left behind).
    with session_scope() as s:
        assert s.query(Run).count() == 0
        assert s.query(RunStep).count() == 0
        assert s.query(LogEvent).count() == 0


def test_keeps_running_run_even_if_old(temp_db):
    # An unfinished run (finished_at NULL) must never be pruned, regardless of age.
    with session_scope() as s:
        running_id = _make_run(s, finished_days_ago=None, running=True)

    with session_scope() as s:
        result = prune_history(s, retention_days=14)

    assert result.runs_deleted == 0
    with session_scope() as s:
        assert s.get(Run, running_id) is not None


def test_prunes_stray_log_events_with_no_run(temp_db):
    now = datetime.now(UTC)
    with session_scope() as s:
        s.add(LogEvent(level=LogLevel.WARN, message="old", ts=now - timedelta(days=30)))
        s.add(LogEvent(level=LogLevel.INFO, message="new", ts=now - timedelta(days=1)))

    with session_scope() as s:
        result = prune_history(s, retention_days=14)

    assert result.logs_deleted == 1
    with session_scope() as s:
        messages = {e.message for e in s.query(LogEvent).all()}
        assert messages == {"new"}


def test_zero_retention_disables_pruning(temp_db):
    with session_scope() as s:
        _make_run(s, finished_days_ago=999)

    with session_scope() as s:
        result = prune_history(s, retention_days=0)

    assert result.total == 0
    with session_scope() as s:
        assert s.query(Run).count() == 1


def test_service_run_prune_uses_configured_retention(temp_config, temp_db):
    deps, _pve, _pbs, _power = make_deps()
    store = ConfigStore.load_or_create()
    store.config.maintenance.history.retention_days = 14
    service = JobService(store, deps=deps)

    with session_scope() as s:
        _make_run(s, finished_days_ago=30)
        _make_run(s, finished_days_ago=1)

    result = service.run_prune()

    assert result.runs_deleted == 1
    with session_scope() as s:
        assert s.query(Run).count() == 1
