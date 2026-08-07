"""The v1.0 backup route cycle: several PVE sources (one a cluster) onto one PBS target.

Everything here runs through the connector fakes — no real PVE/PBS — and the route cycle
itself never touches power: the lease does (see test_lease/test_queue), which is why the
end-to-end cases at the bottom go through ``JobService.enqueue``.
"""

from __future__ import annotations

import time

from fakes import FakeBox, FakePbs, FakePve, UnreachablePve, make_deps
from sqlalchemy import select

from app.config import Config, PbsDevice, PveDevice, Route, RouteGuests, RouteSource
from app.connectors.pve import Guest
from app.core.config_store import ConfigStore
from app.db import session_scope
from app.db.models import (
    DatastoreStat,
    GuestBackup,
    LogEvent,
    LogLevel,
    Run,
    RunKind,
    RunStatus,
    RunTrigger,
)
from app.jobs.backup_cycle import run_route_backup
from app.jobs.recorder import RunRecorder
from app.jobs.service import JobService

# pve-alpha is a cluster: four guests spread over three nodes. pve-beta is a standalone
# node. Both back up to the same PBS, each through its own storage name.
ALPHA_GUESTS = [
    Guest(vmid=100, name="web", type="qemu", status="running", node="n1"),
    Guest(vmid=101, name="db", type="lxc", status="running", node="n1"),
    Guest(vmid=200, name="mail", type="qemu", status="running", node="n2"),
    Guest(vmid=300, name="dns", type="lxc", status="stopped", node="n3"),
]
BETA_GUESTS = [Guest(vmid=500, name="nas", type="qemu", status="running", node="beta")]


def _config(**route_kwargs) -> Config:
    config = Config()
    config.pves = [
        PveDevice(id="pve-alpha", host="192.0.2.10", storages={"pbs1": "pbs-alpha"}),
        PveDevice(id="pve-beta", host="192.0.2.11", storages={"pbs1": "pbs-beta"}),
    ]
    config.pbss = [
        PbsDevice(id="pbs1", host="192.0.2.20", datastore="backup", mac="00:11:22:33:44:55")
    ]
    sources = route_kwargs.pop(
        "sources", [RouteSource(pve="pve-alpha"), RouteSource(pve="pve-beta")]
    )
    config.routes = [
        Route(id="nightly", name="Nightly", kind="backup", target="pbs1", sources=sources,
              **route_kwargs)
    ]
    return config


def _deps(*, alpha=None, beta=None, pbs=None, notify=None, cancelled=None):
    alpha = alpha if alpha is not None else FakePve(guests=list(ALPHA_GUESTS))
    beta = beta if beta is not None else FakePve(guests=list(BETA_GUESTS))
    pbs = pbs or FakePbs()
    deps, *_ = make_deps(
        pves={"pve-alpha": alpha, "pve-beta": beta},
        pbss={"pbs1": pbs},
        notify=notify,
    )
    if cancelled is not None:
        deps.cancelled = cancelled
    return deps, alpha, beta, pbs


def _run(config: Config, deps, route_id: str = "nightly") -> int:
    """Run the cycle directly (no queue, no lease) and return the run id.

    The cycle returns the notification context instead of sending it — the service does
    that once the leases are released — so ``_ctx`` catches what a notification *would*
    have said; :func:`_summary` is the one test that looks at it.
    """
    route = next(r for r in config.routes if r.id == route_id)
    with RunRecorder(
        RunKind.CYCLE, RunTrigger.MANUAL, route_id=route.id, route_name=route.name
    ) as recorder:
        _ctx.append(run_route_backup(config, route, recorder, deps))
        return recorder.run_id


#: The last context :func:`_run` produced (None when the run was cancelled).
_ctx: list = []


def _load(run_id: int) -> tuple[str, dict[str, str]]:
    """Return (run status, {step name: step status})."""
    with session_scope() as session:
        run = session.get(Run, run_id)
        return run.status, {s.name: s.status for s in run.steps}


def _logs(run_id: int, level: LogLevel | None = None) -> list[str]:
    with session_scope() as session:
        rows = session.scalars(select(LogEvent).where(LogEvent.run_id == run_id)).all()
        return [r.message for r in rows if level is None or r.level == level]


