"""The backup route cycle — the heart of Joulenap (ARCHITECTURE.md).

    [wake + wait, held by the lease] -> vzdump per source PVE (selected guests + retention)
    -> [GC, if enabled] -> [verify, if enabled] -> record -> [power-off, held by the lease].

Power is not this module's business: ``JobService._execute`` takes a
:class:`~.lease.PowerLease` on every PBS the route needs around the call, so the cycle
starts with the boxes awake and ends without touching them. On any failure the run is
marked failed and the lease leaves the PBS **on** for inspection.

Also home to the pieces every route kind shares — the task-log tailer, the cancellable
task wait, and the external-schedules watch — which ``route_cycle`` imports rather than
reimplementing.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from ..config import Config, PbsDevice, PbsExternalConfig, Route, RouteGuests, RouteSource
from ..connectors.errors import TaskCancelled
from ..connectors.pbs import DatastoreStatus
from ..connectors.pve import Guest, build_prune_string
from ..db import session_scope
from ..db.datastore_stats import upsert_datastore_stat
from ..db.guest_backups import upsert_last_backups
from ..db.models import LogLevel, RunStatus, StepName
from ..notify.messages import GuestSummary, LocalizedError, RunContext
from .deps import CycleDeps
from .recorder import RunRecorder, set_detail

# Poll cadence while tailing a task's log — snappier than the plain wait default so the
# live Task-log panel narrates in near-real-time (Proxmox has no push API).
_TAIL_INTERVAL = 2.0

# Poll cadence of the external-schedules watch. Coarser than the log tail: nothing is
# narrated here, we only need to notice tasks appearing/finishing within a few seconds.
_MONITOR_INTERVAL = 10.0


# vzdump narrates one line per guest; these are its two outcomes:
#   INFO: Finished Backup of VM 100 (00:01:23)
#   ERROR: Backup of VM 101 failed - command 'lxc-freeze -n 101' failed: exit code 1
_VZDUMP_DONE = re.compile(r"Finished Backup of VM (\d+)")
_VZDUMP_FAILED = re.compile(r"ERROR: Backup of VM (\d+) failed")


def _guest_watcher(summary: GuestSummary, names: dict[int, str]):
    """A log-batch callback that fills ``summary`` from the vzdump output *as it streams*.

    One vzdump task covers every selected guest, so a single guest failing makes the whole
    task exit non-OK and the wait raises before the step body can record anything. Reading
    the outcome line by line into an object owned by the *caller* is what makes the tally
    survive that raise — which is precisely the run the user needs the detail for.
    """

    def watch(lines: list[tuple[int, str]]) -> None:
        for _line_no, text in lines:
            if _VZDUMP_DONE.search(text):
                summary.ok += 1
            elif match := _VZDUMP_FAILED.search(text):
                vmid = int(match.group(1))
                summary.failed.append(names.get(vmid) or str(vmid))

    return watch


def _tailer(recorder: RunRecorder, step: str, source: str, watch=None):
    """A ``wait_task(on_log=...)`` callback that persists each task-log batch.

    Best-effort: a failure to store a log line must never fail an otherwise-fine backup,
    so it's swallowed with a warning (the narration just misses a line). ``watch``, when
    given, also gets each batch — see :func:`_guest_watcher`.
    """

    def on_log(lines: list[tuple[int, str]]) -> None:
        try:
            recorder.task_log(step, source, lines)
        except Exception as exc:  # pragma: no cover - defensive
            recorder.log(LogLevel.WARN, f"could not store task-log line(s): {exc}")
        if watch is not None:
            watch(lines)

    return on_log


class CycleAbort(LocalizedError):
    """Raised when the PBS doesn't come up — the cycle aborts without powering off.

    Takes an ``_ERRORS`` key plus its parameters rather than a finished sentence, so the
    reason survives into the run row in a form both the notifier and the UI can render in
    the configured language.
    """


class CycleCancelled(Exception):
    """Raised when the user stopped the run from the UI (11.2).

    Separate from :class:`CycleAbort` so the run records *why* it ended and the power-off
    decision follows what the user chose in the stop dialog rather than the abort rules.
    """


class _TaskClient(Protocol):
    """The slice of PveClient/PbsClient that :func:`_wait_or_stop` needs."""

    def wait_task(self, upid: str, *args: Any, **kwargs: Any) -> dict[str, Any]: ...
    def stop_task(self, upid: str) -> None: ...


def _wait_or_stop(
    client: _TaskClient,
    upid: str,
    recorder: RunRecorder,
    deps: CycleDeps,
    step: str,
    source: str,
    watch: Callable[[list[tuple[int, str]]], None] | None = None,
) -> None:
    """Wait for a PVE/PBS task, stopping it remotely if the user cancels the run.

    Abandoning the wait isn't enough: the vzdump/GC would keep running on the far side while
    Joulenap considers itself idle, and the next run would collide with it. The stop is
    best-effort — if the API refuses, the run still ends cancelled and the reason is logged,
    because leaving the lock held would be the worse failure (that is the whole point of 11.2).
    """
    try:
        client.wait_task(
            upid,
            poll_interval=_TAIL_INTERVAL,
            on_log=_tailer(recorder, step, source, watch),
            should_cancel=deps.cancelled,
        )
    except TaskCancelled as exc:
        try:
            client.stop_task(upid)
            recorder.log(LogLevel.WARN, f"cancelled: asked {source.upper()} to stop task {upid}")
        except Exception as stop_exc:
            recorder.log(
                LogLevel.ERROR,
                f"cancelled, but could not stop {source.upper()} task {upid}: {stop_exc} "
                "— check it on the server",
            )
        raise CycleCancelled("Run cancelled") from exc


class _TaskLister(Protocol):
    """The slice of PbsClient that :func:`watch_external_tasks` needs."""

    def active_tasks(self) -> list[dict[str, Any]]: ...


def watch_external_tasks(
    pbs: _TaskLister,
    ext: PbsExternalConfig,
    *,
    cancelled: Callable[[], bool],
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> int | None:
    """Follow the PBS's own (externally scheduled) tasks until they are done.

    ``ext`` carries the two timeouts, taken from the PBS device's own ``external`` section:
    how slow a box is belongs to the box, not to the route watching it.

    Both knobs are timeouts, not fixed delays. Phase one waits up to ``first_task_wait``
    for the first task to *appear* — polling starts immediately, so a job that starts 90s
    after wake ends the wait at 90s; the full timeout is only ever spent when no job runs
    at all (returns None so the caller can flag it). Phase two then waits for
    ``idle_wait`` seconds of *continuous* silence, restarting the countdown whenever a
    new task shows up, so the gaps between chained jobs (backup -> prune -> GC -> sync)
    never cause an early power-off. Returns the number of distinct tasks observed.
    """
    seen: set[str] = set()

    def poll() -> bool:
        if cancelled():
            raise CycleCancelled("Run cancelled")
        tasks = pbs.active_tasks()
        seen.update(upid for t in tasks if (upid := t.get("upid")))
        return bool(tasks)

    deadline = clock() + ext.first_task_wait
    while not poll():
        if clock() >= deadline:
            return None
        sleep(_MONITOR_INTERVAL)

    quiet_since: float | None = None
    while True:
        if poll():
            quiet_since = None
        else:
            now = clock()
            if quiet_since is None:
                quiet_since = now
            if now - quiet_since >= ext.idle_wait:
                return len(seen)
        sleep(_MONITOR_INTERVAL)


# --- the route cycle ---------------------------------------------------------
#
# One run executes one **backup route**: N source PVEs (each possibly a cluster) -> one PBS
# target. Wake and power-off are deliberately absent — ``JobService._execute`` takes a
# :class:`~.lease.PowerLease` on the target around the job (M04), so the cycle starts with
# the box already awake and ends without touching its power.
#
# A cycle does not notify either: it *returns* the :class:`RunContext` describing what
# happened and ``_execute`` sends it once the leases are released, so the message can report
# whether the box actually went back to sleep. Returning ``None`` means "say nothing" — the
# user cancelled and is standing at the UI.


def _find_device(devices, device_id: str):
    return next((d for d in devices if d.id == device_id), None)


def _guests_by_node(selection: RouteGuests, guests: list[Guest]) -> dict[str, list[int]]:
    """Group the guests this source wants by the cluster node holding them — a route runs
    one vzdump per node, and only on nodes that actually have something to back up."""
    chosen = set(selection.list)
    picked: dict[str, list[int]] = {}
    for guest in guests:
        if selection.mode == "include" and guest.vmid not in chosen:
            continue
        if selection.mode == "exclude" and guest.vmid in chosen:
            continue
        picked.setdefault(guest.node, []).append(guest.vmid)
    return picked


def _route_backup_source(
    config: Config,
    route: Route,
    source: RouteSource,
    recorder: RunRecorder,
    deps: CycleDeps,
    summary: GuestSummary,
    step,
) -> set[int]:
    """Back up one source PVE onto the route's target: one vzdump per cluster node.

    Returns the vmids this source covered, so the last-backup cache can attribute each guest
    to the PVE it came from. Raises on failure — the caller records that against this
    source's step and moves on to the next source.
    """
    pve = _find_device(config.pves, source.pve)
    if pve is None:
        raise CycleAbort("source_pve_missing", pve=source.pve)
    storage = pve.storages.get(route.target)
    if not storage:
        raise CycleAbort("no_storage_mapping", pve=pve.id, pbs=route.target)

    prune = build_prune_string(route.retention.model_dump())
    step_name = f"{StepName.BACKUP.value}:{source.pve}"
    upids: list[str] = []
    covered: set[int] = set()

    with deps.connect_pve(pve) as client:
        # One cluster-wide listing feeds all three needs: the per-node grouping, the guest
        # tally and the {vmid: name} map the notification names failed guests with.
        guests = client.list_cluster_guests()
        names = {g.vmid: g.name for g in guests}
        per_node = _guests_by_node(source.guests, guests)
        # "all" and "exclude" both stay on vzdump's own ``all`` flag rather than the vmids we
        # just listed, so PVE keeps deciding: a guest marked *exclude from backup* is honoured,
        # and one created since the listing is still covered. Only "include" spells the vmids
        # out, because that is the whole point of the mode.
        all_guests = source.guests.mode != "include"
        excluded = source.guests.list if source.guests.mode == "exclude" else None
        if source.guests.mode == "include":
            # Only worth a warning for "include": there it means a guest you asked for is not
            # being backed up. A stale entry in an "exclude" list just skips nothing.
            missing = sorted(set(source.guests.list) - {g.vmid for g in guests})
            if missing:
                recorder.log(
                    LogLevel.WARN,
                    f"pve '{pve.id}': selected guest(s) {missing} are not on it "
                    "(deleted, a template, or migrated away); skipping them",
                )
        if not per_node:
            raise CycleAbort("no_guests", pve=pve.id)

        for node, vmids in per_node.items():
            upid = client.vzdump(
                storage,
                node=node,
                vmids=None if all_guests else vmids,
                all_guests=all_guests,
                exclude=excluded,
                mode=route.options.mode,
                prune_backups=prune,
                bwlimit=route.options.bwlimit,
            )
            upids.append(upid)
            step.detail = ", ".join(upids)
            # Counted before the wait: a task that dies still set out to back these up.
            summary.total += len(vmids)
            done_before = summary.ok
            _wait_or_stop(
                client,
                upid,
                recorder,
                deps,
                step_name,
                "pve",
                _guest_watcher(summary, names),
            )
            # The task exited OK, so every guest it covered was backed up whatever the log
            # parse made of it — a vzdump wording change must never report "0/14" on a good
            # run. Only on success, so a failed node doesn't advertise guests as backed up.
            summary.ok = done_before + len(vmids)
            covered |= set(vmids)
    return covered


def _route_preflight(
    route: Route, target: PbsDevice, recorder: RunRecorder, deps: CycleDeps
) -> None:
    """Abort before any vzdump if the target datastore is below the route's free-space floor.

    No-op when ``min_free_percent`` is 0 (the default), so the step only appears when the
    user opted in. An abort here leaves the PBS on for inspection (the lease's failure
    policy), matching the other failure paths.
    """
    threshold = route.options.min_free_percent
    if threshold <= 0:
        return
    with recorder.step(StepName.PRECHECK) as step:
        with deps.connect_pbs(target) as pbs:
            ds = pbs.datastore_status()
        _cache_route_datastore(target, recorder, ds)
        set_detail(
            step, "free_space", free=f"{ds.avail_pct:.1f}", avail=f"{ds.avail / 1_000_000_000:.0f}"
        )
        if ds.avail_pct < threshold:
            raise CycleAbort(
                "datastore_full",
                pbs=target.id,
                datastore=target.datastore,
                free=f"{ds.avail_pct:.1f}",
                threshold=threshold,
            )


def _route_gc_step(target: PbsDevice, recorder: RunRecorder, deps: CycleDeps) -> None:
    """Garbage-collect the route's target datastore while the PBS is still awake."""
    with recorder.step(StepName.GC) as step:
        with deps.connect_pbs(target) as pbs:
            upid = pbs.start_gc()
            step.detail = upid
            _wait_or_stop(pbs, upid, recorder, deps, StepName.GC.value, "pbs")


