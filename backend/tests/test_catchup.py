"""BE-R1: the startup check that detects a scheduled backup missed while the process was down."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.config import Config
from app.core.catchup import check_missed_backup
from app.core.scheduler import Scheduler
from app.db import session_scope
from app.db.models import Run, RunKind, RunStatus, RunTrigger


class _RecordingNotifier:
    """Duck-typed stand-in for NotificationService.send_missed_backup."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def send_missed_backup(self, config, missed_at, last_run_at, next_at):
        self.calls.append((missed_at, last_run_at, next_at))


def _add_cycle(started_at: datetime, status: RunStatus = RunStatus.SUCCESS) -> None:
    with session_scope() as session:
        run = Run(kind=RunKind.CYCLE, trigger=RunTrigger.SCHEDULED, status=status)
        run.started_at = started_at
        run.finished_at = started_at + timedelta(minutes=2)
        session.add(run)


def _sched(schedule: str = "0 4 * * *", enabled: bool = True) -> Scheduler:
    cfg = Config()
    cfg.backup.enabled = enabled
    cfg.backup.schedule = schedule
    sched = Scheduler(lambda _t: None, timezone="UTC")
    sched.rearm(cfg)
    return sched


def test_notifies_when_a_scheduled_backup_was_missed(temp_db):
    _add_cycle(datetime(2026, 7, 8, 4, 0, 0, tzinfo=UTC))
    notifier = _RecordingNotifier()
    now = datetime(2026, 7, 11, 10, 0, 0, tzinfo=UTC)
    missed = check_missed_backup(Config(), _sched(), notifier, now=now)
    assert missed == datetime(2026, 7, 9, 4, 0, 0, tzinfo=UTC)
    assert len(notifier.calls) == 1
    assert notifier.calls[0][0] == missed  # missed_at
    assert notifier.calls[0][1] == datetime(2026, 7, 8, 4, 0, 0, tzinfo=UTC)  # anchor


def test_no_notification_when_no_slot_elapsed(temp_db):
    _add_cycle(datetime(2026, 7, 11, 4, 0, 5, tzinfo=UTC))
    notifier = _RecordingNotifier()
    now = datetime(2026, 7, 11, 4, 0, 30, tzinfo=UTC)
    assert check_missed_backup(Config(), _sched(), notifier, now=now) is None
    assert notifier.calls == []


def test_no_notification_on_fresh_install_with_no_cycles(temp_db):
    notifier = _RecordingNotifier()
    now = datetime(2026, 7, 11, 10, 0, 0, tzinfo=UTC)
    assert check_missed_backup(Config(), _sched(), notifier, now=now) is None
    assert notifier.calls == []


def test_aborted_last_run_anchors_and_is_not_reflagged(temp_db):
    # A failed/aborted run at the last slot counts as "attempted" (already notified), so its
    # own slot must not be re-reported as a downtime miss.
    _add_cycle(datetime(2026, 7, 11, 4, 0, 3, tzinfo=UTC), status=RunStatus.ABORTED)
    notifier = _RecordingNotifier()
    now = datetime(2026, 7, 11, 6, 0, 0, tzinfo=UTC)
    assert check_missed_backup(Config(), _sched(), notifier, now=now) is None
    assert notifier.calls == []


def test_no_notification_when_backups_disabled(temp_db):
    _add_cycle(datetime(2026, 7, 8, 4, 0, 0, tzinfo=UTC))
    notifier = _RecordingNotifier()
    now = datetime(2026, 7, 11, 10, 0, 0, tzinfo=UTC)
    assert check_missed_backup(Config(), _sched(enabled=False), notifier, now=now) is None
    assert notifier.calls == []
