"""The backup cycle — the heart of Joulenap (ARCHITECTURE.md).

    wake (WoL) -> wait for PBS -> vzdump (selected guests + retention) ->
    [GC, if enabled] -> power-off (only on success) -> record.

On a wait timeout the cycle *aborts* (PBS never came up, nothing to power off). On any
later failure the run is marked failed and the PBS is **left on** for inspection — the
power-off step simply never runs.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any, Protocol

from ..config import Config
from ..connectors.errors import TaskCancelled
from ..connectors.pbs import DatastoreStatus
from ..connectors.pve import Guest, build_prune_string
from ..db import session_scope
from ..db.datastore_stats import upsert_datastore_stat
from ..db.guest_backups import upsert_last_backups
from ..db.models import LogLevel, RunStatus, StepName, StepStatus
from ..notify.messages import GuestSummary
from .deps import CycleDeps
from .recorder import RunRecorder

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


def _tailer(recorder: RunRecorder, step: StepName, source: str, watch=None):
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


class CycleAbort(Exception):
    """Raised when the PBS doesn't come up — the cycle aborts without powering off."""


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
    step: StepName,
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


def select_vmids(config: Config, guests: list[Guest]) -> tuple[list[int] | None, bool]:
    """Resolve the configured guest selection into vzdump arguments.

    Returns ``(vmids, all_guests)``: for ``mode=all`` -> ``(None, True)``; otherwise an
    explicit id list. ``exclude`` is materialised from ``guests`` (the node's current
    guests) by dropping the excluded ids.
    """
    selection = config.backup.guests
    if selection.mode == "all":
        return None, True
    if selection.mode == "include":
        return list(selection.list), False
    excluded = set(selection.list)
    return [g.vmid for g in guests if g.vmid not in excluded], False


def _run_backup_step(
    config: Config, recorder: RunRecorder, deps: CycleDeps, summary: GuestSummary
) -> None:
    with recorder.step(StepName.BACKUP) as step:
        with deps.build_pve(config) as pve:
            # One listing feeds all three needs: the exclude-mode selection, the guest count
            # and the {vmid: name} map the notification names failed guests with.
            guests = pve.list_guests()
            vmids, all_guests = select_vmids(config, guests)
            if not all_guests and not vmids:
                raise CycleAbort("No guests selected for backup")
            summary.total = len(guests) if all_guests else len(vmids)
            prune = build_prune_string(config.backup.retention.model_dump())
            upid = pve.vzdump(
                config.pve.storage_id,
                vmids=vmids,
                all_guests=all_guests,
                mode=config.backup.mode,
                prune_backups=prune,
                bwlimit=config.backup.bwlimit,
            )
            step.detail = upid
            _wait_or_stop(
                pve,
                upid,
                recorder,
                deps,
                StepName.BACKUP,
                "pve",
                _guest_watcher(summary, {g.vmid: g.name for g in guests}),
            )
            # The task exited OK, so every selected guest was backed up whatever the log
            # parse made of it — a vzdump wording change must never report "0/14" on a good
            # run. Recorded only here so a failed run doesn't advertise guests as backed up.
            summary.ok = summary.total
            recorder.run.guests_ok = summary.total


def _preflight_step(config: Config, recorder: RunRecorder, deps: CycleDeps) -> None:
    """Guard before backup: abort if the (now-awake) PBS datastore is below the configured
    free-space floor, so we never start a backup onto a near-full store.

    No-op when ``min_free_percent`` is 0 (the default), so the step only appears when the
    user has opted in. An abort here leaves the PBS on for inspection/cleanup, matching the
    other failure paths.
    """
    threshold = config.backup.min_free_percent
    if threshold <= 0:
        return
    with recorder.step(StepName.PRECHECK) as step:
        with deps.build_pbs(config) as pbs:
            ds = pbs.datastore_status()
        _cache_datastore_stat(config, recorder, ds)
        free = ds.avail_pct
        step.detail = f"{free:.1f}% free ({ds.avail / 1_000_000_000:.0f} GB)"
        if free < threshold:
            raise CycleAbort(
                f"PBS datastore {config.pbs.datastore!r} only {free:.1f}% free "
                f"(need >= {threshold}%); skipping backup"
            )


