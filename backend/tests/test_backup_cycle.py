"""Backup-cycle state machine, driven entirely through connector fakes."""

from __future__ import annotations

from fakes import FakePbs, FakePower, FakePve, make_deps
from sqlalchemy import select

from app.config import Config
from app.connectors.pve import Guest
from app.db import session_scope
from app.db.models import (
    LogEvent,
    LogLevel,
    Run,
    RunKind,
    RunStatus,
    RunStep,
    RunTrigger,
    StepName,
    StepStatus,
)
from app.jobs.backup_cycle import run_backup_cycle
from app.jobs.recorder import RunRecorder


def _config(**overrides) -> Config:
    cfg = Config()
    cfg.pve.storage_id = "pbs"
    cfg.backup.guests.mode = overrides.get("guests_mode", "all")
    cfg.backup.guests.list = overrides.get("guests_list", [])
    cfg.maintenance.gc.enabled = overrides.get("gc_enabled", True)
    return cfg


def _run(config: Config, deps) -> int:
    with RunRecorder(RunKind.CYCLE, RunTrigger.MANUAL) as recorder:
        run_backup_cycle(config, recorder, deps)
        return recorder.run_id


def _load(run_id: int) -> tuple[str, dict[str, str]]:
    """Return (run status, {step name: step status})."""
    with session_scope() as session:
        run = session.get(Run, run_id)
        return run.status, {s.name: s.status for s in run.steps}


def test_success_path_runs_all_steps_and_powers_off(temp_db):
    wol_calls: list[int] = []
    deps, pve, _pbs, power = make_deps(wol=lambda _c: wol_calls.append(1))

    status, steps = _load(_run(_config(), deps))

    assert status == RunStatus.SUCCESS
    assert steps == {
        StepName.WAKE: StepStatus.SUCCESS,
        StepName.WAIT: StepStatus.SUCCESS,
        StepName.BACKUP: StepStatus.SUCCESS,
        StepName.GC: StepStatus.SUCCESS,
        StepName.VERIFY: StepStatus.SKIPPED,  # after_backup verify off by default
        StepName.POWEROFF: StepStatus.SUCCESS,
    }
    assert wol_calls == [1]
    assert power.powered_off is True
    assert pve.vzdump_args["all_guests"] is True


def test_after_backup_verify_runs_when_enabled(temp_db):
    cfg = _config()
    cfg.maintenance.verify.after_backup = True
    pbs = FakePbs()
    deps, _pve, _pbs, _power = make_deps(pbs=pbs)

    status, steps = _load(_run(cfg, deps))

    assert status == RunStatus.SUCCESS
    assert steps[StepName.VERIFY] == StepStatus.SUCCESS
    assert pbs.verify_started is True
    # Quick verify = only never-verified (new) snapshots: no re-verify window.
    assert pbs.verify_args == {"ignore_verified": True, "outdated_after": None}


def test_verify_cycle_wakes_verifies_and_powers_off(temp_db):
    from app.jobs.backup_cycle import run_verify_cycle

    cfg = _config()
    cfg.maintenance.verify.reverify_days = 30
    pbs = FakePbs()
    deps, _pve, _pbs, power = make_deps(pbs=pbs)

    with RunRecorder(RunKind.VERIFY, RunTrigger.SCHEDULED) as recorder:
        run_verify_cycle(cfg, recorder, deps)
        run_id = recorder.run_id

    status, steps = _load(run_id)
    assert status == RunStatus.SUCCESS
    assert steps == {
        StepName.WAKE: StepStatus.SUCCESS,
        StepName.WAIT: StepStatus.SUCCESS,
        StepName.VERIFY: StepStatus.SUCCESS,
        StepName.POWEROFF: StepStatus.SUCCESS,
    }
    assert pbs.verify_args == {"ignore_verified": True, "outdated_after": 30}
    assert power.powered_off is True


