"""BE-R1: the startup check that detects scheduled runs missed while the process was down.

Per-route now: each route is compared against *its own* last finished run, so a nightly
backup that was missed is reported even while a weekly route was never due.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conftest import with_devices

from app.config import Config
from app.core.catchup import check_missed_runs
from app.core.scheduler import Scheduler
from app.db import session_scope
from app.db.models import Run, RunKind, RunStatus, RunTrigger


class _RecordingNotifier:
    """Duck-typed stand-in for NotificationService.send_missed_backup."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def send_missed_backup(self, config, route, missed_at, last_run_at, next_at):
        self.calls.append((route.id, missed_at, last_run_at, next_at))


def _config(**app_overrides) -> Config:
    cfg = with_devices(Config())
    for key, value in app_overrides.items():
        setattr(cfg.app, key, value)
    return cfg


def _add_run(
    route_id: str, started_at: datetime, status: RunStatus = RunStatus.SUCCESS
) -> None:
    with session_scope() as session:
        run = Run(
            kind=RunKind.CYCLE,
            trigger=RunTrigger.SCHEDULED,
            status=status,
            route_id=route_id,
            route_name=route_id,
        )
        run.started_at = started_at
        run.finished_at = started_at + timedelta(minutes=2)
        session.add(run)


#: Every case below is a genuine-downtime scenario, so the app is "last seen" before any of
#: their runs — which makes the heartbeat clamp a no-op and leaves each assertion about the
#: run history alone. The clamp itself has its own tests at the bottom.
_DOWN_SINCE = datetime(2026, 7, 1, tzinfo=UTC)


def _sched(cfg: Config) -> Scheduler:
    sched = Scheduler(lambda _id, _t: None, timezone="UTC")
    sched.rearm(cfg)
    return sched


def test_notifies_when_a_scheduled_run_was_missed(temp_db):
    # "nightly" fires daily at 02:00; it last ran on the 8th and we're back on the 11th.
    _add_run("nightly", datetime(2026, 7, 8, 2, 0, 0, tzinfo=UTC))
    cfg = _config()
    notifier = _RecordingNotifier()
    now = datetime(2026, 7, 11, 10, 0, 0, tzinfo=UTC)

    reported = check_missed_runs(cfg, _sched(cfg), notifier, now=now, last_seen=_DOWN_SINCE)

    assert [(r.id, at) for r, at in reported] == [
        ("nightly", datetime(2026, 7, 9, 2, 0, 0, tzinfo=UTC))
    ]
    assert len(notifier.calls) == 1
    route_id, missed_at, anchor, _next_at = notifier.calls[0]
    assert route_id == "nightly"
    assert missed_at == datetime(2026, 7, 9, 2, 0, 0, tzinfo=UTC)
    assert anchor == datetime(2026, 7, 8, 2, 0, 0, tzinfo=UTC)


def test_each_route_is_anchored_on_its_own_history(temp_db):
    # Both ran on the 8th; only "nightly" (daily) was due since. "lab" is Saturdays only and
    # the 11th is a Saturday whose 04:00 slot is still ahead of `now`.
    _add_run("nightly", datetime(2026, 7, 8, 2, 0, 0, tzinfo=UTC))
    _add_run("lab", datetime(2026, 7, 4, 4, 0, 0, tzinfo=UTC))
    cfg = _config()
    notifier = _RecordingNotifier()
    now = datetime(2026, 7, 11, 3, 0, 0, tzinfo=UTC)

    check_missed_runs(cfg, _sched(cfg), notifier, now=now, last_seen=_DOWN_SINCE)

    assert [c[0] for c in notifier.calls] == ["nightly"]


def test_no_notification_when_no_slot_elapsed(temp_db):
    _add_run("nightly", datetime(2026, 7, 11, 2, 0, 5, tzinfo=UTC))
    cfg = _config()
    notifier = _RecordingNotifier()
    now = datetime(2026, 7, 11, 2, 0, 30, tzinfo=UTC)

    assert check_missed_runs(cfg, _sched(cfg), notifier, now=now, last_seen=_DOWN_SINCE) == []
    assert notifier.calls == []


def test_no_notification_for_a_route_that_never_ran(temp_db):
    # Fresh install, or a route created today — nothing could have been missed.
    cfg = _config()
    notifier = _RecordingNotifier()
    now = datetime(2026, 7, 11, 10, 0, 0, tzinfo=UTC)

    assert check_missed_runs(cfg, _sched(cfg), notifier, now=now, last_seen=_DOWN_SINCE) == []
    assert notifier.calls == []


