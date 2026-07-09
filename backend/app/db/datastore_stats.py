"""Read/write helpers for the cached PBS datastore usage (see DatastoreStat).

The backup cycle and live status probes call upsert_datastore_stat while the PBS is awake;
/api/status and /api/dashboard read it back (via api/_probe.resolve_datastore) so disk
figures show even while the PBS sleeps. Mirrors db/guest_backups.py.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from .models import DatastoreStat


def upsert_datastore_stat(session: Session, datastore: str, total: int, used: int) -> bool:
    """Upsert cached usage for a datastore. Inserts a new row, advances an existing row whose
    total/used changed, leaves an unchanged row alone. Returns whether a row was written. The
    caller owns the transaction."""
    row = session.get(DatastoreStat, datastore)
    if row is None:
        session.add(DatastoreStat(datastore=datastore, total=total, used=used))
        return True
    if row.total != total or row.used != used:
        row.total = total
        row.used = used
        return True
    return False


def get_datastore_stat(session: Session, datastore: str) -> DatastoreStat | None:
    return session.get(DatastoreStat, datastore)