def run_gc_step(config: Config, recorder: RunRecorder, deps: CycleDeps) -> None:
    """Run garbage collection on the (awake) PBS and wait for it. Shared with the
    manual GC-only job."""
    with recorder.step(StepName.GC) as step:
        with deps.build_pbs(config) as pbs:
            upid = pbs.start_gc()
            step.detail = upid
            _wait_or_stop(pbs, upid, recorder, deps, StepName.GC, "pbs")


def run_verify_step(
    config: Config, recorder: RunRecorder, deps: CycleDeps, *, outdated_after: int | None
) -> None:
    """Verify snapshots on the (awake) PBS and wait for the task.

    ``outdated_after=None`` -> only ever verify never-verified (i.e. new) snapshots — the
    cheap "after each backup" check. An int -> also re-verify snapshots older than that many
    days (0 -> re-verify everything), used by the scheduled full verification.
    """
    with recorder.step(StepName.VERIFY) as step:
        with deps.build_pbs(config) as pbs:
            if outdated_after is not None and outdated_after <= 0:
                upid = pbs.start_verify(ignore_verified=False)
            else:
                upid = pbs.start_verify(ignore_verified=True, outdated_after=outdated_after)
            step.detail = upid
            _wait_or_stop(pbs, upid, recorder, deps, StepName.VERIFY, "pbs")


def _wait_for_pbs(config: Config, recorder: RunRecorder, deps: CycleDeps) -> bool:
    """Wait for the PBS to come up, re-sending Wake-on-LAN between attempts.

    The first magic packet was already sent in the WAKE step. Here we wait up to
    ``wait_timeout`` for the box, and if it still isn't reachable we nudge it again, up
    to ``wol_retries`` more times (a dropped packet or a slow boot shouldn't fail the
    whole cycle). Returns True as soon as PBS answers, False once all attempts are spent.
    """
    p = config.pbs
    attempts = p.wol_retries + 1
    for attempt in range(1, attempts + 1):
        if deps.wait_reachable(config, deps.cancelled):
            return True
        # A cancelled wait returns False like a timeout does, so check *why* before
        # burning the remaining wake attempts on a run the user already stopped.
        if deps.cancelled():
            raise CycleCancelled("Run cancelled while waiting for the PBS")
        if attempt < attempts:
            recorder.log(
                LogLevel.WARN,
                f"PBS still down after wake attempt {attempt}/{attempts} "
                f"({p.wait_timeout}s); re-sending Wake-on-LAN",
            )
            deps.send_wol(config)
    return False


def _read_datastore(
    config: Config, recorder: RunRecorder, deps: CycleDeps
) -> DatastoreStatus | None:
    """Read datastore usage for the success notification, while the PBS is still awake.

    Best-effort: a read failure here must never fail an otherwise-successful cycle, so it
    is logged and swallowed (the notification simply omits the usage line).
    """
    try:
        with deps.build_pbs(config) as pbs:
            ds = pbs.datastore_status()
    except Exception as exc:
        recorder.log(LogLevel.WARN, f"could not read datastore usage: {exc}")
        return None
    recorder.log(LogLevel.INFO, f"PBS datastore {ds.used_pct}% used")
    _cache_datastore_stat(config, recorder, ds)
    return ds


def _cache_datastore_stat(config: Config, recorder: RunRecorder, ds: DatastoreStatus) -> None:
    """Persist the latest datastore usage so the dashboard/UI can show it while the PBS
    sleeps. Best-effort: a cache-write failure must never fail the cycle."""
    try:
        with session_scope() as session:
            upsert_datastore_stat(session, config.pbs.datastore, ds.total, ds.used)
    except Exception as exc:
        recorder.log(LogLevel.WARN, f"could not cache datastore usage: {exc}")


def _refresh_backup_cache(config: Config, recorder: RunRecorder, deps: CycleDeps) -> None:
    """Cache each guest's latest snapshot time while the PBS is awake, so the dashboard can
    show last-backup dates after it sleeps again.

    Best-effort: the PBS is reachable here and the backup already succeeded, so a failure to
    read snapshots or write the cache must never fail the cycle — it's logged and swallowed.
    """
    try:
        with deps.build_pbs(config) as pbs:
            latest = pbs.latest_backups()
        with session_scope() as session:
            upsert_last_backups(session, latest)
    except Exception as exc:
        recorder.log(LogLevel.WARN, f"could not refresh last-backup cache: {exc}")
        return
    recorder.log(LogLevel.INFO, f"cached last-backup times for {len(latest)} guest(s)")