# --- per-node vzdump grouping ------------------------------------------------


def test_cluster_and_standalone_each_get_one_vzdump_per_node(temp_db):
    config = _config()
    deps, alpha, beta, _pbs = _deps()

    status, steps = _load(_run(config, deps))

    assert status == RunStatus.SUCCESS
    # The cluster's three nodes, each once; the standalone node once.
    assert [c["node"] for c in alpha.vzdump_calls] == ["n1", "n2", "n3"]
    assert [c["node"] for c in beta.vzdump_calls] == ["beta"]
    # Each PVE names the same PBS its own way.
    assert {c["storage"] for c in alpha.vzdump_calls} == {"pbs-alpha"}
    assert beta.vzdump_calls[0]["storage"] == "pbs-beta"
    assert steps["backup:pve-alpha"] == "success"
    assert steps["backup:pve-beta"] == "success"


def test_all_mode_uses_vzdumps_own_all_flag(temp_db):
    config = _config()
    deps, alpha, _beta, _pbs = _deps()

    _run(config, deps)

    # Not the vmids we just listed: PVE keeps deciding, so a guest excluded from backup on
    # the host is still honoured.
    assert all(c["all_guests"] is True and c["vmids"] is None for c in alpha.vzdump_calls)


def test_include_mode_groups_the_selection_per_node(temp_db):
    config = _config(
        sources=[
            RouteSource(pve="pve-alpha", guests=RouteGuests(mode="include", list=[100, 300])),
            RouteSource(pve="pve-beta"),
        ]
    )
    deps, alpha, _beta, _pbs = _deps()

    status, _steps = _load(_run(config, deps))

    assert status == RunStatus.SUCCESS
    # n2 holds nothing selected, so it is never woken up with a task.
    assert [(c["node"], c["vmids"]) for c in alpha.vzdump_calls] == [("n1", [100]), ("n3", [300])]
    assert all(c["all_guests"] is False for c in alpha.vzdump_calls)


def test_route_options_and_retention_drive_the_vzdump_arguments(temp_db):
    config = _config()
    route = config.routes[0]
    route.options.mode = "stop"
    route.options.bwlimit = 50_000
    route.retention.keep_last = 3
    route.retention.keep_daily = 0
    route.retention.keep_weekly = 0
    route.retention.keep_monthly = 0
    deps, alpha, _beta, _pbs = _deps()

    _run(config, deps)

    call = alpha.vzdump_calls[0]
    assert call["mode"] == "stop"
    assert call["bwlimit"] == 50_000
    assert call["prune_backups"] == "keep-last=3"


def test_selected_guest_that_is_gone_is_warned_about_and_skipped(temp_db):
    config = _config(
        sources=[RouteSource(pve="pve-alpha", guests=RouteGuests(mode="include", list=[100, 999]))]
    )
    deps, alpha, _beta, _pbs = _deps()

    run_id = _run(config, deps)

    status, _steps = _load(run_id)
    assert status == RunStatus.SUCCESS
    assert [c["vmids"] for c in alpha.vzdump_calls] == [[100]]
    assert any("999" in m for m in _logs(run_id, LogLevel.WARN))


def test_source_left_with_no_guest_fails_that_source(temp_db):
    config = _config(
        sources=[RouteSource(pve="pve-alpha", guests=RouteGuests(mode="include", list=[999]))]
    )
    deps, alpha, _beta, _pbs = _deps()

    status, steps = _load(_run(config, deps))

    assert status == RunStatus.FAILURE
    assert steps["backup:pve-alpha"] == "failure"
    assert alpha.vzdump_calls == []


# --- per-source failure isolation --------------------------------------------


def test_one_broken_source_still_lets_the_others_run(temp_db):
    config = _config()
    deps, alpha, _beta, _pbs = _deps(beta=UnreachablePve())

    run_id = _run(config, deps)
    status, steps = _load(run_id)

    assert status == RunStatus.FAILURE
    assert steps["backup:pve-alpha"] == "success"
    assert steps["backup:pve-beta"] == "failure"
    assert len(alpha.vzdump_calls) == 3  # the healthy source got its full backup
    with session_scope() as session:
        assert "pve-beta" in session.get(Run, run_id).error


