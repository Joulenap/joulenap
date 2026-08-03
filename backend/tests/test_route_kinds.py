"""The v1.0 Sync, External and Verify route cycles, plus the ``run_route`` dispatcher.

Everything runs on the connector fakes — no real PVE/PBS. As with the backup route, the
cycles never touch power: the lease does, which is why the end-to-end cases at the bottom
go through ``JobService.enqueue`` with a ``FakeBox`` per PBS.
"""

from __future__ import annotations

import time

from fakes import FakeBox, FakePbs, FakePve, make_deps
from sqlalchemy import select

from app.config import Config, PbsDevice, PveDevice, Route, RouteSource
from app.connectors.pve import Guest
from app.core.config_store import ConfigStore
from app.db import session_scope
from app.db.models import (
    GuestBackup,
    LogEvent,
    LogLevel,
    Run,
    RunKind,
    RunStatus,
    RunTrigger,
    StepName,
    StepStatus,
    TaskLogLine,
)
from app.jobs.recorder import RunRecorder
from app.jobs.route_cycle import run_route
from app.jobs.service import JobService

GUESTS = [Guest(vmid=100, name="web", type="qemu", status="running", node="pve1")]

# pbs1 is the target of every route here; pbs2 is the sync source. One PVE backs up to
# pbs1, which is what makes an External route's snapshots attributable.
def _config(kind: str, **route_kwargs) -> Config:
    config = Config()
    config.pves = [PveDevice(id="pve1", host="192.0.2.10", storages={"pbs1": "pbs-store"})]
    config.pbss = [
        PbsDevice(id="pbs1", host="192.0.2.20", datastore="backup", mac="00:11:22:33:44:55",
                  api_token_id="root@pam!jn1", api_token_secret="s1", fingerprint="aa:bb"),
        PbsDevice(id="pbs2", host="192.0.2.21", datastore="offsite", mac="00:11:22:33:44:66",
                  api_token_id="root@pam!jn2", api_token_secret="s2", fingerprint="cc:dd"),
    ]
    if kind == "sync":
        route_kwargs.setdefault("source_pbs", "pbs2")
    if kind == "external":
        # The watch polls on real time, so every external test runs with both timeouts at
        # zero: one look for a task, one look for silence, no sleeping.
        config.pbss[0].external.first_task_wait = 0
        config.pbss[0].external.idle_wait = 0
    config.routes = [Route(id="r1", name="R1", kind=kind, target="pbs1", **route_kwargs)]
    return config


def _deps(*, pbs1=None, pbs2=None, pve=None, notify=None, cancelled=None):
    pbs1 = pbs1 or FakePbs()
    pbs2 = pbs2 or FakePbs()
    pve = pve or FakePve(guests=list(GUESTS))
    deps, *_ = make_deps(
        pves={"pve1": pve}, pbss={"pbs1": pbs1, "pbs2": pbs2}, notify=notify
    )
    if cancelled is not None:
        deps.cancelled = cancelled
    return deps, pbs1, pbs2, pve


#: The last context :func:`_run` produced (None when the run was cancelled). The cycle
#: returns it instead of notifying — the service sends it once the leases are released.
_ctx: list = []


def _run(config: Config, deps, kind: RunKind = RunKind.SYNC) -> int:
    route = config.routes[0]
    with RunRecorder(kind, RunTrigger.MANUAL, route_id=route.id, route_name=route.name) as rec:
        _ctx.append(run_route(config, route, rec, deps))
        return rec.run_id


def _load(run_id: int) -> tuple[str, dict[str, str]]:
    """Return (run status, {step name: step status})."""
    with session_scope() as session:
        run = session.get(Run, run_id)
        return run.status, {s.name: s.status for s in run.steps}


def _detail(run_id: int, step: str) -> str | None:
    with session_scope() as session:
        run = session.get(Run, run_id)
        return next(s.detail for s in run.steps if s.name == step)


def _messages(run_id: int, level: str | None = None) -> list[str]:
    with session_scope() as session:
        rows = session.scalars(select(LogEvent).where(LogEvent.run_id == run_id)).all()
        return [r.message for r in rows if level is None or r.level == level]


# --- sync --------------------------------------------------------------------


