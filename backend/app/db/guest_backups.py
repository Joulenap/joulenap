"""Read/write helpers for the cached per-guest last-backup times (see GuestBackup).

The backup cycle calls :func:`upsert_last_backups` while the PBS is awake; the guests API
calls :func:`get_last_backups` to decorate the guest list with cached dates.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import GuestBackup


def upsert_last_backups(session: Session, latest: dict[int, int]) -> int:
    """Upsert ``{vmid: backup-time epoch seconds}`` into the cache.

    Inserts new guests, advances existing rows whose time changed, and leaves unchanged
    rows alone. Returns the number of rows written. The caller owns the transaction.
    """
    written = 0
    for vmid, epoch in latest.items():
        # UTC-aware, matching how UtcDateTime reads every timestamp back — so the
        # change-detection compare below (row.last_backup != ts) is aware-vs-aware.
        ts = datetime.fromtimestamp(epoch, tz=UTC)
        row = session.get(GuestBackup, vmid)
        if row is None:
            session.add(GuestBackup(vmid=vmid, last_backup=ts))
            written += 1
        elif row.last_backup != ts:
            row.last_backup = ts
            written += 1
    return written


def get_last_backups(
    session: Session, vmids: Iterable[int] | None = None
) -> dict[int, datetime]:
    """Return ``{vmid: last_backup}`` from the cache, optionally limited to ``vmids``."""
    stmt = select(GuestBackup)
    if vmids is not None:
        ids = list(vmids)
        if not ids:  # empty filter = no rows; avoids SQLAlchemy's empty-IN warning
            return {}
        stmt = stmt.where(GuestBackup.vmid.in_(ids))
    return {row.vmid: row.last_backup for row in session.scalars(stmt)}
