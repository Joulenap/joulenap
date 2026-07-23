"""Job entry points with a single-run guard.

``JobService`` is the seam between triggers (the scheduler now, the REST API in M4) and
the cycle logic. A non-reentrant lock guarantees only **one** run — backup or GC — is
ever in flight, so a manual run can't collide with a scheduled one.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from ..core.config_store import ConfigStore
from ..db import session_scope
from ..db.models import RunKind, RunStatus, RunTrigger
from ..db.prune import PruneResult, prune_history
from .backup_cycle import run_backup_cycle, run_gc_cycle, run_verify_cycle
from .deps import CycleDeps
from .recorder import RunRecorder

log = logging.getLogger("joulenap.jobs")


class AlreadyRunningError(RuntimeError):
    """Raised when a run is requested while another is still in progress."""


class JobService:
    def __init__(self, config_store: ConfigStore, deps: CycleDeps | None = None):
        self._store = config_store
        self.deps = deps or CycleDeps.default()
        self._lock = threading.Lock()
        # Cancellation (11.2). The cycle can't be interrupted from outside — a blocking
        # thread never yields — so it polls these through `deps`, which we point at our own
        # state here. Guarded by `_lock` only for the run id; the Event is already atomic.
        self._cancel = threading.Event()
        self._cancel_power_off = False
        self._current_run_id: int | None = None
        self.deps.cancelled = self._cancel.is_set
        self.deps.cancel_power_off = lambda: self._cancel_power_off

    @property
    def is_running(self) -> bool:
        return self._lock.locked()

    def cancel(self, run_id: int, *, power_off: bool = False) -> bool:
        """Ask the in-flight run to stop. Returns False if it isn't the one running.

        The run id is required rather than "cancel whatever is running": a click that lands
        just as one run finishes and the next begins would otherwise stop the wrong job. The
        flag is cooperative — the worker notices within a poll interval, stops the remote
        task, and releases the lock on its way out.
        """
        if self._current_run_id != run_id or not self.is_running:
            return False
        self._cancel_power_off = power_off
        self._cancel.set()
        log.info("Cancellation requested for run %d (power_off=%s)", run_id, power_off)
        return True

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        """Hold the single-run lock for a non-job operation (e.g. a manual power-off) so it
        can't race a run *starting* in the gap of a check-then-act. Raises AlreadyRunningError
        if a run already holds the lock; releases it when the block exits."""
        if not self._lock.acquire(blocking=False):
            raise AlreadyRunningError("A backup or GC run is already in progress")
        try:
            yield
        finally:
            self._lock.release()

    # --- blocking entry points (internal / tests) ----------------------------

    def run_backup(
        self, trigger: RunTrigger = RunTrigger.MANUAL, *, power_off: bool = True
    ) -> int:
        """Run a full backup cycle to completion. Returns the run id."""
        return self._run(
            RunKind.CYCLE, trigger, lambda c, r, d: run_backup_cycle(c, r, d, power_off=power_off)
        )

    def run_gc(self, trigger: RunTrigger = RunTrigger.MANUAL, *, power_off: bool = True) -> int:
        """Run a full GC cycle (wake -> GC -> power-off) to completion. Returns the run id."""
        return self._run(
            RunKind.GC, trigger, lambda c, r, d: run_gc_cycle(c, r, d, power_off=power_off)
        )

    def run_verify(self, trigger: RunTrigger = RunTrigger.MANUAL) -> int:
        """Run a full verification cycle (wake -> verify -> power-off). Returns the run id."""
        return self._run(RunKind.VERIFY, trigger, run_verify_cycle)

    # --- non-blocking entry points (HTTP / scheduler) ------------------------

    def submit_backup(
        self, trigger: RunTrigger = RunTrigger.MANUAL, *, power_off: bool = True
    ) -> int:
        """Start a backup cycle in the background; return its run id immediately."""
        return self._submit(
            RunKind.CYCLE, trigger, lambda c, r, d: run_backup_cycle(c, r, d, power_off=power_off)
        )

    def submit_gc(self, trigger: RunTrigger = RunTrigger.MANUAL, *, power_off: bool = True) -> int:
        """Start a full GC cycle in the background; return its run id immediately."""
        return self._submit(
            RunKind.GC, trigger, lambda c, r, d: run_gc_cycle(c, r, d, power_off=power_off)
        )

    def submit_verify(self, trigger: RunTrigger = RunTrigger.MANUAL) -> int:
        """Start a full verification cycle in the background; return its run id immediately."""
        return self._submit(RunKind.VERIFY, trigger, run_verify_cycle)

    # --- history pruning -----------------------------------------------------

    def run_prune(self) -> PruneResult:
        """Delete run history + activity-log rows older than the configured window.

        Independent of the single-run lock: it only touches *finished* rows, so it can
        run alongside an in-flight backup without colliding with the recorder.
        """
        days = self._store.config.maintenance.history.retention_days
        with session_scope() as session:
            result = prune_history(session, retention_days=days)
        if result.total:
            log.info(
                "Pruned history: %d runs + %d log lines older than %d days",
                result.runs_deleted,
                result.logs_deleted,
                days,
            )
        return result

    # --- internals -----------------------------------------------------------

    def _start(self, kind: RunKind, trigger: RunTrigger) -> tuple[RunRecorder, object]:
        """Acquire the single-run lock and create the run row. Caller owns the lock."""
        if not self._lock.acquire(blocking=False):
            raise AlreadyRunningError("A backup or GC run is already in progress")
        try:
            # Clear any cancel left over from the previous run before this one can observe it.
            self._cancel.clear()
            self._cancel_power_off = False
            config = self._store.config  # read live config at run time
            recorder = RunRecorder(kind, trigger)
            self._current_run_id = recorder.run_id
        except BaseException:
            self._lock.release()
            raise
        return recorder, config

    def _run(self, kind: RunKind, trigger: RunTrigger, job) -> int:
        recorder, config = self._start(kind, trigger)
        try:
            with recorder:
                log.info("Starting %s run (%s)", kind.value, trigger.value)
                job(config, recorder, self.deps)
                return recorder.run_id
        finally:
            self._lock.release()

    def _submit(self, kind: RunKind, trigger: RunTrigger, job) -> int:
        recorder, config = self._start(kind, trigger)
        run_id = recorder.run_id

        def worker() -> None:
            try:
                with recorder:
                    log.info("Starting %s run (%s)", kind.value, trigger.value)
                    job(config, recorder, self.deps)
            finally:
                self._lock.release()

        try:
            threading.Thread(target=worker, name=f"joulenap-{kind.value}", daemon=True).start()
        except BaseException:
            # The worker (and its lock-release + recorder finalisation) never runs, so do it
            # here — otherwise the run is stuck RUNNING and the single-run lock is held forever,
            # 409-ing every later run until restart (BE-B6).
            try:
                recorder.finish(RunStatus.FAILURE, error="worker thread failed to start")
            finally:
                recorder.close()
                self._lock.release()
            raise
        return run_id
