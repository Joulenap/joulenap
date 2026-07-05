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
