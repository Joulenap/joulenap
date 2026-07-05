"""The backup cycle — the heart of Joulenap (ARCHITECTURE.md).

    wake (WoL) -> wait for PBS -> vzdump (selected guests + retention) ->
    [GC, if enabled] -> power-off (only on success) -> record.

On a wait timeout the cycle *aborts* (PBS never came up, nothing to power off). On any
later failure the run is marked failed and the PBS is **left on** for inspection — the
power-off step simply never runs.
"""

from __future__ import annotations

from ..config import Config
from ..connectors.pbs import DatastoreStatus
from ..connectors.pve import PveClient, build_prune_string
from ..db import session_scope
from ..db.guest_backups import upsert_last_backups
from ..db.models import LogLevel, RunStatus, StepName, StepStatus
from .deps import CycleDeps
from .recorder import RunRecorder

# Poll cadence while tailing a task's log — snappier than the plain wait default so the
# live Task-log panel narrates in near-real-time (Proxmox has no push API).
_TAIL_INTERVAL = 2.0


def _tailer(recorder: RunRecorder, step: StepName, source: str):
    """A ``wait_task(on_log=...)`` callback that persists each task-log batch.

    Best-effort: a failure to store a log line must never fail an otherwise-fine backup,
    so it's swallowed with a warning (the narration just misses a line).
    """

    def on_log(lines: list[tuple[int, str]]) -> None:
        try:
            recorder.task_log(step, source, lines)
        except Exception as exc:  # pragma: no cover - defensive
            recorder.log(LogLevel.WARN, f"could not store task-log line(s): {exc}")

    return on_log


class CycleAbort(Exception):
    """Raised when the PBS doesn't come up — the cycle aborts without powering off."""


def select_vmids(config: Config, pve: PveClient) -> tuple[list[int] | None, bool]:
    """Resolve the configured guest selection into vzdump arguments.

    Returns ``(vmids, all_guests)``: for ``mode=all`` -> ``(None, True)``; otherwise an
    explicit id list. ``exclude`` is materialised by listing the node's guests and
    dropping the excluded ids.
    """
    guests = config.backup.guests
    if guests.mode == "all":
        return None, True
    if guests.mode == "include":
        return list(guests.list), False
    excluded = set(guests.list)
    vmids = [g.vmid for g in pve.list_guests() if g.vmid not in excluded]
    return vmids, False


def _run_backup_step(config: Config, recorder: RunRecorder, deps: CycleDeps) -> None:
    with recorder.step(StepName.BACKUP) as step:
        with deps.build_pve(config) as pve:
            vmids, all_guests = select_vmids(config, pve)
            if not all_guests and not vmids:
                raise CycleAbort("No guests selected for backup")
            guest_count = len(pve.list_guests()) if all_guests else len(vmids)
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
            pve.wait_task(
                upid,
                poll_interval=_TAIL_INTERVAL,
                on_log=_tailer(recorder, StepName.BACKUP, "pve"),
            )
            # Record the count only once the task succeeded, so a failed run doesn't
            # advertise guests as backed up.
            recorder.run.guests_ok = guest_count


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
            pbs.wait_task(
                upid,
                poll_interval=_TAIL_INTERVAL,
                on_log=_tailer(recorder, StepName.GC, "pbs"),
            )


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
            pbs.wait_task(
                upid,
                poll_interval=_TAIL_INTERVAL,
                on_log=_tailer(recorder, StepName.VERIFY, "pbs"),
            )


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
        if deps.wait_reachable(config):
            return True
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
    return ds


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


def run_backup_cycle(config: Config, recorder: RunRecorder, deps: CycleDeps) -> None:
    """Execute the full cycle, recording each step. Sets the final run status itself."""
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

        _preflight_step(config, recorder, deps)
        _run_backup_step(config, recorder, deps)

        if config.maintenance.gc.enabled:
            run_gc_step(config, recorder, deps)
        else:
            recorder.skip_step(StepName.GC, "GC disabled")

        # Quick verify of just this run's new snapshots, while the PBS is still awake.
        if config.maintenance.verify.after_backup:
            run_verify_step(config, recorder, deps, outdated_after=None)
        else:
            recorder.skip_step(StepName.VERIFY, "verify disabled")

        # Only reached when every prior step succeeded; read usage + refresh the
        # last-backup cache before powering off (both best-effort, PBS still awake).
        datastore = _read_datastore(config, recorder, deps)
        _refresh_backup_cache(config, recorder, deps)
        _poweroff(config, recorder, deps)

        recorder.finish(RunStatus.SUCCESS)
    except CycleAbort as exc:
        recorder.finish(RunStatus.ABORTED, error=str(exc))
    except Exception as exc:  # connector/task failures: leave PBS on, mark failed
        recorder.finish(RunStatus.FAILURE, error=str(exc))

    _notify_result(config, recorder, deps, datastore)


def run_verify_cycle(config: Config, recorder: RunRecorder, deps: CycleDeps) -> None:
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
        _poweroff(config, recorder, deps)

        recorder.finish(RunStatus.SUCCESS)
    except CycleAbort as exc:
        recorder.finish(RunStatus.ABORTED, error=str(exc))
    except Exception as exc:  # connector/task failures: leave PBS on, mark failed
        recorder.finish(RunStatus.FAILURE, error=str(exc))

    _notify_result(config, recorder, deps, datastore)


def _notify_result(
    config: Config,
    recorder: RunRecorder,
    deps: CycleDeps,
    datastore: DatastoreStatus | None = None,
) -> None:
    """Send the result notification. A delivery failure is logged, never fatal — the run
    has already completed and its recorded status must not depend on the notifier."""
    try:
        deps.notify(config, recorder.run, datastore)
    except Exception as exc:
        recorder.log(LogLevel.WARN, f"notification failed: {exc}")
