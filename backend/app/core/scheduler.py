"""In-process scheduler (APScheduler) owned by the app.

Joulenap arms **one cron job per enabled route** and re-arms them all whenever config
changes. GC and verify have no triggers of their own: they are per-route options that run
while the PBS is already awake, or a route of their own kind. This scheduler never touches
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
from apscheduler.triggers.interval import IntervalTrigger

from ..config import Config, Route
from ..db.models import RunTrigger
from ..jobs import AlreadyRunningError
from . import heartbeat

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


#: Every route's job id is ``route:<id>``, so re-arming can drop the whole family without
#: touching the prune job (which is not config-driven).
ROUTE_JOB_PREFIX = "route:"
PRUNE_JOB_ID = "history-prune"
HEARTBEAT_JOB_ID = "heartbeat"
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

    Used by the route/config endpoints to reject a bad ``schedule.cron`` *before* it is
    persisted (BE-B1), so an invalid string can never reach disk and silently leave that
    route unarmed on every restart.
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


def route_cron(route: Route) -> str:
    """The 5-field crontab string a route fires on.

    ``schedule.cron`` wins verbatim when set — it is the escape hatch for a pattern
    ``time`` + ``days`` cannot express (day-of-month, step values), and a 0.9 config
    migrates its advanced cron into it rather than approximating.

    Otherwise: the day flags are Monday-first (``days[0]`` is Monday) while cron numbers
    weekdays Sunday-first, so index ``i`` becomes ``(i + 1) % 7``. Getting that backwards
    shifts every schedule by a day — which is also why the result still goes through
    ``_build_trigger`` and its ``_translate_dow`` like any other crontab string.
    """
    if route.schedule.cron:
        return route.schedule.cron
    hour, minute = route.schedule.time.split(":")
    if all(route.schedule.days):
        dow = "*"
    else:
        dow = ",".join(str((i + 1) % 7) for i, on in enumerate(route.schedule.days) if on)
    return f"{int(minute)} {int(hour)} * * {dow}"


class Scheduler:
    def __init__(
        self,
        run_route: Callable[[str, RunTrigger], object],
        run_prune: Callable[[], object] | None = None,
        timezone: str | tzinfo | None = None,
    ):
        self._timezone = timezone if isinstance(timezone, tzinfo) else resolve_timezone(timezone)
        self._scheduler = BackgroundScheduler(timezone=self._timezone)
        self._run_route = run_route
        self._run_prune = run_prune
        log.info("Scheduler timezone: %s", self._timezone)

    def start(self) -> None:
        if not self._scheduler.running:
            self._scheduler.start()

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    # --- arming --------------------------------------------------------------

    def rearm(self, config: Config) -> None:
        """(Re)build one cron job per enabled route, and re-arm the prune housekeeping job
        so its timezone stays in sync.

        Every route job is removed first, so a route that was disabled, deleted or had its
        schedule changed leaves nothing armed behind it.
        """
        # Re-resolve app.timezone here so a timezone changed at runtime (e.g. saved via
        # Settings, which calls rearm) actually takes effect. Without this the zone is fixed
        # at construction and a new schedule would stay in the old zone until restart.
        self._timezone = resolve_timezone(config.app.timezone)
        for job in self._scheduler.get_jobs():
            if job.id.startswith(ROUTE_JOB_PREFIX):
                self._scheduler.remove_job(job.id)
        if not config.app.scheduler_enabled:
            log.info("Scheduler kill-switch is off; no route armed")
        else:
            for route in config.routes:
                if route.enabled:
                    self._arm_route(route)
        # Prune isn't config-driven, but its trigger carries a timezone too, so re-arm it in
        # the (possibly new) zone — otherwise it keeps firing in the boot-time zone (BE-B7).
        self.arm_prune()

    def _arm_route(self, route: Route) -> None:
        schedule = route_cron(route)
        try:
            trigger = _build_trigger(schedule, self._timezone)
        except (ValueError, TypeError) as exc:
            # A hand-edited/legacy invalid schedule must not crash arming — otherwise one bad
            # string on disk bricks every startup (BE-B1). Skip this route, arm the rest.
            log.warning(
                "Invalid schedule %r for route '%s': %s; not armed", schedule, route.id, exc
            )
            return
        self._scheduler.add_job(
            self._fire_route,
            trigger,
            id=self.job_id(route.id),
            args=[route.id],
            replace_existing=True,
            coalesce=True,  # collapse missed fires (e.g. host asleep) into one
            max_instances=1,
        )
        log.info(
            "Armed route '%s': %s (next run %s)", route.id, schedule, self.next_run_time(route.id)
        )

    def arm_prune(self) -> None:
        """Arm (or, via ``replace_existing``, re-arm) the daily history-prune job in the
        current timezone. No-op when no prune callback was provided. Independent of the
        routes, so it runs even with the kill-switch off; ``rearm`` calls it to keep the
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

    def arm_heartbeat(self) -> None:
        """Arm the liveness stamp. Not config-driven and not re-armed by ``rearm``: it carries
        no timezone (a plain interval), and it must keep running with the kill-switch off —
        the app being up is exactly what it records."""
        self._scheduler.add_job(
            heartbeat.touch,
            IntervalTrigger(seconds=heartbeat.INTERVAL_SECONDS),
            id=HEARTBEAT_JOB_ID,
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        log.info("Armed liveness heartbeat (every %ds)", heartbeat.INTERVAL_SECONDS)

    # --- firing --------------------------------------------------------------

    def _fire_route(self, route_id: str) -> None:
        # A scheduled fire must never crash the scheduler thread; the run itself is recorded
        # to the DB by the service. An "already queued" skip is expected, not an error.
        try:
            self._run_route(route_id, RunTrigger.SCHEDULED)
        except AlreadyRunningError:
            log.info("Scheduled run of route '%s' skipped: it is already queued", route_id)
        except KeyError:
            log.warning("Scheduled route '%s' no longer exists", route_id)
        except Exception:  # noqa: BLE001
            log.exception("Scheduled run of route '%s' failed to start", route_id)

    def _fire_prune(self) -> None:
        try:
            self._run_prune()  # type: ignore[misc]  # guarded by arm_prune
        except Exception:  # noqa: BLE001
            log.exception("Scheduled history prune failed")

    # --- introspection -------------------------------------------------------

    @staticmethod
    def job_id(route_id: str) -> str:
        return f"{ROUTE_JOB_PREFIX}{route_id}"

    @property
    def prune_job(self):
        return self._scheduler.get_job(PRUNE_JOB_ID)

    def route_job(self, route_id: str):
        return self._scheduler.get_job(self.job_id(route_id))

    def armed_route_ids(self) -> list[str]:
        """Which routes currently have a job armed, in no particular order."""
        return [
            job.id[len(ROUTE_JOB_PREFIX) :]
            for job in self._scheduler.get_jobs()
            if job.id.startswith(ROUTE_JOB_PREFIX)
        ]

    def next_run_time(self, route_id: str) -> datetime | None:
        job = self.route_job(route_id)
        # A pending job (scheduler not yet started) has no next_run_time computed.
        return getattr(job, "next_run_time", None) if job else None

    def next_runs(self) -> list[tuple[str, datetime]]:
        """``(route_id, when)`` for every armed route with a computed next fire, soonest
        first — the homepage's "Upcoming runs" list and the status pill's "next" value."""
        out = [
            (route_id, when)
            for route_id in self.armed_route_ids()
            if (when := self.next_run_time(route_id)) is not None
        ]
        return sorted(out, key=lambda pair: pair[1])

    def missed_run_since(self, route_id: str, anchor: datetime, now: datetime) -> datetime | None:
        """The first scheduled fire of ``route_id`` in ``(anchor, now]``, or None if none was
        due.

        Used at startup to detect a run that was due while the process was down (the
        in-memory jobstore has no memory of fires missed across a restart; ``coalesce`` only
        helps while alive — BE-R1). ``anchor`` is that route's last finished run's start; we
        ask the *armed job's own trigger* (so the timezone + DOW translation match the real
        schedule) for the next fire strictly after it, and report it only if it is already in
        the past.

        Returns None when the route has no job armed (disabled, or the kill-switch is off).
        """
        job = self.route_job(route_id)
        if job is None:
            return None
        # +1s so the fire that the anchor run itself served isn't re-reported as missed
        # (cron fires land on whole seconds; anchor is that run's start ~at fire time).
        fire = job.trigger.get_next_fire_time(None, anchor + timedelta(seconds=1))
        return fire if fire is not None and fire <= now else None