def _poweroff(config: Config, recorder: RunRecorder, deps: CycleDeps) -> None:
    """Power the PBS down — but first wait for any running task to finish so the clean
    shutdown doesn't interrupt it.

    If a task is still running after ``poweroff_task_wait`` we leave the PBS on (the backup
    already succeeded; better to waste a little energy than abort someone else's job). If
    the task check itself errors (e.g. a permission gap), we fail *open* and power off
    anyway with a warning, so a successful backup is never held up by a flaky check.
    """
    if config.pbs.poweroff_task_wait > 0:
        try:
            idle = deps.wait_pbs_idle(config)
        except Exception as exc:
            recorder.log(LogLevel.WARN, f"could not check PBS tasks ({exc}); powering off anyway")
            idle = True
        if not idle:
            recorder.log(
                LogLevel.WARN,
                f"PBS still running a task after {config.pbs.poweroff_task_wait}s; "
                "leaving it on rather than interrupting it",
            )
            recorder.skip_step(StepName.POWEROFF, "PBS busy with another task; left on")
            return

    with recorder.step(StepName.POWEROFF) as step:
        try:
            deps.build_power(config).poweroff()
        except Exception as exc:
            # Best-effort: the backup already succeeded and its data is safe, so a failed
            # power-off must not fail the run. Record the step FAILURE (non-fatal) and warn;
            # the PBS is simply left on (same end state as the "PBS busy" skip above).
            step.status = StepStatus.FAILURE
            step.detail = str(exc)  # surface the reason in the step row, not just the log
            recorder.log(LogLevel.WARN, f"power-off failed, PBS left on: {exc}")


def _finish_cancelled(config: Config, recorder: RunRecorder, deps: CycleDeps) -> None:
    """Close out a run the user stopped: record it ABORTED, and honour the power-off choice
    from the stop dialog.

    Power off only when the user asked *and* the WAIT step actually succeeded — cancelling
    during the wake wait means the box may never have come up, so an SSH power-off would just
    fail and muddy the run with a spurious failed step. ``_poweroff`` waits for the PBS to go
    idle first, which also gives the task we just stopped time to unwind.
    """
    woke = any(
        s.name == StepName.WAIT and s.status == StepStatus.SUCCESS for s in recorder.run.steps
    )
    if deps.cancel_power_off() and woke:
        _poweroff(config, recorder, deps)
    elif woke:
        recorder.skip_step(StepName.POWEROFF, "cancelled; PBS left on")
    recorder.finish(RunStatus.ABORTED, error="Cancelled by user")


def _finish_power(
    config: Config, recorder: RunRecorder, deps: CycleDeps, *, power_off: bool
) -> None:
    """Either power the PBS off (normal energy-saving path) or leave it on by explicit
    request, recording the skip so the run history shows why the box is still awake."""
    if not power_off:
        recorder.skip_step(StepName.POWEROFF, "kept on by request")
        return
    _poweroff(config, recorder, deps)