def test_pull_sync_runs_the_job_on_the_target(temp_db):
    config = _config("sync", sync_direction="pull")
    deps, pbs1, pbs2, _pve = _deps()

    run_id = _run(config, deps)

    # The target pulls: remote + job live on it, pointing at the source.
    assert pbs1.remotes == {
        "joulenap-r1": {
            "host": "192.0.2.21",
            "port": 8007,
            "auth_id": "root@pam!jn2",
            "password": "s2",
            "fingerprint": "cc:dd",
        }
    }
    assert pbs1.sync_jobs == {
        "joulenap-r1": {
            "remote": "joulenap-r1",
            "remote_store": "offsite",  # the peer's datastore
            "store": "backup",  # the executing side's own
            "direction": "pull",
        }
    }
    # The run call carries no direction (PBS resolves the job by id); the direction lives in
    # the job body above, and *which* box ran it is the real proof of the semantics.
    assert pbs1.sync_runs == [{"id": "joulenap-r1"}]
    # The source is never touched by the job — it is only kept awake.
    assert (pbs2.remotes, pbs2.sync_jobs, pbs2.sync_runs) == ({}, {}, [])
    status, steps = _load(run_id)
    assert status == RunStatus.SUCCESS
    assert steps[StepName.SYNC] == StepStatus.SUCCESS
    assert _detail(run_id, StepName.SYNC) == "UPID:pbs:sync"


def test_push_sync_runs_the_job_on_the_source(temp_db):
    config = _config("sync", sync_direction="push")
    deps, pbs1, pbs2, _pve = _deps()

    run_id = _run(config, deps)

    # Mirror image: the source sends, so it executes and its remote is the target.
    assert pbs2.remotes["joulenap-r1"]["host"] == "192.0.2.20"
    assert pbs2.remotes["joulenap-r1"]["auth_id"] == "root@pam!jn1"
    assert pbs2.sync_jobs["joulenap-r1"] == {
        "remote": "joulenap-r1",
        "remote_store": "backup",
        "store": "offsite",
        "direction": "push",
    }
    assert pbs2.sync_runs == [{"id": "joulenap-r1"}]
    assert (pbs1.remotes, pbs1.sync_jobs, pbs1.sync_runs) == ({}, {}, [])
    assert _load(run_id)[0] == RunStatus.SUCCESS


def test_sync_tails_the_task_log(temp_db):
    config = _config("sync", options={"gc": False, "verify_after": False})
    deps, *_ = _deps(pbs1=FakePbs(log_lines=["sync started", "10 snapshots transferred"]))

    run_id = _run(config, deps)

    with session_scope() as session:
        lines = session.scalars(select(TaskLogLine).where(TaskLogLine.run_id == run_id)).all()
    assert [(line.step, line.source, line.text) for line in lines] == [
        (StepName.SYNC, "pbs", "sync started"),
        (StepName.SYNC, "pbs", "10 snapshots transferred"),
    ]


def test_sync_maintenance_runs_on_the_target_only(temp_db):
    # Push: the source executes the job, but GC/verify still belong to the box that
    # received the snapshots.
    config = _config("sync", sync_direction="push", options={"gc": True, "verify_after": True})
    deps, pbs1, pbs2, _pve = _deps()

    run_id = _run(config, deps)

    assert (pbs1.gc_started, pbs1.verify_started) == (True, True)
    assert (pbs2.gc_started, pbs2.verify_started) == (False, False)
    _status, steps = _load(run_id)
    assert steps[StepName.GC] == StepStatus.SUCCESS
    assert steps[StepName.VERIFY] == StepStatus.SUCCESS


def test_sync_without_maintenance_skips_both_steps(temp_db):
    config = _config("sync", options={"gc": False, "verify_after": False})
    deps, pbs1, *_ = _deps()

    run_id = _run(config, deps)

    assert (pbs1.gc_started, pbs1.verify_started) == (False, False)
    _status, steps = _load(run_id)
    assert steps[StepName.GC] == StepStatus.SKIPPED
    assert steps[StepName.VERIFY] == StepStatus.SKIPPED


def test_sync_aborts_when_the_source_device_is_gone(temp_db):
    config = _config("sync")
    config.pbss = [p for p in config.pbss if p.id != "pbs2"]
    deps, pbs1, *_ = _deps()

    run_id = _run(config, deps)

    status, steps = _load(run_id)
    assert status == RunStatus.ABORTED
    assert steps == {}  # nothing started
    assert pbs1.sync_runs == []


