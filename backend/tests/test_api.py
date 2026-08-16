"""REST routers: status, config, scheduler toggle, guests, runs/logs, account, dashboard.

Route and device CRUD live in test_api_routes.py / test_api_devices.py.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakeBox, FakePve, UnreachablePve, make_deps
from fastapi.testclient import TestClient

from app import config
from app.config import load_config
from app.connectors.pve import Guest
from app.db import session_scope
from app.db.datastore_stats import get_datastore_stat, upsert_datastore_stat
from app.db.models import Run, RunKind, RunStatus, RunStep, RunTrigger, StepStatus
from app.jobs import JobService
from app.main import create_app


@pytest.fixture
def app_ctx(temp_config, temp_db, monkeypatch):
    """Authenticated TestClient + app. Device reachability is stubbed off by default so
    status tests don't touch the network; inject fake job deps per test."""
    monkeypatch.setattr("app.connectors.net.tcp_reachable", lambda *a, **k: False)
    app = create_app()
    with TestClient(app) as client:
        client.post("/api/auth/setup", json={"username": "admin", "password": "secret12"})
        yield client, app


def _inject(app, **deps_kwargs):
    """Swap app.state.job_service for one wired to in-memory connector fakes.

    The power lease gets a FakeBox too: without it a queued run would send real magic
    packets and then sit through the wake timeout.
    """
    deps, pve, pbs = make_deps(**deps_kwargs)
    app.state.job_service = JobService(
        app.state.config_store, deps=deps, lease_deps=FakeBox().deps()
    )
    return pve, pbs