def run_backup_cycle(
    config: Config, recorder: RunRecorder, deps: CycleDeps, *, power_off: bool = True
) -> None:
    """Execute the full cycle, recording each step. Sets the final run status itself.

    ``power_off=False`` (manual "keep PBS on") runs everything but the final power-off, so a
    PBS the user wants to keep awake (e.g. woken for a restore) is left on."""
    datastore: DatastoreStatus | None = None
    # Owned here, not by the backup step: a guest failing takes the whole vzdump task down
    # and unwinds that frame, and the failed run is exactly the one whose tally we want.
    guests = GuestSummary()
    try:
        with recorder.step(StepName.WAKE):
            deps.send_wol(config)

        with recorder.step(StepName.WAIT):
            if not _wait_for_pbs(config, recorder, deps):
                raise CycleAbort(
                    f"PBS {config.pbs.host}:{config.pbs.port} not reachable after "
                    f"{config.pbs.wol_retries + 1} wake attempt(s) of "
                    f"{config.pbs.wait_timeout}s each"
                )

        _preflight_step(config, recorder, deps)
        _run_backup_step(config, recorder, deps, guests)

        # A cancel that lands between steps must not start the next one — the task waits
        # check the flag themselves, this covers the gaps between them.
        if deps.cancelled():
            raise CycleCancelled("Run cancelled")

        if config.maintenance.gc.enabled:
            run_gc_step(config, recorder, deps)
        else:
            recorder.skip_step(StepName.GC, "GC disabled")

        if deps.cancelled():
            raise CycleCancelled("Run cancelled")

        # Quick verify of just this run's new snapshots, while the PBS is still awake.
        if config.maintenance.verify.after_backup:
            run_verify_step(config, recorder, deps, outdated_after=None)
        else:
            recorder.skip_step(StepName.VERIFY, "verify disabled")

        # Only reached when every prior step succeeded; read usage + refresh the
        # last-backup cache before powering off (both best-effort, PBS still awake).
        datastore = _read_datastore(config, recorder, deps)
        _refresh_backup_cache(config, recorder, deps)
        _finish_power(config, recorder, deps, power_off=power_off)

        recorder.finish(RunStatus.SUCCESS)
    except CycleCancelled:
        # No notification: the user pressed Stop and is standing at the UI — a "backup
        # aborted" push would just be noise about their own click.
        _finish_cancelled(config, recorder, deps)
        return
    except CycleAbort as exc:
        recorder.finish(RunStatus.ABORTED, error=str(exc))
    except Exception as exc:  # connector/task failures: leave PBS on, mark failed
        recorder.finish(RunStatus.FAILURE, error=str(exc))

    _notify_result(config, recorder, deps, datastore, guests)


def run_verify_cycle(
    config: Config, recorder: RunRecorder, deps: CycleDeps, *, power_off: bool = True
) -> None:
    """Scheduled full verification: wake -> verify -> power-off, mirroring the backup cycle
    but verifying existing snapshots instead of creating new ones. The PBS is normally off,
    so this owns its own power cycle. Sets the final run status itself."""
    datastore: DatastoreStatus | None = None
    try:
        with recorder.step(StepName.WAKE):
            deps.send_wol(config)

        with recorder.step(StepName.WAIT):
            if not _wait_for_pbs(config, recorder, deps):
                raise CycleAbort(
                    f"PBS {config.pbs.host}:{config.pbs.port} not reachable after "
                    f"{config.pbs.wol_retries + 1} wake attempt(s) of "
                    f"{config.pbs.wait_timeout}s each"
                )

        run_verify_step(
            config, recorder, deps, outdated_after=config.maintenance.verify.reverify_days
        )

        datastore = _read_datastore(config, recorder, deps)
        _finish_power(config, recorder, deps, power_off=power_off)

        recorder.finish(RunStatus.SUCCESS)
    except CycleCancelled:
        # No notification: the user pressed Stop and is standing at the UI — a "backup
        # aborted" push would just be noise about their own click.
        _finish_cancelled(config, recorder, deps)
        return
    except CycleAbort as exc:
        recorder.finish(RunStatus.ABORTED, error=str(exc))
    except Exception as exc:  # connector/task failures: leave PBS on, mark failed
        recorder.finish(RunStatus.FAILURE, error=str(exc))

    _notify_result(config, recorder, deps, datastore)


def run_gc_cycle(
    config: Config, recorder: RunRecorder, deps: CycleDeps, *, power_off: bool = True
) -> None:
    """Manual garbage collection as a full cycle: wake -> verify reachable -> GC ->
    power-off (unless kept on). The PBS is normally off, so — like the verify cycle — GC
    owns its own power cycle rather than requiring a separately-awake box. Sets the final
    run status itself."""
    datastore: DatastoreStatus | None = None
    try:
        with recorder.step(StepName.WAKE):
            deps.send_wol(config)

        with recorder.step(StepName.WAIT):
            if not _wait_for_pbs(config, recorder, deps):
                raise CycleAbort(
                    f"PBS {config.pbs.host}:{config.pbs.port} not reachable after "
                    f"{config.pbs.wol_retries + 1} wake attempt(s) of "
                    f"{config.pbs.wait_timeout}s each"
                )

        run_gc_step(config, recorder, deps)

        datastore = _read_datastore(config, recorder, deps)
        _finish_power(config, recorder, deps, power_off=power_off)

        recorder.finish(RunStatus.SUCCESS)
    except CycleCancelled:
        # No notification: the user pressed Stop and is standing at the UI — a "backup
        # aborted" push would just be noise about their own click.
        _finish_cancelled(config, recorder, deps)
        return
    except CycleAbort as exc:
        recorder.finish(RunStatus.ABORTED, error=str(exc))
    except Exception as exc:  # connector/task failures: leave PBS on, mark failed
        recorder.finish(RunStatus.FAILURE, error=str(exc))

    _notify_result(config, recorder, deps, datastore)


