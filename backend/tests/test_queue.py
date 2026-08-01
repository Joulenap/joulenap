"""The run queue: FIFO serialisation, and the power lease taken around each route run."""

from __future__ import annotations

import threading
import time

import pytest
from fakes import FakeBox, make_deps
from sqlalchemy import select

from app.config import PbsDevice, PveDevice, Route, RouteSource
from app.core.config_store import ConfigStore
from app.db import session_scope
from app.db.models import Run, RunStatus, RunTrigger
from app.jobs.service import AlreadyQueuedError, JobService


def make_service(box: FakeBox | None = None) -> tuple[JobService, FakeBox]:
    """A service on two PBS boxes with four routes: r1/r2 -> pbs1, r3 -> pbs2, sync
    pbs1 -> pbs2."""
    store = ConfigStore.load_or_create()
    config = store.config
    config.pves = [
        PveDevice(id="pve1", host="192.0.2.10", storages={"pbs1": "pbs", "pbs2": "pbs-b"})
    ]
    config.pbss = [
        PbsDevice(id="pbs1", host="192.0.2.20", datastore="backup", mac="00:11:22:33:44:55"),
        PbsDevice(id="pbs2", host="192.0.2.21", datastore="backup", mac="00:11:22:33:44:66"),
    ]
    config.routes = [
        Route(id="r1", kind="backup", target="pbs1", sources=[RouteSource(pve="pve1")]),
        Route(id="r2", kind="backup", target="pbs1", sources=[RouteSource(pve="pve1")]),
        Route(id="r3", kind="backup", target="pbs2", sources=[RouteSource(pve="pve1")]),
        Route(id="sync", name="Offsite", kind="sync", source_pbs="pbs1", target="pbs2"),
    ]
    box = box or FakeBox()
    deps, _pve, _pbs, _power = make_deps()
    return JobService(store, deps=deps, lease_deps=box.deps()), box


def ok_job(_config, recorder, _deps) -> None:
    recorder.finish(RunStatus.SUCCESS)


def failing_job(_config, _recorder, _deps) -> None:
    raise RuntimeError("vzdump exploded")


class Gate:
    """A job that parks until the test lets it through, so ordering is deterministic."""

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def job(self, _config, recorder, _deps) -> None:
        self.started.set()
        assert self.release.wait(timeout=5)
        recorder.finish(RunStatus.SUCCESS)


