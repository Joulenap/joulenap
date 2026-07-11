"""Startup recovery: runs left RUNNING by a crash are failed on boot."""

from __future__ import annotations

from app.config import Config
from app.db import session_scope
from app.db.models import (
    Run,
    RunKind,
    RunStatus,
    RunStep,
    RunTrigger,
    StepName,
    StepStatus,
)
from app.db.startup import sweep_orphaned_runs
from app.notify.messages import build_interrupted_message


def _add_run(session, status: RunStatus, *, step_status: StepStatus) -> int:
    run = Run(kind=RunKind.CYCLE, trigger=RunTrigger.SCHEDULED, status=status)
    run.steps.append(RunStep(name=StepName.BACKUP, status=step_status))
    session.add(run)
    session.flush()
    return run.id


def test_sweeps_running_run_and_step_to_failure(temp_db):
    with session_scope() as s:
        rid = _add_run(s, RunStatus.RUNNING, step_status=StepStatus.RUNNING)

    with session_scope() as s:
        assert len(sweep_orphaned_runs(s)) == 1

    with session_scope() as s:
        run = s.get(Run, rid)
        assert run.status == RunStatus.FAILURE
        assert run.finished_at is not None
        assert run.error  # a non-empty explanation
        assert run.steps[0].status == StepStatus.FAILURE
        assert run.steps[0].finished_at is not None


def test_leaves_finished_runs_untouched(temp_db):
    with session_scope() as s:
        ok_id = _add_run(s, RunStatus.SUCCESS, step_status=StepStatus.SUCCESS)

    with session_scope() as s:
        assert len(sweep_orphaned_runs(s)) == 0

    with session_scope() as s:
        assert s.get(Run, ok_id).status == RunStatus.SUCCESS


def test_only_running_steps_are_failed(temp_db):
    # A run stuck RUNNING can have an already-finished earlier step (e.g. wake succeeded,
    # then the crash hit during backup) — completed steps must keep their status.
    with session_scope() as s:
        run = Run(kind=RunKind.CYCLE, trigger=RunTrigger.SCHEDULED, status=RunStatus.RUNNING)
        run.steps.append(RunStep(name=StepName.WAKE, status=StepStatus.SUCCESS))
        run.steps.append(RunStep(name=StepName.BACKUP, status=StepStatus.RUNNING))
        s.add(run)
        s.flush()
        rid = run.id

    with session_scope() as s:
        sweep_orphaned_runs(s)

    with session_scope() as s:
        by_name = {step.name: step.status for step in s.get(Run, rid).steps}
        assert by_name[StepName.WAKE] == StepStatus.SUCCESS
        assert by_name[StepName.BACKUP] == StepStatus.FAILURE


def test_swept_run_yields_a_pbs_left_on_alert_when_it_had_woken(temp_db):
    # BE-R2: a crash after the PBS woke (WAIT done, no POWEROFF) -> the interrupted-run alert
    # built from the swept run warns the box is still on.
    with session_scope() as s:
        run = Run(kind=RunKind.CYCLE, trigger=RunTrigger.SCHEDULED, status=RunStatus.RUNNING)
        run.steps.append(RunStep(name=StepName.WAIT, status=StepStatus.SUCCESS))
        run.steps.append(RunStep(name=StepName.BACKUP, status=StepStatus.RUNNING))
        s.add(run)

    with session_scope() as s:
        swept = sweep_orphaned_runs(s)
        alerts = [build_interrupted_message(Config(), r) for r in swept]

    assert len(alerts) == 1
    title, body = alerts[0]
    assert "interrupted by a restart" in title
    assert "left powered on" in body


def test_preserves_existing_error_message(temp_db):
    with session_scope() as s:
        run = Run(
            kind=RunKind.CYCLE,
            trigger=RunTrigger.SCHEDULED,
            status=RunStatus.RUNNING,
            error="original failure detail",
        )
        s.add(run)
        s.flush()
        rid = run.id

    with session_scope() as s:
        sweep_orphaned_runs(s)

    with session_scope() as s:
        assert s.get(Run, rid).error == "original failure detail"
