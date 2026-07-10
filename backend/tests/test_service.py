"""JobService: single-run guard and run-id return value."""

from __future__ import annotations

import threading

import pytest
from fakes import make_deps
from sqlalchemy import select

from app.core.config_store import ConfigStore
from app.db import session_scope
from app.db.models import Run, RunKind, RunStatus
from app.jobs import AlreadyRunningError, JobService


def test_run_backup_records_a_run(temp_config, temp_db):
    deps, _pve, _pbs, _power = make_deps()
    service = JobService(ConfigStore.load_or_create(), deps=deps)

    run_id = service.run_backup()

    with session_scope() as session:
        run = session.get(Run, run_id)
        assert run.kind == RunKind.CYCLE
        assert run.status == RunStatus.SUCCESS
    assert service.is_running is False


def test_run_gc_records_gc_run(temp_config, temp_db):
    deps, _pve, pbs, power = make_deps()
    service = JobService(ConfigStore.load_or_create(), deps=deps)

    run_id = service.run_gc()

    with session_scope() as session:
        run = session.get(Run, run_id)
        assert run.kind == RunKind.GC
        assert run.status == RunStatus.SUCCESS
    assert pbs.gc_started is True
    assert power.powered_off is True  # GC is now a power-managed cycle


def test_overlapping_run_is_rejected(temp_config, temp_db):
    started = threading.Event()
    release = threading.Event()

    def blocking_wol(_config):
        started.set()
        assert release.wait(timeout=5)

    deps, _pve, _pbs, _power = make_deps(wol=blocking_wol)
    service = JobService(ConfigStore.load_or_create(), deps=deps)

    worker = threading.Thread(target=service.run_backup)
    worker.start()
    try:
        assert started.wait(timeout=5)
        assert service.is_running is True
        with pytest.raises(AlreadyRunningError):
            service.run_gc()
    finally:
        release.set()
        worker.join(timeout=5)

    assert service.is_running is False


def test_exclusive_blocks_while_a_run_holds_the_lock(temp_config, temp_db):
    # exclusive() (used by manual power-off) must not enter while a run holds the lock, so a
    # poweroff can't race a starting cycle (BE-B5).
    started = threading.Event()
    release = threading.Event()

    def blocking_wol(_config):
        started.set()
        assert release.wait(timeout=5)

    deps, _pve, _pbs, _power = make_deps(wol=blocking_wol)
    service = JobService(ConfigStore.load_or_create(), deps=deps)

    worker = threading.Thread(target=service.run_backup)
    worker.start()
    try:
        assert started.wait(timeout=5)
        with pytest.raises(AlreadyRunningError), service.exclusive():
            pass  # pragma: no cover - the guard raises before the body runs
    finally:
        release.set()
        worker.join(timeout=5)

    # Lock free again -> exclusive() yields, and releases on exit.
    with service.exclusive():
        assert service.is_running is True
    assert service.is_running is False


def test_submit_releases_lock_if_thread_fails_to_start(temp_config, temp_db, monkeypatch):
    # If Thread.start() raises (e.g. thread/memory exhaustion), the worker's finally never
    # runs, so _submit must release the lock and fail the run itself (BE-B6) — otherwise every
    # later run 409s forever and the run is stuck RUNNING.
    deps, _pve, _pbs, _power = make_deps()
    service = JobService(ConfigStore.load_or_create(), deps=deps)

    class _BadThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr("app.jobs.service.threading.Thread", _BadThread)

    with pytest.raises(RuntimeError):
        service.submit_backup()

    assert service.is_running is False  # lock released, not leaked
    with session_scope() as s:
        run = s.scalars(select(Run)).one()
        assert run.status == RunStatus.FAILURE
        assert run.finished_at is not None
