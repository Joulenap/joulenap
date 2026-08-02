"""The v1.0 route cycles for everything that isn't a backup: Sync, External and Verify.

One entry point, :func:`run_route`, dispatches on ``route.kind``; the backup kind lives in
``backup_cycle.run_route_backup`` (it has its own per-source failure policy) and everything
these three need is imported from there rather than reimplemented.

Like the backup route cycle, none of this touches power: ``JobService._execute`` holds a
:class:`~.lease.PowerLease` on every PBS the route needs — both boxes, for a sync — around
the call, so a cycle starts with the box awake and ends without putting it to sleep.
"""

from __future__ import annotations

from collections.abc import Callable

from ..config import Config, PbsDevice, Route
from ..connectors.pbs import DatastoreStatus
from ..db.models import LogLevel, RunKind, RunStatus, StepName
from ..notify.messages import RunContext
from .backup_cycle import (
    CycleAbort,
    CycleCancelled,
    _find_device,
    _refresh_route_backup_cache,
    _route_gc_step,
    _route_read_datastore,
    _route_verify_step,
    _wait_or_stop,
    run_route_backup,
    watch_external_tasks,
)
from .deps import CycleDeps
from .recorder import RunRecorder

# ``(config, route, target, recorder, deps) -> datastore status for the notification``.
RouteBody = Callable[[Config, Route, PbsDevice, RunRecorder, CycleDeps], "DatastoreStatus | None"]

#: What kind of run each route kind records. The queue needs this to open the run row
#: before the cycle picks its branch, and history/metrics group on it.
RUN_KINDS: dict[str, RunKind] = {
    "backup": RunKind.CYCLE,
    "sync": RunKind.SYNC,
    "external": RunKind.MONITOR,
    "verify": RunKind.VERIFY,
}


def _require_pbs(config: Config, pbs_id: str, route: Route) -> PbsDevice:
    device = _find_device(config.pbss, pbs_id)
    if device is None:
        raise CycleAbort(f"route '{route.id}': pbs '{pbs_id}' no longer exists")
    return device


# --- sync --------------------------------------------------------------------


def _sync_body(
    config: Config, route: Route, target: PbsDevice, recorder: RunRecorder, deps: CycleDeps
) -> DatastoreStatus | None:
    """PBS -> PBS: (re)create the remote + sync job on the executing side, run it, wait.

    Which box executes is what ``sync_direction`` really decides: on ``pull`` the target
    fetches from the source, on ``push`` the source sends to the target. The job always lives
    on the side doing the work and its remote points at the *other* box, so the two
    directions are one code path with the pair swapped.
    """
    source = _require_pbs(config, route.source_pbs, route)
    push = route.sync_direction == "push"
    executor, peer = (source, target) if push else (target, source)
    name = f"joulenap-{route.id}"

    with recorder.step(StepName.SYNC) as step:
        recorder.log(
            LogLevel.INFO,
            f"sync {route.sync_direction}: {source.id} -> {target.id} "
            f"(job on {executor.id} as '{name}')",
        )
        with deps.connect_pbs(executor) as pbs:
            pbs.ensure_remote(
                name,
                host=peer.host,
                port=peer.port,
                auth_id=peer.api_token_id,
                password=peer.api_token_secret,
                fingerprint=peer.fingerprint,
            )
            pbs.ensure_sync_job(
                name,
                remote=name,
                remote_store=peer.datastore,
                store=executor.datastore,
                direction=route.sync_direction,
            )
            upid = pbs.run_sync_job(name, direction=route.sync_direction)
            step.detail = upid
            _wait_or_stop(pbs, upid, recorder, deps, StepName.SYNC.value, "pbs")

    # Maintenance runs on the target: it is the box that just gained snapshots, whichever
    # side pushed or pulled them.
    if route.options.gc:
        _route_gc_step(target, recorder, deps)
    else:
        recorder.skip_step(StepName.GC, "GC disabled for this route")

    if route.options.verify_after:
        _route_verify_step(target, recorder, deps, outdated_after=None)
    else:
        recorder.skip_step(StepName.VERIFY, "verify disabled for this route")

    # No last-backup cache refresh: a synced snapshot carries no hint of which PVE created
    # it, and guessing would attribute another PBS's guests to a local one.
    return _route_read_datastore(target, recorder, deps)


# --- external ----------------------------------------------------------------


def _external_source_pve(config: Config, target: PbsDevice, recorder: RunRecorder) -> str | None:
    """Which PVE's guests an external route's snapshots belong to, if that is knowable.

    Joulenap started none of these backups, so the only evidence is the storage mapping: if
    exactly one PVE backs up to this PBS, its guests are the ones in the datastore. With
    none or several, the attribution would be a guess and the cache is left alone.
    """
    candidates = [pve.id for pve in config.pves if target.id in pve.storages]
    if len(candidates) == 1:
        return candidates[0]
    recorder.log(
        LogLevel.WARN,
        f"{len(candidates)} PVE(s) back up to '{target.id}', so a snapshot cannot be "
        "attributed to one — skipping the last-backup cache refresh",
    )
    return None


