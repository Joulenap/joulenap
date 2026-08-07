"""Bring an existing SQLite file up to the current models.

``Base.metadata.create_all`` creates *missing tables* and never touches one that already
exists, so a column added to a model appears on a fresh install and is simply absent on
every upgraded one — every user has had a live ``data/joulenap.db`` since 0.1.0. This module
closes that gap without pulling in Alembic: it diffs the models against what is actually on
disk and applies the small set of statements SQLite can do in place.

It is deliberately driven by the model metadata rather than a hand-maintained list of DDL,
so adding a column to a model is all a later change has to do.

Two tables are handled differently. ``guest_backups`` and ``datastore_stats`` are caches
that the backup cycle re-upserts whenever it has the PBS awake, and their keys changed in
1.0 (a vmid is only unique within one PVE; two PBSs may share a datastore name). SQLite
cannot ALTER a primary key, so they are dropped and recreated empty instead of rebuilt.
The visible cost is that the dashboard shows no datastore usage and no per-guest last-backup
dates until the next cycle runs; it then heals itself with no user action. Run history in
``runs`` is never dropped.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, text

from .base import Base

log = logging.getLogger(__name__)

# Re-derivable caches whose primary key changed in 1.0. SQLite can't ALTER a primary key,
# and there is nothing here worth a table rebuild.
_CACHE_TABLES = frozenset({"guest_backups", "datastore_stats"})


def upgrade_schema(engine: Engine) -> list[str]:
    """Apply the schema changes ``create_all`` can't. Returns the statements executed.

    Idempotent: a database already matching the models yields an empty list, and so does a
    fresh one (no tables exist yet — ``create_all`` builds them right after). Must be called
    with the models imported, and *before* ``create_all``, so a dropped cache table is
    recreated in the same startup.
    """
    applied: list[str] = []
    with engine.begin() as conn:
        on_disk = {
            row[0]
            for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }
        for table in Base.metadata.sorted_tables:
            if table.name not in on_disk:
                continue  # create_all will build it from scratch
            have = {row[1] for row in conn.execute(text(f"PRAGMA table_info('{table.name}')"))}
            if have == {c.name for c in table.columns}:
                continue
            if table.name in _CACHE_TABLES:
                applied.append(f"DROP TABLE {table.name}")
                conn.execute(text(applied[-1]))
                log.info("db: rebuilding cache table %s for the 1.0 schema", table.name)
                continue
            for column in (c for c in table.columns if c.name not in have):
                if not column.nullable and column.server_default is None:
                    # SQLite refuses ADD COLUMN for a NOT NULL column with no default. Warn
                    # rather than raise: a schema we can't fully upgrade must not stop the
                    # app from booting.
                    log.warning(
                        "db: cannot add NOT NULL column %s.%s to an existing table; "
                        "leaving the schema as it is",
                        table.name,
                        column.name,
                    )
                    continue
                sql_type = column.type.compile(engine.dialect)
                applied.append(f"ALTER TABLE {table.name} ADD COLUMN {column.name} {sql_type}")
                conn.execute(text(applied[-1]))
    if applied:
        log.info("db: upgraded the schema (%s)", "; ".join(applied))
    return applied
