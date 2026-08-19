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
from app.db.models import Run, RunKind, RunStatus, RunTrigger, StepName, StepStatus
from app.jobs.lease import ReleaseOutcome
from app.jobs.service import AlreadyQueuedError, JobService, QueuedRun
from app.notify.messages import RunContext


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
    deps, _pve, _pbs = make_deps()
    return JobService(store, deps=deps, lease_deps=box.deps()), box


def enqueue(service: JobService, route_id: str, job, *, trigger=RunTrigger.SCHEDULED,
            power_off: bool = True) -> int:
    """Queue ``route_id`` with a stand-in job, bypassing the real route cycles.

    The cycles have their own tests; here the point is the queue and the lease around it,
    so the job is whatever the case needs (park, succeed, explode).
    """
    return service.enqueue(
        QueuedRun(
            key=route_id,
            route_id=route_id,
            trigger=trigger,
            kind=RunKind.CYCLE,
            job=job,
            power_off=power_off,
        )
    )


def ok_job(_config, _subject, recorder, _deps) -> None:
    recorder.finish(RunStatus.SUCCESS)


def failing_job(_config, _subject, _recorder, _deps) -> None:
    raise RuntimeError("vzdump exploded")


class Gate:
    """A job that parks until the test lets it through, so ordering is deterministic."""

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def job(self, _config, _subject, recorder, _deps) -> None:
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
        def job(_c, _subject, recorder, _d):
            order.append(route_id)
            recorder.finish(RunStatus.SUCCESS)

        return job

    assert enqueue(service, "r1", gate.job, trigger=RunTrigger.SCHEDULED) == 0
    assert gate.started.wait(timeout=5)
    assert enqueue(service, "r2", record("r2")) == 1
    assert enqueue(service, "r3", record("r3")) == 2
    assert [item.route_id for item in service.pending()] == ["r2", "r3"]
    assert service.current().route_id == "r1"

    gate.release.set()
    drain(service)
    assert order == ["r2", "r3"]


def test_enqueueing_a_route_twice_is_rejected(temp_config, temp_db):
    service, _box = make_service()
    gate = Gate()

    enqueue(service, "r1", gate.job, trigger=RunTrigger.SCHEDULED)
    assert gate.started.wait(timeout=5)
    try:
        with pytest.raises(AlreadyQueuedError, match="already running"):
            enqueue(service, "r1", ok_job, trigger=RunTrigger.MANUAL)
        enqueue(service, "r2", ok_job, trigger=RunTrigger.SCHEDULED)
        with pytest.raises(AlreadyQueuedError, match="already queued"):
            enqueue(service, "r2", ok_job, trigger=RunTrigger.MANUAL)
    finally:
        gate.release.set()
    drain(service)


def test_a_queued_route_can_be_dequeued(temp_config, temp_db):
    service, _box = make_service()
    gate = Gate()

    enqueue(service, "r1", gate.job, trigger=RunTrigger.SCHEDULED)
    assert gate.started.wait(timeout=5)
    enqueue(service, "r2", failing_job, trigger=RunTrigger.SCHEDULED)

    assert service.dequeue("r2") is True
    assert service.dequeue("r2") is False  # gone
    assert service.dequeue("r1") is False  # running, not queued -> cancel() territory
    gate.release.set()
    drain(service)

    with session_scope() as session:
        assert [r.route_id for r in session.scalars(select(Run))] == ["r1"]


def test_the_worker_restarts_after_the_queue_drains(temp_config, temp_db):
    service, _box = make_service()

    enqueue(service, "r1", ok_job, trigger=RunTrigger.SCHEDULED)
    drain(service)
    enqueue(service, "r2", ok_job, trigger=RunTrigger.SCHEDULED)
    drain(service)

    with session_scope() as session:
        assert sorted(r.route_id for r in session.scalars(select(Run))) == ["r1", "r2"]


def test_the_run_row_carries_the_route(temp_config, temp_db):
    service, _box = make_service()

    enqueue(service, "sync", ok_job, trigger=RunTrigger.SCHEDULED)
    drain(service)

    with session_scope() as session:
        run = session.scalars(select(Run)).one()
        assert run.route_id == "sync"
        assert run.route_name == "Offsite"


