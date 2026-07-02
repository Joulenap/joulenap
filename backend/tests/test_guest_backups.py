"""Cached per-guest last-backup times: upsert + read helpers."""

from __future__ import annotations

from datetime import UTC, datetime

from app.db import session_scope
from app.db.guest_backups import get_last_backups, upsert_last_backups


def _utc(epoch: int) -> datetime:
    """How the cache reads a backup time back: UTC-aware (UtcDateTime re-attaches UTC)."""
    return datetime.fromtimestamp(epoch, tz=UTC)


def test_upsert_inserts_and_reads_back(temp_db):
    with session_scope() as s:
        written = upsert_last_backups(s, {100: 1_700_000_000, 101: 1_700_000_500})
    assert written == 2

    with session_scope() as s:
        cached = get_last_backups(s)
    assert cached[100] == _utc(1_700_000_000)
    assert cached[101] == _utc(1_700_000_500)


def test_upsert_advances_changed_time_only(temp_db):
    with session_scope() as s:
        upsert_last_backups(s, {100: 1_700_000_000})

    with session_scope() as s:
        # 100 unchanged, 101 new -> only one write.
        written = upsert_last_backups(s, {100: 1_700_000_000, 101: 1_700_000_500})
    assert written == 1

    with session_scope() as s:
        # 100 advances to a newer time -> one write.
        written = upsert_last_backups(s, {100: 1_700_009_999})
    assert written == 1
    with session_scope() as s:
        assert get_last_backups(s)[100] == _utc(1_700_009_999)


def test_get_last_backups_filters_by_vmid(temp_db):
    with session_scope() as s:
        upsert_last_backups(s, {100: 1_700_000_000, 101: 1_700_000_500, 102: 1_700_001_000})

    with session_scope() as s:
        cached = get_last_backups(s, [100, 102, 999])
    assert set(cached) == {100, 102}  # 999 absent, 101 not requested
