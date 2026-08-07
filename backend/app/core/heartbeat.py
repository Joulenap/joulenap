"""Proof that the process was alive, so "we were down" can be a fact rather than a guess.

The missed-run check (:mod:`.catchup`) exists to notice a scheduled fire that fell in a window
the container was stopped for. It used to infer that window from *"a fire was due and no run
happened"* — which is also true when the schedule was changed since, when the route was disabled
and re-enabled, and when the kill-switch was off. All three sent a notification asserting
"Joulenap was offline when it was due", which nothing in the app had any evidence for.

A file's mtime is already a timestamp, so liveness needs no table and no migration: touch it on a
timer while running, read it at startup, and the gap between the two is the only window in which a
run can honestly be called missed.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from .. import paths

log = logging.getLogger("joulenap.heartbeat")

# Frequency is the precision of the downtime window, nothing more: a restart can leave up to this
# much time looking like downtime, which at worst reports a real fire the app might have served.
INTERVAL_SECONDS = 60


def touch() -> None:
    """Record that the process is alive now. Never raises — a read-only data dir must not
    take the app down, it just leaves ``last_seen`` stale (and the check silent)."""
    try:
        paths.heartbeat_path().touch()
    except OSError as exc:
        log.warning("Could not write the heartbeat file (%s); missed-run detection is off", exc)


def last_seen() -> datetime | None:
    """When the process was last known to be running, or ``None`` if that is unknown.

    ``None`` on a first boot, and on any unreadable file. Callers must treat it as "no window
    to judge against" and report nothing: inventing downtime is what this module exists to stop.
    """
    try:
        return datetime.fromtimestamp(paths.heartbeat_path().stat().st_mtime, UTC)
    except OSError:
        return None


__all__ = ["INTERVAL_SECONDS", "last_seen", "touch"]
