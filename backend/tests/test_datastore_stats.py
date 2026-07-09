"""Cached PBS datastore usage: upsert + read helpers."""

from __future__ import annotations

from app.db import session_scope
from app.db.datastore_stats import get_datastore_stat, upsert_datastore_stat


def test_upsert_inserts_and_reads_back(temp_db):
    with session_scope() as s:
        written = upsert_datastore_stat(s, "backup", 8_000_000_000, 2_000_000_000)
    assert written is True

    with session_scope() as s:
        row = get_datastore_stat(s, "backup")
        assert row is not None
        assert row.total == 8_000_000_000
        assert row.used == 2_000_000_000
        assert row.used_pct == 25.0


def test_upsert_advances_only_on_change(temp_db):
    with session_scope() as s:
        upsert_datastore_stat(s, "backup", 8_000_000_000, 2_000_000_000)

    with session_scope() as s:
        # identical values -> no write
        assert upsert_datastore_stat(s, "backup", 8_000_000_000, 2_000_000_000) is False

    with session_scope() as s:
        # used changed -> one write
        assert upsert_datastore_stat(s, "backup", 8_000_000_000, 3_000_000_000) is True

    with session_scope() as s:
        assert get_datastore_stat(s, "backup").used == 3_000_000_000


def test_get_returns_none_when_absent(temp_db):
    with session_scope() as s:
        assert get_datastore_stat(s, "nope") is None


def test_used_pct_zero_when_total_zero(temp_db):
    with session_scope() as s:
        upsert_datastore_stat(s, "empty", 0, 0)
    with session_scope() as s:
        assert get_datastore_stat(s, "empty").used_pct == 0.0