def test_a_failing_node_task_fails_only_its_source(temp_db):
    config = _config()
    deps, _alpha, _beta, _pbs = _deps(beta=FakePve(guests=list(BETA_GUESTS), fail_task=True))

    status, steps = _load(_run(config, deps))

    assert status == RunStatus.FAILURE
    assert steps["backup:pve-alpha"] == "success"
    assert steps["backup:pve-beta"] == "failure"
    assert steps["gc"] == "success"  # the PBS is awake and the other source's data is real


# --- guest tally --------------------------------------------------------------


def _summary(config, deps_kwargs=None):
    seen: dict = {}

    deps, alpha, beta, pbs = _deps(**(deps_kwargs or {}))
    _ctx.clear()
    _run(config, deps)
    ctx = _ctx[-1]
    seen["datastore"] = ctx.datastore
    seen["guests"] = ctx.guests
    return seen, alpha, beta, pbs


def test_guest_tally_aggregates_across_sources(temp_db):
    seen, _alpha, _beta, _pbs = _summary(_config())

    assert seen["guests"].total == 5  # 4 on the cluster + 1 standalone
    assert seen["guests"].ok == 5
    assert seen["guests"].failed == []


def test_failed_guests_are_named_across_sources(temp_db):
    alpha = FakePve(
        guests=list(ALPHA_GUESTS),
        fail_task=True,
        log_lines=["INFO: Finished Backup of VM 100", "ERROR: Backup of VM 101 failed - boom"],
    )
    seen, _alpha, _beta, _pbs = _summary(_config(), {"alpha": alpha})

    assert seen["guests"].failed == ["db"]  # the guest's name, not its vmid
    # 100 from the failed task's own log (the tally survives the raise) + pve-beta's guest,
    # which still ran because sources are isolated.
    assert seen["guests"].ok == 2


# --- GC / verify / preflight --------------------------------------------------


def test_gc_runs_once_per_route(temp_db):
    config = _config()
    deps, _alpha, _beta, pbs = _deps()

    _load(_run(config, deps))

    assert pbs.gc_started is True


def test_gc_disabled_skips_the_step(temp_db):
    config = _config()
    config.routes[0].options.gc = False
    deps, _alpha, _beta, pbs = _deps()

    status, steps = _load(_run(config, deps))

    assert status == RunStatus.SUCCESS
    assert steps["gc"] == "skipped"
    assert pbs.gc_started is False


def test_verify_after_runs_only_the_new_snapshots(temp_db):
    config = _config()
    config.routes[0].options.verify_after = True
    deps, _alpha, _beta, pbs = _deps()

    status, steps = _load(_run(config, deps))

    assert status == RunStatus.SUCCESS
    assert steps["verify"] == "success"
    assert pbs.verify_args == {"ignore_verified": True, "outdated_after": None}


def test_verify_is_skipped_by_default(temp_db):
    _status, steps = _load(_run(_config(), _deps()[0]))

    assert steps["verify"] == "skipped"


def test_preflight_aborts_before_any_backup_when_the_datastore_is_full(temp_db):
    config = _config()
    config.routes[0].options.min_free_percent = 50
    deps, alpha, _beta, _pbs = _deps(
        pbs=FakePbs(total=8_000_000_000, used=7_000_000_000, avail=1_000_000_000)
    )

    status, steps = _load(_run(config, deps))

    assert status == RunStatus.ABORTED
    assert steps["precheck"] == "failure"
    assert alpha.vzdump_calls == []


def test_preflight_passes_when_there_is_room(temp_db):
    config = _config()
    config.routes[0].options.min_free_percent = 50
    deps, alpha, _beta, _pbs = _deps()

    status, steps = _load(_run(config, deps))

    assert status == RunStatus.SUCCESS
    assert steps["precheck"] == "success"
    assert len(alpha.vzdump_calls) == 3


def test_no_precheck_step_when_the_guard_is_off(temp_db):
    _status, steps = _load(_run(_config(), _deps()[0]))

    assert "precheck" not in steps


# --- caches -------------------------------------------------------------------