def test_cycle_captures_task_log_lines_per_step(temp_db):
    """Backup (PVE), GC (PBS), and VERIFY (PBS) task output is persisted as task_log_lines,
    tagged by the correct step — each source has DISTINCT lines so mis-tagging is detectable."""
    from app.db.models import TaskLogLine

    cfg = _config()
    cfg.maintenance.verify.after_backup = True
    pve = FakePve(log_lines=["INFO: creating vzdump", "VM 100: done"])
    pbs = FakePbs(
        gc_log_lines=["GC starting", "removed 3 chunks"],
        verify_log_lines=["verify group vm/100", "verified OK"],
    )
    deps, _pve, _pbs, _power = make_deps(pve=pve, pbs=pbs)

    run_id = _run(cfg, deps)

    with session_scope() as session:
        rows = session.scalars(
            select(TaskLogLine)
            .where(TaskLogLine.run_id == run_id)
            .order_by(TaskLogLine.id)
        ).all()
        by_step = {}
        for r in rows:
            by_step.setdefault(r.step, []).append((r.source, r.text))

    assert by_step[StepName.BACKUP] == [("pve", "INFO: creating vzdump"), ("pve", "VM 100: done")]
    assert by_step[StepName.GC] == [("pbs", "GC starting"), ("pbs", "removed 3 chunks")]
    assert by_step[StepName.VERIFY] == [("pbs", "verify group vm/100"), ("pbs", "verified OK")]


def test_verify_cycle_full_when_reverify_days_zero(temp_db):
    from app.jobs.backup_cycle import run_verify_cycle

    cfg = _config()
    cfg.maintenance.verify.reverify_days = 0  # force a full re-verify
    pbs = FakePbs()
    deps, _pve, _pbs, _power = make_deps(pbs=pbs)

    with RunRecorder(RunKind.VERIFY, RunTrigger.SCHEDULED) as recorder:
        run_verify_cycle(cfg, recorder, deps)

    assert pbs.verify_args == {"ignore_verified": False, "outdated_after": None}


def test_verify_cycle_aborts_without_power_off_when_pbs_down(temp_db):
    from app.jobs.backup_cycle import run_verify_cycle

    deps, _pve, _pbs, power = make_deps(reachable=False)

    with RunRecorder(RunKind.VERIFY, RunTrigger.SCHEDULED) as recorder:
        run_verify_cycle(_config(), recorder, deps)
        run_id = recorder.run_id

    status, steps = _load(run_id)
    assert status == RunStatus.ABORTED
    assert steps[StepName.WAIT] == StepStatus.FAILURE
    assert StepName.VERIFY not in steps
    assert power.powered_off is False


def test_success_caches_last_backup_times(temp_db):
    from datetime import UTC, datetime

    from app.db.guest_backups import get_last_backups

    pbs = FakePbs(snapshots={100: 1_700_000_000, 101: 1_700_000_500})
    deps, _pve, _pbs, _power = make_deps(pbs=pbs)

    status, _ = _load(_run(_config(), deps))

    assert status == RunStatus.SUCCESS
    with session_scope() as session:
        cached = get_last_backups(session)
    assert cached[100] == datetime.fromtimestamp(1_700_000_000, tz=UTC)
    assert cached[101] == datetime.fromtimestamp(1_700_000_500, tz=UTC)


def test_success_cycle_caches_datastore_stat(temp_db):
    from app.db.datastore_stats import get_datastore_stat

    cfg = _config()
    cfg.pbs.datastore = "backup"
    deps, *_ = make_deps()  # FakePbs defaults: total 8e9, used 2e9

    status, _steps = _load(_run(cfg, deps))
    assert status == RunStatus.SUCCESS

    with session_scope() as s:
        row = get_datastore_stat(s, "backup")
    assert row is not None
    assert row.total == 8_000_000_000
    assert row.used == 2_000_000_000


def test_cache_refresh_failure_does_not_fail_cycle(temp_db):
    # A snapshot-read error during the (best-effort) cache refresh must not fail the run.
    pbs = FakePbs()
    pbs.latest_backups = lambda: (_ for _ in ()).throw(RuntimeError("pbs read failed"))
    deps, _pve, _pbs, power = make_deps(pbs=pbs)

    status, steps = _load(_run(_config(), deps))

    assert status == RunStatus.SUCCESS
    assert steps[StepName.POWEROFF] == StepStatus.SUCCESS
    assert power.powered_off is True


def test_wait_timeout_aborts_without_powering_off(temp_db):
    deps, _pve, _pbs, power = make_deps(reachable=False)

    status, steps = _load(_run(_config(), deps))

    assert status == RunStatus.ABORTED
    assert steps[StepName.WAIT] == StepStatus.FAILURE
    assert StepName.BACKUP not in steps
    assert StepName.POWEROFF not in steps
    assert power.powered_off is False


def test_wake_resends_wol_and_succeeds_on_retry(temp_db):
    # Box is down on the first wait, up on the second -> one extra magic packet, then run.
    reachable = iter([False, True])
    wol_calls: list[int] = []
    deps, _pve, _pbs, power = make_deps(
        reachable=lambda: next(reachable),
        wol=lambda _c: wol_calls.append(1),
    )
    cfg = _config()
    cfg.pbs.wol_retries = 2

    status, steps = _load(_run(cfg, deps))

    assert status == RunStatus.SUCCESS
    assert steps[StepName.WAIT] == StepStatus.SUCCESS
    assert wol_calls == [1, 1]  # initial WAKE + one resend
    assert power.powered_off is True


