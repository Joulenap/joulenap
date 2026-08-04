"""SQLAlchemy engine, session factory and schema bootstrap.

A small SQLite database under ``data/`` holds run history and the activity log. The
app is single-instance and low-traffic, so a synchronous engine with short-lived
sessions is plenty; jobs (milestone 3) open their own ``session_scope()``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .. import paths

_engine = None
_SessionLocal: sessionmaker[Session] | None = None


class Base(DeclarativeBase):
    pass


def _make_engine(db_file: Path):
    # check_same_thread=False: FastAPI may touch a session from a threadpool worker.
    engine = create_engine(
        f"sqlite:///{db_file.as_posix()}",
        future=True,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _record):
        # A running backup cycle commits after every step/log line while the dashboard
        # polls (and writes datastore usage on GET), so concurrent access is the norm:
        #  - WAL lets those readers keep going while the cycle writes, instead of
        #    blocking each other (the default rollback journal serialises them).
        #  - busy_timeout makes a genuinely contended write WAIT rather than fail
        #    instantly with "database is locked".
        #  - foreign_keys makes the ondelete=CASCADE relationships actually enforce
        #    (SQLite leaves FK enforcement off by default).
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


def init_db(db_file: Path | None = None) -> None:
    """Create the engine, session factory and tables. Idempotent; call at startup."""
    global _engine, _SessionLocal
    target = db_file or paths.db_path()
    _engine = _make_engine(target)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    # Import models so they're registered on Base.metadata before create_all.
    from . import models  # noqa: F401
    from .upgrade import upgrade_schema

    # Order matters: upgrade_schema patches tables create_all would leave alone, and may
    # drop a cache table whose key changed — create_all then recreates it in the same call.
    upgrade_schema(_engine)
    Base.metadata.create_all(_engine)


def _ensure_ready() -> sessionmaker[Session]:
    """The session factory, or a clear error if the app never called :func:`init_db`.

    Deliberately does *not* initialise lazily. Doing so ran ``create_all`` against
    ``paths.db_path()`` — the real database — from whatever thread happened to ask first,
    and two of them at once collide (``create_all`` reflects once, then issues CREATEs, so
    the loser raises *table X already exists*). Failing loudly makes a caller that outlived
    its setup obvious instead of silently rebuilding a schema somewhere else.
    """
    if _SessionLocal is None:
        # ASCII on purpose: this can surface on a Windows console with a cp1252 codepage.
        raise RuntimeError("Database is not initialised - call init_db() first")
    return _SessionLocal


def make_session() -> Session:
    """A bare Session the caller owns (commits and closes itself).

    Used by long-running jobs that commit incrementally so an in-flight run stays
    visible to the API; for simple unit-of-work blocks prefer :func:`session_scope`.
    """
    return _ensure_ready()()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session for background jobs: commits on success, rolls back on error."""
    factory = _ensure_ready()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session (no implicit commit)."""
    factory = _ensure_ready()
    session = factory()
    try:
        yield session
    finally:
        session.close()