def test_last_backup_cache_is_attributed_to_each_source(temp_db):
    config = _config()
    # The datastore holds snapshots for guests of both PVEs; each source claims its own.
    deps, _alpha, _beta, _pbs = _deps(
        pbs=FakePbs(snapshots={100: 1_700_000_000, 500: 1_700_000_100})
    )

    _run(config, deps)

    with session_scope() as session:
        rows = {(r.pve_id, r.vmid, r.pbs_id) for r in session.scalars(select(GuestBackup))}
    assert rows == {("pve-alpha", 100, "pbs1"), ("pve-beta", 500, "pbs1")}


def test_datastore_stat_is_keyed_by_the_target_pbs(temp_db):
    config = _config()
    deps, _alpha, _beta, _pbs = _deps()

    _run(config, deps)

    with session_scope() as session:
        row = session.get(DatastoreStat, ("pbs1", "backup"))
    assert row is not None
    assert (row.total, row.used) == (8_000_000_000, 2_000_000_000)


def test_a_cache_failure_never_fails_the_run(temp_db):
    config = _config()

    class NoSnapshots(FakePbs):
        def latest_backups(self):
            raise RuntimeError("snapshot listing exploded")

    deps, _alpha, _beta, _pbs = _deps(pbs=NoSnapshots())

    status, _steps = _load(_run(config, deps))

    assert status == RunStatus.SUCCESS


# --- cancellation -------------------------------------------------------------


def test_cancel_stops_the_running_vzdump_and_aborts(temp_db):
    config = _config()
    notified: list[object] = []
    # False at the between-sources check, then True from inside the first task's wait.
    answers = iter([False])
    deps, alpha, beta, _pbs = _deps(cancelled=lambda: next(answers, True))
    _ctx.clear()

    status, steps = _load(_run(config, deps))
    notified[:] = [c for c in _ctx if c is not None]

    assert status == RunStatus.ABORTED
    assert alpha.stopped == ["UPID:n1:backup"]  # the task it had actually started
    assert steps["backup:pve-alpha"] == "failure"
    assert "backup:pve-beta" not in steps  # a cancel stops the route, not just one source
    assert beta.vzdump_calls == []
    assert notified == []  # the user is standing at the UI; no push about their own click


def test_cancel_before_the_first_source_starts_nothing(temp_db):
    config = _config()
    deps, alpha, _beta, _pbs = _deps(cancelled=lambda: True)

    status, steps = _load(_run(config, deps))

    assert status == RunStatus.ABORTED
    assert alpha.vzdump_calls == []
    assert steps == {}


# --- end to end through the queue (the lease owns the power) ------------------


def _service(config_setup) -> tuple[JobService, FakeBox, dict]:
    store = ConfigStore.load_or_create()
    template = _config()
    store.config.pves = template.pves
    store.config.pbss = template.pbss
    store.config.routes = template.routes
    fakes = {"alpha": FakePve(guests=list(ALPHA_GUESTS)), "beta": config_setup}
    deps, *_ = make_deps(
        pves={"pve-alpha": fakes["alpha"], "pve-beta": fakes["beta"]},
        pbss={"pbs1": FakePbs()},
    )
    # Found asleep, up after the first magic packet — so the wake is observable.
    box = FakeBox(reachable=[False, True])
    return JobService(store, deps=deps, lease_deps=box.deps()), box, fakes


def _enqueue_and_drain(service: JobService) -> None:
    service.run_route("nightly", RunTrigger.MANUAL)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if service.current() is None and not service.pending() and not service.is_running:
            return
        time.sleep(0.01)
    raise AssertionError("queue did not drain")


def test_a_route_run_wakes_the_target_once_and_powers_it_off_once(temp_config, temp_db):
    service, box, _fakes = _service(FakePve(guests=list(BETA_GUESTS)))

    _enqueue_and_drain(service)

    assert box.wol == ["pbs1"]
    assert box.poweroffs == ["pbs1"]
    with session_scope() as session:
        run = session.scalars(select(Run)).one()
    assert run.status == RunStatus.SUCCESS
    assert run.route_id == "nightly"


def test_a_failed_source_leaves_the_pbs_on(temp_config, temp_db):
    service, box, fakes = _service(UnreachablePve())

    _enqueue_and_drain(service)

    assert box.wol == ["pbs1"]
    assert box.poweroffs == []  # left on for inspection
    assert len(fakes["alpha"].vzdump_calls) == 3
    with session_scope() as session:
        assert session.scalars(select(Run)).one().status == RunStatus.FAILURE