def _wait_run(client, run_id, *, timeout=5.0):
    """Poll a run until it leaves RUNNING (background thread), return its final body."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/runs/{run_id}").json()
        if body["status"] != "running":
            return body
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} did not finish in {timeout}s")


# --- auth guard --------------------------------------------------------------


def test_protected_endpoints_require_auth(temp_config, temp_db):
    with TestClient(create_app()) as client:
        for path in ("/api/status", "/api/config", "/api/guests", "/api/runs",
                     "/api/logs", "/api/tasklog", "/api/routes", "/api/devices"):
            assert client.get(path).status_code == 401, path
        for path in ("/api/routes", "/api/routes/nightly/run", "/api/runs/1/stop",
                     "/api/devices/pbss", "/api/devices/pbss/pbs-01/power",
                     "/api/devices/pbss/pbs-01/gc", "/api/devices/pbss/pbs-01/test",
                     "/api/notify/test", "/api/scheduler/toggle", "/api/wizard/wol/test"):
            assert client.post(path).status_code == 401, path


def test_login_locks_out_after_repeated_failures(app_ctx):
    client, _app = app_ctx
    for _ in range(5):
        r = client.post("/api/login", json={"username": "admin", "password": "wrong-xxxx"})
        assert r.status_code == 401
    r = client.post("/api/login", json={"username": "admin", "password": "wrong-xxxx"})
    assert r.status_code == 429


# --- status ------------------------------------------------------------------


def test_status_shape(app_ctx):
    client, _app = app_ctx
    body = client.get("/api/status").json()
    assert body["state"] == "idle"
    assert body["scheduler_enabled"] is True
    assert body["running"] is None and body["queued"] == []
    assert body["last_run"] is None
    # One entry per configured device, for the topology.
    assert [d["id"] for d in body["pves"]] == ["pve-alpha", "pve-beta"]
    assert [d["id"] for d in body["pbss"]] == ["pbs-01", "pbs-02"]
    assert all(d["online"] is False for d in body["pbss"])  # stubbed unreachable


def test_status_lists_the_next_run_of_every_armed_route(app_ctx):
    client, _app = app_ctx
    body = client.get("/api/status").json()
    assert {r["route_id"] for r in body["next_runs"]} == {"nightly", "lab", "offsite"}
    assert body["next_runs"][0]["route_name"]  # named, so the rail needs no second call
    # Soonest first: the rail renders them in order.
    times = [r["at"] for r in body["next_runs"]]
    assert times == sorted(times)


def test_status_reports_a_refused_config_migration(app_ctx, monkeypatch):
    """An empty config looks exactly like a fresh install, so the UI has to be told why."""
    client, _app = app_ctx
    assert client.get("/api/status").json()["config_error"] is None
    monkeypatch.setattr(config, "MIGRATION_ERROR", "routes.0.options.bwlimit: bad")
    assert client.get("/api/status").json()["config_error"] == "routes.0.options.bwlimit: bad"


def test_status_pill_says_paused_when_the_kill_switch_is_off(app_ctx):
    client, app = app_ctx
    app.state.config_store.update(lambda c: setattr(c.app, "scheduler_enabled", False))
    body = client.get("/api/status").json()
    assert body["state"] == "paused"
    assert body["scheduler_enabled"] is False


def test_status_running_names_the_route_in_flight(app_ctx):
    """The pill says "Running · <route>", so the run row has to carry which one."""
    client, _app = app_ctx
    with session_scope() as s:
        s.add(
            Run(
                kind=RunKind.CYCLE,
                trigger=RunTrigger.SCHEDULED,
                status=RunStatus.RUNNING,
                started_at=datetime.now(UTC),
                route_id="nightly",
                route_name="Nightly",
            )
        )

    body = client.get("/api/status").json()
    assert body["state"] == "running"
    assert body["running"]["route_name"] == "Nightly"
    assert body["running"]["kind"] == "cycle"


def test_status_reports_the_queue(app_ctx):
    client, app = app_ctx
    _inject(app)
    service = app.state.job_service
    # Park a run so a second one stays queued behind it.
    import threading

    from app.db.models import RunTrigger as _T
    from app.jobs.service import QueuedRun

    release = threading.Event()
    started = threading.Event()

    def parked(_c, _s, recorder, _d):
        started.set()
        release.wait(timeout=5)
        recorder.finish(RunStatus.SUCCESS)

    service.enqueue(
        QueuedRun(key="nightly", route_id="nightly", trigger=_T.MANUAL,
                  kind=RunKind.CYCLE, job=parked)
    )
    assert started.wait(timeout=5)
    try:
        service.run_route("offsite")
        body = client.get("/api/status").json()
        assert [q["key"] for q in body["queued"]] == ["offsite"]
        assert body["pbss"][0]["holders"] == 1  # the lease the running route holds
    finally:
        release.set()


# --- config ------------------------------------------------------------------


def test_config_get_redacts_secrets(app_ctx):
    client, _app = app_ctx
    cfg = client.get("/api/config").json()
    assert cfg["pves"][0]["api_token_secret"] == "***REDACTED***"
    assert cfg["app"]["secret_key"] == "***REDACTED***"


def test_config_put_preserves_redacted_secrets_and_rearms(app_ctx, temp_config):
    client, _app = app_ctx
    cfg = client.get("/api/config").json()
    cfg["routes"][0]["schedule"]["time"] = "03:30"  # non-secret; secrets stay REDACTED

    r = client.put("/api/config", json=cfg)
    assert r.status_code == 200

    on_disk = load_config(temp_config)
    assert on_disk.routes[0].schedule.time == "03:30"
    # The real secret survived the round-trip rather than being overwritten with REDACTED.
    assert on_disk.pves[0].api_token_secret == "test-pve-secret"
    assert on_disk.app.secret_key not in ("", "***REDACTED***")


def test_config_put_sets_new_secret(app_ctx, temp_config):
    client, _app = app_ctx
    cfg = client.get("/api/config").json()
    cfg["pves"][0]["api_token_secret"] = "brand-new-secret"

    assert client.put("/api/config", json=cfg).status_code == 200
    assert load_config(temp_config).pves[0].api_token_secret == "brand-new-secret"


def test_config_put_rejects_invalid(app_ctx):
    client, _app = app_ctx
    cfg = client.get("/api/config").json()
    cfg["routes"][0]["options"]["mode"] = "not-a-mode"
    assert client.put("/api/config", json=cfg).status_code == 422


def test_config_put_rejects_an_invalid_route_cron(app_ctx, temp_config):
    # A newly-set unparseable cron must 422 before persisting, not leave the route silently
    # unarmed on every restart (BE-B1).
    client, _app = app_ctx
    cfg = client.get("/api/config").json()
    cfg["routes"][0]["schedule"]["cron"] = "0 4 * *"  # 4 fields
    r = client.put("/api/config", json={"routes": cfg["routes"]})
    assert r.status_code == 422
    # Nothing was written: the route on disk is untouched.
    assert load_config(temp_config).routes[0].schedule.cron == ""


def test_config_put_rejects_invalid_mac(app_ctx, temp_config):
    # A newly-set malformed WoL MAC must 422 before persisting, not fail silently at wake
    # time (BE-C2). "00:11:22:33:44" is only 5 octets.
    client, _app = app_ctx
    before = load_config(temp_config).pbss[0].mac
    cfg = client.get("/api/config").json()
    cfg["pbss"][0]["mac"] = "00:11:22:33:44"
    r = client.put("/api/config", json={"pbss": cfg["pbss"]})
    assert r.status_code == 422
    assert "pbs-01" in str(r.json()["detail"])  # says which device
    assert load_config(temp_config).pbss[0].mac == before  # nothing written


def test_config_put_accepts_valid_mac(app_ctx, temp_config):
    client, _app = app_ctx
    cfg = client.get("/api/config").json()
    cfg["pbss"][0]["mac"] = "aa-bb-cc-dd-ee-ff"
    r = client.put("/api/config", json={"pbss": cfg["pbss"]})
    assert r.status_code == 200
    assert load_config(temp_config).pbss[0].mac == "aa-bb-cc-dd-ee-ff"


def test_config_put_partial_body_preserves_secrets(app_ctx, temp_config):
    client, _app = app_ctx
    before = load_config(temp_config)
    # A partial body (only the history window) must not reset anything else.
    r = client.put("/api/config", json={"maintenance": {"history": {"retention_days": 30}}})
    assert r.status_code == 200
    after = load_config(temp_config)
    assert after.maintenance.history.retention_days == 30
    assert after.pves[0].api_token_secret == before.pves[0].api_token_secret
    assert after.pbss[0].api_token_secret == before.pbss[0].api_token_secret
    assert after.app.secret_key == before.app.secret_key


# --- api-key management -------------------------------------------------------


def test_generate_api_key_returns_and_persists(app_ctx):
    client, app = app_ctx
    r = client.post("/api/config/api-key")
    assert r.status_code == 200
    key = r.json()["api_key"]
    assert key and len(key) >= 20
    assert app.state.config_store.config.app.api_key == key


def test_regenerate_api_key_replaces_old(app_ctx):
    client, app = app_ctx
    first = client.post("/api/config/api-key").json()["api_key"]
    second = client.post("/api/config/api-key").json()["api_key"]
    assert first != second
    assert app.state.config_store.config.app.api_key == second


def test_delete_api_key_clears_it(app_ctx):
    client, app = app_ctx
    client.post("/api/config/api-key")
    r = client.delete("/api/config/api-key")
    assert r.status_code == 204
    assert app.state.config_store.config.app.api_key == ""


def test_api_key_management_requires_auth(temp_config, temp_db):
    with TestClient(create_app()) as client:
        assert client.post("/api/config/api-key").status_code == 401
        assert client.delete("/api/config/api-key").status_code == 401


# --- scheduler toggle --------------------------------------------------------


def test_scheduler_toggle_off_disarms_everything(app_ctx, temp_config):
    client, _app = app_ctx
    body = client.post("/api/scheduler/toggle", json={"enabled": False}).json()
    assert body["enabled"] is False
    assert body["next_runs"] == []  # the kill-switch really unarmed every route
    assert load_config(temp_config).app.scheduler_enabled is False


def test_scheduler_toggle_back_on_rearms_every_route(app_ctx):
    client, _app = app_ctx
    client.post("/api/scheduler/toggle", json={"enabled": False})
    body = client.post("/api/scheduler/toggle", json={"enabled": True}).json()
    assert {r["route_id"] for r in body["next_runs"]} == {"nightly", "lab", "offsite"}


# --- guests ------------------------------------------------------------------


def test_guests_lists_one_pves_cluster(app_ctx):
    client, app = app_ctx
    _inject(
        app,
        pves={
            "pve-alpha": FakePve(
                guests=[Guest(vmid=100, name="ct", type="lxc", status="running", node="n1")]
            )
        },
    )
    guests = client.get("/api/guests?pve=pve-alpha").json()
    assert guests == [
        {
            "vmid": 100,
            "name": "ct",
            "type": "lxc",
            "status": "running",
            "node": "n1",
            "last_backup": None,
            "pbs_ids": [],
        }
    ]


def test_guests_require_a_pve(app_ctx):
    # vmids collide across PVEs, so "all the guests" is not a question with one answer.
    client, _app = app_ctx
    assert client.get("/api/guests").status_code == 422
    assert client.get("/api/guests?pve=nope").status_code == 404


def test_guests_include_cached_last_backup(app_ctx):
    from app.db.guest_backups import upsert_last_backups

    client, app = app_ctx
    _inject(
        app,
        pves={
            "pve-alpha": FakePve(
                guests=[Guest(vmid=100, name="ct", type="lxc", status="running", node="n1")]
            )
        },
    )
    epoch = 1_700_000_000
    with session_scope() as session:
        upsert_last_backups(session, "pve-alpha", "pbs-01", {100: epoch})

    guests = client.get("/api/guests?pve=pve-alpha").json()
    # Served as UTC-aware (with an offset) so the frontend converts it to local time.
    assert datetime.fromisoformat(guests[0]["last_backup"]) == datetime.fromtimestamp(
        epoch, tz=UTC
    )


def test_guests_name_every_pbs_holding_a_backup(app_ctx):
    """The homepage's guest panel chips one PBS per copy — a synced guest lists both.

    Also pins that the cache is read per *PVE*: the same vmid on another PVE must not leak
    its PBSs into this listing.
    """
    from app.db.guest_backups import upsert_last_backups

    client, app = app_ctx
    _inject(
        app,
        pves={
            "pve-alpha": FakePve(
                guests=[
                    Guest(vmid=100, name="synced", type="lxc", status="running", node="n1"),
                    Guest(vmid=101, name="local", type="qemu", status="running", node="n1"),
                ]
            )
        },
    )
    older, newer = 1_700_000_000, 1_700_009_999
    with session_scope() as session:
        upsert_last_backups(session, "pve-alpha", "pbs-01", {100: older, 101: newer})
        upsert_last_backups(session, "pve-alpha", "pbs-02", {100: newer})
        upsert_last_backups(session, "pve-beta", "pbs-02", {100: newer})

    by_vmid = {g["vmid"]: g for g in client.get("/api/guests?pve=pve-alpha").json()}
    assert by_vmid[100]["pbs_ids"] == ["pbs-01", "pbs-02"]
    assert by_vmid[101]["pbs_ids"] == ["pbs-01"]
    # Newest across the copies, not the first row found.
    assert datetime.fromisoformat(by_vmid[100]["last_backup"]) == datetime.fromtimestamp(
        newer, tz=UTC
    )


def test_guests_pve_unreachable_returns_502(app_ctx):
    client, app = app_ctx
    _inject(app, pves={"pve-alpha": UnreachablePve()})
    assert client.get("/api/guests?pve=pve-alpha").status_code == 502


# --- stopping a run ----------------------------------------------------------


def test_stop_asks_the_service_to_cancel_that_run(app_ctx):
    client, app = app_ctx
    seen = {}

    def fake_cancel(run_id, *, power_off=False):
        seen["args"] = (run_id, power_off)
        return True

    app.state.job_service.cancel = fake_cancel

    r = client.post("/api/runs/7/stop", json={"power_off": True})
    assert r.status_code == 202
    assert r.json() == {"run_id": 7}
    assert seen["args"] == (7, True)


def test_stop_defaults_to_leaving_the_pbs_on(app_ctx):
    client, app = app_ctx
    seen = {}
    app.state.job_service.cancel = lambda run_id, *, power_off=False: seen.update(
        power_off=power_off
    ) or True

    client.post("/api/runs/7/stop")
    assert seen["power_off"] is False


def test_stop_conflicts_when_that_run_is_not_in_flight(app_ctx):
    # e.g. the click landed just after the run finished — 409, not a silent no-op.
    client, app = app_ctx
    app.state.job_service.cancel = lambda run_id, *, power_off=False: False
    assert client.post("/api/runs/7/stop").status_code == 409


def test_stop_closes_out_a_run_its_worker_abandoned(app_ctx):
    # The history says RUNNING but the service has no such run: the worker died with the
    # row open (#38). Stop sweeps that one run rather than telling the user to restart.
    client, app = app_ctx
    app.state.job_service.cancel = lambda run_id, *, power_off=False: False
    with session_scope() as session:
        run = Run(kind=RunKind.CYCLE, trigger=RunTrigger.MANUAL, status=RunStatus.RUNNING)
        session.add(run)
        session.flush()
        run_id = run.id
        session.add(RunStep(run_id=run_id, name="backup:pve-alpha", status=StepStatus.RUNNING))
    with session_scope() as session:
        other = Run(kind=RunKind.CYCLE, trigger=RunTrigger.MANUAL, status=RunStatus.RUNNING)
        session.add(other)
        session.flush()
        other_id = other.id

    assert client.post(f"/api/runs/{run_id}/stop").status_code == 202

    with session_scope() as session:
        swept = session.get(Run, run_id)
        assert swept.status == RunStatus.FAILURE and swept.error_key == "interrupted"
        assert swept.steps[0].status == StepStatus.FAILURE
        assert session.get(Run, other_id).status == RunStatus.RUNNING  # only that one


# --- runs / logs -------------------------------------------------------------


def _guest(vmid: int) -> Guest:
    return Guest(vmid=vmid, name=f"g{vmid}", type="lxc", status="running", node="n1")


def _run_route(client, app, route_id: str) -> int:
    """Fire a route through the API and wait for it to finish; returns the run id."""
    assert client.post(f"/api/routes/{route_id}/run").status_code == 202
    service = app.state.job_service
    deadline = time.monotonic() + 5
    run_id = None
    while time.monotonic() < deadline:
        current = service.current()
        if current is not None and current.run_id is not None:
            run_id = current.run_id
            _wait_run(client, run_id)
            break
        if not service.pending() and not service.is_running:
            break
        time.sleep(0.01)
    # The run *row* is finalised inside the cycle, but the service only lets go afterwards —
    # power-off, notification, then the lock and `current`. Waiting on the row alone returns
    # inside that window, so a second run of the same route gets 409 and the worker outlives
    # the test. Wait for the queue itself to go idle.
    _drain(app)
    if run_id is not None:
        return run_id
    # It already finished before we looked: take the newest run.
    return client.get("/api/runs").json()[0]["id"]


def _drain(app, timeout: float = 5) -> None:
    """Block until the job service is idle: nothing running, queued or holding the lock."""
    service = app.state.job_service
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if service.current() is None and not service.pending() and not service.is_running:
            return
        time.sleep(0.01)
    raise AssertionError("queue did not drain")


def test_run_not_found(app_ctx):
    client, _app = app_ctx
    assert client.get("/api/runs/999999").status_code == 404


def test_run_summary_carries_guests_ok(app_ctx):
    # The history table shows how many guests a run backed up, so the summary (not just the
    # detail) has to carry it — /api/runs is the only request that view makes per poll.
    client, app = app_ctx
    _inject(app, pves={"pve-alpha": FakePve(guests=[_guest(100)]),
                       "pve-beta": FakePve(guests=[_guest(200)])})
    run_id = _run_route(client, app, "nightly")

    summary = next(r for r in client.get("/api/runs").json() if r["id"] == run_id)
    assert summary["guests_ok"] == client.get(f"/api/runs/{run_id}").json()["guests_ok"]
    assert summary["guests_ok"] is not None
    assert summary["route_id"] == "nightly"  # the history table's route column


def test_tasklog_empty_when_nothing_ran(app_ctx):
    client, _app = app_ctx
    assert client.get("/api/tasklog").json() == {"run_id": None, "lines": []}


def test_tasklog_returns_lines_and_supports_after_cursor(app_ctx):
    client, app = app_ctx
    pve = FakePve(guests=[_guest(100)], log_lines=["INFO: creating vzdump", "VM 100: done"])
    _inject(app, pves={"pve-alpha": pve, "pve-beta": FakePve(guests=[_guest(200)])})

    run_id = _run_route(client, app, "nightly")

    body = client.get("/api/tasklog").json()
    assert body["run_id"] == run_id
    texts = [line["text"] for line in body["lines"]]
    assert "INFO: creating vzdump" in texts and "VM 100: done" in texts
    # A multi-source route labels its steps per PVE, so the panel can group them.
    assert all(line["source"] == "pve" for line in body["lines"])
    assert {line["step"] for line in body["lines"]} <= {"backup:pve-alpha", "backup:pve-beta"}

    # `after` the last id returns no further lines (incremental polling is a no-op when idle).
    last_id = body["lines"][-1]["id"]
    assert client.get(f"/api/tasklog?after={last_id}").json()["lines"] == []


def test_tasklog_can_read_an_older_run_by_id(app_ctx):
    """The homepage expands a *finished* history row, which is never the newest run."""
    client, app = app_ctx
    pve = FakePve(guests=[_guest(100)], log_lines=["first run line"])
    _inject(app, pves={"pve-alpha": pve, "pve-beta": FakePve(guests=[_guest(200)])})
    first = _run_route(client, app, "nightly")

    pve.log_lines = ["second run line"]
    second = _run_route(client, app, "nightly")

    # The default cursor follows the newest run...
    assert client.get("/api/tasklog").json()["run_id"] == second
    # ...and `run` overrides it without disturbing the response shape.
    body = client.get(f"/api/tasklog?run={first}").json()
    assert body["run_id"] == first
    assert "first run line" in [line["text"] for line in body["lines"]]

    # A run that logged nothing echoes its own id rather than null: the caller named it.
    assert client.get("/api/tasklog?run=99999").json() == {"run_id": 99999, "lines": []}


# --- per-device status enrichment --------------------------------------------


def test_status_includes_datastore_and_load_when_online(app_ctx, monkeypatch):
    client, app = app_ctx
    _inject(app)
    monkeypatch.setattr("app.connectors.net.tcp_reachable", lambda *a, **k: True)
    body = client.get("/api/status").json()
    first = body["pbss"][0]
    assert first["online"] is True
    assert first["datastore"]["total"] == 8_000_000_000
    assert first["load"] == {"cpu": 7, "mem": 38, "uptime": 3600}


def test_status_omits_datastore_when_offline(app_ctx):
    client, _app = app_ctx  # fixture stubs reachability to False
    first = client.get("/api/status").json()["pbss"][0]
    assert first["datastore"] is None and first["load"] is None


def test_status_datastore_from_cache_when_offline(app_ctx):
    # The whole point of Joulenap: the box is off most of the time, and the dashboard still
    # has to say how full it was.
    client, _app = app_ctx
    with session_scope() as s:
        upsert_datastore_stat(s, "pbs-01", "backup", 8_000_000_000, 2_000_000_000)

    pbss = {d["id"]: d for d in client.get("/api/status").json()["pbss"]}
    assert pbss["pbs-01"]["datastore"] == {
        "used": 2_000_000_000,
        "total": 8_000_000_000,
        "used_pct": 25.0,
    }
    assert pbss["pbs-01"]["load"] is None  # live-only, stays null when the PBS is offline
    assert pbss["pbs-02"]["datastore"] is None  # a different box, its own cache row


def test_probe_skips_a_device_with_no_host(temp_db):
    from app.api._probe import probe_pbss
    from app.config import Config, PbsDevice

    cfg = Config()
    cfg.pbss = [PbsDevice(id="pbs-01", managed_power=False)]  # no host => never probes
    probes = probe_pbss(cfg, connect=lambda _d: None)
    assert probes["pbs-01"].online is False
    assert probes["pbs-01"].datastore is None and probes["pbs-01"].load is None


def test_resolve_datastore_live_upserts_under_the_device_id(temp_db):
    from app.api._probe import resolve_datastore
    from app.config import PbsDevice
    from app.connectors.pbs import DatastoreStatus

    device = PbsDevice(id="pbs-01", datastore="backup", managed_power=False)
    view = resolve_datastore(device, DatastoreStatus(total=10, used=4, avail=6))
    assert (view.total, view.used) == (10, 4)
    with session_scope() as s:
        row = get_datastore_stat(s, "pbs-01", "backup")
    assert row is not None and row.used == 4  # live reading was persisted, keyed by device


def test_resolve_datastore_offline_uses_that_devices_cache(temp_db):
    from app.api._probe import resolve_datastore
    from app.config import PbsDevice

    device = PbsDevice(id="pbs-01", datastore="backup", managed_power=False)
    other = PbsDevice(id="pbs-02", datastore="offsite", managed_power=False)
    with session_scope() as s:
        upsert_datastore_stat(s, "pbs-01", "backup", 8, 2)

    view = resolve_datastore(device, None)
    assert (view.total, view.used, view.used_pct) == (8, 2, 25.0)
    assert resolve_datastore(other, None) is None  # not one shared row for every box


# --- account -----------------------------------------------------------------


def test_account_update_changes_username_and_password(app_ctx, temp_config):
    client, _app = app_ctx
    r = client.put(
        "/api/account",
        json={"current_password": "secret12", "username": "newadmin", "password": "freshpass"},
    )
    assert r.status_code == 200 and r.json() == {"username": "newadmin"}

    cfg = load_config(temp_config)
    assert cfg.app.auth.username == "newadmin"
    # New password takes effect for login.
    client.post("/api/logout")
    login = client.post("/api/login", json={"username": "newadmin", "password": "freshpass"})
    assert login.status_code == 200


def test_account_update_empty_password_keeps_current(app_ctx, temp_config):
    client, _app = app_ctx
    r = client.put(
        "/api/account", json={"current_password": "secret12", "username": "admin2", "password": ""}
    )
    assert r.status_code == 200
    client.post("/api/logout")
    # Old password still valid under the new username => password unchanged.
    login = client.post("/api/login", json={"username": "admin2", "password": "secret12"})
    assert login.status_code == 200


def test_account_update_omitted_password_keeps_current(app_ctx, temp_config):
    client, _app = app_ctx
    # Password key entirely absent (not just "") also means "keep current".
    r = client.put("/api/account", json={"current_password": "secret12", "username": "admin4"})
    assert r.status_code == 200
    client.post("/api/logout")
    login = client.post("/api/login", json={"username": "admin4", "password": "secret12"})
    assert login.status_code == 200


def test_account_update_short_password_rejected(app_ctx, temp_config):
    client, _app = app_ctx
    r = client.put(
        "/api/account",
        json={"current_password": "secret12", "username": "admin", "password": "ab"},
    )
    assert r.status_code == 422


def test_account_update_wrong_current_password_rejected(app_ctx, temp_config):
    client, _app = app_ctx
    # A valid session alone must not be enough to rotate credentials (BE-S9).
    r = client.put(
        "/api/account",
        json={"current_password": "wrong-pass", "username": "hacker", "password": "takeover1"},
    )
    assert r.status_code == 401
    # Nothing changed: the original credentials still work.
    cfg = load_config(temp_config)
    assert cfg.app.auth.username == "admin"
    client.post("/api/logout")
    login = client.post("/api/login", json={"username": "admin", "password": "secret12"})
    assert login.status_code == 200


def test_account_update_missing_current_password_rejected(app_ctx, temp_config):
    client, _app = app_ctx
    r = client.put("/api/account", json={"username": "admin", "password": "newpass-88"})
    assert r.status_code == 422


def test_password_change_keeps_acting_session_but_revokes_others(app_ctx):
    client, app = app_ctx
    # A second, independent session established before the change.
    with TestClient(app) as other:
        login = other.post("/api/login", json={"username": "admin", "password": "secret12"})
        assert login.status_code == 200
        assert other.get("/api/auth/me").status_code == 200
        # Acting client changes the password.
        r = client.put(
            "/api/account",
            json={"current_password": "secret12", "username": "admin", "password": "newpass-88"},
        )
        assert r.status_code == 200
        # Acting session is kept alive (cookie re-issued with the new hash).
        assert client.get("/api/auth/me").status_code == 200
        # The other pre-existing session is revoked (its pwv no longer matches the new hash).
        assert other.get("/api/auth/me").status_code == 401


# --- dashboard integration ---------------------------------------------------


def _enable_api_key(app, key="dash-key-123"):
    app.state.config_store.update(lambda c: setattr(c.app, "api_key", key))
    return key


def test_dashboard_403_when_no_key_configured(app_ctx):
    client, app = app_ctx
    app.state.config_store.update(lambda c: setattr(c.app, "api_key", ""))
    assert client.get("/api/dashboard").status_code == 403


def test_dashboard_401_without_header(app_ctx):
    client, app = app_ctx
    _enable_api_key(app)
    assert client.get("/api/dashboard").status_code == 401


def test_dashboard_401_with_wrong_key(app_ctx):
    client, app = app_ctx
    _enable_api_key(app, "right-key")
    r = client.get("/api/dashboard", headers={"X-API-Key": "wrong-key"})
    assert r.status_code == 401


def test_dashboard_200_with_header_key(app_ctx):
    client, app = app_ctx
    key = _enable_api_key(app)
    r = client.get("/api/dashboard", headers={"X-API-Key": key})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"state", "routes", "pbss"}
    assert body["state"] == "idle"
    # One entry per route and per PBS — there is no single "next run" or "datastore" now.
    assert [e["id"] for e in body["routes"]] == ["nightly", "lab", "offsite"]
    assert [e["id"] for e in body["pbss"]] == ["pbs-01", "pbs-02"]
    nightly = body["routes"][0]
    assert nightly["kind"] == "backup" and nightly["enabled"] is True
    assert nightly["next_run"] is not None
    assert nightly["last_run_status"] == "never" and nightly["last_run_time"] is None
    # PBS stubbed offline, nothing cached yet:
    assert body["pbss"][0]["state"] == "sleeping"
    assert body["pbss"][0]["datastore_used_pct"] is None


def test_dashboard_datastore_from_cache_when_offline(app_ctx):
    client, app = app_ctx
    key = _enable_api_key(app)
    with session_scope() as s:
        upsert_datastore_stat(s, "pbs-01", "backup", 8_000_000_000, 2_000_000_000)

    entry = client.get("/api/dashboard", headers={"X-API-Key": key}).json()["pbss"][0]
    assert entry["datastore_used_pct"] == 25.0
    assert entry["datastore_used_bytes"] == 2_000_000_000
    assert entry["datastore_total_bytes"] == 8_000_000_000


def test_dashboard_upserts_and_returns_live_when_pbs_online(app_ctx, monkeypatch):
    client, app = app_ctx
    key = _enable_api_key(app)
    monkeypatch.setattr("app.connectors.net.tcp_reachable", lambda *a, **k: True)
    _inject(app)  # deps.connect_pbs -> FakePbs (datastore 8e9/2e9)

    entry = client.get("/api/dashboard", headers={"X-API-Key": key}).json()["pbss"][0]
    assert entry["state"] == "online"
    assert entry["datastore_used_pct"] == 25.0
    assert entry["datastore_used_bytes"] == 2_000_000_000

    # the live reading was persisted to the cache (write-on-GET)
    with session_scope() as s:
        row = get_datastore_stat(s, "pbs-01", "backup")
    assert row is not None and row.used == 2_000_000_000


def test_dashboard_reports_scheduler_paused(app_ctx):
    client, app = app_ctx
    key = _enable_api_key(app)
    client.post("/api/scheduler/toggle", json={"enabled": False})  # goes through rearm
    body = client.get("/api/dashboard", headers={"X-API-Key": key}).json()
    assert body["state"] == "paused"
    assert all(e["next_run"] is None for e in body["routes"])


def test_dashboard_200_with_query_param_key(app_ctx):
    client, app = app_ctx
    key = _enable_api_key(app)
    r = client.get(f"/api/dashboard?key={key}")
    assert r.status_code == 200
    assert r.json()["pbss"][0]["state"] == "sleeping"


def test_dashboard_401_with_non_ascii_key(app_ctx):
    client, app = app_ctx
    _enable_api_key(app)
    r = client.get("/api/dashboard?key=%C3%A9")
    assert r.status_code == 401


def _add_cycle_run(session, status: RunStatus, *, started_at: datetime, route="nightly") -> Run:
    run = Run(
        kind=RunKind.CYCLE,
        trigger=RunTrigger.SCHEDULED,
        status=status,
        started_at=started_at,
        route_id=route,
        route_name=route,
    )
    session.add(run)
    session.flush()
    return run


def test_dashboard_last_run_reflects_the_last_finished_run_not_an_in_progress_one(app_ctx):
    client, app = app_ctx
    key = _enable_api_key(app)
    now = datetime.now(UTC)
    with session_scope() as s:
        _add_cycle_run(s, RunStatus.SUCCESS, started_at=now - timedelta(hours=1))
        _add_cycle_run(s, RunStatus.RUNNING, started_at=now)

    body = client.get("/api/dashboard", headers={"X-API-Key": key}).json()
    nightly = body["routes"][0]
    assert nightly["last_run_status"] == "success"
    assert datetime.fromisoformat(nightly["last_run_time"]) == now - timedelta(hours=1)


def test_dashboard_last_run_is_per_route(app_ctx):
    # A failed sync must not make the nightly backup look failed, and vice versa.
    client, app = app_ctx
    key = _enable_api_key(app)
    now = datetime.now(UTC)
    with session_scope() as s:
        _add_cycle_run(s, RunStatus.SUCCESS, started_at=now - timedelta(hours=2))
        _add_cycle_run(
            s, RunStatus.FAILURE, started_at=now - timedelta(hours=1), route="offsite"
        )

    body = client.get("/api/dashboard", headers={"X-API-Key": key}).json()
    routes = {e["id"]: e for e in body["routes"]}
    assert routes["nightly"]["last_run_status"] == "success"
    assert routes["offsite"]["last_run_status"] == "failed"
    assert routes["lab"]["last_run_status"] == "never"


def test_dashboard_last_run_never_when_only_a_running_one_exists(app_ctx):
    client, app = app_ctx
    key = _enable_api_key(app)
    with session_scope() as s:
        _add_cycle_run(s, RunStatus.RUNNING, started_at=datetime.now(UTC))

    nightly = client.get("/api/dashboard", headers={"X-API-Key": key}).json()["routes"][0]
    assert nightly["last_run_status"] == "never"
    assert nightly["last_run_time"] is None


def test_run_history_renders_the_error_in_the_configured_language(app_ctx):
    """The same failure text is shown in the notification *and* here, so it is stored as a
    key + params and rendered on read — the UI prints whatever this endpoint returns."""
    client, app = app_ctx
    with session_scope() as session:
        run = Run(
            kind=RunKind.CYCLE,
            trigger=RunTrigger.MANUAL,
            status=RunStatus.FAILURE,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            error="route 'nightly': pbs 'pbs-01' no longer exists",
            error_key="pbs_missing",
            error_params='{"route": "nightly", "pbs": "pbs-01"}',
        )
        session.add(run)
        session.flush()
        run_id = run.id

    assert "no longer exists" in client.get(f"/api/runs/{run_id}").json()["error"]

    store = app.state.config_store
    cfg = store.config.model_copy(deep=True)
    cfg.app.language = "it"
    store.replace(cfg)

    for body in (
        client.get(f"/api/runs/{run_id}").json(),
        next(r for r in client.get("/api/runs").json() if r["id"] == run_id),
    ):
        assert body["error"] == "route 'nightly': il pbs 'pbs-01' non esiste più"


def test_run_history_falls_back_to_the_stored_english_error(app_ctx):
    """A pre-1.0 row has no key at all; it must still show its message, not an empty cell."""
    client, _ = app_ctx
    with session_scope() as session:
        run = Run(
            kind=RunKind.CYCLE,
            trigger=RunTrigger.MANUAL,
            status=RunStatus.FAILURE,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            error="vzdump exited with code 255",
        )
        session.add(run)
        session.flush()
        run_id = run.id

    assert client.get(f"/api/runs/{run_id}").json()["error"] == "vzdump exited with code 255"