def _route_verify_step(
    target: PbsDevice, recorder: RunRecorder, deps: CycleDeps, *, outdated_after: int | None
) -> None:
    """Verify snapshots on the route's target. ``outdated_after=None`` -> only never-verified
    (i.e. this run's new) snapshots; an int -> also re-verify ones older than that many days
    (0 -> everything), which is what a Verify route will ask for (M06)."""
    with recorder.step(StepName.VERIFY) as step:
        with deps.connect_pbs(target) as pbs:
            if outdated_after is not None and outdated_after <= 0:
                upid = pbs.start_verify(ignore_verified=False)
            else:
                upid = pbs.start_verify(ignore_verified=True, outdated_after=outdated_after)
            step.detail = upid
            _wait_or_stop(pbs, upid, recorder, deps, StepName.VERIFY.value, "pbs")


def _cache_route_datastore(
    target: PbsDevice, recorder: RunRecorder, ds: DatastoreStatus
) -> None:
    """Persist the target's usage so the UI can show it while the PBS sleeps. Best-effort:
    a cache-write failure must never fail the run."""
    try:
        with session_scope() as session:
            upsert_datastore_stat(session, target.id, target.datastore, ds.total, ds.used)
    except Exception as exc:
        recorder.log(LogLevel.WARN, f"could not cache datastore usage: {exc}")