def test_a_route_deleted_while_queued_is_dropped(temp_config, temp_db):
    service, box = make_service()
    gate = Gate()

    enqueue(service, "r1", gate.job, trigger=RunTrigger.SCHEDULED)
    assert gate.started.wait(timeout=5)
    enqueue(service, "r2", ok_job, trigger=RunTrigger.SCHEDULED)
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
        enqueue(service, "r1", ok_job, trigger=RunTrigger.MANUAL)
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

    enqueue(service, "r1", gate.job, trigger=RunTrigger.SCHEDULED)
    assert gate.started.wait(timeout=5)
    enqueue(service, "r2", ok_job, trigger=RunTrigger.SCHEDULED)
    gate.release.set()
    drain(service)

    assert box.wol == ["pbs1"]
    assert box.poweroffs == ["pbs1"]


def test_a_failed_run_leaves_the_pbs_on(temp_config, temp_db):
    service, box = make_service(FakeBox(reachable=[False, True]))

    enqueue(service, "r1", failing_job, trigger=RunTrigger.SCHEDULED)
    drain(service)

    assert box.wol == ["pbs1"]
    assert box.poweroffs == []
    with session_scope() as session:
        assert session.scalars(select(Run)).one().status == RunStatus.FAILURE


def test_the_next_route_skips_the_wake_of_a_box_left_on(temp_config, temp_db):
    # Acceptance 3, second half: the box the failed run left awake is not woken again.
    service, box = make_service(FakeBox(reachable=[False, True]))

    enqueue(service, "r1", failing_job, trigger=RunTrigger.SCHEDULED)
    drain(service)
    enqueue(service, "r2", ok_job, trigger=RunTrigger.SCHEDULED)
    drain(service)

    assert box.wol == ["pbs1"]  # one packet for both runs
    assert box.poweroffs == ["pbs1"]  # r2 succeeded, so it closes the box


def test_a_manual_run_can_keep_the_pbs_on(temp_config, temp_db):
    service, box = make_service()

    enqueue(service, "r1", ok_job, trigger=RunTrigger.MANUAL, power_off=False)
    drain(service)

    assert box.poweroffs == []


def test_a_sync_route_leases_both_boxes(temp_config, temp_db):
    # A sync route holds two leases — target and source — released independently.
    service, box = make_service(FakeBox(reachable=[False, True]))

    enqueue(service, "sync", ok_job, trigger=RunTrigger.SCHEDULED)
    drain(service)

    assert box.wol == ["pbs2"]  # the target was down at its probe; the source answered
    assert sorted(box.poweroffs) == ["pbs1", "pbs2"]


def test_an_unreachable_pbs_fails_the_run_without_running_the_job(temp_config, temp_db):
    service, box = make_service(FakeBox(reachable=False))
    ran = []
    seen: list[RunContext] = []
    service.deps.notify = seen.append

    enqueue(service, "r1", lambda *_a: ran.append(1), trigger=RunTrigger.SCHEDULED)
    drain(service)

    assert ran == []
    assert box.poweroffs == []
    with session_scope() as session:
        run = session.scalars(select(Run)).one()
        assert run.status == RunStatus.FAILURE
        assert "not reachable" in (run.error or "")

    # "The backup server never came up" is the failure this product exists to report, and
    # it used to send nothing at all: the handler re-raised, unwinding past _notify.
    assert len(seen) == 1
    assert seen[0].run.status == RunStatus.FAILURE
    assert seen[0].route is not None and seen[0].route.id == "r1"


def test_a_sync_route_releases_the_box_that_did_wake_when_the_other_does_not(
    temp_config, temp_db
):
    """The second-order half of the same bug: one lease held, the other unreachable.

    The re-raise skipped ``_release_all`` too, so the box that *did* answer was left awake
    with no POWEROFF step in the timeline and nothing queued to shut it down.
    """
    # The sync route acquires its target (pbs2) first, then its source (pbs1).
    service, box = make_service(FakeBox(unreachable={"pbs1"}))
    seen: list[RunContext] = []
    service.deps.notify = seen.append

    enqueue(service, "sync", lambda *_a: None, trigger=RunTrigger.SCHEDULED)
    drain(service)

    with session_scope() as session:
        run = session.scalars(select(Run)).one()
        assert run.status == RunStatus.FAILURE
        steps = {s.name: s.status for s in run.steps}
    # pbs2 woke, so its release is recorded; a failed run leaves it on for inspection.
    assert "poweroff:pbs2" in steps
    assert box.poweroffs == []
    assert seen and seen[0].left_on == ["pbs2"]


