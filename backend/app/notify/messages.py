"""Server-side, localized notification text.

Notification bodies are built and translated **on the backend** before sending — they
never pass through the frontend i18n. This is a small dictionary keyed by the
``app.language`` config, with an English fallback, mirroring the per-language approach of
the UI locales but kept deliberately tiny (only the strings that ship in a notification).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..config import Config
from ..db.models import Run, RunStatus

if TYPE_CHECKING:
    from ..connectors.pbs import DatastoreStatus

# event keys: success | failure | aborted | test
_MESSAGES: dict[str, dict[str, dict[str, str]]] = {
    "en": {
        "success": {"title": "✅ Joulenap — backup succeeded"},
        "failure": {"title": "❌ Joulenap — backup failed"},
        "aborted": {"title": "⚠️ Joulenap — backup aborted"},
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
            "status": "Status",
        },
    },
    "it": {
        "success": {"title": "✅ Joulenap — backup riuscito"},
        "failure": {"title": "❌ Joulenap — backup fallito"},
        "aborted": {"title": "⚠️ Joulenap — backup interrotto"},
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
            "status": "Stato",
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


def build_run_message(
    config: Config, run: Run, datastore: DatastoreStatus | None = None
) -> tuple[str, str]:
    """``(title, body)`` describing a finished run, in the configured language.

    ``datastore`` (read while the PBS was still awake) adds a usage line on success.
    """
    pack = _pack(config.app.language)
    labels = pack["_labels"]
    event = _STATUS_EVENT.get(run.status, "failure")  # RUNNING shouldn't reach here

    lines = [f"{labels['status']}: {run.status}"]
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

    return pack[event]["title"], "\n".join(lines)


def build_test_message(config: Config) -> tuple[str, str]:
    """``(title, body)`` for the manual 'send test notification' action."""
    pack = _pack(config.app.language)
    return pack["test"]["title"], pack["test"]["body"]