def _route_read_datastore(
    target: PbsDevice, recorder: RunRecorder, deps: CycleDeps
) -> DatastoreStatus | None:
    """Read the target's usage for the notification, while it is still awake. Best-effort:
    a read failure only costs the notification its usage line."""
    try:
        with deps.connect_pbs(target) as pbs:
            ds = pbs.datastore_status()
    except Exception as exc:
        recorder.log(LogLevel.WARN, f"could not read datastore usage: {exc}")
        return None
    recorder.log(LogLevel.INFO, f"PBS '{target.id}' datastore {ds.used_pct}% used")
    _cache_route_datastore(target, recorder, ds)
    return ds


def _refresh_route_backup_cache(
    target: PbsDevice,
    covered: Mapping[str, set[int] | None],
    recorder: RunRecorder,
    deps: CycleDeps,
) -> None:
    """Cache each guest's latest snapshot time per (source pve, target pbs), so the dashboard
    can show last-backup dates once the PBS sleeps again.

    One datastore lists snapshots by vmid with no idea which PVE they came from, so each
    source claims the vmids it actually backed up. Two PVEs sharing a vmid both get a row
    with the same time — that is a real PBS namespace collision, not something to fix here.
    A ``None`` vmid set claims *every* snapshot in the datastore — what an External route can
    say, since it did not choose the guests (it only watched someone else's job).
    Best-effort: the backup already succeeded, so a read/write failure is logged and dropped.
    """
    try:
        with deps.connect_pbs(target) as pbs:
            latest = pbs.latest_backups()
        cached = 0
        with session_scope() as session:
            for pve_id, vmids in covered.items():
                mine = latest if vmids is None else {v: t for v, t in latest.items() if v in vmids}
                upsert_last_backups(session, pve_id, target.id, mine)
                cached += len(mine)
    except Exception as exc:
        recorder.log(LogLevel.WARN, f"could not refresh last-backup cache: {exc}")
        return
    recorder.log(LogLevel.INFO, f"cached last-backup times for {cached} guest(s)")


