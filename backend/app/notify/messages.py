"""Server-side, localized notification text.

Notification bodies are built and translated **on the backend** before sending — they
never pass through the frontend i18n. This is a small dictionary keyed by the
``app.language`` config, with an English fallback, mirroring the per-language approach of
the UI locales but kept deliberately tiny (only the strings that ship in a notification).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from typing import TYPE_CHECKING

from ..config import Config
from ..db.models import Run, RunStatus, StepName, StepStatus

if TYPE_CHECKING:
    from ..connectors.pbs import DatastoreStatus


@dataclass
class GuestSummary:
    """What the vzdump task did per guest, for the notification's ``Guests`` line.

    Deliberately **not** persisted: ``runs`` has no migration path (``init_db`` only calls
    ``create_all``, which never adds a column to an existing table), so this rides along to
    the notifier as an argument exactly like :class:`DatastoreStatus` does.

    ``failed`` holds display names (the vmid when the guest's name is unknown). Only guests
    vzdump actually reported on can land here, so a guest the user excluded from the
    selection is never counted or named.
    """

    total: int = 0  # guests the run set out to back up
    ok: int = 0
    failed: list[str] = field(default_factory=list)


# event keys: success | failure | aborted | test
_MESSAGES: dict[str, dict[str, dict[str, str]]] = {
    "en": {
        "success": {"title": "✅ Joulenap — backup succeeded"},
        "failure": {"title": "❌ Joulenap — backup failed"},
        "aborted": {"title": "⚠️ Joulenap — backup aborted"},
        # Per-kind titles for the non-backup cycles (see _title_for). Full strings rather
        # than a "{noun} succeeded" template: Italian needs gender agreement (backup
        # riuscito / verifica riuscita), which a noun slot can't express.
        "gc": {
            "success": "✅ Joulenap — garbage collection succeeded",
            "failure": "❌ Joulenap — garbage collection failed",
            "aborted": "⚠️ Joulenap — garbage collection aborted",
        },
        "verify": {
            "success": "✅ Joulenap — verification succeeded",
            "failure": "❌ Joulenap — verification failed",
            "aborted": "⚠️ Joulenap — verification aborted",
        },
        "missed": {
            "title": "⚠️ Joulenap — missed scheduled backup",
            "intro": "A scheduled backup was skipped because Joulenap was offline when it "
            "was due.",
        },
        "interrupted": {
            "title": "⚠️ Joulenap — run interrupted by a restart",
            "intro": "Joulenap restarted while a run was in progress; it was marked failed.",
        },
        "test": {
            "title": "🔔 Joulenap — test notification",
            "body": "If you can read this, notifications are configured correctly.",
        },
        "_labels": {
            "trigger": "Trigger",
            "trigger_scheduled": "scheduled",
            "trigger_manual": "manual",
            "duration": "Duration",
            "guests": "Guests",
            "failed": "failed",
            "datastore": "Datastore",
            "used": "used",
            "free": "free",
            "error": "Error",
            "pbs_left_on": "⚠️ PBS left powered on — check it",
            "awake_for": "PBS awake for",
            "missed_run": "Missed run",
            "last_run": "Last backup run",
            "next_run": "Next scheduled run",
            "run_no": "Run",
            # Names for the duration breakdown. Only the phases that do the actual work: the
            # wake packet is instant, and wait/power-off are near-constant overhead that would
            # just crowd the line.
            "phase_backup": "backup",
            "phase_gc": "GC",
            "phase_verify": "verify",
        },
    },
    "it": {
        "success": {"title": "✅ Joulenap — backup riuscito"},
        "failure": {"title": "❌ Joulenap — backup fallito"},
        "aborted": {"title": "⚠️ Joulenap — backup interrotto"},
        "gc": {
            "success": "✅ Joulenap — garbage collection riuscita",
            "failure": "❌ Joulenap — garbage collection fallita",
            "aborted": "⚠️ Joulenap — garbage collection interrotta",
        },
        "verify": {
            "success": "✅ Joulenap — verifica riuscita",
            "failure": "❌ Joulenap — verifica fallita",
            "aborted": "⚠️ Joulenap — verifica interrotta",
        },
        "missed": {
            "title": "⚠️ Joulenap — backup pianificato mancato",
            "intro": "Un backup pianificato è stato saltato perché Joulenap era offline "
            "al momento previsto.",
        },
        "interrupted": {
            "title": "⚠️ Joulenap — esecuzione interrotta da un riavvio",
            "intro": "Joulenap si è riavviato mentre un'esecuzione era in corso; "
            "è stata contrassegnata come fallita.",
        },
        "test": {
            "title": "🔔 Joulenap — notifica di prova",
            "body": "Se leggi questo messaggio, le notifiche sono configurate correttamente.",
        },
        "_labels": {
            "trigger": "Avvio",
            "trigger_scheduled": "pianificato",
            "trigger_manual": "manuale",
            "duration": "Durata",
            "guests": "Guest",
            "failed": "falliti",
            "datastore": "Datastore",
            "used": "usato",
            "free": "liberi",
            "error": "Errore",
            "pbs_left_on": "⚠️ PBS lasciato acceso — controllalo",
            "awake_for": "PBS sveglio da",
            "missed_run": "Esecuzione mancata",
            "last_run": "Ultimo backup eseguito",
            "next_run": "Prossima esecuzione pianificata",
            "run_no": "Run",
            "phase_backup": "backup",
            "phase_gc": "GC",
            "phase_verify": "verifica",
        },
    },
}

_STATUS_EVENT = {
    RunStatus.SUCCESS: "success",
    RunStatus.FAILURE: "failure",
    RunStatus.ABORTED: "aborted",
}


def _pack(language: str) -> dict[str, dict[str, str]]:
    return _MESSAGES.get(language, _MESSAGES["en"])


def _title_for(pack: dict[str, dict[str, str]], kind: str, event: str) -> str:
    """Title for a finished run, worded for the kind of cycle it was.

    A GC or verify cycle reports its own outcome instead of borrowing the backup wording (a
    scheduled verify failure used to notify "backup failed"). Anything without its own block
    — a normal backup cycle, or a kind added later — falls back to the backup title, so a new
    ``RunKind`` degrades to today's behaviour instead of raising.
    """
    return pack.get(kind, {}).get(event) or pack[event]["title"]


def _format_duration(seconds: float) -> str:
    total = int(seconds)
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


#: Steps that get a slot in the duration breakdown, keyed to their ``_labels`` entry. Wake,
#: wait, precheck and power-off are left out on purpose — see the label block's comment.
_PHASE_LABEL = {
    StepName.BACKUP: "phase_backup",
    StepName.GC: "phase_gc",
    StepName.VERIFY: "phase_verify",
}


def _phase_breakdown(labels: dict[str, str], run: Run) -> str:
    """``backup 7m 10s · GC 1m 6s`` — where the run's time actually went, in step order.

    Skipped steps (GC turned off) and steps still running contribute nothing, so the
    parentheses never advertise work that didn't happen. A ``StepName`` added later simply
    doesn't appear rather than raising.
    """
    parts = []
    for step in run.steps:  # the relationship is ordered by started_at
        key = _PHASE_LABEL.get(step.name)
        if key is None or step.status == StepStatus.SKIPPED or not step.finished_at:
            continue
        seconds = (step.finished_at - step.started_at).total_seconds()
        parts.append(f"{labels[key]} {_format_duration(seconds)}")
    return " · ".join(parts)


def human_bytes(n: int) -> str:
    """Binary-unit size, e.g. ``4.6 TiB`` (PBS reports datastore sizes in bytes)."""
    size = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PiB"


def _pbs_left_on(run: Run) -> bool:
    """True if the cycle woke the PBS but never powered it back off — so the box is still
    burning energy and the user should check it.

    The rule: the WAIT step succeeded (the PBS actually came up) **and** no POWEROFF step
    succeeded. That single condition covers every "left on" case uniformly:

      * success but power-off failed / was skipped (PBS busy) — POWEROFF present, not SUCCESS;
      * failure after the PBS woke (vzdump/GC/verify errored) — no POWEROFF step at all;
      * abort after wake (preflight free-space, no guests selected) — no POWEROFF step.

    An abort *before* the box came up (wake/wait timeout) leaves the WAIT step non-SUCCESS, so
    the PBS is off and this correctly returns False — hence why it keys on WAIT, not on the
    run status."""
    woke = any(s.name == StepName.WAIT and s.status == StepStatus.SUCCESS for s in run.steps)
    powered_off = any(
        s.name == StepName.POWEROFF and s.status == StepStatus.SUCCESS for s in run.steps
    )
    return woke and not powered_off


def build_run_message(
    config: Config,
    run: Run,
    datastore: DatastoreStatus | None = None,
    guests: GuestSummary | None = None,
    next_at: datetime | None = None,
) -> tuple[str, str]:
    """``(title, body)`` describing a finished run, in the configured language.

    One field per line, in a fixed order; a field whose data is missing drops out entirely
    rather than rendering a placeholder. ``datastore`` (read while the PBS was still awake)
    adds the usage line, ``guests`` the per-guest tally of a backup cycle, ``next_at`` the
    schedule's following fire.
    """
    pack = _pack(config.app.language)
    labels = pack["_labels"]
    event = _STATUS_EVENT.get(run.status, "failure")  # RUNNING shouldn't reach here

    # The title already conveys success/failure/aborted, so we don't repeat the (untranslated)
    # status enum in the body.
    lines: list[str] = [
        f"{labels['trigger']}: {labels.get(f'trigger_{run.trigger}', run.trigger)}"
    ]
    if run.started_at and run.finished_at:
        duration = (run.finished_at - run.started_at).total_seconds()
        breakdown = _phase_breakdown(labels, run)
        line = f"{labels['duration']}: {_format_duration(duration)}"
        lines.append(f"{line} ({breakdown})" if breakdown else line)
    # No summary at all (a GC or verify cycle, or an abort before the guests were picked)
    # means there is nothing truthful to say about guests — better silent than "0".
    if guests is not None and guests.total:
        line = f"{labels['guests']}: {guests.ok}/{guests.total}"
        if guests.failed:
            line += f" ({labels['failed']}: {', '.join(guests.failed)})"
        lines.append(line)
    if datastore is not None:
        lines.append(
            f"{labels['datastore']}: {datastore.used_pct}% {labels['used']}, "
            f"{human_bytes(datastore.avail)} {labels['free']}"
        )
    if run.error:
        lines.append(f"{labels['error']}: {run.error}")

    if _pbs_left_on(run):
        lines.append(labels["pbs_left_on"])

    if next_at is not None:
        lines.append(f"{labels['next_run']}: {_format_dt(next_at, _timezone(config))}")
    if run.id is not None:
        lines.append(f"{labels['run_no']} #{run.id}")

    return _title_for(pack, run.kind, event), "\n".join(lines)


def _timezone(config: Config) -> tzinfo:
    """The zone every timestamp in a notification is rendered in.

    Imported here rather than at module scope because ``core.scheduler`` pulls in ``jobs``,
    which pulls in this package — a top-level import would be circular."""
    from ..core.scheduler import resolve_timezone

    return resolve_timezone(config.app.timezone)


def _format_dt(dt: datetime | None, tz: tzinfo) -> str:
    """A short absolute timestamp for notifications, e.g. ``2026-07-11 04:00 CEST``.

    Everything is converted into the configured zone first. Cron-derived times already
    arrive in it, but anything read back from the database is UTC (see ``UtcDateTime``) —
    without the conversion one line of the same message would silently be hours off from
    the ones around it."""
    if dt is None:
        return "—"
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z").rstrip()


def build_missed_backup_message(
    config: Config, missed_at: datetime, last_run_at: datetime | None, next_at: datetime | None
) -> tuple[str, str]:
    """``(title, body)`` for a scheduled backup that didn't run because the process was down
    over its window (BE-R1), in the configured language."""
    pack = _pack(config.app.language)
    labels = pack["_labels"]
    tz = _timezone(config)
    lines = [
        pack["missed"]["intro"],
        "",
        f"{labels['missed_run']}: {_format_dt(missed_at, tz)}",
        f"{labels['last_run']}: {_format_dt(last_run_at, tz)}",
        f"{labels['next_run']}: {_format_dt(next_at, tz)}",
    ]
    return pack["missed"]["title"], "\n".join(lines)


def build_interrupted_message(config: Config, run: Run) -> tuple[str, str]:
    """``(title, body)`` for a run that a restart interrupted (swept to FAILURE at startup,
    BE-R2), in the configured language.

    Adds the "PBS left powered on" warning when the box had actually woken before the crash
    (WAIT succeeded, no POWEROFF) — the whole point of the alert: a normally-off box that a
    crash left awake and burning power."""
    pack = _pack(config.app.language)
    labels = pack["_labels"]
    lines = [pack["interrupted"]["intro"]]
    if run.error:
        lines.append(f"{labels['error']}: {run.error}")
    if _pbs_left_on(run):
        lines.append(labels["pbs_left_on"])
        # This alert has no Duration line (the run's own span would span the whole downtime,
        # not the work), so the one interval worth reporting is how long the box has been
        # awake: from the moment it came up to the restart — and, since nothing powered it
        # off, counting still.
        awake_since = _awake_since(run)
        if awake_since and run.finished_at:
            awake = (run.finished_at - awake_since).total_seconds()
            lines.append(f"{labels['awake_for']}: {_format_duration(awake)}")
    if run.id is not None:
        lines.append(f"{labels['run_no']} #{run.id}")
    return pack["interrupted"]["title"], "\n".join(lines)


def _awake_since(run: Run) -> datetime | None:
    """When the PBS finished coming up — the WAIT step's finish, or None if it never did."""
    for step in run.steps:
        if step.name == StepName.WAIT and step.status == StepStatus.SUCCESS:
            return step.finished_at
    return None


def build_test_message(config: Config) -> tuple[str, str]:
    """``(title, body)`` for the manual 'send test notification' action."""
    pack = _pack(config.app.language)
    return pack["test"]["title"], pack["test"]["body"]