class _TaskLister(Protocol):
    """The slice of PbsClient that :func:`watch_external_tasks` needs."""

    def active_tasks(self) -> list[dict[str, Any]]: ...


def watch_external_tasks(
    pbs: _TaskLister,
    config: Config,
    *,
    cancelled: Callable[[], bool],
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> int | None:
    """Follow the PBS's own (externally scheduled) tasks until they are done.

    Both knobs are timeouts, not fixed delays. Phase one waits up to ``first_task_wait``
    for the first task to *appear* — polling starts immediately, so a job that starts 90s
    after wake ends the wait at 90s; the full timeout is only ever spent when no job runs
    at all (returns None so the caller can flag it). Phase two then waits for
    ``idle_wait`` seconds of *continuous* silence, restarting the countdown whenever a
    new task shows up, so the gaps between chained jobs (backup -> prune -> GC -> sync)
    never cause an early power-off. Returns the number of distinct tasks observed.
    """
    ext = config.backup.external
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


def run_monitor_cycle(
    config: Config, recorder: RunRecorder, deps: CycleDeps, *, power_off: bool = True
) -> None:
    """External-schedules mode: wake -> wait -> watch the jobs PVE/PBS start on their own
    -> power off after a quiet period. Joulenap starts no backup/GC of its own here — it is
    only the power manager around schedules that live on PVE/PBS (issue #27).

    A watch that sees no task at all is still a SUCCESS run (the wake/power-off worked),
    but it is logged and flagged in the notification so the user learns their external
    schedule didn't fire. Sets the final run status itself."""
    datastore: DatastoreStatus | None = None
    try:
        with recorder.step(StepName.WAKE):
            deps.send_wol(config)

        with recorder.step(StepName.WAIT):
            if not _wait_for_pbs(config, recorder, deps):
                raise CycleAbort(
                    f"PBS {config.pbs.host}:{config.pbs.port} not reachable after "
                    f"{config.pbs.wol_retries + 1} wake attempt(s) of "
                    f"{config.pbs.wait_timeout}s each"
                )

        with recorder.step(StepName.MONITOR) as step:
            with deps.build_pbs(config) as pbs:
                observed = watch_external_tasks(pbs, config, cancelled=deps.cancelled)
            if observed is None:
                step.detail = "no tasks observed"
                recorder.log(
                    LogLevel.WARN,
                    f"no PBS task appeared within {config.backup.external.first_task_wait}s "
                    "— check the schedules on PVE/PBS; powering off",
                )
            else:
                step.detail = f"{observed} task(s) observed"

        # The external jobs (hopefully) wrote new snapshots — refresh the caches the
        # dashboard serves while the PBS sleeps, exactly like the managed cycle does.
        datastore = _read_datastore(config, recorder, deps)
        _refresh_backup_cache(config, recorder, deps)
        _finish_power(config, recorder, deps, power_off=power_off)

        recorder.finish(RunStatus.SUCCESS)
    except CycleCancelled:
        # No notification: the user pressed Stop and is standing at the UI — a "backup
        # aborted" push would just be noise about their own click.
        _finish_cancelled(config, recorder, deps)
        return
    except CycleAbort as exc:
        recorder.finish(RunStatus.ABORTED, error=str(exc))
    except Exception as exc:  # connector failures: leave PBS on, mark failed
        recorder.finish(RunStatus.FAILURE, error=str(exc))

    _notify_result(config, recorder, deps, datastore)


def _notify_result(
    config: Config,
    recorder: RunRecorder,
    deps: CycleDeps,
    datastore: DatastoreStatus | None = None,
    guests: GuestSummary | None = None,
) -> None:
    """Send the result notification. A delivery failure is logged, never fatal — the run
    has already completed and its recorded status must not depend on the notifier."""
    try:
        deps.notify(config, recorder.run, datastore, guests, deps.next_run())
    except Exception as exc:
        recorder.log(LogLevel.WARN, f"notification failed: {exc}")