def test_an_aborted_last_run_anchors_and_is_not_reflagged(temp_db):
    # A failed/aborted run at the last slot counts as "attempted" (already notified), so its
    # own slot must not be re-reported as a downtime miss.
    _add_run("nightly", datetime(2026, 7, 11, 2, 0, 3, tzinfo=UTC), status=RunStatus.ABORTED)
    cfg = _config()
    notifier = _RecordingNotifier()
    now = datetime(2026, 7, 11, 6, 0, 0, tzinfo=UTC)

    assert check_missed_runs(cfg, _sched(cfg), notifier, now=now, last_seen=_DOWN_SINCE) == []
    assert notifier.calls == []


def test_nothing_is_reported_with_the_kill_switch_off(temp_db):
    _add_run("nightly", datetime(2026, 7, 8, 2, 0, 0, tzinfo=UTC))
    cfg = _config(scheduler_enabled=False)
    notifier = _RecordingNotifier()
    now = datetime(2026, 7, 11, 10, 0, 0, tzinfo=UTC)

    assert check_missed_runs(cfg, _sched(cfg), notifier, now=now, last_seen=_DOWN_SINCE) == []
    assert notifier.calls == []


def test_a_disabled_route_is_never_reported(temp_db):
    _add_run("nightly", datetime(2026, 7, 8, 2, 0, 0, tzinfo=UTC))
    cfg = _config()
    next(r for r in cfg.routes if r.id == "nightly").enabled = False
    notifier = _RecordingNotifier()
    now = datetime(2026, 7, 11, 10, 0, 0, tzinfo=UTC)

    assert check_missed_runs(cfg, _sched(cfg), notifier, now=now, last_seen=_DOWN_SINCE) == []
    assert notifier.calls == []


# --- the downtime window (G3-6) ----------------------------------------------
#
# "A fire was due and no run happened" is not the same fact as "we were down". The check used
# to treat them as one, and asserted "Joulenap was offline" about a process that had been
# running the whole time.


def test_a_fire_the_app_was_up_for_is_not_reported(temp_db):
    """The reported bug: a schedule changed to a time-of-day earlier than now.

    "nightly" last ran on the 8th and its 02:00 slot has come round three times since — but
    the app was alive an hour ago, so it either ran them, or the schedule that says it should
    have did not exist yet. Either way nobody was offline and there is nothing to report.
    """
    _add_run("nightly", datetime(2026, 7, 8, 2, 0, 0, tzinfo=UTC))
    cfg = _config()
    notifier = _RecordingNotifier()
    now = datetime(2026, 7, 11, 10, 0, 0, tzinfo=UTC)

    reported = check_missed_runs(
        cfg,
        _sched(cfg),
        notifier,
        now=now,
        last_seen=datetime(2026, 7, 11, 9, 0, 0, tzinfo=UTC),
    )

    assert reported == []
    assert notifier.calls == []


def test_only_the_fires_inside_the_downtime_window_are_reported(temp_db):
    # Down from the 10th at 03:00 to the 11th at 10:00: the 9th and the 10th's 02:00 slots
    # were served by a running app, the 11th's was not.
    _add_run("nightly", datetime(2026, 7, 8, 2, 0, 0, tzinfo=UTC))
    cfg = _config()
    notifier = _RecordingNotifier()

    reported = check_missed_runs(
        cfg,
        _sched(cfg),
        notifier,
        now=datetime(2026, 7, 11, 10, 0, 0, tzinfo=UTC),
        last_seen=datetime(2026, 7, 10, 3, 0, 0, tzinfo=UTC),
    )

    assert [at for _r, at in reported] == [datetime(2026, 7, 11, 2, 0, 0, tzinfo=UTC)]
    # The message still shows the real last run, not the window's start.
    assert notifier.calls[0][2] == datetime(2026, 7, 8, 2, 0, 0, tzinfo=UTC)


def test_nothing_is_reported_when_liveness_is_unknown(temp_db, monkeypatch):
    # First boot, or a data dir we cannot write: inventing downtime is exactly the failure
    # this guard exists to prevent, so silence is the only honest answer.
    _add_run("nightly", datetime(2026, 7, 8, 2, 0, 0, tzinfo=UTC))
    monkeypatch.setattr("app.core.heartbeat.last_seen", lambda: None)
    cfg = _config()
    notifier = _RecordingNotifier()

    reported = check_missed_runs(
        cfg, _sched(cfg), notifier, now=datetime(2026, 7, 11, 10, 0, 0, tzinfo=UTC)
    )

    assert reported == []
    assert notifier.calls == []
