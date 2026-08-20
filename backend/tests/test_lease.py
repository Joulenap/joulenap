"""PowerLease: wake once, power off last, and never touch an unmanaged box."""

from __future__ import annotations

import pytest
from fakes import FakeBox

from app.config import PbsDevice
from app.jobs.lease import PbsUnreachableError, PowerLease, ReleaseOutcome


def make_pbs(**over) -> PbsDevice:
    base = {
        "id": "pbs1",
        "host": "192.0.2.20",
        "datastore": "backup",
        "mac": "00:11:22:33:44:55",
        "wait_timeout": 30,
        "wol_retries": 2,
    }
    return PbsDevice(**{**base, **over})


# --- acquire -----------------------------------------------------------------


def test_an_awake_pbs_is_not_woken():
    box = FakeBox(reachable=True)
    lease = PowerLease(box.deps())

    assert lease.acquire(make_pbs()) is True  # was_awake
    assert box.wol == []
    assert box.waits == [0]  # a single probe, no waiting


def test_a_sleeping_pbs_is_woken_once():
    box = FakeBox(reachable=[False, True])
    lease = PowerLease(box.deps())

    assert lease.acquire(make_pbs()) is False  # we woke it
    assert box.wol == ["pbs1"]
    assert box.waits == [0, 30]  # probe, then the real wake wait


def test_wake_is_retried_wol_retries_times_then_fails():
    box = FakeBox(reachable=False)
    lease = PowerLease(box.deps())

    with pytest.raises(PbsUnreachableError, match="3 wake attempt"):
        lease.acquire(make_pbs(wol_retries=2))

    assert box.wol == ["pbs1"] * 3  # wol_retries + 1 attempts
    assert lease.state(make_pbs()).holders == 0  # a failed acquire registers no holder


def test_a_run_stopped_before_the_wake_sends_no_packet():
    """Nobody has to clean up a box we never woke. This used to fire the packet anyway and
    then abandon it — so a stopped run left a machine booting with no lease on it, and
    nothing that would ever put it back to sleep."""
    box = FakeBox(reachable=False)
    lease = PowerLease(box.deps(), cancelled=lambda: True)

    with pytest.raises(PbsUnreachableError, match="Cancelled"):
        lease.acquire(make_pbs())

    assert box.wol == []
    assert lease.state(make_pbs()).holders == 0


def test_a_cancel_after_the_packet_sees_the_wake_through():
    """Once the magic packet is out the box is coming up whatever we decide, so finish the
    wait and hand the caller a holder: releasing that lease is the only thing that powers the
    box down again. Bounded by what is left of the interrupted attempt, never a fresh one."""
    box = FakeBox(reachable=[False, False, True])
    # Cancelled from the moment the first packet goes out.
    lease = PowerLease(box.deps(), cancelled=lambda: bool(box.wol))

    assert lease.acquire(make_pbs()) is False  # we woke it

    assert box.wol == ["pbs1"]  # no second attempt after the stop
    assert len(box.waits) == 3  # instant probe, the cancelled wait, the grace wait
    assert lease.state(make_pbs()).holders == 1


def test_second_holder_does_not_probe_or_wake():
    box = FakeBox(reachable=[False, True])
    lease = PowerLease(box.deps())
    pbs = make_pbs()

    lease.acquire(pbs)
    assert lease.acquire(pbs) is False  # the recorded was_awake, no fresh probe
    assert box.wol == ["pbs1"]
    assert box.waits == [0, 30]
    assert lease.state(make_pbs()).holders == 2


def test_two_devices_on_one_machine_share_a_lease():
    """Two datastores on one box are two devices and one power switch.

    Keyed per device, a single run holding both powered the machine off on the first
    release and then reported the second as "left powered on" — a false warning about a box
    that had gone to sleep correctly.
    """
    box = FakeBox(reachable=[False, True])
    lease = PowerLease(box.deps())
    lab = make_pbs(id="pbs2", datastore="lab")
    archive = make_pbs(id="pbs3", datastore="archive")  # same host as pbs2

    assert lease.acquire(lab) is False  # woken
    assert lease.acquire(archive) is False  # the recorded was_awake — no second packet
    assert box.wol == ["pbs2"]
    assert lease.state(archive).holders == 2

    assert lease.release(lab) is ReleaseOutcome.STILL_NEEDED
    assert box.poweroffs == []  # the other datastore is still working
    assert lease.release(archive) is ReleaseOutcome.POWERED_OFF
    assert box.poweroffs == ["pbs3"]


def test_a_queued_run_on_the_other_datastore_of_a_box_keeps_it_on():
    """The waste half of the same bug: pending keys and lease keys must be the same space,
    or a queued run on one datastore lets the box sleep after a run on the other."""
    box = FakeBox()
    lease = PowerLease(box.deps(), pending_pbs_keys=lambda: {"192.0.2.20"})

    lease.acquire(make_pbs(id="pbs2"))
    assert lease.release(make_pbs(id="pbs2")) is ReleaseOutcome.STILL_NEEDED
    assert box.poweroffs == []


def test_a_device_with_no_host_yet_does_not_collide():
    # Half-configured entries are legal mid-wizard and all have host "" — keyed on that
    # alone they would share one refcount.
    box = FakeBox(reachable=True)
    lease = PowerLease(box.deps())

    lease.acquire(make_pbs(id="draft-a", host="", managed_power=False, mac=""))
    assert lease.state(make_pbs(id="draft-b", host="", managed_power=False, mac="")).holders == 0