def test_wake_exhausts_retries_then_aborts(temp_db):
    wol_calls: list[int] = []
    deps, _pve, _pbs, power = make_deps(
        reachable=False,
        wol=lambda _c: wol_calls.append(1),
    )
    cfg = _config()
    cfg.pbs.wol_retries = 2

    status, steps = _load(_run(cfg, deps))

    assert status == RunStatus.ABORTED
    assert steps[StepName.WAIT] == StepStatus.FAILURE
    assert wol_calls == [1, 1, 1]  # initial WAKE + two resends (wol_retries)
    assert StepName.BACKUP not in steps
    assert power.powered_off is False


def test_preflight_passes_when_datastore_has_space(temp_db):
    deps, _pve, _pbs, power = make_deps()  # FakePbs default = 75% free
    cfg = _config()
    cfg.backup.min_free_percent = 50

    status, steps = _load(_run(cfg, deps))

    assert status == RunStatus.SUCCESS
    assert steps[StepName.PRECHECK] == StepStatus.SUCCESS
    assert power.powered_off is True


def test_preflight_aborts_when_datastore_low(temp_db):
    deps, _pve, _pbs, power = make_deps(pbs=FakePbs(avail=200_000_000))  # ~2.5% free
    cfg = _config()
    cfg.backup.min_free_percent = 50

    status, steps = _load(_run(cfg, deps))

    assert status == RunStatus.ABORTED
    assert steps[StepName.PRECHECK] == StepStatus.FAILURE
    assert StepName.BACKUP not in steps
    assert power.powered_off is False


def test_preflight_skipped_when_disabled(temp_db):
    deps, _pve, _pbs, _power = make_deps()
    # min_free_percent defaults to 0 -> no PRECHECK step is recorded at all.
    status, steps = _load(_run(_config(), deps))

    assert status == RunStatus.SUCCESS
    assert StepName.PRECHECK not in steps


def test_backup_failure_leaves_pbs_on(temp_db):
    deps, _pve, _pbs, power = make_deps(pve=FakePve(fail_task=True))

    status, steps = _load(_run(_config(), deps))

    assert status == RunStatus.FAILURE
    assert steps[StepName.BACKUP] == StepStatus.FAILURE
    assert StepName.GC not in steps
    assert StepName.POWEROFF not in steps
    assert power.powered_off is False


def test_gc_disabled_skips_gc_but_still_powers_off(temp_db):
    deps, _pve, pbs, power = make_deps()

    status, steps = _load(_run(_config(gc_enabled=False), deps))

    assert status == RunStatus.SUCCESS
    assert steps[StepName.GC] == StepStatus.SKIPPED
    assert pbs.gc_started is False
    assert power.powered_off is True


def test_gc_failure_leaves_pbs_on(temp_db):
    deps, _pve, _pbs, power = make_deps(pbs=FakePbs(fail_task=True))

    status, steps = _load(_run(_config(), deps))

    assert status == RunStatus.FAILURE
    assert steps[StepName.GC] == StepStatus.FAILURE
    assert StepName.POWEROFF not in steps
    assert power.powered_off is False


def test_poweroff_skipped_when_pbs_busy(temp_db):
    # A task is still running at power-off time -> leave PBS on, but the run still succeeds.
    deps, _pve, _pbs, power = make_deps(pbs_idle=False)

    status, steps = _load(_run(_config(), deps))

    assert status == RunStatus.SUCCESS
    assert steps[StepName.POWEROFF] == StepStatus.SKIPPED
    assert power.powered_off is False


def test_poweroff_proceeds_when_guard_disabled(temp_db):
    # poweroff_task_wait = 0 disables the guard: power off even if a task is running.
    deps, _pve, _pbs, power = make_deps(pbs_idle=False)
    cfg = _config()
    cfg.pbs.poweroff_task_wait = 0

    status, steps = _load(_run(cfg, deps))

    assert status == RunStatus.SUCCESS
    assert steps[StepName.POWEROFF] == StepStatus.SUCCESS
    assert power.powered_off is True