def drain(service: JobService, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if service.current() is None and not service.pending() and not service.is_running:
            return
        time.sleep(0.01)
    raise AssertionError("queue did not drain")


# --- queue mechanics ---------------------------------------------------------


def test_runs_execute_in_fifo_order(temp_config, temp_db):
    service, _box = make_service()
    gate = Gate()
    order: list[str] = []

    def record(route_id):
        def job(_c, recorder, _d):
            order.append(route_id)
            recorder.finish(RunStatus.SUCCESS)

        return job

    assert service.enqueue("r1", RunTrigger.SCHEDULED, gate.job) == 0
    assert gate.started.wait(timeout=5)
    assert service.enqueue("r2", RunTrigger.SCHEDULED, record("r2")) == 1
    assert service.enqueue("r3", RunTrigger.SCHEDULED, record("r3")) == 2
    assert [item.route_id for item in service.pending()] == ["r2", "r3"]
    assert service.current().route_id == "r1"

    gate.release.set()
    drain(service)
    assert order == ["r2", "r3"]


def test_enqueueing_a_route_twice_is_rejected(temp_config, temp_db):
    service, _box = make_service()
    gate = Gate()

    service.enqueue("r1", RunTrigger.SCHEDULED, gate.job)
    assert gate.started.wait(timeout=5)
    try:
        with pytest.raises(AlreadyQueuedError, match="already running"):
            service.enqueue("r1", RunTrigger.MANUAL, ok_job)
        service.enqueue("r2", RunTrigger.SCHEDULED, ok_job)
        with pytest.raises(AlreadyQueuedError, match="already queued"):
            service.enqueue("r2", RunTrigger.MANUAL, ok_job)
    finally:
        gate.release.set()
    drain(service)


def test_a_queued_route_can_be_dequeued(temp_config, temp_db):
    service, _box = make_service()
    gate = Gate()

    service.enqueue("r1", RunTrigger.SCHEDULED, gate.job)
    assert gate.started.wait(timeout=5)
    service.enqueue("r2", RunTrigger.SCHEDULED, failing_job)

    assert service.dequeue("r2") is True
    assert service.dequeue("r2") is False  # gone
    assert service.dequeue("r1") is False  # running, not queued -> cancel() territory
    gate.release.set()
    drain(service)

    with session_scope() as session:
        assert [r.route_id for r in session.scalars(select(Run))] == ["r1"]


def test_the_worker_restarts_after_the_queue_drains(temp_config, temp_db):
    service, _box = make_service()

    service.enqueue("r1", RunTrigger.SCHEDULED, ok_job)
    drain(service)
    service.enqueue("r2", RunTrigger.SCHEDULED, ok_job)
    drain(service)

    with session_scope() as session:
        assert sorted(r.route_id for r in session.scalars(select(Run))) == ["r1", "r2"]


def test_the_run_row_carries_the_route(temp_config, temp_db):
    service, _box = make_service()

    service.enqueue("sync", RunTrigger.SCHEDULED, ok_job)
    drain(service)

    with session_scope() as session:
        run = session.scalars(select(Run)).one()
        assert run.route_id == "sync"
        assert run.route_name == "Offsite"


def test_a_route_deleted_while_queued_is_dropped(temp_config, temp_db):
    service, box = make_service()
    gate = Gate()

    service.enqueue("r1", RunTrigger.SCHEDULED, gate.job)
    assert gate.started.wait(timeout=5)
    service.enqueue("r2", RunTrigger.SCHEDULED, ok_job)
    service._store.config.routes = [r for r in service._store.config.routes if r.id != "r2"]
    gate.release.set()
    drain(service)

    with session_scope() as session:
        assert [r.route_id for r in session.scalars(select(Run))] == ["r1"]
    assert box.poweroffs == ["pbs1"]  # r1 still powered its box down


def test_a_queued_run_waits_for_an_exclusive_block(temp_config, temp_db):
    # exclusive() is the manual power-off: a queued run must not start underneath it.
    service, _box = make_service()

    with service.exclusive():
        service.enqueue("r1", RunTrigger.MANUAL, ok_job)
        time.sleep(0.05)
        with session_scope() as session:
            assert session.scalars(select(Run)).all() == []  # blocked, no run row yet

    drain(service)
    with session_scope() as session:
        assert session.scalars(select(Run)).one().status == RunStatus.SUCCESS


# --- the power lease around a run --------------------------------------------


def test_two_routes_sharing_a_pbs_wake_once_and_power_off_at_the_end(temp_config, temp_db):
    # Acceptance 1 + 2: r2 is queued while r1 runs, so r1's release must not power the box
    # down, and the whole pair costs exactly one wake and one power-off.
    service, box = make_service(FakeBox(reachable=[False, True]))
    gate = Gate()

    service.enqueue("r1", RunTrigger.SCHEDULED, gate.job)
    assert gate.started.wait(timeout=5)
    service.enqueue("r2", RunTrigger.SCHEDULED, ok_job)
    gate.release.set()
    drain(service)

    assert box.wol == ["pbs1"]
    assert box.poweroffs == ["pbs1"]


def test_a_failed_run_leaves_the_pbs_on(temp_config, temp_db):
    service, box = make_service(FakeBox(reachable=[False, True]))

    service.enqueue("r1", RunTrigger.SCHEDULED, failing_job)
    drain(service)

    assert box.wol == ["pbs1"]
    assert box.poweroffs == []
    with session_scope() as session:
        assert session.scalars(select(Run)).one().status == RunStatus.FAILURE


def test_the_next_route_skips_the_wake_of_a_box_left_on(temp_config, temp_db):
    # Acceptance 3, second half: the box the failed run left awake is not woken again.
    service, box = make_service(FakeBox(reachable=[False, True]))

    service.enqueue("r1", RunTrigger.SCHEDULED, failing_job)
    drain(service)
    service.enqueue("r2", RunTrigger.SCHEDULED, ok_job)
    drain(service)

    assert box.wol == ["pbs1"]  # one packet for both runs
    assert box.poweroffs == ["pbs1"]  # r2 succeeded, so it closes the box


def test_a_manual_run_can_keep_the_pbs_on(temp_config, temp_db):
    service, box = make_service()

    service.enqueue("r1", RunTrigger.MANUAL, ok_job, power_off=False)
    drain(service)

    assert box.poweroffs == []


def test_a_sync_route_leases_both_boxes(temp_config, temp_db):
    # A sync route holds two leases — target and source — released independently.
    service, box = make_service(FakeBox(reachable=[False, True]))

    service.enqueue("sync", RunTrigger.SCHEDULED, ok_job)
    drain(service)

    assert box.wol == ["pbs2"]  # the target was down at its probe; the source answered
    assert sorted(box.poweroffs) == ["pbs1", "pbs2"]


def test_an_unreachable_pbs_fails_the_run_without_running_the_job(temp_config, temp_db):
    service, box = make_service(FakeBox(reachable=False))
    ran = []

    service.enqueue("r1", RunTrigger.SCHEDULED, lambda *_a: ran.append(1))
    drain(service)

    assert ran == []
    assert box.poweroffs == []
    with session_scope() as session:
        run = session.scalars(select(Run)).one()
        assert run.status == RunStatus.FAILURE
        assert "not reachable" in (run.error or "")


def test_a_run_on_another_pbs_does_not_hold_the_first(temp_config, temp_db):
    service, box = make_service()

    service.enqueue("r1", RunTrigger.SCHEDULED, ok_job)
    drain(service)
    service.enqueue("r3", RunTrigger.SCHEDULED, ok_job)
    drain(service)

    assert box.poweroffs == ["pbs1", "pbs2"]