def run_route_backup(
    config: Config, route: Route, recorder: RunRecorder, deps: CycleDeps
) -> RunContext | None:
    """Execute one backup route, recording each step. Sets the final run status itself.

    Sources run in order and are isolated from each other: one unreachable PVE fails the run
    but the remaining sources still get their backup, because a shared target and a shared
    wake window are exactly what makes a multi-source route worth having.

    Returns the context to notify with, or ``None`` when the user cancelled.
    """
    datastore: DatastoreStatus | None = None
    # Owned here, not by the per-source step: a guest failing takes the whole vzdump task
    # down and unwinds that frame, and the failed run is exactly the one whose tally we want.
    guests = GuestSummary()
    covered: dict[str, set[int]] = {}
    failed: list[str] = []
    try:
        target = _find_device(config.pbss, route.target)
        if target is None:
            raise CycleAbort("target_pbs_missing", route=route.id, pbs=route.target)

        _route_preflight(route, target, recorder, deps)

        for source in route.sources:
            # A cancel that lands between sources must not start the next one — the task
            # waits check the flag themselves, this covers the gaps between them.
            if deps.cancelled():
                raise CycleCancelled("Run cancelled")
            try:
                with recorder.step(StepName.BACKUP, label=source.pve) as step:
                    covered[source.pve] = _route_backup_source(
                        config, route, source, recorder, deps, guests, step
                    )
            except CycleCancelled:
                raise
            except Exception as exc:
                # The step row already carries the failure; keep going so one broken source
                # doesn't cost the others their backup.
                failed.append(source.pve)
                recorder.log(LogLevel.ERROR, f"source '{source.pve}' failed: {exc}")
        recorder.run.guests_ok = guests.ok

        if deps.cancelled():
            raise CycleCancelled("Run cancelled")

        # GC and verify still run when a source failed: the PBS is awake and the snapshots
        # the other sources wrote are real.
        if route.options.gc:
            _route_gc_step(target, recorder, deps)
        else:
            recorder.skip_step(StepName.GC, "gc_disabled")

        if deps.cancelled():
            raise CycleCancelled("Run cancelled")

        if route.options.verify_after:
            _route_verify_step(target, recorder, deps, outdated_after=None)
        else:
            recorder.skip_step(StepName.VERIFY, "verify_disabled")

        datastore = _route_read_datastore(target, recorder, deps)
        _refresh_route_backup_cache(target, covered, recorder, deps)

        if failed:
            recorder.finish(
                RunStatus.FAILURE,
                error=LocalizedError("sources_failed", sources=", ".join(failed)),
            )
        else:
            recorder.finish(RunStatus.SUCCESS)
    except CycleCancelled:
        # No notification: the user pressed Stop and is standing at the UI — a "backup
        # aborted" push would just be noise about their own click.
        recorder.finish(RunStatus.ABORTED, error=LocalizedError("cancelled"))
        return None
    except CycleAbort as exc:
        recorder.finish(RunStatus.ABORTED, error=exc)
    except Exception as exc:  # connector/task failures: the lease leaves the PBS on
        recorder.finish(RunStatus.FAILURE, error=exc)

    return RunContext(
        config=config, run=recorder.run, route=route, datastore=datastore, guests=guests
    )
