"""Job entry points with a single-run guard.

``JobService`` is the seam between triggers (the scheduler now, the REST API in M4) and
the cycle logic. A non-reentrant lock guarantees only **one** run — backup or GC — is
ever in flight, so a manual run can't collide with a scheduled one.
"""

from __future__ import annotations

import logging
import threading

from ..core.config_store import ConfigStore
from ..db import session_scope
from ..db.models import RunKind, RunTrigger
from ..db.prune import PruneResult, prune_history
from .backup_cycle import run_backup_cycle, run_verify_cycle
from .deps import CycleDeps
from .gc import run_gc
from .recorder import RunRecorder

log = logging.getLogger("joulenap.jobs")


class AlreadyRunningError(RuntimeError):
    """Raised when a run is requested while another is still in progress."""


class JobService:
    def __init__(self, config_store: ConfigStore, deps: CycleDeps | None = None):
        self._store = config_store
        self.deps = deps or CycleDeps.default()
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._lock.locked()

    # --- blocking entry points (internal / tests) ----------------------------

    def run_backup(self, trigger: RunTrigger = RunTrigger.MANUAL) -> int:
        """Run a full backup cycle to completion. Returns the run id."""
        return self._run(RunKind.CYCLE, trigger, run_backup_cycle)

    def run_gc(self, trigger: RunTrigger = RunTrigger.MANUAL) -> int:
        """Run a standalone GC (no power management) to completion. Returns the run id."""
        return self._run(RunKind.GC, trigger, run_gc)

    def run_verify(self, trigger: RunTrigger = RunTrigger.MANUAL) -> int:
        """Run a full verification cycle (wake -> verify -> power-off). Returns the run id."""
        return self._run(RunKind.VERIFY, trigger, run_verify_cycle)

    # --- non-blocking entry points (HTTP / scheduler) ------------------------

    def submit_backup(self, trigger: RunTrigger = RunTrigger.MANUAL) -> int:
        """Start a backup cycle in the background; return its run id immediately."""
        return self._submit(RunKind.CYCLE, trigger, run_backup_cycle)

    def submit_gc(self, trigger: RunTrigger = RunTrigger.MANUAL) -> int:
        """Start a standalone GC in the background; return its run id immediately."""
        return self._submit(RunKind.GC, trigger, run_gc)

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
            config = self._store.config  # read live config at run time
            recorder = RunRecorder(kind, trigger)
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

        threading.Thread(target=worker, name=f"joulenap-{kind.value}", daemon=True).start()
        return run_id
