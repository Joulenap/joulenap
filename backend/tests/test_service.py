"""JobService: single-run guard and run-id return value."""

from __future__ import annotations

import threading
import time

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


# --- cancellation (11.2) -----------------------------------------------------


def test_cancel_ends_the_run_aborted_and_frees_the_lock(temp_config, temp_db):
    # The point of 11.2: a stuck run blocks every later job *and* manual power-off until
    # restart. After a cancel the lock must be free and a new run must start immediately.
    started = threading.Event()
    release = threading.Event()

    def blocking_wol(_config):
        started.set()
        assert release.wait(timeout=5)

    deps, _pve, _pbs, _power = make_deps(wol=blocking_wol)
    service = JobService(ConfigStore.load_or_create(), deps=deps)

    run_id = service.submit_backup()
    assert started.wait(timeout=5)
    assert service.cancel(run_id) is True
    release.set()

    deadline = time.monotonic() + 5
    while service.is_running and time.monotonic() < deadline:
        time.sleep(0.02)
    assert service.is_running is False

    with session_scope() as session:
        run = session.get(Run, run_id)
        assert run.status == RunStatus.ABORTED
        assert "ancelled" in (run.error or "")
    # The lock is genuinely free: another run starts rather than raising.
    assert service.run_gc() != run_id


def test_cancel_refuses_a_run_that_is_not_the_one_in_flight(temp_config, temp_db):
    # A click landing as one run ends and the next begins must not stop the new run.
    started = threading.Event()
    release = threading.Event()

    def blocking_wol(_config):
        started.set()
        assert release.wait(timeout=5)

    deps, _pve, _pbs, _power = make_deps(wol=blocking_wol)
    service = JobService(ConfigStore.load_or_create(), deps=deps)

    run_id = service.submit_backup()
    assert started.wait(timeout=5)
    try:
        assert service.cancel(run_id + 999) is False
    finally:
        release.set()
    deadline = time.monotonic() + 5
    while service.is_running and time.monotonic() < deadline:
        time.sleep(0.02)

    with session_scope() as session:
        assert session.get(Run, run_id).status == RunStatus.SUCCESS  # ran to completion


def test_cancel_is_refused_when_nothing_is_running(temp_config, temp_db):
    deps, _pve, _pbs, _power = make_deps()
    service = JobService(ConfigStore.load_or_create(), deps=deps)
    run_id = service.run_backup()
    assert service.cancel(run_id) is False


def test_a_stale_cancel_does_not_kill_the_next_run(temp_config, temp_db):
    # Cancel arrives moments before the run ends on its own; the flag must not leak into
    # the run that starts next.
    deps, _pve, _pbs, _power = make_deps()
    service = JobService(ConfigStore.load_or_create(), deps=deps)
    first = service.run_backup()
    service.cancel(first)  # refused (nothing running), but prove the state is clean anyway

    second = service.run_backup()
    with session_scope() as session:
        assert session.get(Run, second).status == RunStatus.SUCCESS
