"""Unit tests for the watch loop an External route runs.

The route cycles themselves are covered by ``test_route_backup.py`` (backup) and
``test_route_kinds.py`` (sync/external/verify). What is left here is the piece worth
testing on its own: ``watch_external_tasks`` is a two-phase timeout loop with a resettable
countdown, and driving it through a whole cycle would hide exactly the timing it gets wrong.
"""

from __future__ import annotations

from fakes import FakePbs

from app.config import PbsExternalConfig
from app.jobs.backup_cycle import watch_external_tasks


def _fake_clock():
    """A monotonic clock and a sleep that advances it, so the tests take no real time."""
    t = {"now": 0.0}

    def clock() -> float:
        return t["now"]

    def sleep(seconds: float) -> None:
        t["now"] += seconds

    return t, clock, sleep


def test_watch_first_task_ends_the_grace_wait_early():
    t, clock, sleep = _fake_clock()
    pbs = FakePbs(active_tasks_seq=[[], [], [{"upid": "A"}]])

    n = watch_external_tasks(
        pbs,
        PbsExternalConfig(first_task_wait=900, idle_wait=0),
        cancelled=lambda: False,
        sleep=sleep,
        clock=clock,
    )

    assert n == 1
    assert t["now"] == 20.0  # ended when the task appeared, not after the full 900s


def test_watch_quiet_countdown_resets_when_a_new_task_starts():
    _t, clock, sleep = _fake_clock()
    # Task A, a gap shorter than idle_wait, task B, then real silence.
    pbs = FakePbs(active_tasks_seq=[[{"upid": "A"}], [], [{"upid": "B"}], [], [], []])

    n = watch_external_tasks(
        pbs,
        PbsExternalConfig(first_task_wait=0, idle_wait=15),
        cancelled=lambda: False,
        sleep=sleep,
        clock=clock,
    )

    assert n == 2  # the gap after A did not power off before B ran


def test_watch_returns_none_when_no_task_ever_appears():
    t, clock, sleep = _fake_clock()

    n = watch_external_tasks(
        FakePbs(),
        PbsExternalConfig(first_task_wait=25, idle_wait=300),
        cancelled=lambda: False,
        sleep=sleep,
        clock=clock,
    )

    assert n is None
    assert t["now"] >= 25.0