def test_poweroff_fails_open_when_task_check_errors(temp_db):
    # If the running-task check itself fails, don't hold up a successful backup: power off.
    def boom() -> bool:
        raise RuntimeError("audit denied")

    deps, _pve, _pbs, power = make_deps(pbs_idle=boom)

    status, steps = _load(_run(_config(), deps))

    assert status == RunStatus.SUCCESS
    assert steps[StepName.POWEROFF] == StepStatus.SUCCESS
    assert power.powered_off is True


def test_include_mode_passes_explicit_vmids(temp_db):
    deps, pve, _pbs, _power = make_deps()

    _run(_config(guests_mode="include", guests_list=[100, 200]), deps)

    assert pve.vzdump_args["vmids"] == [100, 200]
    assert pve.vzdump_args["all_guests"] is False


def test_backup_records_guest_count(temp_db):
    guests = [
        Guest(vmid=100, name="a", type="lxc", status="running"),
        Guest(vmid=200, name="b", type="qemu", status="running"),
    ]
    deps, _pve, _pbs, _power = make_deps(pve=FakePve(guests=guests))

    run_id = _run(_config(), deps)  # mode=all -> counts every guest on the node

    with session_scope() as session:
        assert session.get(Run, run_id).guests_ok == 2


def test_exclude_mode_filters_listed_vmids(temp_db):
    guests = [
        Guest(vmid=100, name="a", type="lxc", status="running"),
        Guest(vmid=200, name="b", type="qemu", status="running"),
        Guest(vmid=300, name="c", type="qemu", status="running"),
    ]
    deps, pve, _pbs, _power = make_deps(pve=FakePve(guests=guests))

    _run(_config(guests_mode="exclude", guests_list=[200]), deps)

    assert pve.vzdump_args["vmids"] == [100, 300]


def test_include_mode_with_no_guests_aborts(temp_db):
    deps, _pve, _pbs, power = make_deps()

    status, steps = _load(_run(_config(guests_mode="include", guests_list=[]), deps))

    assert status == RunStatus.ABORTED
    assert steps[StepName.BACKUP] == StepStatus.FAILURE
    assert power.powered_off is False


def test_poweroff_failure_is_non_fatal(temp_db):
    # A power-off that raises after a good backup must NOT fail the run: data is safe, the
    # POWEROFF step is recorded FAILURE, a WARN is logged, and the PBS is left on.
    deps, _pve, _pbs, power = make_deps(power=FakePower(fail=True))
    run_id = _run(_config(), deps)

    status, steps = _load(run_id)
    assert status == RunStatus.SUCCESS
    assert steps[StepName.POWEROFF] == StepStatus.FAILURE
    assert power.powered_off is False

    with session_scope() as session:
        logs = session.scalars(select(LogEvent).where(LogEvent.run_id == run_id)).all()
        poweroff = session.scalars(
            select(RunStep).where(
                RunStep.run_id == run_id, RunStep.name == StepName.POWEROFF
            )
        ).one()
        # The failure reason lands in the step row (for the run-history UI), not just the log.
        assert poweroff.detail == "poweroff failed"
    assert any(lg.level == LogLevel.WARN and "power-off failed" in lg.message for lg in logs)


def test_datastore_read_failure_is_best_effort(temp_db):
    """A datastore-usage read failure after a good backup must not fail the cycle; the
    notification simply omits the usage line (datastore=None)."""
    cfg = _config()
    captured = {}

    def capture(config, run, ds=None):
        captured["status"] = run.status
        captured["ds"] = ds

    pbs = FakePbs(fail_datastore=True)
    deps, _pve, _pbs, _power = make_deps(pbs=pbs, notify=capture)

    run_id = _run(cfg, deps)

    with session_scope() as session:
        run = session.get(Run, run_id)
        assert run.status == RunStatus.SUCCESS
    assert captured["status"] == RunStatus.SUCCESS
    assert captured["ds"] is None


def test_failed_cycle_notifies_with_failure_content(temp_db):
    """A failing backup produces a FAILURE run whose notification renders the failure title
    and surfaces the recorded error (only success was asserted before)."""
    from app.notify.messages import _pack, build_run_message

    cfg = _config()
    captured = {}

    def capture(config, run, ds=None):
        captured["run"] = run
        captured["title"], captured["body"] = build_run_message(config, run, ds)

    pve = FakePve(fail_task=True)
    deps, _pve, _pbs, _power = make_deps(pve=pve, notify=capture)

    _run(cfg, deps)

    assert captured["run"].status == RunStatus.FAILURE
    assert captured["run"].error  # the backup failure was recorded
    # Failure title (not the success one); body surfaces the recorded error.
    assert captured["title"] == _pack(cfg.app.language)["failure"]["title"]
    assert captured["run"].error in captured["body"]