def test_a_failed_sync_task_fails_the_run(temp_db):
    config = _config("sync")
    deps, *_ = _deps(pbs1=FakePbs(fail_task=True))

    run_id = _run(config, deps)

    status, steps = _load(run_id)
    assert status == RunStatus.FAILURE
    assert steps[StepName.SYNC] == StepStatus.FAILURE
    assert StepName.GC not in steps  # the failure unwinds before maintenance


def test_cancel_stops_the_running_sync(temp_db):
    config = _config("sync")
    deps, pbs1, *_ = _deps(cancelled=lambda: True)

    run_id = _run(config, deps)

    status, steps = _load(run_id)
    assert status == RunStatus.ABORTED
    assert steps[StepName.SYNC] == StepStatus.FAILURE
    assert pbs1.stopped == ["UPID:pbs:sync"]  # the far side was told to stop


# --- external ----------------------------------------------------------------


def test_external_watches_and_starts_nothing(temp_db):
    config = _config("external", options={"gc": True, "verify_after": True})
    pbs1 = FakePbs(active_tasks_seq=[[{"upid": "A"}, {"upid": "B"}]])
    deps, _pbs1, _pbs2, pve = _deps(pbs1=pbs1)

    run_id = _run(config, deps, kind=RunKind.MONITOR)

    status, steps = _load(run_id)
    assert status == RunStatus.SUCCESS
    assert steps == {StepName.MONITOR: StepStatus.SUCCESS}
    assert _detail(run_id, StepName.MONITOR) == "2 task(s) observed"
    # The whole point of the kind: Joulenap starts nothing of its own, whatever the
    # route's options say.
    assert pve.vzdump_calls == []
    assert (pbs1.gc_started, pbs1.verify_started) == (False, False)


def test_external_uses_the_devices_own_timeouts(temp_db):
    config = _config("external")
    # The route's *target* says "give up immediately" (see _config) while the other box keeps
    # the 900s default. Reading the wrong device would sit here for a quarter of an hour.
    config.pbss[1].external.first_task_wait = 900
    deps, *_ = _deps()

    run_id = _run(config, deps, kind=RunKind.MONITOR)

    status, _steps = _load(run_id)
    assert status == RunStatus.SUCCESS  # a wake that saw nothing is still a good run
    assert _detail(run_id, StepName.MONITOR) == "no tasks observed"
    assert any("within 0s" in m for m in _messages(run_id, LogLevel.WARN))


def test_external_caches_last_backups_for_the_one_mapped_pve(temp_db):
    config = _config("external")
    deps, *_ = _deps(pbs1=FakePbs(snapshots={100: 1_700_000_000, 200: 1_700_000_100}))

    _run(config, deps, kind=RunKind.MONITOR)

    with session_scope() as session:
        rows = session.scalars(select(GuestBackup)).all()
        cached = {(r.pve_id, r.pbs_id, r.vmid) for r in rows}
    # Joulenap chose no guests here, so every snapshot in the datastore is claimed.
    assert cached == {("pve1", "pbs1", 100), ("pve1", "pbs1", 200)}


def test_external_skips_the_cache_when_the_pve_is_ambiguous(temp_db):
    config = _config("external")
    config.pves.append(PveDevice(id="pve2", host="192.0.2.11", storages={"pbs1": "pbs-store"}))
    deps, *_ = _deps(pbs1=FakePbs(snapshots={100: 1_700_000_000}))

    run_id = _run(config, deps, kind=RunKind.MONITOR)

    with session_scope() as session:
        assert session.scalars(select(GuestBackup)).all() == []
    assert any("cannot be attributed" in m for m in _messages(run_id, LogLevel.WARN))


# --- verify ------------------------------------------------------------------


def test_verify_route_reverifies_older_than_the_window(temp_db):
    config = _config("verify", options={"reverify_days": 30})
    deps, pbs1, *_ = _deps()

    run_id = _run(config, deps, kind=RunKind.VERIFY)

    assert pbs1.verify_args == {"ignore_verified": True, "outdated_after": 30}
    status, steps = _load(run_id)
    assert status == RunStatus.SUCCESS
    assert steps == {StepName.VERIFY: StepStatus.SUCCESS}
    assert _detail(run_id, StepName.VERIFY) == "UPID:pbs:verify"


def test_verify_route_with_zero_days_reverifies_everything(temp_db):
    config = _config("verify", options={"reverify_days": 0})
    deps, pbs1, *_ = _deps()

    _run(config, deps, kind=RunKind.VERIFY)

    assert pbs1.verify_args == {"ignore_verified": False, "outdated_after": None}


