"""Job entry points with a single-run guard and a FIFO run queue.

``JobService`` is the seam between triggers (the scheduler, the REST API) and the cycle
logic. A non-reentrant lock guarantees only **one** run is ever in flight, so a manual run
can't collide with a scheduled one.

Everything goes through :meth:`JobService.enqueue`: runs queue instead of being rejected
(per-route schedules mean two routes can fire minutes apart) and each one takes a
:class:`~.lease.PowerLease` on the PBS devices it touches, so a shared box is woken once
and powered off only after the last run that needs it.

The service also owns the two things a cycle deliberately doesn't: the wake/power-off
steps in the run's timeline (the lease decides them, so it records them) and the result
notification, sent last so it can report whether the box actually went back to sleep.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from ..config import Config, PbsDevice, Route
from ..core.config_store import ConfigStore
from ..db import session_scope
from ..db.models import LogLevel, RunKind, RunStatus, RunTrigger, StepName, StepStatus
from ..db.prune import PruneResult, prune_history
from ..notify.messages import LocalizedError, RunContext
from .deps import CycleDeps
from .lease import LeaseDeps, PbsUnreachableError, PowerLease, ReleaseOutcome, lease_key
from .recorder import RunRecorder, set_detail
from .route_cycle import RUN_KINDS, run_pbs_maintenance, run_route

log = logging.getLogger("joulenap.jobs")

#: What a queued run actually does once its PBS devices are awake. ``subject`` is the
#: :class:`~..config.Route` for a route run and the :class:`~..config.PbsDevice` for an
#: ad-hoc one — whichever the entry names. It returns the context to notify with, or
#: ``None`` to say nothing (the user cancelled), and sets the run's final status itself.
CycleJob = Callable[[Config, Any, RunRecorder, CycleDeps], "RunContext | None"]


class AlreadyRunningError(RuntimeError):
    """Raised when a run is requested while another is still in progress."""


class AlreadyQueuedError(AlreadyRunningError):
    """Raised when a route is enqueued while it is already queued or running.

    Subclasses :class:`AlreadyRunningError` so the existing 409 handlers cover it too.
    """


@dataclass
class QueuedRun:
    """One entry in the run queue.

    ``key`` is what "already queued" means: a route's id, or ``pbs:<id>:gc`` for an ad-hoc
    maintenance run. Exactly one of ``route_id`` / ``pbs_id`` names what the run is about.
    """

    key: str
    trigger: RunTrigger
    kind: RunKind
    job: CycleJob
    route_id: str | None = None
    pbs_id: str | None = None
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
            pending_pbs_keys=self._pending_pbs_keys,
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

    def enqueue(self, item: QueuedRun) -> int:
        """Queue a run. Returns how many runs are ahead of it (0 = it starts now).

        Runs still execute strictly one at a time — serialisation is the design, not a
        limitation. Raises :class:`AlreadyQueuedError` if the same subject is already queued
        or running: two overlapping runs of one route would fight over the same snapshots.
        """
        with self._state_lock:
            if self._current is not None and self._current.key == item.key:
                raise AlreadyQueuedError(f"'{item.key}' is already running")
            if any(queued.key == item.key for queued in self._queue):
                raise AlreadyQueuedError(f"'{item.key}' is already queued")
            ahead = len(self._queue) + (1 if self._current is not None or self.is_running else 0)
            # Start the worker first: it blocks on ``_state_lock`` (which we hold) before
            # looking at the queue, so it can't see the empty deque and exit — and if the
            # thread fails to start, nothing was queued.
            self._ensure_worker()
            self._queue.append(item)
        log.info("Queued '%s' (%s), %d run(s) ahead", item.key, item.trigger.value, ahead)
        return ahead

    def run_route(
        self,
        route_id: str,
        trigger: RunTrigger = RunTrigger.MANUAL,
        *,
        power_off: bool = True,
    ) -> int:
        """Queue a run of one route. Raises ``KeyError`` if no such route is configured."""
        route = next((r for r in self._store.config.routes if r.id == route_id), None)
        if route is None:
            raise KeyError(route_id)
        return self.enqueue(
            QueuedRun(
                key=route.id,
                route_id=route.id,
                trigger=trigger,
                # An unknown kind still gets a run row (CYCLE) so the failure is recorded and
                # visible; run_route aborts it with a reason rather than raising into nothing.
                kind=RUN_KINDS.get(route.kind, RunKind.CYCLE),
                job=run_route,
                power_off=power_off,
            )
        )

    def run_maintenance(
        self,
        pbs_id: str,
        action: str,
        trigger: RunTrigger = RunTrigger.MANUAL,
        *,
        power_off: bool = True,
    ) -> int:
        """Queue an ad-hoc GC or verify on one PBS, outside any route.

        Raises ``KeyError`` for an unknown device and ``ValueError`` for an unknown action.
        """
        if action not in ("gc", "verify"):
            raise ValueError(action)
        device = next((p for p in self._store.config.pbss if p.id == pbs_id), None)
        if device is None:
            raise KeyError(pbs_id)
        return self.enqueue(
            QueuedRun(
                key=f"pbs:{pbs_id}:{action}",
                pbs_id=pbs_id,
                trigger=trigger,
                kind=RunKind.GC if action == "gc" else RunKind.VERIFY,
                job=lambda c, s, r, d: run_pbs_maintenance(c, s, r, d, action=action),
                power_off=power_off,
            )
        )

    def pending(self) -> list[QueuedRun]:
        """The queued (not yet started) runs, in order. The running one is ``current()``."""
        with self._state_lock:
            return list(self._queue)

    def current(self) -> QueuedRun | None:
        """The queued run that is executing right now, if any."""
        with self._state_lock:
            return self._current

    def dequeue(self, key: str) -> bool:
        """Drop a *queued* entry. False if it isn't queued — a run already in flight is
        stopped with :meth:`cancel`, not dequeued."""
        with self._state_lock:
            for item in self._queue:
                if item.key == key:
                    self._queue.remove(item)
                    log.info("Dequeued '%s'", key)
                    return True
        return False

    def _pending_pbs_keys(self) -> set[str]:
        """Which machines the queued runs still need, so the lease doesn't power one down
        seconds before the next run needs it.

        Returned as lease keys, not device ids: the lease counts machines, and the two must
        agree or a queued run on the *other* datastore of a box would not hold it.
        """
        pending = self.pending()
        ids = {item.pbs_id for item in pending if item.pbs_id}
        queued_routes = {item.route_id for item in pending if item.route_id}
        for route in self._store.config.routes:
            if route.id in queued_routes:
                ids.add(route.target)
                if route.source_pbs:
                    ids.add(route.source_pbs)
        # A device deleted between queueing and now resolves to nothing and is dropped: the
        # run itself will be skipped for the same reason.
        return {lease_key(pbs) for pbs in self._store.config.pbss if pbs.id in ids}

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
                    # Cleared with it, or a stop aimed at the run that just ended would pass
                    # cancel()'s guard and land on the next one (or be swallowed): _start
                    # sets the new id only after the DB insert, leaving a window where the
                    # stale id still matches while another run holds the lock.
                    self._current_run_id = None

    def _execute(self, item: QueuedRun) -> None:
        """Take the run's power leases, do its job, release them, then notify.

        The whole run — wake, work, power-off, notification — lives inside one
        ``with recorder:`` block so every phase lands in the same timeline and the message
        goes out last, when whether the box went back to sleep is finally known.
        """
        config = self._store.config  # one snapshot: the subject and its devices must agree
        resolved = self._resolve(config, item)
        if resolved is None:
            return
        subject, devices = resolved

        route = subject if isinstance(subject, Route) else None
        recorder = self._start(item.kind, item.trigger, route)
        item.run_id = recorder.run_id
        held: list[PbsDevice] = []
        multi = len(devices) > 1  # labels every WAIT/POWEROFF step of this run, consistently
        try:
            with recorder:
                log.info(
                    "Starting %s run for '%s' (%s)", item.kind.value, item.key, item.trigger.value
                )
                try:
                    for device in devices:
                        self._acquire_step(device, recorder, multi=multi)
                        held.append(device)
                except PbsUnreachableError as exc:
                    # A cancel abandons the wake wait the same way a timeout does, so say
                    # which one it was rather than filing every stopped run as a failure.
                    cancelled = self._cancel.is_set()
                    if cancelled:
                        recorder.finish(RunStatus.ABORTED, error=LocalizedError("cancelled"))
                    else:
                        recorder.finish(RunStatus.FAILURE, error=exc)
                    # Fall through the *same* release-and-notify path a completed run uses,
                    # rather than re-raising. Re-raising unwound past both of them, so
                    # "the backup server never came up" — the one failure this product
                    # exists to have an opinion about — left a history row and nothing
                    # else, no Telegram, no ntfy, no email. 0.9 notified here, so it was a
                    # regression, and the `pbs_unreachable` entry in the message catalogue
                    # was built for a message that could never be sent.
                    #
                    # Releasing matters just as much on a sync route: the target can be
                    # awake when the source never answers, and that box was otherwise left
                    # burning power with no POWEROFF step in the timeline.
                    left_on = self._release_all(item, held, recorder, multi=multi)
                    held = []
                    # A stopped run stays silent on purpose — the user is standing at the UI.
                    if not cancelled:
                        ctx = RunContext(config=config, run=recorder.run, route=route)
                        ctx.left_on = left_on
                        self._notify(ctx, recorder)
                    return
                if self._cancel.is_set():
                    # Stopped while the boxes were coming up. They are leased now — the
                    # release below is what puts them back to sleep — but there is nothing
                    # left to run, and the cycle's first act is I/O against a server that
                    # booted seconds ago. Letting it start meant an error there filed the run
                    # FAILURE and notified about it, for a run the user stopped by hand.
                    recorder.finish(RunStatus.ABORTED, error=LocalizedError("cancelled"))
                    ctx = None
                else:
                    # The cycle sets the run's final status itself and hands back what to say
                    # about it (None = cancelled, say nothing).
                    ctx = item.job(config, subject, recorder, self.deps)
                left_on = self._release_all(item, held, recorder, multi=multi)
                held = []
                if ctx is not None:
                    ctx.left_on = left_on
                    self._notify(ctx, recorder)
        finally:
            # Only reached with leases still held when the job raised out of the block.
            self._release_all(item, held, recorder=None)
            self._lock.release()

    def _resolve(
        self, config: Config, item: QueuedRun
    ) -> tuple[Route | PbsDevice, list[PbsDevice]] | None:
        """What this entry is about, and the PBS devices it needs awake.

        A route needs its target plus, for a sync, its source. ``None`` means the subject
        was deleted between queueing and starting — the run is skipped rather than half-run.

        Devices sharing a machine are collapsed to one: a sync between two datastores on one
        box is a supported setup, and waking or powering off a machine twice within a single
        run gives the timeline two steps for one physical event — the second of which can
        only ever report something already done.
        """
        if item.pbs_id is not None:
            device = next((p for p in config.pbss if p.id == item.pbs_id), None)
            if device is None:
                log.warning("PBS '%s' was deleted before its run started; dropped", item.pbs_id)
                return None
            return device, [device]

        route = next((r for r in config.routes if r.id == item.route_id), None)
        if route is None:
            log.warning("Route '%s' was deleted before its run started; dropped", item.route_id)
            return None
        devices: dict[str, PbsDevice] = {}
        for pbs_id in [route.target, *([route.source_pbs] if route.source_pbs else [])]:
            device = next((p for p in config.pbss if p.id == pbs_id), None)
            if device is None:
                log.error("Route '%s' points at unknown pbs '%s'; run skipped", route.id, pbs_id)
                return None
            devices.setdefault(lease_key(device), device)
        return route, list(devices.values())

    def _acquire_step(self, device: PbsDevice, recorder: RunRecorder, *, multi: bool) -> None:
        """Take the lease on one device, recorded as a WAIT step so the wake shows up in the
        run's timeline (and the "PBS left powered on" warning has something to key on).

        Labelled by device only when the run touches more than one box: a sync route's
        timeline needs to say *which* PBS, a backup route's doesn't.
        """
        with recorder.step(StepName.WAIT, label=device.id if multi else None) as step:
            was_awake = self.lease.acquire(device)
            set_detail(step, "already_awake" if was_awake else "woken")

    def _release_all(
        self,
        item: QueuedRun,
        devices: list[PbsDevice],
        recorder: RunRecorder | None,
        *,
        multi: bool | None = None,
    ) -> list[str]:
        """Drop every lease this run holds, recording each power-off decision. Returns the
        ids of the boxes genuinely left burning power, for the notification's warning.

        Devices are released independently: a sync route holds two leases and each box may
        have a different answer (another holder, a queued route, unmanaged power) — which
        is why the warning cannot be a single fact about the run.
        ``recorder=None`` is the crash path — the leases must still be dropped, but the run
        row is already being finalised, so there is nothing left to record against.

        ``multi`` labels the POWEROFF steps and must describe *the run*, not this call's
        slice of it: when a wake fails half way, only the boxes that came up are released,
        and deriving it from ``devices`` here would pair a labelled ``wait:pbs-02`` with a
        bare ``poweroff`` in the same timeline. The caller passes what ``_acquire_step``
        used; ``None`` keeps the old derive-it-here behaviour for the crash path.
        """
        succeeded = recorder is not None and recorder.run.status == RunStatus.SUCCESS
        power_off = self._power_off_policy(item, succeeded=succeeded)
        multi = len(devices) > 1 if multi is None else multi
        left_on: list[str] = []
        for device in devices:
            if recorder is None:
                if self.lease.release(device, power_off=power_off) is ReleaseOutcome.LEFT_ON:
                    left_on.append(device.id)
                continue
            with recorder.step(StepName.POWEROFF, label=device.id if multi else None) as step:
                outcome = self.lease.release(device, power_off=power_off)
                set_detail(step, outcome.key)
                if outcome is ReleaseOutcome.POWERED_OFF:
                    continue
                # Not a failure: leaving the box on is the *correct* outcome after a failed
                # run, for an unmanaged device, or while another route still needs it — the
                # detail says which. Only LEFT_ON costs the user power with nobody left to
                # fix it, so only that one earns the notification's warning.
                step.status = StepStatus.SKIPPED
                if outcome is ReleaseOutcome.LEFT_ON:
                    left_on.append(device.id)
        return left_on

    def _notify(self, ctx: RunContext, recorder: RunRecorder) -> None:
        """Send the result notification. A delivery failure is logged, never fatal — the run
        has already completed and its recorded status must not depend on the notifier."""
        if ctx.route is not None:
            ctx.next_at = self.deps.next_run(ctx.route.id)
        try:
            self.deps.notify(ctx)
        except Exception as exc:  # noqa: BLE001 - a broken channel must not fail the run
            recorder.log(LogLevel.WARN, f"notification failed: {exc}")

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
        self, kind: RunKind, trigger: RunTrigger, route: Route | None
    ) -> RunRecorder:
        """Wait for the single-run lock and create the run row. Caller owns the lock.

        Blocking is the point: the queue worker's whole job is to wait its turn. Anything
        that must *not* wait (a manual power-off) takes the lock through :meth:`exclusive`.
        """
        self._lock.acquire()
        try:
            # Clear any cancel left over from the previous run before this one can observe it.
            self._cancel.clear()
            self._cancel_power_off = False
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
        return recorder
