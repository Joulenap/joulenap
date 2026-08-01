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


def upsert_last_backups(
    session: Session, pve_id: str, pbs_id: str, latest: dict[int, int]
) -> int:
    """Upsert ``{vmid: backup-time epoch seconds}`` for one PVE's guests on one PBS.

    Inserts new guests, advances existing rows whose time changed, and leaves unchanged
    rows alone. Returns the number of rows written. The caller owns the transaction.
    """
    written = 0
    for vmid, epoch in latest.items():
        # UTC-aware, matching how UtcDateTime reads every timestamp back — so the
        # change-detection compare below (row.last_backup != ts) is aware-vs-aware.
        ts = datetime.fromtimestamp(epoch, tz=UTC)
        row = session.get(GuestBackup, (pve_id, vmid, pbs_id))
        if row is None:
            session.add(
                GuestBackup(pve_id=pve_id, vmid=vmid, pbs_id=pbs_id, last_backup=ts)
            )
            written += 1
        elif row.last_backup != ts:
            row.last_backup = ts
            written += 1
    return written


def get_last_backups(
    session: Session, vmids: Iterable[int] | None = None
) -> dict[int, datetime]:
    """Return ``{vmid: last_backup}`` from the cache, optionally limited to ``vmids``.

    A guest can now be backed up by several routes onto different PBSs, so a vmid may have
    more than one row; the newest wins, which is what "when was this guest last backed up"
    means to the dashboard. TODO(M07): the guest panel wants the breakdown per (pve, pbs),
    not just the newest — that needs a richer return shape and callers that know their ids.
    """
    stmt = select(GuestBackup)
    if vmids is not None:
        ids = list(vmids)
        if not ids:  # empty filter = no rows; avoids SQLAlchemy's empty-IN warning
            return {}
        stmt = stmt.where(GuestBackup.vmid.in_(ids))
    out: dict[int, datetime] = {}
    for row in session.scalars(stmt):
        current = out.get(row.vmid)
        if current is None or row.last_backup > current:
            out[row.vmid] = row.last_backup
    return out
