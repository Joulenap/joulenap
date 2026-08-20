"""Scheduler arming / re-arming: one cron job per enabled route, plus the prune job."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from conftest import with_devices

from app.config import Config
from app.core.scheduler import (
    HEARTBEAT_JOB_ID,
    PRUNE_HOUR,
    PRUNE_JOB_ID,
    PRUNE_MINUTE,
    Scheduler,
    _build_trigger,
    _translate_dow,
    resolve_timezone,
    route_cron,
)


def _config(**app_overrides) -> Config:
    """The standard fixture: 3 routes over 2 PBS (nightly, lab, offsite)."""
    cfg = with_devices(Config())
    for key, value in app_overrides.items():
        setattr(cfg.app, key, value)
    return cfg


def _route(cfg: Config, route_id: str):
    return next(r for r in cfg.routes if r.id == route_id)


# --- one job per route --------------------------------------------------------


def test_arms_one_job_per_enabled_route():
    sched = Scheduler(lambda _id, _trigger: None)
    sched.rearm(_config())

    assert sorted(sched.armed_route_ids()) == ["lab", "nightly", "offsite"]
    assert isinstance(sched.route_job("nightly").trigger, CronTrigger)


def test_a_disabled_route_is_not_armed():
    cfg = _config()
    _route(cfg, "lab").enabled = False
    sched = Scheduler(lambda _id, _trigger: None)
    sched.rearm(cfg)

    assert sorted(sched.armed_route_ids()) == ["nightly", "offsite"]


def test_the_kill_switch_disarms_every_route():
    sched = Scheduler(lambda _id, _trigger: None)
    sched.rearm(_config())
    sched.rearm(_config(scheduler_enabled=False))

    assert sched.armed_route_ids() == []


def test_rearm_replaces_rather_than_accumulates():
    sched = Scheduler(lambda _id, _trigger: None)
    cfg = _config()
    sched.rearm(cfg)
    _route(cfg, "nightly").schedule.time = "23:30"
    sched.rearm(cfg)

    assert len(sched._scheduler.get_jobs()) == 3


def test_a_deleted_route_leaves_no_job_behind():
    sched = Scheduler(lambda _id, _trigger: None)
    cfg = _config()
    sched.rearm(cfg)
    cfg.routes = [r for r in cfg.routes if r.id != "offsite"]
    sched.rearm(cfg)

    assert sched.route_job("offsite") is None


def test_next_runs_are_sorted_soonest_first():
    sched = Scheduler(lambda _id, _trigger: None)
    sched.start()
    try:
        sched.rearm(_config())
        runs = sched.next_runs()
        assert [route_id for route_id, _ in runs] == sorted(
            [r for r, _ in runs], key=lambda r: dict(runs)[r]
        )
        assert [when for _, when in runs] == sorted(when for _, when in runs)
    finally:
        sched.shutdown()


def test_an_invalid_cron_skips_only_that_route():
    # A hand-edited/invalid cron must not raise out of rearm (BE-B1) — otherwise one bad
    # string on disk bricks every startup. It's skipped; the other routes still arm.
    cfg = _config()
    _route(cfg, "lab").schedule.cron = "0 4 * *"  # 4 fields, unparseable
    sched = Scheduler(lambda _id, _trigger: None)
    sched.rearm(cfg)

    assert sched.route_job("lab") is None
    assert sorted(sched.armed_route_ids()) == ["nightly", "offsite"]


# --- firing -------------------------------------------------------------------


def test_fire_route_passes_the_id_and_the_scheduled_trigger():
    from app.db.models import RunTrigger

    seen: list[tuple] = []
    sched = Scheduler(lambda route_id, trigger: seen.append((route_id, trigger)))
    sched._fire_route("nightly")
    assert seen == [("nightly", RunTrigger.SCHEDULED)]


def test_fire_route_swallows_errors():
    def boom(_id, _trigger):
        raise RuntimeError("boom")

    # Must not propagate — a raising fire would kill the scheduler thread.
    Scheduler(boom)._fire_route("nightly")


def test_fire_route_logs_already_queued_as_info(caplog):
    import logging

    from app.jobs import AlreadyRunningError

    def already_queued(_id, _trigger):
        raise AlreadyRunningError("in progress")

    sched = Scheduler(already_queued)
    with caplog.at_level(logging.INFO, logger="joulenap.scheduler"):
        sched._fire_route("nightly")  # must not raise

    assert any(
        r.levelno == logging.INFO and "already queued" in r.getMessage() for r in caplog.records
    )
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


def test_fire_route_survives_a_route_deleted_mid_flight(caplog):
    import logging

    def gone(_id, _trigger):
        raise KeyError("nightly")

    sched = Scheduler(gone)
    with caplog.at_level(logging.WARNING, logger="joulenap.scheduler"):
        sched._fire_route("nightly")
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


# --- time + days -> cron ------------------------------------------------------


def test_route_cron_every_day_is_a_star():
    cfg = _config()
    assert route_cron(_route(cfg, "nightly")) == "0 2 * * *"


def test_route_cron_maps_monday_first_days_onto_cron_numbering():
    # days[] is Mon..Sun; cron is Sun=0..Sat=6. The "lab" fixture is Saturdays only, which
    # is days[5] -> cron 6. Off by one here shifts every schedule by a day.
    cfg = _config()
    assert route_cron(_route(cfg, "lab")) == "0 4 * * 6"


def test_route_cron_sunday_becomes_zero():
    cfg = _config()
    route = _route(cfg, "nightly")
    route.schedule.days = [False] * 6 + [True]
    assert route_cron(route) == "0 2 * * 0"


def test_route_cron_prefers_the_raw_escape_hatch():
    cfg = _config()
    route = _route(cfg, "nightly")
    route.schedule.cron = "*/15 * 1 * *"
    assert route_cron(route) == "*/15 * 1 * *"


def test_a_sunday_only_route_actually_fires_on_sunday():
    # The cron-vs-APScheduler numbering bug: cron 0 is Sunday, APScheduler's 0 is Monday.
    cfg = _config()
    route = _route(cfg, "nightly")
    route.schedule.days = [False] * 6 + [True]
    sched = Scheduler(lambda _id, _trigger: None, timezone="UTC")
    sched.rearm(cfg)

    ref = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)  # a Wednesday
    nxt = sched.route_job("nightly").trigger.get_next_fire_time(None, ref)
    assert nxt.weekday() == 6  # Sunday


# --- history-prune housekeeping job -------------------------------------------


def test_arm_prune_adds_daily_job():
    sched = Scheduler(lambda _id, _t: None, run_prune=lambda: None)
    sched.arm_prune()
    assert sched.prune_job is not None and sched.prune_job.id == PRUNE_JOB_ID


def test_arm_prune_noop_without_callback():
    sched = Scheduler(lambda _id, _t: None)  # no prune callback
    sched.arm_prune()
    assert sched.prune_job is None


def test_prune_survives_the_kill_switch():
    # Re-arming (or disabling everything) must not drop the prune housekeeping job.
    sched = Scheduler(lambda _id, _t: None, run_prune=lambda: None)
    sched.arm_prune()
    sched.rearm(_config())
    sched.rearm(_config(scheduler_enabled=False))
    assert sched.prune_job is not None
    assert {j.id for j in sched._scheduler.get_jobs()} == {PRUNE_JOB_ID}


def test_rearm_updates_prune_job_timezone():
    # A runtime timezone change (via rearm) must move the prune job into the new zone, not
    # leave it firing in the boot-time zone (BE-B7). Started so replace_existing dedups the
    # prune job (as it does in production, where rearm always runs on a started scheduler).
    sched = Scheduler(lambda _id, _t: None, run_prune=lambda: None, timezone="UTC")
    sched.start()
    try:
        sched.arm_prune()
        assert str(sched.prune_job.trigger.timezone) == "UTC"

        sched.rearm(_config(timezone="Europe/Rome"))
        assert str(sched.prune_job.trigger.timezone) == "Europe/Rome"
    finally:
        sched.shutdown()


def test_fire_prune_invokes_callback():
    calls: list[int] = []
    sched = Scheduler(lambda _id, _t: None, run_prune=lambda: calls.append(1))
    sched._fire_prune()
    assert calls == [1]


def test_fire_prune_swallows_errors():
    def boom():
        raise RuntimeError("db locked")

    Scheduler(lambda _id, _t: None, run_prune=boom)._fire_prune()  # must not propagate


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


def test_route_trigger_carries_configured_timezone():
    sched = Scheduler(lambda _id, _t: None)
    sched.rearm(_config(timezone="Europe/Rome"))
    assert str(sched.route_job("nightly").trigger.timezone) == "Europe/Rome"


def test_rearm_applies_changed_app_timezone():
    # The scheduler starts in UTC; saving a new app.timezone calls rearm(), which must
    # re-arm every route in the new zone rather than keep UTC until a restart.
    sched = Scheduler(lambda _id, _t: None, timezone="UTC")
    sched.rearm(_config(timezone="Europe/Rome"))
    assert str(sched.route_job("nightly").trigger.timezone) == "Europe/Rome"


def test_schedule_fires_at_configured_local_time():
    # The whole point: 02:00 in Europe/Rome must mean 02:00 *Rome local* (00:00 UTC in
    # summer, UTC+2) — not 02:00 UTC as a bare container scheduler would do.
    sched = Scheduler(lambda _id, _t: None)
    sched.rearm(_config(timezone="Europe/Rome"))
    ref = datetime(2026, 7, 1, 12, 0, tzinfo=ZoneInfo("Europe/Rome"))
    nxt = sched.route_job("nightly").trigger.get_next_fire_time(None, ref)
    assert nxt.hour == 2  # 02:00 Rome local
    assert nxt.astimezone(UTC).hour == 0  # == 00:00 UTC


# --- missed fires across a restart (BE-R1) ------------------------------------


def test_missed_run_since_detects_a_fire_during_downtime():
    # The nightly route last ran on the 8th at 02:00; we're back up on the 11th at 10:00
    # having been down over three slots -> the first missed fire (9th 02:00) is reported.
    sched = Scheduler(lambda _id, _t: None, timezone="UTC")
    sched.rearm(_config())
    anchor = datetime(2026, 7, 8, 2, 0, 0, tzinfo=UTC)
    now = datetime(2026, 7, 11, 10, 0, 0, tzinfo=UTC)
    assert sched.missed_run_since("nightly", anchor, now) == datetime(
        2026, 7, 9, 2, 0, 0, tzinfo=UTC
    )


def test_missed_run_since_none_when_no_slot_elapsed():
    # Restarted seconds after a run completed: the served slot is not re-reported and the
    # next slot is still in the future.
    sched = Scheduler(lambda _id, _t: None, timezone="UTC")
    sched.rearm(_config())
    anchor = datetime(2026, 7, 11, 2, 0, 5, tzinfo=UTC)
    now = datetime(2026, 7, 11, 2, 0, 20, tzinfo=UTC)
    assert sched.missed_run_since("nightly", anchor, now) is None


def test_missed_run_since_none_when_the_route_is_not_armed():
    sched = Scheduler(lambda _id, _t: None, timezone="UTC")
    sched.rearm(_config(scheduler_enabled=False))
    anchor = datetime(2026, 7, 8, 2, 0, 0, tzinfo=UTC)
    now = datetime(2026, 7, 11, 10, 0, 0, tzinfo=UTC)
    assert sched.missed_run_since("nightly", anchor, now) is None


# --- liveness heartbeat (G3-6) ------------------------------------------------


def test_arm_heartbeat_adds_an_interval_job():
    sched = Scheduler(lambda _id, _t: None)
    sched.arm_heartbeat()
    job = sched._scheduler.get_job(HEARTBEAT_JOB_ID)
    assert job is not None


def test_the_heartbeat_survives_the_kill_switch():
    # It records that the *app* is up, which is exactly as true with every route disabled —
    # and a heartbeat that stopped there would make the next restart invent downtime.
    sched = Scheduler(lambda _id, _t: None)
    sched.arm_heartbeat()
    sched.rearm(_config(scheduler_enabled=False))
    assert sched._scheduler.get_job(HEARTBEAT_JOB_ID) is not None


def test_the_prune_job_fires_at_the_documented_time():
    """03:30 daily, and the only thing that said so was a log line.

    Moving it would silently change when history is deleted, in the middle of the window
    a nightly backup is most likely to still be running.
    """
    sched = Scheduler(lambda _id, _t: None, run_prune=lambda: None, timezone="UTC")
    sched.arm_prune()

    trigger = str(sched.prune_job.trigger)
    assert f"hour='{PRUNE_HOUR}'" in trigger
    assert f"minute='{PRUNE_MINUTE}'" in trigger
    assert (PRUNE_HOUR, PRUNE_MINUTE) == (3, 30)
