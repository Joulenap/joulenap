"""RunRecorder unit tests: step-status affordance and session lifecycle."""

from __future__ import annotations

import json

import pytest

from app.api.schemas import StepInfo
from app.db import session_scope
from app.db.models import Run, RunKind, RunStatus, RunTrigger, StepName, StepStatus
from app.jobs.recorder import RunRecorder, set_detail


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
        recorder.skip_step(StepName.GC, "gc_disabled")
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


def test_a_step_detail_is_stored_in_english_and_rendered_in_the_users_language(temp_db):
    """The whole seam end to end: what the recorder writes is what the API renders.

    ``detail`` stays English on the row — it is what the notifier reads and what a pre-1.0
    row has — while ``detail_key``/``detail_params`` let ``StepInfo`` rebuild the line in
    Italian on the way out.
    """
    with RunRecorder(RunKind.CYCLE, RunTrigger.MANUAL) as recorder:
        with recorder.step(StepName.PRECHECK) as step:
            set_detail(step, "free_space", free="12.5", avail="640")
        recorder.skip_step(StepName.GC, "gc_disabled")
        run_id = recorder.run_id
        recorder.finish(RunStatus.SUCCESS)

    with session_scope() as session:
        steps = {s.name: s for s in session.get(Run, run_id).steps}
        precheck, gc = steps[StepName.PRECHECK], steps[StepName.GC]

        assert precheck.detail == "12.5% free (640 GB)"
        assert precheck.detail_key == "free_space"
        assert json.loads(precheck.detail_params) == {"free": "12.5", "avail": "640"}
        assert StepInfo.of(precheck, "it").detail == "12.5% libero (640 GB)"
        assert StepInfo.of(precheck).detail == precheck.detail  # English is the default

        assert gc.detail == "GC disabled for this route" and gc.detail_params is None
        assert StepInfo.of(gc, "it").detail == "GC disattivata per questa route"


def test_a_step_detail_with_no_key_is_passed_through_untouched(temp_db):
    """A task UPID (or someone else's error text) has no key and must not be mangled."""
    upid = "UPID:pve:0000ABCD:...:vzdump:101:root@pam:"
    with RunRecorder(RunKind.CYCLE, RunTrigger.MANUAL) as recorder:
        with recorder.step(StepName.BACKUP) as step:
            step.detail = upid
        run_id = recorder.run_id
        recorder.finish(RunStatus.SUCCESS)

    with session_scope() as session:
        step = next(s for s in session.get(Run, run_id).steps if s.name == StepName.BACKUP)
        assert step.detail_key is None
        assert StepInfo.of(step, "it").detail == upid


def test_a_step_that_set_a_detail_and_then_failed_shows_the_error(temp_db):
    """The pre-flight guard's shape: report free space, then abort on it.

    ``render_detail`` prefers a resolvable key over the raw string, so leaving the key in
    place would render "12.5% free" on a FAILURE step and drop the reason entirely.
    """
    with RunRecorder(RunKind.CYCLE, RunTrigger.MANUAL) as recorder:
        with pytest.raises(RuntimeError):
            with recorder.step(StepName.PRECHECK) as step:
                set_detail(step, "free_space", free="12.5", avail="640")
                raise RuntimeError("datastore too full")
        run_id = recorder.run_id
        recorder.finish(RunStatus.FAILURE)

    with session_scope() as session:
        step = next(s for s in session.get(Run, run_id).steps if s.name == StepName.PRECHECK)
        assert step.status == StepStatus.FAILURE
        assert step.detail == "datastore too full"
        assert step.detail_key is None and step.detail_params is None
        assert StepInfo.of(step, "it").detail == "datastore too full"
