"""The idempotent schema upgrade that create_all can't do.

Every user has had a live SQLite file under data/ since 0.1.0, so these tests start from a
hand-written pre-overhaul schema rather than from the models — the whole point is what
happens to a database that already exists.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text

from app.db import session_scope
from app.db.base import Base, _make_engine, init_db
from app.db.models import GuestBackup, Run
from app.db.upgrade import upgrade_schema

# The 0.9 shape of the tables 1.0 changes, plus log_events as an untouched control.
LEGACY_DDL = [
    """CREATE TABLE runs (
        id INTEGER NOT NULL PRIMARY KEY,
        kind VARCHAR(16) NOT NULL,
        trigger VARCHAR(16) NOT NULL,
        status VARCHAR(16) NOT NULL,
        started_at DATETIME NOT NULL,
        finished_at DATETIME,
        guests_ok INTEGER,
        error TEXT
    )""",
    """CREATE TABLE log_events (
        id INTEGER NOT NULL PRIMARY KEY,
        run_id INTEGER,
        ts DATETIME NOT NULL,
        level VARCHAR(8) NOT NULL,
        message TEXT NOT NULL
    )""",
    """CREATE TABLE guest_backups (
        vmid INTEGER NOT NULL PRIMARY KEY,
        last_backup DATETIME NOT NULL
    )""",
    """CREATE TABLE datastore_stats (
        datastore VARCHAR(128) NOT NULL PRIMARY KEY,
        total INTEGER NOT NULL,
        used INTEGER NOT NULL,
        updated_at DATETIME NOT NULL
    )""",
]

LEGACY_ROWS = [
    "INSERT INTO runs (id, kind, trigger, status, started_at, guests_ok) "
    "VALUES (1, 'cycle', 'scheduled', 'success', '2026-07-01 04:00:00', 3)",
    "INSERT INTO log_events (id, run_id, ts, level, message) "
    "VALUES (1, 1, '2026-07-01 04:00:01', 'OK', 'backup finished')",
    "INSERT INTO guest_backups (vmid, last_backup) VALUES (100, '2026-07-01 04:05:00')",
    "INSERT INTO datastore_stats (datastore, total, used, updated_at) "
    "VALUES ('backup', 8000, 2000, '2026-07-01 04:06:00')",
]


def _legacy_db(tmp_path: Path, ddl: list[str] | None = None):
    """An engine on a fresh file carrying the pre-overhaul schema and a row in each table."""
    engine = _make_engine(tmp_path / "legacy.db")
    with engine.begin() as conn:
        for statement in ddl or LEGACY_DDL:
            conn.execute(text(statement))
        if ddl is None:
            for statement in LEGACY_ROWS:
                conn.execute(text(statement))
    return engine


def _columns(engine, table: str) -> set[str]:
    with engine.begin() as conn:
        return {row[1] for row in conn.execute(text(f"PRAGMA table_info('{table}')"))}


def _count(engine, table: str) -> int:
    with engine.begin() as conn:
        return conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()


# --- runs: ADD COLUMN, history preserved --------------------------------------


def test_runs_gains_the_route_columns_and_keeps_its_history(tmp_path: Path):
    engine = _legacy_db(tmp_path)
    applied = upgrade_schema(engine)

    assert {"route_id", "route_name"} <= _columns(engine, "runs")
    assert any("ADD COLUMN route_id" in s for s in applied)
    assert _count(engine, "runs") == 1

    with engine.begin() as conn:
        row = conn.execute(text("SELECT guests_ok, route_id, route_name FROM runs")).one()
    assert row == (3, None, None)  # pre-1.0 runs simply have no route


def test_untouched_tables_are_left_alone(tmp_path: Path):
    engine = _legacy_db(tmp_path)
    applied = upgrade_schema(engine)

    assert not any("log_events" in s for s in applied)
    assert _count(engine, "log_events") == 1


# --- the cache tables: dropped and recreated ----------------------------------


def test_the_cache_tables_are_rebuilt_with_their_new_keys(tmp_path: Path):
    # SQLite can't ALTER a primary key, and these two hold only values the next cycle
    # re-derives, so the upgrade drops them instead of rebuilding row by row.
    engine = _legacy_db(tmp_path)
    applied = upgrade_schema(engine)
    Base.metadata.create_all(engine)

    assert "DROP TABLE guest_backups" in applied
    assert "DROP TABLE datastore_stats" in applied
    assert {"pve_id", "vmid", "pbs_id", "last_backup"} == _columns(engine, "guest_backups")
    assert {"pbs_id", "datastore", "total", "used", "updated_at"} == _columns(
        engine, "datastore_stats"
    )
    # Empty until the next cycle has the PBS awake — the documented, self-healing cost.
    assert _count(engine, "guest_backups") == 0
    assert _count(engine, "datastore_stats") == 0


# --- idempotence --------------------------------------------------------------


def test_running_the_upgrade_twice_changes_nothing(tmp_path: Path):
    engine = _legacy_db(tmp_path)
    upgrade_schema(engine)
    Base.metadata.create_all(engine)

    assert upgrade_schema(engine) == []
    assert {"route_id", "route_name"} <= _columns(engine, "runs")
    assert _count(engine, "runs") == 1


def test_a_fresh_database_needs_no_upgrade(tmp_path: Path):
    engine = _make_engine(tmp_path / "fresh.db")
    assert upgrade_schema(engine) == []  # nothing on disk yet; create_all does it all
    Base.metadata.create_all(engine)
    assert upgrade_schema(engine) == []


# --- degradation rather than a failed boot ------------------------------------


def test_a_column_sqlite_cannot_add_is_skipped_not_raised(tmp_path: Path, caplog):
    # SQLite refuses ADD COLUMN for NOT NULL with no default. run_steps.name is exactly
    # that, so a schema missing it can't be repaired — but the app must still start.
    engine = _legacy_db(
        tmp_path,
        ddl=[
            """CREATE TABLE run_steps (
                id INTEGER NOT NULL PRIMARY KEY,
                run_id INTEGER NOT NULL,
                status VARCHAR(16) NOT NULL,
                started_at DATETIME NOT NULL,
                finished_at DATETIME,
                detail TEXT
            )"""
        ],
    )
    with caplog.at_level("WARNING"):
        upgrade_schema(engine)  # must not raise

    assert "name" not in _columns(engine, "run_steps")
    assert "cannot add NOT NULL column run_steps.name" in caplog.text


# --- end to end through init_db -----------------------------------------------


def test_init_db_upgrades_an_existing_file_in_place(tmp_path: Path, monkeypatch):
    from app.db import base

    db_file = tmp_path / "legacy.db"
    _legacy_db(tmp_path).dispose()  # writes the pre-overhaul file at that path

    monkeypatch.setattr(base, "_engine", None)
    monkeypatch.setattr(base, "_SessionLocal", None)
    init_db(db_file)
    try:
        with session_scope() as session:
            run = session.get(Run, 1)
            assert run is not None and run.guests_ok == 3 and run.route_id is None
            # The rebuilt cache accepts the new key.
            session.add(
                GuestBackup(
                    pve_id="pve-01",
                    vmid=100,
                    pbs_id="pbs-01",
                    last_backup=datetime.now(UTC),
                )
            )
    finally:
        monkeypatch.setattr(base, "_engine", None)
        monkeypatch.setattr(base, "_SessionLocal", None)