def _external_body(
    config: Config, route: Route, target: PbsDevice, recorder: RunRecorder, deps: CycleDeps
) -> DatastoreStatus | None:
    """Wake and watch: the schedules live on PVE/PBS, Joulenap only holds the box awake.

    It starts no task of its own here — no vzdump, no GC, no verify — which is the whole
    point of the kind (issue #27, v0.9's external-schedules mode re-homed onto a route).
    """
    with recorder.step(StepName.MONITOR) as step:
        with deps.connect_pbs(target) as pbs:
            observed = watch_external_tasks(pbs, target.external, cancelled=deps.cancelled)
        if observed is None:
            step.detail = "no tasks observed"
            recorder.log(
                LogLevel.WARN,
                f"no PBS task appeared within {target.external.first_task_wait}s "
                "— check the schedules on PVE/PBS; powering off",
            )
        else:
            step.detail = f"{observed} task(s) observed"

    # Someone else's jobs (hopefully) wrote new snapshots — refresh the caches the dashboard
    # serves while the PBS sleeps, exactly like a managed route does.
    datastore = _route_read_datastore(target, recorder, deps)
    pve_id = _external_source_pve(config, target, recorder)
    if pve_id:
        _refresh_route_backup_cache(target, {pve_id: None}, recorder, deps)
    return datastore


# --- verify ------------------------------------------------------------------


def _verify_body(
    config: Config, route: Route, target: PbsDevice, recorder: RunRecorder, deps: CycleDeps
) -> DatastoreStatus | None:
    """Wake -> verify the target datastore -> done. ``reverify_days`` keeps it incremental
    (0 = re-verify everything); the windowing is PBS's own, Joulenap adds no knobs."""
    _route_verify_step(target, recorder, deps, outdated_after=route.options.reverify_days)
    return _route_read_datastore(target, recorder, deps)


# --- dispatch ----------------------------------------------------------------


_BODIES: dict[str, RouteBody] = {
    "sync": _sync_body,
    "external": _external_body,
    "verify": _verify_body,
}


def _run_body(
    config: Config,
    route: Route | None,
    recorder: RunRecorder,
    body: Callable[[], DatastoreStatus | None],
) -> RunContext | None:
    """Run a single-target body under the shared failure policy.

    Every kind here has one target, one outcome and no partial success, so they share the
    whole envelope: a cancel is the user's own click (recorded, not notified), an abort is a
    run that couldn't start, anything else is a failure — and the lease reads the recorded
    status to decide whether the box stays on for inspection.

    Returns the context to notify with, or ``None`` when the user cancelled.
    """
    datastore: DatastoreStatus | None = None
    try:
        datastore = body()
        recorder.finish(RunStatus.SUCCESS)
    except CycleCancelled:
        # No notification: the user pressed Stop and is standing at the UI.
        recorder.finish(RunStatus.ABORTED, error="Cancelled by user")
        return None
    except CycleAbort as exc:
        recorder.finish(RunStatus.ABORTED, error=str(exc))
    except Exception as exc:  # connector/task failures: the lease leaves the PBS on
        recorder.finish(RunStatus.FAILURE, error=str(exc))

    return RunContext(config=config, run=recorder.run, route=route, datastore=datastore)


def _simple_body(
    config: Config, route: Route, recorder: RunRecorder, deps: CycleDeps
) -> DatastoreStatus | None:
    body = _BODIES.get(route.kind)
    if body is None:
        raise CycleAbort(f"route '{route.id}': unsupported kind '{route.kind}'")
    target = _require_pbs(config, route.target, route)
    return body(config, route, target, recorder, deps)


def run_route(
    config: Config, route: Route, recorder: RunRecorder, deps: CycleDeps
) -> RunContext | None:
    """Execute one route of any kind. Sets the final run status itself.

    The single entry point the queue worker runs: everything that wakes a PBS is a route, so
    nothing else needs to know which flavour it is.
    """
    if route.kind == "backup":
        return run_route_backup(config, route, recorder, deps)
    return _run_body(
        config, route, recorder, lambda: _simple_body(config, route, recorder, deps)
    )


# --- ad-hoc maintenance ------------------------------------------------------


def run_pbs_maintenance(
    config: Config,
    pbs: PbsDevice,
    recorder: RunRecorder,
    deps: CycleDeps,
    *,
    action: str,
) -> RunContext | None:
    """Run a one-off GC or verify on a PBS, outside any route.

    The homepage's "Run GC" / "Run verify" buttons pick a *box*, not a route — you reach for
    them after a restore or a disk scare, and inventing a throwaway route for that would put
    a phantom entry in the topology. It reuses the route steps verbatim, so history reads
    identically; only the route column is empty.
    """

    def body() -> DatastoreStatus | None:
        if action == "gc":
            _route_gc_step(pbs, recorder, deps)
        elif action == "verify":
            # Everything, not just the recently-changed: an ad-hoc verify is a deliberate
            # "check this box now", and reverify_days is a per-route pacing knob.
            _route_verify_step(pbs, recorder, deps, outdated_after=None)
        else:
            raise CycleAbort(f"unsupported maintenance action '{action}'")
        return _route_read_datastore(pbs, recorder, deps)

    return _run_body(config, None, recorder, body)