def test_stopping_a_run_mid_wake_still_powers_the_woken_box_off(temp_config, temp_db):
    """The user stopped the run while the box was booting — someone still has to close it.

    The magic packet was already on the wire, so the PBS comes up regardless. ``acquire``
    registered no holder (it only did so on success), so ``_release_all`` got an empty list:
    no POWEROFF step, no release, and a machine left running until somebody noticed it. The
    stop dialog's power-off toggle had nothing to act on.
    """
    # probe: down -> the wake wait: still down, and cancelled by then -> the grace wait: up.
    service, box = make_service(FakeBox(reachable=[False, False, True]))
    ran: list[int] = []
    seen: list[RunContext] = []
    service.deps.notify = seen.append

    packet = service.lease._deps.send_wol

    def wol_then_stop(pbs) -> None:
        # Exactly where a real Stop lands: after the packet, during the wait for the box.
        packet(pbs)
        assert service.cancel(service._current_run_id, power_off=True) is True

    service.lease._deps.send_wol = wol_then_stop

    enqueue(service, "r1", lambda *_a: ran.append(1), trigger=RunTrigger.MANUAL)
    drain(service)

    assert ran == []  # a stopped run never starts its cycle
    assert box.wol == ["pbs1"]  # one packet: no retry ladder after the stop
    assert box.poweroffs == ["pbs1"]
    assert seen == []  # a stopped run stays silent on purpose
    with session_scope() as session:
        run = session.scalars(select(Run)).one()
        assert run.status == RunStatus.ABORTED
        step = next(s for s in run.steps if s.name == StepName.POWEROFF)
        assert step.detail_key == ReleaseOutcome.POWERED_OFF.key


def test_stopping_a_run_mid_wake_honours_keeping_the_box_on(temp_config, temp_db):
    """The same path with the stop dialog's toggle off: the box is leased and released, but
    deliberately left running — the usual reason to stop a run is to go and work on it."""
    service, box = make_service(FakeBox(reachable=[False, False, True]))
    packet = service.lease._deps.send_wol

    def wol_then_stop(pbs) -> None:
        packet(pbs)
        service.cancel(service._current_run_id, power_off=False)

    service.lease._deps.send_wol = wol_then_stop

    enqueue(service, "r1", lambda *_a: None, trigger=RunTrigger.MANUAL)
    drain(service)

    assert box.poweroffs == []
    with session_scope() as session:
        run = session.scalars(select(Run)).one()
        step = next(s for s in run.steps if s.name == StepName.POWEROFF)
        assert step.detail_key == ReleaseOutcome.LEFT_ON.key


# --- what the run reports as left powered on ---------------------------------
#
# "PBS left powered on" is the notification's energy warning, so it must fire exactly when
# a box is still awake with nothing left to shut it down. The lease is the only place that
# can tell the reasons apart, and it hands its verdict to the message through
# RunContext.left_on (tests/test_notify.py covers the rendering).


def notifying_job(status=RunStatus.SUCCESS):
    """A job that finishes with ``status`` and asks to be notified about it."""

    def job(config, subject, recorder, _deps):
        recorder.finish(status)
        return RunContext(config=config, run=recorder.run)

    return job


def sent(service: JobService) -> list[RunContext]:
    seen: list[RunContext] = []
    service.deps.notify = seen.append
    return seen


def test_an_always_on_pbs_is_never_reported_as_left_on(temp_config, temp_db):
    # The common false positive: a managed_power: false box records a SKIPPED power-off on
    # *every* successful run, so the old step-based rule warned about it every single night.
    service, box = make_service()
    service._store.config.pbss[0].managed_power = False
    seen = sent(service)

    enqueue(service, "r1", notifying_job(), trigger=RunTrigger.SCHEDULED)
    drain(service)

    assert box.poweroffs == []
    assert seen[0].left_on == []


def test_a_failed_run_reports_the_box_it_left_awake(temp_config, temp_db):
    service, box = make_service()
    seen = sent(service)

    enqueue(service, "r1", notifying_job(RunStatus.FAILURE), trigger=RunTrigger.SCHEDULED)
    drain(service)

    assert box.poweroffs == []  # left up for inspection
    assert seen[0].left_on == ["pbs1"]


def test_a_sync_route_reports_only_the_box_that_stayed_up(temp_config, temp_db):
    # The old false negative: one box powering off hid the other one staying awake, because
    # the rule ORed the steps across the whole run instead of pairing them per device.
    service, box = make_service()
    service._store.config.pbss[1].managed_power = False  # target pbs2 is always on
    seen = sent(service)

    enqueue(service, "sync", notifying_job(RunStatus.FAILURE), trigger=RunTrigger.SCHEDULED)
    drain(service)

    assert box.poweroffs == []
    assert seen[0].left_on == ["pbs1"]  # not pbs2: that one is never ours to power down


