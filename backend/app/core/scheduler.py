"""In-process scheduler (APScheduler) owned by the app.

Joulenap arms exactly one cron job — the backup cycle, from ``backup.schedule`` — and
re-arms it whenever config changes. GC has no trigger of its own: it runs as a step of
the backup cycle when enabled (see jobs/backup_cycle.py). This scheduler never touches
systemd/cron on any host.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from ..config import Config
from ..db.models import RunTrigger
from ..jobs import AlreadyRunningError

log = logging.getLogger("joulenap.scheduler")


def resolve_timezone(name: str | None = None) -> tzinfo:
    """Resolve the timezone cron schedules are interpreted in.

    Priority: explicit ``name`` (``app.timezone``) > the ``TZ`` env var > UTC. This
    matters because a container's system zone is UTC unless ``TZ`` is set, so without
    this "backup at 02:00" would silently fire at 02:00 UTC. An unknown/typo'd name
    (or a slim image with no tz database) logs a warning and falls back rather than
    crashing startup — ``tzdata`` is a dependency so valid names resolve everywhere.
    """
    for candidate in (name, os.environ.get("TZ")):
        if not candidate:
            continue
        if candidate == "UTC":
            return UTC
        try:
            return ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            log.warning("Invalid timezone %r (%s); falling back", candidate, exc)
    return UTC


BACKUP_JOB_ID = "backup-cycle"
VERIFY_JOB_ID = "verify-cycle"
PRUNE_JOB_ID = "history-prune"
# History pruning is cheap and time-insensitive; run it once a day, off the hour, so it
# doesn't pile onto the typical early-morning backup window.
PRUNE_HOUR, PRUNE_MINUTE = 3, 30

# Standard cron numbers weekdays 0=Sun..6=Sat (7 also Sun); APScheduler's CronTrigger uses
# 0=Mon..6=Sun. CronTrigger.from_crontab passes the field through WITHOUT converting, which
# silently shifts every weekday by one. We translate to APScheduler's day names instead so a
# "Sundays off" schedule actually skips Sunday.
_CRON_DOW_NAME = {0: "sun", 1: "mon", 2: "tue", 3: "wed", 4: "thu", 5: "fri", 6: "sat", 7: "sun"}


def _build_trigger(schedule: str, tz: tzinfo) -> CronTrigger:
    """Build a CronTrigger from a standard 5-field crontab string, mapping the day-of-week
    field to APScheduler's weekday names so the numbering matches real cron.

    ``tz`` must be passed explicitly: a CronTrigger created without a timezone binds to the
    *system* local zone (UTC in a container), and add_job does NOT override it with the
    scheduler's timezone — so the trigger, not just the scheduler, has to carry the zone.
    """
    fields = schedule.split()
    if len(fields) != 5:
        # Not a plain 5-field crontab — let APScheduler parse it as-is.
        return CronTrigger.from_crontab(schedule, timezone=tz)
    minute, hour, day, month, dow = fields
    return CronTrigger(
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=_translate_dow(dow),
        timezone=tz,
    )


def validate_cron(schedule: str) -> None:
    """Raise ``ValueError``/``TypeError`` if ``schedule`` can't be built into a cron trigger.

    Used by ``PUT /api/config`` to reject a bad schedule *before* it is persisted (BE-B1),
    so an invalid string can never reach disk and brick the next startup's ``rearm``.
    """
    _build_trigger(schedule, UTC)


def _translate_dow(dow: str) -> str:
    """Map a cron day-of-week field (e.g. ``1,2,3,4,5,6``) to APScheduler names
    (``mon,tue,wed,thu,fri,sat``). ``*`` and any non-numeric token pass through."""
    if dow == "*":
        return "*"
    out = []
    for token in dow.split(","):
        out.append(_CRON_DOW_NAME.get(int(token), token) if token.isdigit() else token)
    return ",".join(out)


class Scheduler:
    def __init__(
        self,
        run_backup: Callable[[RunTrigger], object],
        run_prune: Callable[[], object] | None = None,
        run_verify: Callable[[RunTrigger], object] | None = None,
        timezone: str | tzinfo | None = None,
    ):
        self._timezone = timezone if isinstance(timezone, tzinfo) else resolve_timezone(timezone)
        self._scheduler = BackgroundScheduler(timezone=self._timezone)
        self._run_backup = run_backup
        self._run_prune = run_prune
        self._run_verify = run_verify
        log.info("Scheduler timezone: %s", self._timezone)

    def start(self) -> None:
        if not self._scheduler.running:
            self._scheduler.start()

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    def rearm(self, config: Config) -> None:
        """(Re)build the config-driven cron jobs (backup + scheduled verify) and re-arm the
        prune housekeeping job so its timezone stays in sync. Each config-driven job is
        removed first so a disabled/empty schedule leaves nothing armed."""
        # Re-resolve app.timezone here so a timezone changed at runtime (e.g. saved via
        # Settings, which calls rearm) actually takes effect. Without this the zone is fixed
        # at construction and a new schedule would stay in the old zone until restart.
        self._timezone = resolve_timezone(config.app.timezone)
        self._rearm_backup(config)
        self._rearm_verify(config)
        # Prune isn't config-driven, but its trigger carries a timezone too, so re-arm it in
        # the (possibly new) zone — otherwise it keeps firing in the boot-time zone (BE-B7).
        self.arm_prune()

    def _rearm_backup(self, config: Config) -> None:
        if self._scheduler.get_job(BACKUP_JOB_ID):
            self._scheduler.remove_job(BACKUP_JOB_ID)
        if not config.backup.enabled or not config.backup.schedule:
            log.info("Backup job disabled; no schedule armed")
            return
        try:
            trigger = _build_trigger(config.backup.schedule, self._timezone)
        except (ValueError, TypeError) as exc:
            # A hand-edited/legacy invalid schedule must not crash arming — otherwise a bad
            # string on disk bricks every startup (BE-B1). Skip arming, like _rearm_verify.
            log.warning(
                "Invalid backup schedule %r: %s; backup job not armed", config.backup.schedule, exc
            )
            return
        self._scheduler.add_job(
            self._fire_backup,
            trigger,
            id=BACKUP_JOB_ID,
            replace_existing=True,
            coalesce=True,  # collapse missed fires (e.g. host asleep) into one
            max_instances=1,
        )
        log.info("Armed backup job: %s (next run %s)", config.backup.schedule, self.next_run_time)

    def _rearm_verify(self, config: Config) -> None:
        if self._scheduler.get_job(VERIFY_JOB_ID):
            self._scheduler.remove_job(VERIFY_JOB_ID)
        if self._run_verify is None:
            return
        v = config.maintenance.verify
        if not v.enabled or not v.schedule:
            return
        try:
            trigger = _build_trigger(v.schedule, self._timezone)
        except (ValueError, TypeError) as exc:
            # A legacy/invalid schedule string (e.g. the old "monthly") must not crash arming.
            log.warning("Invalid verify schedule %r: %s; verify job not armed", v.schedule, exc)
            return
        self._scheduler.add_job(
            self._fire_verify,
            trigger,
            id=VERIFY_JOB_ID,
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        log.info("Armed scheduled verify job: %s", v.schedule)

    def _fire_backup(self) -> None:
        # A scheduled fire must never crash the scheduler thread; the run itself is recorded
        # to the DB by the service. An "already running" skip is expected, not an error.
        try:
            self._run_backup(RunTrigger.SCHEDULED)
        except AlreadyRunningError:
            log.info("Scheduled backup skipped: a run is already in progress")
        except Exception:  # noqa: BLE001
            log.exception("Scheduled backup run failed to start")

    def _fire_verify(self) -> None:
        try:
            self._run_verify(RunTrigger.SCHEDULED)  # type: ignore[misc]  # guarded by _rearm_verify
        except AlreadyRunningError:
            log.info("Scheduled verify skipped: a run is already in progress")
        except Exception:  # noqa: BLE001
            log.exception("Scheduled verify run failed to start")

    def arm_prune(self) -> None:
        """Arm (or, via ``replace_existing``, re-arm) the daily history-prune job in the
        current timezone. No-op when no prune callback was provided. Independent of backup
        config, so it runs even when backups are disabled; ``rearm`` calls it to keep the
        prune trigger's timezone in sync."""
        if self._run_prune is None:
            return
        self._scheduler.add_job(
            self._fire_prune,
            CronTrigger(hour=PRUNE_HOUR, minute=PRUNE_MINUTE, timezone=self._timezone),
            id=PRUNE_JOB_ID,
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        log.info("Armed history-prune job (daily at %02d:%02d)", PRUNE_HOUR, PRUNE_MINUTE)

    def _fire_prune(self) -> None:
        try:
            self._run_prune()  # type: ignore[misc]  # guarded by arm_prune
        except Exception:  # noqa: BLE001
            log.exception("Scheduled history prune failed")

    @property
    def backup_job(self):
        return self._scheduler.get_job(BACKUP_JOB_ID)

    @property
    def verify_job(self):
        return self._scheduler.get_job(VERIFY_JOB_ID)

    @property
    def prune_job(self):
        return self._scheduler.get_job(PRUNE_JOB_ID)

    @property
    def next_run_time(self) -> datetime | None:
        job = self.backup_job
        # A pending job (scheduler not yet started) has no next_run_time computed.
        return getattr(job, "next_run_time", None) if job else None

    def missed_backup_since(self, anchor: datetime, now: datetime) -> datetime | None:
        """The first scheduled backup fire in ``(anchor, now]``, or None if none was due.

        Used at startup to detect a backup that was due while the process was down (the
        in-memory jobstore has no memory of fires missed across a restart; ``coalesce`` only
        helps while alive — BE-R1). ``anchor`` is the last finished cycle's start; we ask the
        *armed job's own trigger* (so the timezone + DOW translation match the real schedule)
        for the next fire strictly after it, and report it only if it's already in the past.

        Returns None when no backup job is armed (backups disabled / empty schedule)."""
        job = self.backup_job
        if job is None:
            return None
        # +1s so the fire that the anchor run itself served isn't re-reported as missed
        # (cron fires land on whole seconds; anchor is that run's start ~at fire time).
        fire = job.trigger.get_next_fire_time(None, anchor + timedelta(seconds=1))
        return fire if fire is not None and fire <= now else None
