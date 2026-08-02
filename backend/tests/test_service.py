"""JobService entry points: what gets queued, and the single-run guard around it.

The FIFO mechanics and the power lease are ``test_queue.py``; this file is about the two
public ways in — a route run and an ad-hoc PBS maintenance run — plus cancellation and the
``exclusive()`` block the manual power-off takes.
"""

from __future__ import annotations

import threading
import time

import pytest
from fakes import FakeBox, make_deps
from sqlalchemy import select

from app.core.config_store import ConfigStore
from app.db import session_scope
from app.db.models import Run, RunKind, RunStatus, RunTrigger
from app.jobs import AlreadyRunningError, JobService
from app.jobs.service import AlreadyQueuedError, QueuedRun


def _service(box: FakeBox | None = None) -> tuple[JobService, FakeBox]:
    box = box or FakeBox()
    deps, *_ = make_deps()
    return JobService(ConfigStore.load_or_create(), deps=deps, lease_deps=box.deps()), box


def _drain(service: JobService, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if service.current() is None and not service.pending() and not service.is_running:
            return
        time.sleep(0.01)
    raise AssertionError("queue did not drain")


class _Gate:
    """A queued job that parks until the test lets it through."""

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def enqueue(self, service: JobService, route_id: str = "nightly") -> None:
        service.enqueue(
            QueuedRun(
                key=route_id,
                route_id=route_id,
                trigger=RunTrigger.MANUAL,
                kind=RunKind.CYCLE,
                job=self.job,
            )
        )
        assert self.started.wait(timeout=5)

    def job(self, _config, _subject, recorder, _deps) -> None:
        self.started.set()
        assert self.release.wait(timeout=5)
        recorder.finish(RunStatus.SUCCESS)


# --- route runs ---------------------------------------------------------------


def test_run_route_records_a_run_for_that_route(temp_config, temp_db):
    service, _box = _service()

    assert service.run_route("nightly") == 0  # nothing ahead of it
    _drain(service)

    with session_scope() as session:
        run = session.scalars(select(Run)).one()
        assert (run.route_id, run.route_name) == ("nightly", "Nightly")
        assert run.kind == RunKind.CYCLE


def test_run_route_kind_follows_the_route_kind(temp_config, temp_db):
    # A sync route must not be filed as a backup cycle: history, metrics and the
    # notification title all group on this.
    service, _box = _service()

    service.run_route("offsite")
    _drain(service)

    with session_scope() as session:
        assert session.scalars(select(Run)).one().kind == RunKind.SYNC


def test_run_route_rejects_an_unknown_route(temp_config, temp_db):
    service, _box = _service()
    with pytest.raises(KeyError):
        service.run_route("does-not-exist")


def test_the_same_route_cannot_be_queued_twice(temp_config, temp_db):
    service, _box = _service()
    gate = _Gate()
    gate.enqueue(service)
    try:
        with pytest.raises(AlreadyQueuedError):
            service.run_route("nightly")
    finally:
        gate.release.set()
    _drain(service)


def test_another_route_queues_instead_of_being_rejected(temp_config, temp_db):
    # Per-route schedules mean two routes fire minutes apart; the second waits its turn.
    service, _box = _service()
    gate = _Gate()
    gate.enqueue(service)
    try:
        assert service.run_route("offsite") == 1
    finally:
        gate.release.set()
    _drain(service)

    with session_scope() as session:
        assert sorted(r.route_id for r in session.scalars(select(Run))) == ["nightly", "offsite"]


# --- ad-hoc PBS maintenance ---------------------------------------------------


def test_run_maintenance_records_a_route_less_run(temp_config, temp_db):
    service, _box = _service()

    service.run_maintenance("pbs-01", "gc")
    _drain(service)

    with session_scope() as session:
        run = session.scalars(select(Run)).one()
        assert run.kind == RunKind.GC
        assert run.route_id is None  # it belongs to no route, and history says so
        assert run.status == RunStatus.SUCCESS


def test_run_maintenance_verify_is_recorded_as_a_verify(temp_config, temp_db):
    service, _box = _service()

    service.run_maintenance("pbs-02", "verify")
    _drain(service)

    with session_scope() as session:
        assert session.scalars(select(Run)).one().kind == RunKind.VERIFY


def test_run_maintenance_rejects_an_unknown_device_or_action(temp_config, temp_db):
    service, _box = _service()
    with pytest.raises(KeyError):
        service.run_maintenance("nope", "gc")
    with pytest.raises(ValueError):
        service.run_maintenance("pbs-01", "reboot")


def test_gc_and_verify_on_one_box_are_separate_queue_entries(temp_config, temp_db):
    # They key on action as well as device, so asking for both is not "already queued".
    service, _box = _service()
    gate = _Gate()
    gate.enqueue(service)
    try:
        service.run_maintenance("pbs-01", "gc")
        service.run_maintenance("pbs-01", "verify")
        with pytest.raises(AlreadyQueuedError):
            service.run_maintenance("pbs-01", "gc")
    finally:
        gate.release.set()
    _drain(service)


def test_maintenance_wakes_the_box_and_powers_it_off(temp_config, temp_db):
    service, box = _service(FakeBox(reachable=[False, True]))

    service.run_maintenance("pbs-01", "gc")
    _drain(service)

    assert box.wol == ["pbs-01"]
    assert box.poweroffs == ["pbs-01"]


def test_keeping_the_box_on_is_honoured(temp_config, temp_db):
    service, box = _service()

    service.run_maintenance("pbs-01", "gc", power_off=False)
    _drain(service)

    assert box.poweroffs == []


# --- the single-run lock ------------------------------------------------------


def test_exclusive_blocks_while_a_run_holds_the_lock(temp_config, temp_db):
    # exclusive() (used by manual power-off) must not enter while a run holds the lock, so a
    # poweroff can't race a starting cycle (BE-B5).
    service, _box = _service()
    gate = _Gate()
    gate.enqueue(service)
    try:
        with pytest.raises(AlreadyRunningError), service.exclusive():
            pass  # pragma: no cover - the guard raises before the body runs
    finally:
        gate.release.set()
    _drain(service)

    # Lock free again -> exclusive() yields, and releases on exit.
    with service.exclusive():
        assert service.is_running is True
    assert service.is_running is False


# --- cancellation (11.2) -----------------------------------------------------


def test_cancel_ends_the_run_aborted_and_frees_the_lock(temp_config, temp_db):
    # The point of 11.2: a stuck run blocks every later job *and* manual power-off until
    # restart. After a cancel the lock must be free and a new run must start immediately.
    service, _box = _service()
    cancelling = _CancellableGate()
    cancelling.enqueue(service)

    assert service.cancel(cancelling.run_id(service)) is True
    cancelling.release.set()
    _drain(service)

    with session_scope() as session:
        run = session.scalars(select(Run)).one()
        assert run.status == RunStatus.ABORTED
        assert "ancelled" in (run.error or "")
    # The lock is genuinely free: another run starts rather than raising.
    service.run_route("offsite")
    _drain(service)


class _CancellableGate(_Gate):
    """Like ``_Gate``, but the job notices the cancel flag and aborts, as a cycle would."""

    def job(self, _config, _subject, recorder, deps) -> None:
        self.started.set()
        assert self.release.wait(timeout=5)
        if deps.cancelled():
            recorder.finish(RunStatus.ABORTED, error="Cancelled by user")
        else:
            recorder.finish(RunStatus.SUCCESS)

    def run_id(self, service: JobService) -> int:
        current = service.current()
        assert current is not None and current.run_id is not None
        return current.run_id


def test_cancel_refuses_a_run_that_is_not_the_one_in_flight(temp_config, temp_db):
    # A click landing as one run ends and the next begins must not stop the new run.
    service, _box = _service()
    gate = _CancellableGate()
    gate.enqueue(service)
    try:
        assert service.cancel(gate.run_id(service) + 999) is False
    finally:
        gate.release.set()
    _drain(service)

    with session_scope() as session:
        assert session.scalars(select(Run)).one().status == RunStatus.SUCCESS


def test_cancel_is_refused_when_nothing_is_running(temp_config, temp_db):
    service, _box = _service()
    service.run_route("nightly")
    _drain(service)
    with session_scope() as session:
        run_id = session.scalars(select(Run)).one().id
    assert service.cancel(run_id) is False


def test_a_stale_cancel_does_not_kill_the_next_run(temp_config, temp_db):
    # Cancel arrives moments before the run ends on its own; the flag must not leak into
    # the run that starts next.
    service, _box = _service()
    service.run_route("nightly")
    _drain(service)
    with session_scope() as session:
        first = session.scalars(select(Run)).one().id
    service.cancel(first)  # refused (nothing running), but prove the state is clean anyway

    service.run_route("offsite")
    _drain(service)
    with session_scope() as session:
        statuses = {r.route_id: r.status for r in session.scalars(select(Run))}
    assert statuses["offsite"] == RunStatus.SUCCESS