def test_a_box_a_queued_route_still_needs_is_not_reported_as_left_on(temp_config, temp_db):
    # It stays awake on purpose, and the run that follows will close it.
    service, box = make_service()
    seen = sent(service)
    gate = Gate()

    enqueue(service, "r1", gate.job, trigger=RunTrigger.SCHEDULED)
    assert gate.started.wait(timeout=5)
    enqueue(service, "r2", notifying_job(), trigger=RunTrigger.SCHEDULED)
    gate.release.set()
    drain(service)

    assert box.poweroffs == ["pbs1"]  # only r2, the last holder, closed it
    assert seen[0].left_on == []


def test_a_sync_between_two_datastores_of_one_box_powers_it_off_once(temp_config, temp_db):
    """The G3-5 regression, found on real hardware.

    ``pbs2`` and ``pbs3`` are one machine. Keyed per device, the run held two leases on it:
    releasing the first shut the box down, releasing the second reached a machine already
    going down and reported LEFT_ON — so a run that had tidied up perfectly notified the
    user "PBS left powered on — check it".
    """
    service, box = make_service()
    config = service._store.config
    config.pbss.append(
        PbsDevice(id="pbs3", host="192.0.2.21", datastore="archive", mac="00:11:22:33:44:66")
    )
    config.routes.append(Route(id="local-sync", kind="sync", source_pbs="pbs2", target="pbs3"))
    seen = sent(service)

    enqueue(service, "local-sync", notifying_job(), trigger=RunTrigger.SCHEDULED)
    drain(service)

    assert seen[0].left_on == []  # the warning that was false
    assert box.poweroffs == ["pbs3"]  # one machine, one power-off
    with session_scope() as session:
        run = session.scalars(select(Run)).one()
        # One physical event, one step each — not a pair where the second reports on a box
        # the first already shut down.
        assert [s.name for s in run.steps].count(StepName.POWEROFF) == 1
        assert [s.name for s in run.steps].count(StepName.WAIT) == 1


def test_the_power_off_step_records_why_the_box_stayed_up(temp_config, temp_db):
    # The timeline says which of the three "not powered off" reasons applied, so the run
    # detail view doesn't just show a bare SKIPPED.
    service, _box = make_service()
    service._store.config.pbss[0].managed_power = False

    enqueue(service, "r1", ok_job, trigger=RunTrigger.SCHEDULED)
    drain(service)

    with session_scope() as session:
        run = session.scalars(select(Run)).one()
        step = next(s for s in run.steps if s.name == StepName.POWEROFF)
        assert step.status == StepStatus.SKIPPED
        assert step.detail == ReleaseOutcome.UNMANAGED


def test_a_run_on_another_pbs_does_not_hold_the_first(temp_config, temp_db):
    service, box = make_service()

    enqueue(service, "r1", ok_job, trigger=RunTrigger.SCHEDULED)
    drain(service)
    enqueue(service, "r3", ok_job, trigger=RunTrigger.SCHEDULED)
    drain(service)

    assert box.poweroffs == ["pbs1", "pbs2"]


def test_a_single_box_run_records_an_unlabelled_power_off_step(temp_config, temp_db):
    """``multi`` decides whether the POWEROFF step is labelled with a device id.

    A one-box run reads better as a bare "poweroff", and a sync route's two boxes need the
    labels to be told apart. Only the labelled case was covered, so the boundary that picks
    between them was free to move.
    """
    service, _box = make_service()

    enqueue(service, "r1", ok_job, trigger=RunTrigger.SCHEDULED)
    drain(service)

    with session_scope() as session:
        names = {s.name for s in session.scalars(select(Run)).one().steps}
    assert StepName.POWEROFF in names
    assert not any(str(n).startswith("poweroff:") for n in names)


def test_a_two_box_run_labels_each_power_off_step(temp_config, temp_db):
    service, _box = make_service()

    enqueue(service, "sync", ok_job, trigger=RunTrigger.SCHEDULED)
    drain(service)

    with session_scope() as session:
        names = {s.name for s in session.scalars(select(Run)).one().steps}
    assert {"poweroff:pbs1", "poweroff:pbs2"} <= names


def test_a_powered_off_box_is_not_reported_as_left_on(temp_config, temp_db):
    # The notification's "left powered on" line is built from the release outcome, so
    # confusing POWERED_OFF with LEFT_ON would warn about a box that went to sleep fine.
    service, box = make_service()
    seen = sent(service)

    enqueue(service, "r1", notifying_job(), trigger=RunTrigger.SCHEDULED)
    drain(service)

    assert box.poweroffs == ["pbs1"]
    assert seen[0].left_on == []
