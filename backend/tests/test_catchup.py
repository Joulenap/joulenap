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

    reported = check_missed_runs(cfg, _sched(cfg), notifier, now=now)

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

    check_missed_runs(cfg, _sched(cfg), notifier, now=now)

    assert [c[0] for c in notifier.calls] == ["nightly"]


def test_no_notification_when_no_slot_elapsed(temp_db):
    _add_run("nightly", datetime(2026, 7, 11, 2, 0, 5, tzinfo=UTC))
    cfg = _config()
    notifier = _RecordingNotifier()
    now = datetime(2026, 7, 11, 2, 0, 30, tzinfo=UTC)

    assert check_missed_runs(cfg, _sched(cfg), notifier, now=now) == []
    assert notifier.calls == []


def test_no_notification_for_a_route_that_never_ran(temp_db):
    # Fresh install, or a route created today — nothing could have been missed.
    cfg = _config()
    notifier = _RecordingNotifier()
    now = datetime(2026, 7, 11, 10, 0, 0, tzinfo=UTC)

    assert check_missed_runs(cfg, _sched(cfg), notifier, now=now) == []
    assert notifier.calls == []


def test_an_aborted_last_run_anchors_and_is_not_reflagged(temp_db):
    # A failed/aborted run at the last slot counts as "attempted" (already notified), so its
    # own slot must not be re-reported as a downtime miss.
    _add_run("nightly", datetime(2026, 7, 11, 2, 0, 3, tzinfo=UTC), status=RunStatus.ABORTED)
    cfg = _config()
    notifier = _RecordingNotifier()
    now = datetime(2026, 7, 11, 6, 0, 0, tzinfo=UTC)

    assert check_missed_runs(cfg, _sched(cfg), notifier, now=now) == []
    assert notifier.calls == []


def test_nothing_is_reported_with_the_kill_switch_off(temp_db):
    _add_run("nightly", datetime(2026, 7, 8, 2, 0, 0, tzinfo=UTC))
    cfg = _config(scheduler_enabled=False)
    notifier = _RecordingNotifier()
    now = datetime(2026, 7, 11, 10, 0, 0, tzinfo=UTC)

    assert check_missed_runs(cfg, _sched(cfg), notifier, now=now) == []
    assert notifier.calls == []


def test_a_disabled_route_is_never_reported(temp_db):
    _add_run("nightly", datetime(2026, 7, 8, 2, 0, 0, tzinfo=UTC))
    cfg = _config()
    next(r for r in cfg.routes if r.id == "nightly").enabled = False
    notifier = _RecordingNotifier()
    now = datetime(2026, 7, 11, 10, 0, 0, tzinfo=UTC)

    assert check_missed_runs(cfg, _sched(cfg), notifier, now=now) == []
    assert notifier.calls == []
