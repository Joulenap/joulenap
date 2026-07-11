"""Scheduler arming / re-arming. The backup job is the only cron job; GC has none."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger

from app.config import Config
from app.core.scheduler import (
    BACKUP_JOB_ID,
    PRUNE_JOB_ID,
    VERIFY_JOB_ID,
    Scheduler,
    _build_trigger,
    _translate_dow,
    resolve_timezone,
)


def _config(enabled: bool = True, schedule: str = "0 4 * * *") -> Config:
    cfg = Config()
    cfg.backup.enabled = enabled
    cfg.backup.schedule = schedule
    return cfg


def test_arms_backup_job_when_enabled():
    sched = Scheduler(lambda _trigger: None)
    sched.rearm(_config())

    job = sched.backup_job
    assert job is not None and job.id == BACKUP_JOB_ID
    assert isinstance(job.trigger, CronTrigger)


def test_no_job_when_disabled():
    sched = Scheduler(lambda _trigger: None)
    sched.rearm(_config(enabled=False))
    assert sched.backup_job is None


def test_only_one_job_armed_no_separate_gc_job():
    sched = Scheduler(lambda _trigger: None)
    sched.rearm(_config())
    assert len(sched._scheduler.get_jobs()) == 1


def test_rearm_replaces_existing_job():
    sched = Scheduler(lambda _trigger: None)
    sched.rearm(_config(schedule="0 4 * * *"))
    sched.rearm(_config(schedule="30 5 * * *"))
    jobs = sched._scheduler.get_jobs()
    assert len(jobs) == 1
    assert jobs[0].id == BACKUP_JOB_ID


def test_rearm_to_disabled_clears_job():
    sched = Scheduler(lambda _trigger: None)
    sched.rearm(_config())
    sched.rearm(_config(enabled=False))
    assert sched.backup_job is None


def test_next_run_time_computed_when_running():
    sched = Scheduler(lambda _trigger: None)
    sched.start()
    try:
        sched.rearm(_config())
        assert sched.next_run_time is not None
    finally:
        sched.shutdown()


def test_fire_backup_passes_scheduled_trigger():
    from app.db.models import RunTrigger

    seen: list[RunTrigger] = []
    sched = Scheduler(lambda trigger: seen.append(trigger))
    sched._fire_backup()
    assert seen == [RunTrigger.SCHEDULED]


def test_fire_backup_swallows_errors():
    def boom(_trigger):
        raise RuntimeError("already running")

    sched = Scheduler(boom)
    # Must not propagate — a raising fire would kill the scheduler thread.
    sched._fire_backup()


# --- history-prune housekeeping job -------------------------------------------


def test_arm_prune_adds_daily_job():
    sched = Scheduler(lambda _trigger: None, run_prune=lambda: None)
    sched.arm_prune()
    assert sched.prune_job is not None and sched.prune_job.id == PRUNE_JOB_ID


def test_arm_prune_noop_without_callback():
    sched = Scheduler(lambda _trigger: None)  # no prune callback
    sched.arm_prune()
    assert sched.prune_job is None


def test_rearm_preserves_prune_job():
    # Re-arming (or disabling) the backup job must not drop the prune housekeeping job.
    sched = Scheduler(lambda _trigger: None, run_prune=lambda: None)
    sched.arm_prune()
    sched.rearm(_config())
    sched.rearm(_config(enabled=False))
    assert sched.prune_job is not None
    assert {j.id for j in sched._scheduler.get_jobs()} == {PRUNE_JOB_ID}


def test_rearm_updates_prune_job_timezone():
    # A runtime timezone change (via rearm) must move the prune job into the new zone, not
    # leave it firing in the boot-time zone (BE-B7). Started so replace_existing dedups the
    # prune job (as it does in production, where rearm always runs on a started scheduler).
    sched = Scheduler(lambda _trigger: None, run_prune=lambda: None, timezone="UTC")
    sched.start()
    try:
        sched.arm_prune()
        assert sched.prune_job is not None
        assert str(sched.prune_job.trigger.timezone) == "UTC"

        cfg = _config()
        cfg.app.timezone = "Europe/Rome"
        sched.rearm(cfg)
        assert sched.prune_job is not None
        assert str(sched.prune_job.trigger.timezone) == "Europe/Rome"
    finally:
        sched.shutdown()


def test_fire_prune_invokes_callback():
    calls: list[int] = []
    sched = Scheduler(lambda _trigger: None, run_prune=lambda: calls.append(1))
    sched._fire_prune()
    assert calls == [1]


def test_fire_prune_swallows_errors():
    def boom():
        raise RuntimeError("db locked")

    sched = Scheduler(lambda _trigger: None, run_prune=boom)
    sched._fire_prune()  # must not propagate


# --- scheduled verify job -----------------------------------------------------


def _verify_config(enabled: bool = True, schedule: str = "0 3 1 * *") -> Config:
    cfg = _config()
    cfg.maintenance.verify.enabled = enabled
    cfg.maintenance.verify.schedule = schedule
    return cfg


def test_arms_verify_job_when_enabled():
    sched = Scheduler(lambda _t: None, run_verify=lambda _t: None)
    sched.rearm(_verify_config())
    assert sched.verify_job is not None and sched.verify_job.id == VERIFY_JOB_ID


def test_no_verify_job_when_disabled():
    sched = Scheduler(lambda _t: None, run_verify=lambda _t: None)
    sched.rearm(_verify_config(enabled=False))
    assert sched.verify_job is None


def test_no_verify_job_without_callback():
    sched = Scheduler(lambda _t: None)  # no run_verify
    sched.rearm(_verify_config())
    assert sched.verify_job is None


def test_rearm_to_disabled_clears_verify_job():
    sched = Scheduler(lambda _t: None, run_verify=lambda _t: None)
    sched.rearm(_verify_config())
    sched.rearm(_verify_config(enabled=False))
    assert sched.verify_job is None


def test_invalid_backup_schedule_does_not_crash_rearm():
    # A hand-edited/invalid backup schedule must not raise out of rearm (BE-B1) — otherwise
    # a bad string on disk bricks every startup. It's skipped, leaving nothing armed.
    sched = Scheduler(lambda _trigger: None)
    sched.rearm(_config(schedule="0 4 * *"))  # 4 fields, unparseable
    assert sched.backup_job is None


def test_legacy_verify_schedule_does_not_crash_or_arm():
    # The old config used schedule "monthly" (not cron). Arming must not raise, just skip.
    sched = Scheduler(lambda _t: None, run_verify=lambda _t: None)
    sched.rearm(_verify_config(schedule="monthly"))
    assert sched.verify_job is None


def test_fire_verify_passes_scheduled_trigger():
    from app.db.models import RunTrigger

    seen: list[RunTrigger] = []
    sched = Scheduler(lambda _t: None, run_verify=lambda trigger: seen.append(trigger))
    sched._fire_verify()
    assert seen == [RunTrigger.SCHEDULED]


def test_fire_verify_swallows_errors():
    def boom(_trigger):
        raise RuntimeError("already running")

    sched = Scheduler(lambda _t: None, run_verify=boom)
    sched._fire_verify()  # must not propagate


def test_fire_backup_logs_already_running_as_info(caplog):
    import logging

    from app.jobs import AlreadyRunningError

    def already_running(_trigger):
        raise AlreadyRunningError("in progress")

    sched = Scheduler(already_running)
    with caplog.at_level(logging.INFO, logger="joulenap.scheduler"):
        sched._fire_backup()  # must not raise

    assert any(
        r.levelno == logging.INFO and "already in progress" in r.getMessage()
        for r in caplog.records
    )
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


# --- day-of-week mapping (the cron-vs-APScheduler numbering bug) --------------


def test_translate_dow_maps_cron_numbers_to_apscheduler_names():
    # cron 1..6 = Mon..Sat; "Sundays off" must not include sun.
    assert _translate_dow("1,2,3,4,5,6") == "mon,tue,wed,thu,fri,sat"
    assert _translate_dow("0") == "sun"
    assert _translate_dow("7") == "sun"
    assert _translate_dow("*") == "*"


def test_build_trigger_excludes_sunday_when_off():
    # "Sundays off" cron from the UI. Verify the next fire after a Saturday is Monday,
    # i.e. Sunday is skipped (the bug let it fire on Sunday).
    trigger = _build_trigger("0 4 * * 1,2,3,4,5,6", UTC)
    sat = datetime(2026, 6, 27, 5, 0, tzinfo=UTC)  # Saturday 05:00, after 04:00 fire
    nxt = trigger.get_next_fire_time(None, sat)
    assert nxt.weekday() == 0  # Monday (not Sunday=6)


# --- timezone (the container-defaults-to-UTC footgun) -------------------------


def test_resolve_timezone_explicit_name():
    assert str(resolve_timezone("Europe/Rome")) == "Europe/Rome"


def test_resolve_timezone_utc_string_needs_no_tzdata():
    # "UTC" short-circuits to a fixed-offset tz so it resolves even without the IANA db.
    assert resolve_timezone("UTC") is UTC


def test_resolve_timezone_name_takes_precedence_over_env(monkeypatch):
    monkeypatch.setenv("TZ", "America/New_York")
    assert str(resolve_timezone("Europe/Rome")) == "Europe/Rome"


def test_resolve_timezone_falls_back_to_tz_env(monkeypatch):
    monkeypatch.setenv("TZ", "America/New_York")
    assert str(resolve_timezone("")) == "America/New_York"
    assert str(resolve_timezone(None)) == "America/New_York"


def test_resolve_timezone_defaults_to_utc(monkeypatch):
    monkeypatch.delenv("TZ", raising=False)
    assert resolve_timezone("") is UTC


def test_resolve_timezone_invalid_falls_back_to_utc(monkeypatch):
    monkeypatch.delenv("TZ", raising=False)
    assert resolve_timezone("Not/AZone") is UTC


def test_backup_trigger_carries_configured_timezone():
    sched = Scheduler(lambda _t: None)
    cfg = _config(schedule="0 2 * * *")
    cfg.app.timezone = "Europe/Rome"
    sched.rearm(cfg)
    assert str(sched.backup_job.trigger.timezone) == "Europe/Rome"


def test_rearm_applies_changed_app_timezone():
    # The scheduler starts in UTC; saving a new app.timezone calls rearm(), which must
    # re-arm the backup job in the new zone rather than keep UTC until a restart.
    sched = Scheduler(lambda _t: None, timezone="UTC")
    cfg = _config(schedule="0 22 * * *")
    cfg.app.timezone = "Europe/Rome"
    sched.rearm(cfg)
    assert str(sched.backup_job.trigger.timezone) == "Europe/Rome"


def test_schedule_fires_at_configured_local_time():
    # The whole point: "0 2" in Europe/Rome must mean 02:00 *Rome local* (00:00 UTC in
    # summer, UTC+2) — not 02:00 UTC as a bare container scheduler would do.
    sched = Scheduler(lambda _t: None)
    cfg = _config(schedule="0 2 * * *")
    cfg.app.timezone = "Europe/Rome"
    sched.rearm(cfg)
    ref = datetime(2026, 7, 1, 12, 0, tzinfo=ZoneInfo("Europe/Rome"))
    nxt = sched.backup_job.trigger.get_next_fire_time(None, ref)
    assert nxt.hour == 2  # 02:00 Rome local
    assert nxt.astimezone(UTC).hour == 0  # == 00:00 UTC


def test_missed_backup_since_detects_a_fire_during_downtime():
    # Last cycle served the 8th 04:00; we're back up on the 11th at 10:00 having been down
    # over the 9th/10th/11th 04:00 slots -> the first missed fire (9th 04:00) is reported.
    sched = Scheduler(lambda _t: None, timezone="UTC")
    sched.rearm(_config(schedule="0 4 * * *"))
    anchor = datetime(2026, 7, 8, 4, 0, 0, tzinfo=UTC)
    now = datetime(2026, 7, 11, 10, 0, 0, tzinfo=UTC)
    missed = sched.missed_backup_since(anchor, now)
    assert missed == datetime(2026, 7, 9, 4, 0, 0, tzinfo=UTC)


def test_missed_backup_since_none_when_no_slot_elapsed():
    # Restarted seconds after a run completed: the served slot is not re-reported and the
    # next slot is still in the future.
    sched = Scheduler(lambda _t: None, timezone="UTC")
    sched.rearm(_config(schedule="0 4 * * *"))
    anchor = datetime(2026, 7, 11, 4, 0, 5, tzinfo=UTC)
    now = datetime(2026, 7, 11, 4, 0, 20, tzinfo=UTC)
    assert sched.missed_backup_since(anchor, now) is None


def test_missed_backup_since_none_when_no_job_armed():
    sched = Scheduler(lambda _t: None, timezone="UTC")
    sched.rearm(_config(enabled=False))
    anchor = datetime(2026, 7, 8, 4, 0, 0, tzinfo=UTC)
    now = datetime(2026, 7, 11, 10, 0, 0, tzinfo=UTC)
    assert sched.missed_backup_since(anchor, now) is None