# --- unmanaged power ---------------------------------------------------------


def test_unmanaged_pbs_is_only_probed():
    box = FakeBox(reachable=True)
    lease = PowerLease(box.deps())
    pbs = make_pbs(managed_power=False, mac="")

    assert lease.acquire(pbs) is True
    assert lease.release(pbs) is ReleaseOutcome.UNMANAGED  # never Joulenap's to power
    assert box.wol == []
    assert box.poweroffs == []


def test_unmanaged_pbs_that_is_off_fails_the_run_without_wol():
    box = FakeBox(reachable=False)
    lease = PowerLease(box.deps())

    with pytest.raises(PbsUnreachableError, match="managed_power"):
        lease.acquire(make_pbs(managed_power=False, mac=""))

    assert box.wol == []


# --- release -----------------------------------------------------------------


def test_last_holder_powers_the_box_off():
    box = FakeBox()
    lease = PowerLease(box.deps())
    pbs = make_pbs()

    lease.acquire(pbs)
    assert lease.release(pbs) is ReleaseOutcome.POWERED_OFF
    assert box.poweroffs == ["pbs1"]
    assert lease.state(make_pbs()).holders == 0


def test_release_with_another_holder_leaves_the_box_on():
    box = FakeBox()
    lease = PowerLease(box.deps())
    pbs = make_pbs()

    lease.acquire(pbs)
    lease.acquire(pbs)
    assert lease.release(pbs) is ReleaseOutcome.STILL_NEEDED
    assert box.poweroffs == []
    assert lease.release(pbs) is ReleaseOutcome.POWERED_OFF


def test_a_queued_route_on_the_same_pbs_keeps_it_on():
    box = FakeBox()
    lease = PowerLease(box.deps(), pending_pbs_keys=lambda: {"192.0.2.20"})
    pbs = make_pbs()

    lease.acquire(pbs)
    assert lease.release(pbs) is ReleaseOutcome.STILL_NEEDED
    assert box.poweroffs == []


def test_power_off_false_leaves_the_box_on():
    box = FakeBox()
    lease = PowerLease(box.deps())
    pbs = make_pbs()

    lease.acquire(pbs)
    assert lease.release(pbs, power_off=False) is ReleaseOutcome.LEFT_ON
    assert box.poweroffs == []


def test_a_busy_pbs_is_not_interrupted():
    box = FakeBox(idle=False)
    lease = PowerLease(box.deps())
    pbs = make_pbs()

    lease.acquire(pbs)
    assert lease.release(pbs) is ReleaseOutcome.LEFT_ON
    assert box.poweroffs == []


def test_a_failing_idle_check_fails_open():
    # A flaky task check must not keep the box awake all night.
    box = FakeBox(idle_error=RuntimeError("403"))
    lease = PowerLease(box.deps())
    pbs = make_pbs()

    lease.acquire(pbs)
    assert lease.release(pbs) is ReleaseOutcome.POWERED_OFF
    assert box.poweroffs == ["pbs1"]


def test_the_idle_check_is_skipped_when_the_wait_is_zero():
    """poweroff_task_wait=0 means "don't ask, just shut it down".

    Asserted on the recorded call, not by raising: a raising check fails open (the test
    above), so it powers off either way and the assertion could not tell the two apart.
    """
    box = FakeBox()
    lease = PowerLease(box.deps())
    pbs = make_pbs(poweroff_task_wait=0)

    lease.acquire(pbs)
    assert lease.release(pbs) is ReleaseOutcome.POWERED_OFF
    assert box.idle_checks == []


def test_the_idle_check_runs_when_a_wait_is_configured():
    # The other side of the same boundary, so neither direction can drift unnoticed.
    box = FakeBox()
    lease = PowerLease(box.deps())
    pbs = make_pbs(poweroff_task_wait=1)

    lease.acquire(pbs)
    assert lease.release(pbs) is ReleaseOutcome.POWERED_OFF
    assert box.idle_checks == ["pbs1"]


def test_a_failed_power_off_is_swallowed():
    # The run's data is already safe: a failed power-off costs energy, not correctness.
    box = FakeBox(poweroff_error=RuntimeError("ssh refused"))
    lease = PowerLease(box.deps())
    pbs = make_pbs()

    lease.acquire(pbs)
    assert lease.release(pbs) is ReleaseOutcome.LEFT_ON
    assert box.poweroffs == []


def test_releasing_an_unheld_lease_is_ignored():
    box = FakeBox()
    lease = PowerLease(box.deps())

    assert lease.release(make_pbs()) is ReleaseOutcome.LEFT_ON
    assert lease.state(make_pbs()).holders == 0
    assert box.poweroffs == []


def test_a_box_left_on_is_not_remembered_as_awake():
    # Power-off skipped because a route was queued; the next acquire must probe again
    # rather than trust the stale was_awake of the previous holder.
    box = FakeBox(reachable=[False, True])
    lease = PowerLease(box.deps(), pending_pbs_keys=lambda: {"192.0.2.20"})
    pbs = make_pbs()

    assert lease.acquire(pbs) is False
    lease.release(pbs)
    assert lease.acquire(pbs) is True  # probed, found up
    assert box.wol == ["pbs1"]