def test_verify_route_tails_the_task_log(temp_db):
    config = _config("verify")
    deps, *_ = _deps(pbs1=FakePbs(verify_log_lines=["verify ct/100 ok"]))

    run_id = _run(config, deps, kind=RunKind.VERIFY)

    with session_scope() as session:
        lines = session.scalars(select(TaskLogLine).where(TaskLogLine.run_id == run_id)).all()
    assert [(line.step, line.text) for line in lines] == [(StepName.VERIFY, "verify ct/100 ok")]


def test_a_failed_verify_fails_the_run(temp_db):
    config = _config("verify")
    deps, *_ = _deps(pbs1=FakePbs(fail_task=True))

    run_id = _run(config, deps, kind=RunKind.VERIFY)

    status, steps = _load(run_id)
    assert status == RunStatus.FAILURE
    assert steps[StepName.VERIFY] == StepStatus.FAILURE


# --- dispatch ----------------------------------------------------------------


def test_run_route_dispatches_a_backup_route(temp_db):
    config = _config("backup", sources=[RouteSource(pve="pve1")])
    deps, _pbs1, _pbs2, pve = _deps()

    run_id = _run(config, deps, kind=RunKind.CYCLE)

    assert len(pve.vzdump_calls) == 1
    assert _load(run_id)[0] == RunStatus.SUCCESS


def test_an_unknown_kind_aborts_instead_of_raising(temp_db):
    config = _config("verify")
    # Bypasses validation on purpose: config written by hand (or by a future version)
    # must not take the run down with a KeyError.
    config.routes[0] = config.routes[0].model_copy(update={"kind": "teleport"})
    deps, *_ = _deps()

    run_id = _run(config, deps, kind=RunKind.VERIFY)

    status, steps = _load(run_id)
    assert status == RunStatus.ABORTED
    assert steps == {}


def test_a_missing_target_aborts(temp_db):
    config = _config("verify")
    config.pbss = []
    deps, *_ = _deps()

    assert _load(_run(config, deps, kind=RunKind.VERIFY))[0] == RunStatus.ABORTED


def test_the_notification_context_carries_the_target_usage(temp_db):
    config = _config("verify")
    deps, *_ = _deps()

    _run(config, deps, kind=RunKind.VERIFY)

    ctx = _ctx[-1]
    assert ctx is not None
    assert ctx.datastore.total == 8_000_000_000  # the datastore read, for the usage line
    assert ctx.route.id == "r1"  # so the message can name it and honour routes[].notify


# --- end to end through the queue (the lease owns the power) ------------------


def _service(*, fail: bool = False) -> tuple[JobService, FakeBox, dict]:
    store = ConfigStore.load_or_create()
    template = _config("sync")
    store.config.pves = template.pves
    store.config.pbss = template.pbss
    store.config.routes = template.routes
    fakes = {"pbs1": FakePbs(fail_task=fail), "pbs2": FakePbs()}
    deps, *_ = make_deps(pbss=fakes)
    # Both boxes found asleep, each up after its own magic packet (the fake answers one
    # probe at a time, so the pattern covers both wakes) — so each wake is observable.
    box = FakeBox(reachable=[False, True, False, True])
    return JobService(store, deps=deps, lease_deps=box.deps()), box, fakes


def _drain(service: JobService) -> None:
    service.run_route("r1", RunTrigger.MANUAL)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if service.current() is None and not service.pending() and not service.is_running:
            return
        time.sleep(0.01)
    raise AssertionError("queue did not drain")


def test_a_sync_route_wakes_both_boxes_and_powers_both_off(temp_config, temp_db):
    service, box, fakes = _service()

    _drain(service)

    assert sorted(box.wol) == ["pbs1", "pbs2"]
    assert sorted(box.poweroffs) == ["pbs1", "pbs2"]
    assert fakes["pbs1"].sync_runs == [{"id": "joulenap-r1"}]
    with session_scope() as session:
        run = session.scalars(select(Run)).one()
    assert (run.status, run.kind, run.route_id) == (RunStatus.SUCCESS, RunKind.SYNC, "r1")


def test_a_failed_sync_leaves_both_boxes_on(temp_config, temp_db):
    service, box, _fakes = _service(fail=True)

    _drain(service)

    assert sorted(box.wol) == ["pbs1", "pbs2"]
    assert box.poweroffs == []  # left on for inspection, both of them
    with session_scope() as session:
        assert session.scalars(select(Run)).one().status == RunStatus.FAILURE
