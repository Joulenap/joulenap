"""RunRecorder unit tests: step-status affordance and session lifecycle."""

from __future__ import annotations

import pytest

from app.db import session_scope
from app.db.models import Run, RunKind, RunStatus, RunTrigger, StepName, StepStatus
from app.jobs.recorder import RunRecorder


def test_step_body_can_record_non_fatal_failure(temp_db):
    # A body that marks the step FAILURE and returns normally is respected (not overwritten
    # with SUCCESS) and does NOT raise out of the context manager.
    with RunRecorder(RunKind.CYCLE, RunTrigger.MANUAL) as recorder:
        with recorder.step(StepName.POWEROFF) as step:
            step.status = StepStatus.FAILURE
        run_id = recorder.run_id
        recorder.finish(RunStatus.SUCCESS)

    with session_scope() as session:
        steps = {s.name: s.status for s in session.get(Run, run_id).steps}
    assert steps[StepName.POWEROFF] == StepStatus.FAILURE


def test_step_still_auto_succeeds_on_clean_exit(temp_db):
    with RunRecorder(RunKind.CYCLE, RunTrigger.MANUAL) as recorder:
        with recorder.step(StepName.WAKE):
            pass
        run_id = recorder.run_id
        recorder.finish(RunStatus.SUCCESS)
    with session_scope() as session:
        steps = {s.name: s.status for s in session.get(Run, run_id).steps}
    assert steps[StepName.WAKE] == StepStatus.SUCCESS


def test_a_skipped_step_does_not_finish_before_it_started(temp_db):
    # started_at used to come from the column default, which SQLAlchemy evaluates at flush —
    # i.e. after the finished_at passed here — so every skipped step had a negative duration.
    with RunRecorder(RunKind.CYCLE, RunTrigger.MANUAL) as recorder:
        recorder.skip_step(StepName.GC, "GC disabled for this route")
        run_id = recorder.run_id
        recorder.finish(RunStatus.SUCCESS)

    with session_scope() as session:
        step = next(s for s in session.get(Run, run_id).steps if s.name == StepName.GC)
        assert step.status == StepStatus.SKIPPED
        assert step.started_at is not None and step.finished_at is not None
        assert (step.finished_at - step.started_at).total_seconds() == 0


class _SpySession:
    """Minimal session double: first commit raises; tracks close()."""

    def __init__(self) -> None:
        self.closed = False

    def add(self, _obj) -> None:
        pass

    def commit(self) -> None:
        raise RuntimeError("database is locked")

    def close(self) -> None:
        self.closed = True


def test_recorder_closes_session_if_opening_commit_fails():
    spy = _SpySession()
    with pytest.raises(RuntimeError):
        RunRecorder(RunKind.CYCLE, RunTrigger.MANUAL, session_factory=lambda: spy)
    assert spy.closed is True


def test_run_has_no_dead_summary_columns():
    # bytes_total / guests_failed were never populated (JN-008) — they must not exist.
    assert not hasattr(Run, "bytes_total")
    assert not hasattr(Run, "guests_failed")
    assert hasattr(Run, "guests_ok")  # the one that IS used stays
    assert hasattr(Run, "route_id") and hasattr(Run, "route_name")


def test_a_run_records_the_route_it_came_from(temp_db):
    with RunRecorder(
        RunKind.CYCLE, RunTrigger.SCHEDULED, route_id="nightly", route_name="Nightly backup"
    ) as rec:
        rec.finish(RunStatus.SUCCESS)
        run_id = rec.run_id

    with session_scope() as session:
        run = session.get(Run, run_id)
        assert run.route_id == "nightly" and run.route_name == "Nightly backup"


def test_a_run_without_a_route_is_allowed(temp_db):
    # A manual one-off has no route, and so does every run recorded before 1.0.
    with RunRecorder(RunKind.GC, RunTrigger.MANUAL) as rec:
        rec.finish(RunStatus.SUCCESS)
        run_id = rec.run_id

    with session_scope() as session:
        run = session.get(Run, run_id)
        assert run.route_id is None and run.route_name is None
