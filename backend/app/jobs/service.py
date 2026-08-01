"""Job entry points with a single-run guard and a FIFO run queue.

``JobService`` is the seam between triggers (the scheduler, the REST API) and the cycle
logic. A non-reentrant lock guarantees only **one** run is ever in flight, so a manual run
can't collide with a scheduled one.

Route runs go through :meth:`JobService.enqueue`: they queue instead of being rejected
(per-route schedules mean two routes can fire minutes apart) and each one takes a
:class:`~.lease.PowerLease` on the PBS devices it touches, so a shared box is woken once
and powered off only after the last route that needs it.

ponytail: two admission paths coexist during the overhaul — the queue, and the 0.9
``_run``/``_submit`` entry points below, which still 409 when busy. Both take the same
``_lock``, so they serialise correctly against each other; the 0.9 half goes away with the
scheduler/API port in M07.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from ..config import Config, PbsDevice, Route
from ..core.config_store import ConfigStore
from ..db import session_scope
from ..db.models import RunKind, RunStatus, RunTrigger
from ..db.prune import PruneResult, prune_history
from .backup_cycle import run_backup_cycle, run_gc_cycle, run_monitor_cycle, run_verify_cycle
from .deps import CycleDeps
from .lease import LeaseDeps, PbsUnreachableError, PowerLease
from .recorder import RunRecorder

log = logging.getLogger("joulenap.jobs")

# What a queued run actually does once its PBS devices are awake. The cycles in
# backup_cycle.py have this shape; M05 supplies the route-aware ones.
CycleJob = Callable[[Config, RunRecorder, CycleDeps], None]


class AlreadyRunningError(RuntimeError):
    """Raised when a run is requested while another is still in progress."""


class AlreadyQueuedError(AlreadyRunningError):
    """Raised when a route is enqueued while it is already queued or running.

    Subclasses :class:`AlreadyRunningError` so the existing 409 handlers cover it too.
    """


@dataclass
class QueuedRun:
    """One entry in the run queue."""

    route_id: str
    trigger: RunTrigger
    kind: RunKind
    job: CycleJob
    # The manual "power off when finished" toggle; a scheduled run always wants it on.
    power_off: bool = True
    run_id: int | None = None  # assigned when the run actually starts


class JobService:
    def __init__(
        self,
        config_store: ConfigStore,
        deps: CycleDeps | None = None,
        lease_deps: LeaseDeps | None = None,
    ):
        self._store = config_store
        self.deps = deps or CycleDeps.default()
        self._lock = threading.Lock()
        # Run queue. ``_state_lock`` guards the deque, the running entry and the worker
        # handle; it is always taken *inside* ``_lock``, never the other way round.
        self._state_lock = threading.Lock()
        self._queue: deque[QueuedRun] = deque()
        self._current: QueuedRun | None = None
        self._worker: threading.Thread | None = None
        # Cancellation (11.2). The cycle can't be interrupted from outside — a blocking
        # thread never yields — so it polls these through `deps`, which we point at our own
        # state here. Guarded by `_lock` only for the run id; the Event is already atomic.
        self._cancel = threading.Event()
        self._cancel_power_off = False
        self._current_run_id: int | None = None
        self.deps.cancelled = self._cancel.is_set
        self.deps.cancel_power_off = lambda: self._cancel_power_off
        # The lease reads both of the above live: a cancel must also abandon a wake wait.
        self.lease = PowerLease(
            lease_deps or LeaseDeps.default(),
            pending_pbs_ids=self._pending_pbs_ids,
            cancelled=self._cancel.is_set,
        )

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

    # --- run queue -----------------------------------------------------------

    def enqueue(
        self,
        route_id: str,
        trigger: RunTrigger,
        job: CycleJob,
        *,
        kind: RunKind = RunKind.CYCLE,
        power_off: bool = True,
    ) -> int:
        """Queue a run of ``route_id``. Returns how many runs are ahead of it (0 = it starts
        now).

        Runs still execute strictly one at a time — serialisation is the design, not a
        limitation. Raises :class:`AlreadyQueuedError` if that route is already queued or
        running: two overlapping runs of the same route would fight over the same snapshots.
        """
        item = QueuedRun(
            route_id=route_id, trigger=trigger, kind=kind, job=job, power_off=power_off
        )
        with self._state_lock:
            if self._current is not None and self._current.route_id == route_id:
                raise AlreadyQueuedError(f"Route '{route_id}' is already running")
            if any(queued.route_id == route_id for queued in self._queue):
                raise AlreadyQueuedError(f"Route '{route_id}' is already queued")
            ahead = len(self._queue) + (1 if self._current is not None or self.is_running else 0)
            # Start the worker first: it blocks on ``_state_lock`` (which we hold) before
            # looking at the queue, so it can't see the empty deque and exit — and if the
            # thread fails to start, nothing was queued.
            self._ensure_worker()
            self._queue.append(item)
        log.info("Queued route '%s' (%s), %d run(s) ahead", route_id, trigger.value, ahead)
        return ahead

    def pending(self) -> list[QueuedRun]:
        """The queued (not yet started) runs, in order. The running one is ``current()``."""
        with self._state_lock:
            return list(self._queue)

    def current(self) -> QueuedRun | None:
        """The queued run that is executing right now, if any."""
        with self._state_lock:
            return self._current

    def dequeue(self, route_id: str) -> bool:
        """Drop a *queued* route. False if it isn't queued — a run already in flight is
        stopped with :meth:`cancel`, not dequeued."""
        with self._state_lock:
            for item in self._queue:
                if item.route_id == route_id:
                    self._queue.remove(item)
                    log.info("Dequeued route '%s'", route_id)
                    return True
        return False

    def _pending_pbs_ids(self) -> set[str]:
        """Which PBS devices the queued runs still need, so the lease doesn't power one down
        seconds before the next route needs it."""
        queued = {item.route_id for item in self.pending()}
        ids: set[str] = set()
        for route in self._store.config.routes:
            if route.id in queued:
                ids.add(route.target)
                if route.source_pbs:
                    ids.add(route.source_pbs)
        return ids

    def _ensure_worker(self) -> None:
        """Start the drain thread if it isn't running. Caller holds ``_state_lock`` — the
        worker clears ``_worker`` under the same lock before it exits, so an enqueue can
        never hand work to a thread that is already stopping."""
        if self._worker is not None:
            return
        worker = threading.Thread(target=self._drain, name="joulenap-queue", daemon=True)
        self._worker = worker
        try:
            worker.start()
        except BaseException:
            self._worker = None
            raise

    def _drain(self) -> None:
        """Run queued entries one after another until the queue is empty."""
        while True:
            with self._state_lock:
                if not self._queue:
                    self._worker = None
                    return
                item = self._queue.popleft()
                self._current = item
            try:
                self._execute(item)
            except Exception:
                # One bad run must never stall the queue behind it.
                log.exception("Queued run for route '%s' failed", item.route_id)
            finally:
                with self._state_lock:
                    self._current = None

    def _execute(self, item: QueuedRun) -> None:
        """Take the route's power leases, run its job, then release them under the policy."""
        config = self._store.config  # one snapshot: the route and its devices must agree
        route = next((r for r in config.routes if r.id == item.route_id), None)
        if route is None:
            log.warning("Route '%s' was deleted before its run started; dropped", item.route_id)
            return
        devices = self._route_devices(config, route)
        if devices is None:
            return

        recorder, _ = self._start(item.kind, item.trigger, blocking=True, route=route)
        item.run_id = recorder.run_id
        held: list[PbsDevice] = []
        succeeded = False
        try:
            with recorder:
                log.info(
                    "Starting %s run for route '%s' (%s)",
                    item.kind.value,
                    route.id,
                    item.trigger.value,
                )
                try:
                    for device in devices:
                        self.lease.acquire(device)
                        held.append(device)
                except PbsUnreachableError as exc:
                    # A cancel abandons the wake wait the same way a timeout does, so say
                    # which one it was rather than filing every stopped run as a failure.
                    if self._cancel.is_set():
                        recorder.finish(RunStatus.ABORTED, error="Cancelled by user")
                    else:
                        recorder.finish(RunStatus.FAILURE, error=str(exc))
                    raise
                item.job(config, recorder, self.deps)
                # The cycle sets the run's final status itself; read it while the recorder's
                # session is still open.
                succeeded = recorder.run.status == RunStatus.SUCCESS
        finally:
            power_off = self._power_off_policy(item, succeeded=succeeded)
            for device in held:
                # Independently: a sync route holds two leases and each box may have a
                # different answer (another queued route, unmanaged power).
                self.lease.release(device, power_off=power_off)
            self._lock.release()

    def _route_devices(self, config: Config, route: Route) -> list[PbsDevice] | None:
        """The PBS devices a route needs awake — its target, plus the source of a sync route.
        None if one of them no longer exists (the run is skipped rather than half-run)."""
        devices: list[PbsDevice] = []
        for pbs_id in [route.target, *([route.source_pbs] if route.source_pbs else [])]:
            device = next((p for p in config.pbss if p.id == pbs_id), None)
            if device is None:
                log.error("Route '%s' points at unknown pbs '%s'; run skipped", route.id, pbs_id)
                return None
            devices.append(device)
        return devices

    def _power_off_policy(self, item: QueuedRun, *, succeeded: bool) -> bool:
        """Whether *this run* wants its PBS devices powered off. The lease still has the last
        word (another holder, or a queued route that needs the box).

        A failed run leaves the PBS on for inspection; a cancelled one honours the toggle in
        the stop dialog; a manual run honours its "power off when finished" flag.
        """
        if not item.power_off:
            return False
        if self._cancel.is_set():
            return self._cancel_power_off
        return succeeded

    # --- blocking entry points (internal / tests) ----------------------------

    def _backup_slot(self):
        """``(RunKind, cycle fn)`` for the backup slot: the full backup cycle, or — in
        external-schedules mode — the watch cycle (wake -> watch PVE/PBS's own jobs ->
        power off). One switch here covers the scheduler fire, the REST "run now" and the
        blocking test entry point alike."""
        if self._store.config.backup.external.enabled:
            return RunKind.MONITOR, run_monitor_cycle
        return RunKind.CYCLE, run_backup_cycle

    def run_backup(
        self, trigger: RunTrigger = RunTrigger.MANUAL, *, power_off: bool = True
    ) -> int:
        """Run the backup slot's cycle to completion. Returns the run id."""
        kind, cycle = self._backup_slot()
        return self._run(kind, trigger, lambda c, r, d: cycle(c, r, d, power_off=power_off))

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
        """Start the backup slot's cycle in the background; return its run id immediately."""
        kind, cycle = self._backup_slot()
        return self._submit(kind, trigger, lambda c, r, d: cycle(c, r, d, power_off=power_off))

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

    def _start(
        self,
        kind: RunKind,
        trigger: RunTrigger,
        *,
        blocking: bool = False,
        route: Route | None = None,
    ) -> tuple[RunRecorder, object]:
        """Acquire the single-run lock and create the run row. Caller owns the lock.

        ``blocking`` is for the queue worker, whose whole job is to wait its turn; every
        other caller wants the immediate AlreadyRunningError.
        """
        if not self._lock.acquire(blocking=blocking):
            raise AlreadyRunningError("A backup or GC run is already in progress")
        try:
            # Clear any cancel left over from the previous run before this one can observe it.
            self._cancel.clear()
            self._cancel_power_off = False
            config = self._store.config  # read live config at run time
            recorder = RunRecorder(
                kind,
                trigger,
                route_id=route.id if route else None,
                route_name=(route.name or route.id) if route else None,
            )
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
