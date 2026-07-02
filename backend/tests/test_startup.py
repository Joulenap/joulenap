"""Startup recovery: runs left RUNNING by a crash are failed on boot."""

from __future__ import annotations

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
        assert sweep_orphaned_runs(s) == 1

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
        assert sweep_orphaned_runs(s) == 0

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
