"""Server-side, localized notification text.

Notification bodies are built and translated **on the backend** before sending — they
never pass through the frontend i18n. This is a small dictionary keyed by the
``app.language`` config, with an English fallback, mirroring the per-language approach of
the UI locales but kept deliberately tiny (only the strings that ship in a notification).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from ..config import Config
from ..db.models import Run, RunStatus, StepName, StepStatus

if TYPE_CHECKING:
    from ..connectors.pbs import DatastoreStatus

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
            "duration": "Duration",
            "guests": "Guests",
            "datastore": "Datastore",
            "used": "used",
            "free": "free",
            "error": "Error",
            "pbs_left_on": "⚠️ PBS left powered on — check it",
            "missed_run": "Missed run",
            "last_run": "Last backup run",
            "next_run": "Next scheduled run",
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
            "duration": "Durata",
            "guests": "Guest",
            "datastore": "Datastore",
            "used": "usato",
            "free": "liberi",
            "error": "Errore",
            "pbs_left_on": "⚠️ PBS lasciato acceso — controllalo",
            "missed_run": "Esecuzione mancata",
            "last_run": "Ultimo backup eseguito",
            "next_run": "Prossima esecuzione pianificata",
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
    config: Config, run: Run, datastore: DatastoreStatus | None = None
) -> tuple[str, str]:
    """``(title, body)`` describing a finished run, in the configured language.

    ``datastore`` (read while the PBS was still awake) adds a usage line on success.
    """
    pack = _pack(config.app.language)
    labels = pack["_labels"]
    event = _STATUS_EVENT.get(run.status, "failure")  # RUNNING shouldn't reach here

    # The title already conveys success/failure/aborted, so we don't repeat the (untranslated)
    # status enum in the body.
    lines: list[str] = []
    if run.started_at and run.finished_at:
        duration = (run.finished_at - run.started_at).total_seconds()
        lines.append(f"{labels['duration']}: {_format_duration(duration)}")
    if run.guests_ok is not None:
        lines.append(f"{labels['guests']}: {run.guests_ok}")
    if datastore is not None:
        lines.append(
            f"{labels['datastore']}: {datastore.used_pct}% {labels['used']}, "
            f"{human_bytes(datastore.avail)} {labels['free']}"
        )
    if run.error:
        lines.append(f"{labels['error']}: {run.error}")

    if _pbs_left_on(run):
        lines.append(labels["pbs_left_on"])

    return _title_for(pack, run.kind, event), "\n".join(lines)


def _format_dt(dt: datetime | None) -> str:
    """A short absolute timestamp for notifications, e.g. ``2026-07-11 04:00 CEST``.

    The datetimes passed here come straight from the schedule's cron trigger, so they are
    already in the user's configured timezone — no re-localisation needed."""
    if dt is None:
        return "—"
    return dt.strftime("%Y-%m-%d %H:%M %Z").rstrip()


def build_missed_backup_message(
    config: Config, missed_at: datetime, last_run_at: datetime | None, next_at: datetime | None
) -> tuple[str, str]:
    """``(title, body)`` for a scheduled backup that didn't run because the process was down
    over its window (BE-R1), in the configured language."""
    pack = _pack(config.app.language)
    labels = pack["_labels"]
    lines = [
        pack["missed"]["intro"],
        "",
        f"{labels['missed_run']}: {_format_dt(missed_at)}",
        f"{labels['last_run']}: {_format_dt(last_run_at)}",
        f"{labels['next_run']}: {_format_dt(next_at)}",
    ]
    return pack["missed"]["title"], "\n".join(lines)


def build_interrupted_message(config: Config, run: Run) -> tuple[str, str]:
    """``(title, body)`` for a run that a restart interrupted (swept to FAILURE at startup,
    BE-R2), in the configured language.

    Adds the "PBS left powered on" warning when the box had actually woken before the crash
    (WAIT succeeded, no POWEROFF) — the whole point of the alert: a normally-off box that a
    crash left awake and burning power."""
    pack = _pack(config.app.language)
    lines = [pack["interrupted"]["intro"]]
    if run.error:
        lines.append(f"{pack['_labels']['error']}: {run.error}")
    if _pbs_left_on(run):
        lines.append(pack["_labels"]["pbs_left_on"])
    return pack["interrupted"]["title"], "\n".join(lines)


def build_test_message(config: Config) -> tuple[str, str]:
    """``(title, body)`` for the manual 'send test notification' action."""
    pack = _pack(config.app.language)
    return pack["test"]["title"], pack["test"]["body"]
