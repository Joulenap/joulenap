"""JobService: single-run guard and run-id return value."""

from __future__ import annotations

import threading

import pytest
from fakes import make_deps

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
    deps, _pve, pbs, _power = make_deps()
    service = JobService(ConfigStore.load_or_create(), deps=deps)

    run_id = service.run_gc()

    with session_scope() as session:
        run = session.get(Run, run_id)
        assert run.kind == RunKind.GC
        assert run.status == RunStatus.SUCCESS
    assert pbs.gc_started is True


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
